"""Runs a plan in the process that built it: only what is on it, each item re-checked and journaled.

It refuses a plan older than 24 hours or built under other settings, in another window or as
another user. It runs the non-interactive actions first, holding the lock. For each action it:
  1. re-runs safety.check, and for moves volume_id on both ends
  2. re-reads the item. A changed item is skipped as "changed since the dry run"; a count-only
     action (the Recycle Bin, a tool) whose numbers changed is skipped with the new numbers.
     It never asks a question mid-run.
  3. records an "intent" line in the journal (on disk first, for anything undo can put back)
  4. acts
  5. records "done" or "failed"
Then it releases the lock and runs the interactive actions (uninstallers), which may wait for you.

It never overwrites. A move within one volume is os.rename, which fails if the target exists. A
move to another volume copies to <dst>.pctidy-partial (opened with 'xb', so it can't replace
anything), hashing while it copies (BLAKE2b), re-reads and compares the copy, renames it into place,
and only then deletes the source.
"""

import hashlib
import os
import shutil
import stat
import subprocess
import time
from collections import namedtuple

from . import _win, context, journal, safety, volume
from .lock import Lock
from .paths import inside, lp
from .plan import (PLAN_VALID_HOURS, computer_name, fingerprint, is_dir_stat, is_link, settings_hash,
                   tree_stats, unchanged)
from .progress import Line, duration

PARTIAL = ".pctidy-partial"
MAX_IO_ERRORS_IN_A_ROW = 20
DEST_RESERVE = 200 * 1024 ** 2       # keep this much free on the destination beyond the data
CHUNK = 1024 * 1024
UNDOABLE = ("move", "rmdir")
TOO_FULL = ("{drive} is too full to record the move safely. Clear some low-risk items first "
            "(they're at the top of the list).")
WAITING = f"waiting for your decision on an interrupted move (run: {context.command('recover')})"

Tool = namedtuple("Tool", "label command interactive timeout measure")


def _recycle_bin_measure(_action=None):
    """(items, bytes) in the Recycle Bins of every drive, the numbers Explorer shows."""
    import ctypes

    class Info(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint32), ("i64Size", ctypes.c_int64), ("i64NumItems", ctypes.c_int64)]
    info = Info(ctypes.sizeof(Info), 0, 0)
    if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) != 0:
        return None
    return {"items": info.i64NumItems, "bytes": info.i64Size}


def _recycle_bin_empty(_action=None):
    import ctypes
    flags = 0x1 | 0x2 | 0x4      # no confirmation, no progress window, no sound
    result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
    if result not in (0, -2147418113):      # E_UNEXPECTED: already empty
        raise OSError(f"the Recycle Bin couldn't be emptied (error {result & 0xFFFFFFFF:#x})")


# The external tools pc-tidy may run: a fixed table in code. Non-interactive ones get closed stdin
# and a time limit; interactive ones run last, with the lock released.
TOOLS = {
    "conda_clean": Tool("conda", lambda a: ["conda", "clean", "--all", "--yes"], False, 30 * 60, None),
    "conda_env_remove": Tool("conda", lambda a: ["conda", "env", "remove", "--name", a["name"], "--yes"],
                             False, 30 * 60, None),
    "hibernation_off": Tool("powercfg", lambda a: ["powercfg", "/h", "off"], False, 2 * 60, None),
    "winget_uninstall": Tool("winget", lambda a: ["winget", "uninstall", "--exact", "--name", a["name"]],
                             True, None, None),
}
COUNT_ONLY = {"empty_recycle_bin": ("Recycle Bin", _recycle_bin_measure, _recycle_bin_empty)}


class PlanRefused(Exception):
    """The executor won't run this plan at all (too old, other settings, another window)."""


class Skip(Exception):
    """This one action isn't done, for a reason that isn't an I/O error."""


class Stuck(Skip):
    """A move to another volume stopped halfway: its journal entry must stay open for a decision."""


