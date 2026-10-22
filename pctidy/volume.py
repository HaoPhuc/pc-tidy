"""'Another drive' means another volume, not another letter (problem 15).

Space on C: is freed only when bytes leave C:'s volume. A subst letter, a junction or a mapped
drive that points back into C: is the same volume under another name, while a second disk mounted
in a folder on C: (C:\\Data) is a different one. volume_id() tells them apart.
"""

import os
import re
from collections import namedtuple

from . import _win, context
from .paths import inside, norm

GB = 1024 ** 3
FAT32_MAX_FILE = 4 * GB          # FAT32 can't hold a file of 4 GB or more
_GUID = re.compile(r"^\\\\\?\\Volume\{[0-9a-f-]+\}\\", re.I)


def nearest_existing(path):
    """The path itself, or its closest parent that exists. None if not even the drive exists."""
    path = os.path.abspath(_win.plain_path(os.fspath(path)))
    while True:
        if os.path.lexists(_win.long_path(path)):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def volume_id(path):
    """'\\\\?\\volume{...}\\' for the volume the path's bytes are on (its nearest existing parent's,
    if it doesn't exist yet). None for a network path or when Windows can't tell."""
    anchor = nearest_existing(path)
    if anchor is None or not _win.ON_WINDOWS:
        return None
    try:
        final = _win.final_path_guid(anchor)
    except OSError:
        return None
    m = _GUID.match(final)
    return m.group(0).lower() if m else None


def same_volume(a, b):
    """True or False, or None when either volume can't be told."""
    va, vb = volume_id(a), volume_id(b)
    if va is None or vb is None:
        return None
    return va == vb


def is_network(path):
    p = os.path.abspath(_win.plain_path(os.fspath(path)))
    if p.startswith("\\\\"):
        return True
    drive = os.path.splitdrive(p)[0]
    if not drive or not _win.ON_WINDOWS:
        return False
    if _win.drive_type(drive + "\\") == _win.DRIVE_REMOTE:
        return True
    device = _win.dos_device(drive)
    return "\\lanmanredirector\\" in device.lower() or device.lower().startswith("\\device\\mup\\")


def subst_target(letter):
    """The folder a subst letter points at, or None if it isn't one."""
    device = _win.dos_device(letter[:2]) if _win.ON_WINDOWS else ""
    return device[4:] if device.startswith("\\??\\") else None


def free_bytes(path):
    """Free bytes on the volume of a path (or its nearest existing parent). None if unknown."""
    anchor = nearest_existing(path)
    if anchor is None:
        return None
    if not os.path.isdir(anchor):
        anchor = os.path.dirname(anchor)
    try:
        return _win.disk_free(_win.long_path(anchor).rstrip("\\") + "\\")[0]
    except OSError:
        return None


class Volume(namedtuple("Volume", "guid mounts fs label free total drive_type usb problem")):
    @property
    def ready(self):
        return not self.problem

    @property
    def letter(self):
        return next((m for m in self.mounts if len(m) == 3 and m[1] == ":"), None)

    @property
    def name(self):
        """'E:' for a drive letter, the folder for a folder mount."""
        where = self.letter or (self.mounts[0] if self.mounts else self.guid)
        return where[:2] if self.letter else where.rstrip("\\")

    def default_folder(self, name=context.DEST_FOLDER_NAME):
        return os.path.join(self.letter or self.mounts[0], name)


def describe(guid, mounts=None):
    """A Volume for '\\\\?\\Volume{...}\\'."""
    mounts = _win.mount_paths(guid) if mounts is None else mounts
    dtype = _win.drive_type(guid)
    fs = label = ""
    free = total = 0
    problem = None
    try:
        label, fs = _win.file_system(guid)
        free, total = _win.disk_free(guid)
    except OSError:
        name = mounts[0][:2] if mounts and mounts[0][1:2] == ":" else (mounts[0] if mounts else guid)
        problem = f"{name} can't be read (it may be locked with BitLocker, or not ready)"
    usb = dtype == _win.DRIVE_REMOVABLE or _win.bus_type(guid) == _win.BUS_TYPE_USB
    return Volume(guid.lower(), mounts, fs, label, free, total, dtype, usb, problem)


