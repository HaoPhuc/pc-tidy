"""Recovery: settle what an interrupted run left half-done, from its journal and what is on disk.

At start, every command that changes files looks for journal "intent" lines without a "done".

Settled automatically, with no question:
  - the action provably didn't happen (source unchanged, nothing at the destination): not-started
  - it provably finished (nothing at the source, the destination matches): done
  - a .pctidy-partial copy whose source is intact: removed (pc-tidy's own file), then not-started
  - a delete: done or not-started, depending on whether the item still exists
  - a partly deleted folder: partial, and reported

Needs your decision: a move to another volume whose copy is complete and whose source is partly
deleted. Only interactive commands ask (move_off_c.py, pc_tidy.py recover). Until you decide, the
executor refuses anything that touches either path. It is never settled quietly.
"""

import os
from collections import namedtuple

from . import journal, safety
from .executor import PARTIAL, _copy_file, _digest, _remove_own_copy, _remove_tree, _rename_retry
from .lock import Lock
from .paths import lp
from .plan import fingerprint, same_content, same_item, unchanged

Verdict = namedtuple("Verdict", "status why after cleanup")


class Item:
    def __init__(self, run, state, verdict):
        self.run, self.state, self.verdict = run, state, verdict

    @property
    def src(self):
        return self.state.intent.get("src") or self.state.intent.get("path")

    @property
    def dst(self):
        return self.state.intent.get("dst")


class Report:
    def __init__(self):
        self.settled = []
        self.undecided = []
        self.unrecorded = 0

    @property
    def runs(self):
        return list({id(i.run): i.run for i in self.settled + self.undecided}.values())


def evaluate(state):
    """What an interrupted action did, judged from the disk. Read-only."""
    a = state.intent
    op = a.get("op")
    if op == "move":
        return _evaluate_move(state)
    if op == "delete":
        if fingerprint(a["path"]) is None:
            return Verdict("done", "it had been deleted", None, None)
        return Verdict("not-started", "it hadn't been deleted", None, None)
    if op == "delete_tree":
        now = fingerprint(a["path"])
        if now is None:
            return Verdict("done", "it had been deleted", None, None)
        if same_content(now, a["fp"]):
            return Verdict("not-started", "it hadn't been touched", None, None)
        return Verdict("partial", "only part of it had been deleted", None, None)
    if op == "rmdir":
        if os.path.isdir(lp(a["path"])):
            return Verdict("not-started", "the folder is still there", None, None)
        return Verdict("done", "the folder had been removed", None, None)
    return Verdict("failed", "it was interrupted; run the fix again if it is still needed", None, None)


def _evaluate_move(state):
    a = state.intent
    src, dst, fp = a["src"], a["dst"], a["fp"]
    now_src, now_dst = fingerprint(src), fingerprint(dst)
    if a.get("same_volume"):      # os.rename: either it happened or it didn't
        if now_src is None and now_dst is not None and same_item(now_dst, fp):
            return Verdict("done", "the move had finished", now_dst, None)
        if now_src is not None:
            return Verdict("not-started", "the move hadn't happened", None, None)
        return Verdict("failed", "the item isn't at either place any more", None, None)

    copied = state.copied
    if copied is None:            # the original is never touched before its copy is complete
        partial = dst + PARTIAL
        if now_src is None:
            return Verdict("failed", "the original is gone, but its copy was never completed", None, None)
        if os.path.lexists(lp(partial)):
            if unchanged(now_src, fp):
                return Verdict("not-started", "the copy hadn't finished; it was removed", None, partial)
            return Verdict("not-started", f"the copy hadn't finished; the original changed since, so the "
                                          f"unfinished copy was left at {partial}", None, None)
        return Verdict("not-started", "the move hadn't started", None, None)

    dst_ours = now_dst is not None and same_item(now_dst, copied)
    if now_src is None:
        if dst_ours:
            return Verdict("done", "the move had finished", now_dst, None)
        return Verdict("failed", f"the original is gone and the copy at {dst} isn't pc-tidy's any more", None, None)
    whole = unchanged(now_src, fp) or (fp.get("kind") == "file" and now_src.get("kind") == "file")
    if whole:
        if dst_ours and unchanged(now_dst, copied):
            return Verdict("not-started", "the original is whole, so pc-tidy's copy was removed", None, dst)
        return Verdict("not-started", "the original is whole", None, None)
    if dst_ours:
        return Verdict("undecided", "the copy is complete, but part of the original was already deleted", None, None)
    return Verdict("undecided", "part of the original was already deleted, and the copy has changed since", None, None)


def scan(ctx, runs=None):
    """[Item] for every interrupted action, with its verdict. Read-only."""
    runs = journal.load_runs(ctx) if runs is None else runs
    return [Item(r, s, evaluate(s)) for r in runs for s in r.unfinished()]


def undecided(ctx):
    return [i for i in scan(ctx) if i.verdict.status == "undecided"]