class Outcome:
    def __init__(self, action, status, reason=None, nbytes=0):
        self.action, self.status, self.reason, self.bytes = action, status, reason, nbytes

    @property
    def name(self):
        a = self.action
        return a.get("src") or a.get("path") or a.get("tool") or a["op"]

    def __repr__(self):
        return f"<{self.status} {self.name}: {self.reason}>"


class Result:
    def __init__(self, plan):
        self.plan = plan
        self.outcomes = []
        self.free_before = self.free_after = None
        self.stopped = None
        self.journal_path = None
        self.journal_on_disk = True
        self.in_use = 0
        self.stuck = []        # moves left half-done, waiting for a decision

    def with_status(self, status):
        return [o for o in self.outcomes if o.status == status]

    @property
    def done(self):
        return self.with_status("done")

    @property
    def skipped(self):
        return [o for o in self.outcomes if o.status in ("skipped", "failed")]

    @property
    def bytes_moved(self):
        return sum(o.bytes for o in self.done if o.action["op"] == "move")

    @property
    def bytes_freed(self):
        return sum(o.bytes for o in self.done if o.action["op"] in ("delete", "delete_tree"))


def check_plan(plan, settings=None):
    """Raise PlanRefused if this plan may not run in this process now."""
    age = time.time() - plan.created
    if age > PLAN_VALID_HOURS * 3600 or age < -300:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(plan.created))
        raise PlanRefused(f"This list is from {when}, more than {PLAN_VALID_HOURS} hours ago. "
                          "Run the dry run again to see what's there now.")
    if settings is not None and settings_hash(settings) != plan.settings_hash:
        raise PlanRefused("The settings changed since the dry run. Run the dry run again.")
    if (plan.computer, plan.sid, plan.elevated) != (computer_name(), _win.user_sid(), _win.is_elevated()):
        raise PlanRefused("This list was made in another window or by another user. pc-tidy only runs a list "
                          "in the window that showed it to you.")


def execute(plan, ctx=None, settings=None, say=print, on_action=None, on_outcome=None):
    """Run a plan. Raises PlanRefused, or lock.Busy if another run kept the lock for a minute."""
    ctx = ctx or plan.ctx
    check_plan(plan, settings)
    result = Result(plan)
    result.free_before = volume.free_bytes(ctx.system_drive)
    header = {"run": plan.id, "command": plan.command, "plan": plan.id, "created": plan.created,
              "computer": plan.computer, "sid": plan.sid, "elevated": plan.elevated,
              "settings_hash": plan.settings_hash, "info": plan.info}
    jr = journal.Journal(ctx, header)
    runner = _Runner(plan, ctx, jr, result, say, on_action, on_outcome)
    first = [a for a in plan.actions if not a.get("interactive")]
    last = [a for a in plan.actions if a.get("interactive")]
    lk = Lock(ctx, plan.command).acquire(say=say)
    try:
        try:
            runner.open_journal()
            runner.blocked = _blocked_paths(ctx, plan.id)
            for a in first:
                runner.run(a)
        finally:
            lk.release()
        for a in last:
            runner.run(a)
    except BaseException:
        jr.close_file()
        raise
    result.free_after = volume.free_bytes(ctx.system_drive)
    summary = {"done": len(result.done), "skipped": len(result.skipped), "moved": result.bytes_moved,
               "freed": result.bytes_freed, "free_before": result.free_before, "free_after": result.free_after}
    jr.close(summary)
    result.journal_path = jr.path
    result.journal_on_disk = jr.ok is not False and not jr.pending
    return result


def _blocked_paths(ctx, own_run):
    """Paths of interrupted actions that recovery couldn't settle: nothing may touch them."""
    out = []
    for run in journal.load_runs(ctx):
        if run.id == own_run:
            continue
        for a in run.unfinished():
            out += a.paths()
    return out


