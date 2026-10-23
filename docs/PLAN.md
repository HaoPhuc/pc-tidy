# pc-tidy 2.0: build plan

## Status

Updated 2026-10-23. The plan below is the one chosen at the start, unchanged. This section records what has been built since, and the decisions that changed the order.

| Milestone | State |
|---|---|
| M1 Tests and CI | Done in PR #5 |
| M2 Safety core | Done in PR #6. Fixes problems 3, 4, 5, 13 and 15 |
| M3 One settings file | Done in PR #7 |
| M4 cleanup.ps1 | **Lean version**: `cleanup.ps1` reads `[cleanup]` from `settings.ini` (through `python pc_tidy.py cleanup-settings`), and problems 1 and 14 are fixed. Deferred: the Python port with exact per-file dry-run lists (problem 2), the Recycle Bin and conda measurements, the other-account check (problem 8) and winget's agreement prompt (problem 9). |
| M5 Report, M6 Guided entry point | Next, ahead of more infrastructure: what users see comes first |
| M7 What's left, M8 Polish | After M5 and M6 |

Keep each milestone lean: build what the user sees and what the rules need, and cut the rest.

**Goal:** someone who has never opened PowerShell can see what fills C: and free space on it without any way to lose a file by accident.

**What this plan is built on.** Four facts, and every design choice below comes from them:

1. Space on C: is freed only when bytes leave C:'s **volume**: they are deleted, or moved to another volume. Sorting, the Recycle Bin, "holding" folders, and moves to a `subst` letter or a junction that points back into C: free nothing.
2. The one step you can't take back is deleting a file that has no other copy. Everything here makes sure that step only happens to items you saw in a list and confirmed.
3. A GB number in the report is only worth something if the code that does the work can deliver it, with the rights it actually has. Some of C: (Windows Temp, Windows crash dumps) can only be opened by an administrator.
4. Inputs will be bad (typos in settings, odd environment variables, files that change, drives that disappear, a model that answers nonsense) and conditions hostile (C: completely full, a run interrupted, a second run started, a weekly task nobody is watching). Each of those must stop only the work it affects and say why. None may be skipped quietly, none may leave pc-tidy waiting for an answer nobody will give, and none may block the one thing a full C: needs: freeing space.

**Core design.** Every change goes through one path:
- a **dry run** builds an exact list of actions
- you type **yes**
- an **executor** does only what is on that list, re-checking each item and recording each step

**The process that builds a dry run is the one that runs it, with the same rights,** and keeps the plan in memory. A saved copy is only for reading. No plan is handed from a normal window to an elevated one. The guided flow, the existing scripts that change files and the new "what's left" view all go through that path. The report only reads.

A thinner design, a menu that calls the four scripts as they are today, would ship faster. It would keep today's gap, though: after you say yes, `cleanup.ps1` finds its files again and deletes things you were never shown.

```
scan (read-only) -> report (estimates) -> pick fixes -> dry run (exact list, in memory)
   -> type yes -> executor (lock, re-check, journal, act) -> summary: GB actually freed, how to undo
```

| # | Milestone | Covers | Days |
|---|---|---|---|
| M1 | Safety net: tests on fake folders and real test volumes, CI on windows-latest | polish: tests, CI | 4.5 |
| M2 | Safety core: exact plans, journal, lock, path and volume checks, full-disk mode | rules | 7 |
| M3 | One settings file | shared settings | 4 |
| M4 | `cleanup.ps1` on the safety core | rules, keep commands | 4 |
| M5 | Report leads with ranked fixes for C: | ranked report | 5 |
| M6 | One guided entry point | guided flow | 4.5 |
| M7 | What's left after sorting | grouping | 5.5 |
| M8 | Setup, errors, progress, help, real-PC checks | polish | 4.5 |

Tests and CI come first, ahead of the rest of the polish. The milestones after M1 rewrite the code that deletes and moves files, and "keep existing commands working" can't be checked without tests that pin down today's behaviour.

---

## Assumptions

- **C:** means the Windows system drive (`%SystemDrive%`). It is C: on nearly every PC.
- **Another drive** means another **volume**, not another letter (fact 1). M2 defines the test.
- **Non-technical user**: can double-click, read a list, type numbers and `yes`. Setup is a one-time double-click.
- **Admin rights**: on most home PCs the user is an administrator and UAC shows a Yes button. Some users have a standard account, and an administrator must type a password. pc-tidy handles both and never assumes it is elevated.
- **Unattended runs**: the only one is the README's weekly task (`tidy_folder.py --apply`). It opens a console window that nobody may be watching, so `tidy_folder.py` never waits for input.
- **Everything local**: no file names, file contents or scan results leave the PC.
  - pc-tidy itself only talks to Ollama on localhost, and it never listens on a port or pipe.
  - The only other network traffic is downloads during setup (Python packages and the Ollama model, both after asking) and winget's own catalog lookups when you have opted in to uninstalling an app.
  - **pc-tidy's own files** (scans, plans, journals, reports, logs, notices) live in the **data folder** `%LOCALAPPDATA%\pc-tidy`. Windows treats that folder as machine-local: OneDrive folder backup never syncs it, and roaming profiles never copy it.
  - pc-tidy never chooses a OneDrive folder for its output on its own (M5).
- **"A dry run and a yes"**: in the same run and the same process, pc-tidy shows the exact list, you type `yes`, and only that list is acted on. Unattended runs may move files, which can be undone, but never delete.
- **"Moves undoable"**: every move is recorded on disk before it happens and can be put back, including after a crash. Deleting files that rebuild themselves (caches, temp) can't be undone, which is why it needs the dry run and yes.
- **"Keep existing commands working"**: the same command lines, flags, prompts and default output files, except where one of the rules forces a change (two cases, listed in the last table).
  - Wording and report content may improve.
  - Settings move out of the scripts into `settings.ini`, and a one-time import is provided.
  - **No new runtime packages** are added (configparser and ctypes are in the standard library), so the weekly task keeps working with whichever Python it was registered with.
- **Windows 10 22H2 and Windows 11**, with Windows PowerShell 5.1, the shell users actually have. PowerShell 7 is not assumed.
- **Effort** is in focused developer-days for one person who knows this code, tests included.

---

## The rules, enforced by code

| Rule | How the code enforces it | When it can't be met | Proven by |
|---|---|---|---|
| Nothing deleted without a dry run and a yes | Deletions run only through the executor, from a plan built by a dry run earlier in the same process. `yes` is read from the console after the list is printed. No `--yes` flag exists anywhere. Every item is re-checked before it is acted on. Admin-only folders are listed only by an elevated process, which then asks its own yes (M6). | Changed item: skipped and listed. Folder this process can't list: the fix isn't run here. The row says "needs admin", and the wizard runs it in an admin window that makes its own list (M6). C: full: the dry run is shown on screen and yes is still required. Only the saved copy of the list is skipped (M2). | M4/M6 end-to-end tests: answer `no`, empty input, end of input, a file changed after the dry run, a file added after it, a non-elevated window that picked a Windows fix, a run on a full test volume |
| Moves undoable | The journal records an "intent" line before each move and a "done" line after it, flushed to disk. Undo works per entry. Moves never overwrite. | Journal can't be written to disk: no move and no other undoable action. Deletes can't be undone anyway, so they still run after their yes and can empty a full C:. Interrupted run: settled automatically when the disk shows what happened. Otherwise you choose at the next interactive start, and until then nothing touches those paths. | M2 crash test, overwrite test, partial-undo test, unattended-recovery test, full-volume test |
| LLM never decides deletions | The modules that build fixes, plans, protection checks and duplicate lists never import `llm.py`. Risk levels are a fixed table in code. LLM categories can't include `To-Delete`. LLM output is checked against its schema. | LLM missing or broken: everything works except the LLM's labels. | Import-boundary test, "chaos" test (M5) |
| Everything local | `llm.py` accepts only `localhost`, `127.0.0.1` or `::1`. There is no other network code and no listener of any kind. pc-tidy's own files go to `%LOCALAPPDATA%\pc-tidy`, never to a folder it detects as synced by OneDrive. | A non-local URL in settings is a settings error. If you pass `--out` into a OneDrive folder yourself, pc-tidy does it and warns. | Network tripwire and OneDrive-path test (M8) |
| Windows 10/11 | Windows build checked at start. The PowerShell wrapper is written for 5.1. | Not Windows: pc-tidy refuses to change files. Windows Server: it warns. | CI on windows-latest, plus a manual pass on Windows 10 and 11 (M8) |
| Python 3.9+ | No 3.10+ features (`match`, `X \| Y` in runtime annotations, `hashlib.file_digest`, `tomllib`). Version checked by setup and by the wrappers. | Older Python: a message naming the fix. | CI job on 3.9 |
| Keep existing commands working | The old scripts stay as thin entry points over the same core, with flags and prompts unchanged. No new runtime packages. | — | M1 tests of today's behaviour run on every CI build |

Without a yes, pc-tidy removes only things it created itself:
- a `*.pctidy-partial` copy from an interrupted move, and only while the source is intact
- category folders that an undo has just emptied
- its own old log files beyond the rotation limit

---

## Problems in today's code that this plan fixes

All were found by reading the code. Items 1 and 3 were reproduced on this PC (Windows 11, PowerShell 5.1, Python 3.13).

