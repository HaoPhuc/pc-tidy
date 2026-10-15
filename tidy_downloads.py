"""
tidy_downloads.py - sort a messy Downloads folder, using a local LLM for the hard cases.

Usage:
    python tidy_downloads.py            # preview only: shows what would move, changes nothing
    python tidy_downloads.py --apply    # actually move the files
    python tidy_downloads.py --undo     # put back everything from the last --apply run
    python tidy_downloads.py --no-llm   # rules only, skip the LLM

Setup (once):
    1. Install Ollama from https://ollama.com, then in a terminal:  ollama pull llama3.2:3b
    2. pip install -r requirements.txt
    The model is set in llm.py.
"""

import argparse
import json
import logging
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

ARCHIVE_AFTER_DAYS = 60     # files older than this go under Sorted/Archive/<folder>. None to disable
SKIP_NEWER_THAN_MIN = 10    # leave very recent files alone (they may still be downloading)
RENAME_WITH_LLM = True      # let the LLM give LLM-sorted files a clearer name

# extension -> folder. Anything not listed here is sent to the LLM.
RULES = {
    # installers (review this folder, then delete)
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

# folders the LLM chooses from (PDFs, Word docs, text files and unknown types)
CATEGORIES = [
    "Coursework",
    "Research papers",
    "Admin and receipts",
    "Work",
    "Personal",
    "Software",
    "Other",
]

SKIP_SUFFIXES = {".crdownload", ".part", ".partial", ".download", ".tmp"}
SKIP_NAMES = {"desktop.ini", "thumbs.db"}
# -------------------------------------------------------

logging.getLogger("pypdf").setLevel(logging.ERROR)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


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
        "You sort files from a Downloads folder.\n"
        f"Pick the best category from: {', '.join(CATEGORIES)}.\n"
        "Also suggest a short, clear filename without extension (words joined by underscores, "
        "max 60 characters). Keep the original name if it is already clear.\n\n"
        f"Filename: {path.name}\n"
        f"Content preview: {snippet or '(none)'}\n"
    )
    answer = llm.ask_json(prompt, schema)
    category = answer.get("category")
    return (category if category in CATEGORIES else "Other"), answer.get("new_name", "")


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


def plan_moves(source, dest, use_llm):
    now = time.time()
    moves, taken = [], set()
    for f in sorted(p for p in source.iterdir() if p.is_file()):
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
    print(f"\nMoved {len(done)} files. Undo with:  python tidy_downloads.py --undo")


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
    last.rename(last.with_suffix(".undone"))
    print(f"Put back {restored} files from {last.name}.")


def main():
    ap = argparse.ArgumentParser(description="Tidy the Downloads folder.")
    ap.add_argument("--apply", action="store_true", help="actually move files")
    ap.add_argument("--undo", action="store_true", help="revert the last --apply run")
    ap.add_argument("--no-llm", action="store_true", help="rules only, skip the LLM")
    ap.add_argument("--source", type=Path, default=SOURCE)
    ap.add_argument("--dest", type=Path, default=DEST)
    args = ap.parse_args()

    if args.undo:
        undo(args.dest)
        return

    use_llm = not args.no_llm and llm.available()
    if not args.no_llm and not use_llm:
        print("Files that need the LLM will be left in place.\n")
    moves = plan_moves(args.source, args.dest, use_llm)
    if not moves:
        print("Nothing to sort.")
        return

    print()
    for src, dst in moves:
        print(f"{src.name}  ->  {dst.relative_to(args.dest)}")

    if args.apply:
        apply_moves(moves, args.dest)
    else:
        print(f"\nPreview only, {len(moves)} files. Run with --apply to move them.")


if __name__ == "__main__":
    main()
