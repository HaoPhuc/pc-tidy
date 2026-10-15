r"""
disk_report.py - find out what is filling up your C: drive. Read-only: it never deletes anything.

Usage:
    python disk_report.py                    # scan C:\
    python disk_report.py --out report.txt   # also save the report to a file
    python disk_report.py --path D:\         # scan another drive or folder
    python disk_report.py --no-llm           # skip the LLM descriptions

Run PowerShell as administrator to also see inside protected folders.
A full scan of C: takes a few minutes. To act on the report, see cleanup.ps1.
"""

import argparse
import heapq
import os
import re
import shutil
import sys
import time
from collections import namedtuple

import llm

# ---------------- settings: edit these ----------------
MIN_GB = 1.0            # folders smaller than this are hidden from the tree
MAX_DEPTH = 6           # how many levels deep the tree goes
TOP_FILES = 20          # how many of the biggest files to list
TOP_APPS = 25           # how many installed apps to list
CLEANUP_MIN_GB = 0.1    # smallest cleanable item worth listing
LLM_MAX_FOLDERS = 15    # at most this many unknown folders are sent to the LLM
# -------------------------------------------------------

# Known space users. First match wins.
#   pattern: regex on the lowercase path with forward slashes, e.g. c:/users/me/appdata/local/temp
#   kind:    "clean" = something you can free up (listed under THINGS YOU CAN CLEAN UP)
#            "info"  = good to know, usually leave it (listed under GOOD TO KNOW)
#   expand:  show its subfolders in the tree
Rule = namedtuple("Rule", "pattern what todo kind expand")
RULES = [
    # system
    (r"^[a-z]:/(pagefile|swapfile)\.sys$", "Virtual memory", "Leave it", "info", False),
    (r"^[a-z]:/hiberfil\.sys$", "Hibernation file",
     "If you never hibernate, turn it off (cleanup.ps1: Hibernation)", "clean", False),
    (r"^[a-z]:/windows/(livekernelreports|minidump)$|\.dmp$", "Crash dumps",
     "Safe to delete (cleanup.ps1: CrashDumps)", "clean", False),
    (r"^[a-z]:/windows/temp$", "Windows temp files", "Safe (cleanup.ps1: TempFiles)", "clean", False),
    (r"^[a-z]:/windows$", "Windows itself (size overstated because of hard links)",
     "Never delete by hand. Disk Cleanup > 'Clean up system files' can trim it", "info", False),
    (r"^[a-z]:/windows\.old$", "Previous Windows installation",
     "Disk Cleanup > 'Clean up system files' > 'Previous Windows installation(s)'", "clean", False),
    (r"^[a-z]:/recovery$", "Recovery files used by 'Reset this PC'", "Leave it", "info", False),
    (r"^[a-z]:/system volume information$", "System Restore points",
     "Leave it, or limit it in 'Create a restore point' > Configure", "info", False),
    (r"^[a-z]:/\$recycle\.bin$", "Recycle Bin", "Empty it (cleanup.ps1: RecycleBin)", "clean", False),
    (r"^[a-z]:/program files/windowsapps$", "Microsoft Store apps",
     "Managed by Windows. Uninstall apps in Settings > Apps", "info", True),
    (r"^[a-z]:/program files( \(x86\))?$", "Installed programs",
     "Uninstall what you don't use (Settings > Apps, or cleanup.ps1: AppsToUninstall)", "info", True),
    (r"^[a-z]:/program files/matlab/r20\d\d[ab]$", "MATLAB release",
     "Uninstall releases you no longer need", "info", False),
    (r"/wolfram research/documentation[^/]*$", "Wolfram offline documentation",
     "Uninstall the Wolfram '... Documentation' app to use the online docs instead "
     "(cleanup.ps1: AppsToUninstall)", "clean", False),
    (r"^[a-z]:/programdata/package cache$", "Installer cache",
     "Leave it, apps need it to repair or uninstall", "info", False),
    (r"^[a-z]:/programdata/microsoft/search$", "Windows Search index",
     "Leave it, or rebuild it in Indexing Options if it is huge", "info", False),

    # per user
    (r"/appdata/local/temp$", "Temporary files", "Safe (cleanup.ps1: TempFiles)", "clean", False),
    (r"/appdata/local/programs$", "Programs installed for your user only",
     "Uninstall what you don't use (Settings > Apps, or cleanup.ps1: AppsToUninstall)", "info", True),
    (r"/appdata/local/packages/claude_[^/]+(/.*)?$", "Claude desktop app data (includes its VM disk)",
     "Leave it", "info", False),
    (r"/appdata/local/packages$", "Microsoft Store app data",
     "Shrinks when you reset or uninstall the app in Settings > Apps", "info", True),
    (r"/appdata/local/pip/cache$", "pip download cache", "Safe (cleanup.ps1: PipCache)", "clean", False),
    (r"/appdata/local/uv/cache$", "uv download cache", "Safe (cleanup.ps1: UvCache)", "clean", False),
    (r"(ana|mini)conda3?/pkgs$", "conda package cache", "Safe (cleanup.ps1: CondaCache)", "clean", False),
    (r"(ana|mini)conda3?/envs$", "conda environments",
     "Remove ones you don't use (cleanup.ps1: CondaEnvsToRemove)", "clean", True),
    (r"/(ana|mini)conda3?$", "conda installation", "Leave it, clean it with cleanup.ps1: CondaCache", "info", True),
    (r"/\.ollama/models$", "Ollama models",
     "'ollama list', then 'ollama rm <name>' for models you don't use", "clean", False),
    (r"/\.cache/huggingface$", "Hugging Face model cache", "Delete models you no longer use", "clean", False),
    (r"^(?!.*/(appdata|program files[^/]*|programdata|windows|\.vscode|\.cache)/).*/node_modules$",
     "Node.js packages of a project", "Safe to delete, 'npm install' brings them back", "clean", False),
    (r"/appdata/local/docker$|docker_data\.vhdx$", "Docker data",
     "'docker system prune' removes unused images and containers", "clean", False),
    (r"ext4\.vhdx$", "WSL Linux disk",
     "Doesn't shrink by itself. 'wsl --unregister <name>' deletes a distro you don't use", "info", False),
    (r"/google/chrome/user data$", "Chrome profile and cache",
     "Chrome > Delete browsing data > Cached images and files", "clean", False),
    (r"/microsoft/edge/user data$", "Edge profile and cache",
     "Edge > Delete browsing data > Cached images and files", "clean", False),
    (r"/appdata/roaming/code/(cache|cacheddata|cachedextensionvsixs)$", "VS Code cache",
     "Safe to delete while VS Code is closed", "clean", False),
    (r"/(nvidia|amd)/(dxcache|glcache)$|/appdata/local/d3dscache$", "GPU shader cache",
     "Safe, it rebuilds itself (cleanup.ps1: GpuShaderCache)", "clean", False),
    (r"/appdata/local/autodesk/webdeploy$", "Autodesk Fusion program files",
     "Uninstall Fusion if you don't use it", "info", False),
    (r"/steamapps$", "Steam games", "Uninstall games you don't play from Steam", "info", True),
    (r"^[a-z]:/users/[^/]+/onedrive[^/]*$", "OneDrive files kept on this PC",
     "Right-click a folder > 'Free up space' to keep it online-only", "clean", False),
    (r"^[a-z]:/users/[^/]+/downloads$", "Downloads",
     "Run tidy_downloads.py. Old installers: cleanup.ps1: Installers", "clean", False),
]
RULES = [Rule(re.compile(p), what, todo, kind, expand) for p, what, todo, kind, expand in RULES]

