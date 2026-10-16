r"""
disk_report.py - find out what is filling up your drives. Read-only: it never deletes anything.

Usage:
    python disk_report.py                        # scan every drive, save disk_report.pdf and open it
    python disk_report.py --path C:\             # scan only C: (repeat --path for more)
    python disk_report.py --out report.txt       # plain text instead of PDF (repeat --out for both)
    python disk_report.py --no-llm --no-open     # skip the LLM, don't open the PDF

Run PowerShell as administrator to also see inside protected folders.
A full scan takes a few minutes per drive. To act on the report, see cleanup.ps1 and move_off_c.py.
"""

import argparse
import os
import platform
import re
import sys
from datetime import datetime

import classify
import llm
import move_off_c
from scanner import GB, Scan, clean_root, fixed_drives, fmt, norm, system_drive

# ---------------- settings: edit these ----------------
MIN_GB = 1.0            # folders smaller than this are hidden from the tree
MAX_DEPTH = 8           # how many levels deep the tree goes
TOP_FILES = 20          # how many of the biggest files to list
CLEANUP_MIN_GB = 0.1    # smallest cleanable item worth listing
LLM_MAX_FOLDERS = 15    # at most this many unknown folders are described by the LLM
LLM_MAX_APPS = 60       # at most this many unrecognized apps are classified by the LLM
# -------------------------------------------------------

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def build_tree(root, scan):
    """Folders above MIN_GB as rows (size, depth, name, label).
    Also returns the unknown leaf folders (for the LLM) and the 'info' items shown."""
    rows, leaves, info = [], [], []

    def show(folder, depth, under_known):
        rule = classify.known(folder)
        name = folder if depth == 0 else os.path.basename(folder)
        rows.append((scan.sizes[folder], depth, name, rule.what if rule else ""))
        if rule and rule.kind == "info":
            info.append((scan.sizes[folder], folder, rule))
        big = sorted((c for c in scan.children.get(folder, []) if scan.sizes.get(c, 0) >= MIN_GB * GB),
                     key=lambda c: -scan.sizes[c])
        expand = depth < MAX_DEPTH and (rule is None or rule.expand)
        if expand:
            for c in big:
                show(c, depth + 1, under_known or rule is not None)
        if (not big or not expand) and not under_known and rule is None and depth >= 2 \
                and not re.search(r"^[a-z]:/users/[^/]+$", norm(folder)):
            leaves.append(folder)

    show(root, 0, False)
    return rows, leaves, info


def cleanup_items(scan):
    """Every 'clean' item above CLEANUP_MIN_GB. Outermost match only (no node_modules inside node_modules)."""
    found, kept = [], []
    candidates = [(p, s) for p, s in scan.sizes.items() if s >= CLEANUP_MIN_GB * GB]
    candidates += [(p, s) for s, p in scan.big_files if s >= CLEANUP_MIN_GB * GB]
    for path, size in sorted(candidates, key=lambda x: len(x[0])):
        rule = classify.known(path)
        if not rule or rule.kind != "clean":
            continue
        n = norm(path)
        if any(n.startswith(norm(other) + "/") for other in kept):
            continue
        kept.append(path)
        found.append({"size": size, "path": path, "what": rule.what, "todo": rule.todo})
    return sorted(found, key=lambda x: -x["size"])


def llm_describe(folder, scan):
    """What the LLM thinks a folder is. Only a description: a small model is not trusted to judge deleting."""
    subs = sorted(scan.children.get(folder, []), key=lambda c: -scan.sizes.get(c, 0))[:5]
    sub_text = ", ".join(f"{os.path.basename(c)} ({scan.sizes[c] / GB:.1f} GB)" for c in subs) or "(no subfolders)"
    schema = {"type": "object", "properties": {"what": {"type": "string"}}, "required": ["what"]}
    prompt = (
        "A Windows user wants to know what is using their disk space.\n"
        f"Folder: {folder}\n"
        f"Size: {scan.sizes[folder] / GB:.1f} GB\n"
        f"Largest subfolders: {sub_text}\n\n"
        "In at most 10 words, say what this folder most likely is: which app it belongs to "
        "and what it holds. If you don't recognize it, answer 'unknown'."
    )
    return llm.ask_json(prompt, schema).get("what", "?")


