"""A full C:, the case pc-tidy exists for: the system drive and the data folder sit on the 64 MB test
volume, filled to 0 bytes free (see the full_c fixture). Deletes still run after their yes; moves run
only where their journal can be written, which may be the destination drive."""

from pathlib import Path

from pctidy import journal, volume
from support import contents, write

TEMP_FIX = r'''
import sys
from pctidy import context, executor, ui
from pctidy.plan import Plan

ctx = context.current()
plan = Plan("temp-fix", ctx)
plan.add_delete_contents(ctx.temp)
print("Your Temp files:")
for a in plan.actions:
    print(f"  {a['path']}  ({a['bytes']} bytes)")
print("saved copy:", plan.save_copy())
if not ui.ask_yes():
    print("Cancelled, nothing deleted.")
    sys.exit(0)
result = executor.execute(plan, ctx)
print(f"deleted {len(result.done)}, skipped {len(result.skipped)}, journal on disk: {result.journal_on_disk}")
'''


def runs(home):
    return list((home / "AppData" / "Local" / "pc-tidy" / "runs").glob("*.jsonl"))


def test_temp_files_are_deleted_at_0_bytes_free_after_their_dry_run_and_a_yes(full_c, tmp_path):
    temp = full_c.home / "AppData" / "Local" / "Temp"
    assert volume.free_bytes(full_c.root) < 64 * 1024, "the test volume isn't full"
    driver = write(tmp_path / "temp_fix.py", TEMP_FIX)

    no = full_c.run.python(driver, input="no\n")
    assert "Cancelled, nothing deleted." in no.stdout, no.output
    assert len(list(temp.iterdir())) == 20

    yes = full_c.run.python(driver, input="yes\n")

    assert yes.returncode == 0, yes.output
    for i in range(20):
        assert f"cache{i:02}.tmp" in yes.stdout          # the whole list was on screen
    assert "saved copy: None" in yes.stdout              # no room for the readable copy, which stops nothing
    assert "deleted 20, skipped 0, journal on disk: True" in yes.stdout
    assert list(temp.iterdir()) == []
    lines = [r for path in runs(full_c.home) for r in journal.read_lines(path)]
    assert len([r for r in lines if r["t"] == "done"]) == 20     # the record reached the disk once there was room


def test_a_move_at_0_bytes_free_is_refused_with_the_make_room_message(full_c, tmp_path, ntfs_volume):
    lectures = full_c.home / "Videos" / "Lectures"
    before = contents(lectures)
    dest = Path(ntfs_volume.root) / tmp_path.name / "From-C"      # not the default folder

    result = full_c.run.python("move_off_c.py", "--path", lectures, "--to", dest, input="yes\n",
                               extra_roots=[ntfs_volume.root])

    drive = full_c.root.drive
    assert (f"{drive} is too full to record the move safely. Clear some low-risk items first "
            "(they're at the top of the list).") in result.stdout, result.output
    assert contents(lectures) == before
    assert not (dest / "Videos").exists()


def test_a_move_to_the_default_folder_keeps_its_journal_on_that_drive(full_c, ntfs_volume, default_folder):
    lectures = full_c.home / "Videos" / "Lectures"
    before = contents(lectures)

    result = full_c.run.python("move_off_c.py", "--path", lectures, "--to", default_folder, input="yes\n",
                               extra_roots=[ntfs_volume.root])

    assert "Moved 1 of 1 items" in result.stdout, result.output
    assert contents(default_folder / "Videos" / "Lectures") == before
    (on_volume,) = (default_folder / "_pc-tidy").glob("*.jsonl")
    lines = journal.read_lines(on_volume)
    assert [r["t"] for r in lines][:2] == ["run", "intent"]
    assert lines[-1]["t"] == "continued"        # once the move had made room, the journal went back to C:
    moved_on = Path(lines[-1]["path"])
    assert moved_on.parent == full_c.home / "AppData" / "Local" / "pc-tidy" / "runs"
    assert [r["t"] for r in journal.read_lines(moved_on)][-1] == "end"


def test_killed_after_the_first_item_it_recovers_from_the_journal_on_the_other_drive(full_c, ntfs_volume,
                                                                                    default_folder):
    lectures, clip = full_c.home / "Videos" / "Lectures", full_c.home / "Videos" / "clip.mp4"
    before = contents(lectures)

    crashed = full_c.run.python("move_off_c.py", "--path", lectures, "--path", clip, "--to", default_folder,
                                input="yes\n", extra_roots=[ntfs_volume.root], env={"PCTIDY_TEST_CRASH_AFTER": "1"})

    assert crashed.returncode == 9, crashed.output
    assert not lectures.exists() and clip.exists()
    assert contents(default_folder / "Videos" / "Lectures") == before

    recovered = full_c.run.python("pc_tidy.py", "recover", extra_roots=[ntfs_volume.root])

    assert recovered.returncode == 0, recovered.output
    assert "was interrupted" in recovered.stdout and "the move had finished" in recovered.stdout
    status = full_c.run.python("pc_tidy.py", "status", extra_roots=[ntfs_volume.root])
    assert "wait for your decision" not in status.stdout, status.output
