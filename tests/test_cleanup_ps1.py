""".\\cleanup.ps1, and python pc_tidy.py cleanup, which it runs: every [cleanup] switch that is yes, as one
plan for the executor. The dry run lists every item, nothing changes without a typed yes, and the
Recycle Bin and your extra paths ask a second time.

Real runs go through pc_tidy.py directly, on the fake profile (no admin needed). In the sandbox, the
Windows folders, the tools (conda, powercfg, winget) and the Recycle Bin are left out, since they
would act on this PC itself; the tests below that run them use a fake Windows folder, stub tools and
a stub Recycle Bin, in this process. cleanup.ps1 itself only runs as administrator.
"""

import os
import sys

import pytest

from pctidy import cleanup, context, executor, settings
from support import ROOT, is_admin, make_junction, snapshot, write

needs_admin = pytest.mark.skipif(not is_admin(), reason="cleanup.ps1 only runs as administrator")

SWITCHES = ("conda_cache", "pip_cache", "uv_cache", "gpu_shader_cache", "temp_files", "recycle_bin",
            "crash_dumps", "hibernation", "installers")
MB = 1024 ** 2


def cleanup_settings(tmp_path, switches_on=(), extra_paths=()):
    """tmp_path/settings.ini with only these [cleanup] switches on."""
    lines = ["[cleanup]"] + [f"{k} = {'yes' if k in switches_on else 'no'}" for k in SWITCHES]
    if extra_paths:
        lines += ["extra_paths_to_delete ="] + [f"    {p}" for p in extra_paths]
    return write(tmp_path / "settings.ini", "\n".join(lines) + "\n")


def user_files(profile):
    """A snapshot of the profile without pc-tidy's own data folder (the saved list and records) and
    PowerShell's startup cache, which powershell.exe writes."""
    skip = ("AppData\\Local\\pc-tidy", "AppData\\Local\\Microsoft")
    return {k: v for k, v in snapshot(profile).items() if not k.startswith(skip)}


def caches(profile):
    """A pip download and a crash dump, next to the fake profile's Temp file and old installer."""
    local = profile / "AppData" / "Local"
    return write(local / "pip" / "cache" / "wheel.whl", b"w" * 100), write(local / "CrashDumps" / "app.dmp", b"d" * 100)


def answers(monkeypatch, *typed):
    """What is typed at pc-tidy's questions in this process. Then the input ends."""
    feed = iter(typed)

    def fake_input(prompt=""):
        print(prompt)
        try:
            return next(feed)
        except StopIteration:
            raise EOFError from None
    monkeypatch.setattr("builtins.input", fake_input)


# ---------- python pc_tidy.py cleanup ----------
def test_the_dry_run_lists_every_item_and_changes_nothing(run, profile):
    pip, dump = caches(profile)
    before = user_files(profile)

    result = run.python("pc_tidy.py", "cleanup", "--dry-run")

    assert result.returncode == 0, result.output
    out = result.stdout
    assert "DRY RUN: nothing will be changed. This is exactly what .\\cleanup.ps1 would do:" in out
    for path in (pip, dump, profile / "Downloads" / "old-setup.msi", profile / "AppData" / "Local" / "Temp" / "tmp1.tmp"):
        assert f"        {path}" in out, out
    assert str(profile / "Downloads" / "setup.exe") not in out      # 1 day old: installers_min_age_days = 7
    assert "Recycle Bin" not in out                                  # off unless you switch it on
    assert "Left out Windows Temp files: not in the tests" in out
    assert "Dry run done. Nothing was changed." in out
    assert user_files(profile) == before


def test_yes_removes_exactly_the_list(run, profile):
    pip, dump = caches(profile)
    temp = profile / "AppData" / "Local" / "Temp"

    result = run.python("pc_tidy.py", "cleanup", input="yes\n")

    assert result.returncode == 0, result.output
    assert not any(p.exists() for p in (pip, dump, temp / "tmp1.tmp", profile / "Downloads" / "old-setup.msi"))
    assert (profile / "Downloads" / "setup.exe").exists()                 # too new
    assert not os.path.lexists(temp / "link-to-precious")
    assert (profile / "Precious" / "keep.txt").read_bytes() == b"k" * 1000   # the link went, never what it points at
    assert temp.is_dir() and pip.parent.is_dir()                          # what is inside them, not the folders
    assert "What was done is recorded in" in result.stdout


