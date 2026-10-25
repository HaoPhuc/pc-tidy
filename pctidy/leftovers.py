r"""What's left in your folders once Downloads is sorted: the space rules alone can't name.

It looks in Desktop, Documents, Downloads, Music, Pictures, Videos, the Sorted folder ([tidy] dest)
and [leftovers] extra_folders: never in AppData, dot-folders or system folders.

- Duplicates: files of at least duplicate_min_mb with the same content (the same size, then the same
  first and last 64 KB, then the same BLAKE2b hash of the whole file). Hard links to one file, empty
  files and cloud-only OneDrive files (reading one would download it) are not duplicates. One copy of
  each group is kept: one outside Downloads, Desktop and Sorted if there is one, else the oldest. Only
  loose extra copies are offered for deleting: right in Downloads or Desktop, or in one of the
  folders tidy_folder.py sorts into, where copies end up by accident. A copy deeper in a folder (an
  unpacked app, say) or inside a project is listed for you to decide, never offered: it may be needed.
- Big files not opened for months: at least stale_min_mb, last opened stale_after_days ago or more.
  If Windows doesn't record when files are opened, last changed is used, and the report says so.
  They are offered for moving off C: (undoable), never for deleting: old doesn't mean unneeded.
- Empty folders: nothing in them at any depth, not hidden, not inside a project, not changed in the
  last 7 days. Listed only: removing them frees nothing.
- Projects: a folder with .git, or a manifest with matching source in it or below (package.json
  with .js/.ts, a lock file or node_modules; pyproject.toml, setup.py or requirements.txt with .py;
  Cargo.toml with src; CMakeLists.txt with C or C++; a .sln with a .csproj or .vcxproj; an .Rproj
  with .R), strictly below one of those folders, and never a folder tidy_folder.py makes itself. The
  outermost one wins. A venv, Rust's target folder or node_modules (next to its package.json) in a
  project that hasn't changed for 30 days is offered for deleting: it can be made again.

Every time is read from the folder listing before any file is opened, and every file is read
through _win.open_untouched, so checking duplicates never changes when a file was last opened.
"""

import hashlib
import os
import time
from collections import defaultdict

from . import _win, context
from .progress import Line
from .paths import inside, lp, norm

MB, GB = 1024 ** 2, 1024 ** 3
EDGE = 64 * 1024                 # the first and last this many bytes are compared before whole files
CHUNK = 1024 * 1024
EMPTY_MIN_AGE_DAYS = 7
PROJECT_QUIET_DAYS = 30
REBUILDABLE = {"node_modules", "venv", ".venv", "target", "__pycache__"}   # their files are never leftovers
_HIDDEN_OR_SYSTEM = 0x2 | 0x4
_CLOUD_ONLY = 0x1000 | 0x40000 | 0x400000
_LINK_TAGS = (0xA0000003, 0xA000000C)
# bits for "matching source": what is in a folder or below it
_JS, _PY, _C, _R, _VSPROJ = 1, 2, 4, 8, 16
_EXT_BITS = {".js": _JS, ".mjs": _JS, ".cjs": _JS, ".ts": _JS, ".tsx": _JS, ".jsx": _JS, ".py": _PY,
             ".c": _C, ".cc": _C, ".cpp": _C, ".h": _C, ".hpp": _C, ".r": _R, ".csproj": _VSPROJ,
             ".vcxproj": _VSPROJ}
_LOCKS = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml"}


class _File:
    __slots__ = ("path", "size", "mtime", "atime", "attrs")

    def __init__(self, path, st):
        self.path, self.size, self.mtime, self.atime = path, st.st_size, st.st_mtime, st.st_atime
        self.attrs = getattr(st, "st_file_attributes", 0)


class _Dir:
    __slots__ = ("path", "parent", "files", "dirs", "mask", "count", "bytes", "newest", "newest_own",
                 "attrs", "mtime", "rebuild", "unreadable", "project")

    def __init__(self, path, parent, st, rebuild):
        self.path, self.parent, self.rebuild = path, parent, rebuild
        self.files, self.dirs = set(), set()        # lower-case names directly inside
        self.mask = self.count = self.bytes = 0     # below it, at any depth
        self.newest = self.newest_own = 0.0         # newest file; and not counting rebuildable folders
        self.attrs = getattr(st, "st_file_attributes", 0) if st else 0
        self.mtime = st.st_mtime if st else 0.0
        self.unreadable = self.project = False


def scope_roots(ctx, cfg):
    """The folders to look in, those that exist, none inside another."""
    found = [os.path.join(ctx.profile, n) for n in context.KNOWN_FOLDER_NAMES]
    found.append(cfg.value("tidy", "dest"))
    found += cfg.value("leftovers", "extra_folders")
    out = []
    for path in found:
        if os.path.isdir(path) and not any(inside(path, o) or inside(o, path) for o in out):
            out.append(os.path.abspath(path))
    return out


