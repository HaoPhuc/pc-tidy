r"""settings.ini: pc-tidy's one settings file.

settings.template.ini (in git, with a comment on every key) is copied to settings.ini (not in git)
the first time a command runs. Both are found through pc-tidy's own folder (ctx.settings_file), so
an admin window that starts in System32 finds them too.

Reading it:
- The file is read as utf-8-sig, so a BOM added by an editor is harmless.
- It is split at its [section] lines first, and each section is parsed on its own by configparser
  (no interpolation, strict), so every mistake belongs to exactly one section. A section that
  appears twice is a mistake in that section only; a line above the first section is reported and
  ignored.
- %NAME% is filled in from the environment here. A % left over means an undefined variable, which
  is a mistake; %% is a % sign.
- A line starting with # or ; is a comment anywhere. After a yes/no, a number or a choice, a # also
  starts a comment (those values can't contain one). In paths, lists, patterns and text a # is kept,
  because a folder may be called "C# projects", with a warning when " #" looks like a comment.

load() never raises for a mistake in the file: it lists every one with its line number. Each command
asks only about the sections (or keys) it reads, so a typo in [cleanup] never stops the weekly sort.
Precedence: a command-line flag, then settings.ini, then the defaults below (which the template
repeats, line by line).
"""

import configparser
import difflib
import os
import re
from collections import namedtuple
from types import SimpleNamespace
from urllib.parse import urlsplit

from . import context

FILE_NAME = "settings.ini"
TEMPLATE_NAME = "settings.template.ini"
EXIT = 2          # a command refused to start: a section it reads has a mistake

Problem = namedtuple("Problem", "level section key line message")     # level: "error" or "warning"
Entry = namedtuple("Entry", "value raw lines")      # raw: the text as written (a list for lists)
Rule = namedtuple("Rule", "kind name section line fields")      # one [label.*], [category.*] or [apptype.*]


class Key:
    def __init__(self, name, kind, default=None, low=None, high=None, choices=None, required=False,
                 empty_ok=False):
        self.name, self.kind, self.default = name, kind, default
        self.low, self.high, self.choices = low, high, choices
        self.required, self.empty_ok = required, empty_ok


# kinds whose values can't contain a #, so " # text" after them is a comment
INLINE_COMMENTS = {"yesno", "int", "number", "choice"}
LIST_KINDS = {"paths", "deletes", "names", "envs", "categories"}
PATH_KINDS = {"path", "paths", "deletes"}

DEFAULT_EXTENSIONS = {
    # installers (cleanup.ps1 deletes them, setting: Installers)
    ".exe": "To-Delete", ".msi": "To-Delete", ".msix": "To-Delete",
    # images
    ".jpg": "Pictures", ".jpeg": "Pictures", ".png": "Pictures", ".gif": "Pictures",
    ".webp": "Pictures", ".heic": "Pictures", ".bmp": "Pictures", ".svg": "Pictures",
    # video and audio
    ".mp4": "Videos", ".mov": "Videos", ".mkv": "Videos", ".avi": "Videos",
    ".mp3": "Music", ".wav": "Music", ".flac": "Music", ".m4a": "Music",
    # compressed
    ".zip": "Archives", ".rar": "Archives", ".7z": "Archives", ".tar": "Archives", ".gz": "Archives",
    # code and data
    ".py": "Code", ".ipynb": "Code", ".m": "Code", ".c": "Code", ".cpp": "Code", ".h": "Code", ".js": "Code",
    ".csv": "Data", ".json": "Data", ".mat": "Data", ".xlsx": "Data", ".xls": "Data",
    # slides and 3D
    ".pptx": "Slides", ".ppt": "Slides",
    ".stl": "3D models", ".step": "3D models", ".stp": "3D models", ".blend": "3D models",
}
DEFAULT_CATEGORIES = ["Coursework", "Research papers", "Projects and code", "Admin and receipts", "Work",
                      "Personal", "Software", "Other"]
INSTALLER_FOLDER = "To-Delete"     # tidy_folder.py files installers here, and cleanup deletes them

