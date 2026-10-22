"""Shared helpers for the tests: a fake user profile, a way to run the pc-tidy commands against it,
and tree snapshots."""

import ctypes
import os
import shutil
import stat
import string
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD_DIR = Path(__file__).resolve().parent / "guard"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DAY = 86400

FILE_ATTRIBUTE_HIDDEN = 0x2


def long(path):
    """The path with the \\\\?\\ prefix, so paths over 260 characters work everywhere."""
    path = os.path.abspath(str(path))
    return path if path.startswith("\\\\?\\") else "\\\\?\\" + path


def is_admin():
    return os.name == "nt" and ctypes.windll.shell32.IsUserAnAdmin() != 0


def free_letter():
    """An unused drive letter, from Z: down."""
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for letter in reversed(string.ascii_uppercase[3:]):
        if not mask & (1 << (ord(letter) - ord("A"))):
            return letter
    pytest.skip("no free drive letter")


def write(path, data=b"x", age_days=1.0):
    """Create a file (and its folders) and set its modified time age_days back."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    t = time.time() - age_days * DAY
    os.utime(path, (t, t))
    return path


def set_hidden(path):
    attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    if not ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs | FILE_ATTRIBUTE_HIDDEN):
        raise ctypes.WinError()


def make_junction(link, target):
    """A directory junction, which needs no admin rights (a symlink would)."""
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                   check=True, capture_output=True)


def make_fake_profile(base, big_cache=False):
    """A user profile in a temporary folder, with the awkward files real profiles have."""
    home = base / "profile"
    for d in ("Desktop", "Documents", "Downloads", "Music", "Pictures", "Videos",
              "AppData/Local/Temp", "AppData/Roaming"):
        (home / d).mkdir(parents=True)

    dl = home / "Downloads"
    write(dl / "setup.exe", b"MZ installer")
    write(dl / "old-setup.msi", b"old installer", age_days=90)
    write(dl / "photo.jpg", b"jpeg bytes")
    write(dl / "\U0001F389 party.png", b"png bytes")
    write(dl / "song.mp3", b"same song" * 100)
    write(dl / "song copy.mp3", b"same song" * 100)      # a duplicate
    write(dl / "data.csv", b"a,b\n1,2\n")
    write(dl / "old-notes.zip", b"old zip", age_days=90)
    write(dl / "report [1].pdf", b"%PDF-1.4 not really")  # no rule for .pdf: needs the LLM
    write(dl / "fresh.jpg", b"still downloading", age_days=0)
    write(dl / "desktop.ini", b"[.ShellClassInfo]")
    set_hidden(write(dl / "hidden.png", b"hidden"))
    os.chmod(write(dl / "readonly.zip", b"read-only zip"), stat.S_IREAD)
    deep = dl / "deep-folder" / ("x" * 100) / ("y" * 100)
    os.makedirs(long(deep))
    with open(long(deep / ("z" * 60 + ".txt")), "wb") as f:     # a path over 260 characters
        f.write(b"deep")

    lectures = home / "Videos" / "Lectures"
    write(lectures / "lecture1.mp4", b"video" * 400)
    write(lectures / "\U0001F393 intro.mkv", b"intro" * 50)
    write(lectures / "notes" / "week1.txt", b"week 1 notes")
    os.chmod(write(lectures / "slides.pdf", b"slides"), stat.S_IREAD)

    write(home / "Documents" / "draft.txt", b"my draft")

    set_hidden(home / "AppData")      # as in a real profile
    local = home / "AppData" / "Local"
    write(local / "Temp" / "tmp1.tmp", b"t" * 100)
    precious = write(home / "Precious" / "keep.txt", b"k" * 1000).parent
    make_junction(local / "Temp" / "link-to-precious", precious)
    if big_cache:
        big = local / "pip" / "cache" / "wheels.bin"
        big.parent.mkdir(parents=True)
        with open(big, "wb") as f:
            f.truncate(110 * 1024 ** 2)      # over disk_report's 0.1 GB cleanup threshold
    return home


def remove_tree(path):
    """shutil.rmtree that also removes read-only files."""
    def force(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(long(path), onexc=force)
    else:
        shutil.rmtree(long(path), onerror=force)


def snapshot(folder):
    """{relative path: (size, modified time)} for every file under a folder, and 'dir' for folders."""
    root = long(folder)
    out = {}
    for dirpath, dirs, files in os.walk(root):
        for d in dirs:
            out[os.path.relpath(os.path.join(dirpath, d), root)] = "dir"
        for f in files:
            st = os.stat(os.path.join(dirpath, f))
            out[os.path.relpath(os.path.join(dirpath, f), root)] = (st.st_size, st.st_mtime_ns)
    return out


def contents(folder):
    """{relative path: bytes} for every file under a folder. Used across volumes, where times can shift."""
    root = long(folder)
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            with open(os.path.join(dirpath, f), "rb") as fh:
                out[os.path.relpath(os.path.join(dirpath, f), root)] = fh.read()
    return out


class Runner:
    """Runs pc-tidy's commands as a user whose profile is the fake one.

    USERPROFILE, LOCALAPPDATA, APPDATA and TEMP point into the fake profile, and the write guard
    (tests/guard) stops the command from changing anything outside the allowed folders.
    """

    def __init__(self, home, work, roots):
        self.home, self.work = Path(home), Path(work)
        self.roots = [Path(r) for r in roots]
        self.guard_log = self.work / "guard-violations.log"

    def env(self, guarded=True):
        env = dict(os.environ)
        local = self.home / "AppData" / "Local"
        env.update(USERPROFILE=str(self.home), LOCALAPPDATA=str(local),
                   APPDATA=str(self.home / "AppData" / "Roaming"),
                   TEMP=str(local / "Temp"), TMP=str(local / "Temp"),
                   PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        roots = os.pathsep.join(str(r) for r in self.roots)
        env["PCTIDY_SANDBOX_ROOT"] = roots     # pc-tidy's own safety check refuses anything outside
        if guarded:
            env.update(PYTHONPATH=os.pathsep.join([str(GUARD_DIR), str(ROOT)]),
                       PCTIDY_TEST_GUARD_ROOTS=roots, PCTIDY_TEST_GUARD_LOG=str(self.guard_log))
        return env

    def command_env(self, env=None, extra_roots=()):
        """The environment a command runs with: env() plus the drive of the fake profile as the system
        drive, `env` on top, and `extra_roots` (test volumes) allowed as well."""
        full = self.env()
        full["SystemDrive"] = os.path.splitdrive(str(self.home))[0]
        full.update(env or {})
        if extra_roots:
            more = os.pathsep + os.pathsep.join(str(r) for r in extra_roots)
            full["PCTIDY_TEST_GUARD_ROOTS"] += more
            full["PCTIDY_SANDBOX_ROOT"] += more
        return full

    @staticmethod
    def _argv(script, args):
        script = Path(script)
        if not script.is_absolute():
            script = ROOT / script
        return [sys.executable, str(script), *map(str, args)]

    def python(self, script, *args, input=None, extra_roots=(), expect_violations=False, timeout=600, env=None,
               stdin=None):
        """Run a Python script (a pc-tidy command, or a test driver) and return the CompletedProcess."""
        result = subprocess.run(self._argv(script, args), cwd=str(self.work), env=self.command_env(env, extra_roots),
                                input=input, stdin=stdin, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout)
        result.output = result.stdout + result.stderr
        self._check_guard(result, expect_violations)
        return result

    def start(self, script, *args, extra_roots=(), env=None):
        """Start a command without waiting for it. See Started."""
        proc = subprocess.Popen(self._argv(script, args), cwd=str(self.work), env=self.command_env(env, extra_roots),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return Started(proc, self)

    def powershell(self, script, *args, extra_env=None, timeout=600):
        """Run a .ps1 file in Windows PowerShell 5.1. -NonInteractive makes any Read-Host fail at once."""
        env = self.env(guarded=False)
        env.update(extra_env or {})
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(script), *map(str, args)], cwd=str(self.work), env=env,
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=timeout)
        result.output = result.stdout + result.stderr
        return result

    def _check_guard(self, result, expect_violations):
        log = self.guard_log.read_text(encoding="utf-8") if self.guard_log.exists() else ""
        if log and not expect_violations:
            pytest.fail(f"the command tried to change files outside the test folders:\n{log}\n"
                        f"output:\n{result.output}")
        result.violations = log.splitlines()
        if self.guard_log.exists():
            self.guard_log.unlink()


class Started:
    """A command running in the background. Its output is collected byte by byte, so a prompt (which
    has no newline) can be waited for."""

    def __init__(self, proc, runner):
        self.proc, self.runner = proc, runner
        self._out = bytearray()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        while True:
            b = self.proc.stdout.read(1)
            if not b:
                return
            self._out += b

    @property
    def output(self):
        return self._out.decode("utf-8", errors="replace")

    def wait_for(self, text, timeout=60):
        deadline = time.monotonic() + timeout
        while text not in self.output:
            if self.proc.poll() is not None and not self._reader.is_alive():
                pytest.fail(f"the command ended before printing {text!r}:\n{self.output}")
            if time.monotonic() > deadline:
                self.proc.kill()
                pytest.fail(f"the command didn't print {text!r} in {timeout} s:\n{self.output}")
            time.sleep(0.05)

    def send(self, text):
        self.proc.stdin.write(text.encode("utf-8"))
        self.proc.stdin.flush()

    def finish(self, timeout=60):
        """Close its input, wait for it to end, and return its exit code."""
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        code = self.proc.wait(timeout)
        self._reader.join(timeout)
        self.runner._check_guard(self, False)
        return code

    def kill(self):
        self.proc.kill()
        self.proc.wait(30)
        self._reader.join(30)


def fill_volume(folder):
    """Fill the volume `folder` is on until not one more byte fits: first clusters, then the records
    in NTFS's file table, which can hold a small file without a free cluster."""
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "filler.bin", "wb", buffering=0) as f:
        for size in (16 * 1024 ** 2, 1024 ** 2, 64 * 1024, 4096, 512, 64, 1):
            block = b"\0" * size
            while True:
                try:
                    if not f.write(block):
                        break
                except OSError:
                    break
    for i in range(100000):
        try:
            (folder / f"d{i}").mkdir()
        except OSError:
            break
        for j in range(1000):
            try:
                (folder / f"d{i}" / str(j)).write_bytes(b"x" * 500)
            except OSError:
                break


