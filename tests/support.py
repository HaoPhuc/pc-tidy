"""Shared helpers for the tests: a fake user profile, a way to run the pc-tidy commands against it,
and tree snapshots."""

import ctypes
import os
import stat
import string
import subprocess
import sys
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
        if guarded:
            env.update(PYTHONPATH=os.pathsep.join([str(GUARD_DIR), str(ROOT)]),
                       PCTIDY_TEST_GUARD_ROOTS=os.pathsep.join(str(r) for r in self.roots),
                       PCTIDY_TEST_GUARD_LOG=str(self.guard_log))
        return env

    def python(self, script, *args, input=None, extra_roots=(), expect_violations=False, timeout=600):
        """Run a Python script (a pc-tidy command, or a test driver) and return the CompletedProcess."""
        env = self.env()
        if extra_roots:
            env["PCTIDY_TEST_GUARD_ROOTS"] += os.pathsep + os.pathsep.join(str(r) for r in extra_roots)
        env["SystemDrive"] = os.path.splitdrive(str(self.home))[0]
        script = Path(script)
        if not script.is_absolute():
            script = ROOT / script
        result = subprocess.run([sys.executable, str(script), *map(str, args)], cwd=str(self.work), env=env,
                                input=input, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout)
        result.output = result.stdout + result.stderr
        self._check_guard(result, expect_violations)
        return result

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
