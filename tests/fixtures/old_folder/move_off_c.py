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
"""

import argparse
import json
import os
import re
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path

from scanner import GB, Scan, fixed_drives, fmt, norm, system_drive

# ---------------- settings: edit these ----------------
MIN_GB = 2.0                 # smallest folder or file worth offering
DEST_FOLDER_NAME = "From-C"  # default destination is <other drive>:\From-C
LOG_DIR = Path.home() / ".pc-tidy" / "move_logs"
# -------------------------------------------------------

KNOWN_FOLDERS = ["Documents", "Pictures", "Videos", "Music", "Downloads", "Desktop"]
# folders in your profile that are never offered: app data, synced folders, Windows special folders
SKIP_IN_HOME = re.compile(
    r"^(appdata|\..*|onedrive.*|dropbox|google drive|icloud ?drive|(ana|mini)conda3?|saved games|contacts|"
    r"favorites|links|searches|3d objects|ntuser.*|sorted)$", re.I)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def other_drive():
    """The fixed drive other than C: with the most free space, or None."""
    sysd = norm(system_drive())
    others = [d for d in fixed_drives() if norm(d) != sysd and len(d) <= 3]
    if not others:
        return None
    return max(others, key=lambda d: shutil.disk_usage(d).free)


def default_dest():
    d = other_drive()
    return os.path.join(d, DEST_FOLDER_NAME) if d else None


def move_candidates(scan, home, min_bytes=MIN_GB * GB):
    """Personal folders and big files in your profile that are safe to move, biggest first."""
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


def move_tips(scan, home, min_bytes=MIN_GB * GB):
    """Advice for big things this script deliberately doesn't move."""
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


def blocked(path, home):
    """Why a path may not be moved, or None if it is fine."""
    p, h = norm(os.path.abspath(path)), norm(home)
    if not os.path.exists(path):
        return "it doesn't exist"
    if not p.startswith(norm(system_drive())):
        return "it isn't on C:"
    if p == h or p in {norm(os.path.join(home, k)) for k in KNOWN_FOLDERS}:
        return "it's a Windows user folder. Move it with right-click > Properties > Location instead"
    if not p.startswith(h + "/"):
        return "only folders and files inside your user profile can be moved (apps and system files break)"
    first = p[len(h) + 1:].split("/")[0]
    if SKIP_IN_HOME.match(first):
        return "it holds app data or synced files that break when moved by hand"
    return None


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


def _force_remove(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)   # git and some apps mark files read-only
    func(path)


def _rmtree(path):
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_force_remove)
    else:
        shutil.rmtree(path, onerror=_force_remove)


def _remove(path):
    if os.path.isdir(path):
        _rmtree(path)
    elif os.path.exists(path):
        os.chmod(path, stat.S_IWRITE)
        os.remove(path)


def _same_drive(a, b):
    return os.path.splitdrive(os.path.abspath(a))[0].lower() == os.path.splitdrive(os.path.abspath(b))[0].lower()


def safe_move(src, dst):
    """Copy src to dst on another drive, check the copy, then delete src.
    If anything fails before the check passes, the partial copy is removed and src is untouched."""
    if _same_drive(src, dst):
        raise ValueError("destination is on the same drive, that frees no space")
    if os.path.exists(dst):
        raise FileExistsError(f"{dst} already exists")
    before = _tree_stats(src)
    free = shutil.disk_usage(os.path.splitdrive(os.path.abspath(dst))[0] + os.sep).free
    if free < before[1] * 1.02 + 200 * 1024 ** 2:
        raise OSError(f"not enough free space on the destination drive ({free / GB:.1f} GB free)")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)
        after = _tree_stats(dst)
        if after != before:
            raise OSError(f"copy incomplete ({after[0]} of {before[0]} files)")
    except BaseException:
        _remove(dst)
        raise
    try:
        _remove(src)
    except OSError as e:
        print(f"    Copied fine, but some of the original couldn't be deleted ({e}). "
              f"The full copy is at {dst}; delete what's left of {src} yourself.")
    return before[1]


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