| # | Where | Problem | Fixed in |
|---|---|---|---|
| 1 | `cleanup.ps1` `ExtraPathsToDelete` | `Test-Protected` only blocks a path that *is exactly* a protected folder. Evaluated here, `E:\*`, `C:\Windows\*`, `C:\Program Files\*`, `%LOCALAPPDATA%\*` and `...\Documents\*` all pass. `D:\*` would delete everything it can reach on drive D:. | M2, M4 |
| 2 | `cleanup.ps1` real run | It asks for `yes` after showing only category names ("Temp files", "installers in: ..."). The installer file list appears while deleting, and folders are re-enumerated after the yes. So for plain `.\cleanup.ps1`, "nothing deleted without a dry run" isn't true. | M4 |
| 3 | `tidy_folder.py` `apply_moves` | `shutil.move` onto an existing file: on Windows `os.rename` fails, shutil falls back to copying, and **the existing file is overwritten**. If the target appears between plan and apply (for example, two runs at once), a file is silently replaced. | M2 |
| 4 | `tidy_folder.py` | The undo log is written once, at the end. A run killed partway (window closed, power cut) leaves moved files with no log, so `--undo` can't put them back. | M2 |
| 5 | Both `--undo` | The log is renamed `.undone` even when some items failed or (in tidy) were missing, with no message. Those items drop out of undo history. | M2 |
| 6 | `move_off_c.py` | An item is logged only after its original is deleted. Copies are checked by file count and total bytes only. | M2 |
| 7 | `move_off_c.py` | The default destination is the fixed drive with most free space, and many USB hard drives report as "fixed". A BitLocker-locked drive makes `disk_usage` raise and crashes the script. | M2 |
| 8 | `cleanup.ps1`, elevated as another account | `$env:TEMP`, `LOCALAPPDATA` and `USERPROFILE` then belong to the admin account. It cleans the wrong profile and never looks at the user's Downloads. | M4, M6 |
| 9 | `cleanup.ps1` app dry run | `winget list ... --accept-source-agreements \| Out-Null` accepts winget's terms for the user and hides any prompt. | M4 |
| 10 | Report summary "Clean up: about X GB" | It adds up whole folders: all of Downloads, Chrome/Edge `User Data` (the entire browser profile, not just its cache), and all conda envs (whose files are mostly hard links into conda's package cache). That overstates what can be freed. | M5 |
| 11 | `disk_report.py` | reportlab is imported and the PDF written only *after* a multi-minute scan. A missing package, or a `disk_report.pdf` still open in a viewer that locks it, loses the scan to a traceback. | M5 |
| 12 | `scanner.py` | The walk is recursive with the limit raised to 20,000. A very deep tree can overflow the C stack and kill Python with no message. | M8 |
| 13 | `tidy_folder.py --folders` | Nothing limits `--source`. `--source <your profile> --folders --apply` moves every non-hidden profile folder (Documents, Desktop, anaconda3, ...) into category folders. | M2 |
| 14 | `cleanup.ps1` Temp step | `"$env:TEMP\*"` is used as is. If TEMP has been set to a drive root or a general folder (for example `D:\`), the Temp step removes everything there that isn't in use, and nothing checks it. | M2, M4 |
| 15 | `move_off_c.py` `_same_drive` | Compares drive letters only. A `subst` or mapped letter that points into C: counts as another drive, so the originals are deleted and C: gains nothing. A second disk mounted in a folder on C: (`C:\Data`) is refused, although moving there would free C:. | M2 |
| 16 | `disk_report.py`, `tidy_folder.py` | The report goes to the current folder and tidy's log into the sorted folder (`--in-place` on the Desktop puts it there). Either can be a folder OneDrive syncs, since Desktop and Documents are synced when OneDrive folder backup is on, and then file paths are uploaded. | M2, M5 |

---

## M1. Safety net: tests on fake folders and real test volumes, CI (no product changes)

**What changes**
- Add `tests/` (pytest). `conftest.py` builds a fake profile in `tmp_path`:
  - `Downloads` with installers, duplicates, `report [1].pdf`, a name with an emoji, a path longer than 260 characters, and a read-only file
  - `AppData\Local\Temp` containing a junction (`cmd /c mklink /J`, no admin needed) to a fake `Precious` folder
- **Real test volumes**, because a `subst` letter is a folder on the same volume and can't test a move between drives:
  - A session fixture runs a `diskpart` script that creates and attaches small VHDX files under `tmp_path`, formats them and assigns free letters:
    - a 256 MB NTFS volume, "the other drive"
    - a 64 MB FAT32 volume, for the 4 GB file limit
  - M2 adds a 64 MB NTFS volume that stands in for a full C:.
  - Teardown detaches them even after a failure, and a CI step with `if: always()` detaches anything left over.
  - `diskpart` needs admin. CI runners are admin. On a developer's PC without admin these tests skip with the reason. CI sets `PCTIDY_REQUIRE_VOLUMES=1`, which turns that skip into a failure, so the volume tests can never silently stop running in CI.
  - The runner's own `D:` drive is not relied on.
  - A `subst` letter onto a folder of the fake profile is kept as the negative case (problem 15).
- Tests of today's behaviour run the legacy commands as subprocesses, with `USERPROFILE`, `TEMP` and `LOCALAPPDATA` pointed at the fake profile. `Path.home()` follows `USERPROFILE` on Windows, so M1 needs no code changes and captures exactly how the tools behave now.
  - `disk_report.py --path <fake> --out r.txt --no-llm --no-open`: exit 0, every section heading present.
  - `tidy_folder.py --source A --dest B --no-llm`: tree unchanged. `--apply`: the expected moves. `--undo`: tree identical to the snapshot taken before (names, sizes, modified times).
  - `move_off_c.py --path <fake>\Videos\Lectures --to <NTFS volume>:\From-C`, with `yes` piped in: copied to the other volume, original gone. `--undo`: back in place. This is a real cross-volume copy, verify and delete.
  - `cleanup.ps1 -DryRun` (CI runners are admin): exit 0, output contains "DRY RUN". It only reads. The real `cleanup.ps1` is not run until M4, because today's version can't be pointed at fake system folders.
- **Known problems pinned.** Each problem from the table that can be shown on fake folders gets a `pytest.mark.xfail(strict=True)` test now. The milestone that fixes it turns it into a normal test, and a strict xfail that starts passing early fails the build.
  - 1: dry run only, on a test copy of `cleanup.ps1` with `X:\*` in its SETTINGS
  - 3, 4, 5, 13
  - 14: dry run only
  - 15: `move_off_c.py --to <subst letter>` deletes originals and frees nothing
- **A write guard that also covers subprocesses.**
  - `tests\guard\sitecustomize.py` does nothing unless `PCTIDY_TEST_GUARD_ROOTS` is set. When it is, it wraps `os.remove`, `os.unlink`, `os.rmdir`, `os.rename`, `os.replace`, `shutil.rmtree`, `shutil.move` and `open` in write, append or exclusive modes, so they raise on any path outside those roots.
  - The test helper that starts every legacy command sets that variable (the test's `tmp_path` and the test volumes), puts `tests\guard` first on `PYTHONPATH` and sets `PYTHONDONTWRITEBYTECODE=1`. Python imports `sitecustomize` at startup, before `pathlib`. On Python 3.9 and 3.10, `pathlib` binds the `os` functions when it is imported, so `Path.unlink` and `Path.rename` are covered too.
  - The pytest process gets the same guard from an autouse fixture.
  - The guard's own test starts a subprocess that tries to delete a file outside the roots, and expects it to fail, on both Python versions.
  - None of this touches product code.
- `tests/fixtures/legacy_logs/` holds today's `moves_*.json` and `_logs\run_*.json` formats, so later milestones prove that old runs can still be undone.
- `.gitignore` ignores `*.txt` except `requirements.txt`. M1 adds `!requirements-dev.txt` and `!tests/**/*.txt`, otherwise git never tracks the dev requirements and CI fails.
- `.github/workflows/ci.yml`, `runs-on: windows-latest`:
  - Matrix: Python `3.9` and `3.12`.
  - `pip install -r requirements.txt -r requirements-dev.txt`, with `requirements-dev.txt` = `pytest==8.4.2`. pytest 9.x needs Python 3.10; 8.4.2 supports 3.9.
  - `python -m pytest -q`, with `PCTIDY_REQUIRE_VOLUMES=1`
  - A second job with `shell: powershell` (Windows PowerShell 5.1; the runner's default shell is PowerShell 7) runs `Install-Module Pester -MinimumVersion 5.5.0 -MaximumVersion 5.999 -Force -SkipPublisherCheck -Scope CurrentUser`, then `Invoke-Pester tests\ps -CI`. Its tests arrive in M4.
- All three pins declare Python 3.9 support on PyPI (`reportlab==5.0.1` requires >=3.9,<4; `pypdf==6.20.1` and `python-docx==1.2.0` require >=3.9). The 3.9 job proves their dependencies install too. Python 3.9 has been end-of-life since October 2025. If `setup-python` stops providing it, the job fails rather than being skipped.

**Why:** every later milestone rewrites code that moves or deletes files. These tests are the only proof that the existing commands still work, and they are the "tests on fake folders" the request asks for. The copy, verify and delete routine is the one that removes the user's originals, so it is tested between two real volumes, not two letters for one volume. The guard means a bug in a test or in the tool can't touch the developer's real files, even in a subprocess.

**How to test:**
- CI is green on both Python versions, and the log shows the volume tests ran rather than skipped.
- Break one legacy flag on a branch and confirm a test fails.
- Point one test's `--dest` outside `tmp_path` on a branch and confirm the guard stops the subprocess.
- Make the diskpart step fail on a branch and confirm the job fails.

**Effort:** 4.5 days (the volume fixture is the extra day).

**Cut:** coverage reports, extra Python versions, linting, the FAT32 volume (its test then uses a stubbed file-system name).

---

## M2. Safety core: exact plans, journal, lock, path and volume checks, full-disk mode

New package `pctidy\`. The root scripts stay as entry points. Every location (profile, system root, data folder, system drive) comes from one context object, so tests can point all of them at fakes and test volumes.

**What changes**
- **`safety.py`**: one `check(path, operation)`, called by every delete and every move. It refuses:
  1. drive roots, `C:\Users` and every folder directly in it (the profiles, including Public), and the Known Folders themselves (Desktop, Documents, Downloads, Music, Pictures, Videos, `OneDrive*`)
  2. `%SystemRoot%`, both Program Files folders, ProgramData, AppData (which holds the data folder), the pc-tidy folder, *and anything inside them*, except exactly the items in the table below
  3. a wildcard directly under a drive root, `C:\Users`, a profile or a Known Folder (`D:\*`, `Documents\*`). `D:\old-backups\*.zip` and `Documents\old drafts` remain allowed.
  4. following a junction or symlink while deleting a folder: the link is removed and its target is never touched
  5. anything outside `PCTIDY_SANDBOX_ROOT` when that variable is set (the tests use it)
  6. using a drive root, `C:\Users`, a profile folder or anything from item 2 as the folder to sort (`tidy_folder.py --source`)

  **The only exceptions to item 2.** This table matches today's `cleanup.ps1` line by line. Its test fails if a built-in fix ever lists a path that isn't in it.

  | Fix | Scope | Exactly what may be removed |
  |---|---|---|
  | Your Temp files | user | contents of `%TEMP%`, only if it passes the temp check below |
  | Windows Temp files | machine | contents of `%SystemRoot%\Temp` |
  | pip and uv caches | user | contents of `%LOCALAPPDATA%\pip\cache` and `%LOCALAPPDATA%\uv\cache` |
  | GPU shader caches | user | contents of `%LOCALAPPDATA%\NVIDIA\DXCache`, `NVIDIA\GLCache`, `AMD\DxCache`, `AMD\GLCache` and `D3DSCache` |
  | Your crash dumps | user | `%LOCALAPPDATA%\CrashDumps\*.dmp` |
  | Windows crash dumps | machine | the single file `%SystemRoot%\MEMORY.DMP`, `%SystemRoot%\Minidump\*.dmp`, `%SystemRoot%\LiveKernelReports\*.dmp` and `LiveKernelReports\*\*.dmp` |

  **Temp check** (problem 14): `%TEMP%` counts as a temp folder only if, once resolved, it isn't a drive root, `C:\Users`, a profile, a Known Folder or anything else from item 1 or 2, and it is named `Temp` or `Tmp` or is a numbered subfolder of one (remote sessions use `...\Temp\2`). Otherwise: *"Your TEMP variable points at D:\, which isn't a temp folder. The Temp fix was skipped."*

  Accepted cost: the Steam example in today's `cleanup.ps1` comment (a file inside Program Files (x86)) and app folders inside AppData can no longer go in `extra_paths_to_delete`. Remove those by hand or through the app. M3's import says so for each such entry.
- **`volume.py`: "another drive" means another volume** (problem 15).
  - `volume_id(path)` opens the path, or its nearest existing parent, with `FILE_FLAG_BACKUP_SEMANTICS` and reads `GetFinalPathNameByHandleW(..., VOLUME_NAME_GUID)`. That resolves `subst` letters, junctions and folder mount points to the volume the bytes really sit on.
  - Checked on this PC: `C:\Users` and `%TEMP%` both return `\\?\Volume{c29ad460-...}\`, and `E:\Projects` returns a different GUID.
  - `move_off_c` refuses a destination on C:'s volume: *"S: is a folder on C: (a subst drive), so moving there frees nothing on C:."* A second disk mounted in a folder on C: is accepted, because moving there does free C:.
  - The default destination is chosen from volumes, not letters: the local volume with the most free space other than C:'s. A subst letter can never be a candidate.
  - tidy uses `os.rename` only within one volume, and the copy path otherwise.
  - Every move summary shows bytes moved **and** C:'s measured free space before and after. If C: gained much less than was moved (other programs writing, hard-linked files), it says so instead of claiming the moved bytes.
- **`plan.py`**: a plan lists every action with a fingerprint:
  - files: `{op, path, size, mtime_ns, file_id}`. `file_id` comes from `os.stat`, because `DirEntry.stat()` returns `st_ino = 0` and `st_nlink = 0` on Windows (checked on this PC with a hard-linked pair: 0 and 0 from `DirEntry.stat()`, a real ID and 2 from `os.stat`).
  - rebuildable folders (`node_modules`, a venv): `{op: "delete_tree", path, files, bytes, newest_mtime}`
  - moves: `{src, dst, src_volume, dst_volume, fingerprint}`
  - external tools whose contents can't be listed item by item: `{op: "run_tool", tool: "conda_clean", interactive: false, measured: {...}}`, `{op: "run_tool", tool: "winget_uninstall", interactive: true}` or `{op: "empty_recycle_bin", measured: {items, bytes}}`, all taken from a fixed table in code

  The plan is an object held by the process that built it, and the executor takes it from memory. Its record holds the computer name, the user's SID, whether the process was elevated, a hash of the settings and its creation time. A copy is saved to `plans\` in the data folder so you can read the full list. That copy is never executed, so editing it changes nothing, and failing to save it (C: full) stops nothing.
- **`executor.py`** runs a plan in the process that built it, refusing one older than 24 h (`plan_valid_hours`) or built under other settings.

  It runs the non-interactive actions first, holding the lock. For each action it:
  1. re-runs `safety.check`, and for moves `volume_id` on both ends
  2. re-reads the item's details. If the fingerprint changed, the item is skipped as "changed since the dry run". For count-only actions it measures again, and if the count or size changed, the action is skipped and reported with the new numbers: *"Recycle Bin changed since the dry run (now 320 items, 2.6 GB). Run the fix again to see it."* It never asks a question mid-run.
  3. records an `intent` line in the journal (see below for when that must reach the disk first)
  4. acts
  5. records `done` or `failed(reason)`

  It then releases the lock and runs the **interactive** actions last (app uninstalls through winget, which may show winget's own agreement prompt and the app's own uninstaller). No lock is held while anything can wait for you. Non-interactive external tools (`conda clean`, `conda env remove`, `powercfg`) run with stdin closed, so they can't wait for input, and with a time limit (30 min for conda, 2 min for powercfg). After that, pc-tidy stops the tool, reports *"conda didn't finish in 30 minutes"* and moves on.

  Read-only files are cleared and retried. Long paths use the `\\?\` prefix. Files in use are skipped and counted. After 20 I/O errors in a row the run stops (the disk may be failing).

  It never overwrites. Moves within one volume use `os.rename`, which fails if the target exists. Moves to another volume copy to `<dst>.pctidy-partial` (opened with `'xb'`, so it can't replace anything), hash while copying (BLAKE2b), re-read and compare the copy, rename it into place, and only then delete the source.
- **`journal.py`**: one append-only JSONL file per run in `runs\` of the data folder.
  - **No journal on disk, no undoable action.** A move, or an empty-folder removal (undo recreates the folder), runs only after its `intent` line has been written and flushed to disk. If that write fails, nothing undoable runs: *"C: is too full to record the move safely. Clear some low-risk items first (they're at the top of the list)."* An unattended `tidy_folder.py --apply` then moves nothing and exits 1. It leaves a notice if one can still be written, and the wizard's free-space check explains it at the next start.
  - **Deletes don't wait for the disk.** They can't be undone, so their journal is a record, not a safety net. Their lines are buffered in memory and written as soon as a write succeeds. pc-tidy retries after every 50 MB freed, and in the summary at the latest. A crash before that loses only the record: each deleted item is either gone or still there, so there is nothing to recover.
  - **Recovery.** At start, every command that changes files reads journals that have an `intent` without a `done`.
  - **Settled automatically by any command, with no question:**
    - the action provably didn't happen (source unchanged, nothing at the destination): marked `not-started`
    - it provably finished (nothing at the source, destination matches the fingerprint): marked `done`
    - a `.pctidy-partial` copy whose source is intact: removed
    - a delete: `done` or `not-started`, depending on whether the item still exists
    - a partly deleted rebuildable folder: marked `partial` and reported
  - **Needs your decision:** a move to another volume whose copy is complete and whose source is partly deleted. It is shown with both paths and two choices, "finish the move" or "put it back". Only interactive commands ask: the wizard, `move_off_c.py`, `pc_tidy.py undo` and `pc_tidy.py recover`. Until you decide, every executor refuses actions that touch either path: *"waiting for your decision on an interrupted move"*. It is never settled quietly.
  - **`tidy_folder.py` never asks** (preview or `--apply`): it prints one line, skips only the items that touch an undecided path, does the rest and exits 3.
  - **Attention notices.** `attention.jsonl` in the data folder records what an unattended run couldn't do: an undecided recovery, a busy lock, invalid settings, skipped items. The wizard's first screen, the report's first page and `pc_tidy.py status` show these notices until you dismiss them, so a weekly task that keeps failing is never silent.
- **A full C: (the case this product exists for).**
  - Plans live in memory (above). If the readable copy can't be saved, the dry run pages the full list on screen instead of opening it in Notepad.
  - Delete-only work runs at 0 bytes free (above). The lock needs no file (below).
  - **Make room first.** When C: has less than 256 MB free, the wizard says: *"C: is full (12 MB free). Start with the low-risk items at the top, which come back on their own. Moves are offered once there's room to record them."* Dry run, yes and the High-risk second question work as usual.
  - **If no delete can make 64 MB,** a move to another volume writes its journal on the **destination** volume, in `<drive>:\From-C\_pc-tidy\`. In that case the destination is limited to that default folder, so recovery and undo can always find the journal: they look there on every ready local volume. Once the first item has freed space on C:, the journal continues in the data folder, and the copy on the other volume ends with a line pointing there.
  - The scan, report PDF, logs and notices never stop a run when they can't be written. The scan stays in memory, the ranked list is printed on screen, the PDF is saved after cleaning, and a failed log write prints one line instead of a traceback.
- **`lock.py`**: a named mutex, `Global\pc-tidy-<user SID>`, so a run started by the scheduler and a run in your window see the same lock.
  - It is created with a security descriptor that lets the same user's normal and admin windows both open it. "Access denied" on opening it counts as busy, never as free.
  - It needs no file, so it works on a full disk.
  - If the process holding it dies, Windows releases it. The next run is told *"The previous pc-tidy run ended unexpectedly. Checking for unfinished work."* and runs recovery first. No PID or stale-file logic is needed.
  - A best-effort `lock-info.json` in the data folder names the holder for messages. When it can't be read, the message just says "another pc-tidy run".
  - **Held only while non-interactive actions execute or recovery runs, never while pc-tidy or a tool it started can wait for input.** The wizard takes it after `yes` and releases it before uninstalls and the summary. Undo asks all its questions before it takes it. A window left open at a prompt or in an uninstaller therefore never blocks the weekly task.
  - If it is held: wait up to 60 s with *"Waiting for another pc-tidy run (move_off_c.py, started 18:00) to finish..."*, then exit 4: *"Another pc-tidy run is still working. Nothing was changed."* (plus an attention notice if no one is watching).
  - The lock is per user. If two admin accounts clean Windows Temp at once, neither can harm the other: each item is re-checked, and a file the other run already removed is skipped as missing.
- **`undo.py`**:
  - Undoes entry by entry, newest first.
  - Checks that what sits at the recorded place is still what was moved there. It compares the file ID recorded after the move (NTFS keeps it across renames, and the copy's new ID is recorded on the other volume), plus size and modified time. Something else in that place is left alone and reported. If the item was edited since the move, undo asks, before it starts, whether to move it back.
  - If the original place is now taken, it restores beside it as `name (restored).ext` and says so.
  - Keeps failed entries, so a later undo retries them.
  - Reads the old `moves_*.json` and `_logs\run_*.json` formats, and journals left on a destination volume.
- **`tidy_folder.py` and `move_off_c.py`** keep their flags and prompts but run on plan + executor.
  - tidy's journal goes to the data folder. It no longer writes `_logs` into the sorted folder, which can be a OneDrive-synced Desktop with `--in-place` (problem 16). `--undo` still reads old `_logs`.
  - In addition, `move_off_c`:
    - refuses network destinations
    - refuses files of 4 GB or more for FAT32 (file system from `GetVolumeInformationW`)
    - asks a second yes for USB drives (bus type via `IOCTL_STORAGE_QUERY_PROPERTY`, no admin needed): *"E: is a USB drive. The moved files will only be there while it is plugged in."*
    - skips a locked or unreadable drive and names it

**Why:** two of the rules, "dry run and yes" and "moves undoable", are only as strong as the code that touches files. Today there are three such code paths, with problems 1 to 7 and 13 to 16 above. The full-disk rules exist because a pc-tidy that refuses to work at 0 bytes free fails the one user who needs it most.

**How to test**
- Protection table: about 45 parametrized paths and patterns, including:
  - every example in the problems table
  - `%SystemRoot%\MEMORY.DMP` (allowed for Windows crash dumps) and `%SystemRoot%\notepad.exe` (refused)
  - TEMP set to `D:\`, to the profile and to `...\Temp\2`
  - `tidy_folder.py --source <fake profile> --folders --apply` (refused, nothing moved)
- Every path that today's `cleanup.ps1` removes maps to a row of the exception table, and nothing else does.
- **Volumes** (on the M1 test volumes):
  - `move_off_c.py --to <subst letter onto the fake C:>` is refused with nothing deleted. The M1 strict xfail now passes.
  - The NTFS test volume also mounted in a folder of the fake C: (`diskpart assign mount=`) is accepted as another drive.
  - The default destination never lists the subst letter.
- Target file created between plan and execute: the item is skipped and both files survive.
- Crash: `PCTIDY_TEST_CRASH_AFTER=3` (honoured only under the sandbox root) calls `os._exit` after the third move to the NTFS test volume. The next start reports the unfinished run, and `--undo` restores all three.
- Unattended recovery: leave a half-finished cross-volume move in the journal, then run `tidy_folder.py --apply` with stdin closed. It finishes without waiting, skips nothing unrelated, leaves the half-move untouched and writes an attention notice. `pc_tidy.py recover` then offers both choices.
- **Full C:** the context's system drive and data folder sit on the 64 MB test volume, filled to 0 bytes free, with 20 MB of fake Temp files.
  - The Temp fix shows its dry run on screen and runs after `yes`. Its journal reaches the disk once there is room.
  - A move picked at 0 bytes free is refused with the make-room message. With the NTFS test volume as destination, its journal is written there. Killing the run after the first item and starting again recovers from that journal.
  - A unit test makes journal writes raise `ERROR_DISK_FULL` (112): moves stop before touching anything, and deletes continue.
- **Lock:**
  - One process holds the mutex while another runs `tidy_folder.py --apply`. It waits, then exits 4 with nothing changed.
  - A process killed while holding it: the next run gets it at once, says so and runs recovery.
  - An elevated and a non-elevated process of the same user exclude each other.
  - The wizard sitting at its menu holds nothing.
  - A stub "uninstaller" that waits on its own input runs as an interactive action, and `tidy_folder.py --apply` completes meanwhile.
  - A non-interactive stub tool that never ends is stopped at its (shortened) time limit and the lock is released.
- File held open by the test (a file opened by Python can't be deleted or renamed by others): skipped as "in use", the journal entry is kept, and a second undo after closing it restores it.
- Junction inside the fake Temp folder: `Precious` is intact.
- One byte flipped in the copy on the NTFS test volume (writer monkeypatched): the move is refused and the source is intact.
- The saved plan copy edited by hand: the run still does exactly the in-memory list.
- The legacy log fixtures still undo. `tidy_folder.py --apply --in-place` writes nothing into the sorted folder except the sorted files.
- The M1 tests stay green, and the strict xfails for problems 3, 4, 5, 13 and 15 are now normal tests.

**Effort:** 7 days.

**Cut:**
- USB detection can fall back to a generic "make sure this drive stays connected" warning.
- The destination-volume journal can be cut. On a full C: that no delete can relieve, the wizard then names what to delete by hand.
- Never cut: the journal and recovery, the full-disk delete path, the lock rules, no-overwrite, the volume check, or the protection check.

---

## M3. One settings file

**What changes**
- `settings.template.ini` is tracked in git, with a comment on every key. It is copied to `settings.ini` (added to `.gitignore`) on first run. pc-tidy finds it through the script's own folder, so an admin PowerShell that starts in `System32` still finds it.
- **Why INI:**
  - Python reads it with the standard library. TOML would need an extra package on Python 3.9 and 3.10.
  - It allows comments.
  - Backslashes are literal. In a JSON or TOML double-quoted string, `"C:\new"` silently turns `\n` into a newline.
- Sections, in overview. The template has one `key = value` per line. Defaults equal today's values. The only new behaviour is `installers_min_age_days` (see M4).

  ```text
  [report]   min_gb = 1.0 / max_depth = 8 / top_files = 20 / cleanup_min_gb = 0.1 / target_free_percent = 15
  [ai]       enabled = yes / model = llama3.2:3b / url = http://localhost:11434 / timeout_seconds = 180
             max_folders = 15 / max_apps = 60
  [cleanup]  conda_cache, pip_cache, uv_cache, gpu_shader_cache, temp_files, recycle_bin,
             crash_dumps, installers = yes; hibernation = no; installers_min_age_days = 7
             installer_folders / conda_envs_to_remove / apps_to_uninstall / extra_paths_to_delete
             (lists: one item per indented line)
  [move]     min_gb = 1.0 / dest_folder_name = From-C
  [tidy]     source = %USERPROFILE%\Downloads / dest = %USERPROFILE%\Sorted / archive_after_days = 60
             skip_newer_than_min = 10 / rename_with_ai = yes / categories (list)
  [tidy.extensions]   .exe = To-Delete ... (today's RULES)
  [leftovers]         see M7
  [label.<name>] / [category.<name>] / [apptype.<name>]   your own rules, below
  ```
- **Your own rules replace editing `classify.py`.** Today the README's Customizing table sends users into `classify.py`, so all three of its tables get a settings form. Each rule is its own section, so a regex is always a value and never a key. configparser splits a line at its first `=` or `:`, which would break `^[a-z]:/...` used as a key. Example:

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

  - Your rules are checked before the built-in ones, so they can also change a built-in answer.
  - Patterns are compiled when the file is loaded, and a bad regex is an error with its line number.
  - A pattern with capital letters gets a warning, because paths are matched in lowercase with forward slashes, as `classify.py` does today.
  - `category` and `type` must be one of the existing names.
  - A `[label.*]` rule only labels folders in the report. With `kind = clean` it appears under "Things you can clean up" with your text. It is never a pc-tidy fix and never adds a GB number to the ranked list.
- **Loader.** The file is read as `utf-8-sig`, so a BOM added by an editor is harmless.
  - **It is split at `[section]` lines first, and each section is parsed on its own** with `configparser.ConfigParser(interpolation=None, strict=True)`. Every error then belongs to exactly one section.
  - A section that appears twice is an error in that section only. A line before the first section is reported and ignored.
  - The default interpolation would choke on `%USERPROFILE%`. pc-tidy expands `%VAR%` itself, and a `%` left over afterwards means an undefined variable, which is an error.
  - `strict` makes a duplicated key an error instead of letting the last one win.
- **Comments.**
  - A line starting with `#` or `;` is a comment anywhere.
  - On yes/no and number values, `  # text` at the end of the line is also a comment, so `temp_files = no  # later` means `no`. Those values can't contain `#`, so this can't cut a real value.
  - On paths, lists, patterns and text, `#` stays part of the value, because a folder may be called `C# projects`. A value with ` #` in it gets a warning, not an error: *"line 31: if '# old' is a comment, put it on its own line."*
- **Validation** lists all errors at once, with line numbers:
  - unknown key, with a suggestion: *"settings.ini line 14: unknown key 'pip_cahce' in [cleanup]. Did you mean 'pip_cache'?"*
  - wrong type: *"temp_files must be yes or no, got 'ture'"*
  - out-of-range numbers
  - relative paths
  - protected paths (`safety.check`): *"extra_paths_to_delete 'D:\*' is refused: a wildcard directly under a drive root could empty the drive. List the folders you mean, e.g. D:\old-backups."*
  - a non-local AI URL
  - `To-Delete` in the tidy categories, so the LLM can never file things where the installer fix deletes them
  - bad rule sections, as above
- **An error only stops what it affects.** Each command declares the sections it reads, and refuses to start (exit code 2, naming the line) only when one of those has an error:

  | Command | Reads | If a section it reads is broken |
  |---|---|---|
  | `tidy_folder.py` (and the weekly task) | `[tidy]`, `[tidy.extensions]`, `[ai]` | `[tidy]` or `[tidy.extensions]`: exit 2 plus an attention notice. Sorting with defaults could file things where you didn't ask. `[ai]`: runs rules-only, as with `--no-llm`, which only leaves more files in place, plus a notice. |
  | `move_off_c.py` | `[move]` | exit 2 |
  | `.\cleanup.ps1` | `[cleanup]` | exit 2. It reads every switch, so any typo there could flip one. |
  | `disk_report.py` | `[report]`, `[ai]`, rule sections | read-only: a broken section uses its defaults, and a broken rule section is skipped, with a banner naming the line |
  | wizard | all | Errors are shown at step 1. A fix is unavailable only if it reads a key with an error, so a typo in a `[cleanup]` switch (which the wizard ignores, M4) doesn't hide the Temp fix. |

  So `pip_cahce = no` in `[cleanup]` stops `.\cleanup.ps1` with the line number, and Sunday's sort and `move_off_c.py` run as usual.
- **Errors are caught when they are made, not on Sunday.**
  - `settings edit` opens Notepad, waits for it to close and validates at once.
  - The wizard validates at start.
  - An unattended `tidy_folder.py --apply` that is stopped by its own sections writes a notice that the next interactive start shows: *"The weekly sort didn't run on 2026-10-18: settings.ini line 52 in [tidy] ..."*. The task's exit code 2 also shows as its "Last Run Result" in Task Scheduler.
- **Precedence:** command-line flag, then `settings.ini`, then the default. `python pc_tidy.py settings show` prints each value and where it came from. `settings check` validates.
- The constants are removed from the scripts, and their docstrings point to `settings.ini`.
- **Upgrading existing users.** Their edited `cleanup.ps1` and `.py` files will block `git pull`. The README's "Upgrading" steps:
  1. Rename the folder to `pc-tidy-old`.
  2. Clone fresh.
  3. Double-click `setup.cmd`. It finds `..\pc-tidy-old` and offers `settings import`.

  The import, in detail:
  - It reads the old SETTINGS block with regexes (`$Name = $true/$false`, `@("...")` lists, `$env:USERPROFILE` expanded) and drops the old inline `#` comments.
  - It reads the old `.py` constants and `classify.py`'s rule lists with Python's `ast`, mapping the category names (`CACHE`, `SYSTEM`, ...) to their values, so nothing is executed.
  - Rule entries that differ from the released ones become `[label.*]`, `[category.*]` or `[apptype.*]` sections.
  - **An old entry that 2.0 would refuse is not imported.** For example, a `$ExtraPathsToDelete` entry that follows the comment's Steam example inside Program Files (x86). It is listed with the reason: *"Not imported: it is inside Program Files, which pc-tidy 2.0 won't delete from. Remove it by hand or through the app."* The imported file is therefore always valid.
  - It shows every value it imported and writes only after a yes. Lines it can't parse are listed, not guessed.

**Why:** one place to edit, and typos become errors instead of silent changes. Today, writing `$TempFiles = $ture` in `cleanup.ps1` makes `$ture` an undefined variable, which is `$null`. Temp files are then quietly skipped, and nothing says why. An error must still stop only what it affects: a typo in the cleanup switches must not stop the weekly sort, which never reads them.

**How to test:**
- Fixture files with:
  - a BOM, a duplicate key, a duplicate section, a typo, a `C:\new` path, `%NOPE%`, `D:\*`
  - a non-local URL, `To-Delete` as a category
  - a `[label.x]` whose pattern contains `:` and `=` (parsed intact), a bad regex and an unknown category
  - a path containing `#` (kept, warned)
- A missing file gets created from the template. Precedence is checked.
- A user rule overrides a built-in one in a fixture scan.
- **Isolation:** with `pip_cahce = no` in `[cleanup]`, `tidy_folder.py --apply` (stdin closed) and `move_off_c.py` both run normally. `.\cleanup.ps1` exits 2 naming line and key. The wizard still offers the Temp fix.
- `temp_files = no  # later` reads as `no`.
- A broken `[tidy]` makes the weekly command exit 2 with a notice. A broken `[ai]` makes it sort by rules only, with a notice.
- An import from a fixture "old folder" round-trips correctly. That folder has an edited SETTINGS block with inline comments, the Steam example as a real entry, and an edited `classify.py`. The Steam entry is listed as not imported, and the result passes `settings check`.

**Effort:** 4 days.

**Cut:** importing old `.py` constants and `classify.py` (keep the `cleanup.ps1` import, the file every user edited), and `settings edit`. Never cut the rule sections (without them, users still edit `classify.py`) or the per-section isolation.

---

## M4. cleanup.ps1 on the safety core

**What changes**
- The cleanup actions move into `pctidy\cleanup.py`. Each is a fix with a fixed scope (user or machine), fixed folders (the M2 exception table) and its own dry-run builder. The same builder, run in measure mode, produces the report's numbers (M5). Steps that mixed both scopes are split, so a non-admin window can do its half and say what is left:

  | Fix | Scope | What it does |
  |---|---|---|
  | Your Temp files | user | Your Temp folder, after the temp check |
  | Windows Temp files | machine | `Windows\Temp` |
  | Caches | user | pip, uv, GPU shader caches |
  | Your crash dumps | user | `%LOCALAPPDATA%\CrashDumps` |
  | Windows crash dumps | machine | `MEMORY.DMP`, `Minidump`, `LiveKernelReports` |
  | Installers | user | Top-level installer files in `installer_folders`, older than `installers_min_age_days` (new, default 7, so a just-downloaded installer survives) |
  | Recycle Bin | user | Size and count via `SHQueryRecycleBinW`, the same numbers Explorer shows. Emptied per drive with `SHEmptyRecycleBinW`. Measuring all of `C:\$Recycle.Bin`, as today, can count other accounts' deleted files that emptying your own bin doesn't remove. |
  | Hibernation | machine | `powercfg /h off`; the estimate is the size of `hiberfil.sys` |
  | conda | user | `conda clean --all --dry-run --json` for the dry run, `conda clean --all --yes` with stdin closed for the run. Env names are checked against `conda env list --json`. |
  | Apps | user | Existence checked against the registry list `classify.py` already reads (offline, instant). The uninstall runs `winget uninstall --exact --name <name>` in the visible window, without `--accept-source-agreements`, so the user sees and answers winget's own prompts. It is an **interactive** action: it runs last, after the lock is released (M2). |
  | Extra paths | user | `safety.check`, then a per-file plan |

  `temp_files = yes` in `[cleanup]` turns on both Temp fixes, and `crash_dumps = yes` both crash-dump fixes, exactly as the single setting did.
- Recycle Bin and `conda clean` can't be listed item by item for the executor. Their dry run records counts and sizes, the executor measures again just before acting, and if anything changed the fix is skipped and reported with the new numbers (M2). It never asks again mid-run.
- `cleanup.ps1` becomes a wrapper of about 60 lines:
  - The same `param([switch]$DryRun)`, the same admin check and message.
  - Comment-based help (`Get-Help .\cleanup.ps1`). `--help`, `-h` and `/?` are caught through a remaining-arguments parameter, instead of PowerShell's "positional parameter cannot be found" error.
  - **Finding Python.** It tries, in this order, `$PSScriptRoot\.venv\Scripts\python.exe`, then `py -3`, then `python` on PATH.
    - A candidate counts only if `-c "import sys; print(sys.version_info[:2])"` prints 3.9 or newer.
    - The Microsoft Store stub `python.exe` prints "Python was not found" and exits 49 (seen on this PC), while the Python install manager's `py.exe` in the same `WindowsApps` folder works. So the test is "does it run", not "where is it".
    - If nothing passes: *"cleanup.ps1 now runs on Python. Double-click setup.cmd in the pc-tidy folder once, then run this again."* and exit 1.
  - It then runs `& $py "$PSScriptRoot\pc_tidy.py" legacy-cleanup [--dry-run]`. Giving the script's full path matters: `python -m pctidy...` would fail from an admin window that starts in `System32`, because `-m` looks in the current folder.
- The `[cleanup]` switches decide what `.\cleanup.ps1` does, exactly as the SETTINGS block did. The guided flow (M6) ignores the switches: it offers every fix that applies, with nothing preselected, and adds the three lists (`conda_envs_to_remove`, `apps_to_uninstall`, `extra_paths_to_delete`) as fixes you declared yourself.
- `.\cleanup.ps1` without `-DryRun` now **always prints the full dry-run list first**, then asks "Type yes", then runs that exact plan in the same elevated process. `-DryRun` behaves as before.
- **Elevated as another account** than the signed-in user (detected with `WTSQuerySessionInformationW` on the console session versus the process user): user-scope steps are skipped with a message naming both accounts, and machine-scope steps run.
- **Summary:** per fix, estimated versus removed; skipped items grouped by reason; free space per drive before and after (as today). Exit code 0 if all done, 3 if something was skipped, 1 on error.

**Why:** this makes "dry run and yes" true for the most dangerous script, closes the wildcard and TEMP holes, and leaves one tested engine instead of two.

**How to test**
- pytest, using fake `TEMP`, `LOCALAPPDATA` and `USERPROFILE` and a fake system root (with a fake `MEMORY.DMP`) under the sandbox root:
  - the dry run lists exactly the fake files, `MEMORY.DMP` included
  - files added after the dry run survive the real run
  - a file changed after the dry run survives
  - `no`, empty input and end of input all leave everything unchanged
  - TEMP pointed at the fake drive root: the Temp fix is refused and the root is untouched (the M1 xfail for problem 14 now passes)
  - Recycle Bin measured differently at execute time (stubbed): skipped with the new numbers, no prompt
  - an app uninstall (stubbed winget that waits for input) runs after the lock is released, and the conda stub gets closed stdin
- Pester on PowerShell 5.1:
  - `-DryRun` maps to `--dry-run`
  - `--help` prints help
  - the missing-Python message appears
  - the non-admin message is unchanged
  - with `X:\*` in `extra_paths_to_delete`, `.\cleanup.ps1 -DryRun` refuses it with the line number and deletes nothing. This replaces the M1 xfail for problem 1, which ran the old script.
- The real Recycle Bin, hibernation and winget calls are mocked in CI and checked by hand in M8.

**Effort:** 4 days.

**Cut:** listing individual Recycle Bin items (count and size are enough); the conda env size estimate.

---

## M5. Report leads with ranked fixes for C:

**What changes**
- **`pctidy\fixes.py`** builds `Fix` objects from rule matches. Each has:
  - a stable `id` (`temp-user`, `temp-windows`, `move:Videos/Lectures`)
  - a title
  - estimated bytes, "up to X GB", "unknown", or "needs admin to measure"
  - risk and a one-line reason
  - scope (user or machine)
  - either "pc-tidy can do it" or exact manual steps
  - undo text
- **Where the numbers come from.** The tree scan (`scanner.py`) only finds the folders, and keeps no per-file data beyond the biggest files. `DirEntry.stat()` also reports `st_nlink = 0` on Windows (checked, see M2).
  - So each fix's number comes from its own M4 dry-run builder, run in **measure mode** over that fix's folders only. It lists the files and calls `os.stat` on each, which opens the file and fills in the link count and file ID.
  - It calls `GetCompressedFileSizeW` only for files whose attributes (free from the directory listing) say compressed or sparse.
  - Measured on this PC over 20,000 files: `os.stat` 42 µs per file, `GetCompressedFileSizeW` 107 µs. A 200,000-file conda env takes about 10 s with a warm cache.
  - **Budget:** if measuring one fix passes 60 s or 300,000 files, it stops. The row says "up to X GB (shared files not checked)", sorts with the unknowns and adds nothing to the total. A slow disk gives a vaguer number, never a bigger one.
- **Risk is a fixed table in code.** It is never a setting and never the LLM.
  - **Low**: comes back by itself, nothing of yours is lost. Temp files, download caches, shader caches, crash dumps.
  - **Medium**: can be reversed with some effort. Moves (pc-tidy undo), installers and apps (download or reinstall), `node_modules` and venvs (rebuild), hibernation (`powercfg /h on`), duplicate copies (an identical copy remains).
  - **High**: gone for good, with no copy left and no way back. Emptying the Recycle Bin, your `extra_paths_to_delete`, the previous Windows installation (no going back to the old Windows).
- **GB rule:** a number is shown only when it is the sum of exactly the files the fix would remove, each counted once.
  - A file already claimed by a higher-ranked fix isn't counted again (for example, an installer inside a folder that is also offered for moving).
  - Files with more than one hard link (`st_nlink > 1`, from `os.stat`) count as 0, because removing one link frees nothing. A conda env whose files are hard links into conda's package cache shows: *"old-env: 4.2 GB of files, 0.6 GB freed (the rest is shared with conda's package cache)"*.
  - Compressed and sparse files count their size on disk.
  - Today's whole-folder items are split up. Downloads becomes its installers, duplicates and move candidates as separate fixes. conda envs become one fix per env. Chrome and Edge become manual fixes ("Delete browsing data > Cached images and files" in the browser), sized by their `Cache`, `Code Cache` and `GPUCache` folders only and marked "about", because the browser decides exactly what it clears.
  - Moves count only when the destination is another volume (M2). With no other volume, there are no move rows, and the report says so.
  - Where the size can't be computed (Docker's disk image, WSL disks), the row says "unknown (folder is X GB)" and sits at the bottom of its risk tier.
  - **Admin-only folders.** A scan without admin rights can't open `Windows\Temp`, `Minidump` or `LiveKernelReports`. Those rows say "needs admin to measure", sit at the bottom of their tier, and add nothing to the total. They never show 0 or a guess. `hiberfil.sys` can be measured by anyone, so the hibernation row always has a number.
- **Order:** Low, then Medium, then High; biggest first within a tier; unknown sizes last. The order is risk-first because many readers act only on the first few lines. A running total shows when those aren't enough. Under the row where the total reaches `target_free_percent`: *"C: has 9 GB free (2%). Fixes 1–5 bring it to about 15%."* If the Low rows can't get there, the line says so.

  Example (illustrative numbers, scan run with admin rights):

  | # | Fix | Frees | Total | Risk | How | Undo |
  |---|---|---|---|---|---|---|
  | 1 | pip and uv download caches | 3.1 GB | 3.1 | Low: downloaded again when needed | pick 1 | not needed |
  | 2 | Windows Temp files | 2.6 GB | 5.7 | Low: Windows recreates them | pick 2 (admin) | not needed |
  | 3 | Your Temp files | 1.6 GB | 7.3 | Low: apps recreate them | pick 3 | not needed |
  | 4 | Move `Videos\Lectures` to `E:\From-C` | 38.0 GB | 45.3 | Medium: undoable | pick 4 | `python pc_tidy.py undo` |
  | 5 | Turn off hibernation | 12.7 GB | 58.0 | Medium: no hibernate or Fast Startup | pick 5 (admin) | `powercfg /h on` (admin) |
  | 6 | Previous Windows installation | 21 GB | 79.0 | High: no rollback | Manual: Start > *Disk Cleanup* > C: > *Clean up system files* > tick *Previous Windows installation(s)* > OK | none |
  | 7 | Empty Recycle Bin (312 items) | 2.4 GB | 81.4 | High: can't be undone | pick 7 | none |

  Without admin rights, row 2 reads "Windows Temp files: needs admin to measure", moves to the bottom of the Low tier, and the totals leave it out.

  "pick N" means: type N in pc-tidy's guided flow, or run `python pc_tidy.py fix N`. Both show the dry run and ask for yes. "(admin)" means the fix runs in a window with admin rights (M6).
- **Page 1** is the C: drive bar, any attention notices (M2), the fixes table and the target line. Then "What's left" (M7), then today's sections: categories, cleanable items on other drives, move candidates, apps, folder trees, biggest files, good to know, and items matched by your own `[label.*]` rules. The LLM's folder descriptions stay last, with their warning. The text output follows the same order.
- The fix list is saved to `scans\<scan_id>.json` in the data folder, so `python pc_tidy.py fix 3` uses the report's numbers. If it is older than 24 h: *"This report is from <date>. Run a new scan first."* If it couldn't be saved (C: full), the guided flow keeps it in memory, and `fix N` says to use the guided flow.
- **Where the report goes** (problem 16):
  - The guided flow saves to `reports\disk_report-<date>-<time>.pdf` in the data folder, prints that path and offers to open the folder. Reports are kept until you delete them. Each is about 100 KB.
  - `python disk_report.py` keeps today's default, `disk_report.pdf` in the current folder, with two exceptions. If that folder is under `%SystemRoot%` (an admin window that started in `System32`), or inside a OneDrive folder, the report goes to the data folder's `reports\` instead and the message says where and why: *"This folder is synced by OneDrive, so the report was saved to C:\Users\Mai\AppData\Local\pc-tidy\reports instead, which isn't uploaded."*
  - OneDrive folders are the roots in the `OneDrive`, `OneDriveConsumer` and `OneDriveCommercial` variables and in `HKCU\Software\Microsoft\OneDrive\Accounts\*\UserFolder`. Paths are resolved first, so a Documents folder redirected into OneDrive is caught.
  - Other sync apps (Dropbox, Google Drive) can't be detected reliably. That is why pc-tidy's own default is the data folder.
  - An `--out` you give yourself is honoured. If it is inside a OneDrive folder, pc-tidy warns that the file will be uploaded.
- `--path D:\` without C:: the section says "C: wasn't scanned" instead of showing an empty table.
- Before scanning, the report checks it can write its output file. If the file is locked by a viewer, it uses `disk_report-<date>-<time>.pdf` and says so. When a PDF is requested, reportlab is imported at start, so a missing package fails within a second, with "Run setup.cmd". Text-only runs (`--out report.txt`) still work without reportlab, as they do today.

**Why:** the report must first answer "what do I do, how much do I get, what could go wrong". Every GB number must be one the executor can deliver with the rights it will have, so it is measured by the same code that will act. The report lists your file paths, so it must never be placed where it gets uploaded.

**How to test**
- Fixture scans produce a snapshot of the fix list and its order.
- Overlap: installers inside a folder that is also moved are counted once.
- A file and an `os.link` to it count as 0 in the fix that deletes one of them.
- A fake conda env hard-linked into a fake `pkgs` shows files versus freed as above.
- A file compressed with `compact /c` counts its size on disk.
- The measurement budget, stubbed low, gives "up to" and leaves the total alone.
- A Chrome profile counts only its cache bytes.
- Unknown sizes sort last.
- A fake `Windows\Temp` made unreadable (deny ACL set with `icacls` on the fake folder): the row says "needs admin to measure" and the total leaves it out.
- With no other volume in the context: no move rows, and the "no other drive" line.
- Import-boundary test: an `ast` walk proves `fixes`, `plan`, `executor`, `safety`, `cleanup` and the duplicate finder never import `llm`.
- **Chaos test:** `llm.ask_json` is monkeypatched to return hostile values ("delete C:\Users", huge sizes, invalid categories). The fixes, sizes, risks and plan actions must be identical to a `--no-llm` run.
- The text of PDF page 1, read with pypdf, starts with the fixes table.
- A locked output file gets the fallback name.
- With `OneDrive` pointed at a fake folder and the current folder inside it, `python disk_report.py` writes to the data folder and says so. An explicit `--out` there is written, with the warning.

**Effort:** 5 days (per-file measurement is the extra day).

**Cut:** the running total and target line (keep the plain ranking); the per-fix risk reason (keep the level). Never cut measure mode: without it the numbers overstate again.

---

## M6. One guided entry point

**What changes**
- `python pc_tidy.py` with no arguments (or a double-click on `pc-tidy.cmd`, or the Start menu shortcut from setup) starts a console wizard. Console rather than GUI because:
  - tests can drive every screen through standard input
  - nothing listens on a port a web page could reach
  - it runs in the same kind of window as the old tools
- `pc-tidy.cmd` is tracked in the repo. It runs `cd /d "%~dp0"`, then `"%~dp0.venv\Scripts\python.exe" "%~dp0pc_tidy.py"`, so "Run as administrator", which starts in `System32`, behaves like a double-click. If `.venv` is missing, it prints "Double-click setup.cmd first". In both cases it ends with `pause`, because a double-clicked window otherwise closes before the summary can be read. The wizard also ends with "Press Enter to close".
- **Admin rights, without handing plans across.** pc-tidy reads its token's elevation type (`GetTokenInformation`, `TokenElevationType`):
  - **Limited** (an administrator account, UAC-filtered window): step 1 asks once: *"Some fixes are in Windows folders. Restart pc-tidy with admin rights so it can measure and clean them? Windows will ask. (Y/n)"*. On yes, it starts itself again in a new window with `ShellExecuteExW` and the `runas` verb, under the same account, so your Temp and Downloads are still yours. The first window closes, and everything (scan, report, dry run, yes, run) happens in the elevated window. If the prompt is declined (`ShellExecuteExW` fails with `ERROR_CANCELLED`, 1223), it carries on without admin.
  - **Full** (already elevated): it checks that the elevated account is the console user (as in M4). If it isn't, your user-scope fixes are skipped with the M8 message.
  - **Default, not an admin** (a standard account): it stays as it is. Windows fixes you pick are done in an admin window at step 9.
- **Steps:**
  1. **Checks:** Windows 10/11, settings (errors shown, affected fixes marked unavailable, M3), attention notices, unfinished runs (M2 recovery, with its questions, including journals on destination volumes), LLM available or not (optional), admin rights as above, and **free space on C:**. Under 256 MB, the wizard switches to "make room first" (M2): it says so, and the low-risk deletes lead the list.
  2. **Scan C:** with progress. Other drives are only checked for free space and volume identity. Ctrl+C stops it, and a scan never changes anything.
  3. **Report:** the PDF is saved to the data folder's `reports\` and opened, its path is printed, and the ranked list is printed. If C: is too full to save the PDF, the list is printed and the PDF is saved after step 8.
  4. **Pick:** *"Type the numbers of the fixes you want (e.g. 1,2,4 or 1-3), L for all low-risk ones, or press Enter to stop."* Nothing is preselected. Out-of-range or garbled input is rejected with the reason ("There is no fix 12"). Today's `parse_choice` drops such input silently. Manual fixes print their steps. In a window without admin rights, Windows fixes can be picked and are marked "admin window".
  5. **Dry run:** for everything this window can do, the plan is built now, from the disk as it is, not from the scan. For each fix it shows the count, the size and the first 15 items. The full list goes to a text file, and `O` opens it in Notepad (or pages it on screen if the file couldn't be saved). These numbers are exact; the report's were estimates. Picked Windows fixes in a non-admin window are listed as *"Done in an admin window after this, which shows its own list first."*
  6. **Confirm:** type `yes`. High-risk fixes ask again, one at a time, saying what will be lost. Empty input, end of input or anything else cancels. No lock is held while waiting.
  7. **Run:** takes the lock, shows progress per fix for the non-interactive actions, and releases the lock. App uninstalls then run one by one in this window, with no lock held: *"Uninstalling Docker Desktop. If its own uninstaller opens, follow it there."*
  8. **Summary:** removed versus estimated per fix, skipped items by reason, C: free space before and after, the undo command, and the journal path. Exit codes 0, 3 or 1.
  9. **Windows fixes from a non-admin window** (only if any were picked): *"Opening an admin window for: Windows Temp files, hibernation. Windows will ask for permission, and an administrator may need to type a password. That window lists the exact files and asks for yes before removing anything."*
     - It starts `pc_tidy.py windows-fixes --only temp-windows,hibernation` with `ShellExecuteExW`, `runas` and `SEE_MASK_NOCLOSEPROCESS`.
     - That command accepts only the closed list of machine fixes from the M2 table and never touches any profile folder, since it may run as another account. It builds its own dry run with admin rights, asks yes in its own window, runs, shows its own summary and keeps its own journal and lock in the data folder of the account it runs as.
     - The first window waits on the process handle, holding no lock, then shows C: free space before and after.
     - If the prompt is declined: *"Not done: Windows Temp files, hibernation (needs admin)."*

  pc-tidy runs from a folder its user can edit, so whoever approves its UAC prompt is trusting that folder. The closed list protects against mistakes and bad settings, not against someone who can rewrite pc-tidy itself.
- **After the summary, a menu:** Sort a folder (tidy preview, yes, apply; undo), What's left (M7), Undo a recent change (the last 10 runs of any tool), Open reports folder, Settings, Help. The lock is free while the menu waits, so the weekly task can run.
- **Command line** for people who prefer it: `pc_tidy.py scan | report | fix <numbers> [--dry-run] | sort | move | undo [--list] | recover | status | windows-fixes [--only ...] | settings show|check|edit|import | selftest`. Each has `--help` with examples. There is **no `--yes` flag**: a deletion always needs `yes` typed after its dry run in the same process, so unattended runs can only move (undoable), never delete.

**Why:** this is the core of the request. It reuses the same dry-run, yes, execute path as the old scripts, so the rules hold here without any new deletion code. Building each dry run in the process that will act keeps the rule true for folders only an administrator can open. A user whose C: is full gets a working first step instead of an error.

**How to test:** end-to-end on the fake profile with scripted input:
- pick `1,3`, answer `no`: tree unchanged
- answer `yes`: only the listed files are gone, and undo restores the moves
- garbage input, end of input, `1,99`
- elevation type stubbed as Limited, Full and Default: the restart offer, the other-account message, and the step 9 hand-over, each as specified. A declined UAC prompt (`ShellExecuteExW` stubbed to return 1223) leaves the Windows rows as "needs admin" and the rest working.
- `windows-fixes` run directly under the sandbox with a fake system root: lists the files, `no` leaves everything, `yes` removes only the listed files, a file added after its dry run survives, and an id outside the closed list is refused
- the wizard waiting at the menu while `tidy_folder.py --apply` runs: the sort completes
- the wizard at step 7 inside a stub uninstaller that waits for input while `tidy_folder.py --apply` runs: the sort completes
- the full-C: test volume from M2: step 1 shows "make room first", the Temp fix runs, and the summary shows C: before and after
- `pc-tidy.cmd` started with `System32` as the current folder: same behaviour as from its own folder
- a stale scan is refused
- lock held by another process during step 7: waits, then a clean exit 4

**Effort:** 4.5 days.

**Cut:** the self-restart and the `windows-fixes` window. Windows rows then say *"needs admin: right-click pc-tidy.cmd > Run as administrator"* on admin accounts, and *"ask an administrator to run .\cleanup.ps1"* on standard accounts. Also the run-history picker (keep "undo last").

---

## M7. What's left after sorting

**Scope:** the personal folders (Desktop, Documents, Downloads, Pictures, Videos, Music, `Sorted`, plus `[leftovers] extra_folders`). These are the **scope roots**. Never AppData, dot-folders or system folders. It runs after "Sort a folder" in the wizard and also appears as a report section. Duplicate and old-file fixes join the ranked C: list from M5, measured the same way. Empty folders go under "Tidy-ups".

**Reading files without changing "last opened".** The scan records each file's last-access time from `stat` before anything opens a file, and the stale group uses those recorded times. Every read of file contents (duplicate hashing, copy verification, tidy's AI snippets) goes through one opener:
- `CreateFileW` with `GENERIC_READ | FILE_WRITE_ATTRIBUTES`
- then `SetFileTime` with both halves of the last-access `FILETIME` set to `0xFFFFFFFF`, which tells NTFS not to update it for anything done through this handle
- then wrapped with `msvcrt.open_osfhandle`, so pypdf and python-docx read from it like any file

If that access is refused, a duplicate candidate or snippet file isn't read at all and is counted as "not checked". A copy check falls back to a plain read, since the source is about to be deleted anyway.

**What changes**
- **Duplicates.**
  - **Finding them:** files of at least `duplicate_min_mb` (default 1) are grouped by size, then by a hash of their first and last 64 KB, then by a full BLAKE2b hash.
  - **Skipped:**
    - cloud-only OneDrive placeholders (reading them would download them; `scanner.py` already knows their attributes)
    - hard links to the same file (same file ID from `os.stat`)
    - zero-byte files
  - **Inside projects:** listed under "Duplicates inside projects (not offered)", with size and both paths, never proposed for deletion. A build may expect both copies.
  - **Keeper:** a copy outside Downloads, Desktop and Sorted, otherwise the oldest.
  - **What is proposed for deletion:** only extra copies sitting in Downloads, Desktop or Sorted, where copies usually end up by accident. Duplicates between two folders you made yourself are listed for you to decide. Whether a copy can be deleted never depends on which `Sorted` subfolder the LLM chose.
  - **Before deleting:** the executor checks that keeper and extra still have the fingerprints they had when hashed. If either changed, the item is skipped.
  - **Budget:** hashing stops at `hash_budget_gb` (default 50) and says how many groups weren't checked.
  - Risk: Medium.
- **Big files not opened for months.**
  - Files of at least `stale_min_mb` (500), last opened at least `stale_after_days` (180) ago, by the time the scan recorded.
  - Windows records "last opened" only if the lowest bit of `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\NtfsDisableLastAccessUpdate` is 0, and even then up to an hour late. On this PC `fsutil behavior query disablelastaccess` reports 2 ("System Managed, Last Access Time Updates ENABLED"). If the bit is 1 or the value can't be read, pc-tidy uses last-modified instead, and the heading says: *"Not changed in 6 months (Windows isn't recording when files are opened on this PC)."*
  - Offered action: move off C: to another volume (undoable). These files are never offered for deletion: old doesn't mean unneeded.
- **Empty folders.**
  - A folder with no files at any depth.
  - Excluded: Known Folders, folders containing `desktop.ini`, hidden or system folders, junctions, folders inside projects (a build may expect them), and folders changed in the last 7 days. The section says how many were left out because they are inside projects.
  - Removing them frees no space, so they are listed under "Tidy-ups", outside the GB ranking. Removal is journaled on disk (it is undoable), and undo recreates them.
- **Files by project.**
  - **Where a project can be:** strictly below a scope root, so never a Known Folder, `Sorted` or an extra folder itself. Never a folder pc-tidy created either: `Sorted\<category>`, `Sorted\Archive`, `Sorted\Archive\<category>`, an old `_logs`, and the category folders that `--in-place` runs made (known from their journals). A project can sit *inside* one of those, for example `Sorted\Projects and code\robot-arm`, moved there whole by `--folders`.
  - **What makes a project:** a `.git` folder on its own, or a manifest together with matching source in the same folder or below it:
    - `package.json` with `node_modules`, a lock file (`package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`), or a `.js` or `.ts` file
    - `pyproject.toml`, `setup.py` or `requirements.txt` with a `.py` file
    - `Cargo.toml` with a `src` folder
    - `CMakeLists.txt` with a `.c`, `.cpp` or `.h` file
    - a `.sln` with a `.csproj` or `.vcxproj` below it
    - an `.Rproj` with a `.R` file
  - A loose `package.json` in `Sorted\Data` or a `.blend` on the Desktop makes nothing a project. `.blend` and `.prproj` files are documents and are grouped as loose files.
  - The outermost qualifying folder wins.
  - **Loose files:** files in Sorted, Downloads or Desktop are grouped when 3 or more share a name stem after removing ` (1)`, `_v2`, `final`, `copy` and dates.
  - Each group shows size, file count and last change.
  - **Offered actions:**
    - move a project off C: (undoable)
    - in projects untouched for 30 days, rebuildable folders as Medium fixes: `node_modules` (with `package.json` beside it), `venv` or `.venv` (containing `pyvenv.cfg`), `__pycache__`, and `target` (with `Cargo.toml` beside it). Their sizes come from M5's measure mode, so a venv whose files are hard links counts only what deleting it frees.
  - The LLM may name a group, shown as "AI label". It adds nothing else.

**Why:** once Downloads is sorted, the space that remains is in copies, forgotten big files and old projects, which rules alone can't name.

**How to test:** fake trees with:
- identical content under different names, and same size with different content
- `os.link` hard links (not reported)
- a file marked offline via `SetFileAttributesW` (never opened; `open` is monkeypatched to fail on it)
- each keeper case
- duplicates between Documents and Pictures (report only)
- ages set with `os.utime`, and the registry value mocked both ways (the heading text changes)
- **last access kept:** the kernel32 wrapper is mocked to prove `SetFileTime(..., 0xFFFFFFFF)` is called before the first read. Then a file's access time is set a year back with `os.utime`, the file is hashed through the opener, and its `st_atime_ns` is unchanged. The stale list is identical before and after a run that hashed the same files. Where the CI volume doesn't record last access, this second check proves nothing, so the M8 manual checklist repeats it on a PC that does.
- **false projects:** run `tidy_folder.py --apply` on a fake Downloads holding `package.json`, `requirements.txt`, a `.blend` and duplicate PDFs. No project is found in `Sorted`, and the duplicates in `Sorted\Data` are offered. A `.blend` or `requirements.txt` directly on the fake Desktop doesn't make the Desktop a project.
- a `.git` project in Documents, and one moved whole into `Sorted\Projects and code`, are both found. Duplicates inside them are listed as "not offered".
- nested empty folders, `desktop.ini`, Known Folders
- nested projects (the outermost wins)
- the chaos test again (only the labels change)

**Effort:** 5.5 days.

**Cut first:** name-stem grouping of loose files, then LLM labels, then the project rebuild-folder fixes. Keep duplicates, stale files, empty folders and the access-time opener.

---

## M8. Polish: setup, errors, progress, help, real-PC checks

**What changes**
- **Setup.**
  - `setup.cmd` (double-click) runs `powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"`. The bypass applies to that one process only, so setup works before the README's `Set-ExecutionPolicy` step and changes no system setting. `%~dp0` makes it work from any current folder.
  - `setup.ps1` steps:
    1. checks the Windows build (10/11; warns on Server)
    2. finds Python 3.9+ through `py -3`, `python` or conda, using the same "does it run" test as `cleanup.ps1` (M4), so the Microsoft Store stub is rejected. If none is found, it opens python.org's download page and says to tick "Add python.exe to PATH".
    3. checks the pc-tidy folder isn't inside a OneDrive folder (M5's detection). If it is: *"pc-tidy is in a folder OneDrive syncs, so your settings.ini would be uploaded with it. Move the pc-tidy folder to, for example, C:\pc-tidy, then run setup again."* It continues if you choose to.
    4. creates `.venv` and installs the pinned requirements
    5. runs `Unblock-File` on the repo's own `.ps1` files (a ZIP download carries Mark-of-the-Web, which RemoteSigned blocks)
    6. creates `settings.ini` or offers the import
    7. checks for Ollama and offers `ollama pull` (about 2 GB, asks first)
    8. offers a Start menu shortcut
    9. runs `pc_tidy.py selftest`: imports, write access to the data folder, the named mutex, and a dry run on a temporary fake folder
  - It is safe to run again, and takes `-NonInteractive` for CI.
