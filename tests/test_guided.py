"""The guided flow (python pc_tidy.py, or pc-tidy.cmd), driven through its standard input.

It scans the fake profile (in the sandbox it never scans this PC), shows the ranked fixes, and does
only what you pick, after the dry run and a typed yes.
"""

import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from pctidy import guided
from support import ROOT, write

MB = 1024 ** 2


def blob(path, mb, age_days=1.0):
    return write(path, b"\0" * int(mb * MB), age_days=age_days)


def guided_run(run, answers, **kwargs):
    return run.python("pc_tidy.py", "--no-llm", "--no-open", input=answers, **kwargs)


def temp_files(profile, mb=3):
    """Your Temp folder with mb of files: the only fix the fake profile then has."""
    temp = profile / "AppData" / "Local" / "Temp"
    return [blob(temp / f"cache{i}.tmp", mb / 3) for i in range(3)]


def test_no_answer_changes_nothing(run, profile):
    files = temp_files(profile)
    result = guided_run(run, "")         # the input ends at once: that always means no

    assert result.returncode == 0, result.output
    assert "FIXES FOR C:, LOWEST RISK FIRST" in result.stdout
    assert "Nothing was changed." in result.stdout
    assert all(f.exists() for f in files)


def test_no_after_the_dry_run_changes_nothing(run, profile):
    files = temp_files(profile)
    result = guided_run(run, "1\nno\n\n")

    assert "DRY RUN: this is exactly what will happen. Nothing has changed yet." in result.stdout
    assert "Cancelled, nothing changed." in result.stdout
    assert all(f.exists() for f in files)


def test_yes_deletes_exactly_the_list(run, profile):
    files = temp_files(profile)
    precious = profile / "Precious" / "keep.txt"        # what the junction in Temp points at

    result = guided_run(run, "1\nyes\n\n")

    assert result.returncode == 0, result.output
    assert "1  Your Temp files: delete 5 items, 3 MB" in result.stdout     # 3 files, tmp1.tmp, the junction
    assert "Your Temp files: freed 3 MB (the report said 3 MB)" in result.stdout
    assert not any(f.exists() for f in files)
    assert precious.read_bytes() == b"k" * 1000          # the link went, never what it points at
    assert "What was done is recorded in" in result.stdout
    reports = list((profile / "AppData" / "Local" / "pc-tidy" / "reports").glob("disk_report-*.pdf"))
    assert len(reports) == 1                            # in the data folder, never the current folder


def test_answers_it_doesnt_understand_are_asked_again(run, profile):
    files = temp_files(profile)
    result = guided_run(run, "abc\n1,99\n\n")

    assert "I didn't understand 'abc'." in result.stdout
    assert "There is no fix 99: the list goes from 1 to 1." in result.stdout
    assert "Nothing was changed." in result.stdout and "DRY RUN" not in result.stdout
    assert all(f.exists() for f in files)


def test_L_picks_every_low_risk_fix(run, profile):
    temp_files(profile)
    blob(profile / "AppData" / "Local" / "pip" / "cache" / "w.whl", 2)
    result = guided_run(run, "L\nno\n\n")
    dry = result.stdout[result.stdout.index("DRY RUN"):]
    assert "Your Temp files: delete" in dry and "pip and uv caches: delete 1 item, 2 MB" in dry


def test_a_fix_done_by_hand_says_how(run, profile):
    cache = profile / "AppData" / "Local" / "Google" / "Chrome" / "User Data" / "Default" / "Cache"
    blob(cache / "data_1", 2)
    result = guided_run(run, "1\n\n")

    assert "Chrome cache   (by hand)" in result.stdout
    assert ("1  Chrome cache: do it by hand. In Chrome: Settings > Delete browsing data > Cached images and "
            "files.") in result.stdout
    assert "DRY RUN" not in result.stdout
    assert (cache / "data_1").exists()


def test_node_modules_is_deleted_whole(run, profile, tmp_path):
    project = profile / "Documents" / "robot"
    blob(project / "node_modules" / "pkg" / "index.js", 2)
    write(project / "package.json", "{}")
    write(tmp_path / "settings.ini", "[report]\ncleanup_min_gb = 0.001\n")

    result = guided_run(run, "1\nyes\n\n")

    assert result.returncode == 0, result.output
    assert "Node.js packages of robot: delete 1 item, 2 MB" in result.stdout
    assert not (project / "node_modules").exists() and (project / "package.json").exists()


def answer_when(started, prompt, text):
    started.wait_for(prompt)
    started.send(text)


def test_a_file_added_or_changed_after_the_dry_run_survives(run, profile):
    files = temp_files(profile)
    started = run.start("pc_tidy.py", "--no-llm", "--no-open")
    answer_when(started, "Type the numbers of the fixes you want", "1\n")
    started.wait_for("Type yes to do all of this")
    added = write(profile / "AppData" / "Local" / "Temp" / "new.tmp", b"downloading")
    time.sleep(0.05)
    files[0].write_bytes(b"changed")          # a listed file, written to since the dry run
    answer_when(started, "", "yes\n")
    answer_when(started, "Type a number, or press Enter to close", "\n")
    code = started.finish()

    assert code == 3, started.output         # something was skipped
    assert added.exists() and files[0].read_bytes() == b"changed"
    assert not files[1].exists() and not files[2].exists()
    assert "1 skipped: changed since the dry run" in started.output


