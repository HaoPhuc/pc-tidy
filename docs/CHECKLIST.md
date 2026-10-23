# Release checklist: real PCs

CI runs every test on windows-latest, which is a Windows Server image. It runs as administrator, and has no UAC prompt, no OneDrive and no USB disks. These checks need a real PC, so do them by hand on **Windows 10 22H2** and **Windows 11** before a release. Tick each one, and write down what differed.

## Setup
- [ ] Download the ZIP, *Extract All* to `C:\pc-tidy`, and double-click `setup.cmd`. It ends with "pc-tidy is ready". Running it again changes nothing.
- [ ] On a PC without Python, `setup.cmd` opens python.org and says to tick "Add python.exe to PATH".
- [ ] With only the Microsoft Store's `python.exe` placeholder on the PC, setup doesn't use it.
- [ ] Run `pc-tidy.cmd` straight from inside the ZIP (Windows runs it from Temp). The self-test or the fixes say that pc-tidy's folder is inside Temp, and the Temp fix is left out.
- [ ] With the pc-tidy folder inside OneDrive, setup warns before going on.

## Windows rights
- [ ] An administrator account, normal window: the Windows rows say "needs an administrator window". *Run as administrator* on `pc-tidy.cmd` measures and cleans them.
- [ ] A standard account: the same rows say so, and nothing asks for a password unexpectedly.

## OneDrive
- [ ] With OneDrive folder backup on (Desktop and Documents synced), no report, plan, record or log of pc-tidy is in a OneDrive folder.
- [ ] `python disk_report.py` run from a synced folder saves to `%LOCALAPPDATA%\pc-tidy\reports` and says so.

## Drives
- [ ] A USB disk as the destination of a move: it asks a second time.
- [ ] `subst S: C:\Temp\x`, then `python move_off_c.py --to S:\`: refused, nothing deleted.
- [ ] C: nearly full (fill a test PC with `fsutil file createnew`): the guided flow says to start with the low-risk fixes, the Temp fix runs, then a move.

## The local AI
- [ ] Ollama off: everything works without the AI parts, and the report says so.
- [ ] Ollama on: descriptions appear, labeled as guesses, and Ctrl+C during "asking the local AI" skips the AI part.

## Locked and interrupted
- [ ] `disk_report.pdf` open in a PDF viewer during a report: the report is saved under a dated name.
- [ ] The README's weekly task still runs and is recorded. Run it again in each of these cases, and it completes every time:
  - [ ] with the guided flow left open at its menu
  - [ ] with an uninstaller left open
  - [ ] with an interrupted move left undecided (a notice appears at the next start)
- [ ] Kill the process during a move to another drive. `python pc_tidy.py recover`, then `python move_off_c.py --undo`, put everything back.

## Last-access times
- [ ] On a PC where `fsutil behavior query disablelastaccess` says updates are on: give a duplicate a last-access date a year back, run the report, and check that the date hasn't changed.
