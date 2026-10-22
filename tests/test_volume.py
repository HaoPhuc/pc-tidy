"""'Another drive' means another volume: subst letters, mount folders, network, FAT32, USB, locked drives."""

import os
from pathlib import Path

from pctidy import _win, context, volume
from support import contents, sparse_file, write

USB_DRIVER = r'''
import sys
from pctidy import volume

real = volume.volume_of
volume.volume_of = lambda path: (lambda v: v and v._replace(usb=True))(real(path))     # as if on USB

import move_off_c
sys.argv = ["move_off_c.py"] + sys.argv[1:]
sys.exit(move_off_c.main())
'''


def test_a_subst_letter_is_the_volume_of_the_folder_it_points_at(subst_drive):
    assert volume.volume_id(subst_drive.root) == volume.volume_id(subst_drive.target)
    assert volume.volume_id(subst_drive.root + "not\\there\\yet") == volume.volume_id(subst_drive.target)
    assert volume.subst_target(subst_drive.root) is not None
    assert volume.subst_target("C:") is None


def test_the_default_destination_is_a_volume_never_a_subst_letter(subst_drive, monkeypatch):
    monkeypatch.delenv("PCTIDY_SANDBOX_ROOT")       # only reads: look at every real volume
    letter = subst_drive.root[:2].upper()
    ctx = context.current()

    dest, _notes = volume.default_destination(ctx)

    assert all(not m.upper().startswith(letter) for v in volume.local_volumes() for m in v.mounts)
    assert dest is None or not dest.upper().startswith(letter)
    assert dest is None or volume.volume_id(dest) != volume.volume_id(ctx.system_drive)


def test_a_second_disk_mounted_in_a_folder_on_c_is_another_drive(run, profile, tmp_path, ntfs_volume, mounted_folder):
    lectures = profile / "Videos" / "Lectures"
    before = contents(lectures)
    dest = mounted_folder / tmp_path.name / "From-C"
    assert volume.volume_id(dest) == volume.volume_id(ntfs_volume.root) != volume.volume_id(profile)
    assert volume.destination_problem(dest) is None

    moved = run.python("move_off_c.py", "--path", lectures, "--to", dest, input="yes\n", extra_roots=[ntfs_volume.root])

    assert "Moved 1 of 1 items" in moved.stdout, moved.output
    assert not lectures.exists()
    assert contents(Path(ntfs_volume.root) / tmp_path.name / "From-C" / "Videos" / "Lectures") == before
    undone = run.python("move_off_c.py", "--undo", extra_roots=[ntfs_volume.root])
    assert "Moved back 1 of 1 items" in undone.stdout, undone.output
    assert contents(lectures) == before


def test_a_network_destination_is_refused(run, profile):
    lectures = profile / "Videos" / "Lectures"
    before = contents(lectures)

    result = run.python("move_off_c.py", "--path", lectures, "--to", "\\\\server\\share\\From-C", input="yes\n")

    assert result.returncode == 1, result.output
    assert "is on a network drive" in result.stdout
    assert contents(lectures) == before


def test_fat32_refuses_a_file_of_4_gb(run, profile, tmp_path, fat32_volume):
    huge = sparse_file(profile / "Videos" / "recording.mkv", 4 * 1024 ** 3)       # 4 GB that take no space
    dest = Path(fat32_volume.root) / tmp_path.name / "From-C"

    result = run.python("move_off_c.py", "--path", huge, "--to", dest, input="yes\n", extra_roots=[fat32_volume.root])

    assert "uses FAT32, which can't hold files that big" in result.stdout, result.output
    assert huge.stat().st_size == 4 * 1024 ** 3
    assert not dest.exists()


def test_a_usb_drive_asks_a_second_yes(run, profile, tmp_path, ntfs_volume):
    lectures = profile / "Videos" / "Lectures"
    before = contents(lectures)
    driver = write(tmp_path / "usb.py", USB_DRIVER)
    dest = Path(ntfs_volume.root) / tmp_path.name / "From-C"

    declined = run.python(driver, "--path", lectures, "--to", dest, input="yes\nno\n", extra_roots=[ntfs_volume.root])

    assert "is a USB drive. The moved files will only be there while it is plugged in." in declined.stdout, \
        declined.output
    assert "Cancelled, nothing moved." in declined.stdout
    assert contents(lectures) == before

    accepted = run.python(driver, "--path", lectures, "--to", dest, input="yes\nyes\n", extra_roots=[ntfs_volume.root])
    assert "Moved 1 of 1 items" in accepted.stdout, accepted.output


def test_a_locked_drive_is_skipped_and_named(monkeypatch):
    monkeypatch.delenv("PCTIDY_SANDBOX_ROOT")
    ctx = context.current()
    system = volume.Volume(volume.volume_id(ctx.system_drive), [ctx.system_drive], "NTFS", "", 10, 100, 3, False, None)
    locked = volume.Volume("\\\\?\\volume{00000000-0000-0000-0000-000000000001}\\", ["L:\\"], "", "", 0, 0, 3, False,
                           "L: can't be read (it may be locked with BitLocker, or not ready)")
    monkeypatch.setattr(volume, "local_volumes", lambda: [system, locked])

    dest, notes = volume.default_destination(ctx)

    assert dest is None
    assert notes == ["Skipped L: can't be read (it may be locked with BitLocker, or not ready)."]


def test_a_destination_that_cant_be_read_is_refused_by_name(tmp_path, monkeypatch):
    def locked(root):
        raise OSError(None, "This drive is locked by BitLocker Drive Encryption", None, 0x80310000 & 0xFFFF)
    monkeypatch.setattr(_win, "file_system", locked)
    problem = volume.destination_problem(tmp_path / "From-C")
    assert problem == f"{tmp_path.drive} can't be read (it may be locked with BitLocker). Unlock it or pick another drive."


def test_the_same_letter_is_the_same_drive(tmp_path):
    assert volume.destination_problem(tmp_path / "dest") == "the destination is on the same drive, that frees no space"
    assert volume.same_volume(tmp_path, os.environ["SystemDrive"] + "\\")
