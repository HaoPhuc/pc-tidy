# pc-tidy

Find out what is filling up your Windows PC, clean it up safely, and keep your Downloads folder sorted. A small AI model runs **locally** on your PC through [Ollama](https://ollama.com), so none of your files or file names leave your computer.

| Tool | What it does | Changes files? |
|---|---|---|
| `disk_report.py` | Scans C: and lists the biggest folders and files, what you can clean up, and your installed apps by size. The local AI describes big folders it doesn't recognize. | No, read-only |
| `cleanup.ps1` | Cleans what you switch on in its settings: caches, temp files, crash dumps, old conda envs, apps, and any paths you add. | Yes, after a dry run and your confirmation |
| `tidy_downloads.py` | Sorts Downloads into folders by type. The local AI reads unclear PDFs and Word docs to pick a folder and a better name. | Moves only, never deletes, and can undo |

Made for Windows 10 and 11.

---

## Walkthrough

### 1. Install the tools (once)

Open **PowerShell** (press the Windows key and type `PowerShell`), then run these one at a time. Paste with Ctrl+V **or** right-click, not both, or the command gets pasted twice.

```powershell
winget install --id Git.Git -e                      # skip if you already have git
irm https://ollama.com/install.ps1 | iex            # installs Ollama (or download it from ollama.com)
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned # lets PowerShell run scripts like cleanup.ps1
```

You also need Python 3.9 or newer. If you already have Anaconda or Miniconda, use that. Otherwise install Python from [python.org](https://www.python.org/downloads/) and tick **"Add python.exe to PATH"** in the installer.

Close PowerShell and open it again so it picks up the new installs.

### 2. Download the code

Go to the folder where you keep projects, then clone:

```powershell
cd ~
git clone https://github.com/<username>/pc-tidy.git
cd pc-tidy
```

Your prompt should now end in `\pc-tidy>`. Every command below is run from this folder.

> **Tip:** to `cd` into any folder, type `cd ` (with a space), drag the folder from File Explorer into the PowerShell window, and press Enter.

### 3. Set up Python and the AI model

With conda:

```powershell
conda create -n pc-tidy python=3.12 -y
conda activate pc-tidy
pip install -r requirements.txt
```

Without conda:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Then download the AI model (about 2 GB):

```powershell
ollama pull llama3.2:3b
```

Each time you open a new PowerShell window later, run `conda activate pc-tidy` (or `.venv\Scripts\Activate.ps1`) again.

### 4. See what is using your disk

Open PowerShell **as administrator** (right-click PowerShell > *Run as administrator*) so the scan can look inside protected folders. It starts in `C:\Windows\System32`, so go back to the project first:

```powershell
cd <path to pc-tidy>
conda activate pc-tidy
python disk_report.py --out report.txt
```

The scan takes a few minutes and shows a running file count. The report has these sections:

- **Biggest folders**: a tree of everything over 1 GB.
- **Biggest files**: often `hiberfil.sys`, `pagefile.sys`, virtual disks, videos.
- **Things you can clean up**: items the script recognizes, each with what to do. Many say `cleanup.ps1: <Setting>`, which is the setting that cleans it.
- **Good to know**: things to leave alone, such as Windows, the recovery partition, and the page file.
- **What the LLM thinks these unknown folders are**: short descriptions from the local AI. It is a small model and can be wrong. Never delete something just because of this section.
- **Installed apps by size**: their exact names, which you can copy into `cleanup.ps1`.

`report.txt` contains your own folder names, so `.gitignore` keeps it out of git.

### 5. Clean up

Open `cleanup.ps1` in any text editor (Notepad works) and edit the **SETTINGS** block at the top:

```powershell
$CondaCache     = $true    # conda package cache
$PipCache       = $true    # pip download cache
$UvCache        = $true    # uv download cache
$GpuShaderCache = $true    # NVIDIA / AMD / DirectX shader caches
$TempFiles      = $true    # Temp folders
$RecycleBin     = $true    # empty the Recycle Bin
$CrashDumps     = $true    # *.dmp crash dumps
$Hibernation    = $false   # turn off hibernation, frees C:\hiberfil.sys
$Installers     = $true    # installers (.exe, .msi ...) in Downloads and Sorted\To-Delete

$CondaEnvsToRemove  = @("old-env")
$AppsToUninstall    = @("Docker Desktop")          # exact names from the report or Settings > Apps
$ExtraPathsToDelete = @("D:\old-backups\*.zip")    # anything else, wildcards allowed
```

Then, in the administrator PowerShell:

```powershell
.\cleanup.ps1 -DryRun   # shows each step and how much it would free, changes nothing
.\cleanup.ps1           # does it, after you type yes
```

Safety features:
- The dry run checks that every conda env and app name exists before you commit.
- `ExtraPathsToDelete` refuses drive roots, Windows, Program Files, your user folder, Documents and Desktop.
- Files that are in use are skipped instead of forced.
- The dry run lists every installer it would delete. Check it for portable apps, which run straight from their `.exe`, before running it for real.
- Some uninstallers open their own window, so click through those. Restart once afterwards.

### 6. Tidy your Downloads

This doesn't need administrator rights.

```powershell
python tidy_downloads.py            # preview: prints every planned move, changes nothing
python tidy_downloads.py --apply    # moves files into C:\Users\<you>\Sorted
python tidy_downloads.py --undo     # puts the last run back
```

- **Rules handle most files by extension.** Images go to `Pictures`, installers to `To-Delete`, and so on. The `Installers` setting in `cleanup.ps1` deletes them, since once an app is installed you don't need its installer. Files older than 60 days go under `Sorted\Archive`.
- **The local AI handles the rest.** PDFs, Word docs and unknown types are sorted into `Coursework`, `Research papers`, `Admin and receipts`, `Work`, `Personal`, `Software` or `Other`, and get a clearer name.
- **Some files are left alone:** files still downloading or changed in the last 10 minutes.

To use different folders, pass `--source` and `--dest`, for example `python tidy_downloads.py --source D:\Downloads --dest D:\Sorted`, or change `SOURCE` and `DEST` at the top of the file.

### 7. Optional: tidy Downloads automatically every week

In PowerShell, from the project folder with the environment active:

```powershell
$action  = New-ScheduledTaskAction -Execute (Get-Command python).Source -Argument "tidy_downloads.py --apply" -WorkingDirectory (Get-Location).Path
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 6pm
Register-ScheduledTask -TaskName "pc-tidy Downloads" -Action $action -Trigger $trigger
```

It runs while you are logged in. Every run is logged, so `python tidy_downloads.py --undo` still works. To remove the task, run `Unregister-ScheduledTask -TaskName "pc-tidy Downloads"`.

---

## Customizing

| File | What to change |
|---|---|
| `llm.py` | `MODEL`: any Ollama model, for example a bigger one like `qwen2.5:7b` for better descriptions |
| `disk_report.py` | `RULES`: recognized folders and the advice for each. `MIN_GB` and `MAX_DEPTH` control the tree |
| `cleanup.ps1` | The SETTINGS block |
| `tidy_downloads.py` | `SOURCE`, `DEST`, `RULES` (extension to folder), `CATEGORIES` (folders the AI picks from), `ARCHIVE_AFTER_DAYS` |

## Troubleshooting

| Problem | Fix |
|---|---|
| `Error: accepts 1 arg(s), received 3`, or a command looks doubled | It was pasted twice. Right-click also pastes in the terminal, so use only one of the two. |
| `Could not open requirements file` | You're not in the project folder. `cd` into `pc-tidy` first (see the tip in step 2). |
| `running scripts is disabled on this system` | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once. |
| `Ollama isn't running` | Start Ollama from the Start menu. The scripts still work without it, just without the AI parts. |
| `Model llama3.2:3b not found` | `ollama pull llama3.2:3b` |
| `conda` not found in Git Bash | Run `conda init bash` once in PowerShell, then reopen Git Bash. Or just use PowerShell. |
| `N folders could not be opened` | Run PowerShell as administrator. A few system folders stay locked even then, which is normal. |
| The `C:\Windows` size looks too big | Windows uses hard links, so files get counted more than once. Clean it only with Disk Cleanup > *Clean up system files*. |

## How it works

- **Rules first, AI second.** Plain rules handle everything predictable. The AI is used only where rules can't decide: reading a PDF to sort it, or describing an unfamiliar folder.
- **The AI never decides what gets deleted.** Its descriptions are labeled as guesses, and `cleanup.ps1` only does what you switch on.
- **Small and local.** `llama3.2:3b` runs on most laptops without a GPU and nothing is sent online.

## License

MIT, see [LICENSE](LICENSE).
