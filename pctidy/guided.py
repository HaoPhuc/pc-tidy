r"""The guided flow: what `python pc_tidy.py` (or a double-click on pc-tidy.cmd) does.

    1. checks: settings, notices from unattended runs, interrupted runs, admin rights, free space
    2. a scan of C: (it only reads)
    3. the report, saved in %LOCALAPPDATA%\pc-tidy\reports, and the ranked fixes for C:
    4. you pick fixes by number; nothing is picked for you
    5. the dry run: the exact list, built now from the disk as it is, not from the scan
    6. you type yes, and each high-risk fix asks once more
    7. the run: the executor does only what is on the list, re-checking every item
    8. the summary: what was freed, what was skipped and why, C: before and after, how to undo
Then a menu: free more space, sort Downloads, undo, the reports folder, settings, help.

The rules hold here as everywhere: nothing is deleted without the dry run and a yes typed after it
in this window (there is no --yes); moves go through move_off_c.py's plan, so its --undo puts them
back; and the AI never decides what is deleted, because the fixes come from pctidy/fixes.py, which
never asks it. No lock is held while this waits for you, so the weekly sort can always run.

The Windows folders (Windows Temp, crash dumps, hibernation) need an administrator window: in a
normal one they are listed, and say so.
"""

import collections
import os
import shutil
import subprocess
import sys
import time

from . import _win, context, executor, journal, leftovers, lock, recovery, safety, settings, ui, volume
from .fixes import gb
from .paths import expand, inside
from .plan import Plan, fingerprint, old_enough
from .progress import Line

COMMAND = "pc-tidy"
FULL_C = 256 * 1024 ** 2         # under this much free, C: counts as full
SHOW_ITEMS = 15                  # items of each fix shown in the dry run; the rest are in the list file
ADMIN_HINT = "Right-click pc-tidy.cmd > Run as administrator, and pick it there."

HELP = r"""
pc-tidy shows what fills C: and frees space on it, safely:
- The report lists the fixes for C:, lowest risk first. Low: it comes back by itself. Medium: it can
  be undone or rebuilt. High: it is gone for good. Each number counts exactly the files the fix removes.
- Nothing is deleted until you have seen the exact list (the dry run) and typed yes. High-risk fixes
  ask a second time. Moves can be undone with: python move_off_c.py --undo
- The local AI only describes and sorts. It never decides what is deleted.
- Everything stays on this PC. pc-tidy keeps its reports, lists and records in
  %LOCALAPPDATA%\pc-tidy.

The tools on their own (in PowerShell, in the pc-tidy folder):
  python disk_report.py          the report only
  .\cleanup.ps1 -DryRun          the cleanups switched on in settings.ini, as administrator
  python move_off_c.py           move big folders to another drive
  python tidy_folder.py          sort Downloads (preview first, then --apply)
  python pc_tidy.py settings edit
"""


def run(no_llm=False, no_open=False):
    """The whole flow. Returns the exit code: 0 if everything picked was done, 3 if something was
    skipped, 1 on an error, 4 if another pc-tidy run kept the lock."""
    ctx = context.current()
    try:
        cfg = settings.load(ctx, create=True)
        code = checks(ctx, cfg)
        if code is not None:
            return code
        data = show_report(ctx, cfg, no_llm, no_open)
        code = free_space(ctx, settings.load(ctx), data)
        menu(ctx, no_llm, no_open)
        return code
    except KeyboardInterrupt:
        print("\nStopped. Everything pc-tidy did is recorded; run python pc_tidy.py recover to check.")
        return 1


