r"""
move_off_c.py - free up C: by moving big personal folders and files to another drive.

Usage:
    python move_off_c.py                                       # list candidates, pick which to move
    python move_off_c.py --path "C:\Users\me\Videos\Lectures"  # move one specific folder or file
    python move_off_c.py --to E:\From-C                        # choose where they go
    python move_off_c.py --undo                                # move the last batch back

Only your own files are offered: folders and big files in your user profile. Apps, games and
system files are never moved, because they break when moved by hand; the script tells you
the right way for those. Each item is copied, checked, and only then deleted from C:.

"Another drive" means another volume: a subst letter or a link that points back into C: is
refused, because moving there frees nothing, while a second disk mounted in a folder on C: is fine.
Every move is recorded before it happens (in %LOCALAPPDATA%\pc-tidy\runs), so --undo works even
after a crash, and an interrupted move is offered to finish or put back the next time this runs.

Settings are in [move] of settings.ini (python pc_tidy.py settings edit): min_gb, the smallest item
offered (--min-gb wins over it), and dest_folder_name, the folder on the other drive (--to wins
over it). A mistake there stops this script with exit code 2; --undo still works.
"""

import argparse
import os
import re
import sys
from pathlib import Path

from pctidy import context, executor, lock, recovery, safety, settings, ui, volume
from pctidy import undo as undo_core
from pctidy.plan import Plan, fingerprint
from scanner import GB, Scan, fmt, norm, system_drive

LOG_DIR = Path.home() / ".pc-tidy" / "move_logs"     # where versions before 2.0 kept their undo logs

COMMAND = "move_off_c"
KNOWN_FOLDERS = ["Documents", "Pictures", "Videos", "Music", "Downloads", "Desktop"]
# folders in your profile that are never offered: app data, synced folders, Windows special folders
SKIP_IN_HOME = re.compile(
    r"^(appdata|\..*|onedrive.*|dropbox|google drive|icloud ?drive|(ana|mini)conda3?|saved games|contacts|"
    r"favorites|links|searches|3d objects|ntuser.*|sorted)$", re.I)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def options(cfg=None):
    """[move] of settings.ini: min_gb and dest_folder_name."""
    return (cfg or settings.load()).section("move")


def default_dest(notes=None, opts=None):
    """<the other volume with the most free space>\\From-C, or None. Chosen from volumes, not drive
    letters, so a subst letter is never picked; a locked or unreadable drive is skipped (and named
    in `notes`, if a list is given)."""
    opts = opts or options()
    dest, skipped = volume.default_destination(context.current(), opts.dest_folder_name)
    if notes is not None:
        notes.extend(skipped)
    return dest


def other_drive():
    """The root of the default destination's drive, or None."""
    dest = default_dest()
    return os.path.dirname(dest) if dest else None


def move_candidates(scan, home, min_bytes=None):
    """Personal folders and big files in your profile that are safe to move, biggest first."""
    min_bytes = options().min_gb * GB if min_bytes is None else min_bytes
    out, known = [], {norm(os.path.join(home, k)) for k in KNOWN_FOLDERS}
    for top in scan.children.get(home, []):
        name = os.path.basename(top)
        if norm(top) in known:
            for sub in scan.children.get(top, []):
                sub_name = os.path.basename(sub)
                if scan.sizes.get(sub, 0) >= min_bytes and not sub_name.startswith("."):
                    out.append({"path": sub, "size": scan.sizes[sub], "kind": "folder"})
        elif scan.sizes.get(top, 0) >= min_bytes and not SKIP_IN_HOME.match(name):
            out.append({"path": top, "size": scan.sizes[top], "kind": "folder"})
    for size, path in scan.files_over:
        if size >= min_bytes and norm(os.path.dirname(path)) in known:
            out.append({"path": path, "size": size, "kind": "file"})
    return sorted(out, key=lambda c: -c["size"])


def move_tips(scan, home, min_bytes=None):
    """Advice for big things this script deliberately doesn't move."""
    min_bytes = options().min_gb * GB if min_bytes is None else min_bytes
    tips = []
    for k in KNOWN_FOLDERS:
        p = os.path.join(home, k)
        if scan.sizes.get(p, 0) >= 5 * GB:
            tips.append(f"{k} is {scan.sizes[p] / GB:.0f} GB. To move the whole folder: right-click it > "
                        "Properties > Location > Move.")
    for folder, size in scan.sizes.items():
        if size >= min_bytes and norm(folder).endswith("/steamapps") and norm(folder).startswith(norm(system_drive())):
            tips.append(f"Steam games on C: use {size / GB:.0f} GB. Move them with Steam > Settings > Storage, "
                        "not by hand.")
    for top in scan.children.get(home, []):
        if re.match(r"onedrive", os.path.basename(top), re.I) and scan.sizes.get(top, 0) >= min_bytes:
            tips.append(f"{os.path.basename(top)} uses {scan.sizes[top] / GB:.0f} GB on C:. Right-click folders "
                        "> 'Free up space' to keep them online-only instead of moving them.")
    return tips


