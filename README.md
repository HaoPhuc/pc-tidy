# pc-tidy

Find out what is filling up your Windows PC, clean it up safely, move big files off C:, and keep messy folders sorted. A small AI model runs **locally** on your PC through [Ollama](https://ollama.com), so none of your files or file names leave your computer.

| Tool | What it does | Changes files? |
|---|---|---|
| `disk_report.py` | Scans every drive and saves a PDF report: where the space goes by category, what you can clean up, what could move off C:, installed apps by type, and the biggest folders and files. | No, read-only |
| `cleanup.ps1` | Cleans what you switch on in its settings: caches, temp files, crash dumps, old installers, conda envs, apps, and any paths you add. | Yes, after a dry run and your confirmation |
| `move_off_c.py` | Moves big personal folders and files from C: to another drive. It copies, checks the copy, then deletes. | Moves only, and can undo |
| `tidy_folder.py` | Sorts a folder (Downloads by default) into subfolders by type. The local AI reads unclear PDFs and Word docs, and can sort whole subfolders. | Moves only, never deletes, and can undo |

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
git clone https://github.com/HaoPhuc/pc-tidy.git
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

### 4. See what is using your drives

Open PowerShell **as administrator** (right-click PowerShell > *Run as administrator*) so the scan can look inside protected folders. It starts in `C:\Windows\System32`, so go back to the project first:

```powershell
cd <path to pc-tidy>
conda activate pc-tidy
python disk_report.py
```

It scans every drive (a few minutes each), saves `disk_report.pdf` and opens it. The report has these sections:

- **Drives**: used and free space on each drive.
- **Where the space goes**: everything sorted into system, apps, games, dev and science tools, caches and temp, and personal files.
- **Things you can clean up**: items the script recognizes, each with what to do. Many say `cleanup.ps1: <Setting>`, which is the setting that cleans it.
- **Could move to another drive**: big personal folders and files on C: that `move_off_c.py` can move, plus tips for things that need another method (Steam games, OneDrive, whole user folders).
- **Installed apps by type**: grouped into dev tools, games, browsers, communication, office and so on, with their exact names for `cleanup.ps1`.
- **Biggest folders** for each drive, and **biggest files**.
- **Good to know**: things to leave alone, such as Windows, the recovery files, and the page file.
- **What the local AI thinks these folders are**: short descriptions of big folders no rule recognizes. It is a small model and can be wrong. Never delete something just because of this section.

Options: `--path C:\` scans one drive only, `--out report.txt` writes plain text instead (give `--out` twice for both), and `--no-open` doesn't open the PDF. The report contains your own folder names, so `.gitignore` keeps it out of git.

### 5. Clean up

Open `cleanup.ps1` in any text editor (Notepad works) and edit the **SETTINGS** block at the top:

```powershell
$CondaCache     = $true    # conda package cache
$PipCache       = $true    # pip download cache
$UvCache        = $true    # uv download cache
$GpuShaderCache = $true    # NVIDIA / AMD / DirectX shader caches
$TempFiles      = $true    # Temp folders
$RecycleBin     = $true    # empty the Recycle Bin on every drive
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

At the end it shows the free space on every drive, before and after.

### 6. Move big files off C:

If C: is full but another drive has room, move your own big folders and files there. This doesn't need administrator rights.

```powershell
python move_off_c.py                                        # lists candidates, you pick by number
python move_off_c.py --path "C:\Users\<you>\Videos\Lectures" # move one specific folder or file
python move_off_c.py --undo                                 # move the last batch back
```

- By default things go to `<drive with the most free space>:\From-C`, keeping the same folder structure. Use `--to E:\Archive` to choose.
- Each item is copied, the copy is checked, and only then is the original deleted. If a copy fails, the original stays and the partial copy is removed.
- Only folders and files inside your user profile are offered. Apps, games and system files break when moved by hand, so the script points you to the right way instead: Steam > Settings > Storage for games, right-click > Properties > Location for a whole Documents or Videos folder, and "Free up space" for OneDrive.

### 7. Tidy a folder

This doesn't need administrator rights.

```powershell
python tidy_folder.py               # preview Downloads: prints every planned move, changes nothing
python tidy_folder.py --apply       # moves files into C:\Users\<you>\Sorted
python tidy_folder.py --undo        # puts the last run back
```

- **Rules handle most files by extension.** Images go to `Pictures`, installers to `To-Delete`, and so on. The `Installers` setting in `cleanup.ps1` deletes them, since once an app is installed you don't need its installer. Files older than 60 days go under `Sorted\Archive`.
- **The local AI handles the rest.** PDFs, Word docs and unknown types are sorted into `Coursework`, `Research papers`, `Projects and code`, `Admin and receipts`, `Work`, `Personal`, `Software` or `Other`, and get a clearer name.
- **Some files are left alone:** shortcuts, and files still downloading or changed in the last 10 minutes.

Any folder works, not just Downloads:

```powershell
python tidy_folder.py --source "$env:USERPROFILE\Desktop" --in-place --apply   # sort the Desktop inside itself
python tidy_folder.py --source D:\Stuff --dest D:\Sorted --folders            # also sort whole subfolders
```

`--in-place` puts the sorted folders inside the folder you're tidying. `--folders` also moves whole subfolders into a category, after the local AI looks at their name and some of the file names inside. Folders keep their contents as they are. Use the same options plus `--undo` to put a run back.

### 8. Optional: tidy Downloads automatically every week

In PowerShell, from the project folder with the environment active:

```powershell
$action  = New-ScheduledTaskAction -Execute (Get-Command python).Source -Argument "tidy_folder.py --apply" -WorkingDirectory (Get-Location).Path
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 6pm
Register-ScheduledTask -TaskName "pc-tidy Downloads" -Action $action -Trigger $trigger
```

It runs while you are logged in. Every run is logged, so `python tidy_folder.py --undo` still works. To remove the task, run `Unregister-ScheduledTask -TaskName "pc-tidy Downloads"`.

---

## Customizing

| File | What to change |
|---|---|
| `llm.py` | `MODEL`: any Ollama model, for example a bigger one like `qwen2.5:7b` for better descriptions |
| `classify.py` | `_RULES` (recognized folders and advice), `_CATEGORY_RULES` (which folder counts as which category), `_APP_RULES` (app types by keyword) |
| `disk_report.py` | `MIN_GB` and `MAX_DEPTH` for the folder tree, how many items the AI describes |
| `cleanup.ps1` | The SETTINGS block |
| `move_off_c.py` | `MIN_GB`, `DEST_FOLDER_NAME` |
| `tidy_folder.py` | `SOURCE`, `DEST`, `RULES` (extension to folder), `CATEGORIES` (folders the AI picks from), `ARCHIVE_AFTER_DAYS` |

## Troubleshooting

| Problem | Fix |
|---|---|
| `Error: accepts 1 arg(s), received 3`, or a command looks doubled | It was pasted twice. Right-click also pastes in the terminal, so use only one of the two. |
| `Could not open requirements file` | You're not in the project folder. `cd` into `pc-tidy` first (see the tip in step 2). |
| `running scripts is disabled on this system` | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once. |
| `No module named 'reportlab'` | Run `pip install -r requirements.txt` again, since the PDF report needs it. |
| `Ollama isn't running` | Start Ollama from the Start menu. The scripts still work without it, just without the AI parts. |
| `Model llama3.2:3b not found` | `ollama pull llama3.2:3b` |
| `No other drive found` (move_off_c.py) | Pass a destination yourself, for example `--to D:\From-C`. |
| `conda` not found in Git Bash | Run `conda init bash` once in PowerShell, then reopen Git Bash. Or just use PowerShell. |
| `N folders could not be opened` | Run PowerShell as administrator. A few system folders stay locked even then, which is normal. |
| The `C:\Windows` size looks too big | Windows uses hard links, so files get counted more than once. Clean it only with Disk Cleanup > *Clean up system files*. |

## How it works

- **Rules first, AI second.** Plain rules in `classify.py` handle everything predictable. The AI is used only where rules can't decide: reading a PDF to sort it, sorting a whole folder, naming the type of an unfamiliar app, or describing an unfamiliar folder.
- **The AI never decides what gets deleted.** Its descriptions are labeled as guesses, and `cleanup.ps1` only does what you switch on.
- **Small and local.** `llama3.2:3b` runs on most laptops without a GPU and nothing is sent online.
- **Files:** `scanner.py` walks the drives, `classify.py` holds the rules, `pdf_report.py` draws the PDF, and `llm.py` talks to Ollama.

## License

MIT, see [LICENSE](LICENSE).
