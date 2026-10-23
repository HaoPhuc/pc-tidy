"""Walks one or more drives or folders and measures them. Used by disk_report.py and move_off_c.py."""

import heapq
import os
import shutil
import string
import time

from pctidy.progress import Line, duration

GB = 1024 ** 3   # the unit Windows Explorer calls "GB"

# Windows reparse tags and file attributes
JUNCTION, SYMLINK = 0xA0000003, 0xA000000C
CLOUD_ONLY = 0x1000 | 0x40000 | 0x400000   # online-only OneDrive files use no disk space


def norm(path):
    """Lowercase path with forward slashes, for matching rules."""
    return path.replace("\\", "/").lower().rstrip("/")


def fmt(size):
    return f"{size / GB:7.1f} GB"


def system_drive():
    return os.environ.get("SystemDrive", "C:").upper() + "\\"


def fixed_drives():
    """All local fixed drives, e.g. ['C:\\', 'E:\\']. Outside Windows: just the home folder."""
    if os.name != "nt":
        return [os.path.expanduser("~")]
    import ctypes
    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    drives = []
    for i, letter in enumerate(string.ascii_uppercase):
        root = f"{letter}:\\"
        if mask & (1 << i) and k32.GetDriveTypeW(ctypes.c_wchar_p(root)) == 3:   # 3 = DRIVE_FIXED
            drives.append(root)
    return drives


def clean_root(path):
    """'C:' alone means 'current folder on C:', so turn it into 'C:\\'."""
    if len(path) == 2 and path[1] == ":":
        path += "\\"
    return os.path.abspath(path)


class Scan:
    """Results of scanning one or more roots.

    sizes[folder]    total bytes under a folder
    own[folder]      bytes of the files directly inside a folder
    children[folder] its subfolders
    big_files        the TOP_FILES biggest files, biggest first, as (size, path)
    files_over       every file of at least files_over_bytes, as (size, path)
    usage[root]      shutil.disk_usage of each root
    """

    def __init__(self, roots, top_files=20, files_over_bytes=GB):
        self.roots = list(roots)
        self.sizes, self.own, self.children = {}, {}, {}
        self.files_over, self.usage = [], {}
        self.stats = {"files": 0, "bytes": 0, "denied": 0, "seconds": 0.0}
        self._heap, self._top, self._over = [], top_files, files_over_bytes
        self._line = Line()
        self._t0 = time.time()
        for root in self.roots:
            self.usage[root] = shutil.disk_usage(root)
            self._walk(root)
        self._line.clear()
        self.stats["seconds"] = time.time() - self._t0
        self.big_files = sorted(self._heap, reverse=True)
        self.files_over.sort(reverse=True)

    def _progress(self, top):
        elapsed = duration(time.time() - self._t0)
        self._line.show(f"{os.path.basename(top) or top}: {self.stats['files']:,} files, "
                        f"{self.stats['bytes'] / GB:.1f} GB, {elapsed} ...")

    def _walk(self, root):
        """Without recursion, so a folder tree of any depth can't overflow the stack (problem 12)."""
        stack = [(root, False, root)]          # (folder, its subfolders are done, top folder for progress)
        pending = {}
        while stack:
            folder, done, top = stack.pop()
            if done:
                own, subs = pending.pop(folder)
                self.sizes[folder] = own + sum(self.sizes[s] for s in subs)
                self.own[folder], self.children[folder] = own, subs
                continue
            try:
                entries = os.scandir(folder)
            except OSError:
                self.stats["denied"] += 1
                self.sizes[folder] = self.own[folder] = 0
                self.children[folder] = []
                continue
            own, subs = 0, []
            with entries:
                for e in entries:
                    try:
                        st = e.stat(follow_symlinks=False)
                        if e.is_symlink() or getattr(st, "st_reparse_tag", 0) in (JUNCTION, SYMLINK):
                            continue   # links point at folders that are counted elsewhere
                        is_dir = e.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if is_dir:
                        subs.append(e.path)
                        continue
                    size = 0 if getattr(st, "st_file_attributes", 0) & CLOUD_ONLY else st.st_size
                    own += size
                    self.stats["files"] += 1
                    self.stats["bytes"] += size
                    if len(self._heap) < self._top:
                        heapq.heappush(self._heap, (size, e.path))
                    elif size > self._heap[0][0]:
                        heapq.heapreplace(self._heap, (size, e.path))
                    if size >= self._over:
                        self.files_over.append((size, e.path))
                    self._progress(top)
            pending[folder] = (own, subs)
            stack.append((folder, True, top))
            for sub in reversed(subs):
                stack.append((sub, False, sub if folder == root else top))
        return self.sizes[root]