# ---------- 1. checks ----------
def checks(ctx, cfg):
    """None to go on, or the exit code to stop with."""
    print("pc-tidy: see what fills C: and free space on it, safely.\n")
    if os.name != "nt":
        print("pc-tidy runs on Windows 10 and 11 only.")
        return 1
    for note in cfg.notes:
        print(note)
    if cfg.errors():
        print("\n".join(settings.check_text(cfg)))
        print("pc-tidy goes on: only what reads a section with a mistake is left out.\n")
    notices = journal.notices(ctx)
    if notices:
        print("Runs nobody was watching couldn't do everything:")
        for n in notices:
            print(f"  {n.get('time', '')}  {n.get('message', '')}")
        print("(Clear these with: python pc_tidy.py status --dismiss)\n")
    try:
        recovery.at_start(ctx, "pc_tidy.py", ask=ui.ask)
    except lock.Busy:
        print(lock.BUSY_MESSAGE)
        return lock.BUSY_EXIT
    if not _win.is_elevated():
        print("This window isn't running as administrator, so the Windows folders (Windows Temp, crash dumps, "
              f"hibernation) can't be measured or cleaned here. For those: {ADMIN_HINT}\n")
    other = ctx.other_account()
    if other:
        print(f"This window runs as {other[0]}, but {other[1]} is signed in. {other[1]}'s Temp, Downloads and "
              f"other folders are left alone here: only the Windows folders can be cleaned. For the rest, run "
              f"pc-tidy from {other[1]}'s own account.\n")
    free = volume.free_bytes(ctx.system_drive)
    if free is not None and free < FULL_C:
        print(f"{ctx.system_drive[:2]} is full ({gb(free)} free). Start with the low-risk fixes at the top, which "
              "come back on their own. Moves are offered once there's room to record them.\n")
    return None


# ---------- 2-3. scan and report ----------
def show_report(ctx, cfg, no_llm=False, no_open=False):
    import disk_report
    opts = disk_report.prepare(cfg)
    roots = [ctx.profile] if ctx.sandbox else [ctx.system_drive]      # the tests never scan this PC
    data = disk_report.build_report(roots, use_llm=not no_llm and opts.ai.enabled, opts=opts)
    path = save_report(ctx, data, disk_report.render_text(data))
    print("\n".join(fix_list(data["fixes"], availability(ctx, cfg, data["fixes"]["rows"]))))
    if path:
        print(f"\nThe whole report is in {path}")
        if path.endswith(".pdf") and not no_open and not ctx.sandbox and hasattr(os, "startfile"):
            os.startfile(path)
    return data


def save_report(ctx, data, text):
    """The report in the reports folder of pc-tidy's data folder (never one OneDrive uploads). A PDF,
    or text if reportlab is missing. None if it couldn't be saved (C: full), which stops nothing."""
    folder = ctx.folder("reports")
    stem = os.path.join(folder, f"disk_report-{time.strftime('%Y-%m-%d-%H%M%S')}")
    try:
        os.makedirs(folder, exist_ok=True)
        try:
            import pdf_report
        except ImportError:
            print("The PDF report needs reportlab (pip install -r requirements.txt), so it is saved as text.")
        else:
            pdf_report.render(data, stem + ".pdf")
            return stem + ".pdf"
        with open(stem + ".txt", "w", encoding="utf-8") as f:
            f.write(text + "\n")
        return stem + ".txt"
    except OSError as e:
        print(f"Couldn't save the report ({e.strerror or e}). The list of fixes is below.")
        return None


def availability(ctx, cfg, rows):
    """{fix id: None if pc-tidy can do it here, else why not}."""
    elevated = _win.is_elevated()
    conda = shutil.which("conda")
    other = ctx.other_account()
    out = {}
    for row in rows:
        a = row["action"]
        why = None
        if not a:
            why = "by hand"
        elif other and row["scope"] == "user":
            why = f"belongs to {other[1]}, and this window runs as {other[0]}"
        elif row["scope"] == "machine" and not elevated:
            why = "needs an administrator window"
        elif row["state"] == "admin":
            why = "needs an administrator window"
        elif ("tool" in a or "count_only" in a) and ctx.sandbox:
            why = "not in the tests"        # they would act on this PC itself
        elif a.get("tool", "").startswith("conda") and not conda:
            why = "needs conda: open an Anaconda Prompt, go to the pc-tidy folder and run python pc_tidy.py"
        elif row["id"] == "installers" and cfg.key_errors("cleanup", "installer_folders", "installers_min_age_days"):
            why = "settings.ini has a mistake in [cleanup] (python pc_tidy.py settings check)"
        elif row["id"] in ("temp-user", "temp-windows") and cfg.key_errors("cleanup", "temp_min_age_days"):
            why = "settings.ini has a mistake in [cleanup] (python pc_tidy.py settings check)"
        out[row["id"]] = why
    return out