def test_sort_downloads_from_the_menu(run, profile):
    temp_files(profile)                     # one fix, so the first Enter answers "which fixes"
    result = guided_run(run, "\n2\nyes\n\n")

    assert result.returncode == 0, result.output
    assert "Type yes below to move them" in result.stdout
    assert (profile / "Sorted" / "Pictures" / "photo.jpg").exists()
    assert not (profile / "Downloads" / "photo.jpg").exists()


def test_help_from_the_menu(run, profile):
    temp_files(profile)
    result = guided_run(run, "\n7\n9\n\n")
    assert "Nothing is deleted until you have seen the exact list (the dry run) and typed yes." in result.stdout
    assert "Type a number from 1 to 7, or press Enter to close." in result.stdout


def test_moves_go_through_move_off_c_so_its_undo_puts_them_back(run, profile, tmp_path, ntfs_volume):
    lectures = profile / "Videos" / "Lectures"
    blob(lectures / "lecture2.mp4", 3)
    write(tmp_path / "settings.ini", "[move]\nmin_gb = 0.001\n")
    roots = [ntfs_volume.root]

    result = guided_run(run, "1\nyes\n\n", extra_roots=roots)

    assert result.returncode == 0, result.output
    assert f"Move {lectures} to {ntfs_volume.root}From-C" in result.stdout
    assert "Undo the moves with: python move_off_c.py --undo" in result.stdout
    assert not lectures.exists()
    undone = run.python("move_off_c.py", "--undo", extra_roots=roots)
    assert undone.returncode == 0, undone.output
    assert (lectures / "lecture2.mp4").exists()


# ---------- in this process ----------
ROWS = [{"id": "temp-user", "risk": "Low"}, {"id": "caches", "risk": "Low"},
        {"id": "installers", "risk": "Medium"}, {"id": "windows-temp", "risk": "Low"}]
AVAIL = {"temp-user": None, "caches": None, "installers": None, "windows-temp": "needs an administrator window"}


@pytest.mark.parametrize("text, picked", [
    ("1", [0]), ("3,1", [0, 2]), ("1-3", [0, 1, 2]), ("3-2", [1, 2]), ("2 2 2", [1]),
    ("L", [0, 1]),                     # the low-risk ones pc-tidy can do here
    ("l,3", [0, 1, 2]),
])
def test_parse_pick(text, picked):
    assert guided.parse_pick(text, ROWS, AVAIL) == (picked, None)


@pytest.mark.parametrize("text, problem", [
    ("0", "There is no fix 0: the list goes from 1 to 4."),
    ("2-9", "There is no fix 9: the list goes from 1 to 4."),
    ("x", "I didn't understand 'x'."),
    ("1-2-3", "I didn't understand '1-2-3'."),
])
def test_parse_pick_problems(text, problem):
    picked, message = guided.parse_pick(text, ROWS, AVAIL)
    assert picked is None and message.startswith(problem)


def high_risk_dry_run():
    action = {"op": "empty_recycle_bin", "fix": "recycle-bin"}
    return SimpleNamespace(rows=[{"id": "recycle-bin", "risk": "High", "title": "Empty the Recycle Bin"},
                                 {"id": "temp-user", "risk": "Low", "title": "Your Temp files"}],
                           by_fix={"recycle-bin": [action], "temp-user": [{"op": "delete", "fix": "temp-user"}]},
                           deletes=SimpleNamespace(actions=[action, {"op": "delete", "fix": "temp-user"}]))


@pytest.mark.parametrize("answer, kept", [("yes", True), ("", False), (None, False), ("y", False)])
def test_a_high_risk_fix_asks_again(answer, kept, capsys):
    dry = high_risk_dry_run()
    asked = []
    guided.confirm_high(dry, ask=lambda prompt: asked.append(prompt) or answer)

    assert len(asked) == 1 and "is high risk: it can't be undone. Type yes again" in asked[0]
    assert any(a["fix"] == "recycle-bin" for a in dry.deletes.actions) == kept
    assert any(a["fix"] == "temp-user" for a in dry.deletes.actions)       # the rest is untouched


def test_pc_tidy_cmd_works_from_any_folder(tmp_path):
    """A double-click, or Run as administrator (which starts in System32): it finds its own folder."""
    env = dict(os.environ, PCTIDY_PYTHON=sys.executable)
    result = subprocess.run(["cmd", "/c", str(ROOT / "pc-tidy.cmd"), "--help"], cwd=os.environ["SystemRoot"],
                            input="\r\n", capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "usage: pc_tidy.py" in result.stdout
