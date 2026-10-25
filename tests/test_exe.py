"""pc-tidy.exe, the one-file build (packaging/pc-tidy.spec), run on the fake profile.

These run only when PCTIDY_EXE names a built exe. CI builds it and runs them; on your own PC:
    python -m PyInstaller --noconfirm --clean packaging\\pc-tidy.spec
    set PCTIDY_EXE=dist\\pc-tidy.exe
    python -m pytest tests\\test_exe.py

The exe is frozen Python, so the write guard (tests/guard) can't reach inside it. pc-tidy's own
sandbox (PCTIDY_SANDBOX_ROOT) still refuses anything outside the test's folders.
"""

import os
import shutil
import subprocess

import pytest

from support import age, wait_gone, write

EXE = os.environ.get("PCTIDY_EXE")
pytestmark = pytest.mark.skipif(not EXE, reason="PCTIDY_EXE isn't set: build pc-tidy.exe first (see this file)")
MB = 1024 ** 2


def run_exe(run, *args, input=None, exe=None, env=None):
    """Run a copy of pc-tidy.exe (by default one in the test's folder: at its end pc-tidy.exe offers to
    remove itself, and the build must survive the tests)."""
    if exe is None:
        exe = str(run.work / "pc-tidy.exe")
        if not os.path.exists(exe):
            shutil.copy2(EXE, exe)
    env = run.command_env(env)
    # highestAvailable would ask for a UAC prompt in a normal window of an administrator account;
    # RunAsInvoker starts it as this window is (CI's window is elevated anyway)
    env["__COMPAT_LAYER"] = "RunAsInvoker"
    result = subprocess.run([exe, *map(str, args)], cwd=str(run.work), env=env, input=input,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    result.output = result.stdout + result.stderr
    return result


def test_it_asks_for_the_highest_rights_the_account_has():
    """An administrator account gets one UAC prompt and runs as itself; a standard account isn't asked."""
    with open(EXE, "rb") as f:
        data = f.read()
    assert b'requestedExecutionLevel level="highestAvailable"' in data


def test_help(run):
    result = run_exe(run, "--help")
    assert result.returncode == 0, result.output
    assert "usage:" in result.stdout and "undo" in result.stdout


def test_selftest_needs_no_packages(run):
    result = run_exe(run, "selftest")
    assert result.returncode == 0, result.output
    assert "none needed: pc-tidy.exe saves its report as a web page" in result.stdout
    assert "Double-click pc-tidy.exe to start." in result.stdout


def test_it_starts_with_the_quick_sweep(run, profile, tmp_path):
    temp = profile / "AppData" / "Local" / "Temp"
    files = [write(temp / f"cache{i}.tmp", b"c" * MB, age_days=10) for i in range(3)]

    result = run_exe(run, input="yes\n\nk\n\n")      # yes, no more, keep pc-tidy, close the window

    assert result.returncode == 0, result.output
    assert "QUICK SWEEP: temp files older than 7 days" in result.stdout
    assert "Your Temp files older than 7 days: freed 3 MB" in result.stdout
    assert "FIXES FOR C:" not in result.stdout              # no scan unless you ask for more
    assert not any(f.exists() for f in files)
    assert "Press Enter to close." in result.stdout
    assert (tmp_path / "settings.ini").exists()


def test_more_scans_and_saves_a_web_page_without_opening_it(run, profile):
    # more; the fake profile then has no fix worth listing, so no "which fixes" question; close the menu,
    # keep pc-tidy, close the window
    result = run_exe(run, input="m\n\nk\n\n")

    assert result.returncode == 0, result.output
    assert "FIXES FOR C:, LOWEST RISK FIRST" in result.stdout
    reports = list((profile / "AppData" / "Local" / "pc-tidy" / "reports").glob("disk_report-*.html"))
    assert len(reports) == 1, result.output
    assert "Fixes for C:" in reports[0].read_text(encoding="utf-8")
    assert "Open it any time from the menu: Open the reports folder." in result.stdout
    assert "Sort your Downloads" not in result.stdout       # sorting and its AI aren't in the exe


def test_it_unpacks_into_the_data_folder_never_temp(run, profile):
    result = run_exe(run, "status")

    assert result.returncode == 0, result.output
    assert (profile / "AppData" / "Local" / "pc-tidy" / "app").is_dir()
    assert not [p for p in (profile / "AppData" / "Local" / "Temp").iterdir() if p.name.startswith("_MEI")]


def test_at_the_end_it_removes_itself_and_its_files_and_nothing_else(run, profile):
    """Enter at "Remove pc-tidy from this PC?": its own files go, then the exe once its window closes."""
    own = shutil.copy2(EXE, profile / "Downloads" / "pc-tidy.exe")
    data = profile / "AppData" / "Local" / "pc-tidy"
    temp = profile / "AppData" / "Local" / "Temp"
    write(temp / "cache.tmp", b"c" * MB, age_days=10)
    yours = write(data / "my notes.txt", "mine")

    log = run.work / "cleanup.log"
    result = run_exe(run, input="yes\n\n\n\n", exe=str(own),       # sweep, no more, remove, close the window
                     env={"PCTIDY_TEST_CLEANUP_LOG": str(log)})

    assert result.returncode == 0, result.output
    assert "pc-tidy.exe is removed when this window closes." in result.stdout
    gone = wait_gone(own, 120)
    assert gone, ("pc-tidy.exe was still there two minutes after it ended. What its cleanup did:\n"
                  + (log.read_text(encoding="utf-8") if log.exists() else "(no log: it never started)"))
    assert not (data / "runs").exists()                             # the record of the sweep, which only deleted
    assert yours.exists() and data.is_dir()                         # yours stays, and so does that folder
    assert (profile / "Downloads" / "setup.exe").exists()


def test_k_keeps_it(run, profile):
    own = shutil.copy2(EXE, profile / "Downloads" / "pc-tidy.exe")

    result = run_exe(run, input="\nk\n\n", exe=str(own))            # stop at the sweep, keep, close

    assert result.returncode == 0, result.output
    assert "pc-tidy stays. It asks again next time." in result.stdout
    assert os.path.exists(own)


def test_its_own_file_in_downloads_is_never_offered(run, profile):
    """Downloaded more than installers_min_age_days ago, pc-tidy.exe is an old .exe in Downloads."""
    own = shutil.copy2(EXE, profile / "Downloads" / "pc-tidy.exe")
    age(own, 30)

    result = run_exe(run, input="", exe=str(own))

    assert result.returncode == 0, result.output
    assert "Installers older than" not in result.stdout, result.output
    assert os.path.exists(own)
