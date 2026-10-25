r"""One way to fail, for every pc-tidy command: say what happened, why, and the one next step.

    if __name__ == "__main__":
        sys.exit(errors.run(main, "disk_report.py"))

The details (the traceback) go to logs\pc-tidy.log in the data folder (%LOCALAPPDATA%\pc-tidy; 1 MB,
with 5 older ones kept), and to the screen only with --debug. If the log can't be written either,
one line says so: the handler itself never fails. Ctrl+C stops a command with a short line too.
"""

import os
import sys
import time
import traceback

from . import _win, context

LOG_BYTES = 1024 ** 2
LOG_KEEP = 5
DEBUG_FLAG = "--debug"


def run(main, command):
    """main's exit code, or 1 after a message a person can act on."""
    debug = DEBUG_FLAG in sys.argv
    if debug:
        sys.argv.remove(DEBUG_FLAG)
    if context.FROZEN:
        command = context.EXE_NAME
    try:
        _test_hook()
        return main()
    except KeyboardInterrupt:
        print(f"\n{command} stopped (Ctrl+C). What it did is recorded: run {context.command('recover')} to check.")
        return 1
    except Exception as e:      # SystemExit (argparse, --help) passes through untouched
        print(f"\n{command} stopped: {explain(e)}")
        where = log(command, e)
        if debug:
            traceback.print_exc()
        elif where:
            print(f"The details are in {where}. To see them here, run it again with {DEBUG_FLAG}.")
        else:
            print(f"(The details couldn't be saved in pc-tidy's log either. Run it again with {DEBUG_FLAG} "
                  "to see them.)")
        return 1


def explain(e):
    """What happened and the next step, in a sentence or two."""
    if isinstance(e, ImportError):
        name = getattr(e, "name", None) or str(e)
        if context.FROZEN:
            return f"a part it needs is missing from pc-tidy.exe ({name}). Download pc-tidy.exe again."
        return (f"a part it needs isn't installed ({name}). Double-click setup.cmd in the pc-tidy folder, "
                "or run: pip install -r requirements.txt")
    if isinstance(e, OSError):
        where = f" ({e.filename})" if getattr(e, "filename", None) else ""
        if _win.disk_full(e):
            return (f"the disk is full. Free some space first: {context.command('').strip()} lists the low-risk "
                    "fixes at the top, which come back on their own.")
        if _win.in_use(e):
            return f"a file is open in another program{where}. Close that program, then run this again."
        if isinstance(e, PermissionError):
            return (f"Windows didn't allow it{where}. If the file is open in another program, close it; if it is "
                    "a Windows folder, run this in PowerShell opened as administrator.")
        if isinstance(e, FileNotFoundError):
            return f"something it needs isn't there{where}."
        return f"{e.strerror or e}{where}."
    return (f"something went wrong that pc-tidy didn't expect ({type(e).__name__}: {e}). That is a mistake "
            "in pc-tidy, not something you did.")


def log(command, e):
    """Add the traceback to the log, keeping LOG_KEEP older logs of LOG_BYTES each. Its path, or None
    if it couldn't be written."""
    try:
        folder = context.current().folder("logs")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "pc-tidy.log")
        if os.path.exists(path) and os.path.getsize(path) > LOG_BYTES:
            for i in range(LOG_KEEP - 1, 0, -1):
                if os.path.exists(f"{path}.{i}"):
                    os.replace(f"{path}.{i}", f"{path}.{i + 1}")
            os.replace(path, f"{path}.1")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')}  {command} {' '.join(sys.argv[1:])}\n")
            f.write("".join(traceback.format_exception(type(e), e, e.__traceback__)))
        return path
    except Exception:
        return None


def _test_hook():
    """PCTIDY_TEST_FAIL=crash or =missing: the tests' stand-in for a bug or a missing package."""
    what = context.current().test_value("PCTIDY_TEST_FAIL")
    if what == "crash":
        raise RuntimeError("a test failure")
    if what == "missing":
        raise ModuleNotFoundError("No module named 'reportlab'", name="reportlab")
