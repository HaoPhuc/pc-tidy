"""Problems in today's code, pinned as strict xfails (numbers from the pc-tidy 2.0 plan).

Each test asserts the safe behaviour, so it fails today. When the milestone that fixes a problem lands,
its test starts passing, strict=True turns that XPASS into a failure, and the xfail mark is removed so
the test guards the fix from then on. raises=AssertionError keeps an unrelated crash from counting as
the known failure.
"""

import pytest

from support import snapshot, write
from test_cleanup_ps1 import cleanup_copy, needs_admin, section


def known(number, why):
    return pytest.mark.xfail(strict=True, raises=AssertionError, reason=f"problem {number}: {why}")


def require(ok, output):
    """A broken test setup must fail loudly, not pass as the known problem (pytest.fail isn't an AssertionError)."""
    if not ok:
        pytest.fail(f"the test setup didn't work:\n{output}")


@needs_admin
@known(1, "Test-Protected only refuses a path that is exactly a protected folder")
def test_extra_paths_refuse_a_wildcard_under_a_drive_root(run, tmp_path, subst_drive):
    write(subst_drive.target / "everything.txt", b"all of drive X:")
    script = cleanup_copy(tmp_path, extra_paths=[subst_drive.root + "*"])

    result = run.powershell(script, "-DryRun")      # a dry run: nothing is deleted either way

    require(result.returncode == 0, result.output)
    assert "refused" in section(result.output, f"Delete {subst_drive.root}*")


@known(3, "shutil.move overwrites a file that appears at the target after the plan")
def test_tidy_never_overwrites_a_file_that_appears_after_the_plan(tmp_path):
    import tidy_folder

    source, dest = tmp_path / "Downloads", tmp_path / "Sorted"
    write(source / "photo.jpg", b"the new photo")
    ((_, target),) = tidy_folder.plan_moves(source, dest, use_llm=False)
    write(target, b"a photo that was already there")      # e.g. a second run, at the same moment

    tidy_folder.apply_moves([(source / "photo.jpg", target)], dest)

    assert target.read_bytes() == b"a photo that was already there"


CRASH_AFTER_3_MOVES = r'''
import os, shutil, sys
import tidy_folder

real_move, moved = shutil.move, [0]

def move_then_crash(src, dst, *args, **kwargs):
    result = real_move(src, dst, *args, **kwargs)
    moved[0] += 1
    if moved[0] == 3:
        os._exit(9)      # the window was closed, or the power went
    return result

shutil.move = move_then_crash
sys.argv = ["tidy_folder.py"] + sys.argv[1:]
tidy_folder.main()
'''


@known(4, "the undo log is written once, at the end of the run")
def test_tidy_undo_works_after_a_crash(run, profile, tmp_path):
    downloads, dest = profile / "Downloads", tmp_path / "Sorted"
    before = snapshot(downloads)
    driver = write(tmp_path / "crash.py", CRASH_AFTER_3_MOVES)

    crashed = run.python(driver, "--source", downloads, "--dest", dest, "--no-llm", "--apply")
    require(crashed.returncode == 9, crashed.output)
    run.python("tidy_folder.py", "--source", downloads, "--dest", dest, "--undo")

    assert snapshot(downloads) == before


@known(5, "--undo marks the log done even when an item couldn't be put back")
def test_tidy_undo_keeps_items_it_could_not_put_back(run, profile, tmp_path):
    downloads, dest = profile / "Downloads", tmp_path / "Sorted"
    run.python("tidy_folder.py", "--source", downloads, "--dest", dest, "--no-llm", "--apply")
    sorted_photo, aside = dest / "Pictures" / "photo.jpg", tmp_path / "aside.jpg"
    sorted_photo.rename(aside)              # missing while the first undo runs

    run.python("tidy_folder.py", "--source", downloads, "--dest", dest, "--undo")
    sorted_photo.parent.mkdir(parents=True, exist_ok=True)
    aside.rename(sorted_photo)              # back again
    run.python("tidy_folder.py", "--source", downloads, "--dest", dest, "--undo")

    assert (downloads / "photo.jpg").exists(), "a second undo didn't retry the item the first one missed"


FAKE_LLM = r'''
import sys
import llm

llm.available = lambda *args, **kwargs: True
llm.ask_json = lambda prompt, schema, *args, **kwargs: {"category": "Other", "new_name": ""}

import tidy_folder
sys.argv = ["tidy_folder.py"] + sys.argv[1:]
tidy_folder.main()
'''


@known(13, "nothing limits --source, so --folders can move a whole profile")
def test_tidy_refuses_to_sort_a_profile_folder(run, profile, tmp_path):
    driver = write(tmp_path / "fake_llm.py", FAKE_LLM)

    run.python(driver, "--source", profile, "--folders", "--apply")

    for name in ("Desktop", "Documents", "Downloads", "Videos"):
        assert (profile / name).is_dir(), f"{name} was moved out of the profile"


@needs_admin
@known(14, "the Temp step uses $env:TEMP as it is, even when it points at a drive root")
def test_temp_step_refuses_a_temp_variable_that_points_at_a_drive_root(run, tmp_path, subst_drive):
    write(subst_drive.target / "not-temp.txt", b"someone's file")
    script = cleanup_copy(tmp_path, switches_on={"TempFiles"})

    result = run.powershell(script, "-DryRun", extra_env={"TEMP": subst_drive.root, "TMP": subst_drive.root})

    require(result.returncode == 0, result.output)
    step = section(result.output, "Temp files")
    assert "isn't a temp folder" in step or "refused" in step, step


@known(15, "_same_drive compares drive letters, so a subst letter on C: counts as another drive")
def test_move_to_a_subst_letter_on_the_same_volume_is_refused(run, profile, subst_drive):
    lectures = profile / "Videos" / "Lectures"

    run.python("move_off_c.py", "--path", lectures, "--to", subst_drive.root + "From-C", input="yes\n",
               extra_roots=[subst_drive.root])

    assert lectures.exists(), "the originals were deleted, but the copy is on the same volume, so C: gained nothing"
