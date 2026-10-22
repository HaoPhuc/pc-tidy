<#
cleanup.ps1 - free up space on C:. You choose what to clean in the SETTINGS block below,
usually after reading the output of disk_report.py.

Run in PowerShell as administrator, from this folder:
    .\cleanup.ps1 -DryRun    # shows what would be removed and how big it is, changes nothing
    .\cleanup.ps1            # does it (asks you to type yes first)

If PowerShell says running scripts is disabled, run this once:
    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#>
param([switch]$DryRun)

# ============================ SETTINGS: edit these ============================
# $true = clean it, $false = skip it
$CondaCache     = $true    # conda package cache ('conda clean --all'). Needs conda in this window
$PipCache       = $true    # pip download cache
$UvCache        = $true    # uv download cache
$GpuShaderCache = $true    # NVIDIA / AMD / DirectX shader caches. They rebuild themselves
$TempFiles      = $false   # later, once I've looked at what's in there
$RecycleBin     = $true    # empty the Recycle Bin on every drive
$CrashDumps     = $true    # crash dump files (*.dmp) left by Windows and apps
$Hibernation    = $true    # I never hibernate
$Installers     = $true    # installer files (.exe .msi .msix ...) in the folders below.
                           # Check the dry-run list: a portable app that runs straight from
                           # its .exe would be deleted too.

# Folders to delete installers from (top level only, subfolders are left alone).
# Sorted\To-Delete is where tidy_folder.py puts installers.
$InstallerFolders = @("$env:USERPROFILE\Downloads",
                      "$env:USERPROFILE\Installers")   # mine, not Sorted

# conda environments to delete, e.g. @("old-env", "test")
$CondaEnvsToRemove = @("old-env", 'test-env')   # done with these

# Apps to uninstall. Use the exact name from Settings > Apps or from the disk report,
# e.g. @("Docker Desktop", "Origin 2026 SR1")
$AppsToUninstall = @("Docker Desktop")

# Any other files or folders to delete. Wildcards (*) are allowed, e.g.
#   @("C:\Program Files (x86)\Steam\userdata\*\3472040\local\tmp\replay.bin")
$ExtraPathsToDelete = @("${env:ProgramFiles(x86)}\Steam\userdata\*\3472040\local\tmp\replay.bin",
                        "$env:USERPROFILE\old-backups\*.zip")
# ==============================================================================


$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Open PowerShell as administrator and run this again." -ForegroundColor Yellow
    exit 1
}

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

function Remove-Paths([string]$Label, [string[]]$Paths) {
    Write-Step $Label
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

# Folders that ExtraPathsToDelete may never point at, to protect against typos
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
if ($Installers)     { $plan += "installers in: $($InstallerFolders -join ', ')" }
foreach ($e in $CondaEnvsToRemove)  { $plan += "conda env '$e'" }
foreach ($a in $AppsToUninstall)    { $plan += "uninstall '$a'" }
foreach ($p in $ExtraPathsToDelete) { $plan += "delete $p" }

if (-not $plan) {
    Write-Host "Nothing is switched on. Edit the SETTINGS block at the top of cleanup.ps1."
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
if ($TempFiles) { Remove-Paths "Temp files" @("$env:TEMP\*", "$env:SystemRoot\Temp\*") }
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
    $files = @(foreach ($folder in $InstallerFolders) {
        Get-ChildItem -LiteralPath $folder -File -Force -ErrorAction SilentlyContinue |
            Where-Object { $installerTypes -contains $_.Extension.ToLower() }
    })
    $size = [double](($files | Measure-Object -Property Length -Sum).Sum)
    $script:Estimate += $size
    if ($files.Count -eq 0) {
        Write-Host "  none found"
    } else {
        $files | Select-Object -First 40 | ForEach-Object {
            Write-Host ("  {0,8:N0} MB  {1}" -f ($_.Length / 1MB), $_.Name)
        }
        if ($files.Count -gt 40) { Write-Host "  ... and $($files.Count - 40) more" }
        if ($DryRun) {
            Write-Host "  would delete $($files.Count) files, about $(Gb $size)"
        } else {
            $files | Remove-Item -Force -ErrorAction SilentlyContinue
            Write-Host "  deleted $($files.Count) files, about $(Gb $size)"
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
