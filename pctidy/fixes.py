r"""The fixes for C:, ranked: what to do first to free space on the system drive, how much each frees,
how risky it is, and exactly how to do it. disk_report.py puts this list on its first page.

Every number is measured, file by file, over exactly the files the fix would remove:
- a file with more than one hard link counts 0, because removing one link frees nothing (a conda
  environment whose files are hard links into conda's package cache frees far less than its size)
- a compressed or sparse file counts what it takes on disk, a cloud-only OneDrive file counts 0
- links (junctions, symlinks) are never followed: deleting one frees nothing
- a file already counted by a fix higher in the list isn't counted again
A folder this window can't open shows "needs admin to measure" instead of a number. Measuring one
fix stops after MEASURE_SECONDS or MEASURE_FILES files and then shows "up to" the scanned size. Such
rows sort last in their risk group and add nothing to the running total.

The risk of a fix comes from this module, never from settings or the AI:
    Low     it comes back by itself, and nothing of yours is lost
    Medium  it can be undone or rebuilt, with some effort
    High    it is gone for good
"""

import os
import re
import shutil
import time

from . import _win, context, safety
from .progress import Line
from .paths import expand, inside, lp, norm

GB = 1024 ** 3
MB = 1024 ** 2
MEASURE_SECONDS = 60
MEASURE_FILES = 300_000
MIN_ROW_BYTES = MB              # a fix that frees less than this isn't listed
MAX_NODE_FOLDERS = 10
RISKS = ("Low", "Medium", "High")
CLEANUP = "cleanup.ps1"
INSTALLER_TYPES = (".exe", ".msi", ".msix", ".msixbundle", ".appx", ".appxbundle")    # cleanup.ps1's list

_CLOUD_ONLY = 0x1000 | 0x40000 | 0x400000
_COMPRESSED_OR_SPARSE = 0x800 | 0x200
_LINK_TAGS = (0xA0000003, 0xA000000C)       # junction, symlink

# the fixes cleanup.ps1 does inside protected folders, one per row of safety.ALLOWED:
# (its switches in [cleanup], why the risk is low)
_TABLE_FIXES = {
    "temp-user": (("temp_files",), "Apps make them again"),
    "temp-windows": (("temp_files",), "Windows makes them again"),
    "caches": (("pip_cache", "uv_cache"), "Downloaded again when needed"),
    "shader-caches": (("gpu_shader_cache",), "Your graphics driver rebuilds them"),
    "crash-dumps-user": (("crash_dumps",), "Only needed to find out why an app crashed"),
    "crash-dumps-windows": (("crash_dumps",), "Only needed to find out why Windows crashed"),
}
_CONDA_PKGS = re.compile(r"(^|/)(ana|mini)conda3?/pkgs$")
_CONDA_ENVS = re.compile(r"(^|/)(ana|mini)conda3?/envs$")
_BROWSERS = ((re.compile(r"/google/chrome/user data$"), "Chrome"), (re.compile(r"/microsoft/edge/user data$"), "Edge"))
_BROWSER_CACHES = ("Cache", "Code Cache", "GPUCache")
_NODE = re.compile(r"/node_modules$")
# node_modules of apps (VS Code's, npm's own) are not yours to delete; checked below your profile
_APP_FOLDERS = re.compile(r"/(appdata|program files[^/]*|programdata|windows|\.vscode|\.cache)/")


def gb(n):
    """'3.1 GB', or '250 MB' below a GB."""
    return f"{n / GB:.1f} GB" if n >= GB else f"{n / MB:.0f} MB"


def _key(path):
    """How files are compared: the normalised path."""
    return norm(path)


