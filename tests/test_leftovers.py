"""What's left in your folders (pctidy/leftovers.py): duplicates, big files not opened for months,
empty folders and projects, on a fake profile in tmp_path (conftest.py's sandbox points the profile
there)."""

import os
import time

import pytest

import scanner
from pctidy import context, fixes, leftovers, settings
from support import write

MB = 1024 ** 2
DAY = 86400


def blob(path, mb, fill=b"\0", age_days=1.0):
    return write(path, fill * int(mb * MB), age_days=age_days)


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "profile"
    for d in ("Desktop", "Documents", "Downloads", "Music", "Pictures", "Videos", "Sorted",
              "AppData/Local/Temp"):
        (home / d).mkdir(parents=True)
    return home


def age(path, days, atime_days=None):
    """Set a file's or folder's modified (and last-access) time this many days back."""
    m = time.time() - days * DAY
    a = time.time() - (days if atime_days is None else atime_days) * DAY
    os.utime(path, (a, m))
    return path


def analyse(tmp_path, settings_text=None, **env):
    if settings_text:
        write(tmp_path / "settings.ini", settings_text)
    ctx = context.Context(dict(os.environ, **env))
    return leftovers.analyse(ctx, settings.load(ctx), progress=False), ctx


# ---------- duplicates ----------
def test_duplicates_keep_the_copy_outside_downloads_and_offer_only_accidental_copies(tmp_path, home):
    keep = blob(home / "Documents" / "thesis.pdf", 2, b"A", age_days=40)
    in_downloads = blob(home / "Downloads" / "thesis (1).pdf", 2, b"A", age_days=10)
    in_sorted = blob(home / "Sorted" / "Data" / "thesis.pdf", 2, b"A", age_days=5)
    photo = blob(home / "Pictures" / "cat.jpg", 1.5, b"B", age_days=30)
    photo_copy = blob(home / "Videos" / "cat-copy.jpg", 1.5, b"B", age_days=20)
    blob(home / "Downloads" / "x.bin", 2, b"C")
    write(home / "Downloads" / "y.bin", b"C" * (2 * MB - 1) + b"D")          # same size, other content
    original = blob(home / "Documents" / "report.bin", 2, b"E")
    os.link(original, home / "Downloads" / "report-link.bin")              # the same file, not a copy
    write(home / "Downloads" / "empty1.txt", b"")
    write(home / "Downloads" / "empty2.txt", b"")
    blob(home / "Downloads" / "small1.txt", 0.5, b"F")                     # under duplicate_min_mb
    blob(home / "Downloads" / "small2.txt", 0.5, b"F")

    lo, _ctx = analyse(tmp_path)

    groups = {r["keep"]: r for r in lo["duplicates"]}
    assert set(groups) == {str(keep), str(photo)}
    thesis = groups[str(keep)]
    assert {x["path"] for x in thesis["extras"]} == {str(in_downloads), str(in_sorted)}
    assert all(x["offered"] for x in thesis["extras"])
    cat = groups[str(photo)]
    assert cat["extras"] == [{"path": str(photo_copy), "offered": False, "in_project": False}]  # yours to decide
    assert {p[0] for p in lo["pairs"]} == {str(in_downloads), str(in_sorted)}
    assert all(p[1] == str(keep) for p in lo["pairs"])
    assert lo["offered_bytes"] == 4 * MB


def test_with_every_copy_in_downloads_the_oldest_stays(tmp_path, home):
    old = blob(home / "Downloads" / "setup notes.pdf", 1, b"G", age_days=30)
    new = blob(home / "Downloads" / "setup notes (1).pdf", 1, b"G", age_days=2)
    lo, _ctx = analyse(tmp_path)
    assert lo["pairs"] == [[str(new), str(old), leftovers._fp(new), leftovers._fp(old)]]


