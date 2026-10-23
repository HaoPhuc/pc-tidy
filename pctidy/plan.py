"""A plan: the exact list of actions a dry run showed, each with a fingerprint of its item.

The plan is an object held by the process that built it, and the executor takes it from memory.
A copy is saved to plans\\ in the data folder so the full list can be read, but that copy is never
executed: editing it changes nothing, and failing to save it (C: full) stops nothing.

Actions are plain dicts, so they can go into the journal as they are:
    files    {op: "delete", path, fp: {size, mtime_ns, file_id}}
             an extra copy of a duplicate also has keep and keep_fp: the copy that must stay
    folders  {op: "delete_tree", path, fp: {files, bytes, newest_mtime_ns}}
    moves    {op: "move", src, dst, src_volume, dst_volume, same_volume, fp}
    folders  {op: "rmdir", path}             an empty folder (pc-tidy's own, after an undo)
    tools    {op: "run_tool", tool, interactive, measured}
    bin      {op: "empty_recycle_bin", measured: {items, bytes}}
"""

import hashlib
import json
import os
import platform
import stat
import time

from . import _win, context, volume
from .paths import lp

PLAN_VALID_HOURS = 24
KEEP_PLAN_COPIES = 30

_NAME_SURROGATE = 0x20000000      # reparse tags of junctions, symlinks and mount points


def is_link(st):
    """True for a junction, symlink or mount point (never followed), not for e.g. OneDrive files."""
    return bool(getattr(st, "st_file_attributes", 0) & _win.FILE_ATTRIBUTE_REPARSE_POINT
                and getattr(st, "st_reparse_tag", 0) & _NAME_SURROGATE)


def is_dir_stat(st):
    return stat.S_ISDIR(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x10)


def fingerprint(path):
    """What an item looks like now, or None if it isn't there.

    os.stat, not DirEntry.stat(): on Windows the latter reports st_ino = 0."""
    try:
        st = os.lstat(lp(path))
    except OSError:
        return None
    if is_link(st):
        return {"kind": "link", "file_id": st.st_ino, "dev": st.st_dev, "dir": is_dir_stat(st)}
    if is_dir_stat(st):
        fp = tree_stats(path)
        fp.update(kind="dir", file_id=st.st_ino, dev=st.st_dev)
        return fp
    return {"kind": "file", "size": st.st_size, "mtime_ns": st.st_mtime_ns, "file_id": st.st_ino,
            "dev": st.st_dev, "readonly": bool(getattr(st, "st_file_attributes", 0) & 1)}


def tree_stats(path):
    """{files, dirs, bytes, newest_mtime_ns, links, biggest} of a folder, without following links."""
    out = {"files": 0, "dirs": 0, "bytes": 0, "newest_mtime_ns": 0, "links": 0, "biggest": 0}
    stack = [lp(path)]
    while stack:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            out["unreadable"] = out.get("unreadable", 0) + 1
            continue
        for e in entries:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                out["unreadable"] = out.get("unreadable", 0) + 1
                continue
            if is_link(st):
                out["links"] += 1
            elif is_dir_stat(st):
                out["dirs"] += 1
                stack.append(e.path)
            else:
                out["files"] += 1
                out["bytes"] += st.st_size
                out["biggest"] = max(out["biggest"], st.st_size)
                out["newest_mtime_ns"] = max(out["newest_mtime_ns"], st.st_mtime_ns)
    return out


_CONTENT_KEYS = ("kind", "size", "mtime_ns", "files", "dirs", "bytes", "newest_mtime_ns", "links")


def same_content(a, b):
    """Same kind, size and times (and for folders the same number of files and bytes)."""
    return bool(a and b) and all(a.get(k) == b.get(k) for k in _CONTENT_KEYS)


def same_item(a, b):
    """The same file or folder (same file ID on the same volume), whatever its content now."""
    return bool(a and b) and a.get("kind") == b.get("kind") and a.get("file_id") == b.get("file_id") \
        and a.get("dev") == b.get("dev")


def unchanged(a, b):
    return same_item(a, b) and same_content(a, b)


def size_of(fp):
    if not fp:
        return 0
    return fp.get("size", fp.get("bytes", 0)) or 0


def settings_hash(settings):
    return hashlib.sha256(json.dumps(settings or {}, sort_keys=True, default=str).encode()).hexdigest()


