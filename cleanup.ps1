<#
cleanup.ps1 - free up space on C:. You choose what to clean in the [cleanup] section of settings.ini,
usually after reading the output of disk_report.py:
    python pc_tidy.py settings edit

Run in PowerShell as administrator, from this folder:
    .\cleanup.ps1 -DryRun    # shows what would be removed and how big it is, changes nothing
    .\cleanup.ps1            # does it (asks you to type yes first)

It reads settings.ini with Python 3.9 or newer: this folder's .venv, the python of the window (an
active conda or venv), or the py launcher. Set PCTIDY_PYTHON to the full path of python.exe to pick one.

If PowerShell says running scripts is disabled, run this once:
    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#>
param([switch]$DryRun)

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Open PowerShell as administrator and run this again." -ForegroundColor Yellow
    exit 1
}


# ---------- settings: the [cleanup] section of settings.ini ----------
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
    Write-Host "cleanup.ps1 reads its settings with Python 3.9 or newer, and none was found." -ForegroundColor Yellow
    Write-Host "Install Python (see README.md, step 1) or activate pc-tidy's environment, then run this again."
    exit 1
}
$pyExe = $python[0]
$pyArgs = @($python | Select-Object -Skip 1)
$answer = & $pyExe @pyArgs "$PSScriptRoot\pc_tidy.py" cleanup-settings
$code = $LASTEXITCODE
$s = $null
try { $s = ($answer -join "`n") | ConvertFrom-Json } catch { }
if ($null -eq $s) {
    Write-Host "Couldn't read the settings: python pc_tidy.py cleanup-settings ended with exit code $code." -ForegroundColor Red
    exit 1
}
foreach ($m in @($s.messages)) { if ($m) { Write-Host $m -ForegroundColor Yellow } }
if (-not $s.ok) { exit $code }      # a mistake in [cleanup]: the message above names its line

$CondaCache     = [bool]$s.conda_cache
$PipCache       = [bool]$s.pip_cache
$UvCache        = [bool]$s.uv_cache
$GpuShaderCache = [bool]$s.gpu_shader_cache
$TempFiles      = [bool]$s.temp_files
$RecycleBin     = [bool]$s.recycle_bin
$CrashDumps     = [bool]$s.crash_dumps
$Hibernation    = [bool]$s.hibernation
$Installers     = [bool]$s.installers
$InstallersMinAgeDays = [int]$s.installers_min_age_days
$InstallerFolders   = @($s.installer_folders | Where-Object { $_ })
$CondaEnvsToRemove  = @($s.conda_envs_to_remove | Where-Object { $_ })
$AppsToUninstall    = @($s.apps_to_uninstall | Where-Object { $_ })
$ExtraPathsToDelete = @($s.extra_paths_to_delete | Where-Object { $_ })   # pc-tidy's safety check passed each one
$TempOk = [bool]$s.temp_ok      # false if TEMP points at a drive root or a folder of yours

$script:Estimate = 0.0
$Drive = $env:SystemDrive   # usually C:

