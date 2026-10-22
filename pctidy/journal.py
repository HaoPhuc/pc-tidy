"""The journal: one append-only JSONL file per run, in runs\\ of the data folder.

No journal on disk, no undoable action: a move (or the removal of an empty folder, which undo puts
back) runs only after its "intent" line has been written and flushed to disk. Deletes can't be
undone, so their lines are only a record: they are kept in memory when the disk is full and written
as soon as a write succeeds (pc-tidy retries after every 50 MB freed, and at the end at the latest).

Lines, each with "seq" and "time":
    run        the header: command, plan, computer, sid, elevated, settings_hash, info
    intent     an action is about to happen (the plan's action dict)
    copied     a copy on another volume is complete and in place, with its fingerprint
    done       it happened; for moves "after" is the fingerprint at the destination
    failed     it didn't happen, with the reason
    skipped    it wasn't tried (changed since the dry run, ...)
    settled    recovery decided what an interrupted action did: done, not-started, partial, failed
    undone     an undo put the item back ("as": where)
    mkdir      pc-tidy created this folder (undo removes it again once it is empty)
    continued  the rest of this run is in another file (a full C: journal moving to the data folder)
    end        the summary

When C: is full, move_off_c.py keeps the journal on the destination volume instead, in
<drive>:\\From-C\\_pc-tidy\\, and continues it in the data folder once a move has freed room.
"""

import json
import os
import time

from . import context, volume
from .paths import lp, norm

DELETE_RETRY_BYTES = 50 * 1024 ** 2


class JournalUnavailable(OSError):
    """The journal couldn't be written to disk, so no undoable action may run."""


def runs_dir(ctx):
    return ctx.folder("runs")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Journal:
    def __init__(self, ctx, header, folder=None):
        self.ctx = ctx
        self.run_id = header["run"]
        self.folder = folder or runs_dir(ctx)
        self.lines = []          # every line of this run, so it can be continued in another file
        self.pending = []        # lines not on disk yet
        self.seq = 0
        self.ok = None           # None until the first write
        self._file = None
        self._dirty = False      # a write failed halfway, so start the next one on a new line
        self._freed = 0
        self._add(dict(header, t="run"))

    @property
    def path(self):
        return os.path.join(self.folder, self.run_id + ".jsonl")

    def _add(self, record):
        rec = dict(record, seq=self.seq, time=_now())
        self.seq += 1
        self.lines.append(rec)
        self.pending.append(rec)
        return rec

    def _write(self, durable):
        if not self.pending:
            return True
        data = b"".join(json.dumps(r, ensure_ascii=False).encode("utf-8") + b"\n" for r in self.pending)
        try:
            if self._file is None:
                os.makedirs(self.folder, exist_ok=True)
                self._file = open(lp(self.path), "ab", buffering=0)
            if self._dirty:
                data = b"\n" + data
            view = memoryview(data)
            while view:
                view = view[self._file.write(view):]
            if durable:
                os.fsync(self._file.fileno())
        except OSError:
            self._dirty = self._file is not None
            self.ok = False
            return False
        self._dirty, self.ok = False, True
        self.pending = []
        return True

    def start(self):
        """Write the header. False if the disk is full: then only deletes may run."""
        return self._write(durable=True)

    def write(self, record):
        """An undoable step: on disk before this returns, or JournalUnavailable."""
        rec = self._add(record)
        if not self._write(durable=True):
            # its seq number is never reused: part of the line may have reached the file
            self.lines.remove(rec)
            self.pending.remove(rec)
            raise JournalUnavailable(f"can't write {self.path}")
        return rec

    def note(self, record):
        """A record of something that can't be undone anyway: written now if the disk allows it,
        otherwise kept in memory until a later write succeeds."""
        rec = self._add(record)
        if self.ok is not False:
            self._write(durable=False)
        return rec

    def freed(self, nbytes):
        """Bytes freed by a delete. While writes fail, retry after every 50 MB."""
        self._freed += nbytes
        if self.ok is False and self._freed >= DELETE_RETRY_BYTES:
            self._freed = 0
            self._write(durable=False)

    def flush(self):
        return self._write(durable=True)

    def move_to(self, folder):
        """Continue this run's journal in another folder: on the destination volume when the data
        folder is full, and back in the data folder once a move has made room. The new file gets
        every line so far, and the old one ends with a line pointing at it. False if the new file
        can't be written; the journal then stays where it was."""
        old_file, old_folder, old_ok = self._file, self.folder, self.ok
        self._file, self.folder, self.pending, self._dirty = None, folder, list(self.lines), False
        if not self._write(durable=True):
            self.close_file()
            self._file, self.folder, self.pending, self.ok = old_file, old_folder, [], old_ok
            return False
        if old_file is not None:
            rec = {"t": "continued", "path": self.path, "seq": self.seq, "time": _now()}
            self.seq += 1
            try:
                old_file.write(b"\n" + json.dumps(rec, ensure_ascii=False).encode("utf-8") + b"\n")
                os.fsync(old_file.fileno())
            except OSError:
                pass        # recovery merges both files by run id anyway
            try:
                old_file.close()
            except OSError:
                pass
        return True

    def close(self, summary=None):
        self.note({"t": "end", "summary": summary or {}})
        self.flush()
        self.close_file()

    def close_file(self):
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None