class Fix:
    """One row. Measuring fills in bytes and state: measured, about (the tool decides the exact
    amount), up_to or at_least (measuring ran out of time, with or without the scanned size to say
    'up to'), admin (needs admin to measure) or unknown."""

    def __init__(self, id, title, risk, why, how, undo, by="manual", scope="user"):
        assert risk in RISKS
        self.id, self.title, self.risk, self.why, self.how, self.undo, self.by = id, title, risk, why, how, undo, by
        self.scope = scope      # "machine": only an administrator window can do it
        self.action = None      # what the guided flow does, see guided.py; None: you do it by hand
        self.bytes, self.state, self.note, self.limit = 0, "measured", "", 0
        self.files = {}         # normalised path -> bytes it frees
        self.folders = []       # (normalised folder, bytes): a move frees a whole folder
        self.total = None       # the running total, for counted rows
        self.where = ""         # the folder, added to the title when two fixes have the same one

    @property
    def counted(self):
        return self.state in ("measured", "about")

    def size_text(self):
        if self.state == "admin":
            return "needs admin to measure"
        if self.state == "unknown":
            return "unknown"
        if self.state == "up_to":
            return f"up to {gb(self.limit)}"
        if self.state == "at_least":
            return f"at least {gb(self.limit)}"
        return ("about " if self.state == "about" else "") + gb(self.bytes)

    def as_dict(self):
        return {"id": self.id, "title": self.title, "risk": self.risk, "why": self.why, "how": self.how,
                "undo": self.undo, "by": self.by, "bytes": self.bytes, "size": self.size_text(),
                "counted": self.counted, "state": self.state, "total": self.total, "note": self.note,
                "scope": self.scope, "action": self.action}


# ---------- measuring ----------
class _OverBudget(Exception):
    pass


class _Budget:
    def __init__(self):
        self.deadline, self.files = time.monotonic() + MEASURE_SECONDS, 0

    def tick(self):
        self.files += 1
        if self.files > MEASURE_FILES or (self.files % 1000 == 0 and time.monotonic() > self.deadline):
            raise _OverBudget()


def file_bytes(path, listed=None):
    """What deleting one file frees. `listed` is its stat from the folder listing, if there is one."""
    if listed is not None and listed.st_file_attributes & _CLOUD_ONLY:
        return 0            # online-only: checked before opening it, so it is never downloaded
    try:
        st = os.stat(path, follow_symlinks=False)     # opens the file: a listing has no link count on Windows
    except OSError:
        return listed.st_size if listed is not None else 0       # in use, say: the listing's size
    attrs = st.st_file_attributes
    if attrs & _CLOUD_ONLY or st.st_nlink > 1:
        return 0
    if attrs & _COMPRESSED_OR_SPARSE:
        try:
            return _win.compressed_size(path)
        except OSError:
            pass
    return st.st_size


class _Measure:
    """The files of one fix, into fix.files. admin is set when a folder can't be opened."""

    def __init__(self, fix, ctx):
        self.fix, self.ctx, self.budget = fix, ctx, _Budget()
        self.admin = self.partly = False

    def allowed(self, path):
        return not self.ctx.sandbox or self.ctx.in_sandbox(path)     # the tests never measure this PC

    def contents(self, folder):
        """Every file below a folder (not the folder itself)."""
        stack, first = [lp(folder)], True
        while stack:
            current = stack.pop()
            try:
                entries = os.scandir(current)
            except PermissionError:
                if first:
                    self.admin = True
                    return
                self.partly = True
                continue
            except OSError:
                if first:
                    return       # not there
                continue
            first = False
            with entries:
                for e in entries:
                    try:
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if e.is_symlink() or getattr(st, "st_reparse_tag", 0) in _LINK_TAGS:
                        continue
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                        continue
                    self.budget.tick()
                    self.fix.files[_key(e.path)] = file_bytes(e.path, st)

    def matching(self, pattern):
        """The files a pattern like C:\\Windows\\Minidump\\*.dmp matches (or the one file it names)."""
        if "*" not in pattern:
            return self.file(pattern)
        parent = pattern[:pattern.index("*")].rsplit("\\", 1)[0]
        try:
            os.listdir(parent)          # glob says nothing when it can't open a folder
        except PermissionError:
            self.admin = True
            return
        except OSError:
            return
        for path in expand(pattern):
            if os.path.isfile(path):
                self.budget.tick()
                self.fix.files[_key(path)] = file_bytes(path)

    def file(self, path):
        if os.path.isfile(path):
            self.fix.files[_key(path)] = file_bytes(path)


def measure(fix, ctx, contents=(), patterns=(), files=(), limit=0):
    """Fill in fix.files, fix.bytes and fix.state. Returns the fix, or None if none of its places
    are inside the test sandbox."""
    m = _Measure(fix, ctx)
    places = [p for p in list(contents) + list(patterns) + list(files) if m.allowed(p)]
    if not places:
        return None
    try:
        for folder in contents:
            if m.allowed(folder):
                m.contents(folder)
        for pattern in patterns:
            if m.allowed(pattern):
                m.matching(pattern)
        for path in files:
            if m.allowed(path):
                m.file(path)
    except _OverBudget:
        fix.state, fix.limit = ("up_to", limit) if limit else ("at_least", sum(fix.files.values()))
        fix.note = (f"Measuring stopped after {MEASURE_FILES:,} files or {MEASURE_SECONDS} s, so this row isn't in "
                    "the total.")
        fix.bytes = 0
        return fix
    fix.bytes = sum(fix.files.values())
    if m.admin and not fix.files:
        fix.state = "admin"
    elif m.admin or m.partly:
        fix.note = "Some of it is in folders only an administrator can open, so it may free more."
    return fix


