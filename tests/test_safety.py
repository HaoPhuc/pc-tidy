"""safety.check: the protection table, the exceptions that match today's cleanup.ps1, and the TEMP check.

The checks only compare paths, so most cases use a context whose system folders, Program Files and
profiles are all fake folders in tmp_path. Drive roots are real letters, but nothing is ever changed.
"""

import os
import re

import pytest

from pctidy import safety
from pctidy.context import APP_DIR, Context
from support import ROOT


@pytest.fixture
def fake(tmp_path):
    """A whole fake PC in tmp_path: Windows, Program Files, ProgramData, Users with three profiles."""
    users = tmp_path / "Users"
    me = users / "me"
    for d in ("Users/Public", "Users/other", "Users/me/Documents", "Users/me/Desktop", "Users/me/Downloads",
              "Users/me/OneDrive - Contoso", "Users/me/AppData/Local/Temp", "Users/me/AppData/Roaming",
              "Windows", "Program Files", "Program Files (x86)", "ProgramData", "D/Sync/OneDrive"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    env = {
        "USERPROFILE": str(me), "LOCALAPPDATA": str(me / "AppData/Local"), "APPDATA": str(me / "AppData/Roaming"),
        "TEMP": str(me / "AppData/Local/Temp"), "SystemDrive": "C:", "SystemRoot": str(tmp_path / "Windows"),
        "ProgramFiles": str(tmp_path / "Program Files"), "ProgramFiles(x86)": str(tmp_path / "Program Files (x86)"),
        "ProgramW6432": str(tmp_path / "Program Files"), "ProgramData": str(tmp_path / "ProgramData"),
        "PUBLIC": str(users / "Public"), "OneDrive": str(tmp_path / "D/Sync/OneDrive"),
    }
    return tmp_path, env


def context_for(fake, sandbox=False, **overrides):
    tmp_path, env = fake
    env = dict(env, **overrides)
    if sandbox:
        env["PCTIDY_SANDBOX_ROOT"] = str(tmp_path)
    return Context(env)


ME = "Users/me"
LOCAL = "Users/me/AppData/Local"
CASES = [
    # 1. drive roots, Users, the profiles, the Known Folders themselves
    ("D:\\", "delete", "is a whole drive"),
    ("D:", "delete", "is a whole drive"),
    ("D:\\", "move", "is a whole drive"),
    ("{t}/Users", "delete", "holds every user's profile"),
    ("{t}/Users/me", "delete", "is a user profile folder"),
    ("{t}/Users/Public", "move", "is a user profile folder"),
    ("{t}/Users/other", "delete", "is a user profile folder"),
    ("{t}/" + ME + "/Documents", "delete", "is one of your Windows user folders"),
    ("{t}/" + ME + "/Desktop", "move", "is one of your Windows user folders"),
    ("{t}/" + ME + "/Downloads", "delete", "is one of your Windows user folders"),
    ("{t}/" + ME + "/Videos", "move", "is one of your Windows user folders"),
    ("{t}/" + ME + "/OneDrive - Contoso", "move", "is one of your Windows user folders"),
    ("{t}/D/Sync/OneDrive", "delete", "is one of your Windows user folders"),
    ("{t}/D/Sync", "delete", "contains"),                                   # holds a Known Folder
    ("{t}/" + ME + "/Documents/old drafts", "delete", None),
    ("{t}/" + ME + "/Videos/Lectures", "move", None),
    ("{t}/" + ME + "/Projects", "move", None),
    # 2. Windows, Program Files, ProgramData, AppData, pc-tidy: refused, and everything inside them...
    ("{t}/Windows", "delete", "is inside Windows"),
    ("{t}/Windows/notepad.exe", "delete", "is inside Windows"),
    ("{t}/Program Files (x86)/Steam/userdata/1/3472040/local/tmp/replay.bin", "delete", "is inside Program Files"),
    ("{t}/Program Files/App/data", "move", "is inside Program Files"),
    ("{t}/ProgramData/Package Cache/x.msi", "delete", "is inside ProgramData"),
    ("{t}/" + LOCAL + "/Google/Chrome/User Data", "delete", "is inside AppData"),
    ("{t}/Users/me/AppData/Roaming/app.ini", "delete", "is inside AppData"),
    ("{t}/Users/other/AppData/Local/x", "delete", "is inside AppData"),
    ("{t}/" + LOCAL + "/pc-tidy/runs/x.jsonl", "delete", "is inside"),
    (os.path.join(APP_DIR, "tidy_folder.py"), "delete", "is inside pc-tidy's own folder"),
    # ...except exactly the rows of the table (today's cleanup.ps1, line by line)
    ("{t}/Windows/MEMORY.DMP", "delete", None),
    ("{t}/Windows/Minidump/0101-01.dmp", "delete", None),
    ("{t}/Windows/Minidump/notes.txt", "delete", "is inside Windows"),
    ("{t}/Windows/LiveKernelReports/WATCHDOG-1.dmp", "delete", None),
    ("{t}/Windows/LiveKernelReports/WATCHDOG/a.dmp", "delete", None),
    ("{t}/Windows/LiveKernelReports/a/b/c.dmp", "delete", "is inside Windows"),
    ("{t}/Windows/Temp/setup.log", "delete", None),
    ("{t}/Windows/Temp/folder/inner", "delete", None),
    ("{t}/Windows/Temp", "delete", "is inside Windows"),                     # the folder itself
    ("{t}/Windows/Temp/setup.log", "move", "is inside Windows"),             # exceptions are for deletes
    ("{t}/" + LOCAL + "/Temp/tmp1.tmp", "delete", None),
    ("{t}/" + LOCAL + "/pip/cache/wheels", "delete", None),
    ("{t}/" + LOCAL + "/pip/cache", "delete", "is inside AppData"),
    ("{t}/" + LOCAL + "/uv/cache/x", "delete", None),
    ("{t}/" + LOCAL + "/NVIDIA/DXCache/x", "delete", None),
    ("{t}/" + LOCAL + "/AMD/GLCache/x", "delete", None),
    ("{t}/" + LOCAL + "/D3DSCache/x", "delete", None),
    ("{t}/" + LOCAL + "/CrashDumps/app.exe.123.dmp", "delete", None),
    ("{t}/" + LOCAL + "/CrashDumps/readme.txt", "delete", "is inside AppData"),
    # 3. wildcards
    ("D:\\*", "delete", "a wildcard directly under a drive root could empty the drive"),
    ("{t}/Users/*", "delete", "directly under the Users folder"),
    ("{t}/Users/*/Documents", "delete", "directly under the Users folder"),
    ("{t}/Users/me/*", "delete", "directly under a user profile"),
    ("{t}/" + ME + "/Documents/*", "delete", "directly under Documents"),
    ("{t}/Windows/*", "delete", "is inside Windows"),
    ("{t}/Program Files/*", "delete", "is inside Program Files"),
    ("{t}/" + LOCAL + "/*", "delete", "is inside AppData"),
    ("{t}/Windows/*.log", "delete", "is inside Windows"),
    ("{t}/D/old-backups/*.zip", "delete", None),
    ("{t}/" + ME + "/Documents/old drafts/*", "delete", None),
    ("{t}/Windows/Temp/*", "delete", None),
    ("{t}/" + LOCAL + "/Temp/*", "delete", None),
    ("{t}/Windows/Minidump/*.dmp", "delete", None),
    ("{t}/Windows/LiveKernelReports/*/*.dmp", "delete", None),
    ("{t}/" + ME + "/Videos/*", "move", "wildcards are only allowed in paths to delete"),
    # 6. the folder to sort
    ("{t}/Users/me", "sort", "is a user profile folder"),
    ("{t}/Users", "sort", "holds every user's profile"),
    ("D:\\", "sort", "is a whole drive"),
    ("{t}/Windows/Temp", "sort", "is inside Windows"),
    ("{t}/" + LOCAL + "/Temp", "sort", "is inside AppData"),
    ("{t}/" + ME + "/Downloads", "sort", None),
    ("{t}/" + ME + "/Desktop", "sort", None),
    ("{t}/D/Stuff", "sort", None),
    # where a move may go
    ("{t}/" + LOCAL + "/Moved", "move-to", "is inside AppData"),
    ("{t}/D/From-C/Videos/Lectures", "move-to", None),
    ("", "delete", "the path is empty"),
]


@pytest.mark.parametrize("path, op, expected", CASES, ids=[f"{op}:{p}" for p, op, _ in CASES])
def test_protection_table(fake, path, op, expected):
    tmp_path, _env = fake
    ctx = context_for(fake)
    problem = safety.why_refused(path.replace("{t}", str(tmp_path)).replace("/", "\\"), op, ctx)
    if expected is None:
        assert problem is None
    else:
        assert problem and expected in problem, problem


def test_the_context_reads_windows_variable_names_in_any_case(monkeypatch):
    """A copy of os.environ has every name in upper case; Windows itself ignores case."""
    ctx = Context({"systemdrive": "X:", "SYSTEMROOT": "X:\\Windows", "ProgramFiles": "X:\\Apps"})
    assert (ctx.system_drive, ctx.system_root) == ("X:\\", "X:\\Windows")
    assert "X:\\Apps" in ctx.program_files
    monkeypatch.setenv("SystemDrive", "Y:")
    assert Context().system_drive == "Y:\\"


def test_check_raises_refused(fake):
    with pytest.raises(safety.Refused, match="is a whole drive"):
        safety.check("D:\\", "delete", context_for(fake))


def test_the_examples_from_problem_1_are_all_refused(fake):
    """Today's Test-Protected lets all of these through."""
    tmp_path, _env = fake
    ctx = context_for(fake)
    for pattern in ("E:\\*", f"{tmp_path}\\Windows\\*", f"{tmp_path}\\Program Files\\*", f"{tmp_path}\\{LOCAL}\\*",
                    f"{tmp_path}\\{ME}\\Documents\\*", "D:\\*"):
        assert safety.why_refused(pattern.replace("/", "\\"), "delete", ctx), pattern


def test_the_sandbox_refuses_everything_outside_it(fake, tmp_path):
    ctx = context_for(fake, sandbox=True)
    assert "outside the test folders" in safety.why_refused(os.path.join(ROOT, "README.md"), "delete", ctx)
    assert "outside the test folders" in safety.why_refused("D:\\old-backups\\*.zip", "delete", ctx)
    assert safety.why_refused(tmp_path / "D" / "Stuff" / "x.txt", "delete", ctx) is None


def test_a_link_into_a_protected_folder_is_judged_by_where_it_leads(fake, tmp_path):
    from support import make_junction
    ctx = context_for(fake)
    link = tmp_path / "D" / "link-to-windows"
    link.parent.mkdir(parents=True, exist_ok=True)
    make_junction(link, tmp_path / "Windows")

    assert safety.why_refused(link / "notepad.exe", "delete", ctx)      # really C:\Windows\notepad.exe
    assert safety.why_refused(link, "delete", ctx) is None               # the link itself is fine to remove


# ---------- every path today's cleanup.ps1 removes maps to a row, and nothing else does ----------

def cleanup_ps1_patterns(ctx):
    """The Remove-Paths patterns in today's cleanup.ps1, with $env: variables from the context. Each
    once: the Temp step lists Windows\\Temp twice (with and without your TEMP, problem 14)."""
    text = (ROOT / "cleanup.ps1").read_text(encoding="utf-8")
    values = {"TEMP": ctx.temp, "SystemRoot": ctx.system_root, "LOCALAPPDATA": ctx.local_appdata}
    out = []
    for call in re.findall(r'Remove-Paths\s+"[^"]*"\s+@\((.*?)\)', text, re.S):
        for quoted in re.findall(r'"([^"]+)"', call):
            if quoted == "$p":
                continue      # ExtraPathsToDelete: the user's own, checked one by one
            out.append(re.sub(r"\$env:(\w+)", lambda m: values[m.group(1)], quoted))
    return list(dict.fromkeys(out))


def table_patterns(ctx):
    out = []
    for row in safety.ALLOWED:
        for pattern in row.patterns:
            path = pattern.format(temp=ctx.temp, system_root=ctx.system_root, local=ctx.local_appdata)
            out.append(path + "\\*" if row.kind == "contents" else path)
    return out


def test_cleanup_ps1_and_the_exception_table_match_line_by_line(fake):
    ctx = context_for(fake)
    ps1, table = cleanup_ps1_patterns(ctx), table_patterns(ctx)

    assert len(ps1) == 14
    assert sorted(p.lower() for p in ps1) == sorted(p.lower() for p in table)
    for pattern in ps1:
        assert safety.why_refused(pattern, "delete", ctx) is None, pattern


def test_nothing_next_to_an_exception_is_allowed(fake):
    tmp_path, _env = fake
    ctx = context_for(fake)
    for path in (f"{tmp_path}\\{LOCAL}\\pip", f"{tmp_path}\\{LOCAL}\\pip\\other", f"{tmp_path}\\{LOCAL}\\NVIDIA",
                 f"{tmp_path}\\{LOCAL}\\NVIDIA\\Other", f"{tmp_path}\\Windows\\Minidump",
                 f"{tmp_path}\\Windows\\MEMORY.DMP.bak", f"{tmp_path}\\{LOCAL}\\CrashDumps"):
        assert safety.why_refused(path, "delete", ctx), path
        assert safety.allowed_row(path, ctx) is None, path
    assert safety.allowed_row(f"{tmp_path}\\Windows\\MEMORY.DMP", ctx).fix == "crash-dumps-windows"


# ---------- the TEMP check (problem 14) ----------

@pytest.mark.parametrize("temp, ok", [
    ("D:\\", False),
    ("{t}/Users/me", False),
    ("{t}/Users/me/Documents", False),
    ("{t}/" + LOCAL, False),
    ("{t}/Windows", False),
    ("{t}/D/stuff", False),
    ("{t}/" + LOCAL + "/Temp", True),
    ("{t}/" + LOCAL + "/Temp/2", True),         # remote desktop sessions
    ("{t}/Windows/Temp", True),
    ("{t}/D/Tmp", True),
])
def test_temp_check(fake, temp, ok):
    tmp_path, _env = fake
    temp = temp.replace("{t}", str(tmp_path)).replace("/", "\\")
    problem = safety.temp_problem(context_for(fake, TEMP=temp))
    if ok:
        assert problem is None
    else:
        assert problem == f"Your TEMP variable points at {temp}, which isn't a temp folder. The Temp fix was skipped."


def test_a_bad_temp_turns_off_the_temp_row(fake, tmp_path):
    packages = f"{tmp_path}\\{LOCAL}\\Packages".replace("/", "\\")
    ctx = context_for(fake, TEMP=packages)
    assert safety.why_refused(packages + "\\app\\settings.dat", "delete", ctx)
    assert safety.why_refused(packages + "\\*", "delete", ctx)
