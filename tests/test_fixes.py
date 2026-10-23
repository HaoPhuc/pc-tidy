"""The ranked fixes for C: (pctidy/fixes.py): what each frees, measured file by file, and the order.

These run in this process on a fake PC in tmp_path: a profile, and a fake Windows folder through a
Context of their own. The sandbox (conftest.py) keeps them from measuring this PC.
"""

import os
import subprocess

import pytest

import scanner
from pctidy import context, fixes, settings
from support import write

MB = 1024 ** 2
GB = 1024 ** 3


def blob(path, mb, age_days=1.0):
    return write(path, b"\0" * int(mb * MB), age_days=age_days)


def fake_pc(tmp_path, **env):
    """A Context for tmp_path/profile, with tmp_path/Windows as the Windows folder."""
    (tmp_path / "profile" / "AppData" / "Local" / "Temp").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Windows").mkdir(exist_ok=True)
    environ = dict(os.environ, SystemRoot=str(tmp_path / "Windows"), SystemDrive=os.path.splitdrive(str(tmp_path))[0])
    environ.update(env)
    return context.Context(environ)


def build(tmp_path, ctx, move=None):
    home = tmp_path / "profile"
    return fixes.build(scanner.Scan([str(home)]), settings.load(ctx), ctx, move=move, progress=False)


def rows_by_id(result):
    return {r["id"]: r for r in result["rows"]}


def test_fixes_are_measured_exactly_and_ranked_lowest_risk_first(tmp_path):
    home, win = tmp_path / "profile", tmp_path / "Windows"
    local = home / "AppData" / "Local"
    blob(local / "Temp" / "a.tmp", 3)
    blob(local / "pip" / "cache" / "wheel.whl", 2)
    blob(local / "uv" / "cache" / "x" / "y.bin", 1.5)
    blob(local / "D3DSCache" / "s.bin", 1.2)
    blob(local / "CrashDumps" / "app.dmp", 2.2)
    blob(local / "CrashDumps" / "notes.txt", 5)            # not a .dmp: not part of the fix
    blob(win / "Temp" / "w.tmp", 4)
    blob(win / "MEMORY.DMP", 1.5)
    blob(win / "Minidump" / "m.dmp", 1)
    blob(win / "notepad.exe", 3)                           # never part of any fix
    blob(home / "Downloads" / "old-setup.exe", 6, age_days=30)
    blob(home / "Downloads" / "new-setup.exe", 8, age_days=0)    # installers_min_age_days = 7
    ctx = fake_pc(tmp_path)

    result = build(tmp_path, ctx)

    ids = [r["id"] for r in result["rows"]]
    assert ids == ["temp-windows", "caches", "temp-user", "crash-dumps-windows", "crash-dumps-user",
                   "shader-caches", "installers"]
    rows = rows_by_id(result)
    assert rows["caches"]["bytes"] == int(3.5 * MB)
    assert rows["crash-dumps-windows"]["bytes"] == int(2.5 * MB)
    assert rows["crash-dumps-user"]["bytes"] == int(2.2 * MB)
    assert rows["installers"]["bytes"] == 6 * MB and rows["installers"]["risk"] == "Medium"
    assert [r["risk"] for r in result["rows"]] == ["Low"] * 6 + ["Medium"]
    totals = [r["total"] for r in result["rows"]]
    assert totals == sorted(totals) and totals[-1] == sum(r["bytes"] for r in result["rows"])
    assert rows["caches"]["how"] == "cleanup.ps1: pip_cache, uv_cache (on)"
    assert rows["caches"]["size"] == "4 MB"         # 3.5 MB, shown in MB below a GB


def test_a_switch_that_is_off_says_what_to_set(tmp_path):
    blob(tmp_path / "profile" / "AppData" / "Local" / "pip" / "cache" / "w.whl", 2)
    write(tmp_path / "settings.ini", "[cleanup]\npip_cache = no\n")
    rows = rows_by_id(build(tmp_path, fake_pc(tmp_path)))
    assert rows["caches"]["how"] == "cleanup.ps1: set pip_cache = yes"


