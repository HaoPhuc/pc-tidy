r"""python pc_tidy.py settings import: bring the settings of a pc-tidy folder from before 2.0 into
settings.ini.

Before 2.0 the settings were in the scripts themselves: the SETTINGS block of cleanup.ps1, the
constants at the top of disk_report.py, move_off_c.py, tidy_folder.py and llm.py, and the rule lists
of classify.py. Nothing in the old folder is ever run:
- cleanup.ps1's block is read with regexes ($Name = $true/$false, @("...") lists, $env:NAME written
  as %NAME%), and its inline # comments are dropped.
- The .py files are read with Python's ast, mapping classify.py's category names (CACHE, SYSTEM,
  ...) to their values.

Only values that differ from the defaults are imported. Rule entries that differ from the built-in
ones become [label.*], [category.*] and [apptype.*] sections. Each value is checked exactly as
settings.ini is: an old entry that 2.0 would refuse (a path inside Program Files to delete, say) is
not imported but listed with the reason, so the result always passes "settings check". Lines that
can't be read are listed, never guessed.
"""

import ast
import os
import re
import time
from collections import namedtuple

from . import settings

Value = namedtuple("Value", "section key raw source")      # raw: text, or a list of texts
TableEntry = namedtuple("TableEntry", "ext folder source")
NewRule = namedtuple("NewRule", "kind fields source")       # fields: {key: text}
Refused = namedtuple("Refused", "source text reason")
Unread = namedtuple("Unread", "source text")

PS_SETTINGS = {
    "CondaCache": "conda_cache", "PipCache": "pip_cache", "UvCache": "uv_cache",
    "GpuShaderCache": "gpu_shader_cache", "TempFiles": "temp_files", "RecycleBin": "recycle_bin",
    "CrashDumps": "crash_dumps", "Hibernation": "hibernation", "Installers": "installers",
    "InstallerFolders": "installer_folders", "CondaEnvsToRemove": "conda_envs_to_remove",
    "AppsToUninstall": "apps_to_uninstall", "ExtraPathsToDelete": "extra_paths_to_delete",
}
PY_CONSTANTS = {
    "disk_report.py": {"MIN_GB": ("report", "min_gb"), "MAX_DEPTH": ("report", "max_depth"),
                       "TOP_FILES": ("report", "top_files"), "CLEANUP_MIN_GB": ("report", "cleanup_min_gb"),
                       "LLM_MAX_FOLDERS": ("ai", "max_folders"), "LLM_MAX_APPS": ("ai", "max_apps")},
    "move_off_c.py": {"MIN_GB": ("move", "min_gb"), "DEST_FOLDER_NAME": ("move", "dest_folder_name")},
    "tidy_folder.py": {"SOURCE": ("tidy", "source"), "DEST": ("tidy", "dest"),
                       "ARCHIVE_AFTER_DAYS": ("tidy", "archive_after_days"),
                       "SKIP_NEWER_THAN_MIN": ("tidy", "skip_newer_than_min"),
                       "RENAME_WITH_LLM": ("tidy", "rename_with_ai"), "CATEGORIES": ("tidy", "categories")},
    "llm.py": {"MODEL": ("ai", "model"), "OLLAMA": ("ai", "url")},
}


class Found:
    """What an old folder holds that differs from the defaults, and what can't come along."""

    def __init__(self, folder):
        self.folder = folder
        self.values = []          # Value
        self.table = []           # TableEntry: [tidy.extensions] lines added or changed
        self.removed_ext = []     # (ext, source): extensions the old RULES no longer had
        self.rules = []           # NewRule
        self.refused = []         # Refused
        self.unread = []          # Unread
        self.kept_builtin = []    # built-in rules the old classify.py didn't have (they stay)
        self.files = []           # the old files that were read

    @property
    def anything(self):
        return bool(self.values or self.table or self.removed_ext or self.rules)

    def drop_present(self, cfg):
        """Leave out what settings.ini already has, so importing twice changes nothing."""
        self.values = [v for v in self.values if cfg.key_errors(v.section, v.key) or cfg.raw(v.section, v.key) != v.raw
                       or (v.section, v.key) not in cfg.entries]
        table = cfg.table("tidy.extensions")
        self.table = [e for e in self.table if table.get(e.ext) != e.folder]
        self.removed_ext = [(ext, source) for ext, source in self.removed_ext if ext in table]
        have = [(r.kind, {k: _text(v) for k, v in r.fields.items()}) for r in cfg.rules]
        self.rules = [r for r in self.rules if (r.kind, {k: _text(v) for k, v in _fields(r).items()}) not in have]