class _Runner:
    def __init__(self, plan, ctx, jr, result, say, on_action, on_outcome):
        self.plan, self.ctx, self.journal, self.result, self.say = plan, ctx, jr, result, say
        self.on_action, self.on_outcome = on_action, on_outcome
        self.blocked = []
        self.io_errors = 0
        self.moves_acted = 0
        self.on_volume = False      # the journal is on the destination volume (full C:)
        hook = ctx.test_value("PCTIDY_TEST_CRASH_AFTER")
        self.crash_after = int(hook) if hook else None

    # ---------- journal ----------
    def open_journal(self):
        if not self.journal.start():
            self._to_destination_volume()

    def _to_destination_volume(self):
        """C: is too full for the journal: keep it on the destination volume, in <drive>:\\From-C\\_pc-tidy,
        where recovery and undo look for it. Only when every move goes into that default folder."""
        fallback = self.plan.journal_fallback
        moves = [a for a in self.plan.actions if a["op"] == "move"]
        if self.on_volume or not fallback or not moves:
            return False
        if not all(inside(a["dst"], os.path.dirname(fallback)) for a in moves):
            return False
        self.on_volume = self.journal.move_to(fallback)
        return self.on_volume

    def _back_to_data_folder(self):
        """A full-C: run whose journal is on the destination: continue it in the data folder once a
        move has freed some room there."""
        if self.on_volume and self.journal.move_to(journal.runs_dir(self.ctx)):
            self.on_volume = False

    # ---------- one action ----------
    def run(self, a):
        if self.on_action:
            self.on_action(a)
        if self.result.stopped:
            return self._outcome(a, "skipped", self.result.stopped)
        try:
            self._recheck(a)
        except (Skip, OSError) as s:
            reason = str(s) if isinstance(s, Skip) else _reason(s)
            self.journal.note({"t": "skipped", "n": a["n"], "reason": reason})
            return self._outcome(a, "skipped", reason)
        intent = dict(a, t="intent")
        if a["op"] in UNDOABLE:
            try:
                self._write_durable(intent)
            except journal.JournalUnavailable:
                return self._outcome(a, "skipped", TOO_FULL.format(drive=self.ctx.system_drive[:2]))
        else:
            self.journal.note(intent)
        try:
            outcome = getattr(self, "_" + a["op"])(a)
        except Skip as s:
            if not isinstance(s, Stuck):
                self.journal.note({"t": "failed", "n": a["n"], "reason": str(s)})
            return self._outcome(a, "skipped", str(s))
        except FileNotFoundError:
            self.journal.note({"t": "failed", "n": a["n"], "reason": "it's gone"})
            return self._outcome(a, "skipped", "it's gone")
        except OSError as e:
            if _win.in_use(e):
                self.result.in_use += 1
                reason = "it is in use by another program"
                self.journal.note({"t": "failed", "n": a["n"], "reason": reason})
                return self._outcome(a, "skipped", reason)
            self.io_errors += 1
            if self.io_errors >= MAX_IO_ERRORS_IN_A_ROW:
                self.result.stopped = (f"stopped after {MAX_IO_ERRORS_IN_A_ROW} errors in a row "
                                       "(the disk may be failing)")
            self.journal.note({"t": "failed", "n": a["n"], "reason": str(e)})
            return self._outcome(a, "failed", _reason(e))
        self.io_errors = 0
        return outcome

    def _outcome(self, a, status, reason=None, nbytes=0):
        o = Outcome(a, status, reason, nbytes)
        self.result.outcomes.append(o)
        if self.on_outcome:
            self.on_outcome(o)
        return o

    def _write_durable(self, record):
        """journal.write, moving the journal to the destination volume first if C: has no room."""
        try:
            return self.journal.write(record)
        except journal.JournalUnavailable:
            if not self._to_destination_volume():
                raise
            return self.journal.write(record)

    def _done(self, a, nbytes, **record):
        try:
            self._write_durable(dict(record, t="done", n=a["n"]))
        except journal.JournalUnavailable:
            pass        # it happened; recovery will see that on disk and record it
        return self._outcome(a, "done", None, nbytes)

    def _recheck(self, a):
        op = a["op"]
        paths = {"move": [(a.get("src"), "move"), (a.get("dst"), "move-to")],
                 "delete": [(a.get("path"), "delete")], "delete_tree": [(a.get("path"), "delete")],
                 "rmdir": [(a.get("path"), "rmdir")]}.get(op, [])
        for path, operation in paths:
            problem = safety.why_refused(path, operation, self.ctx)
            if problem:
                raise Skip(f"refused: {problem}")
            if any(inside(path, b) or inside(b, path) for b in self.blocked):
                raise Skip(WAITING)
        if op == "move":
            if volume.volume_id(a["src"]) != a["src_volume"] or volume.volume_id(a["dst"]) != a["dst_volume"]:
                raise Skip("the drive changed since the dry run")
            self._same_as_planned(a["src"], a["fp"])
        elif op in ("delete", "delete_tree"):
            self._same_as_planned(a["path"], a["fp"])
            if a.get("keep") and not unchanged(fingerprint(a["keep"]), a.get("keep_fp")):
                # an extra copy of a duplicate goes only while the copy that stays is still there, as it was
                raise Skip("the copy that would stay changed or went since the dry run, so this one stays too")
            if a.get("keep") and not _same_bytes(a["path"], a["keep"]):
                # its size and times can stay the same while its content changes (problem 22)
                raise Skip("the copy that would stay isn't identical to this one any more, so this one stays too")
        elif op == "rmdir":
            if not os.path.isdir(lp(a["path"])):
                raise Skip("it's already gone")
        elif op == "run_tool":
            tool = TOOLS[a["tool"]]
            if tool.measure and a.get("measured") is not None:
                now = tool.measure(a)
                if now != a["measured"]:
                    raise Skip(f"{tool.label} changed since the dry run (now {_numbers(now)}). "
                               "Run the fix again to see it.")
        elif op in COUNT_ONLY:
            label, measure, _act = COUNT_ONLY[op]
            now = measure(a)
            if now != a.get("measured"):
                raise Skip(f"{label} changed since the dry run (now {_numbers(now)}). Run the fix again to see it.")

    def _same_as_planned(self, path, fp):
        now = fingerprint(path)
        if now is None:
            raise Skip("it's gone")
        if not unchanged(now, fp):
            raise Skip("changed since the dry run")

    # ---------- moves ----------
    def _move(self, a):
        src, dst = a["src"], a["dst"]
        if os.path.lexists(lp(dst)):
            raise Skip("the target already exists")
        self._makedirs(os.path.dirname(dst))
        if a["same_volume"]:
            try:
                os.rename(lp(src), lp(dst))
            except FileExistsError:
                raise Skip("the target already exists")
            self._crash_hook()
            after = fingerprint(dst)
        else:
            after = self._copy_then_delete(a)
        out = self._done(a, a.get("bytes", 0), after=after)
        self._back_to_data_folder()
        return out

    def _copy_then_delete(self, a):
        src, dst, partial = a["src"], a["dst"], a["dst"] + PARTIAL
        free = volume.free_bytes(dst)
        need = a.get("bytes", 0) * 1.02 + DEST_RESERVE
        if free is not None and free < need:
            raise Skip(f"not enough free space on the destination drive ({free / volume.GB:.1f} GB free)")
        if a["kind"] == "dir" and a["fp"].get("links"):
            raise Skip("it contains a link (a junction or symlink), which pc-tidy doesn't move to another drive")
        if os.path.lexists(lp(partial)):
            raise Skip(f"a partial copy from an earlier run is in the way: {partial}")
        busy = _first_open_elsewhere(src)
        if busy is not None:     # found now, not after copying gigabytes or with the original half-deleted
            raise Skip(f"{busy} is open in another program, so nothing was copied. Close it, then move it again")
        line, total, done, started = Line(), a.get("bytes", 0), [0], time.monotonic()
        name = os.path.basename(src)

        def copied(n):
            done[0] += n
            rate = done[0] / max(time.monotonic() - started, 0.001)
            left = f", about {duration((total - done[0]) / rate)} left" if total > done[0] and rate else ""
            line.show(f"copying {name}: {done[0] / volume.GB:.1f} of {total / volume.GB:.1f} GB, "
                      f"{rate / 1024 ** 2:.0f} MB/s{left}")
        try:
            digests = _copy_item(src, partial, copied)
            line.show(f"checking the copy of {name} ...", force=True)
            bad = _verify(partial, digests)
            line.clear()
            if bad is not None:
                raise Skip(f"the copy didn't match the original ({bad}), so nothing was moved. "
                           "The destination drive may be failing.")
            _rename_retry(partial, dst)
        except FileExistsError:
            _remove_own_copy(partial)
            raise Skip("the target already exists")
        except BaseException:
            line.clear()
            _remove_own_copy(partial)
            raise
        copied = fingerprint(dst)
        try:
            self._write_durable({"t": "copied", "n": a["n"], "fp": copied})
        except journal.JournalUnavailable:
            _remove_own_copy(dst)
            raise Skip(TOO_FULL.format(drive=self.ctx.system_drive[:2]))
        deleted, error = _delete_source(src)
        if error is not None:
            if not deleted:          # the original is whole: take our copy back out
                _remove_own_copy(dst)
                self.journal.note({"t": "settled", "n": a["n"], "as": "not-started", "why": str(error)})
                raise error
            self.result.stuck.append(a)
            self.blocked += [src, dst]
            raise Stuck(f"the copy is complete, but part of the original couldn't be deleted ({_reason(error)}). "
                       f"Close what is using it, then run: {context.command('recover')}")
        self._crash_hook()
        return copied

    def _makedirs(self, folder):
        missing = []
        while folder and not os.path.isdir(lp(folder)):
            missing.append(folder)
            parent = os.path.dirname(folder)
            if parent == folder:
                break
            folder = parent
        for path in reversed(missing):
            os.mkdir(lp(path))
            self.journal.note({"t": "mkdir", "path": path})

    def _crash_hook(self):
        """PCTIDY_TEST_CRASH_AFTER=N: the tests' stand-in for a closed window or a power cut."""
        self.moves_acted += 1
        if self.crash_after and self.moves_acted >= self.crash_after:
            os._exit(9)

    # ---------- deletes ----------
    def _delete(self, a):
        path = a["path"]
        st = os.lstat(lp(path))
        if is_link(st):
            _remove_link(path, st)
        else:
            _remove_file(path)
        nbytes = a.get("bytes", 0)
        self.journal.freed(nbytes)
        self.journal.note({"t": "done", "n": a["n"]})
        return self._outcome(a, "done", None, nbytes)

    def _delete_tree(self, a):
        removed, error = _remove_tree(a["path"])
        self.journal.freed(removed)
        if error is not None:
            self.journal.note({"t": "settled", "n": a["n"], "as": "partial", "why": str(error)})
            return self._outcome(a, "failed", f"only part of it was deleted ({_reason(error)})", removed)
        self.journal.note({"t": "done", "n": a["n"]})
        return self._outcome(a, "done", None, a.get("bytes", 0))

    def _rmdir(self, a):
        try:
            os.rmdir(lp(a["path"]))
        except OSError as e:
            if os.path.isdir(lp(a["path"])) and os.listdir(lp(a["path"])):
                self.journal.note({"t": "failed", "n": a["n"], "reason": "not empty"})
                return self._outcome(a, "kept", "it isn't empty")
            raise e
        return self._done(a, 0)

    # ---------- tools ----------
    def _run_tool(self, a):
        tool = TOOLS[a["tool"]]
        cmd = tool.command(a)
        found = shutil.which(cmd[0])      # conda is conda.bat, which Windows only starts by its full path
        if found:
            cmd = [found] + cmd[1:]
        try:
            if tool.interactive:
                proc = subprocess.run(cmd)
            else:
                proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, timeout=tool.timeout)
        except subprocess.TimeoutExpired:
            raise Skip(f"{tool.label} didn't finish in {_duration(tool.timeout)}, so pc-tidy stopped it")
        except FileNotFoundError:
            raise Skip(f"{tool.label} isn't available in this window")
        if proc.returncode:
            raise Skip(f"{tool.label} ended with exit code {proc.returncode}")
        self.journal.note({"t": "done", "n": a["n"]})
        return self._outcome(a, "done")

    def _empty_recycle_bin(self, a):
        _label, _measure, act = COUNT_ONLY["empty_recycle_bin"]
        act(a)
        nbytes = (a.get("measured") or {}).get("bytes", 0)
        self.journal.freed(nbytes)
        self.journal.note({"t": "done", "n": a["n"]})
        return self._outcome(a, "done", None, nbytes)


