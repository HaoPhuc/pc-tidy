r"""Removing pc-tidy.exe from this PC when you are done with it: it keeps nothing of its own on C:.

What goes, and nothing else:
- in the data folder (%LOCALAPPDATA%\pc-tidy), the files pc-tidy makes there (OWN, by name), then
  each of its folders if that is empty. Anything else in it stays, and so does the folder.
- pc-tidy.exe itself. A running exe can't be deleted, so a hidden PowerShell waits for pc-tidy to
  end, checks that the file is still the same one (its SHA-256), and removes that one file.
Every path goes through safety.check with the operation "self", which allows only the data folder
and pc-tidy.exe. Nothing is ever chosen by the folder pc-tidy.exe is in (Downloads, often).

What stays: the record of moves to another drive, which undo needs. It is copied first to that
drive, into <drive>\<the moves' top folder>\_pc-tidy, where every pc-tidy looks for records, with a
note beside it. A new download of pc-tidy.exe finds it and can put the files back.

It removes nothing while an interrupted run waits to be settled, or when a record can't be copied
to its drive (a USB drive that isn't plugged in, say).
"""

import base64
import fnmatch
import hashlib
import os
import shutil
import subprocess
import sys

from . import _win, context, journal, lock, recovery, safety
from .paths import lp, norm

# folder in the data folder -> the names of the files pc-tidy makes there
OWN = {
    "": ("settings.ini", "attention.jsonl", "attention-dismissed.jsonl", "lock-info.json"),
    "reports": ("disk_report-*.html", "disk_report-*.pdf", "disk_report-*.txt"),
    "plans": ("*-dry-run.txt", "*.json"),
    "runs": ("*.jsonl",),
    "logs": ("pc-tidy.log", "pc-tidy.log.*"),
    "app": (),          # where pc-tidy.exe unpacks itself: its own loader empties it when it ends
}
NOTE_NAME = "READ ME - moved from C.txt"
QUESTION = ("\nRemove pc-tidy from this PC? That removes pc-tidy.exe and what it keeps in {data} (its reports, "
            "lists and records). The record of moves to another drive stays on that drive.\n"
            "Press Enter to remove it, or type K to keep it for next time: ")


class Refused(Exception):
    """pc-tidy can't remove itself now. str(e) says why, and what to do."""


def offer(ctx=None, ask=None, say=print):
    """Ask, and remove pc-tidy's own files after a yes (Enter). Returns a function to call right
    before pc-tidy ends, which starts the removal of pc-tidy.exe itself, or None if nothing is to
    happen. The end of input means keep: pc-tidy never removes itself without an answer."""
    from . import ui
    ctx = ctx or context.current()
    answer = (ask or ui.ask)(QUESTION.format(data=ctx.data_dir))
    if answer is None or answer.strip().lower() not in ("", "y", "yes"):
        say("pc-tidy stays. It asks again next time.")
        return None
    try:      # no other pc-tidy run may be writing its records meanwhile
        held = lock.Lock(ctx, "pc-tidy remove").acquire(wait=0, say=lambda *_: None)
    except lock.Busy:
        say("pc-tidy stays: another pc-tidy run is working right now. Close it, then remove pc-tidy.")
        return None
    try:
        kept = keep_move_records(ctx)
        for line in kept:
            say(line)
        left = remove_own_files(ctx)
    except (Refused, safety.Refused) as e:
        say(f"pc-tidy stays: {e}")
        return None
    finally:
        held.release()
    if left:
        say(f"{len(left)} item(s) in {ctx.data_dir} aren't pc-tidy's, so they and that folder stay.")
    if not ctx.exe:
        return None
    say("pc-tidy.exe is removed when this window closes.")
    return lambda: remove_exe_after_exit(ctx)


