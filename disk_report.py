r"""
disk_report.py - find out what is filling up your drives. Read-only: it never deletes anything.

Usage:
    python disk_report.py                        # scan every drive, save disk_report.pdf and open it
    python disk_report.py --path C:\             # scan only C: (repeat --path for more)
    python disk_report.py --out report.txt       # plain text instead of PDF (repeat --out for both)
    python disk_report.py --no-llm --no-open     # skip the LLM, don't open the PDF

Run PowerShell as administrator to also see inside protected folders.
A full scan takes a few minutes per drive. To act on the report, see cleanup.ps1 and move_off_c.py.

Settings are in settings.ini (python pc_tidy.py settings edit): [report] for the folder tree and the
lists, [ai] for the local AI, and your own [label.*], [category.*] and [apptype.*] rules. The report
only reads, so a mistake never stops it: a broken value uses its default and a broken rule is
skipped, and the report says so at the top.
"""

import argparse
import os
import platform
import re
import sys
from datetime import datetime
from types import SimpleNamespace

import classify
import llm
import move_off_c
from pctidy import context, fixes, journal, settings
from scanner import GB, Scan, clean_root, fixed_drives, fmt, norm, system_drive

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def options(cfg=None):
    """What the report reads: [report], [ai] and [move] (the "could move" section) of settings.ini, and
    [cleanup] for the fixes (which folders hold installers, which switches are on)."""
    cfg = cfg or settings.load()
    return SimpleNamespace(report=cfg.section("report"), ai=cfg.section("ai"), move=cfg.section("move"), cfg=cfg)


def build_tree(root, scan, opts=None):
    """Folders above [report] min_gb as rows (size, depth, name, label).
    Also returns the unknown leaf folders (for the LLM) and the 'info' items shown."""
    r = (opts or options()).report
    rows, leaves, info = [], [], []

    def show(folder, depth, under_known):
        rule = classify.known(folder)
        name = folder if depth == 0 else os.path.basename(folder)
        rows.append((scan.sizes[folder], depth, name, rule.what if rule else ""))
        if rule and rule.kind == "info":
            info.append((scan.sizes[folder], folder, rule))
        big = sorted((c for c in scan.children.get(folder, []) if scan.sizes.get(c, 0) >= r.min_gb * GB),
                     key=lambda c: -scan.sizes[c])
        expand = depth < r.max_depth and (rule is None or rule.expand)
        if expand:
            for c in big:
                show(c, depth + 1, under_known or rule is not None)
        if (not big or not expand) and not under_known and rule is None and depth >= 2 \
                and not re.search(r"^[a-z]:/users/[^/]+$", norm(folder)):
            leaves.append(folder)

    show(root, 0, False)
    return rows, leaves, info


def cleanup_items(scan, min_gb=None):
    """Every 'clean' item above [report] cleanup_min_gb. Outermost match only (no node_modules inside
    node_modules)."""
    min_gb = options().report.cleanup_min_gb if min_gb is None else min_gb
    found, kept = [], []
    candidates = [(p, s) for p, s in scan.sizes.items() if s >= min_gb * GB]
    candidates += [(p, s) for s, p in scan.big_files if s >= min_gb * GB]
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


def llm_app_types(apps, max_apps=None):
    """Ask the LLM to sort apps no rule recognized into APP_TYPES. Returns {name: type}."""
    out = {}
    apps = apps[:options().ai.max_apps if max_apps is None else max_apps]
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