def fix_list(f, avail):
    """The ranked fixes as the guided flow shows them: what pc-tidy can do here, and what not."""
    out = [f"\nFIXES FOR {f['drive']}, LOWEST RISK FIRST"]
    if f.get("size"):
        out.append(f"  {f['drive']} has {gb(f['free'])} free of {gb(f['size'])} "
                   f"({f['free'] / f['size'] * 100:.0f}%).")
    if not f["rows"]:
        out.append("  Nothing found that is worth doing.")
    for n, r in enumerate(f["rows"], 1):
        why = avail.get(r["id"])
        tail = "" if why is None else f"   ({why})"
        out.append(f"  {n:>2}  {r['risk']:<6}  {r['size']:>22}  {r['title']}{tail}")
    if f.get("target_line"):
        out.append(f"  {f['target_line']}")
    out += [f"  {n}" for n in f["notes"] if not n.startswith("Rows done by cleanup.ps1")]
    return out


# ---------- 4. pick ----------
def parse_pick(text, rows, avail):
    """(indexes, None), or (None, what's wrong with the answer)."""
    picked = []
    for part in text.replace(",", " ").split():
        token = part.strip().lower()
        if token == "l":
            picked += [i for i, r in enumerate(rows) if r["risk"] == "Low" and avail.get(r["id"]) is None]
            continue
        bounds = token.split("-")
        if not all(b.isdigit() for b in bounds) or len(bounds) > 2:
            return None, f"I didn't understand '{part}'. Type numbers like 1,3 or 2-4, L for the low-risk ones, " \
                         "or press Enter to stop."
        a, b = int(bounds[0]), int(bounds[-1])
        for n in (a, b):
            if not 1 <= n <= len(rows):
                return None, f"There is no fix {n}: the list goes from 1 to {len(rows)}."
        picked += range(min(a, b) - 1, max(a, b))
    return sorted(set(picked)), None


def pick(rows, avail):
    """The rows you picked. Empty if you pressed Enter, or if the input ended."""
    if not rows:
        return []
    while True:
        answer = ui.ask("\nType the numbers of the fixes you want (e.g. 1,2,4 or 1-3), L for all the low-risk ones, "
                        "or press Enter to stop: ")
        if answer is None or not answer.strip():
            return []
        indexes, problem = parse_pick(answer, rows, avail)
        if problem:
            print(problem)
            continue
        return [dict(rows[i], n=i + 1) for i in indexes]