# ---------------- reading journals ----------------

def read_lines(path):
    """The records in a journal file. A line cut short by a crash or a full disk is ignored."""
    out = []
    try:
        with open(lp(path), "rb") as f:
            data = f.read()
    except OSError:
        return out
    for raw in data.splitlines():
        try:
            rec = json.loads(raw.decode("utf-8"))
        except ValueError:
            continue
        if isinstance(rec, dict) and "t" in rec:
            out.append(rec)
    return out


class ActionState:
    def __init__(self, intent):
        self.intent = intent
        self.n = intent.get("n")
        self.status = "pending"
        self.after = None
        self.copied = None
        self.reason = None
        self.undone = None

    @property
    def op(self):
        return self.intent.get("op")

    def paths(self):
        a = self.intent
        return [p for p in (a.get("src"), a.get("dst"), a.get("path")) if p]


class Run:
    """Everything one run's journal (or journals) recorded."""

    def __init__(self, run_id):
        self.id = run_id
        self.header = {}
        self.files = []
        self.records = {}
        self.actions = {}
        self.mkdirs = []
        self.skipped = []
        self.continued_in = None
        self._counts = {}

    @property
    def command(self):
        return self.header.get("command")

    @property
    def created(self):
        return self.header.get("created") or 0

    @property
    def when(self):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.created)) if self.created else self.id

    @property
    def active_file(self):
        """The file new lines go to: where the run continued, else the file with the most lines."""
        if self.continued_in and os.path.exists(lp(self.continued_in)):
            return self.continued_in
        return max(self.files, key=lambda f: self._counts[f])

    def add_file(self, path):
        self.files.append(path)
        records = read_lines(path)
        self._counts[path] = len(records)
        for rec in records:
            self.records.setdefault(rec.get("seq", len(self.records) + 10 ** 9), rec)

    def build(self):
        for seq in sorted(self.records, key=lambda s: (isinstance(s, int), s)):
            rec = self.records[seq]
            t, n = rec.get("t"), rec.get("n")
            state = self.actions.get(n)
            if t == "run":
                self.header = rec
            elif t == "intent":
                self.actions[n] = ActionState(rec)
            elif t == "copied" and state:
                state.copied = rec.get("fp")
            elif t == "done" and state:
                state.status, state.after = "done", rec.get("after")
            elif t == "failed" and state:
                state.status, state.reason = "failed", rec.get("reason")
            elif t == "settled" and state:
                state.status, state.reason = rec.get("as"), rec.get("why")
                if rec.get("after") is not None:
                    state.after = rec.get("after")
            elif t == "undone" and state:
                state.undone = rec.get("as") or True
            elif t == "mkdir":
                self.mkdirs.append(rec.get("path"))
            elif t == "skipped":
                self.skipped.append(rec)
            elif t == "continued":
                self.continued_in = rec.get("path")
        return self

    def unfinished(self):
        return [a for a in self.actions.values() if a.status == "pending"]

    def done_moves(self):
        """Moves that happened and haven't been undone, newest first."""
        moves = [a for a in self.actions.values() if a.op == "move" and a.status == "done" and not a.undone]
        return sorted(moves, key=lambda a: -a.n)