def test_a_hard_link_frees_nothing(tmp_path):
    conda = tmp_path / "profile" / "anaconda3"
    shared = blob(conda / "pkgs" / "numpy-1" / "lib.dll", 5)
    (conda / "envs" / "old-env" / "Lib").mkdir(parents=True)
    os.link(shared, conda / "envs" / "old-env" / "Lib" / "lib.dll")
    blob(conda / "envs" / "old-env" / "own.pyd", 2)
    blob(conda / "pkgs" / "numpy-1.tar.bz2", 3)

    rows = rows_by_id(build(tmp_path, fake_pc(tmp_path)))

    env, pkgs = rows["conda-env:old-env"], rows["conda-pkgs"]
    assert env["bytes"] == 2 * MB and env["risk"] == "Medium"
    assert env["note"] == "7 MB of files, 2 MB freed: the rest is shared with conda's package cache"
    assert env["how"] == "cleanup.ps1: add old-env to conda_envs_to_remove"
    assert pkgs["bytes"] == 3 * MB and pkgs["size"] == "about 3 MB"     # lib.dll is used by old-env


def test_file_bytes(tmp_path):
    plain = blob(tmp_path / "plain.bin", 2)
    linked = blob(tmp_path / "linked.bin", 2)
    os.link(linked, tmp_path / "other-name.bin")
    packed = blob(tmp_path / "packed.bin", 4)

    assert fixes.file_bytes(str(plain)) == 2 * MB
    assert fixes.file_bytes(str(linked)) == 0
    compact = subprocess.run(["compact", "/c", "/q", str(packed)], capture_output=True)
    if compact.returncode:
        pytest.skip("this volume can't compress files")
    assert fixes.file_bytes(str(packed)) < MB          # zeros compress to almost nothing


def test_a_folder_this_window_cant_open_needs_admin_to_measure(tmp_path):
    blob(tmp_path / "profile" / "AppData" / "Local" / "Temp" / "a.tmp", 3)
    locked = tmp_path / "Windows" / "Temp"
    blob(locked / "w.tmp", 4)
    deny = subprocess.run(["icacls", str(locked), "/deny", "*S-1-1-0:(RD)"], capture_output=True, text=True)
    assert deny.returncode == 0, deny.stdout + deny.stderr
    try:
        result = build(tmp_path, fake_pc(tmp_path))
    finally:
        subprocess.run(["icacls", str(locked), "/remove:d", "*S-1-1-0"], capture_output=True)

    rows = rows_by_id(result)
    windows = rows["temp-windows"]
    assert windows["size"] == "needs admin to measure"
    assert not windows["counted"] and windows["total"] is None
    assert [r["id"] for r in result["rows"]].index("temp-windows") > [r["id"] for r in result["rows"]].index("temp-user")
    assert any("needs admin to measure" in n for n in result["notes"])


def test_a_file_counts_once(tmp_path):
    """An old installer that is also offered for moving, and a node_modules inside a folder offered
    for moving: each byte is counted by the fix highest in the list only."""
    home = tmp_path / "profile"
    setup = blob(home / "Downloads" / "big-setup.exe", 6, age_days=30)
    project = home / "Documents" / "robot"
    blob(project / "node_modules" / "pkg" / "index.js", 4)
    blob(project / "data.bin", 6)
    write(tmp_path / "settings.ini", "[report]\ncleanup_min_gb = 0.001\n")
    move = {"dest": "X:\\From-C", "candidates": [
        {"path": str(setup), "size": 6 * MB, "kind": "file"},
        {"path": str(project), "size": 10 * MB, "kind": "folder"}]}

    result = build(tmp_path, fake_pc(tmp_path), move=move)

    ids = [r["id"] for r in result["rows"]]
    assert ids == [f"move:{project}", "installers"]       # the move claims node_modules, the installer claims the file
    assert result["rows"][-1]["total"] == 16 * MB
    assert rows_by_id(result)[f"move:{project}"]["how"] == f'python move_off_c.py --path "{project}"'


def test_browser_cache_counts_only_the_cache(tmp_path):
    profile = tmp_path / "profile" / "AppData" / "Local" / "Google" / "Chrome" / "User Data" / "Default"
    blob(profile / "Cache" / "Cache_Data" / "f_000001", 2)
    blob(profile / "Code Cache" / "js" / "x", 1)
    blob(profile / "GPUCache" / "data_0", 0.5)
    blob(profile / "History", 5)                       # your history: never part of the fix

    chrome = rows_by_id(build(tmp_path, fake_pc(tmp_path)))["browser:chrome"]

    assert chrome["bytes"] == int(3.5 * MB) and chrome["size"].startswith("about ")
    assert chrome["how"] == "In Chrome: Settings > Delete browsing data > Cached images and files"