def llm_app_types(apps):
    """Ask the LLM to sort apps no rule recognized into APP_TYPES. Returns {name: type}."""
    out = {}
    apps = apps[:LLM_MAX_APPS]
    for start in range(0, len(apps), 20):
        chunk = apps[start:start + 20]
        listing = "\n".join(f"{i + 1}. {a['name']} (publisher: {a['publisher'] or 'unknown'})"
                            for i, a in enumerate(chunk))
        schema = {"type": "object",
                  "properties": {"types": {"type": "array",
                                           "items": {"type": "string", "enum": classify.APP_TYPES}}},
                  "required": ["types"]}
        prompt = (
            "Sort these Windows apps into types. Allowed types: " + ", ".join(classify.APP_TYPES) + ".\n"
            "Answer with one type per app, in the same order. Use Other if you don't know the app.\n\n"
            + listing
        )
        sys.stderr.write(f"\r  asking LLM about apps {start + 1}-{start + len(chunk)} ...{' ' * 20}")
        try:
            types = llm.ask_json(prompt, schema).get("types", [])
        except Exception:
            continue
        for a, t in zip(chunk, types):
            out[a["name"]] = t
    sys.stderr.write("\r" + " " * 70 + "\r")
    return out


def build_report(roots, use_llm=True):
    print(f"Scanning {', '.join(roots)} (this can take a few minutes per drive) ...")
    scan = Scan(roots, top_files=TOP_FILES, files_over_bytes=move_off_c.MIN_GB * GB)
    data = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "computer": platform.node(),
        "stats": scan.stats,
        "drives": [{"root": r, "used": u.used, "total": u.total, "free": u.free} for r, u in scan.usage.items()],
    }

    cat_totals, cat_of = classify.folder_categories(scan)
    data["categories"] = sorted(((c, b) for c, b in cat_totals.items() if b > 0), key=lambda x: -x[1])

    data["trees"], leaves, info = [], [], []
    for root in roots:
        rows, root_leaves, root_info = build_tree(root, scan)
        data["trees"].append((root, rows))
        leaves += root_leaves
        info += root_info

    data["big_files"] = []
    for size, path in scan.big_files:
        rule = classify.known(path)
        data["big_files"].append({"size": size, "path": path, "what": rule.what if rule else ""})
        if rule and rule.kind == "info":
            info.append((size, path, rule))

    data["cleanup"] = cleanup_items(scan)
    kept, data["info"] = [], []
    for size, path, rule in sorted(info, key=lambda x: len(x[1])):   # outermost first
        n = norm(path)
        if any(n == k or (n.startswith(k + "/") and what == rule.what) for k, what in kept):
            continue   # same thing listed already, e.g. a file inside the Claude app folder
        kept.append((n, rule.what))
        data["info"].append({"size": size, "path": path, "what": rule.what, "todo": rule.todo})
    data["info"].sort(key=lambda x: -x["size"])

    # moving personal files off C:
    sysd = norm(system_drive())
    home = next((k for k in scan.sizes if norm(k) == norm(os.path.expanduser("~"))), None)   # on-disk spelling
    data["move"] = None
    if any(norm(r) == sysd for r in roots) and home:
        dest = move_off_c.default_dest()
        data["move"] = {
            "dest": dest,
            "candidates": move_off_c.move_candidates(scan, home) if dest else [],
            "tips": move_off_c.move_tips(scan, home),
        }

    llm_ok = use_llm and llm.available()
    apps = classify.installed_apps()
    data["apps"] = classify.classify_apps(apps, ask_llm=llm_app_types if llm_ok else None)

    data["llm_folders"] = []
    leaves = [f for f in leaves if cat_of.get(f) != classify.PERSONAL]   # you know what your own folders are
    if leaves and llm_ok:
        for folder in sorted(leaves, key=lambda f: -scan.sizes[f])[:LLM_MAX_FOLDERS]:
            sys.stderr.write(f"\r  asking LLM about {os.path.basename(folder)[:40]} ...{' ' * 20}")
            try:
                what = llm_describe(folder, scan)
            except Exception as e:
                what = f"(LLM failed: {e})"
            data["llm_folders"].append({"size": scan.sizes[folder], "path": folder, "what": what})
        sys.stderr.write("\r" + " " * 70 + "\r")
    data["llm_used"] = llm_ok
    return data


