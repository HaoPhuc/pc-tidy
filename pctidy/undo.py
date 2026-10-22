"""Undo: put the items of the last run back, entry by entry, newest first.

- Checks that what sits at the recorded place is still what was moved there: the file ID recorded
  after the move (NTFS keeps it across renames, and a copy's new ID is recorded on the other volume),
  plus size and modified time. Something else in that place is left alone and reported. If the item
  was edited since the move, it asks before starting whether to move it back.
- If the original place is now taken, it restores beside it as "name (restored).ext" and says so.
- Keeps entries it couldn't put back, so a later undo retries them.
- Reads the journals in the data folder and on destination volumes, and the old formats:
  tidy_folder.py's <dest>\\_logs\\run_*.json and move_off_c.py's ~\\.pc-tidy\\move_logs\\moves_*.json.
  An old log is renamed .undone once every entry in it is back.
"""

import glob
import json
import os
import re
import time

from . import executor, journal
from .paths import lp, norm
from .plan import Plan, fingerprint, same_content, same_item


class Entry:
    def __init__(self, original, now_at, after, ref):
        self.original, self.now_at, self.after, self.ref = original, now_at, after, ref


class Source:
    """One run to undo: a journal, or an old-format log."""

    def __init__(self, name, when, entries, run=None, legacy=None, folders=()):
        self.name, self.when, self.entries = name, when, entries
        self.run, self.legacy = run, legacy
        self.folders = list(folders)     # folders to remove once the undo has emptied them, deepest first


class UndoResult:
    def __init__(self, source=None):
        self.source = source
        self.restored = []      # (Entry, where it went)
        self.kept = []          # (Entry, why)
        self.result = None

    @property
    def total(self):
        return len(self.source.entries) if self.source else 0


def find_last(ctx, command, dest=None, legacy=()):
    """The newest run of `command` (for tidy: into `dest`) that still has something to put back.
    legacy: [(folder, glob pattern, folder that holds the sorted folders or None)]."""
    candidates = []
    for run in journal.load_runs(ctx):
        if run.command != command:
            continue
        if dest is not None and norm(run.header.get("info", {}).get("dest", "")) != norm(dest):
            continue
        moves = run.done_moves()
        if moves:
            entries = [Entry(a.intent["src"], a.intent["dst"], a.after, {"run": run.id, "n": a.n}) for a in moves]
            folders = sorted(set(run.mkdirs), key=lambda p: -p.count("\\"))
            candidates.append(Source(os.path.basename(run.active_file), run.created, entries, run=run,
                                     folders=folders))
    undone = journal.legacy_undone(ctx)
    for folder, pattern, base in legacy:
        for path in glob.glob(os.path.join(glob.escape(folder), pattern)):
            source = _legacy_source(path, base, undone.get(norm(path), set()))
            if source:
                candidates.append(source)
    return max(candidates, key=lambda s: s.when) if candidates else None


def _legacy_source(path, base, done):
    try:
        with open(lp(path), encoding="utf-8") as f:
            items = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(items, list):
        return None
    entries = [Entry(e["from"], e["to"], None, {"legacy": path, "index": i})
               for i, e in enumerate(items) if isinstance(e, dict) and "from" in e and "to" in e and i not in done]
    if not entries:
        return None
    entries.reverse()
    folders = []
    if base:        # today's tidy_folder.py removed the category folders an undo emptied, up to the dest
        for e in entries:
            parent = os.path.dirname(e.now_at)
            while norm(parent) != norm(base) and norm(parent).startswith(norm(base) + "\\"):
                folders.append(parent)
                parent = os.path.dirname(parent)
        folders = sorted(set(folders), key=lambda p: -p.count("\\"))
    m = re.search(r"(\d{8})_(\d{6})", os.path.basename(path))
    try:
        when = time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")) if m else os.path.getmtime(path)
    except (ValueError, OSError):
        when = 0
    return Source(os.path.basename(path), when, entries, legacy=path, folders=folders)


def restored_name(path, taken=()):
    """'name (restored).ext', or 'name (restored 2).ext' if that is taken too."""
    stem, ext = os.path.splitext(path)
    i = 1
    while True:
        candidate = f"{stem} (restored){ext}" if i == 1 else f"{stem} (restored {i}){ext}"
        if not os.path.lexists(lp(candidate)) and norm(candidate) not in taken:
            return candidate
        i += 1


def undo_last(ctx, command, dest=None, legacy=(), say=print, ask=None, on_action=None):
    """Undo the last run of `command`. `ask(prompt)` (None when nobody can answer) is used, before
    anything changes, for items edited since they were moved. Raises lock.Busy."""
    source = find_last(ctx, command, dest, legacy)
    out = UndoResult(source)
    if source is None:
        return out
    planned, edited, taken = [], [], set()
    for e in source.entries:
        now = fingerprint(e.now_at)
        if now is None:
            out.kept.append((e, f"it isn't at {e.now_at} any more"))
            continue
        if e.after is not None:
            if not same_item(now, e.after):
                out.kept.append((e, f"something else is at {e.now_at} now, so it was left alone"))
                continue
            if not same_content(now, e.after):
                edited.append(e)
        planned.append(e)
    for e in edited:        # every question before anything changes, and before the lock is taken
        answer = ask(f"{e.now_at} was changed after it was moved. Type yes to move it back anyway: ") if ask else None
        if (answer or "").strip().lower() != "yes":
            planned.remove(e)
            out.kept.append((e, "it was changed after it was moved, and you chose to leave it"))
    plan = Plan("undo", ctx, info={"undoes": source.name, "command": command})
    targets = {}
    for e in planned:
        target = e.original
        if os.path.lexists(lp(target)) or norm(target) in taken:
            target = restored_name(target, taken)
        taken.add(norm(target))
        try:
            action = plan.add_move(e.now_at, target, undoes=e.ref)
        except FileNotFoundError:
            out.kept.append((e, f"it isn't at {e.now_at} any more"))
            continue
        targets[action["n"]] = e
    for folder in source.folders:
        plan.add_rmdir(folder, quiet=True)
    result = out.result = executor.execute(plan, ctx, say=say, on_action=on_action)
    for o in result.outcomes:
        e = targets.get(o.action["n"])
        if e is None:
            continue
        if o.status == "done":
            out.restored.append((e, o.action["dst"]))
            if source.run is not None:
                journal.append(source.run, {"t": "undone", "n": e.ref["n"], "as": o.action["dst"]})
        else:
            out.kept.append((e, o.reason))
    if source.legacy and len(out.restored) == len(source.entries) and not out.kept:
        try:
            os.rename(source.legacy, os.path.splitext(source.legacy)[0] + ".undone")
        except OSError:
            pass
    return out


def report_kept(out, say=print):
    """The lines every --undo prints about what it couldn't put back."""
    for e, target in out.restored:
        if norm(target) != norm(e.original):
            say(f"  {e.original} was taken, so it was put back as {target}")
    if out.kept:
        say(f"{len(out.kept)} item(s) couldn't be put back. They stay in the undo list, so running --undo "
            "again retries them:")
        for e, why in out.kept:
            say(f"  {e.now_at}: {why}")

