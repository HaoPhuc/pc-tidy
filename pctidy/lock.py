"""One pc-tidy run at a time per user: a named mutex, Global\\pc-tidy-<user SID>.

- The scheduler's weekly run and a run in your own window see the same mutex, and so do your normal
  and your admin windows: it is created with a security descriptor that lets this user open it at
  any integrity level. "Access denied" on opening it counts as busy, never as free.
- It needs no file, so it works on a full disk. If the process holding it dies, Windows releases it.
- It is held only while non-interactive actions run or recovery runs, never while pc-tidy (or a tool
  it started) waits for input, so a window left at a prompt never blocks the weekly task.
- lock-info.json in the data folder names the holder for messages. It is best effort: when it can't
  be read the message says "another pc-tidy run". One left behind means the holder died.
"""

import ctypes
import hashlib
import json
import os
import time

from . import _win, context
from .paths import lp, norm

LOCK_WAIT_SECONDS = 60
BUSY_EXIT = 4
BUSY_MESSAGE = "Another pc-tidy run is still working. Nothing was changed."
DIED_MESSAGE = "The previous pc-tidy run ended unexpectedly. Checking for unfinished work."


class Busy(Exception):
    """Another pc-tidy run held the lock for longer than we waited."""


def lock_name(ctx):
    name = f"Global\\pc-tidy-{_win.user_sid()}"
    if ctx.sandbox:     # each test gets its own lock, and never meets a real run on this PC
        name += "-test-" + hashlib.sha1(norm(ctx.sandbox[0]).encode()).hexdigest()[:12]
    return name


def holder(ctx):
    """'another pc-tidy run (move_off_c.py, started 18:00)', or without the details if unknown."""
    try:
        with open(lp(ctx.folder("lock-info.json")), encoding="utf-8") as f:
            info = json.load(f)
        return f"another pc-tidy run ({info['command']}, started {info['started']})"
    except (OSError, ValueError, KeyError, TypeError):
        return "another pc-tidy run"


class Lock:
    def __init__(self, ctx=None, command="pc-tidy"):
        self.ctx = ctx or context.current()
        self.command = command
        self.handle = None
        self.previous_died = False

    def acquire(self, wait=None, say=print):
        """Take the lock, waiting up to `wait` seconds (default 60). Raises Busy."""
        if not _win.ON_WINDOWS:
            return self
        if wait is None:
            hook = self.ctx.test_value("PCTIDY_TEST_LOCK_WAIT")
            wait = float(hook) if hook else LOCK_WAIT_SECONDS
        deadline = time.monotonic() + wait
        told = False
        while True:
            handle = self._open()
            if handle:
                result = _win.kernel32.WaitForSingleObject(handle, 0)
                if result == _win.WAIT_TIMEOUT:
                    if not told:
                        say(f"Waiting for {holder(self.ctx)} to finish...")
                        told = True
                    left = max(0, deadline - time.monotonic())
                    result = _win.kernel32.WaitForSingleObject(handle, int(left * 1000))
                if result in (_win.WAIT_OBJECT_0, _win.WAIT_ABANDONED):
                    self.handle = handle
                    self.previous_died = result == _win.WAIT_ABANDONED
                    break
                _win.kernel32.CloseHandle(handle)
            elif not told:      # access denied: someone holds it in a way we can't even open
                say(f"Waiting for {holder(self.ctx)} to finish...")
                told = True
            if time.monotonic() >= deadline:
                raise Busy(BUSY_MESSAGE)
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        self._check_info()
        if self.previous_died:
            say(DIED_MESSAGE)
        self._write_info()
        return self

    def _open(self):
        name = lock_name(self.ctx)
        sid = _win.user_sid()
        for sddl in (f"D:P(A;;GA;;;{sid})(A;;GA;;;SY)S:(ML;;NW;;;ME)", f"D:P(A;;GA;;;{sid})(A;;GA;;;SY)"):
            sa = _win.security_attributes(sddl)
            if sa is None:
                continue
            handle = _win.kernel32.CreateMutexW(ctypes.byref(sa), False, name)
            if handle:
                return handle
            if ctypes.get_last_error() == _win.ERROR_ACCESS_DENIED:
                break
        handle = _win.kernel32.CreateMutexW(None, False, name)
        if handle:
            return handle
        return _win.kernel32.OpenMutexW(_win.SYNCHRONIZE | _win.MUTEX_MODIFY_STATE, False, name)

    def _check_info(self):
        try:
            with open(lp(self.ctx.folder("lock-info.json")), encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            return
        if isinstance(info, dict) and info.get("pid") != os.getpid():
            self.previous_died = True      # its holder never got to remove it

    def _write_info(self):
        info = {"command": self.command, "pid": os.getpid(), "started": time.strftime("%H:%M")}
        try:      # the data folder at most, never its parents: those it may not even be able to see
            if not os.path.isdir(lp(self.ctx.data_dir)):
                os.mkdir(lp(self.ctx.data_dir))
        except OSError:
            pass
        try:
            with open(lp(self.ctx.folder("lock-info.json")), "w", encoding="utf-8") as f:
                json.dump(info, f)
        except OSError:
            pass

    def release(self):
        if self.handle is None:
            return
        try:      # remove the note before letting go, so the next holder never finds ours
            os.remove(lp(self.ctx.folder("lock-info.json")))
        except OSError:
            pass
        _win.kernel32.ReleaseMutex(self.handle)
        _win.kernel32.CloseHandle(self.handle)
        self.handle = None

    def __enter__(self):
        return self if self.handle else self.acquire()

    def __exit__(self, *exc):
        self.release()
