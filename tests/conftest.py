import os
import subprocess
import sys
import time
from collections import namedtuple
from pathlib import Path

import pytest

from support import GUARD_DIR, Runner, fill_volume, free_letter, is_admin, make_fake_profile, remove_tree, write

sys.path.insert(0, str(GUARD_DIR))
import pctidy_guard  # noqa: E402

if os.name != "nt":
    pytest.exit("pc-tidy's tests run on Windows only", returncode=4)

pctidy_guard.install()


@pytest.fixture(autouse=True)
def write_guard(tmp_path):
    """While a test runs, this process may only change files inside its tmp_path (plus test volumes)."""
    pctidy_guard.violations.clear()
    pctidy_guard.set_roots([tmp_path])
    try:
        yield pctidy_guard
    finally:
        pctidy_guard.set_roots(None)
    if pctidy_guard.violations:
        pytest.fail("the test tried to change files outside its folders:\n" + "\n".join(pctidy_guard.violations))


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """pc-tidy code run inside this process sees a fake profile in tmp_path (tmp_path/profile, made by
    the `profile` fixture) and refuses anything outside tmp_path, as the commands do under Runner."""
    home = tmp_path / "profile"
    local = home / "AppData" / "Local"
    monkeypatch.setenv("PCTIDY_SANDBOX_ROOT", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("TEMP", str(local / "Temp"))
    monkeypatch.setenv("TMP", str(local / "Temp"))
    return tmp_path


@pytest.fixture
def profile(tmp_path):
    """A fake user profile: tmp_path/profile."""
    return make_fake_profile(tmp_path)


@pytest.fixture
def run(tmp_path, profile):
    """Runs pc-tidy's commands as the owner of the fake profile, guarded to tmp_path."""
    return Runner(profile, tmp_path, [tmp_path])


SubstDrive = namedtuple("SubstDrive", "root target")


@pytest.fixture
def subst_drive(tmp_path):
    """A drive letter made with subst, pointing at a folder in tmp_path: a second letter, same volume."""
    target = tmp_path / "subst-target"
    target.mkdir()
    letter = free_letter()
    made = subprocess.run(["subst", f"{letter}:", str(target)], capture_output=True, text=True)
    if made.returncode:
        pytest.skip(f"subst failed: {made.stdout}{made.stderr}")
    try:
        yield SubstDrive(f"{letter}:\\", target)
    finally:
        subprocess.run(["subst", f"{letter}:", "/D"], capture_output=True)


# ---------- real test volumes ----------
# A subst letter is only a folder on the same volume, so moves between drives are tested on real
# volumes: small VHDX files attached with diskpart. That needs admin rights. CI runs elevated and sets
# PCTIDY_REQUIRE_VOLUMES=1, which turns a skip into a failure so these tests can't silently stop running.

Volume = namedtuple("Volume", "root vhdx fs")


def _diskpart(folder, name, lines):
    script = folder / f"{name}.diskpart"
    script.write_text("\n".join(lines) + "\n", encoding="ascii")
    return subprocess.run(["diskpart", "/s", str(script)], capture_output=True, text=True, errors="replace")


def _detach(folder, vhdx):
    return _diskpart(folder, f"detach-{vhdx.stem}", [f'select vdisk file="{vhdx}"', "detach vdisk"])


@pytest.fixture(scope="session")
def test_volumes(tmp_path_factory):
    """{'ntfs': 512 MB NTFS volume, 'fat32': 64 MB FAT32 volume, 'fullc': 64 MB NTFS volume that stands
    in for a full C:}, each with its own drive letter."""
    if not is_admin():
        if os.environ.get("PCTIDY_REQUIRE_VOLUMES") == "1":
            pytest.fail("PCTIDY_REQUIRE_VOLUMES=1, but this process isn't elevated, "
                        "so the test volumes can't be created")
        pytest.skip("the test volumes need admin rights (diskpart)")
    folder = tmp_path_factory.mktemp("volumes")
    made, volumes = [], {}
    try:
        # 512 MB, not less: move_off_c.py wants 200 MB free on the destination beyond the data itself
        for name, size_mb, fs, extra in (("ntfs", 512, "ntfs", ""), ("fat32", 64, "fat32", " unit=512"),
                                         ("fullc", 64, "ntfs", "")):
            letter = free_letter()
            vhdx = folder / f"pctidy-test-{name}.vhdx"
            made.append(vhdx)
            result = _diskpart(folder, f"create-{name}", [
                f'create vdisk file="{vhdx}" maximum={size_mb} type=expandable',
                f'select vdisk file="{vhdx}"',
                "attach vdisk",
                "create partition primary",
                f"format fs={fs} quick label=PCTIDY{name.upper()}{extra}",
                f"assign letter={letter}",
            ])
            if result.returncode:
                pytest.fail(f"diskpart couldn't create the {name} test volume:\n{result.stdout}{result.stderr}")
            root = f"{letter}:\\"
            for _ in range(50):
                if os.path.isdir(root):
                    break
                time.sleep(0.2)
            else:
                pytest.fail(f"the {name} test volume never appeared as {root}")
            volumes[name] = Volume(root, vhdx, fs)
        yield volumes
    finally:
        for vhdx in made:
            if vhdx.exists():
                _detach(folder, vhdx)


def _allow(vol, write_guard, monkeypatch):
    """Let this process change files on a test volume: the write guard, and pc-tidy's own sandbox."""
    write_guard.add_root(vol.root)
    monkeypatch.setenv("PCTIDY_SANDBOX_ROOT", os.environ["PCTIDY_SANDBOX_ROOT"] + os.pathsep + vol.root)
    return vol


@pytest.fixture
def ntfs_volume(test_volumes, write_guard, sandbox, monkeypatch):
    """The NTFS test volume. It is shared by the session, so each test uses its own folder on it."""
    return _allow(test_volumes["ntfs"], write_guard, monkeypatch)


@pytest.fixture
def fat32_volume(test_volumes, write_guard, sandbox, monkeypatch):
    return _allow(test_volumes["fat32"], write_guard, monkeypatch)


@pytest.fixture
def default_folder(ntfs_volume):
    """<NTFS test volume>\\From-C: the folder move_off_c.py picks by default, and the only place a run on
    a full C: may keep its journal. It is shared, so every test that uses it leaves it empty again."""
    folder = Path(ntfs_volume.root) / "From-C"
    yield folder
    if folder.exists():
        remove_tree(folder)


@pytest.fixture
def mounted_folder(ntfs_volume, profile):
    """The NTFS test volume, also mounted in a folder of the fake C: (like a second disk at C:\\Data)."""
    import ctypes
    name = ctypes.create_unicode_buffer(100)       # mountvol wants it exactly as Windows writes it
    if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(ntfs_volume.root, name, len(name)):
        pytest.fail(f"no volume name for {ntfs_volume.root}: {ctypes.WinError()}")
    folder = profile / "Data"
    folder.mkdir()
    made = subprocess.run(["mountvol", str(folder) + "\\", name.value], capture_output=True, text=True)
    if made.returncode:
        pytest.fail(f"mountvol couldn't mount the test volume in {folder}:\n{made.stdout}{made.stderr}")
    try:
        yield folder
    finally:      # before tmp_path is cleaned up, which must never reach into the volume
        subprocess.run(["mountvol", str(folder) + "\\", "/D"], capture_output=True)


FullC = namedtuple("FullC", "root home run")
MB = 1024 ** 2


@pytest.fixture
def full_c(test_volumes, write_guard, tmp_path):
    """A fake profile on the 64 MB NTFS test volume, filled to 0 bytes free: a full C:. Its runner makes
    that volume the system drive, with pc-tidy's data folder on it. Temp holds 20 MB of files."""
    vol = test_volumes["fullc"]
    write_guard.add_root(vol.root)
    root = Path(vol.root) / tmp_path.name
    home = root / "profile"
    for d in ("Downloads", "Documents", "Videos/Lectures", "AppData/Local/Temp", "AppData/Roaming"):
        (home / d).mkdir(parents=True)
    write(home / "Videos" / "Lectures" / "lecture1.mp4", b"v" * MB)
    write(home / "Videos" / "Lectures" / "notes.txt", b"week 1")
    write(home / "Videos" / "clip.mp4", b"c" * (MB // 2))
    for i in range(20):
        write(home / "AppData" / "Local" / "Temp" / f"cache{i:02}.tmp", bytes([i]) * MB, age_days=30)
    fill_volume(root / "filler")
    try:
        yield FullC(root, home, Runner(home, tmp_path, [tmp_path, vol.root]))
    finally:
        remove_tree(root)
