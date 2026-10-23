r"""
tidy_folder.py - sort a messy folder (Downloads by default), using a local LLM for the hard cases.

Usage:
    python tidy_folder.py                          # preview Downloads: shows what would move, changes nothing
    python tidy_folder.py --apply                  # move the files into ~/Sorted
    python tidy_folder.py --undo                   # put back everything from the last --apply run
    python tidy_folder.py --source "%USERPROFILE%\Desktop" --in-place --apply
                                                   # sort the Desktop into folders inside the Desktop
    python tidy_folder.py --source D:\Stuff --folders
                                                   # also sort whole subfolders (asks the LLM what each one is)
    python tidy_folder.py --no-llm                 # rules only, skip the LLM

Setup (once):
    1. Install Ollama from https://ollama.com, then in a terminal:  ollama pull llama3.2:3b
    2. pip install -r requirements.txt

Settings are in settings.ini (python pc_tidy.py settings edit): [tidy] has the folders, the archive
age and the AI's categories, [tidy.extensions] the folder for each extension, and [ai] the model.
--source and --dest win over the folders there.

It never waits for input (the weekly task runs it with nobody watching) and never deletes anything.
Every move is recorded in pc-tidy's data folder (%LOCALAPPDATA%\pc-tidy\runs) before it happens, so
--undo works even after a crash. Exit codes: 0 done, 1 refused or nothing could be recorded,
2 a mistake in [tidy] or [tidy.extensions] of settings.ini (nothing is sorted: sorting with defaults
could file things where you didn't ask), 3 something was skipped, 4 another run was busy. With
--apply, 2, 3 and 4 are also kept as a notice (python pc_tidy.py status). A mistake in [ai] only
makes it sort by the rules, as with --no-llm, with a notice too.
"""

import argparse
import logging
import os
import re
import sys
import time
from collections import namedtuple
from pathlib import Path

import llm
from pctidy import context, errors, executor, journal, lock, recovery, safety, settings, ui
from pctidy import undo as undo_core
from pctidy.paths import inside
from pctidy.plan import Plan

SKIP_SUFFIXES = {".crdownload", ".part", ".partial", ".download", ".tmp",
                 ".lnk", ".url"}     # shortcuts stay where they are (Desktop)
SKIP_NAMES = {"desktop.ini", "thumbs.db"}
SECTIONS = ("tidy", "tidy.extensions")      # a mistake in these stops the sort (exit 2)

logging.getLogger("pypdf").setLevel(logging.ERROR)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

# what a sort is built under, from [tidy] and [tidy.extensions]:
#   rules: extension -> folder (anything not listed goes to the LLM); categories: the folders the LLM
#   chooses from; archive_after_days: older files go under <dest>/Archive/<folder> (0 = never);
#   skip_newer_than_min: very recent files are left alone (they may still be downloading)
Options = namedtuple("Options", "rules categories archive_after_days skip_newer_than_min rename_with_llm")


def options(cfg=None):
    cfg = cfg or settings.load()
    t = cfg.section("tidy")
    return Options(cfg.table("tidy.extensions"), t.categories, t.archive_after_days, t.skip_newer_than_min,
                   t.rename_with_ai)


def our_folders(opts):
    """Folders this script creates; never sorted again when sorting in place."""
    return {name.lower() for name in list(opts.rules.values()) + opts.categories + ["Archive", "_logs"]}


def read_snippet(path, limit=1500):
    """First bit of text from a document, so the LLM can see what it's about."""
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            text = " ".join((p.extract_text() or "") for p in PdfReader(str(path)).pages[:2])
        elif ext == ".docx":
            import docx
            text = " ".join(p.text for p in docx.Document(str(path)).paragraphs[:60])
        elif ext in {".txt", ".md", ".tex", ".rtf", ".log"}:
            text = path.read_text(errors="ignore")[: limit * 2]
        else:
            return ""
    except Exception:
        return ""
    return re.sub(r"\s+", " ", text).strip()[:limit]