- **Errors.** One top-level handler. The user sees what happened, why, and the one next step. Tracebacks go to `logs\pc-tidy.log` in the data folder (rotating, 1 MB × 5) and are shown only with `--debug`. If the log itself can't be written, the handler prints one line saying so and never fails because of it. Each message matches a row in the README's troubleshooting table. Examples:

  | Situation | Message |
  |---|---|
  | Report PDF locked | *"Couldn't write disk_report.pdf because another program has it open. Saved the report as disk_report-2026-10-21-2140.pdf instead."* |
  | Elevated as another account | *"This window runs as 'Admin', but 'Mai' is signed in. Mai's Temp and Downloads were not touched. Run pc-tidy from Mai's account; it opens an admin window for the Windows fixes."* |
  | Drive unavailable | *"Drive E: can't be read (locked by BitLocker or disconnected). Skipped it."* |
  | Destination on C: | *"S: is a folder on C: (a subst drive), so moving there frees nothing on C:. Pick a different drive."* |
  | C: full | *"C: is full (12 MB free). Start with the low-risk items at the top. Moves are offered once there's room to record them."* |
  | Settings error elsewhere | *"settings.ini line 14 in [cleanup]: unknown key 'pip_cahce'. This doesn't affect sorting; .\cleanup.ps1 won't run until it's fixed."* |
  | Weekly sort blocked | *"The weekly sort on 2026-10-18 skipped 2 files: they are part of an interrupted move that needs your decision. Choose now? (finish / put back / later)"* |