def _text(value):
    if hasattr(value, "pattern"):
        return value.pattern
    return settings._show(value) if isinstance(value, bool) else str(value)


def _fields(rule):
    """A NewRule's fields as settings.ini reads them (yes/no as True/False), to compare with yours."""
    return {k: (v == "yes") if k == "expand" else v for k, v in rule.fields.items()}


def read_folder(folder, ctx):
    """Everything to import from an old pc-tidy folder."""
    found = Found(folder)
    _read_cleanup(os.path.join(folder, "cleanup.ps1"), found, ctx)
    for name, constants in PY_CONSTANTS.items():
        _read_constants(os.path.join(folder, name), constants, found, ctx)
    _read_rules(os.path.join(folder, "classify.py"), found, ctx)
    return found


# ---------- checking one value the way settings.ini is checked ----------
def _check(ctx, section, lines):
    """The mistakes and warnings settings.ini would have for these lines in [section]."""
    text = "\n".join([f"[{section}]"] + lines) + "\n"
    s = settings.parse(text.encode("utf-8"), ctx)
    return s.errors(), s.warnings()


def _render(key, raw):
    if isinstance(raw, list):
        return [f"{key} ="] + [f"    {item}" for item in raw]
    return [f"{key} = {raw}"]


def _add_value(found, ctx, section, key, raw, source):
    """Keep a value if it differs from the default; list items 2.0 refuses are left out, one by one."""
    k = settings._key(section, key)
    if isinstance(raw, list):
        kept = []
        for item in raw:
            if item[:1] in ("#", ";"):
                found.refused.append(Refused(source, item, "an item can't start with # or ; in settings.ini"))
                continue
            errors, _ = _check(ctx, section, _render(key, [item]))
            if errors:
                found.refused.append(Refused(source, item, errors[0].message))
            else:
                kept.append(item)
        raw = kept
    else:
        errors, _ = _check(ctx, section, _render(key, raw))
        if errors:
            found.refused.append(Refused(source, f"{key} = {raw}", errors[0].message))
            return
    default = k.default if isinstance(k.default, list) else settings._show(k.default)
    if raw != default and not (isinstance(raw, str) and _same_value(section, key, raw, ctx)):
        found.values.append(Value(section, key, raw, source))


def _same_value(section, key, raw, ctx):
    """True if raw reads as the default (1 and 1.0, yes and true)."""
    s = settings.parse("\n".join([f"[{section}]"] + _render(key, raw)).encode("utf-8"), ctx)
    return s.value(section, key) == s.default(section, key)


def _literal_path(text):
    """A path from the old files as settings.ini writes it: a % sign becomes %%."""
    return text.replace("%", "%%")


# ---------- cleanup.ps1 ----------
_PS_ASSIGN = re.compile(r"^\s*\$(\w+)\s*=(.*)$")


def _read_text(path):
    with open(path, "rb") as f:
        data = f.read()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("mbcs" if os.name == "nt" else "latin-1", errors="replace")


def _read_cleanup(path, found, ctx):
    try:
        lines = _read_text(path).splitlines()
    except OSError:
        return
    found.files.append(path)
    seen, i = set(), 0
    while i < len(lines):
        m = _PS_ASSIGN.match(lines[i])
        if not m or m.group(1) not in PS_SETTINGS or m.group(1) in seen:
            i += 1
            continue
        name, first = m.group(1), i
        seen.add(name)
        key = PS_SETTINGS[name]
        source = f"cleanup.ps1 line {first + 1}"
        k = settings._key("cleanup", key)
        if k.kind == "yesno":
            value = _ps_code(m.group(2)).strip().lower()
            if value in ("$true", "$false"):
                _add_value(found, ctx, "cleanup", key, "yes" if value == "$true" else "no", source)
            else:
                found.unread.append(Unread(source, lines[first].strip()))
            i += 1
            continue
        code, i = _ps_collect(lines, i, m.group(2))
        items = _ps_list(code)
        if items is None:
            found.unread.append(Unread(source, " ".join(x.strip() for x in lines[first:i + 1])))
        else:
            _add_value(found, ctx, "cleanup", key, items, source)
        i += 1


