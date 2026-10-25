# pc-tidy.exe: one file with Python inside. Build it from the repository root with
#     python -m pip install -r packaging\requirements-build.txt
#     python -m PyInstaller --noconfirm --clean packaging\pc-tidy.spec
# It lands in dist\pc-tidy.exe. CI builds it the same way (.github/workflows/ci.yml).
#
# What is in it: the guided flow, moves, undo and recover, on the standard library only. The
# report is a web page (html_report.py). Not in it: the AI, sorting (tidy_folder.py), cleanup.ps1
# and reportlab, pypdf and python-docx, which stay in the source edition.
#
# It unpacks into %LOCALAPPDATA%\pc-tidy\app each time it runs, never into %TEMP%: the Temp fix
# would otherwise be cleaning up after pc-tidy while it runs. AppData is a folder no fix may touch.
import os

from PyInstaller.utils.win32 import winmanifest

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# PyInstaller writes either asInvoker or requireAdministrator into the manifest. pc-tidy wants
# highestAvailable: an administrator account gets one UAC prompt and runs elevated as itself, and a
# standard account runs as it is, never asked for someone else's password (docs/PLAN-3.md, M11).
# The manifest is set before the archive is appended, so it is set here, not by editing the exe
# afterwards. tests/test_exe.py checks the built exe, so a PyInstaller that changes this is caught.
_set_execution_level = winmanifest._set_execution_level


def _highest_available(manifest_dom, root_element, uac_admin=False, uac_uiaccess=False):
    _set_execution_level(manifest_dom, root_element, uac_admin, uac_uiaccess)
    for element in root_element.getElementsByTagName("requestedExecutionLevel"):
        element.setAttribute("level", "highestAvailable")


winmanifest._set_execution_level = _highest_available


def _version_info():
    """What Properties > Details shows: product, version and author. The version comes from
    PCTIDY_VERSION (CI sets the v* tag, e.g. v3.0.0), else 0.0.0."""
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                     VarFileInfo, VarStruct, VSVersionInfo)
    text = os.environ.get("PCTIDY_VERSION", "0.0.0").lstrip("v")
    numbers = []
    for part in text.replace("-", ".").replace("+", ".").split(".")[:4]:
        if not part.isdigit():
            break
        numbers.append(int(part))
    numbers = tuple((numbers + [0, 0, 0, 0])[:4])
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[StringFileInfo([StringTable("040904B0", [
                  StringStruct("CompanyName", "Phuc Huynh"),
                  StringStruct("FileDescription", "pc-tidy: free space on C:, safely"),
                  StringStruct("FileVersion", text),
                  StringStruct("InternalName", "pc-tidy"),
                  StringStruct("LegalCopyright", "Copyright (c) 2026 Phuc Huynh. MIT License."),
                  StringStruct("OriginalFilename", "pc-tidy.exe"),
                  StringStruct("ProductName", "pc-tidy"),
                  StringStruct("ProductVersion", text)])]),
              VarFileInfo([VarStruct("Translation", [1033, 1200])])])

a = Analysis(
    [os.path.join(ROOT, "pc_tidy.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "settings.template.ini"), ".")],
    # imported inside functions, so named here as well
    hiddenimports=["disk_report", "html_report", "move_off_c"],
    excludes=["pdf_report", "reportlab", "pypdf", "docx", "tidy_folder", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="pc-tidy",
    console=True,
    upx=False,
    runtime_tmpdir=r"%LOCALAPPDATA%\pc-tidy\app",
    manifest=os.path.join(SPECPATH, "pc-tidy.manifest"),
    version=_version_info(),
)
