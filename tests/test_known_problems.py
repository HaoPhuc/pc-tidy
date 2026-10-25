"""Problems in the code before 2.0, numbers from the pc-tidy 2.0 plan.

Each test asserts the safe behaviour. A problem not fixed yet is pinned as a strict xfail: when the
milestone that fixes it lands, its test starts passing, strict=True turns that XPASS into a failure,
and the xfail mark is removed so the test guards the fix from then on. raises=AssertionError keeps an
unrelated crash from counting as the known failure.

Fixed in M2 (the safety core): 3, 4, 5, 13, 15. Fixed in M4 (cleanup.ps1 on settings.ini): 1, 14.
Fixed in M9 (cleanup.ps1 on the executor), numbers from docs/PLAN-3.md: 17, 18, 19.
Fixed in M10 (safer fixes): 20, 21, 22, 23, 26; 24 and 25 are in test_fixes.py.
Nothing is pinned any more; known() stays for the next problem found.
"""

import os
import time
from pathlib import Path

import pytest

from pctidy import _win, context, executor, safety, settings
from pctidy.plan import Plan, fingerprint
from support import age, age_tree, snapshot, write
from test_cleanup_ps1 import cleanup_settings

MB = 1024 ** 2


def known(number, why):
    return pytest.mark.xfail(strict=True, raises=AssertionError, reason=f"problem {number}: {why}")


def require(ok, output):
    """A broken test setup must fail loudly, not pass as the known problem (pytest.fail isn't an AssertionError)."""
    if not ok:
        pytest.fail(f"the test setup didn't work:\n{output}")


def test_a_wildcard_under_a_drive_root_is_refused(subst_drive):
    """Problem 1, fixed in M4: X:\\* is refused even when X: is a subst letter, which resolves to a
    folder. (Test-Protected in the old cleanup.ps1 only refused a path that is exactly a protected folder.)"""
    problem = safety.why_refused(subst_drive.root + "*", "delete", context.current())
    assert problem and "a wildcard directly under a drive root could empty the drive" in problem, problem


def test_extra_paths_refuse_a_wildcard_under_a_drive_root(run, tmp_path, subst_drive):
    """Problem 1, fixed in M4: cleanup.ps1 stops at the line, before it does anything."""
    write(subst_drive.target / "everything.txt", b"all of drive X:")
    cleanup_settings(tmp_path, extra_paths=[subst_drive.root + "*"])

    result = run.python("pc_tidy.py", "cleanup", input="yes\nyes\n")      # what .\cleanup.ps1 runs

    assert result.returncode == settings.EXIT, result.output
    assert (f"settings.ini line 12 in [cleanup]: extra_paths_to_delete '{subst_drive.root}*' is refused: "
            "a wildcard directly under a drive root could empty the drive.") in result.output
    assert "DRY RUN" not in result.output
    assert (subst_drive.target / "everything.txt").exists()


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


def test_temp_step_refuses_a_temp_variable_that_points_at_a_drive_root(run, tmp_path, subst_drive):
    """Problem 14, fixed in M4: a TEMP that isn't a temp folder is left alone (the old cleanup.ps1
    emptied "$env:TEMP\\*" whatever it pointed at). A real run, not only the dry run, since M9."""
    write(subst_drive.target / "not-temp.txt", b"someone's file")
    cleanup_settings(tmp_path, switches_on={"temp_files"})

    result = run.python("pc_tidy.py", "cleanup", input="yes\n", env={"TEMP": subst_drive.root, "TMP": subst_drive.root})

    assert result.returncode == 0, result.output
    assert (f"Your TEMP variable points at {subst_drive.root}, which isn't a temp folder. The Temp fix was skipped."
            in result.stdout), result.output
    assert (subst_drive.target / "not-temp.txt").exists()


def test_brackets_in_an_extra_path_are_part_of_its_name(run, tmp_path):
    """Problem 17, fixed in M9: D:\\old[1] is that one folder. The old cleanup.ps1 checked it as one
    folder, then gave it to Remove-Item -Path, which read [1] as a wildcard: it deleted old1 instead
    and left old[1]."""
    meant = write(tmp_path / "backups" / "old[1]" / "a.txt", b"delete me").parent
    other = write(tmp_path / "backups" / "old1" / "b.txt", b"keep me").parent
    cleanup_settings(tmp_path, extra_paths=[meant])

    dry = run.python("pc_tidy.py", "cleanup", "--dry-run")
    result = run.python("pc_tidy.py", "cleanup", input="yes\nyes\n")

    assert f"Delete {meant}: delete 1 item" in dry.stdout, dry.output
    assert str(other) not in dry.stdout
    assert result.returncode == 0, result.output
    assert not meant.exists()
    assert (other / "b.txt").read_bytes() == b"keep me"