# ---------------- file operations (no overwrite, no links followed) ----------------

def _write(f, chunk):
    """One write to a copy. A function of its own so a test can corrupt a copy on purpose."""
    f.write(chunk)


def _copy_file(src, dst, copied=None):
    """Copy one file to a new file (never replacing one) and return its BLAKE2b digest. copied(n)
    is told about every chunk, for the progress line."""
    h = hashlib.blake2b()
    with open(lp(src), "rb") as fin, open(lp(dst), "xb") as fout:
        while True:
            chunk = fin.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
            _write(fout, chunk)
            if copied:
                copied(len(chunk))
        fout.flush()
        os.fsync(fout.fileno())
    st = os.stat(lp(src))
    os.utime(lp(dst), ns=(st.st_atime_ns, st.st_mtime_ns))
    if getattr(st, "st_file_attributes", 0) & _win.FILE_ATTRIBUTE_READONLY:
        os.chmod(lp(dst), stat.S_IREAD)
    return h.hexdigest()


def _copy_item(src, dst, copied=None):
    """Copy a file or a folder tree to dst. Returns {relative path: digest}."""
    st = os.lstat(lp(src))
    if not is_dir_stat(st):
        return {"": _copy_file(src, dst, copied)}
    digests, dirs = {}, []
    os.mkdir(lp(dst))
    stack = [""]
    while stack:
        rel = stack.pop()
        here = os.path.join(src, rel) if rel else src
        dirs.append(rel)
        for e in sorted(os.scandir(lp(here)), key=lambda e: e.name):
            sub = os.path.join(rel, e.name) if rel else e.name
            est = e.stat(follow_symlinks=False)
            if is_link(est):
                raise Skip(f"it contains a link ({sub}), which pc-tidy doesn't move to another drive")
            if is_dir_stat(est):
                os.mkdir(lp(os.path.join(dst, sub)))
                stack.append(sub)
            else:
                digests[sub] = _copy_file(os.path.join(src, sub), os.path.join(dst, sub), copied)
    for rel in reversed(dirs):       # folder times last, after their contents were written
        here = os.path.join(src, rel) if rel else src
        st = os.stat(lp(here))
        os.utime(lp(os.path.join(dst, rel) if rel else dst), ns=(st.st_atime_ns, st.st_mtime_ns))
    return digests


