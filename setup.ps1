<#
.SYNOPSIS
Sets pc-tidy up on this PC. Double-click setup.cmd, which runs this. Safe to run again.

.DESCRIPTION
1. checks that this is Windows 10 or 11
2. finds Python 3.9 or newer that really runs (the Microsoft Store's python.exe stub doesn't count)
3. warns if the pc-tidy folder is in a folder OneDrive syncs (settings.ini would be uploaded)
4. makes .venv in this folder and installs the packages pc-tidy needs (requirements.txt)
5. unblocks pc-tidy's own scripts (a ZIP download marks them as from the internet)
6. makes settings.ini, or brings in the settings of ..\pc-tidy-old (a version before 2.0), after a yes
7. checks for the local AI (Ollama) and offers to download its model (about 2 GB), after a yes
8. runs python pc_tidy.py selftest

It changes no setting of Windows or PowerShell: setup.cmd runs it with -ExecutionPolicy Bypass for
this one window only.

.PARAMETER NonInteractive
Ask nothing (for CI): no import, no model download.
#>
param([switch]$NonInteractive)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Step([string]$Text) { Write-Host "`n== $Text" -ForegroundColor Cyan }
function Fail([string]$Text) {
    Write-Host "  $Text" -ForegroundColor Red
    Write-Host "`npc-tidy isn't set up yet. Fix that, then double-click setup.cmd again."
    exit 1
}
function Ask([string]$Question) {
    if ($NonInteractive) { return $false }
    return (Read-Host "  $Question Type yes or press Enter to skip") -eq "yes"
}
function Test-Python([string[]]$Command) {
    # a Python counts only if it runs and is 3.9 or newer
    $exe = $Command[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { return $false }
    $rest = @($Command | Select-Object -Skip 1)
    $answer = & $exe @rest -c "import sys; print(sys.version_info >= (3, 9))" 2>$null
    return ($LASTEXITCODE -eq 0 -and "$answer".Trim() -eq "True")
}

Step "Windows"
$build = [Environment]::OSVersion.Version.Build
if ([Environment]::OSVersion.Version.Major -lt 10) { Fail "pc-tidy needs Windows 10 or 11." }
$caption = (Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue).Caption
Write-Host "  $caption (build $build)"
if ($caption -match "Server") { Write-Host "  This is Windows Server: pc-tidy is made for Windows 10 and 11." -ForegroundColor Yellow }

Step "Python"
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$python = $null
foreach ($c in @(, @($venvPython)) + @(, @("py", "-3")) + @(, @("python"))) {
    if (Test-Python $c) { $python = $c; break }
}
if (-not $python) {
    Write-Host "  Python 3.9 or newer isn't installed (or only the Microsoft Store's placeholder is)." -ForegroundColor Yellow
    Write-Host "  Install it from python.org, and tick 'Add python.exe to PATH' in the installer."
    if (-not $NonInteractive) { Start-Process "https://www.python.org/downloads/windows/" }
    Fail "Python is missing."
}
$pyExe = $python[0]
$pyArgs = @($python | Select-Object -Skip 1)
Write-Host "  $(& $pyExe @pyArgs -c 'import sys; print(sys.version.split()[0], sys.executable)')"

Step "OneDrive"
$here = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$synced = @($env:OneDrive, $env:OneDriveConsumer, $env:OneDriveCommercial) | Where-Object { $_ } |
    Where-Object { $here.StartsWith($_.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) } | Select-Object -First 1
if ($synced) {
    Write-Host "  pc-tidy is in $synced, which OneDrive syncs, so your settings.ini would be uploaded with it." -ForegroundColor Yellow
    Write-Host "  Better: move the pc-tidy folder to, for example, C:\pc-tidy, then run setup again."
    if (-not $NonInteractive -and -not (Ask "Go on here anyway?")) { exit 1 }
} else {
    Write-Host "  The pc-tidy folder isn't synced by OneDrive."
}

Step "The packages pc-tidy needs (.venv)"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "  Making .venv ..."
    & $pyExe @pyArgs -m venv (Join-Path $PSScriptRoot ".venv")
    if ($LASTEXITCODE -ne 0) { Fail "Python couldn't make .venv." }
}
& $venvPython -m pip install --disable-pip-version-check --quiet -r (Join-Path $PSScriptRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { Fail "pip couldn't install the packages in requirements.txt (is this PC online?)." }
Write-Host "  Installed: reportlab, pypdf, python-docx."

Step "Unblock pc-tidy's scripts"
Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -Include *.ps1, *.cmd -File | Unblock-File
Write-Host "  Done (a ZIP download marks them as from the internet, which PowerShell would refuse)."

Step "settings.ini"
$old = Join-Path (Split-Path -Parent $PSScriptRoot) "pc-tidy-old"
if ((Test-Path -LiteralPath $old) -and -not $NonInteractive -and (Ask "Bring in the settings of $old (a version before 2.0)?")) {
    & $venvPython (Join-Path $PSScriptRoot "pc_tidy.py") settings import $old
} else {
    & $venvPython (Join-Path $PSScriptRoot "pc_tidy.py") settings check
}

Step "The local AI (optional)"
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    $model = (& $venvPython -c "from pctidy import settings; print(settings.load().value('ai', 'model'))").Trim()
    $have = (& ollama list 2>$null | Out-String)
    if ($have -match [regex]::Escape($model)) {
        Write-Host "  Ollama has $model."
    } elseif (Ask "Download the AI model $model (about 2 GB)?") {
        & ollama pull $model
    } else {
        Write-Host "  Skipped. pc-tidy works without it, just without the AI's descriptions. Later: ollama pull $model"
    }
} else {
    Write-Host "  Ollama isn't installed. pc-tidy works without it; see README.md, step 1, to add it."
}

Step "Self-test"
& $venvPython (Join-Path $PSScriptRoot "pc_tidy.py") selftest
if ($LASTEXITCODE -ne 0) { Fail "The self-test found a problem (above)." }
Write-Host "`npc-tidy is ready. Double-click pc-tidy.cmd to start." -ForegroundColor Green
exit 0