# Problem 18 (.\cleanup.ps1 asked for yes after category names only, then listed folders again and
# deleted what it found) is fixed in M9: test_cleanup_ps1.py runs it for real, including a file that
# appears after the dry run.


def test_the_recycle_bin_is_off_by_default(tmp_path):
    """Problem 19, fixed in M9: the template switched the Recycle Bin on, so .\\cleanup.ps1 emptied it
    after one yes, while the guided flow called it high risk and asked twice."""
    cfg = settings.load(context.current(), create=True)
    assert cfg.value("cleanup", "recycle_bin") is False
    assert "\nrecycle_bin = no\n" in (tmp_path / "settings.ini").read_text(encoding="utf-8")


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


# ---------- M10, numbers from docs/PLAN-3.md ----------

def test_the_temp_fix_leaves_anything_from_the_last_week(run, profile, tmp_path):
    """Problem 20, fixed in M10: a file opened from a ZIP or an email lives in Temp while you work on
    it, and so do a running installer's files. Only items nothing was created or changed in for
    temp_min_age_days (7) go."""
    temp = profile / "AppData" / "Local" / "Temp"
    old = write(temp / "old.tmp", b"o", age_days=30)
    fresh = write(temp / "report.docx", b"edited today", age_days=1)
    unpacked = write(temp / "from-a-zip.xlsx", b"x", age_days=0)       # unpacked now, with the ZIP's old date
    t = time.time() - 300 * 86400
    os.utime(unpacked, (t, t))
    old_folder = age_tree(write(temp / "old-setup" / "data.cab", b"c").parent, 30)
    busy_folder = age_tree(write(temp / "running-setup" / "data.cab", b"c").parent, 30)
    write(busy_folder / "progress.log", b"still writing", age_days=0)
    age(busy_folder, 30)            # the folder itself looks old: what is in it counts
    cleanup_settings(tmp_path, switches_on={"temp_files"})

    result = run.python("pc_tidy.py", "cleanup", input="yes\n")

    assert result.returncode == 0, result.output
    assert "Your Temp files older than 7 days: delete" in result.stdout
    assert "(3 newer items stay)" in result.stdout, result.output
    assert not old.exists() and not old_folder.exists() and not (temp / "tmp1.tmp").exists()
    assert fresh.exists() and unpacked.exists() and (busy_folder / "data.cab").exists()


def test_the_copy_that_stays_is_never_deleted_by_another_fix(run, profile):
    """Problem 21, fixed in M10: the copy of a duplicate that stays (the older one, in Downloads) is
    also an old installer. Picked together, the two fixes deleted both copies."""
    stays = write(profile / "Downloads" / "tool.exe", b"T" * (2 * MB), age_days=40)
    extra = write(profile / "Desktop" / "tool.exe", b"T" * (2 * MB), age_days=20)

    result = run.python("pc_tidy.py", "--no-llm", "--no-open", input="1,2\nyes\nyes\n\n")

    assert result.returncode == 0, result.output
    assert f"Left out {stays}: it is the copy that stays of {extra}" in result.stdout, result.output
    assert stays.exists() and not extra.exists()
    assert not (profile / "Downloads" / "old-setup.msi").exists()        # the rest of the installers fix ran


def test_an_extra_copy_stays_if_the_kept_copy_changed_but_kept_its_size_and_time(tmp_path):
    """Problem 22, fixed in M10: a tool that edits a file and keeps its modified time (tag editors can)
    leaves size, time and file ID alone. The bytes are compared right before deleting the extra copy."""
    keep = write(tmp_path / "Documents" / "song.mp3", b"A" * 4096, age_days=40)
    extra = write(tmp_path / "Downloads" / "song.mp3", b"A" * 4096)
    plan = Plan("test", context.current())
    action = plan.add_delete(extra)
    action["keep"], action["keep_fp"] = str(keep), fingerprint(keep)
    st = os.stat(keep)
    keep.write_bytes(b"B" * 4096)                           # new tags, same size
    os.utime(keep, ns=(st.st_atime_ns, st.st_mtime_ns))     # and the time put back
    assert fingerprint(keep) == action["keep_fp"]

    result = executor.execute(plan)

    assert [o.reason for o in result.skipped] == [
        "the copy that would stay isn't identical to this one any more, so this one stays too"]
    assert extra.exists()