def _digest(path):
    h = hashlib.blake2b()
    with open(lp(path), "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                return h.hexdigest()
            h.update(chunk)


def _first_open_elsewhere(src):
    """The first file of a file or folder tree that another program has open (its name, relative to
    the folder), or None. Links are not followed."""
    if not is_dir_stat(os.lstat(lp(src))):
        return os.path.basename(src) if _win.open_elsewhere(src) else None
    stack = [""]
    while stack:
        rel = stack.pop()
        for e in os.scandir(lp(os.path.join(src, rel) if rel else src)):
            sub = os.path.join(rel, e.name) if rel else e.name
            est = e.stat(follow_symlinks=False)
            if is_link(est):
                continue
            if is_dir_stat(est):
                stack.append(sub)
            elif _win.open_elsewhere(os.path.join(src, sub)):
                return sub
    return None


def _same_bytes(a, b):
    """True if two files hold exactly the same bytes, read now without changing when either was last
    opened. False if either can't be read that way: then the extra copy stays."""
    try:
        with _win.open_untouched(a) as fa, _win.open_untouched(b) as fb:
            while True:
                x, y = fa.read(CHUNK), fb.read(CHUNK)
                if x != y:
                    return False
                if not x:
                    return True
    except OSError:
        return False


def _verify(copy, digests):
    """Re-read the copy. The relative path of the first file that doesn't match, or None."""
    for rel, digest in digests.items():
        if _digest(os.path.join(copy, rel) if rel else copy) != digest:
            return rel or os.path.basename(copy)
    return None


def _rename_retry(src, dst, tries=5):
    """os.rename (never replaces), retried briefly: a virus scanner may still hold a new file."""
    for i in range(tries):
        try:
            os.rename(lp(src), lp(dst))
            return
        except FileExistsError:
            raise
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.2 * (i + 1))


