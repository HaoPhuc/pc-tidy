# Release checklist: real PCs

CI runs every test on windows-latest, which is a Windows Server image. It runs as administrator, and has no UAC prompt, no OneDrive and no USB disks. These checks need a real PC, so do them by hand on **Windows 10 22H2** and **Windows 11** before a release. Tick each one, and write down what differed.

## pc-tidy.exe
- [ ] Download `pc-tidy.exe` from a CI run or a release and double-click it. On an administrator account Windows asks once; the window then cleans the Windows rows too. On a standard account nothing asks for a password, and the Windows rows say why they are left out.
- [ ] It starts with the quick sweep within a few seconds, listing only temp files older than 7 days, caches and crash dumps. `yes` frees them; `M` then scans and shows the ranked list.
- [ ] After the sweep or the menu, the window waits for "Press Enter to close" before it closes.
- [ ] Open it from inside a ZIP in Explorer (Windows runs it from Temp): the Temp fix runs, leaving out only the folder it is in.
- [ ] While it runs, `%LOCALAPPDATA%\pc-tidy\app` holds its unpacked files, and Temp holds none of them.
- [ ] At the end, Enter at "Remove pc-tidy from this PC?", then close the window: within a few seconds pc-tidy.exe is gone from Downloads, the other files there stay, and `%LOCALAPPDATA%\pc-tidy` is gone (or holds only what you put there yourself).
- [ ] The same after a move to another drive: `<drive>:\From-C\_pc-tidy` holds the record and `READ ME - moved from C.txt` lists the moves. Download pc-tidy.exe again, type M, and *Undo the last move off C:* puts them back.
- [ ] `K` keeps it, and the next start asks again.

## Setup
- [ ] Download the ZIP, *Extract All* to `C:\pc-tidy`, and double-click `setup.cmd`. It ends with "pc-tidy is ready". Running it again changes nothing.
- [ ] On a PC without Python, `setup.cmd` opens python.org and says to tick "Add python.exe to PATH".
- [ ] With only the Microsoft Store's `python.exe` placeholder on the PC, setup doesn't use it.
- [ ] Run `pc-tidy.cmd` straight from inside the ZIP (Windows runs it from Temp). The Temp fix leaves out only the folder pc-tidy is in, and says so; the rest of Temp is offered.
- [ ] With the pc-tidy folder inside OneDrive, setup warns before going on.

## Windows rights
- [ ] An administrator account, normal window: the Windows rows say "needs an administrator window". *Run as administrator* on `pc-tidy.cmd` measures and cleans them.
- [ ] A standard account: the same rows say so, and nothing asks for a password unexpectedly.
- [ ] A standard account, *Run as administrator* on `pc-tidy.cmd` with an administrator's password: it says it runs as the other account, and only the Windows rows can be picked. `.\cleanup.ps1` the same way cleans only the Windows folders.
- [ ] `.\cleanup.ps1` in an administrator PowerShell, with `recycle_bin = yes` and a test folder in `extra_paths_to_delete`: the exact list comes first, each of those two asks a second time, Enter at the second question leaves it out, and the summary shows C: before and after.

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
- [ ] A video playing from a folder you move to another drive: the move is skipped before anything is copied, naming the file.
- [ ] Outlook with a `.pst` in Documents: that folder isn't offered as a move, and the tips say why.
- [ ] A document opened from a ZIP in Explorer, edited and saved: the Temp fix leaves it alone for 7 days.

## Last-access times
- [ ] On a PC where `fsutil behavior query disablelastaccess` says updates are on: give a duplicate a last-access date a year back, run the report, and check that the date hasn't changed.