# ---------- 5. the dry run ----------
class DryRun:
    """The plans for the fixes you picked, built now from the disk: deletes (and tools) in one plan,
    moves in move_off_c.py's own plan, so `python move_off_c.py --undo` puts them back."""

    def __init__(self, ctx, cfg, rows, move_dest=None):
        self.ctx, self.cfg, self.rows = ctx, cfg, rows
        self.deletes = Plan(COMMAND, ctx, plan_settings(cfg), info={"fixes": [r["id"] for r in rows]})
        self.moves = None
        self.refused = []          # (path, why)
        self.by_fix = collections.defaultdict(list)
        self.young = collections.Counter()      # fix id: items left out for being too new
        moves = []
        for row in rows:
            a = row["action"]
            start = len(self.deletes.actions)
            if "delete" in a:
                self._deletes(row, a)
            elif "tool" in a:
                self._tool(row, a)
            elif "count_only" in a:
                measured = executor.COUNT_ONLY[a["count_only"]][1]()
                if measured and measured.get("items"):
                    self.deletes.add_count_only(a["count_only"], measured)
            elif "move" in a:
                moves.append({"path": a["move"], "fix": row["id"]})
            for action in self.deletes.actions[start:]:
                action["fix"] = row["id"]
                self.by_fix[row["id"]].append(action)
        self._pin_kept_copies()
        if moves and move_dest:
            import move_off_c
            fix_of = {os.path.abspath(m["path"]): m["fix"] for m in moves}
            self.moves = move_off_c.build_plan(moves, ctx.profile, move_dest, ctx)
            for action in self.moves.actions:
                action["fix"] = fix_of.get(action["src"], f"move:{action['src']}")
                self.by_fix[action["fix"]].append(action)

    def _add(self, path):
        problem = safety.why_refused(path, "delete", self.ctx)
        if problem:
            self.refused.append((path, problem))
            return None
        try:
            return self.deletes.add_delete(path)
        except FileNotFoundError:
            return None

    def _duplicates(self, pairs):
        """Each extra copy, with the copy that stays: the executor deletes the extra only while that
        copy is still there, unchanged."""
        for extra, keep, extra_fp, keep_fp in pairs:
            if leftovers._fp(extra) != extra_fp or leftovers._fp(keep) != keep_fp:
                self.refused.append((extra, "it or the copy that would stay changed since the scan"))
                continue
            action = self._add(extra)
            if action is not None:
                action["keep"], action["keep_fp"] = keep, fingerprint(keep)

    def _pin_kept_copies(self):
        """The copy that stays of a duplicate is never deleted by another fix in the same run, so an
        extra copy can't go together with the copy that was meant to stay (problem 21)."""
        kept = {a["keep"]: a["path"] for a in self.deletes.actions if a.get("keep")}
        if not kept:
            return
        drop = []
        for a in self.deletes.actions:
            if a["op"] not in ("delete", "delete_tree") or a.get("keep"):
                continue
            hit = next((k for k in kept if inside(k, a["path"])), None)
            if hit:
                drop.append(a)
                self.refused.append((a["path"], f"{'it is' if inside(a['path'], hit) else 'it holds'} the copy "
                                                f"that stays of {kept[hit]}"))
        if drop:
            gone = {id(a) for a in drop}
            self.deletes.actions = [a for a in self.deletes.actions if id(a) not in gone]
            for fix in self.by_fix:
                self.by_fix[fix] = [a for a in self.by_fix[fix] if id(a) not in gone]

    def _deletes(self, row, a):
        if a["delete"] == "duplicates":
            return self._duplicates(a["pairs"])
        places = a["places"]
        if row["id"] == "installers":           # listed again now: one may have changed since the scan
            from . import fixes
            fresh = fixes.installers_fix(self.ctx, self.cfg)
            places = fresh.action["places"] if fresh else []
        days = a.get("min_age_days", 0)
        for place in places:
            if a["delete"] == "contents":
                try:
                    names = sorted(os.listdir(place), key=str.lower)
                except OSError:
                    continue
                for name in names:
                    if old_enough(os.path.join(place, name), days):
                        self._add(os.path.join(place, name))
                    else:
                        self.young[row["id"]] += 1
            elif a["delete"] == "patterns":
                for path in expand(place):
                    if os.path.isfile(path):
                        self._add(path)
            elif a["delete"] == "matches":      # your extra paths: every file or folder the path names
                for path in expand(place):
                    self._add(path)
            else:
                self._add(place)

    def _tool(self, row, a):
        name = a.get("name")
        if a["tool"] == "conda_env_remove" and (name == "base" or os.environ.get("CONDA_DEFAULT_ENV") == name):
            self.refused.append((name, "it is conda's own environment, or the one this window is in"))
            return
        self.deletes.add_tool(a["tool"], measured=None, **({"name": name} if name else {}))

    @property
    def empty(self):
        return not self.deletes.actions and not (self.moves and self.moves.actions)

    def describe(self, row):
        """Lines saying exactly what this fix will do."""
        acts = self.by_fix.get(row["id"], [])
        head = f"  {row['n']:>2}  {row['title']}"
        young = self.young.get(row["id"])
        stay = [f"        ({young:,} newer item{'s' if young != 1 else ''} stay)"] if young else []
        if not acts:
            return [f"{head}: nothing to do any more"] + stay
        a = acts[0]
        if a["op"] == "run_tool":
            tool = executor.TOOLS[a["tool"]]
            return [f"{head}: run {' '.join(tool.command(a))}. {tool.label} decides exactly what it removes."]
        if a["op"] == "empty_recycle_bin":
            m = a["measured"]
            return [f"{head}: empty the Recycle Bin on every drive, {m['items']:,} item{'s' if m['items'] != 1 else ''}, "
                    f"{gb(m['bytes'])}. This can't be undone."]
        if a["op"] == "move":
            return [f"{head}: move {a['src']}  ->  {a['dst']}, {gb(a['bytes'])}. "
                    "Undo with: python move_off_c.py --undo"]
        size = sum(x.get("bytes", 0) for x in acts)
        lines = [f"{head}: delete {len(acts):,} item{'s' if len(acts) != 1 else ''}, {gb(size)}"]
        lines += [f"        {x['path']}" + (f"   (the copy in {os.path.dirname(x['keep'])} stays)" if x.get("keep") else "")
                  for x in acts[:SHOW_ITEMS]]
        if len(acts) > SHOW_ITEMS:
            lines.append(f"        ... and {len(acts) - SHOW_ITEMS:,} more")
        return lines + stay

    def text(self):
        lines = [f"pc-tidy dry run {self.deletes.id}, {time.strftime('%Y-%m-%d %H:%M')}. Nothing has changed yet.", ""]
        for row in self.rows:
            lines.append(f"{row['n']}  {row['title']}")
            for a in self.by_fix.get(row["id"], []):
                if a["op"] in ("delete", "delete_tree"):
                    stays = f", the copy {a['keep']} stays" if a.get("keep") else ""
                    lines.append(f"    delete  {a['path']}  ({gb(a.get('bytes', 0))}{stays})")
                elif a["op"] == "move":
                    lines.append(f"    move    {a['src']}  ->  {a['dst']}  ({gb(a['bytes'])})")
                else:
                    lines += self.describe(row)[:1]
        for path, why in self.refused:
            lines.append(f"refused: {path}: {why}")
        return "\n".join(lines) + "\n"

    def save_text(self):
        """The full list as a text file, or None if it couldn't be saved (C: full)."""
        folder = self.ctx.folder("plans")
        path = os.path.join(folder, f"{self.deletes.id}-dry-run.txt")
        try:
            os.makedirs(folder, exist_ok=True)
            with open(path, "x", encoding="utf-8") as f:
                f.write(self.text())
        except OSError:
            return None
        return path