def ask_llm(path, snippet, categories):
    schema = {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": categories},
            "new_name": {"type": "string"},
        },
        "required": ["category", "new_name"],
    }
    prompt = (
        "You sort files from a messy folder.\n"
        f"Pick the best category from: {', '.join(categories)}.\n"
        "Also suggest a short, clear filename without extension (words joined by underscores, "
        "max 60 characters). Keep the original name if it is already clear.\n\n"
        f"Filename: {path.name}\n"
        f"Content preview: {snippet or '(none)'}\n"
    )
    answer = llm.ask_json(prompt, schema)
    category = answer.get("category")
    return (category if category in categories else "Other"), answer.get("new_name", "")


def folder_sample(folder, limit=25):
    """Some file names inside a folder, plus its file count and size, for the LLM."""
    names, count, size = [], 0, 0
    for dirpath, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
        for f in files:
            count += 1
            try:
                size += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
            if len(names) < limit:
                names.append(os.path.relpath(os.path.join(dirpath, f), folder))
    return names, count, size


def ask_llm_folder(folder, categories):
    names, count, size = folder_sample(folder)
    schema = {"type": "object", "properties": {"category": {"type": "string", "enum": categories}},
              "required": ["category"]}
    prompt = (
        "You sort folders from a messy folder into categories.\n"
        f"Pick the best category from: {', '.join(categories)}.\n\n"
        f"Folder name: {folder.name}\n"
        f"It has {count} files, {size / 1024 ** 2:.0f} MB. Some of them:\n"
        + "\n".join(f"- {n}" for n in names) + "\n"
    )
    category = llm.ask_json(prompt, schema).get("category")
    return category if category in categories else "Other"


def clean_name(suggested, original):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", suggested).strip(" ._")
    name = re.sub(r"\s+", "_", name)[:60]
    return (name or original.stem) + original.suffix


def unique(target, taken):
    """Add ' (1)', ' (2)' ... if the name is already used."""
    candidate, i = target, 1
    while candidate.exists() or candidate in taken:
        candidate = target.with_name(f"{target.stem} ({i}){target.suffix}")
        i += 1
    return candidate


def is_hidden(path):
    return bool(getattr(path.stat(), "st_file_attributes", 0) & 2)  # Windows hidden flag


def plan_moves(source, dest, use_llm, folders=False, opts=None):
    opts = opts or options()
    now = time.time()
    moves, taken = [], set()
    in_place = source.resolve() == dest.resolve()
    entries = sorted(source.iterdir(), key=lambda p: p.name.lower())

    for f in (p for p in entries if p.is_file()):
        ext = f.suffix.lower()
        if ext in SKIP_SUFFIXES or f.name.lower() in SKIP_NAMES or f.name.startswith("~$") or is_hidden(f):
            continue
        age_days = (now - f.stat().st_mtime) / 86400
        if age_days * 1440 < opts.skip_newer_than_min:
            continue

        new_name = f.name
        if ext in opts.rules:
            folder = opts.rules[ext]
        elif use_llm:
            print(f"  asking LLM about {f.name} ...")
            try:
                folder, suggestion = ask_llm(f, read_snippet(f), opts.categories)
            except Exception as e:
                print(f"    LLM failed ({e}), leaving it in place")
                continue
            if opts.rename_with_llm and suggestion:
                new_name = clean_name(suggestion, f)
        else:
            continue  # no LLM: leave it for a later run

        base = dest
        if folder != settings.INSTALLER_FOLDER and opts.archive_after_days and age_days > opts.archive_after_days:
            base = dest / "Archive"
        target = unique(base / folder / new_name, taken)
        taken.add(target)
        moves.append((f, target))

    if folders:
        if not use_llm:
            print("  --folders needs the LLM (Ollama), so subfolders are left alone.")
            return moves
        for d in (p for p in entries if p.is_dir()):
            if d.name.startswith(".") or is_hidden(d) or d.resolve() == dest.resolve():
                continue
            if in_place and d.name.lower() in our_folders(opts):
                continue   # a folder this script made on an earlier run
            print(f"  asking LLM about folder {d.name} ...")
            try:
                category = ask_llm_folder(d, opts.categories)
            except Exception as e:
                print(f"    LLM failed ({e}), leaving it in place")
                continue
            target = unique(dest / category / d.name, taken)
            taken.add(target)
            moves.append((d, target))
    return moves


COMMAND = "tidy_folder"


def plan_settings(opts):
    """What a plan was built under: the executor refuses a plan whose settings changed since."""
    return {"rules": opts.rules, "categories": opts.categories, "archive_after_days": opts.archive_after_days,
            "skip_newer_than_min": opts.skip_newer_than_min, "rename_with_llm": opts.rename_with_llm}