- **Progress.** Written to stderr at most once a second, and off when output isn't a console:
  - scan: current top folder, files, GB, elapsed time, and how long the last scan took
  - fix measurement: fix name, files checked
  - copy: GB done of total, MB/s, time left
  - hashing and deleting: items done of total
  - LLM: *"3 of 15. Ctrl+C skips the AI part."*

  The scanner walk becomes iterative (problem 12), which also makes the current-folder display possible. An unreadable or locked drive is skipped and named.
- **Where files go:** everything pc-tidy writes for itself is in `%LOCALAPPDATA%\pc-tidy` (M2). Reports follow the M5 rule, so an admin window that starts in `System32` never writes there, and a OneDrive-synced folder is never chosen by pc-tidy.
- **Help:** `--help` with examples on every command (argparse epilog). `cleanup.ps1` gets comment-based help (M4).
- **README rewritten:** Start (`setup.cmd`, then the pc-tidy shortcut); the guided flow; "Using the individual tools" with every old command unchanged; a settings table replacing "Customizing", including your own rules; where pc-tidy keeps its files; Upgrading; troubleshooting as the error catalogue.

**Why:**
- A non-technical user meets setup and the first error message before any of the safety work. If setup needs a terminal command, or an error is a traceback, they stop there.
- A scan or a copy takes minutes. A window that shows nothing looks frozen, and people close frozen windows in the middle of a move.
- `--help`, comment-based help and the rewritten README are how the old commands stay usable alongside the new entry point.
- CI's Windows Server image has no UAC prompts, OneDrive, USB disks or real full disks. Those can only be checked by hand on real Windows 10 and 11 PCs before release.