# ---------- what stays: the record of moves ----------
def keep_move_records(ctx):
    """Copy each record of moves that undo can still put back to the drive they went to. Lines to
    show. Raises Refused if a run waits to be settled or a record can't reach its drive."""
    if recovery.scan(ctx):
        raise Refused("a run was interrupted and waits to be settled. Start pc-tidy again: it does that "
                      "first, and asks you about a half-done move.")
    runs_dir = norm(journal.runs_dir(ctx))
    out, notes = [], {}
    for run in journal.load_runs(ctx):
        moves = run.done_moves()
        if not moves:
            continue
        folder = _record_folder(moves[0].intent["dst"])
        if folder is None:
            raise Refused(f"the drive of {moves[0].intent['dst']} can't be found (is it plugged in?), so the "
                          "record of what was moved there can't be kept there.")
        for path in run.files:
            if norm(os.path.dirname(path)) != runs_dir:
                continue          # already on that drive (a run on a full C: kept it there)
            try:
                _copy_checked(path, os.path.join(folder, os.path.basename(path)))
            except OSError as e:
                raise Refused(f"the record of the moves couldn't be saved in {folder} ({e.strerror or e}). "
                              "Is that drive plugged in?") from None
        notes.setdefault(os.path.dirname(folder), []).append((run, moves))
    for top, entries in notes.items():
        note = os.path.join(top, NOTE_NAME)
        try:
            _write_note(note, entries)
        except OSError:
            pass          # the record itself is there, and that is what undo needs
        out.append(f"Kept on {os.path.splitdrive(top)[0] or top}: the record of what was moved there, so a new "
                   f"download of pc-tidy.exe can put it back. See {note}")
    return out


def _record_folder(dst):
    """<the destination's mount>\\<its top folder>\\_pc-tidy (volume.journal_folders looks there), or
    None if its drive can't be found."""
    try:
        mount = _win.volume_path_name(dst)
    except OSError:
        return None
    rel = os.path.relpath(os.path.abspath(dst), mount)
    top = rel.split(os.sep)[0]
    if top in ("", ".", ".."):
        return None
    return os.path.join(mount, top, context.VOLUME_JOURNAL_FOLDER)


