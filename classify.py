"""Rules that recognize folders and apps. Used by disk_report.py and move_off_c.py.

All folder patterns are regexes on the lowercase path with forward slashes,
e.g. c:/users/me/appdata/local/temp. Edit the lists to teach the tools about your own PC.
"""

import re
from collections import namedtuple

from scanner import norm, system_drive

# ---------------------------------------------------------------------------
# 1. Known space users, with advice. First match wins.
#    kind "clean": something you can free up   (THINGS YOU CAN CLEAN UP)
#    kind "info":  good to know, usually leave  (GOOD TO KNOW)
#    expand: show its subfolders in the folder tree
# ---------------------------------------------------------------------------
Rule = namedtuple("Rule", "pattern what todo kind expand")
_RULES = [
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
    (r"/steamapps$", "Steam games",
     "Uninstall games you don't play, or move them: Steam > Settings > Storage", "info", True),
    (r"^[a-z]:/users/[^/]+/onedrive[^/]*$", "OneDrive files kept on this PC",
     "Right-click a folder > 'Free up space' to keep it online-only", "clean", False),
    (r"^[a-z]:/users/[^/]+/downloads$", "Downloads",
     "Sort it with tidy_folder.py. Old installers: cleanup.ps1: Installers", "clean", False),
]
RULES = [Rule(re.compile(p), what, todo, kind, expand) for p, what, todo, kind, expand in _RULES]


def known(path):
    """The first RULES entry that matches a path, or None."""
    p = norm(path)
    for rule in RULES:
        if rule.pattern.search(p):
            return rule
    return None


# ---------------------------------------------------------------------------
# 2. Space by category. Each folder takes the category of the first pattern that
#    matches it exactly; otherwise it inherits its parent's category.
# ---------------------------------------------------------------------------
SYSTEM, APPS, GAMES, DEV, CACHE, PERSONAL, OTHER = (
    "System", "Apps & app data", "Games", "Dev & science tools", "Caches & temp", "Personal files", "Other")
FOLDER_CATEGORIES = [SYSTEM, APPS, GAMES, DEV, CACHE, PERSONAL, OTHER]

_CATEGORY_RULES = [
    # caches and temp (checked first, they live inside other categories)
    (r"/appdata/local/temp$|^[a-z]:/windows/(temp|livekernelreports|minidump|softwaredistribution/download)$", CACHE),
    (r"/appdata/local/(pip|uv)/cache$|(ana|mini)conda3?/pkgs$|/appdata/local/crashdumps$", CACHE),
    (r"/(nvidia|amd)/(dxcache|glcache)$|/appdata/local/d3dscache$|^[a-z]:/\$recycle\.bin$", CACHE),
    (r"/user data/[^/]+/(cache|code cache|gpucache|service worker)$", CACHE),
    (r"/appdata/local/microsoft/windows/inetcache$|/appdata/roaming/code/(cache|cacheddata|cachedextensionvsixs)$", CACHE),
    (r"^[a-z]:/users/[^/]+/\.cache$", CACHE),
    # system
    (r"^[a-z]:/(windows|recovery|system volume information|windows\.old|\$windows\.~bt|\$winreagent|boot|efi|perflogs)$", SYSTEM),
    (r"^[a-z]:/programdata/microsoft$", SYSTEM),
    # games
    (r"/(steam|steamlibrary|epic games|riot games|gog galaxy|ea games|ubisoft|battle\.net|xboxgames|games)$", GAMES),
    # developer and science tools
    (r"(^|/)(ana|mini)conda3?$|/\.conda$|/\.vscode$|/appdata/roaming/code$|/\.cache/huggingface$", DEV),
    (r"/\.ollama$|/node_modules$|/\.npm$|/appdata/(local|roaming)/npm(-cache)?$|/\.(cargo|rustup|gradle|m2|nuget)$", DEV),
    (r"/appdata/local/(docker|wsl|jetbrains|mathworks|githubdesktop|autodesk)$", DEV),
    (r"/appdata/local/packages/canonicalgroup[^/]*$", DEV),
    (r"/appdata/local/programs/(microsoft vs code|ollama|dockerdesktop|python|github desktop|cursor)$", DEV),
    (r"^[a-z]:/program files( \(x86\))?/(git|matlab|mathworks|wolfram research|originlab|jetbrains|nodejs|cmake|r|rstudio|miktex|docker|autodesk|ansys inc|comsol)$", DEV),
    (r"^[a-z]:/program files/common files/wolfram research$", DEV),
    # apps and their data
    (r"^[a-z]:/program files( \(x86\))?$|^[a-z]:/programdata$|/appdata$", APPS),
    (r"^[a-z]:/users/[^/]+/\.[^/]+$", APPS),    # other dot folders in your profile hold app settings
    # personal files
    (r"^[a-z]:/users/[^/]+$|^[a-z]:/users/public$", PERSONAL),
]
CATEGORY_RULES = [(re.compile(p), c) for p, c in _CATEGORY_RULES]


def folder_categories(scan):
    """Bytes per category for a Scan. Counts each folder's own files once."""
    totals = dict.fromkeys(FOLDER_CATEGORIES, 0)
    sysroot = norm(system_drive())
    cat_of = {}

    def resolve(folder, parent_cat):
        n = norm(folder)
        for pattern, cat in CATEGORY_RULES:
            if pattern.search(n):
                return cat
        return parent_cat

    for root in scan.roots:
        n = norm(root)
        if n == sysroot:
            root_cat, root_files = OTHER, SYSTEM      # pagefile.sys, hiberfil.sys ...
        elif re.fullmatch(r"[a-z]:", n):
            root_cat = root_files = PERSONAL          # a data drive is mostly your own files
        else:
            root_cat = root_files = resolve(root, OTHER)
        totals[root_files] += scan.own.get(root, 0)
        stack = [(c, root_cat) for c in scan.children.get(root, [])]
        while stack:
            folder, parent_cat = stack.pop()
            cat = resolve(folder, parent_cat)
            cat_of[folder] = cat
            totals[cat] += scan.own.get(folder, 0)
            stack.extend((c, cat) for c in scan.children.get(folder, []))
    return totals, cat_of


