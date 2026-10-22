"""Test-only write guard: refuses any change to the file system outside a list of allowed folders.

The tests use it so that a bug in a test or in pc-tidy can never touch real files. It wraps the
functions that create, change or remove files and raises PermissionError for any path outside the
allowed roots. Every refusal is also recorded, because pc-tidy catches most exceptions and carries
on, which would otherwise hide one.

In the pytest process, conftest.py calls set_roots() for each test. In a pc-tidy command started by
a test, sitecustomize.py (in this folder, first on PYTHONPATH) calls install() at interpreter start,
before pathlib is imported, so Path methods are covered too.
"""

import builtins
import io
import os
import shutil

_roots = None          # None: the guard lets everything through
_log_path = None       # where a subprocess records refusals, see PCTIDY_TEST_GUARD_LOG
violations = []        # refusals in this process

_real = {}
_real_open = builtins.open


def _clean(path):
    path = os.fsdecode(os.fspath(path))
    if path.startswith("\\\\?\\UNC\\"):
        path = "\\\\" + path[8:]
    elif path.startswith("\\\\?\\"):
        path = path[4:]
    return path


def _resolve(path):
    """Both the plain absolute path and the real one (junctions and subst letters resolved)."""
    path = _clean(path)
    out = {os.path.normcase(os.path.abspath(path))}
    try:
        out.add(os.path.normcase(_clean(os.path.realpath(path))))
    except (OSError, ValueError):
        pass
    return out


def _inside(candidate, root):
    return candidate == root or candidate.startswith(root.rstrip("\\/") + os.sep)


def check(op, path):
    if _roots is None or isinstance(path, int):
        return
    if _clean(path).lower() in ("nul", "\\\\.\\nul"):      # the null device; Python 3.9's platform module writes to it
        return
    if any(_inside(c, r) for c in _resolve(path) for r in _roots):
        return
    message = f"pc-tidy test guard: refused to {op} {_clean(path)}, which is outside the test folders"
    violations.append(message)
    if _log_path:
        with _real_open(_log_path, "a", encoding="utf-8") as f:
            f.write(message + "\n")
    raise PermissionError(message)


def set_roots(roots, log_path=None):
    """Allow changes only inside these folders. None switches the guard off."""
    global _roots, _log_path
    _roots = None if roots is None else [r for root in roots for r in _resolve(root)]
    _log_path = log_path


def add_root(root):
    _roots.extend(_resolve(root))


class _Guarded:
    """A checked stand-in for a function. An object rather than a function, because Python 3.9's
    pathlib keeps os.mkdir and friends as class attributes, where a plain function would be bound
    as a method and receive the accessor as its first argument."""

    def __init__(self, real, op, nargs):
        self.real, self.op, self.nargs = real, op, nargs
        self.__name__ = self.__qualname__ = getattr(real, "__name__", op)
        self.__wrapped__ = real

    def __call__(self, *args, **kwargs):
        for p in args[:self.nargs]:
            check(self.op, p)
        return self.real(*args, **kwargs)


class _GuardedOsOpen(_Guarded):
    WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

    def __call__(self, path, flags, *args, **kwargs):
        if flags & self.WRITE_FLAGS:
            check(self.op, path)
        return self.real(path, flags, *args, **kwargs)


class _GuardedOpen(_Guarded):
    def __call__(self, file, mode="r", *args, **kwargs):
        if not isinstance(file, int) and any(c in mode for c in "wax+"):
            check(self.op, file)
        return self.real(file, mode, *args, **kwargs)


def _wrap(module, name, op, nargs=1, kind=_Guarded):
    real = getattr(module, name)
    _real[(module.__name__, name)] = real
    setattr(module, name, kind(real, op, nargs))


def install():
    """Wrap the file-changing functions. Safe to call more than once."""
    if _real:
        return
    for name in ("remove", "unlink", "rmdir", "mkdir", "chmod", "utime"):
        _wrap(os, name, name)
    for name in ("rename", "replace"):
        _wrap(os, name, name, nargs=2)
    _wrap(shutil, "rmtree", "delete the folder")
    _wrap(shutil, "move", "move", nargs=2)
    _wrap(os, "open", "open for writing", kind=_GuardedOsOpen)
    guarded_open = _GuardedOpen(_real_open, "open for writing", 1)
    builtins.open = io.open = guarded_open
