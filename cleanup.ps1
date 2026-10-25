<#
.SYNOPSIS
Free up space on C:. You choose what to clean in the [cleanup] section of settings.ini.

.DESCRIPTION
cleanup.ps1 cleans what is switched on in [cleanup] (python pc_tidy.py settings edit), usually after
reading the report of disk_report.py. Run it in PowerShell opened as administrator.

It always shows the exact list first: every file and folder it would remove. Nothing changes until
you type yes, and the Recycle Bin and your extra_paths_to_delete ask a second time. Then it removes
only what was on that list, skipping anything that changed since. In extra_paths_to_delete only *
and ? are wildcards: D:\Photos [2019] is that one folder.

This script only checks for an administrator window and finds Python; the work is done by
python pc_tidy.py cleanup, with Python 3.9 or newer: this folder's .venv, the python of the window
(an active conda or venv), or the py launcher. Set PCTIDY_PYTHON to the full path of python.exe to
pick one.

If PowerShell says running scripts is disabled, run this once:
    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

.PARAMETER DryRun
Show the exact list and how big it is. Changes nothing.

.EXAMPLE
.\cleanup.ps1 -DryRun
Shows the exact list of what would go, and changes nothing.

.EXAMPLE
.\cleanup.ps1
Shows the same list, from the disk as it is now, and does it after you type yes.
#>
param(
    [switch]$DryRun,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest
)

if ($Rest) {
    if ($Rest | Where-Object { $_ -in '--help', '-h', '/?', '-help', 'help' }) {
        Get-Help $PSCommandPath -Detailed
        exit 0
    }
    Write-Host "cleanup.ps1 doesn't know $($Rest -join ' '). Use -DryRun, or nothing. For help: .\cleanup.ps1 --help" -ForegroundColor Yellow
    exit 1
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Open PowerShell as administrator and run this again." -ForegroundColor Yellow
    exit 1
}

function Find-Python {
    # The first Python 3.9+ that really runs. The Microsoft Store's python.exe stub only says
    # "Python was not found", so where it is says nothing: a candidate counts only if it runs.
    $candidates = @()
    if ($env:PCTIDY_PYTHON) { $candidates += ,@($env:PCTIDY_PYTHON) }
    $candidates += ,@("$PSScriptRoot\.venv\Scripts\python.exe")
    $candidates += ,@("python")
    $candidates += ,@("py", "-3")
    foreach ($c in $candidates) {
        $exe = $c[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $rest = @($c | Select-Object -Skip 1)
        $answer = & $exe @rest -c "import sys; print(sys.version_info >= (3, 9))" 2>$null
        if ($LASTEXITCODE -eq 0 -and "$answer".Trim() -eq "True") { return ,$c }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Write-Host "cleanup.ps1 runs on Python 3.9 or newer, and none was found." -ForegroundColor Yellow
    Write-Host "Double-click setup.cmd in the pc-tidy folder once (or activate pc-tidy's environment), then run this again."
    exit 1
}
# the script's full path, so this works from any folder (an admin window starts in System32)
$pyArgs = @($python | Select-Object -Skip 1) + @("$PSScriptRoot\pc_tidy.py", "cleanup")
if ($DryRun) { $pyArgs += "--dry-run" }
& $python[0] @pyArgs
exit $LASTEXITCODE