function Get-FixedDrives {
    try   { @((Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" -ErrorAction Stop).DeviceID) }
    catch { @($Drive) }
}
$Drives = Get-FixedDrives   # e.g. C:, E:

function Write-Step([string]$Text) { Write-Host "`n== $Text" -ForegroundColor Cyan }
function Gb($Bytes) { "{0:N1} GB" -f ($Bytes / 1GB) }
function Get-FreeBytes([string]$D) { (Get-PSDrive $D.TrimEnd(':')).Free }
function Get-FreeMap { $m = [ordered]@{}; foreach ($d in $Drives) { $m[$d] = Get-FreeBytes $d }; $m }

function Get-SizeBytes([string[]]$Paths) {
    $total = 0.0
    foreach ($p in $Paths) {
        foreach ($item in Get-Item -Path $p -Force -ErrorAction SilentlyContinue) {
            if ($item.PSIsContainer) {
                $sum = (Get-ChildItem -LiteralPath $item.FullName -Recurse -Force -File -ErrorAction SilentlyContinue |
                        Measure-Object -Property Length -Sum).Sum
                $total += [double]$sum
            } else {
                $total += [double]$item.Length
            }
        }
    }
    return $total
}

function Remove-Paths([string]$Label, [string[]]$Paths, [string]$Note) {
    Write-Step $Label
    if ($Note) { Write-Host "  $Note" -ForegroundColor Yellow }
    $size = Get-SizeBytes $Paths
    $script:Estimate += $size
    if ($DryRun) {
        Write-Host "  would remove about $(Gb $size)"
        return
    }
    foreach ($p in $Paths) {
        Remove-Item -Path $p -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Host "  removed about $(Gb $size) (files in use are skipped)"
}

# Folders that ExtraPathsToDelete may never point at. pc-tidy's safety check (python pc_tidy.py
# cleanup-settings) refuses far more, wildcards included; this stays as a second line of defence.
$Protected = @($Drives | ForEach-Object { "$_\" }) + @("$Drive\", $env:SystemRoot, $env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:ProgramData,
               "$Drive\Users", $env:USERPROFILE, "$env:USERPROFILE\Documents", "$env:USERPROFILE\Desktop") |
    Where-Object { $_ } | ForEach-Object { $_.Replace('/', '\').TrimEnd('\').ToLower() }

function Test-Protected([string]$Path) {
    if (-not $Path.Trim()) { return $true }
    foreach ($item in Get-Item -Path $Path -Force -ErrorAction SilentlyContinue) {
        if ($Protected -contains $item.FullName.Replace('/', '\').TrimEnd('\').ToLower()) { return $true }
    }
    return $false
}


# ---------- show the plan ----------
$plan = @()
if ($CondaCache)     { $plan += "conda package cache" }
if ($PipCache)       { $plan += "pip download cache" }
if ($UvCache)        { $plan += "uv download cache" }
if ($GpuShaderCache) { $plan += "GPU shader caches" }
if ($TempFiles)      { $plan += "Temp files" }
if ($RecycleBin)     { $plan += "Recycle Bin" }
if ($CrashDumps)     { $plan += "crash dumps" }
if ($Hibernation)    { $plan += "turn off hibernation" }
if ($Installers)     { $plan += "installers older than $InstallersMinAgeDays days in: $($InstallerFolders -join ', ')" }
foreach ($e in $CondaEnvsToRemove)  { $plan += "conda env '$e'" }
foreach ($a in $AppsToUninstall)    { $plan += "uninstall '$a'" }
foreach ($p in $ExtraPathsToDelete) { $plan += "delete $p" }

if (-not $plan) {
    Write-Host "Nothing is switched on in the [cleanup] section of settings.ini. Change it with: python pc_tidy.py settings edit"
    exit
}
if ($DryRun) { Write-Host "DRY RUN: nothing will be changed. Planned steps:" -ForegroundColor Green }
else         { Write-Host "This will permanently remove:" -ForegroundColor Yellow }
$plan | ForEach-Object { Write-Host "  - $_" }
if (-not $DryRun -and (Read-Host "`nType yes to continue") -ne "yes") {
    Write-Host "Cancelled, nothing changed."
    exit
}
$before = Get-FreeMap


# ---------- conda ----------
$hasConda = [bool](Get-Command conda -ErrorAction SilentlyContinue)
if (($CondaCache -or $CondaEnvsToRemove.Count -gt 0) -and -not $hasConda) {
    Write-Host "`nconda isn't available in this window, so the conda steps are skipped." -ForegroundColor Yellow
}
if ($hasConda) {
    if ($CondaEnvsToRemove.Count -gt 0) {
        $envPaths = (conda env list --json | Out-String | ConvertFrom-Json).envs
    }
    foreach ($name in $CondaEnvsToRemove) {
        Write-Step "Remove conda env '$name'"
        $path = $envPaths | Where-Object { (Split-Path $_ -Leaf) -eq $name } | Select-Object -First 1
        if ($name -eq "base") {
            Write-Host "  'base' can't be removed, skipping" -ForegroundColor Yellow
        } elseif (-not $path) {
            Write-Host "  not found ('conda env list' shows your envs), skipping" -ForegroundColor Yellow
        } elseif ($env:CONDA_DEFAULT_ENV -eq $name) {
            Write-Host "  you are inside it. Run 'conda activate base' first. Skipping" -ForegroundColor Yellow
        } else {
            $size = Get-SizeBytes @($path)
            $script:Estimate += $size
            if ($DryRun) { Write-Host "  would remove $path (about $(Gb $size))" }
            else         { conda remove -n $name --all -y | Out-Host }
        }
    }
    if ($CondaCache) {
        Write-Step "conda package cache"
        if ($DryRun) { conda clean --all --dry-run | Out-Host }
        else         { conda clean --all -y | Out-Host }
    }
}


# ---------- caches and temp files ----------
if ($PipCache) { Remove-Paths "pip download cache" @("$env:LOCALAPPDATA\pip\cache\*") }
if ($UvCache)  { Remove-Paths "uv download cache"  @("$env:LOCALAPPDATA\uv\cache\*") }
if ($GpuShaderCache) {
    Remove-Paths "GPU shader caches" @(
        "$env:LOCALAPPDATA\NVIDIA\DXCache\*", "$env:LOCALAPPDATA\NVIDIA\GLCache\*",
        "$env:LOCALAPPDATA\AMD\DxCache\*", "$env:LOCALAPPDATA\AMD\GLCache\*",
        "$env:LOCALAPPDATA\D3DSCache\*")
}
if ($TempFiles) {
    if ($TempOk) { Remove-Paths "Temp files" @("$env:TEMP\*", "$env:SystemRoot\Temp\*") }
    else {
        Remove-Paths "Temp files" @("$env:SystemRoot\Temp\*") ("Your TEMP variable points at $env:TEMP, which isn't " +
            "a temp folder, so it is left alone. Only $env:SystemRoot\Temp is cleaned.")
    }
}
if ($CrashDumps) {
    Remove-Paths "Crash dumps" @(
        "$env:SystemRoot\LiveKernelReports\*.dmp", "$env:SystemRoot\LiveKernelReports\*\*.dmp",
        "$env:SystemRoot\Minidump\*.dmp", "$env:SystemRoot\MEMORY.DMP",
        "$env:LOCALAPPDATA\CrashDumps\*.dmp")
}
if ($RecycleBin) {
    Write-Step "Recycle Bin on $($Drives -join ', ')"
    $size = Get-SizeBytes @($Drives | ForEach-Object { "$_\`$Recycle.Bin" })
    $script:Estimate += $size
    if ($DryRun) { Write-Host "  would free about $(Gb $size)" }
    else {
        Clear-RecycleBin -Force -ErrorAction SilentlyContinue   # every drive
        Write-Host "  emptied"
    }
}


# ---------- installers ----------
if ($Installers) {
    Write-Step "Installers in $($InstallerFolders -join ', ')"
    $installerTypes = @(".exe", ".msi", ".msix", ".msixbundle", ".appx", ".appxbundle")
    $cutoff = (Get-Date).AddDays(-$InstallersMinAgeDays)    # one you just downloaded may not be installed yet
    $all = @(foreach ($folder in $InstallerFolders) {
        Get-ChildItem -LiteralPath $folder -File -Force -ErrorAction SilentlyContinue |
            Where-Object { $installerTypes -contains $_.Extension.ToLower() }
    })
    $files = @($all | Where-Object { $_.LastWriteTime -lt $cutoff })
    $size = [double](($files | Measure-Object -Property Length -Sum).Sum)
    $script:Estimate += $size
    $newer = $all.Count - $files.Count
    if ($newer -gt 0) { Write-Host "  $newer changed in the last $InstallersMinAgeDays days, left alone (installers_min_age_days)" }
    $noun = if ($files.Count -eq 1) { "file" } else { "files" }
    if ($files.Count -eq 0) {
        Write-Host "  none found"
    } else {
        $files | Select-Object -First 40 | ForEach-Object {
            Write-Host ("  {0,8:N0} MB  {1}" -f ($_.Length / 1MB), $_.Name)
        }
        if ($files.Count -gt 40) { Write-Host "  ... and $($files.Count - 40) more" }
        if ($DryRun) {
            Write-Host "  would delete $($files.Count) $noun, about $(Gb $size)"
        } else {
            $files | Remove-Item -Force -ErrorAction SilentlyContinue
            Write-Host "  deleted $($files.Count) $noun, about $(Gb $size)"
        }
    }
}


# ---------- hibernation ----------
if ($Hibernation) {
    Write-Step "Turn off hibernation"
    $size = Get-SizeBytes @("$Drive\hiberfil.sys")
    $script:Estimate += $size
    if ($DryRun) { Write-Host "  would free about $(Gb $size) (hiberfil.sys)" }
    else         { powercfg /h off; Write-Host "  hibernation is off. Undo with: powercfg /h on" }
}


# ---------- apps ----------
if ($AppsToUninstall.Count -gt 0) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "`nwinget isn't available. Uninstall these in Settings > Apps instead." -ForegroundColor Yellow
    } else {
        foreach ($name in $AppsToUninstall) {
            Write-Step "Uninstall '$name'"
            if ($DryRun) {
                winget list --name $name --exact --accept-source-agreements | Out-Null
                if ($LASTEXITCODE -eq 0) { Write-Host "  found, would uninstall" }
                else { Write-Host "  not found by that exact name. Copy the name from Settings > Apps." -ForegroundColor Yellow }
            } else {
                Write-Host "  (some apps open their own uninstall window, click through it)"
                winget uninstall --name $name --exact
                if ($LASTEXITCODE -ne 0) {
                    Write-Host "  not uninstalled automatically. Remove it in Settings > Apps > Installed apps." -ForegroundColor Yellow
                }
            }
        }
    }
}


# ---------- extra paths ----------
foreach ($p in $ExtraPathsToDelete) {
    if (Test-Protected $p) {
        Write-Step "Delete $p"
        Write-Host "  refused: empty path, or a system or top-level folder" -ForegroundColor Red
        continue
    }
    if (-not (Test-Path -Path $p)) {
        Write-Step "Delete $p"
        Write-Host "  nothing found at that path, skipping" -ForegroundColor Yellow
        continue
    }
    Remove-Paths "Delete $p" @($p)
}


# ---------- summary ----------
if ($DryRun) {
    Write-Host "`nDry run done. About $(Gb $script:Estimate) would be freed, plus what conda clean and the uninstalls free." -ForegroundColor Green
    Write-Host "Run .\cleanup.ps1 without -DryRun to do it."
} else {
    $after = Get-FreeMap
    Write-Host "`nDone. Free space:" -ForegroundColor Green
    foreach ($d in $Drives) {
        Write-Host "  ${d}  $(Gb $before[$d]) -> $(Gb $after[$d]) (+$(Gb ($after[$d] - $before[$d])))" -ForegroundColor Green
    }
    if ($AppsToUninstall.Count -gt 0) { Write-Host "Restart once to finish the uninstalls." }
}
