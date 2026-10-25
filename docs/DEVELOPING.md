# Developing pc-tidy

For people who work on pc-tidy, or prefer to set the folder version up by hand. Everyone else: download pc-tidy.exe, or double-click `setup.cmd` (see the [README](../README.md)).

## 1. Install the tools (once)

Open **PowerShell** (press the Windows key and type `PowerShell`), then run these one at a time. Paste with Ctrl+V **or** right-click, not both, or the command gets pasted twice.

```powershell
winget install --id Git.Git -e                      # skip if you already have git
winget install --id Ollama.Ollama -e                # the local AI (optional), or download it from ollama.com
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned # lets PowerShell run scripts like cleanup.ps1
```

You also need Python 3.9 or newer. If you already have Anaconda or Miniconda, use that. Otherwise install Python from [python.org](https://www.python.org/downloads/) and tick **"Add python.exe to PATH"** in the installer.

Close PowerShell and open it again so it picks up the new installs.

## 2. Download the code

Go to the folder where you keep projects, then clone:

```powershell
cd ~
git clone https://github.com/HaoPhuc/pc-tidy.git
cd pc-tidy
```

Your prompt should now end in `\pc-tidy>`. Every command below is run from this folder.

> **Tip:** to `cd` into any folder, type `cd ` (with a space), drag the folder from File Explorer into the PowerShell window, and press Enter.

## 3. Set up Python and the AI model

With conda:

```powershell
conda create -n pc-tidy python=3.12 -y
conda activate pc-tidy
pip install -r requirements.txt
```

Without conda (this is what `setup.cmd` does):

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

## 4. Run the tests

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

They run on fake folders in a temporary folder, and a write guard (`tests/guard`) stops them from changing anything else. Some need admin rights (the VHDX test volumes and `cleanup.ps1`'s real runs), and skip without them; CI runs elevated, so they run there. `docs/CHECKLIST.md` lists what only a real PC can check.

## 5. Build pc-tidy.exe

In a venv with PyInstaller alone, so nothing else ends up inside the exe:

```powershell
python -m venv build-venv
build-venv\Scripts\python.exe -m pip install -r packaging\requirements-build.txt
build-venv\Scripts\python.exe -m PyInstaller --noconfirm --clean packaging\pc-tidy.spec
```

It lands in `dist\pc-tidy.exe`. Set `PCTIDY_VERSION` (for example `v2.0.0`) first to put a version in its Properties. Then test it:

```powershell
$env:PCTIDY_EXE = "dist\pc-tidy.exe"
python -m pytest -q tests\test_exe.py
```

Each test runs its own copy, since at its end pc-tidy.exe offers to remove itself.

## 6. Releasing

See [RELEASING.md](RELEASING.md): a `v*` tag builds, signs (once that is set up), tests, attests and publishes pc-tidy.exe.
