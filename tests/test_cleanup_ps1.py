"""cleanup.ps1 as it behaves today. Only the dry run is ever run: today's script can't be pointed at
fake system folders, so a real run would clean this PC's own Windows Temp and Recycle Bin."""

import re

import pytest

from support import ROOT, is_admin, snapshot

needs_admin = pytest.mark.skipif(not is_admin(), reason="cleanup.ps1 only runs as administrator")


def section(output, title):
    """The output of one '== <title>' step."""
    m = re.search(r"^== " + re.escape(title) + r"\s*$(.*?)(?=^== |\Z)", output, re.M | re.S)
    assert m, f"no '== {title}' step in:\n{output}"
    return m.group(1)


def cleanup_copy(tmp_path, switches_on=(), extra_paths=()):
    """A copy of cleanup.ps1 with its SETTINGS switches set, for dry runs only."""
    text = (ROOT / "cleanup.ps1").read_text(encoding="utf-8")
    text = re.sub(r"^\$(\w+)(\s*=\s*)\$(?:true|false)",
                  lambda m: f"${m.group(1)}{m.group(2)}${'true' if m.group(1) in switches_on else 'false'}",
                  text, flags=re.M)
    paths = ", ".join(f'"{p}"' for p in extra_paths)
    text = text.replace("$ExtraPathsToDelete = @()", f"$ExtraPathsToDelete = @({paths})")
    copy = tmp_path / "cleanup-copy.ps1"
    copy.write_text(text, encoding="utf-8")
    return copy


@pytest.mark.skipif(is_admin(), reason="this window is elevated")
def test_asks_for_an_admin_window(run):
    result = run.powershell(ROOT / "cleanup.ps1", "-DryRun")
    assert result.returncode == 1
    assert "Open PowerShell as administrator and run this again." in result.output


def user_files(profile):
    """A snapshot of the profile without PowerShell's own startup cache, which powershell.exe writes."""
    shell_cache = "AppData\\Local\\Microsoft"
    return {k: v for k, v in snapshot(profile).items() if not k.startswith(shell_cache)}


@needs_admin
def test_dry_run_changes_nothing(run, profile):
    before = user_files(profile)
    result = run.powershell(ROOT / "cleanup.ps1", "-DryRun")

    assert result.returncode == 0, result.output
    assert "DRY RUN: nothing will be changed" in result.output
    assert "Dry run done." in result.output
    installers = section(result.output, f"Installers in {profile}\\Downloads, {profile}\\Sorted\\To-Delete")
    assert "setup.exe" in installers and "old-setup.msi" in installers
    assert "would delete 2 files" in installers
    assert user_files(profile) == before


@needs_admin
def test_dry_run_with_everything_switched_off(run, tmp_path):
    result = run.powershell(cleanup_copy(tmp_path), "-DryRun")
    assert "Nothing is switched on." in result.output