def plan_settings(cfg):
    """What the deletes plan was built under: the executor refuses it if these change before the yes."""
    return {"installer_folders": cfg.value("cleanup", "installer_folders"),
            "installers_min_age_days": cfg.value("cleanup", "installers_min_age_days"),
            "temp_min_age_days": cfg.value("cleanup", "temp_min_age_days")}


def confirm_high(dry, ask=ui.ask):
    """Each high-risk fix asks once more, saying what is lost. Its actions are dropped unless you type yes."""
    for row in dry.rows:
        acts = dry.by_fix.get(row["id"])
        if row["risk"] != "High" or not acts:
            continue
        answer = ask(f"\n{row['title']} is high risk: it can't be undone. Type yes again to include it, "
                     "or press Enter to leave it out: ")
        if (answer or "").strip().lower() != "yes":
            dry.deletes.actions = [a for a in dry.deletes.actions if a.get("fix") != row["id"]]
            dry.by_fix.pop(row["id"], None)
            print(f"  Left out: {row['title']}.")


# ---------- the whole step 4-8 ----------
def free_space(ctx, cfg, data):
    rows = data["fixes"]["rows"]
    avail = availability(ctx, cfg, rows)
    picked = pick(rows, avail)
    if not picked:
        print("Nothing was changed.")
        return 0
    doable = []
    for row in picked:
        why = avail.get(row["id"])
        if why is None:
            doable.append(row)
        elif why == "by hand":
            print(f"  {row['n']:>2}  {row['title']}: do it by hand. {row['how']}.")
        else:
            print(f"  {row['n']:>2}  {row['title']}: not done here, it {why}."
                  + (f" {ADMIN_HINT}" if "administrator" in why else ""))
    if not doable:
        return 0
    move_dest = (data.get("move") or {}).get("dest")
    dry = DryRun(ctx, cfg, doable, move_dest)
    listing = show_dry_run(dry, "\nDRY RUN: this is exactly what will happen. Nothing has changed yet.")
    if dry.empty:
        print("There is nothing left to do: everything picked is already gone.")
        return 0
    if dry.moves and dry.moves.actions:
        import move_off_c
        print(move_off_c.BACKUP_NOTE)
    return confirm_and_run(ctx, dry, listing)