# ---------- the fixes ----------
def _cleanup_how(cfg, switches):
    """'cleanup.ps1: pip_cache, uv_cache (on)', or what to switch on first."""
    off = [k for k in switches if not cfg.value("cleanup", k)]
    if not off:
        return f"{CLEANUP}: {', '.join(switches)} (on)"
    return f"{CLEANUP}: set {' and '.join(f'{k} = yes' for k in off)}"


def _listed_how(cfg, key, value, what):
    listed = [x.lower() for x in cfg.value("cleanup", key)]
    if value.lower() in listed:
        return f"{CLEANUP}: {what} is in {key}"
    return f"{CLEANUP}: add {value} to {key}"


def table_fixes(ctx, cfg, notes):
    """The fixes cleanup.ps1 does inside protected folders, measured over the safety table's rows."""
    out = []
    temp_problem = safety.temp_problem(ctx)
    for row in safety.ALLOWED:
        if row.fix == "temp-user" and temp_problem:
            notes.append(temp_problem.replace("The Temp fix was skipped.", "It isn't listed, and cleanup.ps1 "
                                                                          "leaves it alone."))
            continue
        switches, why = _TABLE_FIXES[row.fix]
        fix = Fix(row.fix, row.title, "Low", why, _cleanup_how(cfg, switches), "Not needed", by=CLEANUP,
                  scope=row.scope)
        values = {"temp": ctx.temp, "system_root": ctx.system_root, "local": ctx.local_appdata}
        paths = [p.format(**values) for p in row.patterns]
        kind = "contents" if row.kind == "contents" else "patterns"
        fix.action = {"delete": kind, "places": paths}
        out.append(measure(fix, ctx, **{kind: paths}))
    return out


def _folders(scan, pattern, ctx):
    """Scanned folders that match a pattern, except those inside a Temp folder: the Temp fixes cover
    them already (a copy of a profile left in Temp has its own conda and Chrome folders, say)."""
    temps = (ctx.temp, os.path.join(ctx.system_root, "Temp"))
    return [f for f in scan.sizes if pattern.search(f.replace("\\", "/").lower())
            and not any(inside(f, t) for t in temps)]


def conda_fixes(scan, ctx, cfg):
    out = []
    for pkgs in _folders(scan, _CONDA_PKGS, ctx):
        fix = Fix("conda-pkgs", "conda package cache", "Low", "Downloaded again when needed. Packages your "
                  "environments use are hard links, so they don't count", _cleanup_how(cfg, ("conda_cache",)) +
                  ", in a window where conda works", "Not needed", by=CLEANUP)
        fix.action = {"tool": "conda_clean"}
        fix = measure(fix, ctx, contents=[pkgs], limit=scan.sizes[pkgs])
        if fix:
            fix.where = pkgs
            fix.state = "about" if fix.state == "measured" else fix.state      # conda clean decides exactly
            out.append(fix)
    for envs in _folders(scan, _CONDA_ENVS, ctx):
        for env in scan.children.get(envs, []):
            name = os.path.basename(env)
            fix = Fix(f"conda-env:{name}", f"conda environment {name}", "Medium",
                      "Can be made again from its packages", _listed_how(cfg, "conda_envs_to_remove", name, name),
                      f"conda create -n {name} ... again", by=CLEANUP)
            fix.action = {"tool": "conda_env_remove", "name": name}
            fix = measure(fix, ctx, contents=[env], limit=scan.sizes.get(env, 0))
            if fix:
                fix.where = env
                size = scan.sizes.get(env, 0)
                if fix.counted and size > MB and fix.bytes < size * 0.9:
                    fix.note = (f"{gb(size)} of files, {gb(fix.bytes)} freed: the rest is shared with conda's "
                                "package cache")
                out.append(fix)
    return out