# Windows reparse tags and file attributes
JUNCTION, SYMLINK = 0xA0000003, 0xA000000C
CLOUD_ONLY = 0x1000 | 0x40000 | 0x400000   # online-only OneDrive files use no disk space

GB = 1024 ** 3   # the unit Windows Explorer calls "GB"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def norm(path):
    return path.replace("\\", "/").lower().rstrip("/")


def known(path):
    p = norm(path)
    for rule in RULES:
        if rule.pattern.search(p):
            return rule
    return None


def fmt(size):
    return f"{size / GB:7.1f} GB"


def scan(root):
    """Walk the folder tree once. Returns folder sizes, subfolders, biggest files and stats."""
    sizes, children, big_files = {}, {}, []
    stats = {"files": 0, "bytes": 0, "denied": 0}
    last_print = [0.0]

    def progress():
        now = time.time()
        if now - last_print[0] > 1:
            last_print[0] = now
            sys.stderr.write(f"\r  scanned {stats['files']:,} files, {stats['bytes'] / GB:.1f} GB ...")
            sys.stderr.flush()

    def walk(folder):
        total, subs = 0, []
        try:
            entries = os.scandir(folder)
        except OSError:
            stats["denied"] += 1
            sizes[folder] = 0
            return 0
        with entries:
            for e in entries:
                try:
                    st = e.stat(follow_symlinks=False)
                    if e.is_symlink() or getattr(st, "st_reparse_tag", 0) in (JUNCTION, SYMLINK):
                        continue   # links point at folders that are counted elsewhere
                    is_dir = e.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    subs.append(e.path)
                    total += walk(e.path)
                else:
                    size = 0 if getattr(st, "st_file_attributes", 0) & CLOUD_ONLY else st.st_size
                    total += size
                    stats["files"] += 1
                    stats["bytes"] += size
                    if len(big_files) < TOP_FILES:
                        heapq.heappush(big_files, (size, e.path))
                    elif size > big_files[0][0]:
                        heapq.heapreplace(big_files, (size, e.path))
                    progress()
        sizes[folder] = total
        children[folder] = subs
        return total

    sys.setrecursionlimit(20000)
    walk(root)
    sys.stderr.write("\r" + " " * 70 + "\r")
    return sizes, children, sorted(big_files, reverse=True), stats


