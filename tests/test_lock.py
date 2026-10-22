"""The lock: one run at a time per user, never held while anything waits for input."""

import subprocess
import time

import pytest

from pctidy import context, journal, lock
from support import is_admin, snapshot, start_unelevated, wait_handle, write

HOLDER = r'''
import os, sys, time
from pctidy import _win, context
from pctidy.lock import Lock

held = Lock(context.current(), "holder.py").acquire()
print(f"holding elevated={_win.is_elevated()}", flush=True)
stop = sys.argv[1] if len(sys.argv) > 1 else None
if stop:
    while not os.path.exists(stop):
        time.sleep(0.05)
else:
    sys.stdin.read()
held.release()
print("released", flush=True)
'''

STUB_UNINSTALLER = r'''
import sys
from pctidy import context, executor
from pctidy.plan import Plan

waits = "import sys; print('uninstaller waiting', flush=True); sys.stdin.readline(); print('uninstaller done')"
executor.TOOLS["winget_uninstall"] = executor.Tool("winget", lambda a: [sys.executable, "-c", waits], True, None, None)
plan = Plan("uninstall", context.current())
plan.add_tool("winget_uninstall", name="Some App")
result = executor.execute(plan)
print("result", [o.status for o in result.outcomes], flush=True)
'''

QUICK = {"PCTIDY_TEST_LOCK_WAIT": "2"}


def weekly_sort(run, profile, tmp_path, **kwargs):
    return run.python("tidy_folder.py", "--source", profile / "Downloads", "--dest", tmp_path / "Sorted",
                      "--no-llm", "--apply", stdin=subprocess.DEVNULL, **kwargs)


def test_a_busy_lock_makes_the_weekly_sort_wait_then_exit_4(run, profile, tmp_path):
    holder = run.start(write(tmp_path / "holder.py", HOLDER))
    holder.wait_for("holding")
    before = snapshot(profile / "Downloads")

    result = weekly_sort(run, profile, tmp_path, env=QUICK)

    assert holder.finish() == 0
    assert result.returncode == lock.BUSY_EXIT, result.output
    assert "Waiting for another pc-tidy run (holder.py, started " in result.stdout
    assert lock.BUSY_MESSAGE in result.stdout
    assert snapshot(profile / "Downloads") == before and not (tmp_path / "Sorted").exists()
    assert any("another pc-tidy run was busy" in n["message"] for n in journal.notices(context.current()))


def test_a_run_that_died_holding_the_lock_is_noticed_and_recovery_runs_first(run, profile, tmp_path):
    holder = run.start(write(tmp_path / "holder.py", HOLDER))
    holder.wait_for("holding")
    holder.kill()       # the window was closed

    result = weekly_sort(run, profile, tmp_path)

    assert result.returncode == 0, result.output
    assert lock.DIED_MESSAGE in result.stdout
    assert "Moved 9 items" in result.stdout


def test_move_off_c_waiting_at_its_prompt_holds_no_lock(run, profile, tmp_path):
    picker = run.start("move_off_c.py", "--min-gb", "0", "--to", tmp_path / "dest")
    picker.wait_for("Type the numbers")

    result = weekly_sort(run, profile, tmp_path)

    assert result.returncode == 0, result.output
    assert "Moved 9 items" in result.stdout
    assert picker.finish() == 0          # the end of input is "no": nothing moved
    assert "Nothing moved." in picker.output


def test_an_uninstaller_waiting_for_input_holds_no_lock(run, profile, tmp_path):
    uninstall = run.start(write(tmp_path / "uninstall.py", STUB_UNINSTALLER))
    uninstall.wait_for("uninstaller waiting")

    result = weekly_sort(run, profile, tmp_path)

    assert result.returncode == 0, result.output
    uninstall.send("\n")
    assert uninstall.finish() == 0
    assert "uninstaller done" in uninstall.output and "result ['done']" in uninstall.output


@pytest.mark.skipif(not is_admin(), reason="needs an elevated window to compare with a normal one")
def test_an_elevated_and_a_normal_window_of_the_same_user_exclude_each_other(run, profile, tmp_path):
    holder_py, stop = write(tmp_path / "holder.py", HOLDER), tmp_path / "stop"
    sort_args = ["tidy_folder.py", ["--source", profile / "Downloads", "--dest", tmp_path / "Sorted", "--no-llm",
                                    "--apply"]]

    # the elevated window holds the lock, a normal one runs the weekly sort
    holder = run.start(holder_py, stop)
    holder.wait_for("holding elevated=True")
    out = tmp_path / "normal-sort.txt"
    code = wait_handle(start_unelevated(run._argv(*sort_args), run.command_env(QUICK), run.work, out))
    write(stop, b"")
    assert holder.finish() == 0
    assert code == lock.BUSY_EXIT, out.read_text(encoding="utf-8", errors="replace")

    # and the other way round
    stop.unlink()
    out = tmp_path / "normal-holder.txt"
    handle = start_unelevated(run._argv(holder_py, [stop]), run.command_env(), run.work, out)
    try:
        for _ in range(600):
            if "holding" in out.read_text(encoding="utf-8", errors="replace"):
                break
            time.sleep(0.1)
        assert "holding elevated=False" in out.read_text(encoding="utf-8", errors="replace")
        result = weekly_sort(run, profile, tmp_path, env=QUICK)
    finally:
        write(stop, b"")
        assert wait_handle(handle) == 0
    assert result.returncode == lock.BUSY_EXIT, result.output