def show_dry_run(dry, header):
    """The dry run on screen: what each fix will do, what was left out and why, the total. Returns
    the path of the full list, or None if it couldn't be saved or there is nothing to do."""
    print(header)
    for row in dry.rows:
        print("\n".join(dry.describe(row)))
    for path, why in dry.refused:
        print(f"  Left out {path}: {why}.")
    if dry.empty:
        return None
    total = sum(a.get("bytes") or (a.get("measured") or {}).get("bytes", 0) for a in dry.deletes.actions) + \
        sum(a["bytes"] for a in (dry.moves.actions if dry.moves else []))
    print(f"\nIn all: about {gb(total)}.")
    listing = dry.save_text()
    if listing:
        print(f"The full list: {listing}")
    return listing


def confirm_and_run(ctx, dry, listing):
    """The typed yes, a second yes for each high-risk fix, then the run and its summary."""
    if not confirm(ctx, listing):
        print("Cancelled, nothing changed.")
        return 0
    confirm_high(dry)
    if dry.empty:
        print("Nothing left to do. Nothing was changed.")
        return 0
    return execute(ctx, dry)


def confirm(ctx, listing):
    """True only for yes. O opens the full list in Notepad first."""
    while True:
        answer = ui.ask("\nType yes to do all of this, O to see the full list, or press Enter to cancel: ")
        choice = (answer or "").strip().lower()
        if choice == "o":
            if listing and not ctx.sandbox:
                subprocess.Popen(["notepad.exe", listing])
            print(f"The list is in {listing}." if listing else "The list couldn't be saved; it is above.")
            continue
        return choice == "yes"


# ---------- 7-8. run and summary ----------
class _Progress:
    def __init__(self, dry):
        self.dry, self.current, self.count, self.line = dry, None, 0, Line()
        self.titles = {r["id"]: (r["n"], r["title"]) for r in dry.rows}

    def start(self, a):
        fix = a.get("fix")
        if fix != self.current:
            self.current, self.count = fix, 0
            n, title = self.titles.get(fix, ("", fix))
            print(f"  {n}  {title} ...")
        self.count += 1
        total = len(self.dry.by_fix.get(fix, []))
        if total > 1:
            self.line.show(f"    {self.count:,} of {total:,}")


def execute(ctx, dry):
    drive = ctx.system_drive[:2]
    before = volume.free_bytes(ctx.system_drive)
    progress = _Progress(dry)
    outcomes, records = [], []
    print("\nWorking ...")
    try:
        if dry.deletes.actions:
            result = executor.execute(dry.deletes, ctx, settings=plan_settings(settings.load(ctx)),
                                      on_action=progress.start)
            outcomes += result.outcomes
            records.append(result.journal_path)
        if dry.moves and dry.moves.actions:
            import move_off_c
            problem = volume.destination_problem(dry.moves.info["dest"], ctx)
            if problem:
                print(f"  The moves weren't done: {problem}")
                outcomes += [executor.Outcome(a, "skipped", problem) for a in dry.moves.actions]
            else:
                result = executor.execute(dry.moves, ctx,
                                          settings=move_off_c.plan_settings(move_off_c.options(settings.load(ctx))),
                                          on_action=progress.start)
                outcomes += result.outcomes
                records.append(result.journal_path)
    except lock.Busy:
        print(lock.BUSY_MESSAGE)
        return lock.BUSY_EXIT
    except executor.PlanRefused as e:
        print(e)
        return 1
    progress.line.clear()
    after = volume.free_bytes(ctx.system_drive)
    return summary(dry, outcomes, before, after, drive, [r for r in records if r])