def build_tree(root, sizes, children):
    """Folders above MIN_GB as an indented tree.
    Returns the lines, the unknown leaf folders (for the LLM) and the 'info' folders shown."""
    lines, leaves, info = [], [], []

    def show(folder, depth, under_known):
        rule = known(folder)
        name = folder if depth == 0 else os.path.basename(folder)
        note = f"   <- {rule.what}" if rule else ""
        lines.append(f"{fmt(sizes[folder])}  {'  ' * depth}{name}{note}")
        if rule and rule.kind == "info":
            info.append((sizes[folder], folder, rule))
        big = sorted((c for c in children.get(folder, []) if sizes.get(c, 0) >= MIN_GB * GB),
                     key=lambda c: -sizes[c])
        expand = depth < MAX_DEPTH and (rule is None or rule.expand)
        if expand:
            for c in big:
                show(c, depth + 1, under_known or rule is not None)
        if (not big or not expand) and not under_known and rule is None and depth >= 2 \
                and not re.search(r"^[a-z]:/users/[^/]+$", norm(folder)):
            leaves.append(folder)

    show(root, 0, False)
    return lines, leaves, info


def cleanup_items(sizes, big_files):
    """Every 'clean' item above CLEANUP_MIN_GB. Outermost match only (no node_modules inside node_modules)."""
    found, kept = [], []
    candidates = [(p, s) for p, s in sizes.items() if s >= CLEANUP_MIN_GB * GB]
    candidates += [(p, s) for s, p in big_files if s >= CLEANUP_MIN_GB * GB]
    for path, size in sorted(candidates, key=lambda x: len(x[0])):
        rule = known(path)
        if not rule or rule.kind != "clean":
            continue
        n = norm(path)
        if any(n.startswith(norm(other) + "/") for other in kept):
            continue
        kept.append(path)
        found.append((size, path, rule))
    return sorted(found, key=lambda x: -x[0])


def llm_describe(folder, sizes, children):
    """Ask the LLM what a folder is. Only a description: a small model is not trusted to judge deleting."""
    subs = sorted(children.get(folder, []), key=lambda c: -sizes.get(c, 0))[:5]
    sub_text = ", ".join(f"{os.path.basename(c)} ({sizes[c] / GB:.1f} GB)" for c in subs) or "(no subfolders)"
    schema = {"type": "object", "properties": {"what": {"type": "string"}}, "required": ["what"]}
    prompt = (
        "A Windows user wants to know what is using their disk space.\n"
        f"Folder: {folder}\n"
        f"Size: {sizes[folder] / GB:.1f} GB\n"
        f"Largest subfolders: {sub_text}\n\n"
        "In at most 10 words, say what this folder most likely is: which app it belongs to "
        "and what it holds. If you don't recognize it, answer 'unknown'."
    )
    return llm.ask_json(prompt, schema).get("what", "?")


