# pc-tidy 3.0: safe on every path, then a one-click broom

## Status

Written 2026-10-25, from reading the code at `bf88129` (M8 merged). Problem 17 was reproduced on this PC; the others were found by reading the code.

| Milestone | State |
|---|---|
| M9 One engine that deletes | **Done**, merged in PR #13. Fixes problems 17, 18 and 19. The command is `python pc_tidy.py cleanup [--dry-run]`, which replaces the JSON-only `cleanup-settings`. Tools and the Recycle Bin are tested with stand-ins in-process, and `cleanup.ps1`'s own real run only in CI, which is elevated. |
| M10 Safer fixes | **Items 1–7 done**, merged in PR #14. They fix problems 20–26: the Temp age floor (`temp_min_age_days`), pinned and byte-checked kept copies, the other-account check, Node.js only in quiet projects, installers High, and app folders, files in use and the backup note for moves. Item 8, parking on another drive, is not built: it was the planned cut. |
| M11 One file, no Python | **Done**, on the same branch. PyInstaller 6.22.3 (`packaging/pc-tidy.spec`), 8.8 MB, about 4–5 s to start. Unpacks into `%LOCALAPPDATA%\pc-tidy\app`; the manifest asks for `highestAvailable` (PyInstaller itself only writes `asInvoker` or `requireAdministrator`, so the spec sets it, and `tests/test_exe.py` checks it). The report is HTML wherever reportlab is missing; the source edition keeps its PDF. pc-tidy in Temp now leaves out only the item that holds it. CI builds and tests the exe on every push and publishes it on a `v*` tag. The spike didn't compare Nuitka, and Defender couldn't scan here: false positives are checked in M14 with signing. |
| M12–M14 | Not started |

**Goal, in order:**
1. No clean can lose a file you didn't mean to lose, whichever way you start it.
2. Then: pc-tidy works like a broom. You download one file, it cleans the PC, and it removes itself.

---

## The verdict

**What is strong.** The executor is the right design, and it is well built. Plans live in memory and every item has a fingerprint. The journal reaches the disk before anything undo can put back. Nothing is overwritten and links are never followed. A move copies, hashes, checks the copy, then deletes. The lock is never held at a prompt, and it all works on a full C:. The tests are better than most commercial tools have: a write guard that also covers subprocesses, real VHDX volumes, and the LLM-boundary and chaos tests.

**What is weak** is everything around that core:
- One path still deletes without it: `cleanup.ps1`. That path has a confirmed bug that deletes the wrong folder.
- The gate in front of the executor lets through some fixes that can hurt. The executor then carries them out faithfully.
- Getting pc-tidy onto a PC takes five manual steps and hundreds of MB on the drive it is meant to rescue. Removing it isn't possible at all. That is the opposite of a broom.

---

## Safety: problems found

The numbers continue the problems table of [PLAN.md](PLAN.md).