def summary(dry, outcomes, before, after, drive, records):
    print("\nDONE")
    skipped_any = False
    for row in dry.rows:
        outs = [o for o in outcomes if o.action.get("fix") == row["id"]]
        if not outs:
            continue
        done = [o for o in outs if o.status == "done"]
        skipped = [o for o in outs if o.status != "done"]
        skipped_any = skipped_any or bool(skipped)
        nbytes = sum(o.bytes for o in done)
        op = outs[0].action["op"]
        if op == "move":
            what = f"moved {gb(nbytes)}"
        elif op == "run_tool":
            what = "done" if done else "not done"
        else:
            what = f"freed {gb(nbytes)}"
        line = f"  {row['n']:>2}  {row['title']}: {what}"
        if op not in ("run_tool", "move") and row.get("size"):
            line += f" (the report said {row['size']})"
        print(line)
        if skipped:
            reasons = collections.Counter(o.reason or o.status for o in skipped)
            print("      " + "; ".join(f"{count:,} skipped: {reason}" for reason, count in reasons.most_common(3)))
    if before is not None and after is not None:
        gained = f" ({'+' if after >= before else '-'}{gb(abs(after - before))})"
        print(f"\nFree space on {drive} went from {gb(before)} to {gb(after)}{gained}.")
    if any(o.action["op"] == "move" and o.status == "done" for o in outcomes):
        print("Undo the moves with: python move_off_c.py --undo")
    if any(o.action["op"] in ("delete", "delete_tree", "empty_recycle_bin") and o.status == "done" for o in outcomes):
        print("Deleted files can't be put back.")
    for path in records:
        print(f"What was done is recorded in {path}")
    return 3 if skipped_any else 0


# ---------- the menu ----------
MENU = """
What next?
  1  Free up space on C: again (a new scan and report)
  2  Sort your Downloads into folders (you see the list first)
  3  Undo the last move off C:
  4  Undo the last sort
  5  Open the reports folder
  6  Change settings
  7  Help"""


def _script(name, *args, stdin=None):
    return subprocess.run([sys.executable, os.path.join(context.APP_DIR, name), *args], stdin=stdin)


def menu(ctx, no_llm=False, no_open=False):
    while True:
        print(MENU)
        answer = ui.ask("Type a number, or press Enter to close: ")
        choice = (answer or "").strip()
        if not choice:
            return
        if choice == "1":
            cfg = settings.load(ctx)
            free_space(ctx, cfg, show_report(ctx, cfg, no_llm, no_open))
        elif choice == "2":
            sort_downloads(ctx, no_llm)
        elif choice == "3":
            _script("move_off_c.py", "--undo")
        elif choice == "4":
            _script("tidy_folder.py", "--undo", stdin=subprocess.DEVNULL)
        elif choice == "5":
            folder = ctx.folder("reports")
            os.makedirs(folder, exist_ok=True)
            print(f"The reports are in {folder}")
            if not ctx.sandbox and hasattr(os, "startfile"):
                os.startfile(folder)
        elif choice == "6":
            _script("pc_tidy.py", "settings", "edit")
        elif choice == "7":
            print(HELP)
        else:
            print("Type a number from 1 to 7, or press Enter to close.")


def sort_downloads(ctx, no_llm=False):
    """tidy_folder.py's preview, then --apply after a yes, then what's left in your folders. Moves
    only, and --undo puts them back."""
    extra = ["--no-llm"] if no_llm else []
    script = os.path.join(context.APP_DIR, "tidy_folder.py")
    preview = subprocess.run([sys.executable, script, *extra], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace")
    out = preview.stdout.replace("Run again with --apply to move them", "Type yes below to move them")
    print(out, end="")
    if preview.returncode or "Nothing to sort." in out:
        return
    if not ui.ask_yes("Type yes to sort them: "):
        print("Nothing was moved.")
        return
    _script("tidy_folder.py", "--apply", *extra, stdin=subprocess.DEVNULL)
    print("\n".join(leftovers.render(leftovers.analyse(ctx, settings.load(ctx)), gb)))
    print("The extra copies in Downloads, Desktop and Sorted are in the fixes: type 1 below for a new list.")
