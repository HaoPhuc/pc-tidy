"""Removing pc-tidy from this PC (pctidy/remove.py): only its own files go, the record of moves to
another drive stays there, and nothing goes while a run waits to be settled or another run works.

These run in this process, with PCTIDY_TEST_EXE standing in for pc-tidy.exe. tests/test_exe.py
removes a real pc-tidy.exe.
"""

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from pctidy import context, executor, lock, remove
from pctidy.plan import Plan
from support import write

MB = 1024 ** 2


@pytest.fixture
def exe(profile, monkeypatch):
    """A pc-tidy.exe downloaded into the fake Downloads, next to the fake profile's own setup.exe."""
    path = write(profile / "Downloads" / "pc-tidy.exe", b"MZ pc-tidy" * 1000)
    monkeypatch.setenv("PCTIDY_TEST_EXE", str(path))
    return path


def data_folder(profile):
    """pc-tidy's own files in its data folder, and two things of yours that are there too."""
    data = profile / "AppData" / "Local" / "pc-tidy"
    own = [write(data / "reports" / "disk_report-2026-10-25-101010.html", "<html>"),
           write(data / "plans" / "20261025-101010-1-abcd-dry-run.txt", "list"),
           write(data / "logs" / "pc-tidy.log", "log"), write(data / "logs" / "pc-tidy.log.1", "old log"),
           write(data / "settings.ini", "[cleanup]\n"), write(data / "attention.jsonl", "{}\n"),
           write(data / "app" / "_MEI1234" / "python3.dll", "an earlier run's unpacked files")]
    yours = [write(data / "my notes.txt", "mine"), write(data / "backup" / "old.zip", "mine")]
    return data, own, yours


def delete_run(tmp_path):
    """A run that only deleted: its record goes too, since there is nothing to undo."""
    plan = Plan("pc-tidy", context.current())
    plan.add_delete(write(tmp_path / "profile" / "AppData" / "Local" / "Temp" / "x.tmp", b"x"))
    return executor.execute(plan)


def test_only_pc_tidys_own_files_go(tmp_path, profile, exe, capsys):
    data, own, yours = data_folder(profile)
    record = Path(delete_run(tmp_path).journal_path)

    leave = remove.offer(ask=lambda question: "")

    assert leave is not None
    assert not any(p.exists() for p in own) and not record.exists()
    assert all(p.exists() for p in yours) and data.is_dir()        # not empty, so it stays
    assert not (data / "app").exists() and not (data / "reports").exists()
    assert "2 item(s) in" in capsys.readouterr().out
    assert exe.exists()                                            # only once pc-tidy has ended

    proc = remove.remove_exe_after_exit(context.current(), wait_for=[])
    assert proc.wait(120) == 0
    assert not exe.exists()
    assert (profile / "Downloads" / "setup.exe").exists()          # never the folder it is in, nor its neighbours
    assert all(p.exists() for p in yours)


def test_the_data_folder_goes_when_nothing_else_is_in_it(profile, exe):
    data = profile / "AppData" / "Local" / "pc-tidy"
    write(data / "reports" / "disk_report-2026-10-25-101010.html", "<html>")

    remove.offer(ask=lambda question: "")
    proc = remove.remove_exe_after_exit(context.current(), wait_for=[])

    assert proc.wait(120) == 0
    assert not data.exists() and not exe.exists()


def test_a_different_file_where_pc_tidy_exe_was_stays(profile, exe):
    """The SHA-256 is taken when pc-tidy decides to go; whatever is at that path later stays."""
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(4)"])
    proc = remove.remove_exe_after_exit(context.current(), wait_for=[sleeper.pid])
    exe.write_bytes(b"MZ another program, downloaded since")

    assert proc.wait(120) == 0
    assert exe.read_bytes() == b"MZ another program, downloaded since"


def test_the_cleanup_needs_no_powershell_module():
    """Started from a PowerShell 7 window, Windows PowerShell can inherit a module path where module
    cmdlets fail to load: on CI, Get-FileHash returned nothing and the exe stayed. .NET calls only."""
    script = remove.cleanup_script("C:\\x\\pc-tidy.exe", "AB", ["C:\\x\\app"], [1], log="C:\\x\\log")
    for cmdlet in ("Get-FileHash", "Remove-Item", "Test-Path", "Wait-Process", "Add-Content", "Get-Date",
                   "Start-Sleep", "Out-File"):
        assert cmdlet not in script, cmdlet


@pytest.mark.parametrize("answer", [None, "k", "K", "no"])
def test_anything_but_enter_or_yes_keeps_it(profile, exe, answer, capsys):
    data, own, yours = data_folder(profile)

    assert remove.offer(ask=lambda question: answer) is None

    assert all(p.exists() for p in own + yours) and exe.exists()
    assert "pc-tidy stays. It asks again next time." in capsys.readouterr().out


def test_an_interrupted_run_keeps_everything(run, tmp_path, profile, exe, capsys):
    data, own, _yours = data_folder(profile)
    crashed = run.python("tidy_folder.py", "--source", profile / "Downloads", "--dest", tmp_path / "Sorted", "--no-llm",
                         "--apply", env={"PCTIDY_TEST_CRASH_AFTER": "1"})      # killed after its first move
    assert crashed.returncode == 9, crashed.output

    assert remove.offer(ask=lambda question: "") is None

    assert "a run was interrupted and waits to be settled" in capsys.readouterr().out
    assert all(p.exists() for p in own) and exe.exists()


def test_while_another_run_works_everything_stays(profile, exe, capsys):
    data, own, _yours = data_folder(profile)
    holding, done = threading.Event(), threading.Event()

    def other_run():
        with lock.Lock(context.current(), "move_off_c.py"):
            holding.set()
            done.wait(30)
    t = threading.Thread(target=other_run)
    t.start()
    holding.wait(30)
    try:
        assert remove.offer(ask=lambda question: "") is None
    finally:
        done.set()
        t.join(30)

    assert "another pc-tidy run is working right now" in capsys.readouterr().out
    assert all(p.exists() for p in own)


def test_the_record_of_moves_stays_on_their_drive_and_a_new_pc_tidy_undoes_them(run, profile, exe, ntfs_volume,
                                                                                 default_folder):
    lectures = profile / "Videos" / "Lectures"
    moved = run.python("move_off_c.py", "--path", lectures, "--to", default_folder, input="yes\n",
                       extra_roots=[ntfs_volume.root])
    assert moved.returncode == 0, moved.output
    (record,) = (profile / "AppData" / "Local" / "pc-tidy" / "runs").glob("*.jsonl")

    leave = remove.offer(ask=lambda question: "")

    assert leave is not None and not record.exists()
    assert (default_folder / "_pc-tidy" / record.name).exists()
    note = (default_folder / remove.NOTE_NAME).read_text(encoding="utf-8")
    assert str(lectures) in note and "pc-tidy.exe undo" in note
    undone = run.python("move_off_c.py", "--undo", extra_roots=[ntfs_volume.root])      # a new pc-tidy
    assert undone.returncode == 0, undone.output
    assert (lectures / "lecture1.mp4").exists()