def test_run_as_another_account_only_the_windows_folders_are_cleaned(run, profile, tmp_path):
    """Problem 23, fixed in M10: Run as administrator with a parent's password runs pc-tidy as the
    parent, whose Temp and Downloads aren't the person's at the screen."""
    cleanup_settings(tmp_path, switches_on={"temp_files", "installers"})
    someone = "SOMEPC\\someone-else"

    result = run.python("pc_tidy.py", "cleanup", input="yes\n", env={"PCTIDY_TEST_SESSION_USER": someone})

    assert result.returncode == 0, result.output
    assert f"but {someone} is signed in, so only the Windows folders are cleaned" in result.stdout
    assert f"Left out Your Temp files older than 7 days: it belongs to {someone}" in result.stdout
    assert (profile / "AppData" / "Local" / "Temp" / "tmp1.tmp").exists()
    assert (profile / "Downloads" / "old-setup.msi").exists()


def test_the_guided_flow_says_whose_folders_they_are(run, profile):
    temp = profile / "AppData" / "Local" / "Temp"
    files = [write(temp / f"cache{i}.tmp", b"c" * MB, age_days=10) for i in range(3)]
    someone = "SOMEPC\\someone-else"

    result = run.python("pc_tidy.py", "--no-llm", "--no-open", input="1\n\n", env={"PCTIDY_TEST_SESSION_USER": someone})

    assert f"but {someone} is signed in" in result.stdout, result.output
    assert f"(belongs to {someone}, and this window runs as" in result.stdout
    assert "DRY RUN" not in result.stdout
    assert all(f.exists() for f in files)


def test_the_accounts_can_be_read():
    me = _win.process_user()
    assert me and "\\" in me
    session = _win.session_user()          # None in a session nobody is signed in to (a service)
    assert session is None or "\\" in session


def test_app_folders_are_never_offered_as_moves(run, profile, tmp_path):
    """Problem 26, fixed in M10: Outlook's mail files, game saves and other apps' folders in Documents
    break the app when moved by hand."""
    outlook = write(profile / "Documents" / "Outlook Files" / "me.pst", b"mail").parent
    archive = write(profile / "Documents" / "old mail" / "2019.pst", b"mail").parent
    saves = write(profile / "Documents" / "My Games" / "save1.dat", b"save").parent

    results = [run.python("move_off_c.py", "--path", p, "--to", tmp_path / "dest") for p in (outlook, archive, saves)]

    assert "Outlook Files is where an app keeps its own files" in results[0].stdout, results[0].output
    assert "it holds Outlook's mail files (.pst or .ost)" in results[1].stdout, results[1].output
    assert "My Games is where an app keeps its own files" in results[2].stdout, results[2].output
    assert outlook.exists() and archive.exists() and saves.exists()


def test_app_folders_are_left_out_of_the_candidates_with_a_tip(profile):
    import move_off_c
    import scanner
    write(profile / "Documents" / "Outlook Files" / "me.pst", b"m" * 3000)
    write(profile / "Documents" / "Thesis" / "data.bin", b"d" * 3000)
    scan = scanner.Scan([str(profile)])

    paths = [c["path"] for c in move_off_c.move_candidates(scan, str(profile), min_bytes=2000)]
    tips = move_off_c.move_tips(scan, str(profile), min_bytes=2000)

    assert str(profile / "Documents" / "Thesis") in paths
    assert not any("Outlook Files" in p for p in paths)
    assert any("Outlook Files" in t and "isn't offered" in t for t in tips)


def test_a_move_says_it_leaves_the_backup(run, profile, tmp_path):
    import move_off_c
    result = run.python("move_off_c.py", "--path", profile / "Videos" / "Lectures", "--to", tmp_path / "dest",
                        input="no\n")
    assert move_off_c.BACKUP_NOTE in result.stdout, result.output


def test_a_file_open_in_another_program_is_found_before_copying(tmp_path):
    folder = tmp_path / "Lectures"
    write(folder / "a.mp4", b"a")
    held = write(folder / "notes" / "open.docx", b"open in Word")

    assert executor._first_open_elsewhere(str(folder)) is None
    with open(held, "rb"):
        assert executor._first_open_elsewhere(str(folder)) == os.path.join("notes", "open.docx")
        assert _win.open_elsewhere(str(held))
    assert not _win.open_elsewhere(str(held))


def test_a_move_with_a_file_open_copies_nothing(tmp_path, profile, ntfs_volume):
    lectures = profile / "Videos" / "Lectures"
    dest = Path(ntfs_volume.root) / tmp_path.name / "From-C" / "Lectures"
    plan = Plan("test", context.current())
    plan.add_move(lectures, dest)

    with open(lectures / "lecture1.mp4", "rb"):           # playing in a video player
        result = executor.execute(plan)

    (outcome,) = result.outcomes
    assert outcome.status == "skipped" and "lecture1.mp4 is open in another program, so nothing was copied" \
        in outcome.reason
    assert not dest.exists() and not Path(str(dest) + executor.PARTIAL).exists()
    assert (lectures / "lecture1.mp4").exists()