def _ps_code(line):
    """A line of PowerShell without its # comment (a # inside quotes stays)."""
    quote = None
    for j, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:j]
    return line


def _ps_collect(lines, i, rest):
    """The code of an assignment that may go on over several lines, until its ( ) are closed.
    Returns (code, index of its last line)."""
    code = _ps_code(rest)
    depth = _depth(code)
    while depth > 0 and i + 1 < len(lines):
        i += 1
        more = _ps_code(lines[i])
        code += "\n" + more
        depth += _depth(more)
    return code, i


def _depth(code):
    depth, quote = 0, None
    for ch in code:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
    return depth


_PS_TOKEN = re.compile(r'\s+|,|@\(|\)|"(?:[^"]|"")*"|\'(?:[^\']|\'\')*\'')


def _ps_list(code):
    """The strings of @("a", 'b') (or "a", "b", or one string) as settings.ini texts, or None if it
    is anything else (a variable, a command, ...)."""
    items, pos, code = [], 0, code.strip()
    while pos < len(code):
        m = _PS_TOKEN.match(code, pos)
        if not m:
            return None
        token, pos = m.group(0), m.end()
        if token.startswith('"'):
            text = _ps_double(token[1:-1].replace('""', '"'))
            if text is None:
                return None
            items.append(text)
        elif token.startswith("'"):
            items.append(_literal_path(token[1:-1].replace("''", "'")))
    return items


_PS_ENV = re.compile(r"\$\{env:([^}]+)\}|\$env:(\w+)", re.I)


def _ps_double(text):
    """A double-quoted PowerShell string: $env:NAME and ${env:NAME} become %NAME%. Any other $ or a
    backtick escape can't be read without running PowerShell, so None."""
    out, pos = [], 0
    for m in _PS_ENV.finditer(text):
        out.append(_literal_path(text[pos:m.start()]))
        out.append(f"%{m.group(1) or m.group(2)}%")
        pos = m.end()
    out.append(_literal_path(text[pos:]))
    rest = _PS_ENV.sub("", text)
    if "$" in rest or "`" in rest:
        return None
    return "".join(out)


# ---------- the .py constants ----------
def _module(path, found):
    try:
        text = _read_text(path)
    except OSError:
        return None
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError as e:
        found.unread.append(Unread(f"{os.path.basename(path)} line {e.lineno}", "Python that can't be read"))
        return None
    found.files.append(path)
    return tree


def _assignments(tree):
    """{name: node} for each top-level NAME = value (the first one)."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out.setdefault(node.targets[0].id, node)
    return out


def _read_constants(path, constants, found, ctx):
    tree = _module(path, found)
    if tree is None:
        return
    name_of_file = os.path.basename(path)
    assigned = _assignments(tree)
    for name, (section, key) in constants.items():
        node = assigned.get(name)
        if node is None:
            continue
        source = f"{name_of_file} line {node.lineno}"
        k = settings._key(section, key)
        try:
            if k.kind == "path":
                value = _path_expression(node.value)
            else:
                value = ast.literal_eval(node.value)
        except ValueError:
            value = None
        if value is None and name == "ARCHIVE_AFTER_DAYS" and _is_none(node.value):
            value = 0
        if value is None:
            found.unread.append(Unread(source, f"{name} = {ast.get_source_segment(_read_text(path), node.value)}"))
            continue
        if k.kind in settings.LIST_KINDS and isinstance(value, (list, tuple)):
            raw = [str(v) for v in value]
        elif isinstance(value, bool):
            raw = "yes" if value else "no"
        else:
            raw = str(value)
        _add_value(found, ctx, section, key, raw, source)
    rules = assigned.get("RULES")
    if name_of_file == "tidy_folder.py" and rules is not None:
        _read_extensions(rules, found, ctx)


def _is_none(node):
    return isinstance(node, ast.Constant) and node.value is None


def _path_expression(node):
    """Path.home() / "Downloads", Path(r"D:\\Stuff") or a plain string, as settings.ini text."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _literal_path(node.value.replace("/", "\\"))
    if isinstance(node, ast.Call) and not node.keywords:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "home" and not node.args \
                and isinstance(func.value, ast.Name) and func.value.id == "Path":
            return "%USERPROFILE%"
        if isinstance(func, ast.Name) and func.id == "Path" and len(node.args) == 1:
            return _path_expression(node.args[0])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left, right = _path_expression(node.left), _path_expression(node.right)
        if left is not None and right is not None:
            return left.rstrip("\\") + "\\" + right.lstrip("\\")
    raise ValueError("not a path")


