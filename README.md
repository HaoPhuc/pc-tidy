# pc-tidy

Find out what is filling up your Windows PC, clean it up safely, move big files off C:, and keep messy folders sorted. A small AI model runs **locally** on your PC through [Ollama](https://ollama.com), so none of your files or file names leave your computer.

| Tool | What it does | Changes files? |
|---|---|---|
| `pc-tidy.cmd` (or `python pc_tidy.py`) | **The easy way.** Double-click it: it scans C:, shows the fixes for C: lowest risk first, and does the ones you pick. Then a menu to sort Downloads, undo, or change settings. | Only what you pick, after the exact list and your yes |
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

**The easy way:** double-click `pc-tidy.cmd` in the pc-tidy folder. To include the Windows folders (Windows Temp, crash dumps, hibernation), right-click it and choose *Run as administrator* instead. It:

1. scans C: and saves the report in `%LOCALAPPDATA%\pc-tidy\reports` (a folder OneDrive never uploads)
2. shows the fixes for C:, numbered, lowest risk first, with what each frees
3. asks which ones you want: type numbers like `1,3` or `2-4`, `L` for all the low-risk ones, or press Enter to stop. Nothing is picked for you.
4. shows exactly what it will do (the dry run): every file it would delete, every folder it would move. Nothing has changed yet.
5. does it only after you type `yes`. A high-risk fix (emptying the Recycle Bin) asks a second time.
6. says what was freed, what was skipped and why, and how much free space C: has now

Fixes it can't do for you (the browser cache, Windows.old) say exactly how to do them by hand. Moves are made by `move_off_c.py`, so `python move_off_c.py --undo` puts them back. After that, a menu lets you sort Downloads, undo the last move or sort, open the reports folder, or change settings.

Each tool also works on its own. Open PowerShell **as administrator** (right-click PowerShell > *Run as administrator*) so the scan can look inside protected folders. It starts in `C:\Windows\System32`, so go back to the project first:

```powershell
cd <path to pc-tidy>
conda activate pc-tidy
python disk_report.py
```

It scans every drive (a few minutes each), saves `disk_report.pdf` and opens it. The report has these sections:

- **Fixes for C:**: what to do first, as a numbered list. Lowest risk first (things that come back by themselves), then what can be undone or rebuilt, then what is gone for good. Each fix says how much it frees, measured file by file (a file shared through a hard link counts as nothing, since deleting it frees nothing), how risky it is, exactly how to do it, and how to undo it. A running total and one line say which fixes bring C: to 15% free (`target_free_percent` in `settings.ini`). Run the report as administrator to measure the Windows folders too.
- **What's left in your folders** (Desktop, Documents, Downloads, Music, Pictures, Videos and Sorted):
  - **Duplicates:** files with exactly the same content. Hard links are left out, since they're one file. Loose extra copies (right in Downloads or Desktop, or in a Sorted folder), where copies end up by accident, become a fix; an identical copy always stays. Copies deeper in a folder, such as an unpacked app, are only listed.
  - **Big files you haven't opened for months:** these can be moved off C:, never deleted.
  - **Empty folders** and **projects** (folders with `.git`, or a `package.json`, `requirements.txt`, `Cargo.toml` ... with their source). A project untouched for 30 days offers its venv or Rust `target` folder as a fix, since those can be rebuilt.

  Checking for duplicates never changes when a file was last opened.
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

Choose what to clean in the `[cleanup]` section of `settings.ini`:

```powershell
python pc_tidy.py settings edit
```

```ini
[cleanup]
conda_cache = yes        # conda package cache
pip_cache = yes          # pip download cache
uv_cache = yes           # uv download cache
gpu_shader_cache = yes   # NVIDIA / AMD / DirectX shader caches
temp_files = yes         # Temp folders
recycle_bin = yes        # empty the Recycle Bin on every drive
crash_dumps = yes        # *.dmp crash dumps
hibernation = no         # turn off hibernation, frees C:\hiberfil.sys
installers = yes         # installers (.exe, .msi ...) in Downloads and Sorted\To-Delete
installers_min_age_days = 7

conda_envs_to_remove =
    old-env
apps_to_uninstall =
    Docker Desktop
extra_paths_to_delete =
    D:\old-backups\*.zip
```

Then, in the administrator PowerShell (with pc-tidy's environment active, as in step 4):

```powershell
.\cleanup.ps1 -DryRun   # shows each step and how much it would free, changes nothing
.\cleanup.ps1           # does it, after you type yes
```

Safety features:
- A mistake in `[cleanup]` stops `cleanup.ps1` before it does anything, and names the line.
- The dry run checks that every conda env and app name exists before you commit.
- `extra_paths_to_delete` refuses drive roots, Windows, Program Files, AppData, your user folder and its Windows folders (Documents, Desktop, Downloads ...), and a wildcard directly under any of them, such as `D:\*`.
- If your `TEMP` variable points somewhere that isn't a temp folder (a drive root, say), your Temp folder is left alone and only `C:\Windows\Temp` is cleaned.
- Installers changed in the last `installers_min_age_days` days are left alone, since you may not have installed them yet.
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

- **Rules handle most files by extension.** Images go to `Pictures`, installers to `To-Delete`, and so on. The `Installers` setting in `cleanup.ps1` deletes them, since once an app is installed you don't need its installer. Files older than 60 days go under `Sorted\Archive`. The folders, the rules and the 60 days are in `settings.ini` (see [Settings](#settings)).
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

If a run can't do its work, for example because of a mistake in the `[tidy]` part of `settings.ini`, it says so the next time you run `python pc_tidy.py status`, and Task Scheduler shows its exit code as the task's "Last Run Result".

---

## Settings

All settings are in one file, `settings.ini` in the pc-tidy folder. pc-tidy makes it from `settings.template.ini` the first time a tool runs, and every line in it has a comment that says what it does.

```powershell
python pc_tidy.py settings edit    # opens settings.ini in Notepad, and checks it when you close Notepad
python pc_tidy.py settings check   # lists every mistake, with its line number
python pc_tidy.py settings show    # shows every value and where it came from
```

| Section | Used by | What's in it |
|---|---|---|
| `[report]` | `disk_report.py` | `min_gb` and `max_depth` for the folder tree, `top_files`, `cleanup_min_gb`, `target_free_percent` |
| `[ai]` | `tidy_folder.py`, `disk_report.py` | `enabled`, `model` (any Ollama model, for example a bigger one like `qwen2.5:7b` for better descriptions), `url` (only this PC is allowed), `timeout_seconds`, how many folders and apps the AI describes |
| `[leftovers]` | `disk_report.py` | `extra_folders` to look in too, `duplicate_min_mb`, `stale_min_mb` and `stale_after_days` for big unopened files, `hash_budget_gb` |
| `[move]` | `move_off_c.py` | `min_gb`, `dest_folder_name` |
| `[tidy]` | `tidy_folder.py` and the weekly task | `source`, `dest`, `archive_after_days`, `skip_newer_than_min`, `rename_with_ai`, `categories` (the folders the AI picks from) |
| `[tidy.extensions]` | `tidy_folder.py` and the weekly task | `extension = folder`, for example `.pdf = Papers` |
| `[cleanup]` | `cleanup.ps1` | What it cleans: the switches and lists of step 5, and `installers_min_age_days` |
| `[label.<name>]`, `[category.<name>]`, `[apptype.<name>]` | `disk_report.py` | Your own rules, instead of editing `classify.py` (below) |

How to write it:
- One `key = value` per line. A list has one item per line, indented under its key. Lines starting with `#` are comments.
- `%USERPROFILE%` and other `%NAME%` variables are filled in, and a backslash is just a backslash: `C:\new` is the folder `C:\new`.
- An option on the command line wins over `settings.ini`, for example `--source` or `--min-gb`. A line you delete uses its default.
- **A mistake stops only the tools that read its section.** A typo in `[tidy]` stops `tidy_folder.py` with the line number, but not `move_off_c.py`. A mistake in `[ai]` makes `tidy_folder.py` sort by its rules only. `disk_report.py` never stops: it uses the default for a broken value, skips a broken rule, and says so at the top of the report.

**Your own rules** for the disk report each go in a section of their own. They are checked before the built-in ones, so they can also change a built-in answer. Patterns are regular expressions on the path in lower case with forward slashes:

```ini
[label.old-games]
pattern = ^[a-z]:/games/old$
what = Old game installers
todo = Delete the ones you have finished
kind = clean

[category.lab-data]
pattern = ^d:/lab$
category = Personal files

[apptype.cad]
match = onshape|rhino
type = Dev & science tools
```

`kind = clean` lists the folder under "Things you can clean up", `kind = info` under "Good to know". The allowed categories and app types are listed in the comments at the end of `settings.ini`.

## Upgrading from a version before 2.0

Before 2.0 the settings were inside the scripts, so your edited `cleanup.ps1` and `.py` files would block `git pull`. Instead:

1. Rename your `pc-tidy` folder to `pc-tidy-old`.
2. Clone a fresh copy, as in step 2 of the walkthrough.
3. In the new folder, run:

```powershell
python pc_tidy.py settings import ..\pc-tidy-old
```

It reads the SETTINGS block of the old `cleanup.ps1`, the settings at the top of the old `.py` files and your changes to `classify.py`, without running any of them. It lists every value it would import and writes `settings.ini` only after you type `yes`. A value that pc-tidy 2.0 refuses is listed with the reason and left out (for example a path inside Program Files in `$ExtraPathsToDelete`), and so is any line it can't read. In this version `cleanup.ps1` still reads its own SETTINGS block, so copy that block into the new `cleanup.ps1` too.

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
| `... didn't start: settings.ini has a mistake` | Run `python pc_tidy.py settings edit`, fix the line it names, and close Notepad. It checks the file again. |
| `conda` not found in Git Bash | Run `conda init bash` once in PowerShell, then reopen Git Bash. Or just use PowerShell. |
| `N folders could not be opened` | Run PowerShell as administrator. A few system folders stay locked even then, which is normal. |
| The `C:\Windows` size looks too big | Windows uses hard links, so files get counted more than once. Clean it only with Disk Cleanup > *Clean up system files*. |

## How it works

- **Rules first, AI second.** Plain rules in `classify.py` (plus your own in `settings.ini`) handle everything predictable. The AI is used only where rules can't decide: reading a PDF to sort it, sorting a whole folder, naming the type of an unfamiliar app, or describing an unfamiliar folder.
- **The AI never decides what gets deleted.** Its descriptions are labeled as guesses, and `cleanup.ps1` only does what you switch on.
- **Small and local.** `llama3.2:3b` runs on most laptops without a GPU and nothing is sent online.
- **Files:** `scanner.py` walks the drives, `classify.py` holds the built-in rules, `pdf_report.py` draws the PDF, `llm.py` talks to Ollama, and `pctidy\settings.py` reads `settings.ini`.

## License

MIT, see [LICENSE](LICENSE).