def blocked(path, home, ctx=None):
    """Why a path may not be moved, or None if it is fine."""
    ctx = ctx or context.current()
    p, h = norm(os.path.abspath(path)), norm(home)
    if not os.path.exists(path):
        return "it doesn't exist"
    if volume.volume_id(path) != volume.volume_id(ctx.system_drive):
        return f"it isn't on {ctx.system_drive[:2]}"
    if p == h or p in {norm(os.path.join(home, k)) for k in KNOWN_FOLDERS}:
        return "it's a Windows user folder. Move it with right-click > Properties > Location instead"
    if not p.startswith(h + "/"):
        return "only folders and files inside your user profile can be moved (apps and system files break)"
    first = p[len(h) + 1:].split("/")[0]
    if SKIP_IN_HOME.match(first):
        return "it holds app data or synced files that break when moved by hand"
    return safety.why_refused(path, "move", ctx)


def dest_for(src, home, dest_root):
    rel = os.path.relpath(src, home)
    return os.path.join(dest_root, rel)


def _tree_stats(path):
    """(number of files, total bytes) under a path."""
    if os.path.isfile(path):
        return 1, os.path.getsize(path)
    n = b = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                b += os.path.getsize(os.path.join(dirpath, f))
                n += 1
            except OSError:
                pass
    return n, b


def parse_choice(text, n):
    """'1,3-5' -> [0, 2, 3, 4] (indexes), ignoring numbers out of range."""
    picked = []
    for part in re.split(r"[,\s]+", text.strip()):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        picked += [i - 1 for i in range(a, b + 1) if 1 <= i <= n and i - 1 not in picked]
    return picked


def plan_settings(opts):
    """What a plan was built under: the executor refuses a plan whose settings changed since."""
    return {"min_gb": opts.min_gb, "dest_folder_name": opts.dest_folder_name}


def build_plan(items, home, dest_root, ctx=None, opts=None):
    """The exact list of moves, with each item's fingerprint taken now. Items the destination's file
    system can't hold are left out, with a line saying why."""
    ctx = ctx or context.current()
    opts = opts or options(settings.load(ctx))
    plan = Plan(COMMAND, ctx, plan_settings(opts), info={"dest": dest_root})
    dest_volume = volume.volume_of(dest_root)
    fat32 = dest_volume is not None and dest_volume.fs.upper() == "FAT32"
    for item in items:
        src = item["path"]
        fp = fingerprint(src)
        if fp is None:
            print(f"Not moving {src}: it isn't there any more.")
            continue
        biggest = fp.get("size", fp.get("biggest", 0))
        if fat32 and biggest >= volume.FAT32_MAX_FILE:
            print(f"Not moving {src}: it has a file of 4 GB or more, and {dest_volume.name} uses FAT32, "
                  "which can't hold files that big.")
            continue
        plan.add_move(src, dest_for(src, home, dest_root))
    if volume.is_default_folder(dest_root, opts.dest_folder_name):
        # if C: is too full for the journal, it can be kept here, where recovery and undo look for it
        plan.journal_fallback = os.path.join(dest_root, context.VOLUME_JOURNAL_FOLDER)
    return plan


def run_plan(plan, ctx=None):
    """Do the moves after the yes. Returns the exit code."""
    ctx = ctx or context.current()
    drive = ctx.system_drive[:2]
    problem = volume.destination_problem(plan.info["dest"], ctx)
    if problem:      # checked again here, as every move is: moving onto C:'s own volume frees nothing
        for a in plan.actions:
            print(f"  moving {a['src']}  ->  {a['dst']} ...")
            print(f"    skipped: {problem}")
        print(f"\nMoved 0 of {len(plan.actions)} items.")
        return 1

    def started(a):
        print(f"  moving {a['src']}  ->  {a['dst']} ...")

    def ended(o):
        if o.status != "done":
            print(f"    skipped: {o.reason}")

    try:
        # read again: if settings.ini changed while you were answering, the executor refuses the plan
        result = executor.execute(plan, ctx, settings=plan_settings(options(settings.load(ctx))),
                                  on_action=started, on_outcome=ended)
    except lock.Busy:
        print(lock.BUSY_MESSAGE)
        return lock.BUSY_EXIT
    except executor.PlanRefused as e:
        print(e)
        return 1
    done = [o for o in result.done if o.action["op"] == "move"]
    print(f"\nMoved {len(done)} of {len(plan.actions)} items ({result.bytes_moved / GB:.1f} GB).")
    line = executor.free_space_line(result, drive)
    if line:
        print(line)
    if done:
        print("Undo with:  python move_off_c.py --undo")
    return 0 if len(done) == len(plan.actions) else 3


