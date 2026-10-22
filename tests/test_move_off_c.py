"""move_off_c.py as it behaves today. Moves between drives run on real test volumes (see conftest.py)."""

import json
from pathlib import Path

from support import FIXTURES, contents, write


def test_refuses_a_whole_windows_user_folder(run, profile, tmp_path):
    result = run.python("move_off_c.py", "--path", profile / "Videos", "--to", tmp_path / "dest")
    assert result.returncode == 0, result.output
    assert "it's a Windows user folder" in result.stdout
    assert (profile / "Videos" / "Lectures" / "lecture1.mp4").exists()


def test_refuses_app_data(run, profile, tmp_path):
    result = run.python("move_off_c.py", "--path", profile / "AppData" / "Local" / "Temp", "--to", tmp_path / "dest")
    assert "it holds app data or synced files" in result.stdout


def test_refuses_anything_outside_the_profile(run, tmp_path):
    elsewhere = write(tmp_path / "elsewhere" / "big.bin", b"b").parent
    result = run.python("move_off_c.py", "--path", elsewhere, "--to", tmp_path / "dest")
    assert "only folders and files inside your user profile can be moved" in result.stdout
    assert elsewhere.exists()


def test_cancel_moves_nothing(run, profile, tmp_path):
    lectures = profile / "Videos" / "Lectures"
    before = contents(lectures)
    result = run.python("move_off_c.py", "--path", lectures, "--to", tmp_path / "dest", input="no\n")
    assert "Cancelled, nothing moved." in result.stdout
    assert contents(lectures) == before


def test_refuses_a_destination_with_the_same_drive_letter(run, profile, tmp_path):
    lectures = profile / "Videos" / "Lectures"
    before = contents(lectures)
    result = run.python("move_off_c.py", "--path", lectures, "--to", tmp_path / "dest", input="yes\n")
    assert "destination is on the same drive" in result.stdout
    assert contents(lectures) == before


def test_undo_with_nothing_to_undo(run):
    result = run.python("move_off_c.py", "--undo")
    assert result.returncode == 0
    assert "Nothing to undo." in result.stdout


def test_moves_to_another_volume_and_back(run, profile, tmp_path, ntfs_volume):
    lectures = profile / "Videos" / "Lectures"
    dest = Path(ntfs_volume.root) / tmp_path.name / "From-C"
    before = contents(lectures)

    moved = run.python("move_off_c.py", "--path", lectures, "--to", dest, input="yes\n",
                       extra_roots=[ntfs_volume.root])

    assert moved.returncode == 0, moved.output
    assert "Moved 1 of 1 items" in moved.stdout
    assert not lectures.exists()
    assert contents(dest / "Videos" / "Lectures") == before

    undone = run.python("move_off_c.py", "--undo", extra_roots=[ntfs_volume.root])

    assert undone.returncode == 0, undone.output
    assert "Moved back 1 of 1 items" in undone.stdout
    assert contents(lectures) == before
    assert not (dest / "Videos" / "Lectures").exists()


def test_refuses_a_destination_without_room(run, profile, tmp_path, fat32_volume):
    """It wants 200 MB free beyond the data itself, which a 64 MB volume never has."""
    lectures = profile / "Videos" / "Lectures"
    dest = Path(fat32_volume.root) / tmp_path.name / "From-C"
    before = contents(lectures)

    result = run.python("move_off_c.py", "--path", lectures, "--to", dest, input="yes\n",
                        extra_roots=[fat32_volume.root])

    assert "not enough free space on the destination drive" in result.stdout, result.output
    assert contents(lectures) == before
    assert not (dest / "Videos" / "Lectures").exists()


def test_a_move_log_in_todays_format_still_undoes(run, profile, tmp_path, ntfs_volume):
    """The format later versions must keep reading: ~/.pc-tidy/move_logs/moves_<time>.json."""
    dest = Path(ntfs_volume.root) / tmp_path.name / "From-C"
    template = (FIXTURES / "legacy_logs" / "move_off_c" / "moves_20260101_120000.json").read_text(encoding="utf-8")
    log = (template.replace("{HOME}", json.dumps(str(profile))[1:-1])
                   .replace("{DEST}", json.dumps(str(dest))[1:-1]))
    (entry,) = json.loads(log)
    write(Path(entry["to"]) / "beach.mp4", b"b" * 2000)
    write(Path(entry["to"]) / "sunset.jpg", b"s" * 1000)
    before = contents(entry["to"])
    write(profile / ".pc-tidy" / "move_logs" / "moves_20260101_120000.json", log)

    result = run.python("move_off_c.py", "--undo", extra_roots=[ntfs_volume.root])

    assert result.returncode == 0, result.output
    assert contents(entry["from"]) == before
    assert not Path(entry["to"]).exists()
    assert (profile / ".pc-tidy" / "move_logs" / "moves_20260101_120000.undone").exists()
