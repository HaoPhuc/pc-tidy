"""Recovery: an interrupted run is settled from its journal and the disk, or waits for a decision."""

import os
import stat
import subprocess
from pathlib import Path

from pctidy import context, executor, journal
from pctidy.plan import Plan, fingerprint
from support import contents, write


def half_finished_move(src, dst, delete=1):
    """What a run that was killed while deleting the original of a folder it had copied to another
    volume leaves: the journal's intent and copied lines, a complete copy, a partly deleted source."""
    ctx = context.current()
    plan = Plan("move_off_c", ctx, info={"dest": str(dst.parent)})
    a = plan.add_move(src, dst)
    assert not a["same_volume"]
    jr = journal.Journal(ctx, {"run": plan.id, "command": "move_off_c", "created": plan.created})
    assert jr.start()
    jr.write(dict(a, t="intent"))
    dst.parent.mkdir(parents=True, exist_ok=True)
    executor._copy_item(str(src), str(dst))
    jr.write({"t": "copied", "n": a["n"], "fp": fingerprint(dst)})
    jr.close_file()
    for f in sorted(p for p in src.rglob("*") if p.is_file())[:delete]:
        os.chmod(f, stat.S_IWRITE)
        f.unlink()


def test_an_unattended_sort_leaves_a_half_finished_move_alone(run, profile, tmp_path, ntfs_volume):
    lectures, downloads, sorted_dir = profile / "Videos" / "Lectures", profile / "Downloads", tmp_path / "Sorted"
    before = contents(lectures)
    dst = Path(ntfs_volume.root) / tmp_path.name / "From-C" / "Videos" / "Lectures"
    half_finished_move(lectures, dst)
    left = contents(lectures)

    weekly = run.python("tidy_folder.py", "--source", downloads, "--dest", sorted_dir, "--no-llm", "--apply",
                        stdin=subprocess.DEVNULL, extra_roots=[ntfs_volume.root], timeout=120)

    assert weekly.returncode == 3, weekly.output
    assert "An earlier move is waiting for your decision" in weekly.stdout
    assert "Moved 9 items" in weekly.stdout                      # everything unrelated was done
    assert contents(lectures) == left and contents(dst) == before    # the half-move is untouched
    notices = journal.notices(context.current())
    assert any("waiting for your decision" in n["message"] for n in notices)

    later = run.python("pc_tidy.py", "recover", input="\n", extra_roots=[ntfs_volume.root])
    assert later.returncode == 3, later.output
    assert "1) finish the move" in later.stdout and "2) put it back" in later.stdout
    assert contents(lectures) == left

    back = run.python("pc_tidy.py", "recover", input="2\n", extra_roots=[ntfs_volume.root])
    assert back.returncode == 0, back.output
    assert contents(lectures) == before
    assert not dst.exists()

    status = run.python("pc_tidy.py", "status", "--dismiss", extra_roots=[ntfs_volume.root])
    assert "wait for your decision" not in status.stdout
    assert "Dismissed." in status.stdout


def test_a_half_finished_move_can_be_finished(run, profile, tmp_path, ntfs_volume):
    lectures = profile / "Videos" / "Lectures"
    before = contents(lectures)
    dst = Path(ntfs_volume.root) / tmp_path.name / "From-C" / "Videos" / "Lectures"
    half_finished_move(lectures, dst, delete=2)

    done = run.python("pc_tidy.py", "recover", input="1\n", extra_roots=[ntfs_volume.root])

    assert done.returncode == 0, done.output
    assert not lectures.exists()
    assert contents(dst) == before
    undone = run.python("move_off_c.py", "--undo", extra_roots=[ntfs_volume.root])     # and it can be undone
    assert "Moved back 1 of 1 items" in undone.stdout, undone.output
    assert contents(lectures) == before


def test_move_off_c_asks_about_a_half_finished_move_before_anything_else(run, profile, tmp_path, ntfs_volume):
    lectures = profile / "Videos" / "Lectures"
    dst = Path(ntfs_volume.root) / tmp_path.name / "From-C" / "Videos" / "Lectures"
    half_finished_move(lectures, dst)

    result = run.python("move_off_c.py", "--undo", input="", extra_roots=[ntfs_volume.root])

    assert "2) put it back" in result.stdout, result.output
    assert dst.exists()


def test_a_partial_copy_whose_source_is_intact_is_removed_without_a_question(run, profile, tmp_path, ntfs_volume):
    lectures = profile / "Videos" / "Lectures"
    before = contents(lectures)
    dst = Path(ntfs_volume.root) / tmp_path.name / "From-C" / "Videos" / "Lectures"
    ctx = context.current()
    plan = Plan("move_off_c", ctx, info={"dest": str(dst.parent)})
    a = plan.add_move(lectures, dst)
    jr = journal.Journal(ctx, {"run": plan.id, "command": "move_off_c", "created": plan.created})
    assert jr.start()
    jr.write(dict(a, t="intent"))
    jr.close_file()
    partial = Path(str(dst) + executor.PARTIAL)
    write(partial / "lecture1.mp4", b"video")          # killed halfway through the copy

    result = run.python("tidy_folder.py", "--source", profile / "Downloads", "--dest", tmp_path / "Sorted",
                        "--no-llm", "--apply", stdin=subprocess.DEVNULL, extra_roots=[ntfs_volume.root])

    assert result.returncode == 0, result.output
    assert "the copy hadn't finished; it was removed" in result.stdout
    assert not partial.exists()
    assert contents(lectures) == before


def test_a_rename_that_finished_before_the_crash_is_recorded_as_done(run, profile, tmp_path):
    downloads, dest = profile / "Downloads", tmp_path / "Sorted"
    crashed = run.python("tidy_folder.py", "--source", downloads, "--dest", dest, "--no-llm", "--apply",
                         env={"PCTIDY_TEST_CRASH_AFTER": "1"})
    assert crashed.returncode == 9, crashed.output

    result = run.python("pc_tidy.py", "recover")

    assert result.returncode == 0, result.output
    assert "the move had finished" in result.stdout
    again = run.python("pc_tidy.py", "recover")
    assert "Nothing was interrupted" in again.stdout