def undo(ctx=None):
    """Move the last batch back."""
    ctx = ctx or context.current()

    def started(a):
        if a["op"] == "move":
            print(f"  moving back {a['src']}  ->  {a['dst']} ...")

    try:
        out = undo_core.undo_last(ctx, COMMAND, legacy=[(ctx.legacy_move_logs, "moves_*.json", None)],
                                  ask=ui.ask, on_action=started)
    except lock.Busy:
        print(lock.BUSY_MESSAGE)
        return lock.BUSY_EXIT
    except executor.PlanRefused as e:
        print(e)
        return 1
    if out.source is None:
        print("Nothing to undo.")
        return 0
    print(f"Moved back {len(out.restored)} of {out.total} items.")
    undo_core.report_kept(out)
    return 3 if out.kept else 0


def main():
    ap = argparse.ArgumentParser(description="Move big personal folders and files from C: to another drive.")
    ap.add_argument("--path", action="append", help="move this folder or file (can be given more than once)")
    ap.add_argument("--to", help="destination folder (default: <other drive>\\From-C, or the folder named "
                                 "by [move] dest_folder_name in settings.ini)")
    ap.add_argument("--min-gb", type=float, help="smallest item to offer (default: [move] min_gb in settings.ini, 1)")
    ap.add_argument("--undo", action="store_true", help="move the last batch back")
    args = ap.parse_args()

    ctx = context.current()
    cfg = settings.load(ctx, create=True)
    for note in cfg.notes:
        print(note)
    problems = cfg.errors("move")
    if problems and not args.undo:      # undo reads nothing from [move], so it still works
        print(settings.refusal(cfg, problems, "move_off_c.py"))
        return settings.EXIT
    opts = options(cfg)
    min_gb = opts.min_gb if args.min_gb is None else args.min_gb
    try:      # finish what an interrupted run left, and ask about moves only you can decide
        recovery.at_start(ctx, "move_off_c.py", ask=ui.ask)
    except lock.Busy:
        print(lock.BUSY_MESSAGE)
        return lock.BUSY_EXIT
    if args.undo:
        return undo(ctx)

    home = str(Path.home())
    notes = []
    dest_root = args.to or default_dest(notes, opts)
    for note in notes:
        print(note)
    if not dest_root:
        print(f"No other drive found. Pass a destination with --to, e.g. --to D:\\{opts.dest_folder_name}")
        return 0
    if volume.is_network(dest_root):
        print(f"Can't move to {dest_root}: {volume.destination_problem(dest_root, ctx)}")
        return 1

    if args.path:
        items = []
        for p in args.path:
            reason = blocked(p, home, ctx)
            if reason:
                print(f"Not moving {p}: {reason}.")
            else:
                items.append({"path": os.path.abspath(p), "size": _tree_stats(p)[1], "kind": "path"})
        if not items:
            return 0
    else:
        print(f"Scanning {home} ...")
        scan = Scan([home], files_over_bytes=min_gb * GB)
        cands = move_candidates(scan, home, min_gb * GB)
        for tip in move_tips(scan, home, min_gb * GB):
            print(f"  Tip: {tip}")
        if not cands:
            print(f"\nNothing of {min_gb:g} GB or more that is safe to move.")
            return 0
        free = volume.free_bytes(dest_root)
        there = f" ({free / GB:.0f} GB free there)" if free is not None else ""
        print(f"\nCan move to {dest_root}{there}:")
        for i, c in enumerate(cands, 1):
            print(f"  {i:2})  {fmt(c['size'])}  {c['path']}")
        choice = ui.ask("\nType the numbers to move (e.g. 1,3 or 2-4), or press Enter to cancel: ")
        items = [cands[i] for i in parse_choice(choice or "", len(cands))]
        if not items:
            print("Nothing moved.")
            return 0

    plan = build_plan(items, home, dest_root, ctx, opts)
    if not plan.actions:
        return 0
    total = sum(a["bytes"] for a in plan.actions)
    print(f"\nThis moves {len(plan.actions)} item(s), {total / GB:.1f} GB, to {dest_root}:")
    for a in plan.actions:
        print(f"  {a['src']}  ->  {a['dst']}")
    plan.save_copy()
    if not ui.ask_yes("Type yes to continue: "):
        print("Cancelled, nothing moved.")
        return 0
    dest_volume = volume.volume_of(dest_root)
    if dest_volume is not None and dest_volume.usb:
        print(f"{dest_volume.name} is a USB drive. The moved files will only be there while it is plugged in.")
        if not ui.ask_yes("Type yes again to move them there: "):
            print("Cancelled, nothing moved.")
            return 0
    return run_plan(plan, ctx)


if __name__ == "__main__":
    sys.exit(main())