SECTIONS = {
    "report": (
        Key("min_gb", "number", 1.0, 0, 100000),
        Key("max_depth", "int", 8, 1, 100),
        Key("top_files", "int", 20, 0, 10000),
        Key("cleanup_min_gb", "number", 0.1, 0, 100000),
        Key("target_free_percent", "int", 15, 1, 90),
    ),
    "ai": (
        Key("enabled", "yesno", True),
        Key("model", "text", "llama3.2:3b"),
        Key("url", "url", "http://localhost:11434"),
        Key("timeout_seconds", "int", 180, 5, 3600),
        Key("max_folders", "int", 15, 0, 1000),
        Key("max_apps", "int", 60, 0, 10000),
    ),
    "cleanup": (
        Key("conda_cache", "yesno", True),
        Key("pip_cache", "yesno", True),
        Key("uv_cache", "yesno", True),
        Key("gpu_shader_cache", "yesno", True),
        Key("temp_files", "yesno", True),
        Key("temp_min_age_days", "int", 7, 0, 36500),
        Key("recycle_bin", "yesno", False),
        Key("crash_dumps", "yesno", True),
        Key("hibernation", "yesno", False),
        Key("installers", "yesno", True),
        Key("installers_min_age_days", "int", 7, 0, 36500),
        Key("installer_folders", "paths", ["%USERPROFILE%\\Downloads", "%USERPROFILE%\\Sorted\\To-Delete"]),
        Key("conda_envs_to_remove", "envs", []),
        Key("apps_to_uninstall", "names", []),
        Key("extra_paths_to_delete", "deletes", []),
    ),
    "leftovers": (
        Key("extra_folders", "paths", []),
        Key("duplicate_min_mb", "number", 1.0, 0, 1000000),
        Key("stale_min_mb", "number", 500.0, 0, 10000000),
        Key("stale_after_days", "int", 180, 1, 36500),
        Key("hash_budget_gb", "number", 50.0, 0, 100000),
    ),
    "move": (
        Key("min_gb", "number", 1.0, 0, 100000),
        Key("dest_folder_name", "folder", context.DEST_FOLDER_NAME),
    ),
    "tidy": (
        Key("source", "path", "%USERPROFILE%\\Downloads"),
        Key("dest", "path", "%USERPROFILE%\\Sorted"),
        Key("archive_after_days", "int", 60, 0, 36500),
        Key("skip_newer_than_min", "int", 10, 0, 525600),
        Key("rename_with_ai", "yesno", True),
        Key("categories", "categories", DEFAULT_CATEGORIES),
    ),
}
TABLES = {"tidy.extensions": DEFAULT_EXTENSIONS}       # extension = folder, any number of lines
RULE_KINDS = {
    "label": (
        Key("pattern", "regex", required=True),
        Key("what", "text", required=True),
        Key("todo", "text", "", empty_ok=True),
        Key("kind", "choice", "info", choices=("clean", "info")),
        Key("expand", "yesno", False),
    ),
    "category": (
        Key("pattern", "regex", required=True),
        Key("category", "choice", required=True, choices="category"),
    ),
    "apptype": (
        Key("match", "regex", required=True),
        Key("type", "choice", required=True, choices="type"),
    ),
}

# what a mistake in each section stops ("settings check" says it, and each command acts on it)
CONSEQUENCES = {
    "report": "disk_report.py uses the default instead.",
    "ai": "tidy_folder.py sorts by its rules only (as with --no-llm) until it's fixed; "
          "disk_report.py uses the default instead.",
    "cleanup": ".\\cleanup.ps1 won't run until it's fixed.",
    "leftovers": "disk_report.py uses the default instead.",
    "move": "move_off_c.py won't run until it's fixed.",
    "tidy": "tidy_folder.py (and the weekly sort) won't run until it's fixed.",
    "tidy.extensions": "tidy_folder.py (and the weekly sort) won't run until it's fixed.",
    "rule": "disk_report.py skips this rule until it's fixed.",
    None: "Nothing reads it.",
}

_HEADER = re.compile(r"\[(?P<name>[^\]]*)\](?P<rest>.*)$")
_VAR = re.compile(r"%%|%([^%\s]+)%")
_BOOLS = {"yes": True, "no": False, "true": True, "false": False, "on": True, "off": False, "1": True, "0": False}
_NOT_IN_NAMES = set('\\/:*?"<>|')
_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def template_path():
    return os.path.join(context.APP_DIR, TEMPLATE_NAME)


def section_kind(name):
    """'plain', 'table', 'rule' or None, for a lower-case section name."""
    if name in SECTIONS:
        return "plain"
    if name in TABLES:
        return "table"
    kind, _, rest = name.partition(".")
    if kind in RULE_KINDS and rest.strip():
        return "rule"
    return None


def consequence(section):
    kind = section_kind(section) if section else None
    return CONSEQUENCES["rule" if kind == "rule" else section if kind else None]