def sparse_file(path, size):
    """A file of `size` bytes that takes no disk space: marked sparse, then extended with SetEndOfFile
    (Python's truncate() writes real zeros on Windows). No admin needed."""
    from ctypes import wintypes as w
    write(path, b"")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = w.HANDLE
    k32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
    k32.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, w.LPVOID, w.DWORD, w.LPVOID, w.DWORD,
                                    ctypes.POINTER(w.DWORD), w.LPVOID]
    k32.SetFilePointerEx.argtypes = [w.HANDLE, ctypes.c_longlong, w.LPVOID, w.DWORD]
    k32.SetEndOfFile.argtypes = [w.HANDLE]
    k32.CloseHandle.argtypes = [w.HANDLE]
    handle = k32.CreateFileW(str(path), 0x40000000, 0, None, 3, 0, None)      # GENERIC_WRITE, OPEN_EXISTING
    if handle == w.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        returned = w.DWORD()
        if not (k32.DeviceIoControl(handle, 0x900C4, None, 0, None, 0, ctypes.byref(returned), None)   # FSCTL_SET_SPARSE
                and k32.SetFilePointerEx(handle, size, None, 0) and k32.SetEndOfFile(handle)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        k32.CloseHandle(handle)
    return path


def start_unelevated(argv, env, cwd, output):
    """Start a process as this user but without admin rights, at medium integrity: what a normal (not
    elevated) window of the same user gets. Its stdout and stderr go to the file `output`.
    Returns the process handle, for wait_handle()."""
    import msvcrt
    from ctypes import wintypes as w
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    P = ctypes.c_void_p
    k32.GetCurrentProcess.restype = w.HANDLE
    k32.CloseHandle.argtypes = [w.HANDLE]
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    adv.CreateRestrictedToken.argtypes = [w.HANDLE, w.DWORD, w.DWORD, P, w.DWORD, P, w.DWORD, P,
                                          ctypes.POINTER(w.HANDLE)]
    adv.ConvertStringSidToSidW.argtypes = [w.LPCWSTR, ctypes.POINTER(P)]
    adv.GetLengthSid.argtypes = [P]
    adv.GetLengthSid.restype = w.DWORD
    adv.SetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, P, w.DWORD]
    adv.CreateProcessAsUserW.argtypes = [w.HANDLE, w.LPCWSTR, P, P, P, w.BOOL, w.DWORD, P, w.LPCWSTR, P, P]

    def check(ok, what):
        if not ok:
            raise OSError(f"{what} failed: {ctypes.WinError(ctypes.get_last_error())}")

    token, restricted = w.HANDLE(), w.HANDLE()
    # TOKEN_DUPLICATE | TOKEN_QUERY | TOKEN_ASSIGN_PRIMARY | TOKEN_ADJUST_DEFAULT | TOKEN_ADJUST_SESSIONID
    check(adv.OpenProcessToken(k32.GetCurrentProcess(), 0x2 | 0x8 | 0x1 | 0x80 | 0x100, ctypes.byref(token)),
          "OpenProcessToken")
    check(adv.CreateRestrictedToken(token, 0x4, 0, None, 0, None, 0, None, ctypes.byref(restricted)),   # LUA_TOKEN
          "CreateRestrictedToken")

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", P), ("Attributes", w.DWORD)]
    sid = P()
    check(adv.ConvertStringSidToSidW("S-1-16-8192", ctypes.byref(sid)), "ConvertStringSidToSidW")   # medium
    label = SidAndAttributes(sid, 0x20)      # SE_GROUP_INTEGRITY
    check(adv.SetTokenInformation(restricted, 25, ctypes.byref(label),                 # TokenIntegrityLevel
                                  ctypes.sizeof(label) + adv.GetLengthSid(sid)), "SetTokenInformation")

    class StartupInfo(ctypes.Structure):
        _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR), ("lpTitle", w.LPWSTR),
                    ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD), ("dwYSize", w.DWORD),
                    ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                    ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                    ("lpReserved2", P), ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]

    class ProcessInformation(ctypes.Structure):
        _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE), ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]

    with open(output, "wb") as out, open(os.devnull, "rb") as nul:
        h_out, h_in = msvcrt.get_osfhandle(out.fileno()), msvcrt.get_osfhandle(nul.fileno())
        os.set_handle_inheritable(h_out, True)
        os.set_handle_inheritable(h_in, True)
        si = StartupInfo()
        si.cb = ctypes.sizeof(si)
        si.dwFlags = 0x100           # STARTF_USESTDHANDLES
        si.hStdInput, si.hStdOutput, si.hStdError = h_in, h_out, h_out
        pi = ProcessInformation()
        block = "".join(f"{k}={v}\0" for k, v in sorted(env.items(), key=lambda kv: kv[0].upper())) + "\0"
        env_buf = (ctypes.c_wchar * len(block))(*block)
        cmd = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        check(adv.CreateProcessAsUserW(restricted, None, cmd, None, None, True, 0x400 | 0x08000000, env_buf,
                                       str(cwd), ctypes.byref(si), ctypes.byref(pi)), "CreateProcessAsUserW")
    k32.CloseHandle(pi.hThread)
    for h in (restricted, token):
        k32.CloseHandle(h)
    return pi.hProcess


def wait_handle(handle, timeout=120):
    """Wait for a process started by start_unelevated and return its exit code."""
    from ctypes import wintypes as w
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    k32.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    k32.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
    k32.CloseHandle.argtypes = [w.HANDLE]
    try:
        if k32.WaitForSingleObject(handle, int(timeout * 1000)) != 0:
            k32.TerminateProcess(handle, 1)
            pytest.fail(f"the process didn't end in {timeout} s")
        code = w.DWORD()
        k32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value
    finally:
        k32.CloseHandle(handle)
