"""Where everything is: the profile, Windows, Program Files, the data folder and the system drive.

Every pc-tidy module asks a Context for these places instead of reading environment variables
itself, so the tests can point all of them at fake folders and test volumes.

PCTIDY_SANDBOX_ROOT (one or more folders, separated by ';') is set by the tests only. While it is
set, safety.check refuses anything outside those folders, and the test hooks below are honoured.
"""

import ctypes
import os
import uuid

from .paths import inside, norm

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST_FOLDER_NAME = "From-C"          # move_off_c.py's default destination: <other drive>:\From-C
VOLUME_JOURNAL_FOLDER = "_pc-tidy"   # journals kept on the destination volume when C: is full

KNOWN_FOLDER_NAMES = ("Desktop", "Documents", "Downloads", "Music", "Pictures", "Videos")
_KNOWN_FOLDER_IDS = (
    "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",   # Desktop
    "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",   # Documents
    "{374DE290-123F-4565-9164-0C4925E467CB}",   # Downloads
    "{4BD8D571-6D19-48D3-BE97-422220080E43}",   # Music
    "{33E28130-4E1E-4676-835A-98395C3BC3BB}",   # Pictures
    "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",   # Videos
)


def _abs(path):
    return os.path.abspath(os.path.expandvars(path)) if path else path


class Context:
    def __init__(self, environ=None):
        # Windows environment names ignore case, and a copy of os.environ has them in upper case
        env = {k.upper(): v for k, v in (os.environ if environ is None else environ).items()}
        self.env = env

        def get(*names, default=None):
            for name in names:
                if env.get(name.upper()):
                    return env[name.upper()]
            return default

        self.profile = _abs(get("USERPROFILE", default=os.path.expanduser("~")))
        self.local_appdata = _abs(get("LOCALAPPDATA", default=os.path.join(self.profile, "AppData", "Local")))
        self.roaming_appdata = _abs(get("APPDATA", default=os.path.join(self.profile, "AppData", "Roaming")))
        self.temp = _abs(get("TEMP", "TMP", default=os.path.join(self.local_appdata, "Temp")))
        drive = get("SystemDrive", default="C:").rstrip("\\/")
        self.system_drive = drive.upper() + "\\"
        self.system_root = _abs(get("SystemRoot", "windir", default=self.system_drive + "Windows"))
        self.program_files = []
        for name, default in (("ProgramFiles", "Program Files"), ("ProgramFiles(x86)", "Program Files (x86)"),
                              ("ProgramW6432", "Program Files")):
            path = _abs(get(name, default=self.system_drive + default))
            if norm(path) not in map(norm, self.program_files):
                self.program_files.append(path)
        self.program_data = _abs(get("ProgramData", "ALLUSERSPROFILE", default=self.system_drive + "ProgramData"))
        public = get("PUBLIC")
        self.users_dir = os.path.dirname(_abs(public)) if public else self.system_drive + "Users"
        self.data_dir = os.path.join(self.local_appdata, "pc-tidy")
        self.app_dir = APP_DIR
        self.legacy_move_logs = os.path.join(self.profile, ".pc-tidy", "move_logs")
        roots = get("PCTIDY_SANDBOX_ROOT", default="")
        self.sandbox = [os.path.abspath(r) for r in roots.split(os.pathsep) if r.strip()]
        self._known = None

    # ---------- places ----------
    def folder(self, *parts):
        """A folder in the data folder (runs, plans, ...). It may not exist yet."""
        return os.path.join(self.data_dir, *parts)

    def known_folders(self):
        """Desktop, Documents, Downloads, Music, Pictures, Videos and the OneDrive folders."""
        if self._known is None:
            found = [os.path.join(self.profile, name) for name in KNOWN_FOLDER_NAMES]
            try:
                found += [e.path for e in os.scandir(self.profile)
                          if e.name.lower().startswith("onedrive") and e.is_dir()]
            except OSError:
                pass
            found += [_abs(self.env[k]) for k in ("ONEDRIVE", "ONEDRIVECONSUMER", "ONEDRIVECOMMERCIAL")
                      if self.env.get(k)]
            if not self.sandbox and norm(self.profile) == norm(os.path.expanduser("~")):
                found += _shell_known_folders()      # where Windows says they are, e.g. moved to D:
            out, seen = [], set()
            for path in found:
                if norm(path) not in seen:
                    seen.add(norm(path))
                    out.append(path)
            self._known = out
        return self._known

    # ---------- the test sandbox ----------
    def in_sandbox(self, path):
        return any(inside(path, root) for root in self.sandbox)

    def test_value(self, name):
        """A test hook from the environment, honoured only while the sandbox is set."""
        return self.env.get(name.upper()) if self.sandbox else None


def current():
    """The context for this process, read fresh from the environment."""
    return Context()


def new_run_id(now=None):
    import time
    t = time.localtime(now)
    return time.strftime("%Y%m%d-%H%M%S", t) + f"-{os.getpid()}-{uuid.uuid4().hex[:4]}"


def _shell_known_folders():
    if os.name != "nt":
        return []
    out = []
    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
    for text in _KNOWN_FOLDER_IDS:
        guid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(text).bytes_le)
        path = ctypes.c_wchar_p()
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path)) == 0:
            out.append(path.value)
            ole32.CoTaskMemFree(path)
    return out