@pytest.mark.parametrize("typed", ["no\n", "\n", ""])
def test_anything_but_yes_changes_nothing(run, profile, typed):
    caches(profile)
    before = user_files(profile)

    result = run.python("pc_tidy.py", "cleanup", input=typed)

    assert result.returncode == 0, result.output
    assert "Cancelled, nothing changed." in result.stdout
    assert user_files(profile) == before


def test_a_file_added_or_changed_after_the_dry_run_survives(run, profile):
    temp = profile / "AppData" / "Local" / "Temp"
    listed = [write(temp / f"cache{i}.tmp", b"c" * 100) for i in range(3)]
    started = run.start("pc_tidy.py", "cleanup")
    started.wait_for("Type yes to do all of this")
    added = write(temp / "new.tmp", b"downloading")
    listed[0].write_bytes(b"changed")
    started.send("yes\n")

    assert started.finish() == 3, started.output          # something was skipped
    assert added.exists() and listed[0].read_bytes() == b"changed"
    assert not listed[1].exists() and not listed[2].exists()
    assert "1 skipped: changed since the dry run" in started.output


def test_an_extra_path_asks_a_second_time(run, tmp_path):
    stuff = write(tmp_path / "old-stuff" / "notes.txt", b"old").parent
    cleanup_settings(tmp_path, extra_paths=[stuff])

    result = run.python("pc_tidy.py", "cleanup", input="yes\n\n")      # yes, then Enter at the second question

    assert result.returncode == 0, result.output
    assert f"Delete {stuff} is high risk: it can't be undone. Type yes again" in result.stdout
    assert "Nothing left to do. Nothing was changed." in result.stdout
    assert (stuff / "notes.txt").exists()


def test_a_link_in_an_extra_path_goes_but_never_what_it_points_at(run, profile, tmp_path):
    stuff = write(tmp_path / "old-stuff" / "notes.txt", b"old").parent
    make_junction(stuff / "link-to-precious", profile / "Precious")
    cleanup_settings(tmp_path, extra_paths=[stuff])

    result = run.python("pc_tidy.py", "cleanup", input="yes\nyes\n")

    assert result.returncode == 0, result.output
    assert not stuff.exists()
    assert (profile / "Precious" / "keep.txt").read_bytes() == b"k" * 1000


def test_only_star_and_question_mark_are_wildcards(run, tmp_path):
    old = tmp_path / "old-backups"
    gone = [write(old / "b [2].zip"), write(old / "b [2] copy.zip"), write(old / "c1.log")]
    kept = [write(old / "b 2.zip"), write(old / "b [2].txt"), write(old / "c12.log")]
    cleanup_settings(tmp_path, extra_paths=[f"{old}\\b [2]*.zip", f"{old}\\c?.log"])

    result = run.python("pc_tidy.py", "cleanup", input="yes\nyes\nyes\n")

    assert result.returncode == 0, result.output
    assert not any(f.exists() for f in gone)
    assert all(f.exists() for f in kept), "[2] was read as 'one of the characters 2'"


def test_a_mistake_in_cleanup_stops_it_before_anything(run, profile, tmp_path):
    write(tmp_path / "settings.ini", "[cleanup]\ntemp_files = ture\n")

    result = run.python("pc_tidy.py", "cleanup", input="yes\n")

    assert result.returncode == settings.EXIT, result.output
    assert ".\\cleanup.ps1 didn't start" in result.stdout
    assert "settings.ini line 2 in [cleanup]: temp_files must be yes or no" in result.stdout
    assert "DRY RUN" not in result.stdout
    assert (profile / "AppData" / "Local" / "Temp" / "tmp1.tmp").exists()