def test_checking_duplicates_never_changes_when_a_file_was_last_opened(tmp_path, home):
    a = blob(home / "Documents" / "video.mp4", 3, b"V")
    b = blob(home / "Downloads" / "video (1).mp4", 3, b"V")
    year_ago = time.time() - 365 * DAY
    for f in (a, b):
        os.utime(f, (year_ago, year_ago))
    before = [os.stat(f).st_atime_ns for f in (a, b)]

    lo, _ctx = analyse(tmp_path)

    assert lo["pairs"]                                   # both were read, whole
    assert [os.stat(f).st_atime_ns for f in (a, b)] == before


def test_a_copy_inside_a_project_is_never_offered(tmp_path, home):
    project = home / "Documents" / "robot"
    (project / ".git").mkdir(parents=True)
    vendored = blob(project / "lib" / "model.bin", 1, b"M")
    blob(project / "build" / "model.bin", 1, b"M")
    data = blob(project / "data.bin", 1, b"N")
    loose = blob(home / "Downloads" / "data.bin", 1, b"N")

    lo, _ctx = analyse(tmp_path)

    groups = {r["keep"]: r for r in lo["duplicates"]}
    assert groups[str(data)]["extras"] == [{"path": str(loose), "offered": True, "in_project": False}]
    inside = next(r for r in lo["duplicates"] if r["keep"].startswith(str(project / ""))
                  and r["keep"] != str(data))
    assert inside["extras"][0]["in_project"] and not inside["extras"][0]["offered"]
    assert str(vendored) in (inside["keep"], inside["extras"][0]["path"])


def test_reading_stops_at_its_budget(tmp_path, home):
    for i in range(3):
        blob(home / "Downloads" / f"a{i}.bin", 1, b"Z")
    lo, _ctx = analyse(tmp_path, "[leftovers]\nhash_budget_gb = 0.0000001\n")
    assert lo["unchecked"] == 1 and lo["duplicates"] == []
    assert any("weren't checked" in line for line in leftovers.render(lo, fixes.gb))


# ---------- big files not opened for months ----------
def test_big_files_not_opened_for_months(tmp_path, home):
    opened_long_ago = age(blob(home / "Videos" / "old.mkv", 2), 400)
    changed_long_ago_opened_today = age(blob(home / "Videos" / "watched.mkv", 2), 400, atime_days=0)
    age(blob(home / "Videos" / "new.mkv", 2), 3)
    age(blob(home / "Videos" / "small.mkv", 0.5), 400)
    text = "[leftovers]\nstale_min_mb = 1\n"

    lo_on, _ctx = analyse(tmp_path, text, PCTIDY_TEST_LAST_ACCESS="on")
    lo_off, _ctx = analyse(tmp_path, text, PCTIDY_TEST_LAST_ACCESS="off")

    assert [s["path"] for s in lo_on["stale"]] == [str(opened_long_ago)]
    assert {s["path"] for s in lo_off["stale"]} == {str(opened_long_ago), str(changed_long_ago_opened_today)}
    assert any("Windows isn't recording when files are opened" in line for line in leftovers.render(lo_off, fixes.gb))


# ---------- empty folders ----------
def test_empty_folders(tmp_path, home):
    outer = home / "Documents" / "New folder"
    (outer / "inner").mkdir(parents=True)
    recent = home / "Documents" / "made today"
    recent.mkdir()
    hidden = home / "Documents" / "hidden empty"
    hidden.mkdir()
    os.system(f'attrib +h "{hidden}"')
    project = home / "Documents" / "robot"
    (project / ".git").mkdir(parents=True)
    (project / "logs").mkdir()
    for d in (outer / "inner", outer, hidden, project / "logs"):
        age(d, 30)

    lo, _ctx = analyse(tmp_path)

    assert lo["empty"] == [str(outer)]                     # once, and not its empty subfolder
    assert lo["empty_in_projects"] == 1


