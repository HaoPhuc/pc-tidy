"""Problems in the code before 2.0, numbers from the pc-tidy 2.0 plan.

Each test asserts the safe behaviour. A problem not fixed yet is pinned as a strict xfail: when the
milestone that fixes it lands, its test starts passing, strict=True turns that XPASS into a failure,
and the xfail mark is removed so the test guards the fix from then on. raises=AssertionError keeps an
unrelated crash from counting as the known failure.

Fixed in M2 (the safety core): 3, 4, 5, 13, 15. Still pinned: 1 and 14 (cleanup.ps1, M4).
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


def test_tidy_never_overwrites_a_file_that_appears_after_the_plan(tmp_path):
    """Problem 3, fixed in M2: moves never overwrite, and a target that appears is skipped."""
    import tidy_folder

    source, dest = tmp_path / "Downloads", tmp_path / "Sorted"
    write(source / "photo.jpg", b"the new photo")
    ((_, target),) = tidy_folder.plan_moves(source, dest, use_llm=False)
    write(target, b"a photo that was already there")      # e.g. a second run, at the same moment

    code = tidy_folder.apply_moves([(source / "photo.jpg", target)], dest)

    assert target.read_bytes() == b"a photo that was already there"
    assert (source / "photo.jpg").read_bytes() == b"the new photo"      # both files survive
    assert code == 3


def test_tidy_undo_works_after_a_crash(run, profile, tmp_path):
    """Problem 4, fixed in M2: each move is in the journal before it happens, so a run killed partway
    (PCTIDY_TEST_CRASH_AFTER ends the process with os._exit after the third move) can still be undone."""
    downloads, dest = profile / "Downloads", tmp_path / "Sorted"
    before = snapshot(downloads)

    crashed = run.python("tidy_folder.py", "--source", downloads, "--dest", dest, "--no-llm", "--apply",
                         env={"PCTIDY_TEST_CRASH_AFTER": "3"})
    require(crashed.returncode == 9, crashed.output)
    undone = run.python("tidy_folder.py", "--source", downloads, "--dest", dest, "--undo")

    assert "was interrupted" in undone.stdout, undone.output       # the next start reports the run
    assert "Put back 3 items" in undone.stdout, undone.output
    assert snapshot(downloads) == before


def test_tidy_undo_keeps_items_it_could_not_put_back(run, profile, tmp_path):
    """Problem 5, fixed in M2: undo keeps the entries it couldn't put back, and a later undo retries them."""
    downloads, dest = profile / "Downloads", tmp_path / "Sorted"
    run.python("tidy_folder.py", "--source", downloads, "--dest", dest, "--no-llm", "--apply")
    sorted_photo, aside = dest / "Pictures" / "photo.jpg", tmp_path / "aside.jpg"
    sorted_photo.rename(aside)              # missing while the first undo runs

    first = run.python("tidy_folder.py", "--source", downloads, "--dest", dest, "--undo")
    sorted_photo.parent.mkdir(parents=True, exist_ok=True)
    aside.rename(sorted_photo)              # back again
    second = run.python("tidy_folder.py", "--source", downloads, "--dest", dest, "--undo")

    assert first.returncode == 3 and "couldn't be put back" in first.stdout, first.output
    assert "Put back 1 items" in second.stdout, second.output
    assert (downloads / "photo.jpg").exists(), "a second undo didn't retry the item the first one missed"


FAKE_LLM = r'''
import sys
import llm

llm.available = lambda *args, **kwargs: True
llm.ask_json = lambda prompt, schema, *args, **kwargs: {"category": "Other", "new_name": ""}

import tidy_folder
sys.argv = ["tidy_folder.py"] + sys.argv[1:]
sys.exit(tidy_folder.main())
'''


def test_tidy_refuses_to_sort_a_profile_folder(run, profile, tmp_path):
    """Problem 13, fixed in M2: a profile can't be the folder to sort."""
    driver = write(tmp_path / "fake_llm.py", FAKE_LLM)

    result = run.python(driver, "--source", profile, "--folders", "--apply")

    assert result.returncode == 1, result.output
    assert "is a user profile folder" in result.stdout, result.output
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


def test_move_to_a_subst_letter_on_the_same_volume_is_refused(run, profile, subst_drive):
    """Problem 15, fixed in M2: another drive means another volume, not another letter."""
    lectures = profile / "Videos" / "Lectures"
    before = snapshot(lectures)

    result = run.python("move_off_c.py", "--path", lectures, "--to", subst_drive.root + "From-C", input="yes\n",
                        extra_roots=[subst_drive.root])

    letter = subst_drive.root[:2]
    assert f"{letter} is a folder on C: (a subst drive), so moving there frees nothing on C:." in result.stdout, \
        result.output
    assert snapshot(lectures) == before
    assert not (subst_drive.target / "From-C").exists()
    assert lectures.exists(), "the originals were deleted, but the copy is on the same volume, so C: gained nothing"
