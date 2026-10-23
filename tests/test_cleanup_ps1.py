"""cleanup.ps1: what it cleans comes from the [cleanup] section of settings.ini, read and checked by
python pc_tidy.py cleanup-settings.

Only dry runs are ever run: cleanup.ps1 can't be pointed at fake system folders, so a real run would
clean this PC's own Windows Temp and Recycle Bin. Runner sets PCTIDY_SANDBOX_ROOT, so the settings.ini
it reads (and makes from the template if it is missing) is the one in the test's tmp_path.
"""

import json
import os
import re

import pytest

from pctidy import settings
from support import ROOT, is_admin, snapshot, write

needs_admin = pytest.mark.skipif(not is_admin(), reason="cleanup.ps1 only runs as administrator")

SWITCHES = ("conda_cache", "pip_cache", "uv_cache", "gpu_shader_cache", "temp_files", "recycle_bin",
            "crash_dumps", "hibernation", "installers")


def section(output, title):
    """The output of one '== <title>' step."""
    m = re.search(r"^== " + re.escape(title) + r"\s*$(.*?)(?=^== |\Z)", output, re.M | re.S)
    assert m, f"no '== {title}' step in:\n{output}"
    return m.group(1)


def cleanup_settings(tmp_path, switches_on=(), extra_paths=()):
    """tmp_path/settings.ini with only these [cleanup] switches on."""
    lines = ["[cleanup]"] + [f"{k} = {'yes' if k in switches_on else 'no'}" for k in SWITCHES]
    if extra_paths:
        lines += ["extra_paths_to_delete ="] + [f"    {p}" for p in extra_paths]
    return write(tmp_path / "settings.ini", "\n".join(lines) + "\n")


def user_files(profile):
    """A snapshot of the profile without PowerShell's own startup cache, which powershell.exe writes."""
    shell_cache = "AppData\\Local\\Microsoft"
    return {k: v for k, v in snapshot(profile).items() if not k.startswith(shell_cache)}


# ---------- python pc_tidy.py cleanup-settings (no admin needed) ----------
def cleanup_json(run, **env):
    result = run.python("pc_tidy.py", "cleanup-settings", env=env)
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1, result.output       # one line of JSON, nothing else
    assert lines[0].isascii()                    # PowerShell reads it in any code page
    return result, json.loads(lines[0])


def test_cleanup_settings_are_the_checked_values(run, profile, tmp_path):
    result, s = cleanup_json(run)

    assert result.returncode == 0 and s["ok"], result.output
    assert (tmp_path / "settings.ini").exists()          # made from the template, in the sandbox
    assert s["installer_folders"] == [str(profile / "Downloads"), str(profile / "Sorted" / "To-Delete")]
    assert s["temp_files"] is True and s["hibernation"] is False
    assert s["installers_min_age_days"] == 7
    assert s["extra_paths_to_delete"] == [] and s["temp_ok"] is True


def test_cleanup_settings_refuse_a_mistake_in_cleanup(run, tmp_path):
    write(tmp_path / "settings.ini", "[cleanup]\npip_cahce = no\n[tidy]\narchive_after_days = 60\n")

    result, s = cleanup_json(run)

    assert result.returncode == settings.EXIT
    assert s["ok"] is False
    text = "\n".join(s["messages"])
    assert ".\\cleanup.ps1 didn't start" in text
    assert "settings.ini line 2 in [cleanup]: unknown key 'pip_cahce'. Did you mean 'pip_cache'?" in text


def test_cleanup_settings_say_when_temp_is_not_a_temp_folder(run, profile):
    documents = str(profile / "Documents")
    _result, s = cleanup_json(run, TEMP=documents, TMP=documents)
    assert s["ok"] and s["temp_ok"] is False


def test_cleanup_settings_check_extra_paths(run, profile, tmp_path):
    old = write(profile / "Documents" / "old drafts" / "a.txt", b"a").parent
    write(tmp_path / "settings.ini", f"[cleanup]\nextra_paths_to_delete =\n    {old}\n    {profile}\\Documents\\*\n")

    result, s = cleanup_json(run)

    assert result.returncode == settings.EXIT, result.output
    text = "\n".join(s["messages"])
    assert "line 4 in [cleanup]" in text and "a wildcard directly under Documents could empty it" in text
    assert "line 3" not in text            # a folder inside Documents is fine


# ---------- cleanup.ps1 itself ----------
@pytest.mark.skipif(is_admin(), reason="this window is elevated")
def test_asks_for_an_admin_window(run):
    result = run.powershell(ROOT / "cleanup.ps1", "-DryRun")
    assert result.returncode == 1
    assert "Open PowerShell as administrator and run this again." in result.output


@needs_admin
def test_dry_run_changes_nothing(run, profile, tmp_path):
    before = user_files(profile)
    result = run.powershell(ROOT / "cleanup.ps1", "-DryRun")

    assert result.returncode == 0, result.output
    assert "DRY RUN: nothing will be changed" in result.output
    assert "Dry run done." in result.output
    assert (tmp_path / "settings.ini").exists()          # made from the template, as any command does
    installers = section(result.output, f"Installers in {profile}\\Downloads, {profile}\\Sorted\\To-Delete")
    assert "old-setup.msi" in installers                 # 90 days old
    assert "setup.exe" not in installers                 # 1 day old: installers_min_age_days = 7
    assert "1 changed in the last 7 days, left alone" in installers
    assert "would delete 1 file," in installers
    assert user_files(profile) == before


@needs_admin
def test_dry_run_with_everything_switched_off(run, tmp_path):
    cleanup_settings(tmp_path)
    result = run.powershell(ROOT / "cleanup.ps1", "-DryRun")
    assert result.returncode == 0, result.output
    assert "Nothing is switched on in the [cleanup] section of settings.ini." in result.output


@needs_admin
def test_a_mistake_in_cleanup_stops_it_before_anything(run, tmp_path):
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
    assert "cleanup.ps1 reads its settings with Python 3.9 or newer, and none was found." in result.output
