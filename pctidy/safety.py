"""safety.check(path, operation): the one gate that every delete and every move goes through.

It refuses:
1. drive roots, the Users folder and every folder directly in it (the profiles, Public included),
   and the Known Folders themselves (Desktop, Documents, Downloads, Music, Pictures, Videos, OneDrive*)
2. Windows, both Program Files folders, ProgramData, AppData, pc-tidy's own folders, and anything
   inside them, except exactly the rows of ALLOWED below; and pc-tidy.exe itself, wherever it was
   started from, with every folder it is in
3. a wildcard directly under a drive root, the Users folder, a profile or a Known Folder (D:\\*)
4. following a junction or symlink while deleting a folder (the executor removes the link itself)
5. anything outside PCTIDY_SANDBOX_ROOT when the tests set it
6. a drive root, the Users folder, a profile or anything from item 2 as the folder to sort

Operations: "delete", "move" (the item moved), "move-to" (where it goes), "rmdir" (an empty folder
pc-tidy made), "sort" (tidy_folder.py --source), "self" (removing pc-tidy itself: only its data
folder and pc-tidy.exe are allowed, nothing else at all; see pctidy/remove.py).
"""

import os
from collections import namedtuple

from . import context
from .paths import has_wildcard, inside, is_root, match, norm, real, real_parent

OPERATIONS = ("delete", "move", "move-to", "rmdir", "sort", "self")

Row = namedtuple("Row", "fix title scope kind patterns")

# The only things inside a protected folder that may ever be deleted: one row per built-in fix, line
# by line what today's cleanup.ps1 removes there. A "contents" row allows anything below its folders
# (never the folder itself); a "files" row allows exactly the files its patterns match.
ALLOWED = (
    Row("temp-user", "Your Temp files", "user", "contents", ("{temp}",)),
    Row("temp-windows", "Windows Temp files", "machine", "contents", ("{system_root}\\Temp",)),
    Row("caches", "pip and uv caches", "user", "contents", ("{local}\\pip\\cache", "{local}\\uv\\cache")),
    Row("shader-caches", "GPU shader caches", "user", "contents",
        ("{local}\\NVIDIA\\DXCache", "{local}\\NVIDIA\\GLCache", "{local}\\AMD\\DxCache",
         "{local}\\AMD\\GLCache", "{local}\\D3DSCache")),
    Row("crash-dumps-user", "Your crash dumps", "user", "files", ("{local}\\CrashDumps\\*.dmp",)),
    Row("crash-dumps-windows", "Windows crash dumps", "machine", "files",
        ("{system_root}\\MEMORY.DMP", "{system_root}\\Minidump\\*.dmp",
         "{system_root}\\LiveKernelReports\\*.dmp", "{system_root}\\LiveKernelReports\\*\\*.dmp")),
)

# files and folders at the root of any drive that Windows itself owns
_ROOT_SYSTEM_NAMES = ("$recycle.bin", "system volume information", "pagefile.sys", "hiberfil.sys",
                      "swapfile.sys", "recovery", "boot", "bootmgr", "$windows.~bt", "$windows.~ws")


class Refused(Exception):
    """The safety check refused a path. str(e) is a sentence for the user."""


def check(path, operation, ctx=None):
    """Raise Refused if pc-tidy may not do `operation` to `path`."""
    problem = why_refused(path, operation, ctx)
    if problem:
        raise Refused(problem)


def why_refused(path, operation, ctx=None):
    """The reason pc-tidy may not do `operation` to `path`, or None if it may."""
    if operation not in OPERATIONS:
        raise ValueError(f"unknown operation {operation!r}")
    ctx = ctx or context.current()
    text = os.fspath(path).strip()
    if not text:
        return "the path is empty"
    rules = rules_for(ctx)
    if operation == "self":
        return rules.self_problem(text)
    if has_wildcard(text):
        if operation != "delete":
            return "wildcards are only allowed in paths to delete"
        return rules.pattern_problem(text)
    resolved = real(text) if operation == "sort" else real_parent(text)
    for candidate in dict.fromkeys([norm(text), norm(resolved)]):
        problem = rules.path_problem(candidate, operation, text)
        if problem:
            return problem
    return None