def build_plan(moves, source, dest, ctx=None, opts=None):
    """The exact list for the executor, with each item's fingerprint taken now, at the dry run.
    Items the safety check refuses are left out, with a line saying why."""
    ctx = ctx or context.current()
    opts = opts or options(settings.load(ctx))
    plan = Plan(COMMAND, ctx, plan_settings(opts), info={"source": str(source), "dest": str(dest)})
    for src, dst in moves:
        problem = safety.why_refused(src, "move", ctx) or safety.why_refused(dst, "move-to", ctx)
        if problem:
            print(f"  not moving {src.name}: {problem}")
            continue
        try:
            plan.add_move(src, dst)
        except FileNotFoundError:
            continue
    return plan


def apply_moves(moves, dest, ctx=None, source=None):
    """Move the planned (source, target) pairs. Returns the exit code."""
    ctx = ctx or context.current()
    return run_plan(build_plan(moves, source or dest, dest, ctx), ctx)


def _notice(ctx, message, **details):
    journal.attention(ctx, "tidy", message, command="tidy_folder.py", **details)


def run_plan(plan, ctx, waiting=()):
    try:
        # read again: if settings.ini changed since the dry run, the executor refuses the plan
        result = executor.execute(plan, ctx, settings=plan_settings(options(settings.load(ctx))))
    except lock.Busy:
        print(lock.BUSY_MESSAGE)
        _notice(ctx, f"The sort of {plan.info.get('source')} didn't run: another pc-tidy run was busy.")
        return lock.BUSY_EXIT
    except executor.PlanRefused as e:
        print(e)
        return 1
    _print_skipped(result.skipped)
    moved = len([o for o in result.done if o.action["op"] == "move"])
    print(f"\nMoved {moved} items. Undo with the same command plus --undo instead of --apply")
    if not result.skipped:
        return 3 if waiting else 0
    reasons = sorted({o.reason for o in result.skipped})
    _notice(ctx, f"The sort of {plan.info.get('source')} skipped {len(result.skipped)} item(s): " + "; ".join(reasons))
    if not moved and any("too full to record" in r for r in reasons):
        return 1
    return 3


def _print_skipped(skipped):
    by_reason = {}
    for o in skipped:
        by_reason.setdefault(o.reason, []).append(Path(o.name).name)
    for reason, names in by_reason.items():
        if len(names) > 3:
            print(f"  skipped {len(names)} items: {reason}")
        else:
            for name in names:
                print(f"  skipped {name}: {reason}")


def undo(dest, ctx=None):
    """Put back the last --apply run into this dest, from pc-tidy's journal or an old _logs file."""
    ctx = ctx or context.current()
    dest = Path(dest)
    try:
        recovery.at_start(ctx, "tidy_folder.py")
        out = undo_core.undo_last(ctx, COMMAND, dest=str(dest),
                                  legacy=[(str(dest / "_logs"), "run_*.json", str(dest))], ask=ui.ask)
    except lock.Busy:
        print(lock.BUSY_MESSAGE)
        return lock.BUSY_EXIT
    except executor.PlanRefused as e:
        print(e)
        return 1
    if out.source is None:
        print("Nothing to undo.")
        return 0
    print(f"Put back {len(out.restored)} items from {out.source.name}.")
    undo_core.report_kept(out)
    return 3 if out.kept else 0


def _given(path):
    return Path(os.path.expandvars(str(path))).expanduser()


def _ai_ready(cfg, ctx, unattended):
    """True if the LLM may be used: [ai] has no mistake (else rules only, with a notice), it is
    switched on, and Ollama answers."""
    problems = cfg.errors("ai")
    if problems:
        print(f"{cfg.name} has a mistake in [ai], so files are sorted by the rules only (as with --no-llm):")
        for p in problems:
            print(f"  {cfg.describe(p)}")
        if unattended:
            _notice(ctx, f"The sort on {time.strftime('%Y-%m-%d')} used the rules only, without the AI: "
                         f"{cfg.describe(problems[0])}. Fix it with: python pc_tidy.py settings edit")
        return False
    ai = cfg.section("ai")
    if not ai.enabled:
        print(f"The AI is switched off in {cfg.name} ([ai] enabled = no).")
        return False
    llm.configure(ai)
    return llm.available()