def computer_name():
    return os.environ.get("COMPUTERNAME") or platform.node()


class Plan:
    """The exact list of actions a dry run showed. Build it, show it, and give it to executor.execute."""

    def __init__(self, command, ctx=None, settings=None, info=None):
        self.ctx = ctx or context.current()
        self.id = context.new_run_id()
        self.command = command
        self.created = time.time()
        self.computer = computer_name()
        self.sid = _win.user_sid()
        self.elevated = _win.is_elevated()
        self.settings_hash = settings_hash(settings)
        self.info = dict(info or {})
        self.actions = []
        self.journal_fallback = None     # a folder on the destination volume, for a full C:
        self._volumes = {}

    def _add(self, action):
        action["n"] = len(self.actions) + 1
        self.actions.append(action)
        return action

    def _volume(self, path):
        folder = os.path.dirname(os.path.abspath(path))
        if folder not in self._volumes:
            self._volumes[folder] = volume.volume_id(folder)
        return self._volumes[folder]

    def add_move(self, src, dst, **extra):
        """A move. Raises FileNotFoundError if src isn't there."""
        src, dst = os.path.abspath(os.fspath(src)), os.path.abspath(os.fspath(dst))
        fp = fingerprint(src)
        if fp is None:
            raise FileNotFoundError(f"{src} isn't there")
        src_volume, dst_volume = self._volume(src), self._volume(dst)
        same = src_volume is not None and src_volume == dst_volume
        return self._add(dict(op="move", src=src, dst=dst, kind=fp["kind"], src_volume=src_volume,
                              dst_volume=dst_volume, same_volume=same, fp=fp, bytes=size_of(fp), **extra))

    def add_delete(self, path):
        path = os.path.abspath(os.fspath(path))
        fp = fingerprint(path)
        if fp is None:
            raise FileNotFoundError(f"{path} isn't there")
        if fp["kind"] == "dir":
            return self._add(dict(op="delete_tree", path=path, fp=fp, bytes=size_of(fp)))
        return self._add(dict(op="delete", path=path, fp=fp, bytes=size_of(fp)))

    def add_delete_contents(self, folder):
        """Everything inside a folder: files one by one, subfolders as trees, links as links."""
        out = []
        try:
            entries = sorted(os.scandir(lp(folder)), key=lambda e: e.name.lower())
        except OSError:
            return out
        for e in entries:
            try:
                out.append(self.add_delete(os.path.join(os.path.abspath(folder), e.name)))
            except FileNotFoundError:
                pass
        return out

    def add_rmdir(self, path, quiet=False):
        return self._add(dict(op="rmdir", path=os.path.abspath(os.fspath(path)), quiet=quiet))

    def add_tool(self, tool, measured=None, **extra):
        from .executor import TOOLS
        spec = TOOLS[tool]
        return self._add(dict(op="run_tool", tool=tool, interactive=spec.interactive, measured=measured, **extra))

    def add_count_only(self, op, measured):
        return self._add(dict(op=op, measured=measured))

    @property
    def total_bytes(self):
        return sum(a.get("bytes", 0) for a in self.actions)

    def record(self):
        return {"plan": self.id, "command": self.command, "created": self.created,
                "created_text": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.created)),
                "computer": self.computer, "sid": self.sid, "elevated": self.elevated,
                "settings_hash": self.settings_hash, "info": self.info, "actions": self.actions}

    def save_copy(self):
        """Save a readable copy to plans\\ in the data folder. Returns its path, or None if it
        couldn't be written (C: full): that never stops anything, the list is on screen anyway."""
        folder = self.ctx.folder("plans")
        path = os.path.join(folder, f"{self.id}-{self.command}.json")
        try:
            os.makedirs(folder, exist_ok=True)
            with open(path, "x", encoding="utf-8") as f:
                f.write("// A copy of the list pc-tidy showed you. Editing it changes nothing: pc-tidy only\n"
                        "// ever does the list it holds in memory.\n")
                json.dump(self.record(), f, indent=1, ensure_ascii=False)
        except OSError:
            return None
        _rotate(folder, KEEP_PLAN_COPIES)
        return path


def _rotate(folder, keep):
    """Remove pc-tidy's own oldest plan copies beyond `keep`."""
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
        for name in names[:-keep]:
            os.remove(os.path.join(folder, name))
    except OSError:
        pass
