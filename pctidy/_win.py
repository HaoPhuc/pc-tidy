"""The few Windows calls pc-tidy needs, through ctypes (standard library only).

Every function here only reads, except the mutex calls used by lock.py.
"""

import ctypes
import functools
import os
from ctypes import wintypes

ON_WINDOWS = os.name == "nt"

ERROR_FILE_NOT_FOUND = 2
ERROR_PATH_NOT_FOUND = 3
ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33
ERROR_HANDLE_DISK_FULL = 39
ERROR_DISK_FULL = 112
ERROR_ALREADY_EXISTS = 183

DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_REMOTE, DRIVE_CDROM, DRIVE_RAMDISK = 2, 3, 4, 5, 6
BUS_TYPE_USB = 7

FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

_OPEN_EXISTING = 3
_FILE_SHARE_ALL = 0x1 | 0x2 | 0x4
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_VOLUME_NAME_GUID = 0x1
_IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
_TOKEN_QUERY = 0x8
_TOKEN_USER, _TOKEN_ELEVATION = 1, 20

SYNCHRONIZE = 0x00100000
MUTEX_MODIFY_STATE = 0x0001
WAIT_OBJECT_0, WAIT_ABANDONED, WAIT_TIMEOUT = 0x0, 0x80, 0x102

if ON_WINDOWS:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    def _declare(fn, restype, *argtypes):
        fn.restype, fn.argtypes = restype, list(argtypes)

    _H, _D, _B, _S, _WS, _P = (wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR,
                               wintypes.LPWSTR, wintypes.LPVOID)
    _PD = ctypes.POINTER(wintypes.DWORD)
    _PU = ctypes.POINTER(ctypes.c_ulonglong)
    _declare(kernel32.CreateFileW, _H, _S, _D, _D, _P, _D, _D, _H)
    _declare(kernel32.CloseHandle, _B, _H)
    _declare(kernel32.GetFinalPathNameByHandleW, _D, _H, _WS, _D, _D)
    _declare(kernel32.FindFirstVolumeW, _H, _WS, _D)
    _declare(kernel32.FindNextVolumeW, _B, _H, _WS, _D)
    _declare(kernel32.FindVolumeClose, _B, _H)
    _declare(kernel32.GetVolumePathNamesForVolumeNameW, _B, _S, _WS, _D, _PD)
    _declare(kernel32.GetVolumePathNameW, _B, _S, _WS, _D)
    _declare(kernel32.GetDriveTypeW, wintypes.UINT, _S)
    _declare(kernel32.GetVolumeInformationW, _B, _S, _WS, _D, _PD, _PD, _PD, _WS, _D)
    _declare(kernel32.GetDiskFreeSpaceExW, _B, _S, _PU, _PU, _PU)
    _declare(kernel32.DeviceIoControl, _B, _H, _D, _P, _D, _P, _D, _PD, _P)
    _declare(kernel32.QueryDosDeviceW, _D, _S, _WS, _D)
    _declare(kernel32.GetCurrentProcess, _H)
    _declare(kernel32.LocalFree, _P, _P)
    _declare(kernel32.CreateMutexW, _H, _P, _B, _S)
    _declare(kernel32.OpenMutexW, _H, _D, _B, _S)
    _declare(kernel32.WaitForSingleObject, _D, _H, _D)
    _declare(kernel32.ReleaseMutex, _B, _H)
    _declare(advapi32.OpenProcessToken, _B, _H, _D, ctypes.POINTER(_H))
    _declare(advapi32.GetTokenInformation, _B, _H, ctypes.c_int, _P, _D, _PD)
    _declare(advapi32.ConvertSidToStringSidW, _B, _P, ctypes.POINTER(_P))
    _declare(advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW, _B, _S, _D, ctypes.POINTER(_P), _PD)


def _error(what):
    code = ctypes.get_last_error()
    err = ctypes.WinError(code)
    err.strerror = f"{what}: {err.strerror}"
    return err