def render_text(data):
    out = [f"DISK REPORT  {data['generated']}  {data['computer']}"]
    s = data["stats"]
    out.append(f"Scanned {s['files']:,} files ({s['bytes'] / GB:.1f} GB) in {s['seconds']:.0f} s")

    out.append("\nDRIVES")
    for d in data["drives"]:
        out.append(f"  {d['root']:<4} {d['used'] / GB:6.0f} GB used of {d['total'] / GB:.0f} GB, "
                   f"{d['free'] / GB:.0f} GB free")

    out.append("\nWHERE THE SPACE GOES")
    for cat, b in data["categories"]:
        out.append(f"{fmt(b)}  {cat}")

    out.append("\nTHINGS YOU CAN CLEAN UP")
    for c in data["cleanup"]:
        out.append(f"{fmt(c['size'])}  {c['path']}\n             {c['what']}. {c['todo']}")
    if not data["cleanup"]:
        out.append("  (none found)")

    m = data["move"]
    if m is not None:
        out.append("\nCOULD MOVE TO ANOTHER DRIVE (python move_off_c.py)")
        if not m["dest"]:
            out.append("  No other drive found.")
        for c in m["candidates"]:
            out.append(f"{fmt(c['size'])}  {c['path']}")
        if m["dest"] and not m["candidates"]:
            out.append("  Nothing big in your profile that is safe to move.")
        for tip in m["tips"]:
            out.append(f"  Tip: {tip}")

    if data["apps"]:
        out.append("\nINSTALLED APPS BY TYPE (sizes as reported by the installers, often missing)")
        for app_t in classify.APP_TYPES:
            group = [a for a in data["apps"] if a["type"] == app_t]
            if not group:
                continue
            total = sum(a["size"] for a in group)
            size_text = f"{total / GB:.1f} GB" if total else "sizes unknown"
            out.append(f"  {app_t} ({len(group)} app{'s' if len(group) != 1 else ''}, {size_text})")
            for a in group:
                size = fmt(a["size"]) if a["size"] else "      ?   "
                out.append(f"  {size}  {a['name']}" + ("  [LLM guess]" if a.get("llm") else ""))

    for root, rows in data["trees"]:
        out.append(f"\nBIGGEST FOLDERS ON {root} (over {MIN_GB:g} GB)")
        for size, depth, name, label in rows:
            out.append(f"{fmt(size)}  {'  ' * depth}{name}" + (f"   <- {label}" if label else ""))

    out.append("\nBIGGEST FILES")
    for f in data["big_files"]:
        out.append(f"{fmt(f['size'])}  {f['path']}" + (f"   <- {f['what']}" if f["what"] else ""))

    out.append("\nGOOD TO KNOW (usually leave these alone)")
    for i in data["info"]:
        out.append(f"{fmt(i['size'])}  {i['path']}\n             {i['what']}. {i['todo']}")

    if data["llm_folders"]:
        out.append("\nWHAT THE LLM THINKS THESE UNKNOWN FOLDERS ARE")
        out.append("  (a small model that can be wrong. This is a description, not advice to delete.)")
        for f in data["llm_folders"]:
            out.append(f"{fmt(f['size'])}  {f['path']}\n             {f['what']}")

    if data["stats"]["denied"]:
        out.append(f"\n{data['stats']['denied']} folders could not be opened. Running PowerShell as administrator "
                   "includes most of them (a few system folders stay locked).")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Show what is using disk space. Never deletes anything.")
    ap.add_argument("--path", action="append", help="drive or folder to scan (default: every fixed drive)")
    ap.add_argument("--out", action="append",
                    help="report file, .pdf or .txt (default: disk_report.pdf). Repeat for both")
    ap.add_argument("--no-llm", action="store_true", help="skip the LLM parts")
    ap.add_argument("--no-open", action="store_true", help="don't open the PDF when done")
    args = ap.parse_args()

    roots = [clean_root(p) for p in (args.path or fixed_drives())]
    data = build_report(roots, use_llm=not args.no_llm)
    text = render_text(data)
    print("\n" + text)

    for out in args.out or ["disk_report.pdf"]:
        if out.lower().endswith(".pdf"):
            import pdf_report
            pdf_report.render(data, out)
            print(f"\nPDF report saved to {os.path.abspath(out)}")
            if not args.no_open and hasattr(os, "startfile"):
                os.startfile(os.path.abspath(out))
        else:
            with open(out, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            print(f"\nText report saved to {os.path.abspath(out)}")


if __name__ == "__main__":
    main()