def _read_extensions(node, found, ctx):
    try:
        old = ast.literal_eval(node.value)
    except ValueError:
        found.unread.append(Unread(f"tidy_folder.py line {node.lineno}", "RULES = ..."))
        return
    if not isinstance(old, dict):
        found.unread.append(Unread(f"tidy_folder.py line {node.lineno}", "RULES = ..."))
        return
    source = f"tidy_folder.py line {node.lineno}"
    default = settings.DEFAULT_EXTENSIONS
    for ext, folder in old.items():
        ext, folder = str(ext).lower(), str(folder)
        if default.get(ext) == folder:
            continue
        errors, _ = _check(ctx, "tidy.extensions", [f"{ext} = {folder}"])
        if errors:
            found.refused.append(Refused(source, f"{ext} = {folder}", errors[0].message))
        else:
            found.table.append(TableEntry(ext, folder, source))
    found.removed_ext = [(ext, source) for ext in default if ext not in {str(e).lower() for e in old}]


# ---------- classify.py's rules ----------
def _read_rules(path, found, ctx):
    tree = _module(path, found)
    if tree is None:
        return
    import classify       # the built-in rules, to compare with
    names = {n: getattr(classify, n) for n in ("SYSTEM", "APPS", "GAMES", "DEV", "CACHE", "PERSONAL", "OTHER")}
    for node in tree.body:      # the old file's own names, if it changed them
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Tuple) \
                and isinstance(node.value, ast.Tuple):
            for target, value in zip(node.targets[0].elts, node.value.elts):
                if isinstance(target, ast.Name) and isinstance(value, ast.Constant):
                    names[target.id] = value.value
    assigned = _assignments(tree)
    old_labels = _entries(assigned.get("_RULES"), names, found, 5)
    old_categories = _entries(assigned.get("_CATEGORY_RULES"), names, found, 2)
    old_apps = _entries(assigned.get("_APP_RULES"), names, found, 2)
    for line, (pattern, what, todo, kind, expand) in old_labels:
        if (pattern, what, todo, kind, expand) not in classify._RULES:
            _add_rule(found, ctx, "label", {"pattern": pattern, "what": what, "todo": todo, "kind": kind,
                                            "expand": "yes" if expand else "no"}, f"classify.py line {line}")
    for line, (pattern, category) in old_categories:
        if (pattern, category) not in classify._CATEGORY_RULES:
            _add_rule(found, ctx, "category", {"pattern": pattern, "category": category}, f"classify.py line {line}")
    for line, (app_type, pattern) in old_apps:
        if (app_type, pattern) not in classify._APP_RULES:
            _add_rule(found, ctx, "apptype", {"match": pattern, "type": app_type}, f"classify.py line {line}")
    # a built-in rule the old file doesn't have at all (not even changed: same pattern, or same label
    # or app type) was removed by hand; settings can't remove one, so say that it stays
    if old_labels:
        seen = {e[0] for _l, e in old_labels} | {e[1] for _l, e in old_labels}
        found.kept_builtin += [r[1] for r in classify._RULES if r[0] not in seen and r[1] not in seen]
    if old_categories:
        seen = {e[0] for _l, e in old_categories}
        found.kept_builtin += [f"{r[1]}: {r[0]}" for r in classify._CATEGORY_RULES if r[0] not in seen]
    if old_apps:
        seen = {e[0] for _l, e in old_apps}
        found.kept_builtin += [f"apps of type {r[0]}" for r in classify._APP_RULES if r[0] not in seen]


