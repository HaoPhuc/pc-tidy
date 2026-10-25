"""safety.check: the protection table, the exceptions that match today's cleanup.ps1, and the TEMP check.

The checks only compare paths, so most cases use a context whose system folders, Program Files and
profiles are all fake folders in tmp_path. Drive roots are real letters, but nothing is ever changed.
"""

import os

import pytest

from pctidy import paths, safety
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


# ---------- every folder a [cleanup] switch empties maps to a row, and nothing else does ----------

def table_patterns(ctx):
    out = []
    for row in safety.ALLOWED:
        for pattern in row.patterns:
            path = pattern.format(temp=ctx.temp, system_root=ctx.system_root, local=ctx.local_appdata)
            out.append(path + "\\*" if row.kind == "contents" else path)
    return out


def test_the_cleanup_switches_and_the_exception_table_match_line_by_line(fake):
    """Every place a [cleanup] switch empties inside a protected folder is in the exception table, and
    every row of the table has a switch."""
    from pctidy import cleanup, settings
    switches = {k.name for k in settings.SECTIONS["cleanup"]}
    covered = []
    for switch, fix, part, _title in cleanup._TABLE:
        assert switch in switches, switch
        (row,) = [r for r in safety.ALLOWED if r.fix == fix]
        covered += [p for p in row.patterns if part is None or part in p]
    assert sorted(covered) == sorted(p for r in safety.ALLOWED for p in r.patterns)

    ctx = context_for(fake)
    table = table_patterns(ctx)
    assert len(table) == 14
    for pattern in table:
        assert safety.why_refused(pattern, "delete", ctx) is None, pattern


# ---------- wildcards: only * and ? ----------

@pytest.mark.parametrize("name, pattern, matches", [
    ("old[1]", "old[1]", True), ("old1", "old[1]", False),          # problem 17
    ("b [2].zip", "b [2]*.zip", True), ("b 2.zip", "b [2]*.zip", False),
    ("c1.log", "c?.log", True), ("c12.log", "c?.log", False),
    ("Report.PDF", "*.pdf", True), ("a]b", "a]b", True), ("[!x]", "[!x]", True), ("y", "[!x]", False),
])
def test_only_star_and_question_mark_are_wildcards(name, pattern, matches):
    assert paths.part_matches(name, pattern) is matches


def test_expand_finds_exactly_what_a_path_names(tmp_path):
    for name in ("old[1]", "old1", "old2"):
        (tmp_path / name).mkdir()

    assert paths.expand(str(tmp_path / "old[1]")) == [str(tmp_path / "old[1]")]
    assert paths.expand(str(tmp_path / "old?")) == [str(tmp_path / "old1"), str(tmp_path / "old2")]
    assert paths.expand(str(tmp_path / "*" / "nothing")) == []
    assert paths.expand(str(tmp_path / "missing")) == []


def test_the_exception_table_treats_brackets_as_part_of_a_name(fake):
    """A Windows folder whose name has [ ] in it: its crash dumps match, and nothing else does."""
    tmp_path, _env = fake
    ctx = context_for(fake, SystemRoot=str(tmp_path / "Win[dows]"))
    assert safety.allowed_row(str(tmp_path / "Win[dows]" / "Minidump" / "a.dmp"), ctx).fix == "crash-dumps-windows"
    assert safety.allowed_row(str(tmp_path / "Wind" / "Minidump" / "a.dmp"), ctx) is None


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


def test_pc_tidy_in_temp_leaves_out_only_the_item_that_holds_it(fake, tmp_path):
    """Run straight from a ZIP, Explorer unpacks pc-tidy into Temp. The Temp fix still runs: only the
    folder pc-tidy is in is left out (M11; before, the whole Temp fix was)."""
    temp = f"{tmp_path}\\{LOCAL}\\Temp".replace("/", "\\")
    ctx = context_for(fake, TEMP=temp)
    ctx.app_dir = temp + "\\Temp1_pc-tidy.zip\\pc-tidy"
    assert safety.temp_problem(ctx) is None
    assert "contains" in safety.why_refused(temp + "\\Temp1_pc-tidy.zip", "delete", ctx)
    assert safety.why_refused(temp + "\\setup-leftovers", "delete", ctx) is None


def test_pc_tidy_exe_is_never_touched_and_neither_is_its_folder(fake, tmp_path):
    temp = f"{tmp_path}\\{LOCAL}\\Temp".replace("/", "\\")
    exe = temp + "\\Temp1_pc-tidy.zip\\pc-tidy.exe"
    ctx = context_for(fake, sandbox=True, TEMP=temp, PCTIDY_TEST_EXE=exe)
    assert safety.why_refused(exe, "delete", ctx) == f"{exe} is pc-tidy itself"
    assert "contains" in safety.why_refused(temp + "\\Temp1_pc-tidy.zip", "delete", ctx)
    assert safety.why_refused(exe, "move", ctx) == f"{exe} is pc-tidy itself"
    assert safety.temp_problem(ctx) is None


def test_a_drive_root_temp_says_so_even_with_pc_tidy_on_that_drive(fake):
    """Seen on CI, where the repository is on D: and a test sets TEMP=D:\\."""
    ctx = context_for(fake, TEMP="D:\\")
    ctx.app_dir = "D:\\a\\pc-tidy\\pc-tidy"
    assert safety.temp_problem(ctx) == "Your TEMP variable points at D:\\, which isn't a temp folder. The Temp fix was skipped."


def test_a_bad_temp_turns_off_the_temp_row(fake, tmp_path):
    packages = f"{tmp_path}\\{LOCAL}\\Packages".replace("/", "\\")
    ctx = context_for(fake, TEMP=packages)
    assert safety.why_refused(packages + "\\app\\settings.dat", "delete", ctx)
    assert safety.why_refused(packages + "\\*", "delete", ctx)