def browser_fixes(scan, ctx):
    out = []
    for pattern, browser in _BROWSERS:
        for user_data in _folders(scan, pattern, ctx):
            caches = [os.path.join(profile, name) for profile in scan.children.get(user_data, [])
                      for name in _BROWSER_CACHES]
            fix = Fix(f"browser:{browser.lower()}", f"{browser} cache", "Low", f"{browser} downloads it again",
                      f"In {browser}: Settings > Delete browsing data > Cached images and files", "Not needed")
            fix = measure(fix, ctx, contents=[c for c in caches if c in scan.sizes])
            if fix:
                fix.where = user_data
                fix.state = "about" if fix.state == "measured" else fix.state     # the browser decides exactly
                out.append(fix)
    return out


def installers_fix(ctx, cfg):
    days = cfg.value("cleanup", "installers_min_age_days")
    folders = cfg.value("cleanup", "installer_folders")
    cutoff = time.time() - days * 86400
    fix = Fix("installers", f"Installers older than {days} days in {', '.join(os.path.basename(f) for f in folders)}",
              "Medium", "Download one again if you need it", _cleanup_how(cfg, ("installers",)), "Download it again",
              by=CLEANUP)
    files = []
    for folder in folders:
        try:
            with os.scandir(folder) as entries:
                for e in entries:
                    if (os.path.splitext(e.name)[1].lower() in INSTALLER_TYPES and e.is_file()
                            and e.stat().st_mtime < cutoff):
                        files.append(e.path)
        except OSError:
            continue
    fix.action = {"delete": "files", "places": files}
    return measure(fix, ctx, files=files) if files else None


def node_fixes(scan, ctx, cfg):
    min_bytes = cfg.value("report", "cleanup_min_gb") * GB
    found = []
    home = norm(ctx.profile)
    for folder in sorted(_folders(scan, _NODE, ctx), key=len):          # outermost first
        n = norm(folder)
        below = n[len(home):] if n.startswith(home + "\\") else n      # the part an app folder would be in
        if scan.sizes[folder] < min_bytes or _APP_FOLDERS.search(below.replace("\\", "/") + "/"):
            continue
        if any(norm(folder).startswith(norm(f) + "\\") for f in found):
            continue
        found.append(folder)
    out = []
    for folder in sorted(found, key=lambda f: -scan.sizes[f])[:MAX_NODE_FOLDERS]:
        project = os.path.dirname(folder)
        fix = Fix(f"node:{folder}", f"Node.js packages of {os.path.basename(project)}", "Medium",
                  "npm install brings them back", f"Delete the folder {folder}", f"npm install in {project}")
        fix.action = {"delete": "files", "places": [folder]}
        out.append(measure(fix, ctx, contents=[folder], limit=scan.sizes[folder]))
    return out


def duplicate_fix(leftovers, ctx):
    """The extra copies in Downloads, Desktop and Sorted (pctidy/leftovers.py found them): one fix."""
    pairs = [p for p in (leftovers or {}).get("pairs", []) if not ctx.sandbox or ctx.in_sandbox(p[0])]
    if not pairs:
        return None
    fix = Fix("duplicates", f"Extra copies of files in Downloads, Desktop or Sorted ({len(pairs)})", "Medium",
              "An identical copy of each stays where it is",
              "python pc_tidy.py (it shows each copy that goes and the one that stays, then asks)",
              "None, but the identical copy stays", by="pc-tidy")
    fix.files = {_key(extra): file_bytes(extra) for extra, _keep, _efp, _kfp in pairs}
    fix.bytes = sum(fix.files.values())
    fix.action = {"delete": "duplicates", "pairs": pairs}
    return fix


def rebuildable_fixes(leftovers, ctx):
    """A venv or Rust's target folder in a project that hasn't changed for 30 days."""
    out = []
    for r in (leftovers or {}).get("rebuildable", []):
        name = os.path.basename(r["project"])
        if r["kind"] == "venv":
            title, why, undo = (f"Python virtual environment of {name}", "Can be made again from the project's "
                                "requirements", f"python -m venv {os.path.basename(r['path'])}, then pip install")
        else:
            title, why, undo = f"Rust build output of {name}", "cargo build makes it again", f"cargo build in {r['project']}"
        fix = Fix(f"rebuild:{r['path']}", title, "Medium", why, f"Delete the folder {r['path']}", undo)
        fix.action = {"delete": "files", "places": [r["path"]]}
        fix.where = r["path"]
        out.append(measure(fix, ctx, contents=[r["path"]]))
    return out