# ---------- projects ----------
def test_projects_are_found_without_false_ones(tmp_path, home):
    git = home / "Documents" / "robot"
    (git / ".git").mkdir(parents=True)
    write(git / "src" / "main.c", "int main;")
    (git / "sub" / ".git").mkdir(parents=True)            # a project inside a project: the outer one wins
    node = home / "Documents" / "site"
    write(node / "package.json", "{}")
    write(node / "src" / "index.ts", "export {}")
    moved_whole = home / "Sorted" / "Projects and code" / "old-thing"
    (moved_whole / ".git").mkdir(parents=True)
    write(home / "Sorted" / "Data" / "package.json", "{}")            # a loose manifest sorted by type
    write(home / "Sorted" / "Code" / "requirements.txt", "numpy")     # tidy_folder.py's own folder
    write(home / "Sorted" / "Code" / "script.py", "print()")
    write(home / "Desktop" / "requirements.txt", "numpy")             # the Desktop itself is never a project
    write(home / "Desktop" / "notes.py", "print()")
    write(home / "Desktop" / "scene.blend", "blend")

    lo, _ctx = analyse(tmp_path)

    assert sorted(p["path"] for p in lo["projects"]) == sorted(map(str, (git, node, moved_whole)))


def test_a_quiet_projects_venv_is_offered_as_a_fix(tmp_path, home):
    quiet = home / "Documents" / "analysis"
    write(quiet / "requirements.txt", "numpy", age_days=60)
    write(quiet / "main.py", "print()", age_days=60)
    write(quiet / "venv" / "pyvenv.cfg", "home = x", age_days=1)        # the venv's own files don't count
    blob(quiet / "venv" / "Lib" / "numpy.pyd", 3, age_days=1)
    busy = home / "Documents" / "thesis-code"
    write(busy / "pyproject.toml", "[project]")
    write(busy / "run.py", "print()")
    write(busy / ".venv" / "pyvenv.cfg", "home = x")

    lo, ctx = analyse(tmp_path)

    assert lo["rebuildable"] == [{"path": str(quiet / "venv"), "kind": "venv", "project": str(quiet)}]
    result = fixes.build(scanner.Scan([str(home)]), settings.load(ctx), ctx, leftovers=lo, progress=False)
    row = next(r for r in result["rows"] if r["id"] == f"rebuild:{quiet / 'venv'}")
    assert row["title"] == "Python virtual environment of analysis" and row["risk"] == "Medium"
    assert row["bytes"] >= 3 * MB


def test_the_extra_copies_are_one_fix(tmp_path, home):
    blob(home / "Documents" / "thesis.pdf", 2, b"A", age_days=40)
    blob(home / "Downloads" / "thesis (1).pdf", 2, b"A")
    blob(home / "Desktop" / "thesis copy.pdf", 2, b"A")
    lo, ctx = analyse(tmp_path)

    result = fixes.build(scanner.Scan([str(home)]), settings.load(ctx), ctx, leftovers=lo, progress=False)

    row = next(r for r in result["rows"] if r["id"] == "duplicates")
    assert row["title"] == "Extra copies of files in Downloads, Desktop or Sorted (2)"
    assert row["risk"] == "Medium" and row["bytes"] == 4 * MB
    assert row["why"] == "An identical copy of each stays where it is"


def test_copies_inside_an_unpacked_app_are_never_offered(tmp_path, home):
    """Seen on a real PC: an installer unpacked in Downloads holds two identical resources.pak files.
    Deleting one could break the app, so only loose copies are offered."""
    app = home / "Downloads" / "_temp_matlab" / "bin" / "win64"
    blob(app / "resources.pak", 2, b"P", age_days=10)
    blob(app / "cef_resources" / "resources.pak", 2, b"P", age_days=10)

    lo, _ctx = analyse(tmp_path)

    assert lo["pairs"] == []
    (group,) = lo["duplicates"]
    assert [x["offered"] for x in group["extras"]] == [False]