**How to test**
- `setup.ps1 -NonInteractive` on a clean CI runner, run twice.
- `--help` exits 0 and shows examples for every command.
- A forced exception shows no traceback without `--debug`. The same with the log folder made unwritable shows one extra line and no crash.
- Progress is silent when output isn't a console.
- **Network tripwire:** a full end-to-end run with `socket.socket.connect` and `socket.socket.bind` patched to fail on anything that isn't a loopback connection to Ollama.
- **OneDrive path test:** with `OneDrive` and `OneDriveConsumer` pointed at a fake folder that holds the fake Documents and Desktop, a full wizard run plus `tidy_folder.py --source <fake Desktop> --in-place --apply` writes nothing under that folder except the sorted files themselves.
- **Manual release checklist on real PCs.** windows-latest is a Server image with no OneDrive, USB drives or UAC prompts, so check these by hand on Windows 10 22H2 and Windows 11:
  - an admin account: the restart-with-admin offer, accepted and declined
  - a standard account: the step 9 admin window, with an administrator's password, and declined
  - OneDrive folder backup turned on: the report, journals and logs are not in any OneDrive folder, and `python disk_report.py` run from a synced folder saves to the data folder and says so
  - a USB disk as the destination
  - `subst S: C:\Temp\x`, then `move_off_c.py --to S:\`: refused
  - C: nearly full (a test PC filled with `fsutil file createnew`): make room first, then a move
  - Ollama off, then on
  - `disk_report.pdf` open in a PDF viewer during a report
  - the README's weekly task still runs and is journaled. Run it again in three situations, and it completes each time:
    - with the wizard left open at its menu
    - with an uninstaller left open at step 7
    - with an interrupted move left undecided (the notice also appears at the next start)
  - the process killed during a move, then undo
  - on a PC where `fsutil behavior query disablelastaccess` reports last-access updates on: run a duplicate scan, then confirm a year-old file's last-access time is unchanged

**Effort:** 4.5 days.

**Cut:** the Start menu shortcut (`pc-tidy.cmd` in the folder is enough), time-left estimates.

---

## Existing commands after the upgrade

| Command | Behaviour in 2.0 | Test |
|---|---|---|
| `python disk_report.py` | Same. The report now opens with the fixes for C:. Uses a fallback file name if the PDF is locked. **Change forced by "everything local":** if the current folder is under Windows or inside a OneDrive folder, the PDF goes to `%LOCALAPPDATA%\pc-tidy\reports` and the path is printed. Without admin rights, Windows-folder rows say "needs admin to measure" (the README still says to run it as administrator). | M1, M5 |
| `python disk_report.py --path C:\` (and `--out`, `--no-llm`, `--no-open`) | Same. An `--out` inside OneDrive is written, with a warning. | M1, M5 |
| `.\cleanup.ps1 -DryRun` | Same. Reads `[cleanup]` from `settings.ini`. | M4 (Pester) |
| `.\cleanup.ps1` | Shows the full dry-run list before "Type yes", then deletes only that list, in the same elevated window. Refuses a TEMP that isn't a temp folder. App uninstalls run last. | M4 |
| `python move_off_c.py` (`--path`, `--to`, `--min-gb`) | Same prompts. Copies are hash-checked. A USB destination asks twice. **Change forced by "moves undoable" and fact 1:** a destination on C:'s own volume (a subst letter, a junction) is refused. | M1, M2 |
| `python move_off_c.py --undo` | Same, and it also undoes moves made before the upgrade | M2 (legacy fixtures) |
| `python tidy_folder.py` (`--apply`, `--undo`, `--source`, `--dest`, `--in-place`, `--folders`, `--no-llm`) | Same. Journaled in the data folder instead of `_logs`, never overwrites, and old `_logs` still undo. One new limit: `--source` refuses a drive root, a profile folder or a system folder (problem 13). | M1, M2 |
| README weekly task (`tidy_folder.py --apply`) | Same. Never waits for input: an undecided interrupted move only makes it skip the items involved and leave a notice. If another run is executing, it waits up to 60 s, then exits 4 with nothing changed and a notice. A wizard window left open, even inside an uninstaller, doesn't block it. Only an error in `[tidy]` or `[tidy.extensions]` stops it; a typo elsewhere in `settings.ini` doesn't. It keeps using the Python it was registered with, because no runtime packages are added. | M2, M3 (run with stdin closed), M8 manual |

---

## Effort and what to cut

**Total:** about 39 focused days, roughly eight weeks full-time (M1 4.5, M2 7, M3 4, M4 4, M5 5, M6 4.5, M7 5.5, M8 4.5).

**If time is short**, cut in this order. Each cut still covers every item in the request, and together they save about 6.5 days:
1. Name-stem grouping and LLM labels in M7 (1.5 days).
2. The self-restart and the `windows-fixes` admin window in M6, replaced by "run as administrator" messages (1.5 days).
3. Start menu shortcut and time-left estimates in M8 (1 day).
4. Importing old `.py` constants and `classify.py`, and `settings edit`, in M3 (1 day). The rule sections stay, so nobody has to edit a script.
5. Running total and target line in M5 (0.5 day).
6. USB bus-type detection in M2, replaced by a generic warning (0.5 day).
7. The destination-volume journal for a full C: in M2, replaced by naming what to delete by hand (0.5 day).

**Never cut:**
- the protection check and its exception table
- the volume check ("another drive" means another volume)
- plans with per-item re-checks, and dry runs built by the process that acts
- the journal and recovery, and deletes that still work on a full C:
- the lock rules (never held while anything can wait for input)
- unattended runs that never wait for input
- per-section settings validation
- no-overwrite moves
- measure-mode GB numbers
- the access-time opener
- the test write guard, the LLM-boundary and chaos tests, the real test volumes, and the Python 3.9 CI job

**Out of scope for this version:** a GUI, scheduled deletions, automating Disk Cleanup, DISM or Windows.old, OneDrive automation, registry cleaning, auto-update, telemetry.

A "soft delete" was also left out on purpose:
- The Recycle Bin frees nothing on C: until it is emptied, and depending on the shell flags, a file too big for the bin can be deleted outright without a warning.
- A pc-tidy holding folder also frees nothing until it is emptied.

The safeguards instead are fingerprint checks of duplicates right before deleting, the dry run and the typed yes.