def installed_apps():
    """Installed apps with the size their installer reported (Windows only)."""
    try:
        import winreg
    except ImportError:
        return []
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    apps = {}
    for hive, path in keys:
        try:
            root = winreg.OpenKey(hive, path)
        except OSError:
            continue
        with root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, i)) as k:
                        name = winreg.QueryValueEx(k, "DisplayName")[0]
                        size_kb = winreg.QueryValueEx(k, "EstimatedSize")[0]
                except OSError:
                    continue
                if name and isinstance(size_kb, int) and size_kb > 0:
                    apps[name] = max(apps.get(name, 0), size_kb * 1024)
    return sorted(apps.items(), key=lambda x: -x[1])


def main():
    default = "C:\\" if os.name == "nt" else os.path.expanduser("~")
    ap = argparse.ArgumentParser(description="Show what is using disk space. Never deletes anything.")
    ap.add_argument("--path", default=default, help="drive or folder to scan (default C:\\)")
    ap.add_argument("--no-llm", action="store_true", help="skip the LLM descriptions of unknown folders")
    ap.add_argument("--out", help="also save the report to this text file")
    args = ap.parse_args()

    root = args.path
    if re.fullmatch(r"[A-Za-z]:", root):
        root += "\\"   # "C:" alone means "current folder on C:", not the whole drive
    root = os.path.abspath(root)

    print(f"Scanning {root} (this can take a few minutes) ...")
    t0 = time.time()
    sizes, children, big_files, stats = scan(root)
    out = []

    usage = shutil.disk_usage(root)
    out.append(f"\nDRIVE  {usage.used / GB:.0f} GB used of {usage.total / GB:.0f} GB, {usage.free / GB:.0f} GB free")
    out.append(f"Scanned {stats['files']:,} files ({stats['bytes'] / GB:.1f} GB) in {time.time() - t0:.0f} s")

    out.append(f"\nBIGGEST FOLDERS (over {MIN_GB:g} GB)")
    lines, leaves, info = build_tree(root, sizes, children)
    out += lines

    out.append("\nBIGGEST FILES")
    for size, path in big_files:
        rule = known(path)
        out.append(f"{fmt(size)}  {path}" + (f"   <- {rule.what}" if rule else ""))
        if rule and rule.kind == "info":
            info.append((size, path, rule))

    out.append("\nTHINGS YOU CAN CLEAN UP")
    items = cleanup_items(sizes, big_files)
    for size, path, rule in items:
        out.append(f"{fmt(size)}  {path}\n             {rule.what}. {rule.todo}")
    if not items:
        out.append("  (none found)")

    out.append("\nGOOD TO KNOW (usually leave these alone)")
    seen = set()
    for size, path, rule in sorted(info, key=lambda x: -x[0]):
        if path not in seen:
            seen.add(path)
            out.append(f"{fmt(size)}  {path}\n             {rule.what}. {rule.todo}")

    if leaves and not args.no_llm and llm.available():
        out.append("\nWHAT THE LLM THINKS THESE UNKNOWN FOLDERS ARE")
        out.append("  (a small model that can be wrong. This is a description, not advice to delete.)")
        for folder in sorted(leaves, key=lambda f: -sizes[f])[:LLM_MAX_FOLDERS]:
            sys.stderr.write(f"\r  asking LLM about {os.path.basename(folder)[:40]} ...{' ' * 20}")
            try:
                what = llm_describe(folder, sizes, children)
            except Exception as e:
                what = f"(LLM failed: {e})"
            out.append(f"{fmt(sizes[folder])}  {folder}\n             {what}")
        sys.stderr.write("\r" + " " * 70 + "\r")

    apps = installed_apps()
    if apps:
        out.append("\nINSTALLED APPS BY SIZE (as reported by their installers, often missing or rough)")
        for name, size in apps[:TOP_APPS]:
            out.append(f"{fmt(size)}  {name}")
        out.append("  Uninstall in Settings > Apps, or copy the exact name into cleanup.ps1: AppsToUninstall")

    if stats["denied"]:
        out.append(f"\n{stats['denied']} folders could not be opened. Running PowerShell as administrator "
                   "includes most of them (a few system folders stay locked).")

    report = "\n".join(out)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"\nSaved to {args.out}")


if __name__ == "__main__":
    main()