def test_the_recycle_bin_is_high_risk_and_last(tmp_path):
    blob(tmp_path / "profile" / "AppData" / "Local" / "Temp" / "a.tmp", 3)
    ctx = fake_pc(tmp_path, PCTIDY_TEST_RECYCLE_BIN=f"{2 * GB},312")

    result = build(tmp_path, ctx)

    last = result["rows"][-1]
    assert last["id"] == "recycle-bin" and last["risk"] == "High"
    assert last["title"] == "Empty the Recycle Bin (312 items)"
    assert last["total"] == 2 * GB + 3 * MB


def test_a_temp_that_isnt_a_temp_folder_is_left_out(tmp_path):
    documents = tmp_path / "profile" / "Documents"
    blob(documents / "thesis.docx", 3)
    ctx = fake_pc(tmp_path, TEMP=str(documents), TMP=str(documents))

    result = build(tmp_path, ctx)

    assert "temp-user" not in rows_by_id(result)
    assert any("isn't a temp folder. It isn't listed, and cleanup.ps1 leaves it alone." in n for n in result["notes"])


def test_no_fixes_when_the_system_drive_wasnt_scanned(tmp_path):
    ctx = fake_pc(tmp_path, SystemDrive="Q:")
    result = build(tmp_path, ctx)
    assert result["scanned"] is False and result["rows"] == []
    assert result["notes"] == ["Q: wasn't scanned, so there are no fixes for it. Scan it with: "
                               "python disk_report.py --path Q:\\"]


@pytest.mark.parametrize("target, line", [
    (5, "C: has 10% free, more than the 5% to aim for."),
    (15, "Fixes 1-2 bring C: to about 16% free (the low-risk ones alone: about 12%). "
         "15% free is a comfortable amount."),
    (12, "Fix 1 brings C: to about 12% free. 12% free is a comfortable amount."),
    (30, "All the fixes together bring C: to about 19% free, short of the 30% to aim for. "
         "The biggest folders and files below show what else fills it."),
])
def test_target_line(target, line):
    out = {"drive": "C:", "free": 10 * GB, "size": 100 * GB, "rows": [
        {"risk": "Low", "total": 2 * GB}, {"risk": "Medium", "total": 6 * GB},
        {"risk": "Medium", "total": None}, {"risk": "High", "total": 9 * GB}]}
    assert fixes.target_line(out, target) == line


def test_measuring_stops_at_its_budget(tmp_path, monkeypatch):
    for i in range(5):
        blob(tmp_path / "profile" / "AppData" / "Local" / "pip" / "cache" / f"{i}.whl", 1.5)
    monkeypatch.setattr(fixes, "MEASURE_FILES", 3)

    caches = rows_by_id(build(tmp_path, fake_pc(tmp_path)))["caches"]

    assert caches["size"].startswith("at least ") and not caches["counted"] and caches["total"] is None


def test_the_ai_is_never_imported():
    """The fixes, their sizes and their risks never depend on the LLM."""
    import ast
    tree = ast.parse(open(fixes.__file__, encoding="utf-8").read())
    names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "llm" not in names and "llm" not in modules


def test_folders_inside_temp_are_left_to_the_temp_fix(tmp_path):
    """A copy of a profile left in Temp has its own conda and Chrome folders: the Temp fix covers them."""
    temp = tmp_path / "profile" / "AppData" / "Local" / "Temp"
    copy = temp / "old-copy"
    blob(copy / "anaconda3" / "pkgs" / "x.tar.bz2", 3)
    blob(copy / "AppData" / "Local" / "Google" / "Chrome" / "User Data" / "Default" / "Cache" / "f", 2)

    rows = rows_by_id(build(tmp_path, fake_pc(tmp_path)))

    assert set(rows) == {"temp-user"} and rows["temp-user"]["bytes"] == 5 * MB


def test_two_fixes_with_the_same_name_say_where(tmp_path):
    home = tmp_path / "profile"
    blob(home / "anaconda3" / "pkgs" / "a.tar.bz2", 3)
    blob(home / "miniconda3" / "pkgs" / "b.tar.bz2", 2)

    titles = [r["title"] for r in build(tmp_path, fake_pc(tmp_path))["rows"]]

    assert titles == [f"conda package cache in {home / 'anaconda3'}", f"conda package cache in {home / 'miniconda3'}"]
