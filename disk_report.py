r"""
disk_report.py - find out what is filling up your C: drive. Read-only: it never deletes anything.

Usage:
    python disk_report.py                    # scan C:\
    python disk_report.py --path D:\         # scan another drive or folder
    python disk_report.py --no-llm           # skip the LLM guesses
    python disk_report.py --out report.txt   # also save the report to a file

Run PowerShell as administrator to also see inside protected folders.
A full scan of C: takes a few minutes.
"""

import argparse
import heapq
import os
import re
import shutil
import sys
import time

import llm

# ---------------- settings: edit these ----------------
MIN_GB = 1.0            # folders smaller than this are hidden from the tree
MAX_DEPTH = 6           # how many levels deep the tree goes
TOP_FILES = 20          # how many of the biggest files to list
TOP_APPS = 25           # how many installed apps to list
CLEANUP_MIN_GB = 0.1    # smallest known-cleanable item worth listing
LLM_MAX_FOLDERS = 15    # at most this many unknown folders are sent to the LLM

# Known space users: (pattern on the lowercase path with forward slashes, what it is, what to do, expand in tree)
KNOWN = [
    (r"^[a-z]:/hiberfil\.sys$", "Hibernation file",
     "If you never use hibernate: 'powercfg /h off' in an admin PowerShell", False),
    (r"^[a-z]:/(pagefile|swapfile)\.sys$", "Virtual memory", "Leave it", False),
    (r"^[a-z]:/windows$", "Windows itself (size overstated because of hard links)",
     "Never delete by hand. Disk Cleanup > 'Clean up system files'", False),
    (r"^[a-z]:/windows\.old$", "Previous Windows installation",
     "Disk Cleanup > 'Clean up system files' > 'Previous Windows installation(s)'", False),
    (r"^[a-z]:/\$recycle\.bin$", "Recycle Bin", "Empty the Recycle Bin", False),
    (r"^[a-z]:/system volume information$", "System Restore points",
     "Leave it, or limit it in 'Create a restore point' > Configure", False),
    (r"^[a-z]:/program files( \(x86\))?$", "Installed programs",
     "Uninstall what you don't use: Settings > Apps > Installed apps", True),
    (r"^[a-z]:/programdata/package cache$", "Installer cache", "Leave it, apps need it to repair or uninstall", False),
    (r"/appdata/local/temp$", "Temporary files", "Safe: Settings > System > Storage > Temporary files", False),
    (r"/appdata/local/pip/cache$", "pip download cache", "Safe: 'pip cache purge'", False),
    (r"(ana|mini)conda3?/pkgs$", "conda package cache", "Safe: 'conda clean --all'", False),
    (r"(ana|mini)conda3?/envs$", "conda environments", "Remove unused ones: 'conda env remove -n <name>'", True),
    (r"/\.ollama/models$", "Ollama models", "'ollama list', then 'ollama rm <name>' for ones you don't use", False),
    (r"/\.cache/huggingface$", "Hugging Face model cache", "Delete models you no longer use", False),
    (r"/node_modules$", "Node.js packages", "Safe to delete, 'npm install' brings them back", False),
    (r"\.vhdx$|/appdata/local/docker$", "Docker / WSL virtual disk",
     "'docker system prune'; WSL disks don't shrink by themselves", False),
    (r"/google/chrome/user data$", "Chrome profile and cache", "Chrome > Delete browsing data > Cached files", False),
    (r"/microsoft/edge/user data$", "Edge profile and cache", "Edge > Delete browsing data > Cached files", False),
    (r"/appdata/roaming/code/(cache|cacheddata|cachedextensionvsixs)$", "VS Code cache",
     "Safe to delete while VS Code is closed", False),
    (r"/(nvidia|amd)/(dxcache|glcache)$|/d3dscache$", "GPU shader cache",
     "Safe: Settings > System > Storage > Temporary files", False),
    (r"/steamapps$", "Steam games", "Uninstall games you don't play from Steam", True),
    (r"/onedrive[^/]*$", "OneDrive files kept on this PC",
     "Right-click a folder > 'Free up space' to keep it online-only", False),
    (r"^[a-z]:/users/[^/]+/downloads$", "Downloads", "Run tidy_downloads.py", False),
]
KNOWN = [(re.compile(p), what, todo, expand) for p, what, todo, expand in KNOWN]

# Windows file attributes and reparse tags
JUNCTION, SYMLINK = 0xA0000003, 0xA000000C
CLOUD_ONLY = 0x1000 | 0x40000 | 0x400000   # OneDrive files that are online-only use no disk space
# -------------------------------------------------------

GB = 1024 ** 3   # same unit Windows Explorer calls "GB"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def norm(path):
    return path.replace("\\", "/").lower().rstrip("/")


def known(path):
    p = norm(path)
    for pattern, what, todo, expand in KNOWN:
        if pattern.search(p):
            return what, todo, expand
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