def test_extra_paths_are_checked_line_by_line(run, profile, tmp_path):
    old = write(profile / "Documents" / "old drafts" / "a.txt", b"a").parent
    write(tmp_path / "settings.ini", f"[cleanup]\nextra_paths_to_delete =\n    {old}\n    {profile}\\Documents\\*\n")

    result = run.python("pc_tidy.py", "cleanup", "--dry-run")

    assert result.returncode == settings.EXIT, result.output
    assert "line 4 in [cleanup]" in result.stdout
    assert "a wildcard directly under Documents could empty it" in result.stdout
    assert "line 3" not in result.stdout            # a folder inside Documents is fine


def test_with_everything_switched_off(run, tmp_path):
    cleanup_settings(tmp_path)
    result = run.python("pc_tidy.py", "cleanup", "--dry-run")
    assert result.returncode == 0, result.output
    assert "Nothing is switched on in the [cleanup] section of settings.ini." in result.stdout


# ---------- in this process, with stand-ins for what would act on this PC ----------
def test_the_windows_folders_lose_only_what_the_safety_table_allows(monkeypatch, tmp_path, profile, capsys):
    windows = tmp_path / "Windows"
    gone = [write(windows / "Temp" / "setup.log"), write(windows / "Minidump" / "a.dmp"), write(windows / "MEMORY.DMP"),
            write(windows / "LiveKernelReports" / "WATCHDOG" / "w.dmp")]
    kept = [write(windows / "notepad.exe"), write(windows / "Minidump" / "notes.txt"), windows / "Temp"]
    cleanup_settings(tmp_path, switches_on={"temp_files", "crash_dumps"})
    answers(monkeypatch, "yes")

    code = cleanup.run(ctx=context.Context(dict(os.environ, SystemRoot=str(windows))))

    out = capsys.readouterr().out
    assert code == 0, out
    assert not any(p.exists() for p in gone), out
    assert all(p.exists() for p in kept)


@pytest.mark.parametrize("typed, emptied", [(("yes", ""), 0), (("yes", "yes"), 1), (("yes", "y"), 0)])
def test_the_recycle_bin_asks_a_second_time(monkeypatch, tmp_path, profile, capsys, typed, emptied):
    calls = []
    monkeypatch.setitem(executor.COUNT_ONLY, "empty_recycle_bin",
                        ("Recycle Bin", lambda _a=None: {"items": 3, "bytes": 3 * MB}, lambda _a=None: calls.append(1)))
    monkeypatch.setattr(cleanup, "_why_not_here", lambda ctx, row: None)
    cleanup_settings(tmp_path, switches_on={"recycle_bin"})
    answers(monkeypatch, *typed)

    code = cleanup.run()

    out = capsys.readouterr().out
    assert code == 0, out
    assert "Recycle Bin: empty the Recycle Bin on every drive, 3 items, 3 MB. This can't be undone." in out
    assert "Recycle Bin is high risk: it can't be undone. Type yes again" in out
    assert len(calls) == emptied


def test_a_tool_runs_through_the_executor_with_its_input_closed(monkeypatch, tmp_path, profile, capsys):
    log = tmp_path / "powercfg.log"
    stub = [sys.executable, "-c", f"import sys; open(r'{log}', 'w').write(repr(sys.stdin.read()))"]
    monkeypatch.setitem(executor.TOOLS, "hibernation_off", executor.Tool("powercfg", lambda a: stub, False, 60, None))
    monkeypatch.setattr(cleanup, "_why_not_here", lambda ctx, row: None)
    cleanup_settings(tmp_path, switches_on={"hibernation"})
    answers(monkeypatch, "yes")

    code = cleanup.run()

    out = capsys.readouterr().out
    assert code == 0, out
    assert "Turn off hibernation: done" in out
    assert log.read_text() == "''"          # its input is closed, so it can never wait for an answer