def allowed_row(path, ctx=None):
    """The ALLOWED row that lets pc-tidy delete this path inside a protected folder, or None."""
    ctx = ctx or context.current()
    rules = rules_for(ctx)
    for candidate in dict.fromkeys([norm(path), norm(real_parent(path))]):
        row = rules.row_for(candidate)
        if row:
            return row
    return None


def temp_problem(ctx=None):
    """None if %TEMP% is a real temp folder, else the message the Temp fix shows (problem 14)."""
    ctx = ctx or context.current()
    return rules_for(ctx).temp_problem()


def rules_for(ctx):
    rules = getattr(ctx, "_safety_rules", None)
    if rules is None:
        rules = ctx._safety_rules = _Rules(ctx)
    return rules


class _Rules:
    """The protected places of one context, normalised once (both as written and with links and
    8.3 short names resolved, since %TEMP% is often C:\\Users\\RUNNER~1\\...)."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.show = {}
        self.sandbox = set()
        for root in ctx.sandbox:
            self.sandbox |= self._forms(root)
        self.users_dir = self._keep(self._forms(ctx.users_dir))
        profiles = [ctx.profile]
        try:
            profiles += [e.path for e in os.scandir(ctx.users_dir) if e.is_dir()]
        except OSError:
            pass
        self.profiles = set()
        for p in profiles:
            self.profiles |= self._keep(self._forms(p))
        self.known = set()
        for p in ctx.known_folders():
            self.known |= self._keep(self._forms(p))
        closed = [(ctx.system_root, "Windows"), (ctx.program_data, "ProgramData"),
                  (ctx.local_appdata, "AppData"), (ctx.roaming_appdata, "AppData"),
                  (ctx.app_dir, "pc-tidy's own folder"), (ctx.data_dir, "pc-tidy's data folder")]
        closed += [(pf, "Program Files") for pf in ctx.program_files]
        closed += [(os.path.join(p, "AppData"), "AppData") for p in profiles]
        self.closed = {}
        for path, label in closed:
            for form in self._keep(self._forms(path)):
                self.closed.setdefault(form, label)
        self.exe = self._keep(self._forms(ctx.exe)) if ctx.exe else set()
        self.top = self.users_dir | self.profiles | self.known
        self.protected = self.top | set(self.closed) | self.exe

    def _forms(self, path):
        out = {norm(path), norm(real(path))}
        for form in out:
            self.show.setdefault(form, os.path.abspath(path))
        return out

    def _keep(self, paths):
        """In the sandbox, a protected folder that contains the sandbox is the real one around the
        test's own fake folders, so it doesn't apply inside it (rule 5 covers everything outside)."""
        if not self.sandbox:
            return set(paths)
        return {p for p in paths if not any(inside(s, p) for s in self.sandbox)}

    def _name(self, p, given=None):
        return given or self.show.get(p) or p

    # ---------- concrete paths ----------
    def path_problem(self, p, op, given=None):
        shown = self._name(p, given)
        if self.sandbox and not any(inside(p, s) for s in self.sandbox):
            return f"{shown} is outside the test folders (PCTIDY_SANDBOX_ROOT)"
        if is_root(p):
            return f"{shown} is a whole drive"
        if is_root(os.path.dirname(p)) and os.path.basename(p) in _ROOT_SYSTEM_NAMES:
            return f"{shown} belongs to Windows"
        if p in self.users_dir:
            return f"{shown} holds every user's profile"
        if p in self.profiles:
            return f"{shown} is a user profile folder"
        if p in self.known and op != "sort":
            return (f"{shown} is one of your Windows user folders. pc-tidy works on what's inside it, "
                    "never on the folder itself")
        if p in self.exe:
            return f"{shown} is pc-tidy itself"
        if op != "move-to":
            for q in self.protected:
                # sorting a folder pc-tidy.exe is in is fine: moving the exe itself is refused, file by file
                if inside(q, p, strict=True) and not (op == "sort" and (q in self.known or q in self.exe)):
                    return f"{shown} contains {self._name(q)}"
        for q, label in self.closed.items():
            if inside(p, q):
                if op == "delete" and p != q and self.row_for(p):
                    return None
                return f"{shown} is inside {label}, which pc-tidy doesn't change"
        return None

    def self_problem(self, text):
        """Removing pc-tidy itself: the data folder (and what is in it) and pc-tidy.exe, nothing else.
        Links are resolved, so a link inside the data folder can't lead out of it."""
        if has_wildcard(text):
            return "wildcards aren't allowed here"
        p, resolved = norm(text), norm(real(text))
        shown = self._name(p, text)
        if self.sandbox and not any(inside(p, s) for s in self.sandbox):
            return f"{shown} is outside the test folders (PCTIDY_SANDBOX_ROOT)"
        data = self._forms(self.ctx.data_dir)
        if all(any(inside(form, d) for d in data) or form in self.exe for form in (p, resolved)):
            return None
        return f"{shown} isn't pc-tidy's own: only its data folder and pc-tidy.exe are removed"

    def row_for(self, p):
        """The ALLOWED row that matches a concrete, normalised path."""
        for row in ALLOWED:
            if row.fix == "temp-user" and self.temp_problem():
                continue
            for pattern in self._expand(row):
                if row.kind == "contents":
                    if inside(p, pattern, strict=True):
                        return row
                elif match(p, pattern):
                    return row
        return None

    def _expand(self, row):
        ctx, out = self.ctx, []
        for pattern in row.patterns:
            for temp in {ctx.temp, real(ctx.temp)}:
                out.append(norm(pattern.format(temp=temp, system_root=ctx.system_root, local=ctx.local_appdata)))
        return list(dict.fromkeys(out))

    # ---------- wildcards ----------
    def pattern_problem(self, pattern):
        text = os.path.abspath(pattern.replace("/", "\\"))
        drive, rest = os.path.splitdrive(text)
        parts = [x for x in rest.split("\\") if x]
        first = next(i for i, x in enumerate(parts) if has_wildcard(x))
        written = norm(os.path.join(drive + "\\", *parts[:first]))
        parent = norm(real(written))
        if self.sandbox and not any(inside(parent, s) for s in self.sandbox):
            return f"{pattern} is outside the test folders (PCTIDY_SANDBOX_ROOT)"
        # both as written and resolved: X:\* is a drive root to whoever wrote it, even when X: is a
        # subst letter that resolves to a folder
        forms = (written, parent)
        what = None
        if any(is_root(f) for f in forms):
            what = "a drive root could empty the drive"
        elif any(f in self.users_dir for f in forms):
            what = "the Users folder could delete every profile"
        elif any(f in self.profiles for f in forms):
            what = "a user profile could delete its Documents, Desktop and more"
        elif any(f in self.known for f in forms):
            what = f"{os.path.basename(self._name(parent))} could empty it"
        if what:
            return (f"'{pattern}' is refused: a wildcard directly under {what}. List the folders you mean, "
                    "e.g. D:\\old-backups.")
        whole = norm(text)
        for row in ALLOWED:
            if row.fix == "temp-user" and self.temp_problem():
                continue
            for allowed in self._expand(row):
                if row.kind == "contents" and inside(parent, allowed):
                    return None
                if row.kind == "files" and whole == allowed:
                    return None
        for q, label in self.closed.items():
            if inside(parent, q):
                return f"'{pattern}' is inside {label}, which pc-tidy doesn't change"
        for q in self.protected:
            if inside(q, parent, strict=True):
                return f"'{pattern}' could match {self._name(q)}"
        return None

    # ---------- %TEMP% ----------
    def temp_problem(self):
        if not hasattr(self, "_temp"):
            t = norm(real(self.ctx.temp))
            name, parent_name = os.path.basename(t), os.path.basename(os.path.dirname(t))
            named_temp = name in ("temp", "tmp") or (name.isdigit() and parent_name in ("temp", "tmp"))
            # pc-tidy itself may run from a real Temp folder (a ZIP opened in Explorer): the Temp fix
            # then leaves out only the item that holds it, which the "contains" check refuses
            own = self._forms(self.ctx.app_dir) | self.exe
            bad = (not named_temp or is_root(t) or t in self.top or t in self.closed
                   or any(inside(q, t, strict=True) for q in self.protected - own))
            self._temp = (f"Your TEMP variable points at {self.ctx.temp}, which isn't a temp folder. "
                          "The Temp fix was skipped.") if bad else None
        return self._temp
