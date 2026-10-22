"""Undo: entry by entry, never onto something else, keeping what it couldn't put back."""

from pathlib import Path

from support import contents, snapshot, write

SORT = ("--no-llm", "--apply")


def tidy(run, downloads, dest, *args, **kwargs):
    return run.python("tidy_folder.py", "--source", downloads, "--dest", dest, *args, **kwargs)


def test_an_original_place_that_is_taken_gets_the_item_beside_it(run, profile, tmp_path):
    downloads, dest = profile / "Downloads", tmp_path / "Sorted"
    tidy(run, downloads, dest, *SORT)
    write(downloads / "photo.jpg", b"a new photo with the same name")

    undone = tidy(run, downloads, dest, "--undo")

    assert undone.returncode == 0, undone.output
    assert (downloads / "photo.jpg").read_bytes() == b"a new photo with the same name"
    assert (downloads / "photo (restored).jpg").read_bytes() == b"jpeg bytes"
    assert "was taken, so it was put back as" in undone.stdout


def test_something_else_in_the_sorted_place_is_left_alone(run, profile, tmp_path):
    downloads, dest = profile / "Downloads", tmp_path / "Sorted"
    tidy(run, downloads, dest, *SORT)
    sorted_photo = dest / "Pictures" / "photo.jpg"
    sorted_photo.unlink()
    write(sorted_photo, b"a different file, put there later")

    undone = tidy(run, downloads, dest, "--undo")

    assert undone.returncode == 3, undone.output
    assert "something else is at" in undone.stdout
    assert sorted_photo.read_bytes() == b"a different file, put there later"
    assert not (downloads / "photo.jpg").exists()
    assert (downloads / "song.mp3").exists()         # everything else came back


def test_an_item_edited_since_the_move_is_only_moved_back_after_a_yes(run, profile, tmp_path):
    downloads, dest = profile / "Downloads", tmp_path / "Sorted"
    tidy(run, downloads, dest, *SORT)
    sorted_csv = dest / "Data" / "data.csv"
    with open(sorted_csv, "ab") as f:
        f.write(b"3,4\n")

    kept = tidy(run, downloads, dest, "--undo", input="no\n")
    assert "was changed after it was moved" in kept.stdout, kept.output
    assert sorted_csv.exists() and not (downloads / "data.csv").exists()

    again = tidy(run, downloads, dest, "--undo", input="yes\n")
    assert again.returncode == 0, again.output
    assert (downloads / "data.csv").read_bytes() == b"a,b\n1,2\n3,4\n"


def test_a_file_held_open_is_kept_and_a_second_undo_restores_it(run, profile, tmp_path):
    downloads, dest = profile / "Downloads", tmp_path / "Sorted"
    before = snapshot(downloads)
    tidy(run, downloads, dest, *SORT)

    with open(dest / "Music" / "song.mp3", "rb"):       # open in a player
        first = tidy(run, downloads, dest, "--undo")
    second = tidy(run, downloads, dest, "--undo")

    assert first.returncode == 3 and "in use" in first.stdout, first.output
    assert "Put back 1 items" in second.stdout, second.output
    assert snapshot(downloads) == before


def test_in_place_writes_nothing_into_the_folder_but_the_sorted_files(run, profile):
    desktop = profile / "Desktop"
    write(desktop / "screenshot.png", b"png")
    write(desktop / "budget.xlsx", b"xlsx")

    applied = run.python("tidy_folder.py", "--source", desktop, "--in-place", "--no-llm", "--apply")

    assert applied.returncode == 0, applied.output
    assert sorted(str(p.relative_to(desktop)) for p in desktop.rglob("*")) == [
        "Data", "Data\\budget.xlsx", "Pictures", "Pictures\\screenshot.png"]


def test_moves_to_another_volume_killed_after_the_third_are_all_undone(run, profile, tmp_path, ntfs_volume):
    items = [profile / "Videos" / "Lectures", write(profile / "Videos" / "clip.mp4", b"clip" * 500),
             write(profile / "Music" / "album" / "track1.mp3", b"track" * 300).parent,
             write(profile / "Documents" / "thesis.pdf", b"%PDF thesis")]
    before = {p: contents(p) if p.is_dir() else p.read_bytes() for p in items}
    dest = Path(ntfs_volume.root) / tmp_path.name / "From-C"
    args = [a for p in items for a in ("--path", p)]

    crashed = run.python("move_off_c.py", *args, "--to", dest, input="yes\n", extra_roots=[ntfs_volume.root],
                         env={"PCTIDY_TEST_CRASH_AFTER": "3"})
    assert crashed.returncode == 9, crashed.output
    assert not items[2].exists() and items[3].exists()

    undone = run.python("move_off_c.py", "--undo", extra_roots=[ntfs_volume.root])

    assert "was interrupted" in undone.stdout, undone.output       # the next start reports the run
    assert "Moved back 3 of 3 items" in undone.stdout, undone.output
    for p, data in before.items():
        assert (contents(p) if p.is_dir() else p.read_bytes()) == data, p
    assert not any((dest / "Videos").iterdir()) if (dest / "Videos").exists() else True