def _walk(root, files, dirs):
    """Every folder below root into dirs (parents first) and every file outside rebuildable folders
    into files. Links are never followed and dot-folders (.git, .venv) are not walked into."""
    try:
        top = os.stat(lp(root))
    except OSError:
        return
    stack = [(root, None, top, False)]
    while stack:
        path, parent, st, rebuild = stack.pop()
        d = _Dir(path, parent, st, rebuild)
        dirs.append(d)
        try:
            entries = list(os.scandir(lp(path)))
        except OSError:
            d.unreadable = True
            continue
        for e in entries:
            try:
                est = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if e.is_symlink() or getattr(est, "st_reparse_tag", 0) in _LINK_TAGS:
                continue
            name = e.name.lower()
            full = os.path.join(path, e.name)
            if e.is_dir(follow_symlinks=False):
                d.dirs.add(name)
                if not name.startswith("."):
                    stack.append((full, d, est, rebuild or name in REBUILDABLE))
                continue
            d.files.add(name)
            d.mask |= _EXT_BITS.get(os.path.splitext(name)[1], 0)
            d.count += 1
            d.bytes += 0 if est.st_file_attributes & _CLOUD_ONLY else est.st_size
            d.newest = max(d.newest, est.st_mtime)
            if not rebuild:
                d.newest_own = max(d.newest_own, est.st_mtime)
                files.append(_File(full, est))
    for d in reversed(dirs):           # children come after their parents
        p = d.parent
        if p is not None:
            p.mask |= d.mask
            p.count += d.count
            p.bytes += d.bytes
            p.newest = max(p.newest, d.newest)
            p.newest_own = max(p.newest_own, d.newest_own)
            p.unreadable = p.unreadable or d.unreadable


def _is_project(d):
    f, sub = d.files, d.dirs
    if ".git" in sub:
        return True
    if "package.json" in f and (d.mask & _JS or "node_modules" in sub or f & _LOCKS):
        return True
    if f & {"pyproject.toml", "setup.py", "requirements.txt"} and d.mask & _PY:
        return True
    if "cargo.toml" in f and "src" in sub:
        return True
    if "cmakelists.txt" in f and d.mask & _C:
        return True
    if any(n.endswith(".sln") for n in f) and d.mask & _VSPROJ:
        return True
    return any(n.endswith(".rproj") for n in f) and bool(d.mask & _R)


def tidy_folder_names(cfg):
    """The folders tidy_folder.py makes: never a project themselves (a project can be inside one)."""
    return ({v.lower() for v in cfg.table("tidy.extensions").values()}
            | {c.lower() for c in cfg.value("tidy", "categories")} | {"archive", "_logs"})


def _ours(d, roots, made):
    """True for <root>\\<category> and <root>\\Archive\\<category>, which tidy_folder.py makes."""
    p = d.parent
    if p is None or os.path.basename(d.path).lower() not in made:
        return False
    if norm(p.path) in roots:
        return True
    return os.path.basename(p.path).lower() == "archive" and p.parent is not None and norm(p.parent.path) in roots


# ---------- reading without touching ----------
def _read(path, budget, whole=False):
    """BLAKE2b of the first and last EDGE bytes (or of the whole file). None if it can't be read
    without changing its last-access time, or if the budget ran out."""
    try:
        f = _win.open_untouched(path)
    except OSError:
        return None
    h = hashlib.blake2b(digest_size=20)
    with f:
        if whole:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                budget[0] -= len(chunk)
                h.update(chunk)
        else:
            h.update(f.read(EDGE))
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size > EDGE:
                f.seek(max(EDGE, size - EDGE))
                h.update(f.read(EDGE))
            budget[0] -= min(size, 2 * EDGE)
    return h.hexdigest()


def _same_content(group, budget):
    """The group split by content: lists of 2 or more files with identical bytes. None if the
    budget ran out. The first and last 64 KB are compared first, so most files are never read whole."""
    edges = defaultdict(list)
    for f in group:
        if budget[0] <= 0:
            return None
        digest = _read(f.path, budget)
        if digest is not None:
            edges[digest].append(f)
    out = []
    for g in edges.values():
        if len(g) < 2:
            continue
        whole = defaultdict(list)
        for f in g:
            if budget[0] <= 0:
                return None
            digest = _read(f.path, budget, whole=True)
            if digest is not None:
                whole[digest].append(f)
        out += [w for w in whole.values() if len(w) > 1]
    return out