def long_path(path):
    """The path with the \\\\?\\ prefix, so paths over 260 characters work."""
    path = os.fspath(path)
    if path.startswith("\\\\?\\") or path.startswith("\\\\.\\"):
        return path
    path = os.path.abspath(path)
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def plain_path(path):
    """The path without the \\\\?\\ prefix."""
    path = os.fspath(path)
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\") and not path.lower().startswith("\\\\?\\volume{"):
        return path[4:]
    return path


def final_path_guid(path):
    """'\\\\?\\Volume{...}\\rest\\of\\path': where the path's bytes really are, with subst letters,
    junctions and folder mount points resolved. Raises OSError (also for network paths)."""
    handle = kernel32.CreateFileW(long_path(path), 0, _FILE_SHARE_ALL, None, _OPEN_EXISTING,
                                  _FILE_FLAG_BACKUP_SEMANTICS, None)
    if handle == INVALID_HANDLE_VALUE:
        raise _error(f"can't open {path}")
    try:
        buf = ctypes.create_unicode_buffer(32768)
        if not kernel32.GetFinalPathNameByHandleW(handle, buf, len(buf), _VOLUME_NAME_GUID):
            raise _error(f"can't tell which volume {path} is on")
        return buf.value
    finally:
        kernel32.CloseHandle(handle)


def volume_guids():
    """Every volume on this PC, as '\\\\?\\Volume{...}\\'."""
    buf = ctypes.create_unicode_buffer(1024)
    handle = kernel32.FindFirstVolumeW(buf, len(buf))
    if handle == INVALID_HANDLE_VALUE:
        return []
    out = [buf.value]
    try:
        while kernel32.FindNextVolumeW(handle, buf, len(buf)):
            out.append(buf.value)
    finally:
        kernel32.FindVolumeClose(handle)
    return out


def mount_paths(guid):
    """Where a volume is mounted: drive letters ('E:\\') and folders ('C:\\Data\\')."""
    size = wintypes.DWORD(0)
    kernel32.GetVolumePathNamesForVolumeNameW(guid, None, 0, ctypes.byref(size))
    if not size.value:
        return []
    buf = ctypes.create_unicode_buffer(size.value)
    if not kernel32.GetVolumePathNamesForVolumeNameW(guid, buf, size.value, ctypes.byref(size)):
        return []
    return [p for p in ctypes.wstring_at(ctypes.addressof(buf), size.value).split("\0") if p]


def volume_path_name(path):
    """The mount point a path is under: 'E:\\', or 'C:\\Data\\' for a folder mount."""
    buf = ctypes.create_unicode_buffer(32768)
    if not kernel32.GetVolumePathNameW(os.path.abspath(path), buf, len(buf)):
        raise _error(f"can't find the drive of {path}")
    return buf.value


def drive_type(root):
    return kernel32.GetDriveTypeW(root if root.endswith("\\") else root + "\\")


def file_system(root):
    """(label, file system name), e.g. ('Data', 'NTFS'). Raises OSError for a locked or missing drive."""
    label, fs = ctypes.create_unicode_buffer(261), ctypes.create_unicode_buffer(261)
    if not kernel32.GetVolumeInformationW(root if root.endswith("\\") else root + "\\", label, len(label),
                                          None, None, None, fs, len(fs)):
        raise _error(f"can't read {root}")
    return label.value, fs.value


def disk_free(path):
    """(free bytes for this user, total bytes) of the volume a folder is on. Raises OSError."""
    free, total, _all = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
    if not kernel32.GetDiskFreeSpaceExW(path, ctypes.byref(free), ctypes.byref(total), ctypes.byref(_all)):
        raise _error(f"can't read the free space of {path}")
    return free.value, total.value


class _PropertyQuery(ctypes.Structure):
    _fields_ = [("PropertyId", ctypes.c_int), ("QueryType", ctypes.c_int),
                ("AdditionalParameters", ctypes.c_ubyte * 1)]


class _DeviceDescriptor(ctypes.Structure):
    _fields_ = [("Version", wintypes.DWORD), ("Size", wintypes.DWORD), ("DeviceType", ctypes.c_ubyte),
                ("DeviceTypeModifier", ctypes.c_ubyte), ("RemovableMedia", ctypes.c_ubyte),
                ("CommandQueueing", ctypes.c_ubyte), ("VendorIdOffset", wintypes.DWORD),
                ("ProductIdOffset", wintypes.DWORD), ("ProductRevisionOffset", wintypes.DWORD),
                ("SerialNumberOffset", wintypes.DWORD), ("BusType", ctypes.c_int),
                ("RawPropertiesLength", wintypes.DWORD), ("RawDeviceProperties", ctypes.c_ubyte * 1)]


def bus_type(volume):
    """The storage bus of a volume ('E:\\' or '\\\\?\\Volume{...}\\'), e.g. BUS_TYPE_USB. None if unknown.
    Opening the device with no access rights is enough, so this needs no admin."""
    device = volume.rstrip("\\")
    if len(device) == 2 and device[1] == ":":
        device = "\\\\.\\" + device
    handle = kernel32.CreateFileW(device, 0, _FILE_SHARE_ALL, None, _OPEN_EXISTING, 0, None)
    if handle == INVALID_HANDLE_VALUE:
        return None
    try:
        query = _PropertyQuery(0, 0)      # StorageDeviceProperty, PropertyStandardQuery
        out = ctypes.create_string_buffer(1024)
        returned = wintypes.DWORD()
        if not kernel32.DeviceIoControl(handle, _IOCTL_STORAGE_QUERY_PROPERTY, ctypes.byref(query),
                                        ctypes.sizeof(query), out, len(out), ctypes.byref(returned), None):
            return None
        return _DeviceDescriptor.from_buffer_copy(out[:ctypes.sizeof(_DeviceDescriptor)]).BusType
    finally:
        kernel32.CloseHandle(handle)


def dos_device(letter):
    """What a drive letter points at: '\\\\Device\\\\HarddiskVolume3', or '\\\\??\\\\C:\\\\...' for subst."""
    buf = ctypes.create_unicode_buffer(32768)
    if not kernel32.QueryDosDeviceW(letter.rstrip("\\"), buf, len(buf)):
        return ""
    return buf.value


def _token_info(kind):
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise _error("can't read this process's token")
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, kind, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(max(size.value, 4))
        if not advapi32.GetTokenInformation(token, kind, buf, len(buf), ctypes.byref(size)):
            raise _error("can't read this process's token")
        return buf
    finally:
        kernel32.CloseHandle(token)


@functools.lru_cache(maxsize=None)
def user_sid():
    """The SID of the user this process runs as, e.g. 'S-1-5-21-...-1001'."""
    if not ON_WINDOWS:
        return f"uid-{os.getuid()}"
    buf = _token_info(_TOKEN_USER)
    psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
    text = ctypes.c_void_p()
    if not advapi32.ConvertSidToStringSidW(psid, ctypes.byref(text)):
        raise _error("can't read the user's SID")
    try:
        return ctypes.wstring_at(text.value)
    finally:
        kernel32.LocalFree(text)


def is_elevated():
    if not ON_WINDOWS:
        return os.geteuid() == 0
    try:
        return bool(ctypes.cast(_token_info(_TOKEN_ELEVATION), ctypes.POINTER(wintypes.DWORD))[0])
    except OSError:
        return False


class SecurityAttributes(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", wintypes.LPVOID),
                ("bInheritHandle", wintypes.BOOL)]


def security_attributes(sddl):
    """SECURITY_ATTRIBUTES for an SDDL string, or None if Windows refuses it. The descriptor is
    deliberately never freed: it lives as long as the process, and there is one per lock."""
    descriptor = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        return None
    return SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)


def winerror(exc):
    return getattr(exc, "winerror", None) or 0


def in_use(exc):
    """True for 'the file is open in another program'."""
    return winerror(exc) in (ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION)


def disk_full(exc):
    import errno
    return winerror(exc) in (ERROR_DISK_FULL, ERROR_HANDLE_DISK_FULL) or getattr(exc, "errno", None) == errno.ENOSPC