# ---------- reading ----------
def load(ctx=None, create=False):
    """The settings for this context. With create=True (the commands), a missing settings.ini is
    made from the template first. Without settings.ini the template's values are used, and without
    the template the defaults in this module."""
    ctx = ctx or context.current()
    path, notes = ctx.settings_file, []
    if create and not os.path.exists(path):
        notes.append(create_from_template(ctx))
    for candidate, from_defaults in ((path, False), (template_path(), True)):
        try:
            with open(candidate, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            continue
        except OSError as e:
            notes.append(f"Couldn't read {candidate} ({e.strerror or e}).")
            continue
        s = parse(data, ctx, name=os.path.basename(candidate), path=candidate, from_defaults=from_defaults)
        s.notes = [n for n in notes if n] + s.notes
        return s
    s = parse(b"", ctx, name=FILE_NAME, path=None, from_defaults=True)
    s.notes = [n for n in notes if n]
    return s


def create_from_template(ctx):
    """Copy the template to settings.ini. Returns a line to show, or None if there is nothing to say."""
    path = ctx.settings_file
    try:
        with open(template_path(), "rb") as f:
            data = f.read()
    except OSError:
        return f"{TEMPLATE_NAME} is missing from {context.APP_DIR}, so pc-tidy uses its defaults."
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)       # pc-tidy.exe keeps it in the data folder
        with open(path, "xb") as f:
            f.write(data)
    except FileExistsError:
        return None
    except OSError as e:
        return f"Couldn't create {path} ({e.strerror or e}), so pc-tidy uses the defaults."
    return f"Made {path} from {TEMPLATE_NAME}. Change it with: {context.command('settings edit')}"


def parse(data, ctx=None, name=FILE_NAME, path=None, from_defaults=False):
    """Settings from the bytes of a settings file."""
    ctx = ctx or context.current()
    s = Settings(ctx, path, name, from_defaults)
    lines, undecodable = _decode(data)
    preamble, chunks = _split(lines)
    for n, text in preamble:
        s.add("error", None, None, n, f"'{_short(text)}' is above the first [section] line, so it is ignored. "
                                     "Move it into the section it belongs to.")
    present = {c.key for c in chunks}
    first = {}
    for c in chunks:
        if c.key in first:
            s.add("error", c.key, None, c.line, f"[{c.name}] appears twice, on lines {first[c.key].line} and "
                                               f"{c.line}. Merge them into one.")
            continue
        first[c.key] = c
    for c in chunks:
        bad = [n for n in undecodable if c.line <= n <= c.last]
        for n in bad:
            s.add("error", c.key, None, n, "this line isn't UTF-8 text. In Notepad: File > Save as, "
                                          "Encoding: UTF-8.")
        if first.get(c.key) is c:
            s._section(c, present)
    for n in undecodable:
        if not any(c.line <= n <= c.last for c in chunks):
            s.add("error", None, None, n, "this line isn't UTF-8 text. In Notepad: File > Save as, Encoding: UTF-8.")
    s.problems.sort(key=lambda p: (p.line or 0, p.level != "error"))
    return s