def test_the_lists_become_fixes_and_the_tests_leave_tools_out(monkeypatch, tmp_path, profile):
    stuff = tmp_path / "old-stuff"
    write(tmp_path / "settings.ini", "[cleanup]\nconda_cache = yes\nrecycle_bin = yes\nconda_envs_to_remove =\n"
                                     "    old-env\napps_to_uninstall =\n    Docker Desktop\nextra_paths_to_delete =\n"
                                     f"    {stuff}\n")
    monkeypatch.setattr(cleanup.shutil, "which", lambda name: f"C:\\stub\\{name}.exe")
    ctx = context.current()

    notes = []
    rows = {r["id"]: r for r in cleanup.rows(ctx, settings.load(ctx), notes)}

    assert rows["conda-pkgs"]["action"] == {"tool": "conda_clean"}
    assert rows["conda-env:old-env"]["action"] == {"tool": "conda_env_remove", "name": "old-env"}
    assert rows["app:Docker Desktop"]["action"] == {"tool": "winget_uninstall", "name": "Docker Desktop"}
    assert executor.TOOLS["winget_uninstall"].interactive         # runs last, with the lock released
    assert rows["recycle-bin"]["risk"] == rows[f"extra:{stuff}"]["risk"] == "High"
    for name in ("conda-pkgs", "conda-env:old-env", "app:Docker Desktop", "recycle-bin"):
        assert cleanup._why_not_here(ctx, rows[name]) == cleanup.NOT_IN_TESTS
    assert cleanup._why_not_here(ctx, rows[f"extra:{stuff}"]) is None


# ---------- cleanup.ps1 itself ----------
@pytest.mark.skipif(is_admin(), reason="this window is elevated")
def test_asks_for_an_admin_window(run):
    result = run.powershell(ROOT / "cleanup.ps1", "-DryRun")
    assert result.returncode == 1
    assert "Open PowerShell as administrator and run this again." in result.output


@needs_admin
def test_dry_run_changes_nothing(run, profile, tmp_path):
    caches(profile)
    before = user_files(profile)

    result = run.powershell(ROOT / "cleanup.ps1", "-DryRun")

    assert result.returncode == 0, result.output
    assert "DRY RUN: nothing will be changed" in result.output
    assert f"        {profile}\\Downloads\\old-setup.msi" in result.output
    assert "Dry run done." in result.output
    assert (tmp_path / "settings.ini").exists()          # made from the template, as any command does
    assert user_files(profile) == before


@needs_admin
def test_a_real_run_does_the_list_after_yes(run, tmp_path):
    meant = write(tmp_path / "backups" / "old[1]" / "a.txt", b"delete me").parent
    other = write(tmp_path / "backups" / "old1" / "b.txt", b"keep me").parent
    cleanup_settings(tmp_path, extra_paths=[meant])

    result = run.powershell(ROOT / "cleanup.ps1", input="yes\nyes\n")

    assert result.returncode == 0, result.output
    assert "DRY RUN: this is exactly what will happen. Nothing has changed yet." in result.output
    assert not meant.exists()
    assert (other / "b.txt").read_bytes() == b"keep me"


@needs_admin
def test_a_mistake_in_cleanup_stops_cleanup_ps1_with_its_exit_code(run, tmp_path):
    write(tmp_path / "settings.ini", "[cleanup]\ntemp_files = ture\n")
    result = run.powershell(ROOT / "cleanup.ps1", "-DryRun")

    assert result.returncode == settings.EXIT, result.output
    assert "settings.ini line 2 in [cleanup]: temp_files must be yes or no" in result.output
    assert "DRY RUN" not in result.output


@needs_admin
@pytest.mark.skipif((ROOT / ".venv").exists(), reason="this checkout has a .venv, which cleanup.ps1 would use")
def test_says_so_when_there_is_no_python(run):
    system32 = os.path.join(os.environ["SystemRoot"], "System32")
    result = run.powershell(ROOT / "cleanup.ps1", "-DryRun", extra_env={
        "PCTIDY_PYTHON": "C:\\no-such-folder\\python.exe",
        "PATH": f"{system32};{system32}\\WindowsPowerShell\\v1.0"})

    assert result.returncode == 1, result.output
    assert "cleanup.ps1 runs on Python 3.9 or newer, and none was found." in result.output