def _remove_file(path):
    """Delete one file, clearing the read-only flag if that is what stops it."""
    try:
        os.remove(lp(path))
    except PermissionError as e:
        if _win.in_use(e):
            raise
        st = os.lstat(lp(path))
        if not getattr(st, "st_file_attributes", 0) & _win.FILE_ATTRIBUTE_READONLY:
            raise
        os.chmod(lp(path), stat.S_IWRITE)
        os.remove(lp(path))


def _remove_link(path, st):
    """Remove a junction or symlink itself. Its target is never touched."""
    if is_dir_stat(st):
        os.rmdir(lp(path))
    else:
        os.unlink(lp(path))


def _rmdir_force(path):
    try:
        os.rmdir(lp(path))
    except PermissionError:
        os.chmod(lp(path), stat.S_IWRITE)
        os.rmdir(lp(path))


def _remove_tree(path):
    """Delete a folder tree without ever following a link (rule 4). Returns (bytes removed, first
    error or None). Stops at the first error."""
    removed = 0
    try:
        st = os.lstat(lp(path))
    except FileNotFoundError:
        return 0, None
    if is_link(st):
        _remove_link(path, st)
        return 0, None
    if not is_dir_stat(st):
        size = st.st_size
        _remove_file(path)
        return size, None
    order, stack = [], [path]
    try:
        while stack:      # depth first, without recursion: trees can be deeper than Python's stack
            folder = stack.pop()
            order.append(folder)
            for e in os.scandir(lp(folder)):
                est = e.stat(follow_symlinks=False)
                full = os.path.join(folder, e.name)
                if is_link(est):
                    _remove_link(full, est)
                elif is_dir_stat(est):
                    stack.append(full)
                else:
                    _remove_file(full)
                    removed += est.st_size
        for folder in reversed(order):
            _rmdir_force(folder)
    except OSError as e:
        return removed, e
    return removed, None