def journal_files(ctx):
    """Every journal file pc-tidy can find: the data folder's runs\\ and the From-C\\_pc-tidy folders."""
    out = []
    for folder in [runs_dir(ctx)] + volume.journal_folders(ctx):
        try:
            names = os.listdir(lp(folder))
        except OSError:
            continue
        out += [os.path.join(folder, n) for n in sorted(names) if n.endswith(".jsonl")]
    return out


def load_runs(ctx=None):
    """Every run, oldest first, with undo runs applied to the runs they undid."""
    ctx = ctx or context.current()
    runs = {}
    for path in journal_files(ctx):
        run_id = os.path.basename(path)[:-len(".jsonl")]
        runs.setdefault(run_id, Run(run_id)).add_file(path)
    out = [r.build() for r in sorted(runs.values(), key=lambda r: r.id)]
    by_id = {r.id: r for r in out}
    for r in out:
        for a in r.actions.values():
            ref = a.intent.get("undoes") or {}
            target = by_id.get(ref.get("run"))
            if a.status == "done" and target and ref.get("n") in target.actions:
                target.actions[ref["n"]].undone = target.actions[ref["n"]].undone or a.intent.get("dst")
    return out


def legacy_undone(ctx=None):
    """{legacy log path (normalised): set of entry indexes} that undo runs have put back."""
    out = {}
    for r in load_runs(ctx):
        for a in r.actions.values():
            ref = a.intent.get("undoes") or {}
            if a.status == "done" and ref.get("legacy"):
                out.setdefault(norm(ref["legacy"]), set()).add(ref.get("index"))
    return out


def append(run, record):
    """Add a line to a run's journal (recovery and undo use this). False if the disk is full."""
    path = run.active_file
    seq = max([s for s in run.records if isinstance(s, int)] + [0]) + 1
    rec = dict(record, seq=seq, time=_now())
    try:
        with open(lp(path), "ab", buffering=0) as f:
            f.write(b"\n" + json.dumps(rec, ensure_ascii=False).encode("utf-8") + b"\n")
            os.fsync(f.fileno())
    except OSError:
        return False
    run.records[seq] = rec
    return True


# ---------------- attention notices ----------------
# What an unattended run couldn't do: an interrupted move waiting for a decision, a busy lock,
# skipped items. `python pc_tidy.py status` shows them until they are dismissed.

def attention(ctx, kind, message, **details):
    path = ctx.folder("attention.jsonl")
    rec = dict(details, t=kind, time=_now(), message=message)
    try:
        os.makedirs(ctx.data_dir, exist_ok=True)
        with open(lp(path), "ab") as f:
            f.write(json.dumps(rec, ensure_ascii=False).encode("utf-8") + b"\n")
        return True
    except OSError:
        return False


def notices(ctx):
    return read_lines(ctx.folder("attention.jsonl"))


def dismiss_notices(ctx):
    """Keep the old notices in attention-dismissed.jsonl, and start an empty list."""
    path = ctx.folder("attention.jsonl")
    if not os.path.exists(lp(path)):
        return 0
    count = len(notices(ctx))
    try:
        with open(lp(path), "rb") as src, open(lp(ctx.folder("attention-dismissed.jsonl")), "ab") as dst:
            dst.write(src.read())
        os.remove(lp(path))
    except OSError:
        return 0
    return count