def tree_lines(root, sizes, children):
    """Folders above MIN_GB as an indented tree. Also returns the tree's leaf folders."""
    lines, leaves = [], []

    def show(folder, depth, under_known):
        k = known(folder)
        name = folder if depth == 0 else os.path.basename(folder)
        note = f"   <- {k[0]}" if k else ""
        lines.append(f"{fmt(sizes[folder])}  {'  ' * depth}{name}{note}")
        big = sorted((c for c in children.get(folder, []) if sizes.get(c, 0) >= MIN_GB * GB),
                     key=lambda c: -sizes[c])
        expand = depth < MAX_DEPTH and (k is None or k[2])
        if expand:
            for c in big:
                show(c, depth + 1, under_known or k is not None)
        if (not big or not expand) and not under_known and k is None and depth >= 2 \
                and not re.search(r"^[a-z]:/users/[^/]+$", norm(folder)):
            leaves.append(folder)

    show(root, 0, False)
    return lines, leaves


def cleanup_items(sizes, big_files):
    """Every known item above CLEANUP_MIN_GB, outermost match only (no node_modules inside node_modules)."""
    found, kept = [], []
    candidates = [(p, s) for p, s in sizes.items() if s >= CLEANUP_MIN_GB * GB]
    candidates += [(p, s) for s, p in big_files if s >= CLEANUP_MIN_GB * GB]
    for path, size in sorted(candidates, key=lambda x: len(x[0])):
        k = known(path)
        if not k:
            continue
        n = norm(path)
        if any(n.startswith(norm(other) + "/") for other in kept):
            continue
        kept.append(path)
        found.append((size, path, k[0], k[1]))
    return sorted(found, reverse=True)


def llm_guess(folder, sizes, children):
    subs = sorted(children.get(folder, []), key=lambda c: -sizes.get(c, 0))[:5]
    sub_text = ", ".join(f"{os.path.basename(c)} ({sizes[c] / GB:.1f} GB)" for c in subs) or "(no subfolders)"
    schema = {
        "type": "object",
        "properties": {
            "what": {"type": "string"},
            "safe_to_delete": {"type": "string", "enum": ["yes", "no", "maybe", "unknown"]},
            "tip": {"type": "string"},
        },
        "required": ["what", "safe_to_delete", "tip"],
    }
    prompt = (
        "You help a Windows user understand what is using their disk space.\n"
        f"Folder: {folder}\n"
        f"Size: {sizes[folder] / GB:.1f} GB\n"
        f"Largest subfolders: {sub_text}\n\n"
        "In a few words, say what this folder most likely is. "
        "Then say if deleting it is safe: yes (cache or leftovers that get recreated), "
        "no (system files, an installed app, or personal files), maybe, or unknown if you don't recognize it. "
        "Finally give one short practical tip. Never guess wildly; say unknown instead."
    )
    return llm.ask_json(prompt, schema)


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
    ap.add_argument("--no-llm", action="store_true", help="skip LLM guesses for unknown folders")
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
    lines, leaves = tree_lines(root, sizes, children)
    out += lines

    out.append("\nBIGGEST FILES")
    for size, path in big_files:
        k = known(path)
        out.append(f"{fmt(size)}  {path}" + (f"   <- {k[0]}" if k else ""))

    out.append("\nKNOWN THINGS YOU CAN CLEAN UP")
    items = cleanup_items(sizes, big_files)
    for size, path, what, todo in items:
        out.append(f"{fmt(size)}  {path}\n             {what}. {todo}")
    if not items:
        out.append("  (none found)")

    if leaves and not args.no_llm and llm.available():
        out.append("\nLLM GUESSES FOR UNKNOWN BIG FOLDERS (a small model, so double-check before acting)")
        for folder in sorted(leaves, key=lambda f: -sizes[f])[:LLM_MAX_FOLDERS]:
            sys.stderr.write(f"\r  asking LLM about {os.path.basename(folder)[:40]} ...{' ' * 20}")
            try:
                g = llm_guess(folder, sizes, children)
                out.append(f"{fmt(sizes[folder])}  {folder}\n             {g.get('what', '?')} | "
                           f"safe to delete: {g.get('safe_to_delete', '?')} | {g.get('tip', '')}")
            except Exception as e:
                out.append(f"{fmt(sizes[folder])}  {folder}\n             (LLM failed: {e})")
        sys.stderr.write("\r" + " " * 70 + "\r")

    apps = installed_apps()
    if apps:
        out.append("\nINSTALLED APPS BY SIZE (as reported by their installers, often missing or rough)")
        for name, size in apps[:TOP_APPS]:
            out.append(f"{fmt(size)}  {name}")
        out.append("  Uninstall in Settings > Apps > Installed apps")

    if stats["denied"]:
        out.append(f"\n{stats['denied']} folders could not be opened. "
                   "Run PowerShell as administrator to include them.")

    report = "\n".join(out)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"\nSaved to {args.out}")


if __name__ == "__main__":
    main()