def _delete_source(src):
    """Delete the original after its copy is safe. Returns (whether anything was deleted, error)."""
    st = os.lstat(lp(src))
    if not is_dir_stat(st):
        try:
            _remove_file(src)
        except OSError as e:
            return False, e
        return True, None
    removed_before = _count_files(src)
    _bytes, error = _remove_tree(src)
    if error is None:
        return True, None
    return _count_files(src) != removed_before, error


def _count_files(path):
    return tree_stats(path)["files"]


def _remove_own_copy(path):
    """Remove a copy pc-tidy itself just made (a .pctidy-partial, or a copy whose original is whole)."""
    if os.path.lexists(lp(path)):
        _remove_tree(path)


# ---------------- messages ----------------

def _reason(e):
    if isinstance(e, OSError):
        if _win.in_use(e):
            return "in use by another program"
        if _win.disk_full(e):
            return "the disk is full"
        return e.strerror or str(e)
    return str(e)


def _numbers(measured):
    if not measured:
        return "unknown"
    parts = []
    if "items" in measured:
        parts.append(f"{measured['items']} items")
    if "bytes" in measured:
        parts.append(f"{measured['bytes'] / volume.GB:.1f} GB")
    return ", ".join(parts) or str(measured)


def _duration(seconds):
    if seconds >= 60 and seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{seconds:g} seconds"


def free_space_line(result, drive):
    """'Free space on C: went from 10.2 GB to 12.5 GB.' plus a warning if it gained much less than moved."""
    before, after = result.free_before, result.free_after
    if before is None or after is None:
        return ""
    gb = volume.GB
    line = f"Free space on {drive} went from {before / gb:.1f} GB to {after / gb:.1f} GB."
    moved = result.bytes_moved
    if moved and after - before < moved * 0.5:
        line += (f" That is less than the {moved / gb:.1f} GB moved: other programs may be writing to {drive}, "
                 "or some files were hard links that are still there.")
    return line