def main():
    ap = argparse.ArgumentParser(description="Sort a messy folder into subfolders.",
                                 epilog=ui.examples(__doc__), formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="actually move files")
    ap.add_argument("--undo", action="store_true", help="revert the last --apply run")
    ap.add_argument("--no-llm", action="store_true", help="rules only, skip the LLM")
    ap.add_argument("--source", type=Path,
                    help="folder to sort (default: [tidy] source in settings.ini, normally Downloads)")
    ap.add_argument("--dest", type=Path,
                    help="where sorted folders go (default: [tidy] dest in settings.ini, normally ~/Sorted)")
    ap.add_argument("--in-place", action="store_true", help="put the sorted folders inside the source folder")
    ap.add_argument("--folders", action="store_true", help="also sort whole subfolders (needs the LLM)")
    args = ap.parse_args()

    ctx = context.current()
    cfg = settings.load(ctx, create=True)
    for note in cfg.notes:
        print(note)
    if args.undo:      # it only needs the folder it puts things back from
        given = args.source if args.in_place else args.dest
        problems = [] if given else cfg.key_errors("tidy", "source" if args.in_place else "dest")
    else:
        problems = cfg.errors(*SECTIONS)
    if problems:       # sorting with defaults instead could file things where you didn't ask
        print(settings.refusal(cfg, problems, "tidy_folder.py"))
        if args.apply:
            more = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
            _notice(ctx, f"The sort didn't run on {time.strftime('%Y-%m-%d')}: {cfg.describe(problems[0])}{more}. "
                         "Fix it with: python pc_tidy.py settings edit")
        return settings.EXIT
    opts, folders = options(cfg), cfg.section("tidy")
    source = _given(args.source) if args.source else Path(folders.source)
    dest = source if args.in_place else _given(args.dest) if args.dest else Path(folders.dest)

    if args.undo:
        return undo(dest, ctx)
    if not source.is_dir():
        print(f"{source} is not a folder.")
        return 0
    problem = safety.why_refused(source, "sort", ctx)
    if problem:      # e.g. the whole profile with --folders (problem 13)
        print(f"Not sorting {source}: {problem}. Pick a folder inside it, such as Downloads.")
        return 1
    if dest != source:
        problem = safety.why_refused(dest, "move-to", ctx)
        if problem:
            print(f"Not sorting into {dest}: {problem}.")
            return 1

    if args.apply:
        try:
            waiting = recovery.at_start(ctx, "tidy_folder.py").undecided      # never asks
        except lock.Busy:
            print(lock.BUSY_MESSAGE)
            _notice(ctx, f"The sort of {source} didn't run: another pc-tidy run was busy.")
            return lock.BUSY_EXIT
    else:
        waiting = recovery.undecided(ctx)
    blocked = [p for item in waiting for p in (item.src, item.dst) if p]
    if waiting:
        print("An earlier move is waiting for your decision (run: python pc_tidy.py recover). "
              "Items that touch it are skipped.")
        if args.apply:
            _notice(ctx, "An interrupted move is waiting for your decision. Run: python pc_tidy.py recover",
                    paths=blocked)

    use_llm = not args.no_llm and _ai_ready(cfg, ctx, args.apply)
    if not args.no_llm and not use_llm:
        print("Files that need the LLM will be left in place.\n")
    moves = [(src, dst) for src, dst in plan_moves(source, dest, use_llm, folders=args.folders, opts=opts)
             if not any(inside(p, b) or inside(b, p) for p in (src, dst) for b in blocked)]
    if not moves:
        print("Nothing to sort.")
        return 3 if waiting else 0
    plan = build_plan(moves, source, dest, ctx, opts)

    print()
    for a in plan.actions:
        kind = "[folder] " if a["kind"] == "dir" else ""
        print(f"{kind}{Path(a['src']).name}  ->  {Path(a['dst']).relative_to(dest)}")

    if args.apply:
        plan.save_copy()
        return run_plan(plan, ctx, waiting)
    print(f"\nPreview only, {len(plan.actions)} items. Run again with --apply to move them into {dest}.")
    return 3 if waiting else 0


if __name__ == "__main__":
    sys.exit(errors.run(main, "tidy_folder.py"))