def move_fixes(move):
    """Moves to another volume, from move_off_c.py's candidates (disk_report.py passes them in)."""
    out = []
    if not move or not move.get("dest"):
        return out
    for c in move["candidates"]:
        fix = Fix(f"move:{c['path']}", f"Move {c['path']} to {move['dest']}", "Medium",
                  "Can be undone", f'python move_off_c.py --path "{c["path"]}"', "python move_off_c.py --undo",
                  by="move_off_c.py")
        fix.action = {"move": c["path"]}
        fix.bytes = c["size"]
        if c["kind"] == "file":
            fix.files = {_key(c["path"]): c["size"]}
        else:
            fix.folders = [(_key(c["path"]), c["size"])]
        out.append(fix)
    return out


def _root_file(ctx, name):
    """The size of a file at the root of the system drive, from the folder listing (hiberfil.sys
    can't be opened, but the listing has its size). None if it isn't there, or in the sandbox."""
    if ctx.sandbox:
        return None
    try:
        with os.scandir(ctx.system_drive) as entries:
            for e in entries:
                if e.name.lower() == name:
                    return e.stat(follow_symlinks=False).st_size
    except OSError:
        pass
    return None


def hibernation_fix(ctx, cfg):
    size = _root_file(ctx, "hiberfil.sys")
    if not size:
        return None
    fix = Fix("hibernation", "Turn off hibernation", "Medium",
              "No hibernate or Fast Startup until you turn it back on", _cleanup_how(cfg, ("hibernation",)),
              "powercfg /h on (as administrator)", by=CLEANUP, scope="machine")
    fix.action = {"tool": "hibernation_off"}
    fix.bytes = size
    return fix


def recycle_bin_fix(ctx, cfg):
    hook = ctx.test_value("PCTIDY_TEST_RECYCLE_BIN")       # "bytes,items": the tests' own Recycle Bin
    if hook:
        size, items = (int(x) for x in hook.split(","))
    elif ctx.sandbox:
        return None
    else:
        found = _win.recycle_bin(ctx.system_drive) if _win.ON_WINDOWS else None
        if not found:
            return None
        size, items = found
    fix = Fix("recycle-bin", f"Empty the Recycle Bin ({items:,} item{'s' if items != 1 else ''})", "High",
              "Can't be undone: check what's in it first", _cleanup_how(cfg, ("recycle_bin",)), "None", by=CLEANUP)
    fix.action = {"count_only": "empty_recycle_bin"}
    fix.bytes = size
    return fix


def windows_old_fix(scan, ctx):
    old = os.path.join(ctx.system_drive, "Windows.old")
    size = next((s for f, s in scan.sizes.items() if norm(f) == norm(old)), 0)
    if not size:
        return None
    fix = Fix("windows-old", "Previous Windows installation (Windows.old)", "High",
              "No going back to the old version of Windows",
              "Start > Disk Cleanup > C: > Clean up system files > tick 'Previous Windows installation(s)' > OK",
              "None", scope="machine")
    fix.bytes, fix.state = size, "about"       # partly unreadable without admin, and Windows decides
    return fix


# ---------- the list ----------
def _rank_key(fix):
    return RISKS.index(fix.risk), not fix.counted, -fix.bytes, fix.id


def _dedupe(fixes):
    """A file counts once, for the fix highest in the list."""
    claimed, folders = {}, []
    for fix in sorted(fixes, key=_rank_key):
        prefixes = tuple(f + "\\" for f in folders)
        if fix.files:
            keep = {p: b for p, b in fix.files.items() if p not in claimed and not p.startswith(prefixes)}
            if len(keep) != len(fix.files):
                fix.files = keep
                if fix.counted:
                    fix.bytes = sum(keep.values())
            claimed.update(keep)
        for folder, _size in fix.folders:
            if folder.startswith(prefixes):
                fix.bytes = 0
            else:
                fix.bytes -= sum(b for p, b in claimed.items() if p.startswith(folder + "\\"))
            folders.append(folder)


def on_system_drive(path, ctx):
    return norm(path).startswith(norm(ctx.system_drive))