def run_moves(items, home, dest_root):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"moves_{datetime.now():%Y%m%d_%H%M%S}.json"
    done, freed = [], 0
    for item in items:
        src = item["path"]
        dst = dest_for(src, home, dest_root)
        print(f"  moving {src}  ->  {dst} ...")
        try:
            freed += safe_move(src, dst)
        except Exception as e:
            print(f"    skipped: {e}")
            continue
        done.append({"from": src, "to": dst, "bytes": item["size"]})
        log_path.write_text(json.dumps(done, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nMoved {len(done)} of {len(items)} items, freed about {freed / GB:.1f} GB on C:.")
    if done:
        print("Undo with:  python move_off_c.py --undo")


def undo():
    logs = sorted(LOG_DIR.glob("moves_*.json")) if LOG_DIR.exists() else []
    if not logs:
        print("Nothing to undo.")
        return
    last = logs[-1]
    entries = json.loads(last.read_text(encoding="utf-8"))
    back = 0
    for e in reversed(entries):
        print(f"  moving back {e['to']}  ->  {e['from']} ...")
        try:
            safe_move(e["to"], e["from"])
            back += 1
        except Exception as ex:
            print(f"    skipped: {ex}")
    last.rename(last.with_suffix(".undone"))
    print(f"Moved back {back} of {len(entries)} items.")


def main():
    ap = argparse.ArgumentParser(description="Move big personal folders and files from C: to another drive.")
    ap.add_argument("--path", action="append", help="move this folder or file (can be given more than once)")
    ap.add_argument("--to", help=f"destination folder (default: <other drive>\\{DEST_FOLDER_NAME})")
    ap.add_argument("--min-gb", type=float, default=MIN_GB, help="smallest item to offer")
    ap.add_argument("--undo", action="store_true", help="move the last batch back")
    args = ap.parse_args()

    if args.undo:
        undo()
        return

    home = str(Path.home())
    dest_root = args.to or default_dest()
    if not dest_root:
        print("No other drive found. Pass a destination with --to, e.g. --to D:\\From-C")
        return

    if args.path:
        items = []
        for p in args.path:
            reason = blocked(p, home)
            if reason:
                print(f"Not moving {p}: {reason}.")
            else:
                items.append({"path": os.path.abspath(p), "size": _tree_stats(p)[1], "kind": "path"})
        if not items:
            return
    else:
        print(f"Scanning {home} ...")
        scan = Scan([home], files_over_bytes=args.min_gb * GB)
        cands = move_candidates(scan, home, args.min_gb * GB)
        for tip in move_tips(scan, home, args.min_gb * GB):
            print(f"  Tip: {tip}")
        if not cands:
            print(f"\nNothing of {args.min_gb:g} GB or more that is safe to move.")
            return
        free = shutil.disk_usage(os.path.splitdrive(os.path.abspath(dest_root))[0] + os.sep).free
        print(f"\nCan move to {dest_root} ({free / GB:.0f} GB free there):")
        for i, c in enumerate(cands, 1):
            print(f"  {i:2})  {fmt(c['size'])}  {c['path']}")
        choice = input("\nType the numbers to move (e.g. 1,3 or 2-4), or press Enter to cancel: ")
        items = [cands[i] for i in parse_choice(choice, len(cands))]
        if not items:
            print("Nothing moved.")
            return

    total = sum(i["size"] for i in items)
    print(f"\nThis moves {len(items)} item(s), {total / GB:.1f} GB, to {dest_root}:")
    for i in items:
        print(f"  {i['path']}  ->  {dest_for(i['path'], home, dest_root)}")
    if input("Type yes to continue: ").strip().lower() != "yes":
        print("Cancelled, nothing moved.")
        return
    run_moves(items, home, dest_root)


if __name__ == "__main__":
    main()