def _entries(node, names, found, size):
    """[(line, tuple)] of a list of tuples of strings (and the category names), or [] if it isn't there."""
    if node is None:
        return []
    if not isinstance(node.value, ast.List):
        found.unread.append(Unread(f"classify.py line {node.lineno}", f"{node.targets[0].id} = ..."))
        return []
    out = []
    for elt in node.value.elts:
        try:
            if not isinstance(elt, ast.Tuple) or len(elt.elts) != size:
                raise ValueError
            values = []
            for part in elt.elts:
                if isinstance(part, ast.Name) and part.id in names:
                    values.append(names[part.id])
                else:
                    values.append(ast.literal_eval(part))
            if not all(isinstance(v, (str, bool)) for v in values):
                raise ValueError
            out.append((elt.lineno, tuple(values)))
        except ValueError:
            found.unread.append(Unread(f"classify.py line {elt.lineno}", "a rule that isn't plain text"))
    return out


def _add_rule(found, ctx, kind, fields, source):
    lines = [f"{key} = {value}" for key, value in fields.items()]
    if any("\n" in str(v) or "\r" in str(v) for v in fields.values()):
        found.refused.append(Refused(source, kind, "a value with a line break can't be written in settings.ini"))
        return
    errors, _ = _check(ctx, f"{kind}.imported", lines)
    if errors:
        found.refused.append(Refused(source, f"{kind}: {fields.get('what') or fields.get('pattern') or ''}",
                                     errors[0].message))
        return
    found.rules.append(NewRule(kind, fields, source))


# ---------- writing it into settings.ini ----------
def apply(text, found):
    """The text of settings.ini with everything found put in. Keys are changed where they are, so
    the comments stay; new keys go at the end of their section, rules at the end of the file."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = re_lines(text)
    _preamble, chunks = settings._split(lines)
    edits = []       # (first line, last line, new lines): replaces lines first..last (1-based)
    ends = {}        # section -> lines to add at its end
    first_chunk = {}
    for c in chunks:
        first_chunk.setdefault(c.key, c)
    for v in found.values:
        c = first_chunk.get(v.section)
        new = _render(v.key, v.raw)
        line = next(((k[1], k[3]) for k in (c.keys if c else []) if k[0] == v.key), None)
        if line:
            start, more = line
            edits.append((start, more[-1][0] if more else start, new))
        else:
            ends.setdefault(v.section, []).extend(new)
    c = first_chunk.get("tidy.extensions")
    changed = {e.ext: e for e in found.table}
    removed = {ext for ext, _s in found.removed_ext}
    if c is None and (changed or removed):      # a new section is the whole table, not only the changes
        table = {ext: folder for ext, folder in settings.DEFAULT_EXTENSIONS.items() if ext not in removed}
        table.update({e.ext: e.folder for e in found.table})
        ends["tidy.extensions"] = [f"{ext} = {folder}" for ext, folder in table.items()]
        changed = {}
    for key, line, _first, more in (c.keys if c else []):
        if key in removed:
            edits.append((line, more[-1][0] if more else line, []))
        elif key in changed:
            edits.append((line, more[-1][0] if more else line, [f"{key} = {changed.pop(key).folder}"]))
    if changed:
        ends.setdefault("tidy.extensions", []).extend([f"{e.ext} = {e.folder}" for e in changed.values()])
    for section, new in ends.items():
        c = first_chunk.get(section)
        if c:
            last = max([c.line] + [k[1] for k in c.keys] + [n for k in c.keys for n, _t in k[3]])
            edits.append((last + 1, last, new))        # an insert after `last`
        else:
            edits.append((len(lines) + 1, len(lines), ["", f"[{section}]"] + new))
    for start, end, new in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        lines[start - 1:end] = new
    taken = {c.key for c in chunks}
    if found.rules:
        lines += ["", f"# imported from {os.path.join(found.folder, 'classify.py')} "
                      f"on {time.strftime('%Y-%m-%d')}"]
    for rule in found.rules:
        name = _unique(f"{rule.kind}.{_slug(rule.fields.get('what') or rule.fields.get('category') or rule.fields.get('type'))}",
                       taken)
        lines += ["", f"[{name}]"] + [f"{k} = {v}" for k, v in rule.fields.items()]
    return newline.join(lines) + newline


def re_lines(text):
    lines = re.split(r"\r\n|\r|\n", text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "rule").lower()).strip("-")[:40] or "rule"


def _unique(name, taken):
    candidate, i = name, 2
    while candidate in taken:
        candidate = f"{name}-{i}"
        i += 1
    taken.add(candidate)
    return candidate