def _decode(data):
    """(lines, numbers of the lines that aren't UTF-8). Lines are split like configparser splits them."""
    try:
        text = data.decode("utf-8-sig")
        bad = []
    except UnicodeDecodeError:
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        parts, bad = [], []
        for n, raw in enumerate(re.split(rb"\r\n|\r|\n", data), 1):
            try:
                parts.append(raw.decode("utf-8"))
            except UnicodeDecodeError:
                parts.append(raw.decode("utf-8", errors="replace"))
                bad.append(n)
        text = "\n".join(parts)
    lines = re.split(r"\r\n|\r|\n", text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines, bad


class _Chunk:
    """One [section] and the lines below it, up to the next one."""

    def __init__(self, name, line, rest):
        self.name = name.strip()
        self.key = self.name.lower()
        self.line = self.last = line
        self.rest = rest.strip()
        self.lines = []       # (number, text) after the header, every line, for configparser
        self.keys = []        # [key, line, value on that line, [(line, text) of indented lines]]
        self.bad = []         # (line, text) of lines that aren't "key = value"


def _split(lines):
    """(lines above the first section, chunks). A line is read the way configparser reads it: blank
    lines and comments are skipped, a line indented more than its key belongs to that key, and any
    other line is a [section] line or a "key = value" line."""
    preamble, chunks = [], []
    chunk, cur, key_indent = None, None, 0
    for n, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if not stripped or stripped[0] in "#;":
            if chunk is not None:
                chunk.lines.append((n, raw))
                chunk.last = n
            continue
        indent = len(raw) - len(raw.lstrip())
        if cur is not None and indent > key_indent:
            cur[3].append((n, stripped))
            chunk.lines.append((n, raw))
            chunk.last = n
            continue
        m = _HEADER.match(stripped)
        if m:
            chunk = _Chunk(m.group("name"), n, m.group("rest"))
            chunks.append(chunk)
            cur = None
            continue
        if chunk is None:
            preamble.append((n, stripped))
            continue
        chunk.lines.append((n, raw))
        chunk.last = n
        key_indent = indent
        key, eq, value = stripped.partition("=")
        if eq and key.strip():
            cur = [key.strip().lower(), n, value.strip(), []]
            chunk.keys.append(cur)
        else:
            chunk.bad.append((n, stripped))      # configparser keeps the key before it, so do we
    return preamble, chunks


def _configparser(chunk):
    """configparser's reading of one section: ({key: value}, [lines it couldn't read], duplicate key
    line or None). Its line 1 is the [section] line."""
    text = "[s]\n" + "\n".join(raw for _n, raw in chunk.lines)
    number = {i + 2: n for i, (n, _raw) in enumerate(chunk.lines)}

    def parser(strict):
        return configparser.ConfigParser(interpolation=None, strict=strict, delimiters=("=",),
                                         comment_prefixes=("#", ";"), inline_comment_prefixes=None,
                                         default_section="\x00")

    bad, duplicate = [], None
    cp = parser(True)
    try:
        cp.read_string(text)
    except configparser.DuplicateOptionError as e:
        duplicate = number.get(e.lineno)
        cp = parser(False)
        try:
            cp.read_string(text)
        except configparser.ParsingError as e2:
            bad = [number.get(err[0]) for err in e2.errors]
    except configparser.ParsingError as e:
        bad = [number.get(err[0]) for err in e.errors]
    values = dict(cp["s"]) if cp.has_section("s") else {}
    return values, [n for n in bad if n], duplicate


def expand(text, env):
    """(text with %NAME% filled in from env, [problems]). Names ignore case; %% is a % sign."""
    out, problems, pos = [], [], 0
    for m in _VAR.finditer(text):
        out.append(text[pos:m.start()])
        if "%" in text[pos:m.start()]:
            problems.append("lone")
        pos = m.end()
        if m.group(0) == "%%":
            out.append("%")
        elif m.group(1).upper() in env:
            out.append(env[m.group(1).upper()])
        else:
            problems.append(m.group(0))
            out.append(m.group(0))
    out.append(text[pos:])
    if "%" in text[pos:]:
        problems.append("lone")
    return "".join(out), problems


def url_problem(url):
    """Why pc-tidy may not talk to this AI address, or None. Only this PC is allowed."""
    try:
        parts = urlsplit(url)
        host, _port = parts.hostname, parts.port
    except ValueError:
        return f"'{url}' isn't a web address like http://localhost:11434"
    if parts.scheme not in ("http", "https") or not host:
        return f"'{url}' isn't a web address like http://localhost:11434"
    if host.lower() not in LOCAL_HOSTS:
        return (f"url must point at this PC (localhost, 127.0.0.1 or [::1]), got '{url}'. pc-tidy never "
                "sends your file names off this PC.")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return f"url is only the address of Ollama, like http://localhost:11434, got '{url}'"
    return None


def folder_name_problem(name):
    """Why `name` can't be a folder name, or None."""
    if not name:
        return "is empty"
    if any(c in _NOT_IN_NAMES or ord(c) < 32 for c in name):
        return 'must be a folder name, so it can\'t contain \\ / : * ? " < > |'
    if name.endswith((".", " ")):
        return "can't end with a dot or a space"
    if name.split(".")[0].lower() in _RESERVED:
        return "is a name Windows keeps for itself"
    return None


def _full_path(path):
    drive, rest = os.path.splitdrive(path)
    return bool(drive) and (drive.startswith(("\\\\", "//")) or rest[:1] in ("\\", "/"))


def _short(text, limit=60):
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _vocabulary(name):
    import classify        # next to pc_tidy.py, so on the path whenever a pc-tidy command runs
    return {"category": classify.FOLDER_CATEGORIES, "type": classify.APP_TYPES}[name]


class Settings:
    """What load() read: values, where each came from, and every mistake with its line."""

    def __init__(self, ctx, path, name, from_defaults):
        self.ctx = ctx
        self.path = path                       # the file read, or None
        self.name = name                       # its name in messages
        self.from_defaults = from_defaults     # True if there was no settings.ini to read
        self.entries = {}                      # (section, key) -> Entry
        self.tables = {}                       # section -> ({key: value}, [lines])
        self.rules = []
        self.problems = []
        self.notes = []
        self.env = dict(ctx.env)
        self.env.setdefault("USERPROFILE", ctx.profile)

    # ---------- asking ----------
    def errors(self, *sections):
        """The mistakes in these sections (all of them if none are named)."""
        return [p for p in self.problems if p.level == "error" and (not sections or p.section in sections)]

    def warnings(self, *sections):
        return [p for p in self.problems if p.level == "warning" and (not sections or p.section in sections)]

    def key_errors(self, section, *keys):
        """The mistakes that affect these keys: their own, and the ones of the whole section."""
        return [p for p in self.errors(section) if p.key is None or p.key in keys]

    def value(self, section, key):
        """The value to use: from settings.ini, or the default if it isn't there or has a mistake."""
        entry = self.entries.get((section, key))
        if entry is None or self.key_errors(section, key):
            return self.default(section, key)
        return entry.value

    def default(self, section, key):
        k = _key(section, key)
        if k.kind == "path":
            return os.path.normpath(expand(k.default, self.env)[0])
        if k.kind in PATH_KINDS:
            return [os.path.normpath(expand(p, self.env)[0]) for p in k.default]
        return list(k.default) if isinstance(k.default, list) else k.default

    def raw(self, section, key):
        entry = self.entries.get((section, key))
        if entry is None or self.key_errors(section, key):
            return _key(section, key).default
        return entry.raw

    def section(self, name):
        """Every key of a section as attributes, e.g. settings.section("tidy").source."""
        return SimpleNamespace(**{k.name: self.value(name, k.name) for k in SECTIONS[name]})

    def table(self, name):
        if name not in self.tables or self.errors(name):
            return dict(TABLES[name])
        return dict(self.tables[name][0])

    def good_rules(self):
        """Your rules from the rule sections that have no mistake, in the order of the file."""
        broken = {p.section for p in self.errors()}
        return [r for r in self.rules if r.section not in broken]

    def where(self, section, key):
        """Where the value comes from: 'settings.ini line 12', 'default', ..."""
        errors = self.key_errors(section, key)
        if errors:
            return f"default ({self.describe_place(errors[0])} has a mistake)"
        entry = self.entries.get((section, key))
        if entry is None or self.from_defaults:
            return "default"
        return self._lines(entry.lines)

    def _lines(self, lines):
        if not lines:
            return self.name
        if lines[0] == lines[-1]:
            return f"{self.name} line {lines[0]}"
        return f"{self.name} lines {lines[0]}-{lines[-1]}"

    def describe_place(self, p):
        return f"{self.name} line {p.line}" if p.line else self.name

    def describe(self, p):
        """'settings.ini line 14 in [cleanup]: unknown key ...'"""
        where = self.describe_place(p)
        return f"{where} in [{p.section}]: {p.message}" if p.section else f"{where}: {p.message}"

    # ---------- building ----------
    def add(self, level, section, key, line, message):
        self.problems.append(Problem(level, section, key, line, message))

    def _section(self, c, present):
        kind = section_kind(c.key)
        if c.rest and c.rest[0] not in "#;":
            self.add("error", c.key or None, None, c.line,
                     f"'{_short(c.rest)}' after [{c.name}] on the same line. Put it on a line of its own.")
        if not c.key:
            self.add("error", None, None, c.line, "a section needs a name between [ and ].")
            return
        if kind is None:
            self._unknown_section(c, present)
            return
        values, unread, duplicate = _configparser(c)
        for n in sorted(set(unread) | {n for n, _t in c.bad}):
            text = dict(c.bad).get(n) or dict(c.lines).get(n, "").strip()
            hint = " List items go on indented lines under their key." if _full_path(text) else ""
            self.add("error", c.key, None, n, f"'{_short(text)}' isn't 'key = value'.{hint}")
        if kind == "plain":
            schema = {k.name: k for k in SECTIONS[c.key]}
        elif kind == "rule":
            schema = {k.name: k for k in RULE_KINDS[c.key.partition(".")[0]]}
        else:
            schema = None
        seen, table, table_lines = {}, {}, []
        for key, line, first, more in c.keys:
            if key in seen:
                self.add("error", c.key, key, line, f"{key} is set twice, on lines {seen[key]} and {line}. "
                                                    "Delete one of them.")
                continue
            seen[key] = line
            raw = values.get(key, first)
            items = ([(line, first)] if first else []) + more
            if schema is None:
                self._table_entry(c, key, raw, line, items, table)
                table_lines += [n for n, _t in items] or [line]
                continue
            if key not in schema:
                self._unknown_key(c, key, line, schema)
                continue
            self._entry(c.key, schema[key], raw, line, items)
        if duplicate and not any(p.line == duplicate for p in self.problems):
            self.add("error", c.key, None, duplicate, "a key is set twice in this section. Delete one of them.")
        if kind == "table":
            self.tables[c.key] = (table, table_lines or [c.line])
        elif kind == "rule":
            self._rule(c, schema)

    def _unknown_section(self, c, present):
        rest = c.key.partition(".")[2]
        known = list(SECTIONS) + list(TABLES) + [f"{k}.{rest or '<name>'}" for k in RULE_KINDS]
        match = difflib.get_close_matches(c.key, known, n=1, cutoff=0.75)
        if match:
            # a typo in the name of a section that isn't in the file: its settings are lost, so the
            # section it was meant to be counts as broken, never as quietly using its defaults
            target = match[0] if match[0] in SECTIONS or match[0] in TABLES else c.key
            if target in present:
                target = c.key
            self.add("error", target, None, c.line, f"unknown section [{c.name}]. Did you mean [{match[0]}]?")
        else:
            self.add("error", c.key, None, c.line,
                     f"unknown section [{c.name}]. The sections are [report], [ai], [cleanup], [move], [tidy], "
                     "[tidy.extensions], and your own rules [label.<name>], [category.<name>] and "
                     "[apptype.<name>].")

    def _unknown_key(self, c, key, line, schema):
        match = difflib.get_close_matches(key, list(schema), n=1, cutoff=0.6)
        if match:
            # most likely a typo of that key, whose value is then not what you meant
            self.add("error", c.key, match[0], line, f"unknown key '{key}'. Did you mean '{match[0]}'?")
        else:
            self.add("error", c.key, None, line,
                     f"unknown key '{key}'. The keys of [{c.name}] are: {', '.join(schema)}.")

    def _entry(self, section, k, raw, line, items):
        """Check one value and keep it, with any mistakes, as (section, key)."""
        lines = [n for n, _t in items] or [line]
        if k.kind in LIST_KINDS:
            texts = [t for _n, t in items]
            if texts != [x.strip() for x in raw.split("\n") if x.strip()]:
                items = [(line, x.strip()) for x in raw.split("\n") if x.strip()]    # never seen: trust configparser
            value = self._list(section, k, items)
            self.entries[(section, k.name)] = Entry(value, [t for _n, t in items], lines)
            return
        if len(items) > 1:
            self.add("error", section, k.name, items[1][0],
                     f"{k.name} takes one value, on the line of the key. Lines {items[1][0]}-{items[-1][0]} "
                     "are indented under it, which makes them part of it: remove their indent.")
        text = raw.strip().split("\n")[0].strip()
        if k.kind in INLINE_COMMENTS:
            text = re.split(r"[#;]", text, maxsplit=1)[0].strip()
        value = self._scalar(section, k, text, line)
        self.entries[(section, k.name)] = Entry(value, text, lines)

    def _warn_hash(self, section, key, line, text):
        m = re.search(r"\s(#.*)$", text)
        if m:
            self.add("warning", section, key, line, f"if '{_short(m.group(1), 40)}' is a comment, put it on its "
                                                    "own line.")

    def _scalar(self, section, k, text, line):
        def bad(message):
            self.add("error", section, k.name, line, message)

        if not text and not k.empty_ok:
            if k.default is None:
                bad(f"{k.name} is empty.")
            else:
                bad(f"{k.name} is empty. Write a value, or delete the line to use the default ({_show(k.default)}).")
            return None
        if k.kind not in INLINE_COMMENTS:
            self._warn_hash(section, k.name, line, text)
        if k.kind == "yesno":
            if text.lower() not in _BOOLS:
                bad(f"{k.name} must be yes or no, got '{text}'")
                return None
            return _BOOLS[text.lower()]
        if k.kind in ("int", "number"):
            return self._number(k, text, bad)
        if k.kind == "text":
            return text
        if k.kind == "url":
            problem = url_problem(text)
            if problem:
                bad(problem)
            return text.rstrip("/")
        if k.kind == "folder":
            problem = folder_name_problem(text)
            if problem:
                bad(f"{k.name} {problem}, got '{text}'")
            return text
        if k.kind == "path":
            return self._path(section, k.name, text, line)
        if k.kind == "regex":
            return self._regex(section, k.name, text, line)
        if k.kind == "choice":
            choices = _vocabulary(k.choices) if isinstance(k.choices, str) else k.choices
            for choice in choices:
                if text.lower() == choice.lower():
                    return choice
            match = difflib.get_close_matches(text, choices, n=1, cutoff=0.6)
            hint = f" Did you mean '{match[0]}'?" if match else ""
            bad(f"{k.name} must be one of: {', '.join(choices)}. Got '{text}'.{hint}")
            return None
        raise ValueError(k.kind)

    def _number(self, k, text, bad):
        whole = k.kind == "int"
        if whole and re.fullmatch(r"[+-]?\d+", text):
            value = int(text)
        elif not whole and re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)", text):
            value = float(text)
        else:
            if re.fullmatch(r"[+-]?\d+,\d+", text):
                bad(f"{k.name} must be written with a dot, like {text.replace(',', '.')}, got '{text}'")
            elif whole and re.fullmatch(r"[+-]?\d*\.\d+", text):
                bad(f"{k.name} must be a whole number, got '{text}'")
            else:
                bad(f"{k.name} must be a {'whole ' if whole else ''}number, got '{text}'")
            return None
        if not k.low <= value <= k.high:
            bad(f"{k.name} must be from {_show(k.low)} to {_show(k.high)}, got {text}")
            return None
        return value

    def _path(self, section, key, text, line, wildcards=False):
        """A full path with %NAME% filled in, or None (and a mistake)."""
        def bad(message):
            self.add("error", section, key, line, message)
            return None

        if text.lower().startswith(("$env:", "${env:")):
            name = re.match(r"\$\{?env:([\w()]+)\}?", text, re.I)
            better = f"%{name.group(1)}%" + text[name.end():] if name else text
            return bad(f"'{text}' is PowerShell. In settings.ini write {better}")
        value, problems = expand(text, self.env)
        undefined = [p for p in problems if p != "lone"]
        if undefined:
            return bad(f"{', '.join(undefined)} in '{text}' isn't set on this PC. (Write %% for a % sign in a name.)")
        if problems:
            return bad(f"'{text}' has a % sign on its own. Write %% for a % sign in a name.")
        if not _full_path(value):
            return bad(f"{key} must be a full path that starts with a drive, like D:\\Stuff, got '{text}'")
        if not wildcards and any(c in value for c in "*?"):
            return bad(f"{key} can't have wildcards (* or ?), got '{text}'")
        return os.path.normpath(value)

    def _regex(self, section, key, text, line):
        try:
            pattern = re.compile(text)
        except re.error as e:
            self.add("error", section, key, line, f"{key} isn't a valid pattern: {e.msg} at character {e.pos + 1}"
                                                  if e.pos is not None else f"{key} isn't a valid pattern: {e.msg}")
            return None
        if re.search(r"[A-Z]", re.sub(r"\\.", "", text)):
            what = "app names and publishers" if key == "match" else "paths"
            self.add("warning", section, key, line,
                     f"{key} has capital letters, but {what} are matched in lower case, so they never match. "
                     "Write it in lower case.")
        return pattern

    def _list(self, section, k, items):
        out = []
        for n, text in items:
            self._warn_hash(section, k.name, n, text)
            if k.kind in PATH_KINDS:
                value = self._path(section, k.name, text, n, wildcards=k.kind == "deletes")
                if value is None:
                    continue
                if k.kind == "deletes":
                    value = self._delete_target(section, k.name, text, value, n)
                    if value is None:
                        continue
            elif k.kind == "envs":
                if text.lower() == "base":
                    self.add("error", section, k.name, n, "'base' is conda's own environment and can't be removed.")
                    continue
                if not re.fullmatch(r"[^\s/\\:#]+", text):
                    self.add("error", section, k.name, n, f"'{text}' isn't a conda environment name.")
                    continue
                value = text
            elif k.kind == "categories":
                problem = folder_name_problem(text)
                if problem:
                    self.add("error", section, k.name, n, f"category '{text}' {problem}")
                    continue
                if text.lower() == INSTALLER_FOLDER.lower():
                    self.add("error", section, k.name, n,
                             f"categories can't include {INSTALLER_FOLDER}: cleanup deletes installers there, so the "
                             "AI must never file anything there.")
                    continue
                if text.lower() in (x.lower() for x in out):
                    self.add("warning", section, k.name, n, f"category '{text}' is listed twice.")
                    continue
                value = text
            else:
                value = text
            out.append(value)
        return out

    def _delete_target(self, section, key, text, value, line):
        from . import safety
        problem = safety.why_refused(value, "delete", self.ctx)
        if not problem:
            return value
        if problem.startswith("'"):
            message = f"{key} {problem}"           # "'D:\*' is refused: a wildcard directly under ..."
        else:
            message = f"{key} '{text}' is refused: {problem}."
        self.add("error", section, key, line, message)
        return None

    def _table_entry(self, c, key, raw, line, items, table):
        if len(items) > 1:
            self.add("error", c.key, None, items[1][0], f"{key} takes one folder, on the line of the extension. "
                                                        "Remove the indent of the lines under it.")
            return
        if not re.fullmatch(r"\.[^.\s\\/:*?\"<>|]+", key):
            self.add("error", c.key, None, line,
                     f"'{key}' must be one extension that starts with a dot, like .pdf"
                     + (" (only the last part counts: .gz for .tar.gz)" if key.count(".") > 1 else ""))
            return
        folder = raw.strip()
        problem = folder_name_problem(folder)
        if problem:
            self.add("error", c.key, None, line, f"the folder for {key} {problem}, got '{folder}'")
            return
        self._warn_hash(c.key, None, line, folder)
        table[key] = folder

    def _rule(self, c, schema):
        fields = {}
        for k in schema.values():
            entry = self.entries.pop((c.key, k.name), None)
            if entry is None:
                if k.required:
                    self.add("error", c.key, None, c.line, f"[{c.name}] needs a line '{k.name} = ...'.")
                fields[k.name] = k.default
            else:
                fields[k.name] = entry.value
        self.rules.append(Rule(c.key.partition(".")[0], c.key.partition(".")[2], c.key, c.line, fields))