def _copy_checked(src, dst):
    """Copy a record to dst and read it back. An existing identical copy is fine."""
    with open(lp(src), "rb") as f:
        data = f.read()
    if os.path.exists(lp(dst)):
        with open(lp(dst), "rb") as f:
            if f.read() == data:
                return
    os.makedirs(lp(os.path.dirname(dst)), exist_ok=True)
    tmp = dst + ".pctidy-partial"
    with open(lp(tmp), "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    with open(lp(tmp), "rb") as f:
        if f.read() != data:
            raise OSError(f"the copy of {os.path.basename(src)} didn't read back the same")
    os.replace(lp(tmp), lp(dst))


def _write_note(path, entries):
    lines = ["These were moved here from C: by pc-tidy, to free space on C:.",
             "To put them back where they were, download pc-tidy.exe again, start it, type M,",
             "and pick 'Undo the last move off C:' in the menu (or run: pc-tidy.exe undo).",
             f"The record pc-tidy reads for that is in the folder {context.VOLUME_JOURNAL_FOLDER} next to this note.",
             "Please leave that folder where it is.", ""]
    for run, moves in entries:
        lines.append(f"Moved on {run.when}:")
        lines += [f"  {a.intent['src']}  ->  {a.intent['dst']}" for a in sorted(moves, key=lambda a: a.n)]
        lines.append("")
    with open(lp(path), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------- what goes ----------
def own_files(ctx):
    """(pc-tidy's own files in the data folder, folders pc-tidy.exe unpacked itself into on earlier
    runs, the other items there that stay)."""
    own, unpacked, other = [], [], []
    running = norm(getattr(sys, "_MEIPASS", "")) if context.FROZEN else None
    for sub, patterns in OWN.items():
        folder = os.path.join(ctx.data_dir, sub) if sub else ctx.data_dir
        try:
            entries = list(os.scandir(lp(folder)))
        except OSError:
            continue
        for e in entries:
            path = os.path.join(folder, e.name)
            if e.is_dir(follow_symlinks=False):
                if not sub and e.name in OWN:
                    continue      # one of pc-tidy's own folders: looked at on its own
                if sub == "app" and e.name.startswith("_MEI"):
                    if norm(path) != running:     # this run's own goes when it ends
                        unpacked.append(path)
                    continue
                other.append(path)
            elif any(fnmatch.fnmatch(e.name.lower(), p) for p in patterns):
                own.append(path)
            else:
                other.append(path)
    return own, unpacked, other


def remove_own_files(ctx):
    """Remove pc-tidy's own files and earlier runs' unpacked folders, then its folders where that
    left them empty. Returns the items that stay because they aren't pc-tidy's."""
    from .executor import _remove_tree     # never follows a link
    own, unpacked, other = own_files(ctx)
    for path in own:
        safety.check(path, "self", ctx)
        try:
            os.remove(lp(path))
        except OSError:
            other.append(path)
    for path in unpacked:
        safety.check(path, "self", ctx)
        _remove_tree(path)
    for sub in [s for s in OWN if s] + [""]:
        folder = os.path.join(ctx.data_dir, sub) if sub else ctx.data_dir
        safety.check(folder, "self", ctx)
        try:
            os.rmdir(lp(folder))
        except OSError:
            pass          # not empty (app holds this run's unpacked files until it ends), or not there
    return other


def file_hash(path):
    h = hashlib.sha256()
    with open(lp(path), "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def cleanup_script(exe, sha256, folders, pids, quiet=True, log=None):
    """The PowerShell that removes pc-tidy.exe once the processes `pids` have ended: only that one file,
    and only while its SHA-256 is still `sha256`; then each of `folders` if it is empty. `log` (the
    tests' only) gets a line per step."""
    def q(text):
        return "'" + str(text).replace("'", "''") + "'"
    # .NET only, no cmdlets from modules: started from a PowerShell 7 window, Windows PowerShell can
    # inherit a module path where Get-FileHash and friends fail to load (seen on CI: an empty hash)
    return "\n".join([
        "$ErrorActionPreference = " + ("'SilentlyContinue'" if quiet else "'Continue'"),
        f"$log = {q(log or '')}",
        "function Note([string]$text) { if ($log) { [System.IO.File]::AppendAllText($log, "
        "[DateTime]::Now.ToString('HH:mm:ss.fff') + ' ' + $text + [Environment]::NewLine) } }",
        "function Hash([string]$path) { try { $s = [System.IO.File]::OpenRead($path)",
        "    try { [BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($s)).Replace('-', '') }",
        "    finally { $s.Dispose() } } catch { '' } }",
        "Note 'started'",
        f"foreach ($id in @({', '.join(str(p) for p in pids)})) {{",
        "    try { [void][System.Diagnostics.Process]::GetProcessById($id).WaitForExit(120000) } catch {}",
        "    Note \"waited for $id\" }",
        "[System.Threading.Thread]::Sleep(500)",
        f"$exe = {q(exe)}",
        "$hash = Hash $exe",
        "Note \"hash $hash\"",
        f"if ($hash -eq {q(sha256)}) {{",
        # a virus scanner may hold a new exe for a while: keep trying for a minute
        "    for ($i = 0; $i -lt 60 -and [System.IO.File]::Exists($exe); $i++) {",
        "        try { [System.IO.File]::Delete($exe) }",
        "        catch { Note ('not yet: ' + $_.Exception.Message); [System.Threading.Thread]::Sleep(1000) } } }",
        "Note ('exe still there: ' + [System.IO.File]::Exists($exe))",
        # never recursive: a folder goes only if it is empty
        f"foreach ($d in @({', '.join(q(f) for f in folders)})) {{ try {{ [System.IO.Directory]::Delete($d, $false) }} catch {{}} }}",
        "exit 0",
    ])


def remove_exe_after_exit(ctx, wait_for=None):
    """Start a hidden PowerShell that waits for pc-tidy to end, then removes pc-tidy.exe if it is
    still the same file, then the unpack folder and the data folder if they are empty. Returns the
    process, or None if it couldn't start (then the exe stays)."""
    exe = ctx.exe
    safety.check(exe, "self", ctx)
    folders = [os.path.join(ctx.data_dir, "app"), ctx.data_dir]
    for folder in folders:
        safety.check(folder, "self", ctx)
    # pc-tidy.exe runs as two processes: its loader, which empties the unpack folder last, and Python
    pids = [os.getpid(), os.getppid()] if context.FROZEN else [os.getpid()]
    script = cleanup_script(exe, file_hash(exe), folders, pids if wait_for is None else wait_for,
                            log=ctx.test_value("PCTIDY_TEST_CLEANUP_LOG"))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    shell = shutil.which("powershell") or os.path.join(os.environ.get("SystemRoot", "C:\\Windows"),
                                                       "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    # a hidden console of its own (PowerShell runs nothing without one, so not DETACHED_PROCESS), its own
    # process group (Ctrl+C in pc-tidy's window doesn't reach it), and out of any job pc-tidy.exe's loader
    # keeps its processes in, where Windows allows that, so it outlives the loader
    flags = 0x08000000 | 0x00000200       # CREATE_NO_WINDOW, CREATE_NEW_PROCESS_GROUP
    # a PowerShell 7 window's module path breaks Windows PowerShell's own modules: let it use its default
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    for extra in (0x01000000, 0):         # CREATE_BREAKAWAY_FROM_JOB, then without it
        try:
            return subprocess.Popen([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                     "-EncodedCommand", encoded], creationflags=flags | extra, close_fds=True, env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            continue
    return None