def settle(ctx, say=print):
    """Settle everything provable. The caller holds the lock. Returns a Report."""
    report = Report()
    for item in scan(ctx):
        v = item.verdict
        if v.status == "undecided":
            report.undecided.append(item)
            continue
        if v.cleanup:
            problem = safety.why_refused(v.cleanup, "delete", ctx)
            if problem:       # the journal is a file on disk: what it names still goes through the check
                say(f"Not removing {v.cleanup}: {problem}. That interrupted move stays unsettled.")
                continue
            _remove_own_copy(v.cleanup)
        rec = {"t": "settled", "n": item.state.n, "as": v.status, "why": v.why}
        if v.after is not None:
            rec["after"] = v.after
        if not journal.append(item.run, rec):
            report.unrecorded += 1
        report.settled.append(item)
    _tell(report, say)
    return report


def _tell(report, say):
    for run in report.runs:
        items = [i for i in report.settled + report.undecided if i.run is run]
        say(f"An earlier pc-tidy run ({run.command}, {run.when}) was interrupted. What it left:")
        for i in items:
            what = f"{i.src}  ->  {i.dst}" if i.dst else i.src
            say(f"  {what}: {i.verdict.why}")
    if report.unrecorded:
        say(f"  ({report.unrecorded} of these couldn't be written to the journal yet, because the disk is "
            "full. They will be checked again next time.)")


def describe(item):
    return [f"An earlier move was interrupted ({item.run.command}, {item.run.when}):",
            f"  from: {item.src}",
            f"  to:   {item.dst}",
            f"{item.verdict.why[0].upper()}{item.verdict.why[1:]}.",
            "  1) finish the move: delete what is left at the original place",
            "  2) put it back: copy the missing files back from the copy, then remove the copy"]


def ask_choice(item, ask, say=print):
    """'finish', 'put-back' or None (decide later). `ask` returns None at end of input."""
    for line in describe(item):
        say(line)
    answer = ask("Type 1 or 2, or press Enter to decide later: ")
    return {"1": "finish", "2": "put-back"}.get((answer or "").strip())


def decide(ctx, item, choice, say=print):
    """Carry out a decision about an interrupted move. The caller holds the lock. True if settled."""
    a = item.state.intent
    src, dst = a["src"], a["dst"]
    problem = safety.why_refused(src if choice == "finish" else dst, "delete", ctx)
    if problem:
        say(f"pc-tidy won't do that: {problem}.")
        return False
    if choice == "finish":
        _removed, error = _remove_tree(src)
        if error is not None:
            say(f"Couldn't delete the rest of {src} ({error}). Close what is using it and try again.")
            return False
        journal.append(item.run, {"t": "settled", "n": item.state.n, "as": "done",
                                  "why": "you chose to finish the move", "after": fingerprint(dst)})
        say(f"Finished the move: {src} is now only at {dst}.")
        return True
    if choice == "put-back":
        try:
            _copy_back(dst, src)
        except OSError as e:
            say(f"Couldn't put {src} back ({e}). Nothing was removed from {dst}.")
            return False
        now = fingerprint(src)
        if not same_content(now, a["fp"]):
            say(f"{src} still doesn't match what was there before the move, so {dst} was kept.")
            return False
        _removed, error = _remove_tree(dst)
        if error is not None:
            say(f"{src} is back. pc-tidy's copy at {dst} couldn't be removed ({error}); delete it yourself.")
        journal.append(item.run, {"t": "settled", "n": item.state.n, "as": "not-started",
                                  "why": "you chose to put it back"})
        say(f"Put back {src}.")
        return True
    return False


def _copy_back(copy, original):
    """Copy every file the original lost back from pc-tidy's complete copy, verified, never replacing."""
    if not os.path.isdir(lp(copy)):
        if not os.path.lexists(lp(original)):
            _copy_verified(copy, original)
        return
    for dirpath, _dirs, files in os.walk(lp(copy)):
        rel = os.path.relpath(dirpath, lp(copy))
        target_dir = original if rel == "." else os.path.join(original, rel)
        os.makedirs(lp(target_dir), exist_ok=True)
        for name in files:
            if name.endswith(PARTIAL):
                continue
            target = os.path.join(target_dir, name)
            leftover = target + PARTIAL
            if os.path.lexists(lp(leftover)):
                _remove_own_copy(leftover)
            if not os.path.lexists(lp(target)):
                _copy_verified(os.path.join(copy, rel, name) if rel != "." else os.path.join(copy, name), target)


def _copy_verified(src, dst):
    partial = dst + PARTIAL
    digest = _copy_file(src, partial)
    if _digest(partial) != digest:
        _remove_own_copy(partial)
        raise OSError(f"the copy of {src} didn't match")
    _rename_retry(partial, dst)


def at_start(ctx, command, say=print, ask=None):
    """What every command that changes files does first: take the lock, settle what can be proven,
    release it, and (interactive commands only, so `ask` is given) ask about the rest.
    Raises lock.Busy. Returns the Report, whose .undecided lists what is still waiting."""
    with Lock(ctx, command).acquire(say=say):
        report = settle(ctx, say)
    if ask:
        for item in list(report.undecided):
            choice = ask_choice(item, ask, say)
            if not choice:
                continue
            with Lock(ctx, command).acquire(say=say):
                if decide(ctx, item, choice, say):
                    report.undecided.remove(item)
    return report

