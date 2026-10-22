"""tidy_folder.py as it behaves today: preview, --apply, --undo, --in-place, and old undo logs."""

import json
from pathlib import Path, PureWindowsPath

from support import FIXTURES, snapshot, write

# where each file in the fake Downloads goes, by the extension rules (no LLM)
EXPECTED = {
    "data.csv": "Data/data.csv",
    "old-notes.zip": "Archive/Archives/old-notes.zip",     # older than 60 days
    "old-setup.msi": "To-Delete/old-setup.msi",           # installers are never archived
    "photo.jpg": "Pictures/photo.jpg",
    "readonly.zip": "Archives/readonly.zip",
    "setup.exe": "To-Delete/setup.exe",
    "song copy.mp3": "Music/song copy.mp3",
    "song.mp3": "Music/song.mp3",
    "\U0001F389 party.png": "Pictures/\U0001F389 party.png",
}
# no rule and no LLM, still downloading, Windows' own file, hidden, a subfolder
LEFT_ALONE = ["report [1].pdf", "fresh.jpg", "desktop.ini", "hidden.png", "deep-folder"]


def test_preview_changes_nothing(run, profile, tmp_path):
    downloads, sorted_dir = profile / "Downloads", tmp_path / "Sorted"
    before = snapshot(downloads)

    result = run.python("tidy_folder.py", "--source", downloads, "--dest", sorted_dir, "--no-llm")

    assert result.returncode == 0, result.output
    assert snapshot(downloads) == before
    assert not sorted_dir.exists()
    assert f"Preview only, {len(EXPECTED)} items" in result.stdout
    for name, target in EXPECTED.items():
        assert f"{name}  ->  {PureWindowsPath(target)}" in result.stdout


def test_apply_moves_files_by_their_extension(run, profile, tmp_path):
    downloads, sorted_dir = profile / "Downloads", tmp_path / "Sorted"
    data = {name: (downloads / name).read_bytes() for name in EXPECTED}

    result = run.python("tidy_folder.py", "--source", downloads, "--dest", sorted_dir, "--no-llm", "--apply")

    assert result.returncode == 0, result.output
    assert f"Moved {len(EXPECTED)} items" in result.stdout
    for name, target in EXPECTED.items():
        assert not (downloads / name).exists()
        assert (sorted_dir / target).read_bytes() == data[name]
    assert sorted(p.name for p in downloads.iterdir()) == sorted(LEFT_ALONE)
    logs = list((sorted_dir / "_logs").glob("run_*.json"))
    assert len(logs) == 1
    assert len(json.loads(logs[0].read_text(encoding="utf-8"))) == len(EXPECTED)


def test_undo_puts_back_the_exact_tree(run, profile, tmp_path):
    downloads, sorted_dir = profile / "Downloads", tmp_path / "Sorted"
    before = snapshot(downloads)

    applied = run.python("tidy_folder.py", "--source", downloads, "--dest", sorted_dir, "--no-llm", "--apply")
    undone = run.python("tidy_folder.py", "--source", downloads, "--dest", sorted_dir, "--undo")

    assert applied.returncode == 0 and undone.returncode == 0, applied.output + undone.output
    assert f"Put back {len(EXPECTED)} items" in undone.stdout
    assert snapshot(downloads) == before          # names, sizes and modified times
    assert [p.name for p in sorted_dir.iterdir()] == ["_logs"]     # the emptied category folders are gone
    assert len(list((sorted_dir / "_logs").glob("run_*.undone"))) == 1


def test_in_place_sorts_inside_the_folder_and_undoes(run, profile):
    desktop = profile / "Desktop"
    write(desktop / "screenshot.png", b"png")
    write(desktop / "budget.xlsx", b"xlsx")
    before = snapshot(desktop)

    applied = run.python("tidy_folder.py", "--source", desktop, "--in-place", "--no-llm", "--apply")
    again = run.python("tidy_folder.py", "--source", desktop, "--in-place", "--no-llm")
    undone = run.python("tidy_folder.py", "--source", desktop, "--in-place", "--undo")

    assert (applied.returncode, again.returncode, undone.returncode) == (0, 0, 0), applied.output
    assert "Moved 2 items" in applied.stdout
    assert "Nothing to sort." in again.stdout         # its own folders aren't sorted again
    after = snapshot(desktop)
    assert {k: v for k, v in after.items() if not k.startswith("_logs")} == before


def test_undo_with_nothing_to_undo(run, tmp_path):
    result = run.python("tidy_folder.py", "--source", tmp_path, "--dest", tmp_path / "Sorted", "--undo")
    assert result.returncode == 0
    assert "Nothing to undo." in result.stdout


def test_a_source_that_is_not_a_folder(run, tmp_path):
    result = run.python("tidy_folder.py", "--source", tmp_path / "missing", "--no-llm")
    assert result.returncode == 0
    assert "is not a folder." in result.stdout


def test_an_undo_log_in_todays_format_still_undoes(run, tmp_path):
    """The format later versions must keep reading: Sorted/_logs/run_<time>.json."""
    source, dest = tmp_path / "Downloads", tmp_path / "Sorted"
    source.mkdir()
    template = (FIXTURES / "legacy_logs" / "tidy" / "_logs" / "run_20260101_120000.json").read_text(encoding="utf-8")
    log = (template.replace("{SOURCE}", json.dumps(str(source))[1:-1])
                   .replace("{DEST}", json.dumps(str(dest))[1:-1]))
    entries = json.loads(log)
    for e in entries:
        write(Path(e["to"]), b"sorted earlier")
    write(dest / "_logs" / "run_20260101_120000.json", log)

    result = run.python("tidy_folder.py", "--source", source, "--dest", dest, "--undo")

    assert result.returncode == 0, result.output
    for e in entries:
        assert Path(e["from"]).read_bytes() == b"sorted earlier"
        assert not Path(e["to"]).exists()