def build_report(roots, use_llm=True, opts=None):
    opts = opts or options()
    print(f"Scanning {', '.join(roots)} (this can take a few minutes per drive) ...")
    scan = Scan(roots, top_files=opts.report.top_files, files_over_bytes=opts.move.min_gb * GB)
    data = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "computer": platform.node(),
        "settings_problems": list(getattr(opts, "problems", [])),
        "min_gb": opts.report.min_gb,
        "stats": scan.stats,
        "drives": [{"root": r, "used": u.used, "total": u.total, "free": u.free} for r, u in scan.usage.items()],
    }

    cat_totals, cat_of = classify.folder_categories(scan)
    data["categories"] = sorted(((c, b) for c, b in cat_totals.items() if b > 0), key=lambda x: -x[1])

    data["trees"], leaves, info = [], [], []
    for root in roots:
        rows, root_leaves, root_info = build_tree(root, scan, opts)
        data["trees"].append((root, rows))
        leaves += root_leaves
        info += root_info

    data["big_files"] = []
    for size, path in scan.big_files:
        rule = classify.known(path)
        data["big_files"].append({"size": size, "path": path, "what": rule.what if rule else ""})
        if rule and rule.kind == "info":
            info.append((size, path, rule))

    data["cleanup"] = cleanup_items(scan, opts.report.cleanup_min_gb)
    kept, data["info"] = [], []
    for size, path, rule in sorted(info, key=lambda x: len(x[1])):   # outermost first
        n = norm(path)
        if any(n == k or (n.startswith(k + "/") and what == rule.what) for k, what in kept):
            continue   # same thing listed already, e.g. a file inside the Claude app folder
        kept.append((n, rule.what))
        data["info"].append({"size": size, "path": path, "what": rule.what, "todo": rule.todo})
    data["info"].sort(key=lambda x: -x["size"])

    # moving personal files off C: (scanned from C:\ or from inside the profile)
    ctx = context.current()
    home = next((k for k in scan.sizes if norm(k) == norm(ctx.profile)), None)   # on-disk spelling
    data["move"] = None
    if home and any(fixes.on_system_drive(r, ctx) for r in roots):
        dest = move_off_c.default_dest(opts=opts.move)
        min_bytes = opts.move.min_gb * GB
        data["move"] = {
            "dest": dest,
            "candidates": move_off_c.move_candidates(scan, home, min_bytes) if dest else [],
            "tips": move_off_c.move_tips(scan, home, min_bytes),
        }

    # the ranked fixes for C:, measured file by file; they lead the report
    data["fixes"] = fixes.build(scan, getattr(opts, "cfg", None) or settings.load(), ctx, move=data["move"])
    data["notices"] = [n.get("message", "") for n in journal.notices(ctx)]

    llm_ok = use_llm and llm.available()
    apps = classify.installed_apps()
    data["apps"] = classify.classify_apps(apps, ask_llm=(lambda unknown: llm_app_types(unknown, opts.ai.max_apps))
                                          if llm_ok else None)

    data["llm_folders"] = []
    leaves = [f for f in leaves if cat_of.get(f) != classify.PERSONAL]   # you know what your own folders are
    if leaves and llm_ok:
        for folder in sorted(leaves, key=lambda f: -scan.sizes[f])[:opts.ai.max_folders]:
            sys.stderr.write(f"\r  asking LLM about {os.path.basename(folder)[:40]} ...{' ' * 20}")
            try:
                what = llm_describe(folder, scan)
            except Exception as e:
                what = f"(LLM failed: {e})"
            data["llm_folders"].append({"size": scan.sizes[folder], "path": folder, "what": what})
        sys.stderr.write("\r" + " " * 70 + "\r")
    data["llm_used"] = llm_ok
    return data


def render_fixes(f):
    """The ranked fixes for C:, as lines of text."""
    out = [f"\nFIXES FOR {f['drive']}, LOWEST RISK FIRST"]
    if f.get("size"):
        out.append(f"  {f['drive']} has {f['free'] / GB:.1f} GB free of {f['size'] / GB:.0f} GB "
                   f"({f['free'] / f['size'] * 100:.0f}%).")
    if f["scanned"] and not f["rows"]:
        out.append("  Nothing found that is worth doing.")
    for n, r in enumerate(f["rows"], 1):
        total = f"total {fixes.gb(r['total'])}" if r["total"] is not None else ""
        out.append(f"  {n:>2}  {r['risk']:<6}  {r['size']:>22}  {total:<16}  {r['title']}")
        out.append(f"      {r['why']}. How: {r['how']}. Undo: {r['undo']}.")
        if r["note"]:
            out.append(f"      {r['note']}")
    if f.get("target_line"):
        out.append(f"  {f['target_line']}")
    out += [f"  {n}" for n in f["notes"]]
    return out