def build(scan, cfg, ctx=None, move=None, progress=True, leftovers=None):
    """The ranked fixes for the system drive, as a dict for the report. `leftovers` (from
    pctidy/leftovers.py) adds the extra copies of files and rebuildable project folders."""
    ctx = ctx or context.current()
    drive = ctx.system_drive.rstrip("\\")
    out = {"drive": drive, "rows": [], "notes": [], "scanned": True, "target_line": ""}
    if not any(on_system_drive(r, ctx) for r in scan.roots):
        out["scanned"] = False
        out["notes"].append(f"{drive} wasn't scanned, so there are no fixes for it. Scan it with: "
                            f"python disk_report.py --path {drive}\\")
        return out
    if not any(norm(r) == norm(ctx.system_drive) for r in scan.roots):
        out["notes"].append(f"Only part of {drive} was scanned ({', '.join(scan.roots)}), so this list may miss "
                            "things.")

    line = Line() if progress else None

    def step(what):
        if line:
            line.show(f"measuring the fixes: {what} ...", force=True)

    fixes = []
    step("caches and temp files")
    fixes += table_fixes(ctx, cfg, out["notes"])
    step("conda")
    fixes += conda_fixes(scan, ctx, cfg)
    step("browser caches")
    fixes += browser_fixes(scan, ctx)
    step("installers")
    fixes.append(installers_fix(ctx, cfg))
    step("Node.js packages")
    fixes += node_fixes(scan, ctx, cfg)
    fixes += move_fixes(move)
    fixes += [duplicate_fix(leftovers, ctx)] + rebuildable_fixes(leftovers, ctx)
    fixes += [hibernation_fix(ctx, cfg), recycle_bin_fix(ctx, cfg), windows_old_fix(scan, ctx)]
    if line:
        line.clear()

    fixes = [f for f in fixes if f is not None]
    _dedupe(fixes)
    fixes = [f for f in fixes if not f.counted or f.bytes >= MIN_ROW_BYTES]
    fixes.sort(key=_rank_key)
    titles = [f.title for f in fixes]
    for fix in fixes:                # two conda installs both have a package cache, say
        if titles.count(fix.title) > 1 and fix.where:
            fix.title += f" in {os.path.dirname(fix.where) if fix.id == 'conda-pkgs' else fix.where}"

    running = 0
    for fix in fixes:
        if fix.counted:
            running += fix.bytes
            fix.total = running
    out["rows"] = [f.as_dict() for f in fixes]

    try:
        usage = shutil.disk_usage(ctx.system_drive)
        out["free"], out["size"] = usage.free, usage.total
    except OSError:
        out["free"] = out["size"] = None
    out["target_line"] = target_line(out, cfg.value("report", "target_free_percent"))

    if move is not None and not move.get("dest"):
        out["notes"].append("There is no other drive to move files to, so no moves are listed.")
    if any(f.state == "admin" for f in fixes):
        out["notes"].append("Rows that say 'needs admin to measure': run the report in PowerShell opened as "
                            "administrator to measure them.")
    if any(f.by == CLEANUP for f in fixes):
        out["notes"].append(f"Rows done by {CLEANUP}: run .\\cleanup.ps1 -DryRun, then .\\cleanup.ps1, in "
                            "PowerShell opened as administrator. It does every [cleanup] switch that is yes in "
                            "settings.ini, so switch off the ones you don't want first: python pc_tidy.py "
                            "settings edit")
        if cfg.errors("cleanup"):
            out["notes"].append(f"{CLEANUP} won't run until the mistake in [cleanup] is fixed: "
                                "python pc_tidy.py settings check")
    return out


def target_line(out, target):
    """'Fixes 1-5 bring C: to about 16% free.', or why that isn't possible."""
    free, size, drive = out.get("free"), out.get("size"), out["drive"]
    if not size:
        return ""
    now = free / size * 100
    if now >= target:
        return f"{drive} has {now:.0f}% free, more than the {target}% to aim for."
    for i, row in enumerate(out["rows"], 1):
        if row["total"] is not None and (free + row["total"]) / size * 100 >= target:
            low = [r for r in out["rows"][:i] if r["risk"] == "Low"]
            which = "Fix 1 brings" if i == 1 else f"Fixes 1-{i} bring"
            line = f"{which} {drive} to about {(free + row['total']) / size * 100:.0f}% free"
            if len(low) < i:
                low_total = max((r["total"] for r in low if r["total"] is not None), default=0)
                line += f" (the low-risk ones alone: about {(free + low_total) / size * 100:.0f}%)"
            return line + f". {target}% free is a comfortable amount."
    reached = max((r["total"] for r in out["rows"] if r["total"] is not None), default=0)
    return (f"All the fixes together bring {drive} to about {(free + reached) / size * 100:.0f}% free, short of the "
            f"{target}% to aim for. The biggest folders and files below show what else fills it.")
