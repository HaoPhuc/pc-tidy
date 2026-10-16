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
    The model is set in llm.py.
"""

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import llm

# ---------------- settings: edit these ----------------
SOURCE = Path.home() / "Downloads"
DEST = Path.home() / "Sorted"

ARCHIVE_AFTER_DAYS = 60     # files older than this go under <dest>/Archive/<folder>. None to disable
SKIP_NEWER_THAN_MIN = 10    # leave very recent files alone (they may still be downloading)
RENAME_WITH_LLM = True      # let the LLM give LLM-sorted files a clearer name

# extension -> folder. Anything not listed here is sent to the LLM.
RULES = {
    # installers (cleanup.ps1 deletes them, setting: Installers)
    ".exe": "To-Delete", ".msi": "To-Delete", ".msix": "To-Delete",
    # images
    ".jpg": "Pictures", ".jpeg": "Pictures", ".png": "Pictures", ".gif": "Pictures",
    ".webp": "Pictures", ".heic": "Pictures", ".bmp": "Pictures", ".svg": "Pictures",
    # video and audio
    ".mp4": "Videos", ".mov": "Videos", ".mkv": "Videos", ".avi": "Videos",
    ".mp3": "Music", ".wav": "Music", ".flac": "Music", ".m4a": "Music",
    # compressed
    ".zip": "Archives", ".rar": "Archives", ".7z": "Archives", ".tar": "Archives", ".gz": "Archives",
    # code and data
    ".py": "Code", ".ipynb": "Code", ".m": "Code", ".c": "Code", ".cpp": "Code", ".h": "Code", ".js": "Code",
    ".csv": "Data", ".json": "Data", ".mat": "Data", ".xlsx": "Data", ".xls": "Data",
    # slides and 3D
    ".pptx": "Slides", ".ppt": "Slides",
    ".stl": "3D models", ".step": "3D models", ".stp": "3D models", ".blend": "3D models",
}

# folders the LLM chooses from (PDFs, Word docs, text files, unknown types, and whole folders)
CATEGORIES = [
    "Coursework",
    "Research papers",
    "Projects and code",
    "Admin and receipts",
    "Work",
    "Personal",
    "Software",
    "Other",
]

SKIP_SUFFIXES = {".crdownload", ".part", ".partial", ".download", ".tmp",
                 ".lnk", ".url"}     # shortcuts stay where they are (Desktop)
SKIP_NAMES = {"desktop.ini", "thumbs.db"}
# -------------------------------------------------------

logging.getLogger("pypdf").setLevel(logging.ERROR)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

# folders this script creates; never sorted again when sorting in place
OUR_FOLDERS = {name.lower() for name in list(RULES.values()) + CATEGORIES + ["Archive", "_logs"]}


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


def ask_llm(path, snippet):
    schema = {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": CATEGORIES},
            "new_name": {"type": "string"},
        },
        "required": ["category", "new_name"],
    }
    prompt = (
        "You sort files from a messy folder.\n"
        f"Pick the best category from: {', '.join(CATEGORIES)}.\n"
        "Also suggest a short, clear filename without extension (words joined by underscores, "
        "max 60 characters). Keep the original name if it is already clear.\n\n"
        f"Filename: {path.name}\n"
        f"Content preview: {snippet or '(none)'}\n"
    )
    answer = llm.ask_json(prompt, schema)
    category = answer.get("category")
    return (category if category in CATEGORIES else "Other"), answer.get("new_name", "")


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


def ask_llm_folder(folder):
    names, count, size = folder_sample(folder)
    schema = {"type": "object", "properties": {"category": {"type": "string", "enum": CATEGORIES}},
              "required": ["category"]}
    prompt = (
        "You sort folders from a messy folder into categories.\n"
        f"Pick the best category from: {', '.join(CATEGORIES)}.\n\n"
        f"Folder name: {folder.name}\n"
        f"It has {count} files, {size / 1024 ** 2:.0f} MB. Some of them:\n"
        + "\n".join(f"- {n}" for n in names) + "\n"
    )
    category = llm.ask_json(prompt, schema).get("category")
    return category if category in CATEGORIES else "Other"


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


def plan_moves(source, dest, use_llm, folders=False):
    now = time.time()
    moves, taken = [], set()
    in_place = source.resolve() == dest.resolve()
    entries = sorted(source.iterdir(), key=lambda p: p.name.lower())

    for f in (p for p in entries if p.is_file()):
        ext = f.suffix.lower()
        if ext in SKIP_SUFFIXES or f.name.lower() in SKIP_NAMES or f.name.startswith("~$") or is_hidden(f):
            continue
        age_days = (now - f.stat().st_mtime) / 86400
        if age_days * 1440 < SKIP_NEWER_THAN_MIN:
            continue

        new_name = f.name
        if ext in RULES:
            folder = RULES[ext]
        elif use_llm:
            print(f"  asking LLM about {f.name} ...")
            try:
                folder, suggestion = ask_llm(f, read_snippet(f))
            except Exception as e:
                print(f"    LLM failed ({e}), leaving it in place")
                continue
            if RENAME_WITH_LLM and suggestion:
                new_name = clean_name(suggestion, f)
        else:
            continue  # no LLM: leave it for a later run

        base = dest
        if folder != "To-Delete" and ARCHIVE_AFTER_DAYS and age_days > ARCHIVE_AFTER_DAYS:
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
            if in_place and d.name.lower() in OUR_FOLDERS:
                continue   # a folder this script made on an earlier run
            print(f"  asking LLM about folder {d.name} ...")
            try:
                category = ask_llm_folder(d)
            except Exception as e:
                print(f"    LLM failed ({e}), leaving it in place")
                continue
            target = unique(dest / category / d.name, taken)
            taken.add(target)
            moves.append((d, target))
    return moves


def apply_moves(moves, dest):
    log_dir = dest / "_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"run_{datetime.now():%Y%m%d_%H%M%S}.json"
    done = []
    try:
        for src, dst in moves:
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(src), str(dst))
                done.append({"from": str(src), "to": str(dst)})
            except Exception as e:
                print(f"  could not move {src.name}: {e}")
    finally:
        if done:
            log_path.write_text(json.dumps(done, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nMoved {len(done)} items. Undo with the same command plus --undo instead of --apply")


def undo(dest):
    logs = sorted((dest / "_logs").glob("run_*.json"))
    if not logs:
        print("Nothing to undo.")
        return
    last = logs[-1]
    entries = json.loads(last.read_text(encoding="utf-8"))
    restored = 0
    for e in reversed(entries):
        now_at, original = Path(e["to"]), Path(e["from"])
        if now_at.exists():
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(now_at), str(unique(original, set())))
            restored += 1
            parent = now_at.parent        # remove category folders that are now empty
            while parent != dest and parent.is_relative_to(dest):
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
    last.rename(last.with_suffix(".undone"))
    print(f"Put back {restored} items from {last.name}.")


def main():
    ap = argparse.ArgumentParser(description="Sort a messy folder into subfolders.")
    ap.add_argument("--apply", action="store_true", help="actually move files")
    ap.add_argument("--undo", action="store_true", help="revert the last --apply run")
    ap.add_argument("--no-llm", action="store_true", help="rules only, skip the LLM")
    ap.add_argument("--source", type=Path, default=SOURCE, help="folder to sort (default: Downloads)")
    ap.add_argument("--dest", type=Path, default=DEST, help="where sorted folders go (default: ~/Sorted)")
    ap.add_argument("--in-place", action="store_true", help="put the sorted folders inside the source folder")
    ap.add_argument("--folders", action="store_true", help="also sort whole subfolders (needs the LLM)")
    args = ap.parse_args()

    source = Path(os.path.expandvars(str(args.source))).expanduser()
    dest = source if args.in_place else Path(os.path.expandvars(str(args.dest))).expanduser()

    if args.undo:
        undo(dest)
        return
    if not source.is_dir():
        print(f"{source} is not a folder.")
        return

    use_llm = not args.no_llm and llm.available()
    if not args.no_llm and not use_llm:
        print("Files that need the LLM will be left in place.\n")
    moves = plan_moves(source, dest, use_llm, folders=args.folders)
    if not moves:
        print("Nothing to sort.")
        return

    print()
    for src, dst in moves:
        kind = "[folder] " if src.is_dir() else ""
        print(f"{kind}{src.name}  ->  {dst.relative_to(dest)}")

    if args.apply:
        apply_moves(moves, dest)
    else:
        print(f"\nPreview only, {len(moves)} items. Run again with --apply to move them into {dest}.")


if __name__ == "__main__":
    main()