| # | Where | Problem | Found by |
|---|---|---|---|
| 17 | `cleanup.ps1`, `extra_paths_to_delete` | pc-tidy's safety check treats `[` and `]` as ordinary characters (`paths.WILDCARDS = "*?"`). `cleanup.ps1` then passes the path to `Get-Item -Path`, `Test-Path -Path` and `Remove-Item -Path`, where `[...]` is a wildcard. So `D:\Photos [2019]` passes the check as one folder. PowerShell then matches `D:\Photos 2`, `Photos 0`, `Photos 1` and `Photos 9`, deletes them, and leaves `Photos [2019]` alone. The dry run measures those same wrong folders, under the right name. | **Reproduced** on this PC (Windows PowerShell 5.1.26100): `Remove-Item -Path ...\old[1] -Recurse -Force` deleted the folder `old1` with its file, and left `old[1]`. |
| 18 | `cleanup.ps1`, real run | Problem 2 is still open. The yes comes after category names only, the installer list is printed after the yes, while deleting, and folders are listed again after the yes. This is the one path that deletes without the executor: no fingerprints, no journal, no lock, and links are left to PowerShell instead of rule 4. Its real run has no test: every test runs `-DryRun`. | Read |
| 19 | `settings.template.ini` | `recycle_bin = yes` is the default. So `.\cleanup.ps1` empties every Recycle Bin after one yes, while the guided flow calls the same action High risk and asks twice. Two engines give two answers, and the bin is your last undo for things you deleted yourself. | Read |
| 20 | Temp fixes (both engines) | They delete everything in `%TEMP%` and `Windows\Temp`, however new. A file you open from a ZIP in Explorer, or from an email attachment, is opened from Temp, and the edits are saved there. A running installer keeps its files there too. Disk Cleanup leaves temp files from the last 7 days. | Read |
| 21 | Duplicates + Installers | The copy that stays can be deleted by another fix in the same run. Take `Downloads\tool.exe` (older, so it is the copy that stays) and `Desktop\tool.exe`, and pick both "Extra copies" and "Installers". If the duplicates run first, the Desktop copy goes, with the dry run saying "the copy in Downloads stays". Then the installers fix deletes the Downloads copy, and nothing is left. The executor checks the copy that stays only when it deletes the extra, not afterwards. Which fix runs first depends on the ranking: both are Medium. | Read |
| 22 | Duplicates | When the extra copy is deleted, "unchanged" means the same size, modified time and file ID, not the same content. Some tools change a file and keep its modified time (tag editors offer this). If the size stays the same too, the copy that stays is no longer identical, and the extra is deleted anyway. | Read |
| 23 | Elevation (both engines) | Problem 8 is still open: `_win.py` has no console-user check. On a standard account, *Run as administrator* with a parent's password runs pc-tidy as the parent. It cleans the parent's Temp and offers the parent's Downloads installers and duplicates to the person at the keyboard, who has never seen them. | Read |
| 24 | Node.js fix | It offers any `node_modules` of 100 MB or more anywhere on C:, outside a short list of app folders (`_APP_FOLDERS` covers `.vscode` but not `.vscode-insiders`, `.cursor`, `scoop` ...). It doesn't check for a project or for 30 quiet days, as the venv fix does. So an editor extension or a tool installed by a package manager can be offered, and deleting it breaks that app. | Read |
| 25 | Installers fix | It is called Medium ("download it again"). But it deletes portable programs, which run straight from their `.exe`, and installers you can't download again: old versions, or software you bought. The only safeguard is reading the list. | Read |
| 26 | Moves | App folders inside Documents are offered as moves: `Outlook Files` (Outlook's `.pst` mail files, often the biggest thing there), `My Games` (game saves), `WindowsPowerShell`, `Visual Studio 20xx`. A file that an app holds open shows up only partway through the copy. If the app lets others read the file but not delete it, it shows up after the copy, leaving the folder half-deleted until `recover`. Nothing says that moved files leave the folders File History and Windows Backup cover. | Read |

Checked and fine: on this PC, `Remove-Item -Recurse` didn't follow a junction inside the folder it removed. Windows 10's PowerShell 5.1 wasn't tested. M9 removes the question anyway, because `cleanup.ps1` stops deleting by itself.

## Convenience: why it isn't a broom

| # | Problem |
|---|---|
| C1 | **Five steps before the first clean:** install Python and tick "Add to PATH", download the ZIP, extract it somewhere that isn't Temp or OneDrive, double-click `setup.cmd` (which needs the internet for pip), then double-click `pc-tidy.cmd`. Setup also offers a 2 GB AI model. |
| C2 | **The install needs room on the drive it is meant to rescue.** Python, `.venv` and pip's cache all go on C:. On a full C:, setup fails before pc-tidy runs, so a new user never reaches the full-disk mode M2 built. |
| C3 | **It can't remove itself, and it leaves things behind:** `.venv`, `settings.ini`, `%LOCALAPPDATA%\pc-tidy` (reports, plans, journals and logs, all kept for ever except the logs), pip's cache, Python itself, and optionally Ollama and its model. pc-tidy would offer to delete its own pip cache on the next run. |
| C4 | **A session is long.** A full scan of C: and up to 50 GB of duplicate hashing run before the first question. Then a PDF opens, about 20 rows wait to be read, numbers have to be typed, and a 7-item menu follows. |
| C5 | **Admin rights need a right-click almost nobody knows** (*Run as administrator*). The self-restart was deferred in M6. |
| C6 | **Too many doors.** There are five entry points and two deleting engines with different defaults (problem 19). The README's longest section is a developer walkthrough: git, conda, Ollama's `irm ... \| iex`, and `Set-ExecutionPolicy`, which changes a PowerShell setting that setup promises to leave alone. |

---

## The target: a broom

1. The GitHub page has one link: **Download pc-tidy.exe**. It is one signed file and needs no Python.
2. Double-click it. On an administrator account, Windows asks once. pc-tidy then runs elevated as you, so Temp and Downloads are still yours. A standard account gets no password prompt; the Windows folders are left out, and pc-tidy says so.
3. A few seconds later: *"Quick sweep: 6.3 GB of temp files older than 7 days, download caches and crash dumps. They come back by themselves."* The exact list is one key away. Type `yes`.
4. *"Freed 6.1 GB. C: had 3.2 GB free, now 9.3 GB."* Then `M` for more: moves to another drive, extra copies, old installers, hibernation, the Recycle Bin. This is today's full flow.
5. *"Remove pc-tidy from this PC now? (Enter = yes)"*. It removes everything it put on C:, then the exe itself. If it moved anything, the record of those moves stays next to the moved files on the other drive.

## Order and effort

| # | Milestone | Fixes | Days |
|---|---|---|---|
| M9 | One engine that deletes | 17, 18, 19, C6 | 3 |
| M10 | Safer fixes | 20–26 | 4.5 |
| M11 | One file, no Python | C1, C2, C5 | 3 |
| M12 | The quick sweep | C4 | 2 |
| M13 | Leave nothing behind | C3 | 2.5 |
| M14 | A release you can trust | C1, C6 | 1.5, plus setting up signing |

**Total:** about 16.5 days. M9 and M10 come first and stand on their own. Ship them in the current edition even if the broom waits.

---

## M9. One engine that deletes

**What changes**
- `python pc_tidy.py cleanup [--dry-run]` builds one plan from the `[cleanup]` switches and its three lists. Every action it needs already exists:
  - table fixes (contents and patterns), installers (files), extra paths (one action per file after `safety.check`)
  - the Recycle Bin (count only)
  - `conda clean` and `conda env remove`, hibernation (tools with a time limit)
  - winget (an interactive tool, run last with the lock released)

  It reuses the guided flow's `DryRun`, `confirm`, `confirm_high` and `summary`: the full list before yes, `O` for every item, and a second yes for High risk.
- `cleanup.ps1` keeps its help, the admin check and Find-Python, and then runs `& $py pc_tidy.py cleanup @args`. `Remove-Paths`, `Get-SizeBytes` and `Test-Protected` are removed. This is the deferred half of M4.
- **One meaning for every character, in `paths.py`.** In `extra_paths_to_delete`, only `*` and `?` are wildcards. `[` and `]` are literal everywhere, so they are escaped before any `fnmatch` or `glob` (both treat `[...]` as a set, just like PowerShell).
- `recycle_bin = no` in the template. An existing `settings.ini` keeps its value, but the bin now shows as High risk with its own second yes.
- The guided flow also offers the three lists as fixes, as M4 planned: conda envs (Medium), apps (Medium, interactive), and your extra paths (High).

**How to test**
- Real runs, not only `-DryRun`, on the fake profile and a fake system root under the sandbox root:
  - `no`, empty input and end of input change nothing
  - a file added after the dry run survives
  - a junction inside an extra path: the link goes, its target stays
- Folders `old[1]` and `old1`, with `extra_paths_to_delete = <fake>\old[1]`: only `old[1]` goes, and the dry run measures only it. This is the regression test for problem 17.
- The Recycle Bin and winget are stubbed. The bin asks a second time.
- Pester: `-DryRun` maps to `--dry-run`, `--help` still works, and the missing-Python message is unchanged.

**Cut:** nothing. This is the safety fix.

## M10. Safer fixes

**What changes**
1. **Temp age floor** (problem 20). `[cleanup] temp_min_age_days = 7`, for your Temp and Windows Temp. An item goes only if every file in it was both created and changed more than N days ago. The creation time matters because Explorer keeps a ZIP's old dates when it unpacks, while the creation time is the day it unpacked. The row reads "Temp files older than 7 days".
2. **The copy that stays is pinned** (problem 21). When the plan is built, a delete from any other fix that hits a kept copy is dropped and listed: *"kept: it is the copy that stays of Desktop\tool.exe"*.
3. **Same content, checked at the last moment** (problem 22). Right before an extra copy is deleted, both files get a full BLAKE2b hash. Any difference skips the item. This costs time only for the files you picked.
4. **Console-user check** (problem 23), in both engines. The process user's SID is compared with that of the user signed in at the console (`WTSGetActiveConsoleSessionId`, `WTSQuerySessionInformationW`). If they differ, user-scope fixes are off, with PLAN.md's M8 message.
5. **Node.js through projects** (problem 24). `node_modules` is offered only inside a project found by `leftovers.py`, with `package.json` beside it and 30 quiet days: the same rule as venvs and `target`. That also limits it to your personal folders, without dot-folders.
6. **Installers are High** (problem 25): *"Gone for good. Portable programs run from their .exe, and old versions may not be downloadable."* Item 8 brings this back to Medium when the files can be parked.
7. **Moves leave app folders alone** (problem 26).
   - These are no longer offered: `Outlook Files`, `My Games`, `WindowsPowerShell`, `PowerShell`, `Visual Studio*`, and any folder holding `.pst` or `.ost` files. Each gets a tip instead, e.g. *"Move Outlook's data in Outlook: File > Account Settings > Data Files."*
   - **In-use check before copying:** each file is opened for a moment with no sharing (`CreateFileW`, share mode 0). Anything in use skips the item before a byte is copied.
   - The dry run says once: *"Moved files are no longer covered by File History or Windows Backup of your user folders, unless you back up the other drive too."*
8. **Park instead of delete** (decision below). When another volume exists, the deletes that can hurt (extra copies, installers, your extra paths) become moves to `<drive>:\From-C\_parked\<date>\`. They go through the move plan (copy, hash, check, delete, journal), so `undo` puts them back. C: gains just as much. Parked items older than 30 days are listed under "Tidy-ups" to empty, outside the C: ranking, since emptying them frees the other drive, not C:. With only one drive, deletes stay deletes, as now.

**How to test**
- Temp: a fresh file, an old file, an old folder holding one fresh file, and a file with an old modified time but a new creation time. Only the old file goes.
- The `tool.exe` case with the fixes in both orders: one copy always remains.
- The copy that stays is changed with its time and size kept: the extra is skipped.
- The console user is stubbed as a different account: user-scope rows are unavailable and say why.
- `.cursor\extensions\x\node_modules` and a `node_modules` in an active project are not offered.
- A fake `Documents\Outlook Files\mail.pst` is not offered. A file held open in a candidate folder: skipped before copying, and nothing is on the destination.
- Parking: a duplicate parked on the NTFS test volume, then `undo` puts it back.

**Cut:** parking (item 8) if time is short. Items 1–7 never.

---

## M11. One file, no Python

**What changes**
- CI builds `pc-tidy.exe` from a version tag. It holds the guided flow, moves, undo and recover. The AI, sorting, `disk_report.py`, `cleanup.ps1` and the weekly task stay in the source edition, unchanged.
- **No third-party packages in the exe.** The report becomes one HTML file, made with the standard library: it opens in the browser and is searchable. So reportlab isn't needed. pypdf and python-docx only serve the AI sort.
- **Packer: a one-day spike**, PyInstaller against Nuitka. The rules:
  - It unpacks into `%LOCALAPPDATA%\pc-tidy\app\<version>`, never into `%TEMP%`. AppData is inside safety rule 2, so no fix can touch it. Both packers unpack into Temp by default, and both have an option to change that (PyInstaller `--runtime-tmpdir`, Nuitka `--onefile-tempdir-spec`). The spike checks that it can point at `%LOCALAPPDATA%`.
  - Pick the one with fewer false positives in Defender and on VirusTotal for the unsigned build, then by size and start time.
- **Manifest `requestedExecutionLevel = highestAvailable`.** Administrator accounts get one UAC prompt and run elevated as themselves, so problem 23 can't happen in the exe. Standard accounts run as they are, with no password prompt, and the Windows rows say "needs an administrator".
- **pc-tidy knows its own files.** At start, the exe's own path, its unpack folder and the data folder join safety's closed list. So pc-tidy.exe in Downloads is never offered as an installer, a duplicate or a file to sort. If it runs from Temp (opened from inside a ZIP), only its own folder is left out, not the whole Temp fix as today.

**How to test**
- CI runs the built exe on the fake profile under `PCTIDY_SANDBOX_ROOT`, with the end-to-end scripts of `test_guided.py`.
- A fake Downloads with `pc-tidy.exe` older than the installer age: it isn't listed.

**Cut:** none. The spike can end in "Nuitka" or "PyInstaller", but not in "unpack into Temp".

## M12. The quick sweep

**What changes**
- The exe starts with the checks, then measures only the Low-risk folders: Temp (with the age floor), the download caches, shader caches and crash dumps. That takes seconds: no full scan of C:, no hashing.
- **One screen:** the total, what it is, "comes back by itself", the first items, `O` for the full list, then type `yes`. This is the one place where fixes come picked for you: only Low risk, and only after you have seen the list. It is a deliberate change to M6's "nothing is picked for you".
- Then the summary (freed, C: before and after), and `M` for more, which is today's full flow: scan, report, ranked list, picking, moves.
- The HTML report is saved in the reports folder and offered, not opened by itself.

**How to test:** a scripted sweep on the fake profile: `yes` deletes only the listed Low items, `no` and end of input change nothing, and `M` reaches the ranked list.

## M13. Leave nothing behind

**What changes**
- At the end: *"Remove pc-tidy from this PC? It keeps nothing of its own on C:. (Enter = yes, K = keep it for next time)"*.
- **What is removed, exactly:**
  - the files in the unpack manifest (written when it unpacked: every path with its hash)
  - the data folder's reports, plans and logs, and the journals of runs that only deleted (there is nothing to undo there)

  Nothing is chosen by guessing a folder: never "the folder pc-tidy.exe is in" (that may be Downloads), and never anything from an environment variable. Every removal goes through `safety.check` with a new operation, `self`, which allows only paths in the manifest and the data folder.
- **The exe itself:** a running exe can be renamed but not deleted. pc-tidy records its size and hash, then starts a hidden `powershell -NoProfile` that waits for pc-tidy's process to end. That process checks the hash and removes that one file with `-LiteralPath` (problem 17's lesson). A different file at that path stays.
- **What stays, if anything was moved or parked:** the run's journal is copied to `<drive>:\From-C\_pc-tidy\`, where recovery and undo already look. Beside it goes `READ ME - moved from C.txt`: what was moved, when, and *"To put it back, download pc-tidy.exe again and choose Undo."* There is no separate undo script: one engine.
- pc-tidy refuses to remove itself while an interrupted move waits for a decision, or before a journal has reached the destination drive.
- `K` keeps it, and the next start offers removal again.

**How to test**
- The exe in a fake Downloads next to other `.exe` files: only pc-tidy.exe goes.
- The exe replaced after start (the hash differs): it stays.
- After a move, the journal is on the NTFS test volume. A fresh copy of the exe finds it and undoes the move.
- An undecided interrupted move: no removal, and the reason is shown.

## M14. A release you can trust

pc-tidy runs as administrator and deletes files, which makes it worth attacking. The broom only works if people can trust the file without clicking through warnings.

- **Sign the exe.** Unsigned, SmartScreen shows "Windows protected your PC" and hides *Run anyway* behind *More info*. That isn't one button, and it teaches people to click through warnings. Options are an OV certificate or Microsoft's cloud signing service. It costs a small yearly fee, the only money in this plan.
- **Build only in GitHub Actions from a version tag.** Publish the SHA-256 and add a build provenance attestation (`actions/attest-build-provenance`). Then `gh attestation verify pc-tidy.exe -R HaoPhuc/pc-tidy` proves the file came from this repository's workflow.
- **No network, no auto-update.** The exe never downloads anything. Each sweep uses the latest download, so nothing can be slipped in later.
- **README:** the first screen says "Download pc-tidy.exe and double-click it". The developer walkthrough (git, conda, Ollama, `Set-ExecutionPolicy`) moves to `docs/DEVELOPING.md`.
- **CHECKLIST additions:**
  - clean Windows 10 22H2 and Windows 11 machines without Python
  - a Defender scan, and SmartScreen on a fresh download
  - a standard account
  - the exe run from inside a ZIP in Explorer
  - the exe left in Downloads and run again a week later
  - remove, download again, undo a move

---

## Decisions to make

| Decision | Recommendation | Why |
|---|---|---|
| Temp age floor | 7 days | The same as Disk Cleanup. It frees a little less and never touches what you're working on. |
| Park the deletes that can hurt on another drive | Yes, when there is one | It gives undo to the only deletes that can hurt, and C: gains just as much. PLAN.md rejected a soft delete because the Recycle Bin and a folder on C: free nothing. Parking on another drive avoids that. |
| PDF or HTML report | HTML, in both editions | No package, it opens anywhere, and there is one renderer to keep. |
| AI in the exe | No | A 2 GB model and a separate program, used only for sorting and descriptions. It stays in the source edition. |
| Code signing | Pay for it | Without it, the broom isn't one click. |
| Default answer to "Remove pc-tidy?" | Yes | It's the broom you asked for, and nothing of yours is in what it removes. |

## Not doing

- **An `irm https://... | iex` one-liner as "the button".** It runs unsigned code from the internet straight in an admin shell.
- **Auto-update.**
- **A PowerShell undo script left behind with the moved files.** A second engine is how problems 17–19 happened.
- **A one-file packer that unpacks into `%TEMP%`.** The Temp fix would be cleaning up after pc-tidy while it runs.
- **A GUI, for now.** The console flow is tested end to end. Revisit after the exe ships.