def _key(section, key):
    for k in SECTIONS[section]:
        if k.name == key:
            return k
    raise KeyError((section, key))


def default_of(section, key):
    """A key's default as the template writes it (paths keep their %NAME%)."""
    return _key(section, key).default


def _show(value):
    """A value as it is written in settings.ini."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


# ---------- telling ----------
def show_text(s):
    """Every value and where it came from (python pc_tidy.py settings show)."""
    if s.from_defaults:
        out = ["There is no settings.ini, so these are the defaults."]
    else:
        out = [f"Settings from {s.path}"]
    for name, keys in SECTIONS.items():
        out.append(f"\n[{name}]")
        for k in keys:
            value, where = s.value(name, k.name), s.where(name, k.name)
            raw = s.raw(name, k.name)
            if k.kind in LIST_KINDS:
                out.append(_column(f"  {k.name} =", where))
                shown = value if k.kind not in PATH_KINDS else raw
                for i, item in enumerate(shown):
                    extra = f"   ({value[i]})" if k.kind in PATH_KINDS and i < len(value) and value[i] != item else ""
                    out.append(f"      {item}{extra}")
                continue
            text = _show(value)
            if k.kind == "path" and raw != value:
                text = f"{raw}   ({value})"
            out.append(_column(f"  {k.name} = {text}", where))
    for name in TABLES:
        table = s.table(name)
        errors = s.errors(name)
        if errors:
            where = f"default ({s.describe_place(errors[0])} has a mistake)"
        elif name in s.tables and not s.from_defaults:
            where = s._lines(s.tables[name][1])
        else:
            where = "default"
        out.append(_column(f"\n[{name}]", where))
        for ext, folder in table.items():
            out.append(f"  {ext} = {folder}")
    if s.rules:
        out.append("\nYour own rules (checked before the built-in ones):")
        broken = {p.section for p in s.errors()}
        for r in s.rules:
            state = "skipped, it has a mistake" if r.section in broken else s._lines([r.line])
            out.append(_column(f"  [{r.section}]", state))
            for key, value in r.fields.items():
                text = value.pattern if hasattr(value, "pattern") else _show(value)
                out.append(f"      {key} = {text}")
    problems = check_text(s, quiet=True)
    if problems:
        out.append("")
        out += problems
    return "\n".join(out)


def _column(left, right, width=48):
    return f"{left:<{width}} {right}" if len(left) < width else f"{left}   {right}"


def check_text(s, quiet=False):
    """Every mistake and warning, each with what it stops (python pc_tidy.py settings check)."""
    errors, warnings = s.errors(), s.warnings()
    out = list(s.notes)
    if not errors and not warnings:
        if not quiet:
            out.append(f"{s.name} is fine: no mistakes." if not s.from_defaults else
                       "There is no settings.ini, so pc-tidy uses its defaults.")
        return out
    if errors:
        out.append(f"{s.name} has {len(errors)} mistake{'s' if len(errors) != 1 else ''}:")
        told = set()
        for p in errors:
            out.append(f"  {s.describe(p)}")
            if p.section not in told:
                told.add(p.section)
                out.append(f"      {consequence(p.section)}")
    if warnings:
        out.append(f"{'And ' if errors else ''}{len(warnings)} warning{'s' if len(warnings) != 1 else ''} "
                   "(these don't stop anything):")
        out += [f"  {s.describe(p)}" for p in warnings]
    return out


def refusal(s, problems, command):
    """What a command prints when a section it reads has a mistake."""
    out = [f"{command} didn't start: {s.name} has {'a mistake' if len(problems) == 1 else 'mistakes'} "
           "in a section it reads."]
    out += [f"  {s.describe(p)}" for p in problems]
    out.append(f"Fix it with:  {context.command('settings edit')}   (the file is {s.path})")
    return "\n".join(out)