def render_text(data):
    out = [f"DISK REPORT  {data['generated']}  {data['computer']}"]
    s = data["stats"]
    out.append(f"Scanned {s['files']:,} files ({s['bytes'] / GB:.1f} GB) in {s['seconds']:.0f} s")
    if data.get("settings_problems"):
        out.append("\nSETTINGS.INI HAS MISTAKES (this report used the defaults for them, and skipped broken rules)")
        out += [f"  {p}" for p in data["settings_problems"]]
        out.append("  Fix them with: python pc_tidy.py settings edit")
    if data.get("notices"):
        out.append("\nNEEDS YOUR ATTENTION (runs nobody was watching couldn't do everything)")
        out += [f"  {n}" for n in data["notices"]]
        out.append("  Clear these with: python pc_tidy.py status --dismiss")
    if data.get("fixes"):
        out += render_fixes(data["fixes"])

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
        out.append(f"\nBIGGEST FOLDERS ON {root} (over {data.get('min_gb', 1.0):g} GB)")
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


def prepare(cfg):
    """The report's options from settings.ini, your own rules in place and the AI set up. A mistake
    in what the report reads is printed, and the report goes on with the defaults for it."""
    # its own sections, your rules, and sections with names nothing knows (a misspelt rule, say)
    problems = [p for p in cfg.errors() if p.section in ("report", "ai", "move")
                or settings.section_kind(p.section or "") in ("rule", None)]
    if problems:       # the report only reads: it goes on with the defaults, and says so
        print(f"{cfg.name} has mistakes. This report uses the defaults for them and skips broken rules:")
        for p in problems:
            print(f"  {cfg.describe(p)}")
        print("Fix them with: python pc_tidy.py settings edit\n")
    classify.use_rules(cfg.good_rules())
    opts = options(cfg)
    opts.problems = [cfg.describe(p) for p in problems]
    llm.configure(opts.ai)
    return opts


def main():
    ap = argparse.ArgumentParser(description="Show what is using disk space. Never deletes anything.")
    ap.add_argument("--path", action="append", help="drive or folder to scan (default: every fixed drive)")
    ap.add_argument("--out", action="append",
                    help="report file, .pdf or .txt (default: disk_report.pdf). Repeat for both")
    ap.add_argument("--no-llm", action="store_true", help="skip the LLM parts")
    ap.add_argument("--no-open", action="store_true", help="don't open the PDF when done")
    args = ap.parse_args()

    cfg = settings.load(create=True)
    for note in cfg.notes:
        print(note)
    opts = prepare(cfg)

    outs = args.out or ["disk_report.pdf"]
    if any(o.lower().endswith(".pdf") for o in outs):
        try:
            import pdf_report  # noqa: F401  (now, not after a scan of several minutes)
        except ImportError as e:
            print(f"The PDF report needs reportlab, which isn't installed ({e}).\n"
                  "Run: pip install -r requirements.txt   (or get a text report with --out report.txt)")
            return 1
    outs = [writable(o) for o in outs]

    roots = [clean_root(p) for p in (args.path or fixed_drives())]
    data = build_report(roots, use_llm=not args.no_llm and opts.ai.enabled, opts=opts)
    text = render_text(data)
    print("\n" + text)

    for out in outs:
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
    return 0


def writable(out):
    """The report file to write: `out`, or a dated name next to it if a program (a PDF viewer, say)
    has `out` open, so the scan isn't lost to an error at the very end."""
    if not os.path.exists(out):
        return out
    try:
        with open(out, "r+b"):
            return out
    except PermissionError:
        stem, ext = os.path.splitext(out)
        other = f"{stem}-{datetime.now():%Y-%m-%d-%H%M%S}{ext}"
        print(f"{out} is open in another program, so this report goes to {other} instead.")
        return other


if __name__ == "__main__":
    sys.exit(main())