def _distinct(group):
    """Leave one of each set of hard links to the same file: they are one file, not copies."""
    seen, out = set(), []
    for f in group:
        try:
            st = os.stat(lp(f.path))
        except OSError:
            continue
        key = (st.st_dev, st.st_ino)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _fp(path):
    """(size, mtime_ns): enough to tell that a copy changed between the scan and the dry run."""
    try:
        st = os.stat(lp(path))
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns]


# ---------- the analysis ----------
def analyse(ctx, cfg, progress=True):
    """Everything 'What's left' shows, as a dict for the report."""
    o = cfg.section("leftovers")
    roots = scope_roots(ctx, cfg)
    sorted_dir = cfg.value("tidy", "dest")
    accidental = [os.path.join(ctx.profile, "Downloads"), os.path.join(ctx.profile, "Desktop"), sorted_dir]

    line = Line() if progress else None

    def say(what, force=True):
        if line:
            line.show(f"{what} ...", force=force)

    files, dirs = [], []
    for root in roots:
        say(f"looking through {root}")
        _walk(root, files, dirs)
    root_keys = {norm(r) for r in roots}
    made = tidy_folder_names(cfg)

    # projects: outermost first (parents come first in dirs)
    projects = []
    for d in dirs:
        p = d.parent
        while p is not None and not p.project:
            p = p.parent
        if p is not None:
            continue                     # inside a project already
        if d.parent is not None and not _ours(d, root_keys, made) and _is_project(d):
            d.project = True
            projects.append(d)
    project_keys = [norm(d.path) for d in projects]

    def in_project(path):
        n = norm(path)
        return any(n == k or n.startswith(k + "\\") for k in project_keys)

    # empty folders, outermost only
    now = time.time()
    empty, in_projects, reported = [], 0, set()
    for d in dirs:
        if d.parent is None or d.count or d.unreadable or d.rebuild or d.attrs & _HIDDEN_OR_SYSTEM:
            continue
        if d.mtime > now - EMPTY_MIN_AGE_DAYS * 86400:
            continue
        if in_project(d.path):
            in_projects += 1
            continue
        if d.parent is not None and norm(d.parent.path) in reported:
            reported.add(norm(d.path))
            continue
        reported.add(norm(d.path))
        empty.append(d.path)

    # big files not opened for months
    hook = ctx.test_value("PCTIDY_TEST_LAST_ACCESS")       # "on" or "off": the tests' own setting
    recorded = (hook == "on") if hook else _win.last_access_recorded()
    cutoff = now - o.stale_after_days * 86400
    stale = sorted(({"path": f.path, "size": f.size, "time": f.atime if recorded else f.mtime}
                    for f in files if f.size >= o.stale_min_mb * MB and not f.attrs & _CLOUD_ONLY
                    and (f.atime if recorded else f.mtime) < cutoff), key=lambda s: -s["size"])

    # duplicates
    by_size = defaultdict(list)
    for f in files:
        if f.size and f.size >= o.duplicate_min_mb * MB and not f.attrs & _CLOUD_ONLY:
            by_size[f.size].append(f)
    budget, unchecked, groups = [o.hash_budget_gb * GB], 0, []
    sizes = sorted((s for s, g in by_size.items() if len(g) > 1), reverse=True)     # biggest first
    for i, size in enumerate(sizes):
        say(f"checking duplicates: {i + 1} of {len(sizes)} sizes", force=False)
        group = _distinct(by_size[size])
        if len(group) < 2:
            continue
        same = _same_content(group, budget)
        if same is None:
            unchecked = len(sizes) - i
            break
        groups += same
    if line:
        line.clear()

    # A loose file: right in Downloads or Desktop, or in a folder of Sorted that tidy_folder.py files
    # things into (Sorted\Pictures, Sorted\Archive\Pictures). A copy deeper down is part of something,
    # an unpacked app, say, that may need it, so it is listed but never offered.
    loose_in = {norm(os.path.join(ctx.profile, "Downloads")), norm(os.path.join(ctx.profile, "Desktop"))}
    sorted_key, archive_key = norm(sorted_dir), norm(os.path.join(sorted_dir, "Archive"))

    def loose(path):
        folder = norm(os.path.dirname(path))
        return folder in loose_in or norm(os.path.dirname(folder)) in (sorted_key, archive_key)

    dup_rows, pairs, offered_bytes = [], [], 0
    for g in groups:
        outside = sorted((f for f in g if not any(inside(f.path, a) for a in accidental)), key=lambda f: f.mtime)
        keep = outside[0] if outside else min(g, key=lambda f: f.mtime)
        extras = []
        for f in sorted(g, key=lambda f: f.path.lower()):
            if f is keep:
                continue
            offer = loose(f.path) and not in_project(f.path)
            extras.append({"path": f.path, "offered": offer, "in_project": in_project(f.path)})
            if offer:
                pairs.append([f.path, keep.path, _fp(f.path), _fp(keep.path)])
                offered_bytes += f.size
        dup_rows.append({"size": g[0].size, "keep": keep.path, "extras": extras})
    dup_rows.sort(key=lambda r: -r["size"] * len(r["extras"]))

    # rebuildable folders of quiet projects
    rebuildable = []
    for d in projects:
        if d.newest_own > now - PROJECT_QUIET_DAYS * 86400:
            continue
        for name, kind in (("venv", "venv"), (".venv", "venv"), ("target", "target"), ("node_modules", "node")):
            folder = os.path.join(d.path, name)
            if name not in d.dirs:
                continue
            if kind == "venv" and not os.path.isfile(os.path.join(folder, "pyvenv.cfg")):
                continue
            if kind == "target" and "cargo.toml" not in d.files:
                continue
            if kind == "node" and "package.json" not in d.files:
                continue
            rebuildable.append({"path": folder, "kind": kind, "project": d.path})

    return {
        "roots": roots,
        "recorded": recorded,
        "stale_after_days": o.stale_after_days,
        "stale_min_mb": o.stale_min_mb,
        "duplicates": dup_rows,
        "pairs": pairs,
        "offered_bytes": offered_bytes,
        "unchecked": unchecked,
        "stale": stale,
        "empty": empty,
        "empty_in_projects": in_projects,
        "projects": sorted(({"path": d.path, "bytes": d.bytes, "files": d.count, "newest": d.newest}
                            for d in projects), key=lambda p: -p["bytes"]),
        "rebuildable": rebuildable,
    }