# ---------------------------------------------------------------------------
# 3. Installed apps by type. Keyword regexes on "name | publisher", first match wins.
#    Apps nothing matches go to the LLM (if it is running), otherwise to "Other".
# ---------------------------------------------------------------------------
APP_TYPES = ["Dev & science tools", "Games", "Browsers", "Communication", "Office & productivity",
             "Media & creative", "Cloud & sync", "Security & VPN", "System, drivers & runtimes",
             "Utilities", "Other"]

_APP_RULES = [
    ("System, drivers & runtimes",
     r"runtime|redistributable|webview2|driver|nvidia|\bamd\b|realtek|\bintel\b|\.net\b|directx|physx|"
     r"chipset|dolby|gigabyte|control center|\bbios\b|synaptics|update health|pc health check|vcredist|"
     r"windows app sdk|microsoft update"),
    ("Dev & science tools",
     r"visual studio|\bgit\b|github|python|anaconda|miniconda|docker|subsystem for linux|node\.js|matlab|"
     r"mathworks|wolfram|mathematica|originlab|jetbrains|pycharm|intellij|cmake|ollama|postman|rstudio|"
     r"\br for windows|miktex|texstudio|tex live|arduino|kicad|ltspice|comsol|ansys|autodesk|fusion|"
     r"solidworks|freecad|windows sdk|windows terminal|powershell|putty|winscp|filezilla|wireshark|"
     r"codex|cursor|labview|origin \d"),
    ("Games", r"steam|epic games|riot|valorant|league of legends|battle\.net|\bea app\b|ubisoft|gog galaxy|"
              r"minecraft|xbox|roblox|\bgame"),
    ("Browsers", r"chrome|firefox|^microsoft edge\b|brave|opera|vivaldi|tor browser|\barc\b"),
    ("Communication", r"teams|zoom|discord|slack|whatsapp|telegram|zalo|skype|signal|messenger|thunderbird"),
    ("Office & productivity",
     r"microsoft 365|\boffice\b|\bword\b|excel|powerpoint|onenote|outlook|notion|obsidian|acrobat|\bpdf\b|"
     r"zotero|mendeley|evernote|todoist|libreoffice|claude|chatgpt|copilot|grammarly|foxit"),
    ("Media & creative", r"spotify|\bvlc\b|\bobs\b|audacity|photoshop|premiere|illustrator|adobe|gimp|"
                         r"inkscape|canva|blender|davinci|capcut|figma|krita|itunes|media player|handbrake"),
    ("Cloud & sync", r"onedrive|dropbox|google drive|icloud|\bmega\b|nextcloud|syncthing"),
    ("Security & VPN", r"vpn|antivirus|norton|mcafee|avast|\bavg\b|bitdefender|kaspersky|malwarebytes|"
                       r"\beset\b|urban"),
    ("Utilities", r"7-zip|winrar|qbittorrent|utorrent|bittorrent|everything|powertoys|wiztree|windirstat|"
                  r"ccleaner|revo uninstaller|notepad\+\+|teamviewer|anydesk|rufus|logitech|razer|corsair"),
]
APP_RULES = [(t, re.compile(p)) for t, p in _APP_RULES]


def app_type(name, publisher=""):
    """App type from keyword rules, or None if no rule matches."""
    text = f"{name} | {publisher}".lower()
    for app_t, pattern in APP_RULES:
        if pattern.search(text):
            return app_t
    return None


def installed_apps():
    """Installed apps (Windows only) as dicts: name, publisher, size (bytes, 0 if unknown)."""
    try:
        import winreg
    except ImportError:
        return []
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]

    def value(key, name, default=None):
        try:
            return winreg.QueryValueEx(key, name)[0]
        except OSError:
            return default

    apps = {}
    for hive, path in keys:
        try:
            root = winreg.OpenKey(hive, path)
        except OSError:
            continue
        with root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    k = winreg.OpenKey(root, winreg.EnumKey(root, i))
                except OSError:
                    continue
                with k:
                    name = value(k, "DisplayName")
                    if not name or value(k, "SystemComponent") == 1 or value(k, "ParentKeyName"):
                        continue
                    if str(value(k, "ReleaseType", "")).lower() in ("update", "hotfix", "security update"):
                        continue
                    size_kb = value(k, "EstimatedSize", 0)
                    size = size_kb * 1024 if isinstance(size_kb, int) and size_kb > 0 else 0
                    old = apps.get(name)
                    if not old or size > old["size"]:
                        apps[name] = {"name": name, "publisher": value(k, "Publisher", "") or "", "size": size}
    return sorted(apps.values(), key=lambda a: (-a["size"], a["name"].lower()))


def classify_apps(apps, ask_llm=None):
    """Adds 'type' to each app. ask_llm(list_of_apps) -> {name: type} handles apps no rule matches."""
    unknown = []
    for a in apps:
        a["type"] = app_type(a["name"], a["publisher"])
        if a["type"] is None:
            unknown.append(a)
    if unknown and ask_llm:
        guesses = ask_llm(unknown)
        for a in unknown:
            guess = guesses.get(a["name"])
            if guess in APP_TYPES:
                a["type"], a["llm"] = guess, True
    for a in apps:
        a["type"] = a["type"] or "Other"
    return apps