def local_volumes():
    """Every volume in or attached to this PC that has a letter or a folder, ready or not."""
    if not _win.ON_WINDOWS:
        return []
    out = []
    for guid in _win.volume_guids():
        mounts = _win.mount_paths(guid)
        if mounts and _win.drive_type(guid) in (_win.DRIVE_FIXED, _win.DRIVE_REMOVABLE):
            out.append(describe(guid, mounts))
    return out


def volume_of(path):
    """The Volume a path is on, or None."""
    vid = volume_id(path)
    if vid is None:
        return None
    try:
        mount = _win.volume_path_name(nearest_existing(path))
    except OSError:
        mount = None
    return describe(vid, [mount] if mount else None)


def default_destination(ctx=None, folder_name=context.DEST_FOLDER_NAME):
    """(destination folder or None, [notes about drives that were skipped]).

    The local volume with the most free space other than C:'s, chosen from volumes, not letters, so
    a subst letter can never be picked. Drives on USB come last: they aren't always plugged in."""
    ctx = ctx or context.current()
    system = volume_id(ctx.system_drive)
    notes, ready = [], []
    for v in local_volumes():
        if v.guid == system:
            continue
        if ctx.sandbox and not any(ctx.in_sandbox(m) for m in v.mounts):
            continue
        if v.problem:
            notes.append(f"Skipped {v.problem}.")
        else:
            ready.append(v)
    if not ready:
        return None, notes
    best = max(ready, key=lambda v: (not v.usb, v.free))
    return best.default_folder(folder_name), notes


def destination_problem(dest, ctx=None):
    """Why moving to `dest` would not free space on C:, or None if it would."""
    ctx = ctx or context.current()
    sys_letter = ctx.system_drive[:2]
    if is_network(dest):
        return f"{dest} is on a network drive. pc-tidy only moves to drives in or attached to this PC."
    letter = os.path.splitdrive(os.path.abspath(dest))[0].upper() or dest
    if nearest_existing(dest) is None:
        return f"{letter} doesn't exist"
    vid, system = volume_id(dest), volume_id(ctx.system_drive)
    try:
        _win.file_system(vid or letter + "\\")
    except OSError:
        return f"{letter} can't be read (it may be locked with BitLocker). Unlock it or pick another drive."
    if vid is None or system is None:
        return f"pc-tidy can't tell which drive {dest} is on, so it can't check that moving there frees {sys_letter}"
    if vid != system:
        return None
    if letter == sys_letter.upper():
        return "the destination is on the same drive, that frees no space"
    if subst_target(letter):
        return f"{letter} is a folder on {sys_letter} (a subst drive), so moving there frees nothing on {sys_letter}."
    return f"{dest} is a folder on {sys_letter} (through a link), so moving there frees nothing on {sys_letter}."


def is_default_folder(dest, folder_name=context.DEST_FOLDER_NAME):
    """True if dest is exactly <a mount point of its volume>\\From-C, where a journal can be kept."""
    anchor = nearest_existing(dest)
    if anchor is None:
        return False
    try:
        mount = _win.volume_path_name(anchor)
    except OSError:
        return False
    return norm(dest) == norm(os.path.join(mount, folder_name))


def journal_folders(ctx=None, folder_name=context.DEST_FOLDER_NAME):
    """<mount>\\From-C\\_pc-tidy on every ready local volume: where full-C: runs keep their journal."""
    ctx = ctx or context.current()
    out = []
    for v in local_volumes():
        if v.problem:
            continue
        for m in v.mounts:
            folder = os.path.join(m, folder_name, context.VOLUME_JOURNAL_FOLDER)
            if ctx.sandbox and not ctx.in_sandbox(folder):
                continue
            if os.path.isdir(_win.long_path(folder)):
                out.append(folder)
    return out


def under_any(path, folders):
    return any(inside(path, f) for f in folders)