def _when(t):
    return time.strftime("%Y-%m-%d", time.localtime(t)) if t else "never"


def render(lo, gb, limit=10):
    """'What's left' as lines of text, for the report and the guided flow."""
    names = ", ".join(os.path.basename(r) for r in lo["roots"]) or "none of them exist"
    out = [f"\nWHAT'S LEFT IN YOUR FOLDERS ({names})"]

    extras = sum(len(r["extras"]) for r in lo["duplicates"])
    offered = len(lo["pairs"])
    if lo["duplicates"]:
        wasted = sum(r["size"] * len(r["extras"]) for r in lo["duplicates"])
        out.append(f"  Duplicates: {extras} extra cop{'y' if extras == 1 else 'ies'}, {gb(wasted)}. "
                   f"{offered} of them, loose in Downloads, Desktop or Sorted, are in the fixes ({gb(lo['offered_bytes'])}); "
                   "decide about the rest yourself.")
        for r in lo["duplicates"][:limit]:
            out.append(f"    {gb(r['size']):>9}  keep   {r['keep']}")
            for x in r["extras"]:
                tag = "extra" if x["offered"] else ("in a project, not offered" if x["in_project"] else "yours to decide")
                out.append(f"    {'':>9}  {tag:<5}  {x['path']}")
        if len(lo["duplicates"]) > limit:
            out.append(f"    ... and {len(lo['duplicates']) - limit} more groups")
    else:
        out.append("  Duplicates: none found.")
    if lo["unchecked"]:
        out.append(f"  {lo['unchecked']} groups of same-size files weren't checked: reading stopped at "
                   "hash_budget_gb in [leftovers].")

    what = "opened" if lo["recorded"] else "changed"
    note = "" if lo["recorded"] else " (Windows isn't recording when files are opened on this PC)"
    out.append(f"  Big files not {what} in {lo['stale_after_days']} days, {lo['stale_min_mb']:g} MB or more{note}:")
    for s in lo["stale"][:limit]:
        out.append(f"    {gb(s['size']):>9}  {s['path']}   last {what} {_when(s['time'])}")
    if not lo["stale"]:
        out.append("    none")
    elif len(lo["stale"]) > limit:
        out.append(f"    ... and {len(lo['stale']) - limit} more")

    out.append(f"  Empty folders (nothing in them at any depth): {len(lo['empty'])}"
               + (f", and {lo['empty_in_projects']} inside projects, left alone" if lo["empty_in_projects"] else ""))
    out += [f"    {p}" for p in lo["empty"][:limit]]
    if len(lo["empty"]) > limit:
        out.append(f"    ... and {len(lo['empty']) - limit} more")

    out.append(f"  Projects: {len(lo['projects'])}")
    for p in lo["projects"][:limit]:
        out.append(f"    {gb(p['bytes']):>9}  {p['path']}   {p['files']:,} files, last changed {_when(p['newest'])}")
    if len(lo["projects"]) > limit:
        out.append(f"    ... and {len(lo['projects']) - limit} more")
    return out
