"""The test write guard: nothing a test runs may change files outside the test's own folders."""

import os

import pytest

ATTEMPTS = r'''
import os, shutil, sys
from pathlib import Path

target = Path(sys.argv[1])
attempts = {
    "os.remove": lambda: os.remove(target / "a.txt"),
    "Path.unlink": lambda: (target / "b.txt").unlink(),
    "Path.write_text": lambda: (target / "c.txt").write_text("overwritten"),
    "open for writing": lambda: open(target / "new.txt", "w").close(),
    "os.rename": lambda: os.rename(target / "d.txt", target / "renamed.txt"),
    "shutil.rmtree": lambda: shutil.rmtree(target / "folder"),
    "Path.mkdir": lambda: (target / "made").mkdir(),
}
for name, attempt in attempts.items():
    try:
        attempt()
        print("ALLOWED", name)
    except PermissionError:
        print("BLOCKED", name)
    except Exception as e:
        print("ERROR", name, e)
'''
VICTIMS = ["a.txt", "b.txt", "c.txt", "d.txt", "folder/inner.txt"]


def _victims(folder):
    (folder / "folder").mkdir(parents=True)
    for name in VICTIMS:
        (folder / name).write_text("keep me")


@pytest.fixture(scope="session")
def outside(tmp_path_factory):
    """Files outside every test's tmp_path. Session-scoped, so they exist before the guard is on."""
    folder = tmp_path_factory.mktemp("outside")
    _victims(folder)
    return folder


def test_guard_stops_a_subprocess_outside_the_test_folders(run, tmp_path, outside):
    driver = tmp_path / "attempts.py"
    driver.write_text(ATTEMPTS)
    result = run.python(driver, outside, expect_violations=True)

    assert result.returncode == 0, result.output
    assert result.stdout.count("BLOCKED") == 7, result.output
    assert len(result.violations) == 7
    for name in VICTIMS:
        assert (outside / name).read_text() == "keep me"
    assert not (outside / "new.txt").exists() and not (outside / "made").exists()


def test_guard_lets_a_subprocess_work_inside_the_test_folders(run, tmp_path):
    driver = tmp_path / "attempts.py"
    driver.write_text(ATTEMPTS)
    inside = tmp_path / "inside"
    _victims(inside)
    result = run.python(driver, inside)

    assert result.stdout.count("ALLOWED") == 7, result.output
    assert sorted(p.name for p in inside.iterdir()) == ["c.txt", "made", "new.txt", "renamed.txt"]


def test_guard_stops_this_process_outside_its_tmp_path(write_guard, outside):
    with pytest.raises(PermissionError):
        os.remove(outside / "a.txt")
    with pytest.raises(PermissionError):
        open(outside / "new.txt", "w")
    assert (outside / "a.txt").exists()
    assert len(write_guard.violations) == 2
    write_guard.violations.clear()     # expected here, so don't fail the test for them
