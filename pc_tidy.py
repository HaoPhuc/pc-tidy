r"""
pc_tidy.py - see what fills C: and free space on it, safely. With no command it guides you: a scan of C:,
the report with the fixes for C: ranked lowest risk first, the fixes you pick, the exact list (the dry
run), your yes, and a summary. Double-clicking pc-tidy.cmd does the same.

Usage:
    python pc_tidy.py                    # the guided flow
    python pc_tidy.py --no-llm --no-open # ... without the local AI, and without opening the PDF
    python pc_tidy.py recover            # check runs that were interrupted, and decide about half-done moves
    python pc_tidy.py status             # what unattended runs couldn't do, and anything left unfinished
    python pc_tidy.py status --dismiss   # ... and then clear those notices
    python pc_tidy.py settings show      # every setting, and where its value came from
    python pc_tidy.py settings check     # every mistake in settings.ini, with its line number
    python pc_tidy.py settings edit      # open settings.ini in Notepad, and check it when Notepad closes
    python pc_tidy.py settings import ..\pc-tidy-old
                                         # bring in the settings of a pc-tidy folder from before 2.0
    python pc_tidy.py cleanup-settings   # the [cleanup] settings as JSON, for cleanup.ps1
"""

import argparse
import json
import os
import subprocess
import sys
import time

from pctidy import context, guided, journal, lock, recovery, safety, settings, settings_import, ui

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def recover(ctx):
    try:
        report = recovery.at_start(ctx, "pc_tidy.py recover", ask=ui.ask)
    except lock.Busy:
        print(lock.BUSY_MESSAGE)
        return lock.BUSY_EXIT
    if not report.settled and not report.undecided and not report.unrecorded:
        print("Nothing was interrupted. Everything pc-tidy started has finished.")
        return 0
    if report.undecided:
        print(f"\n{len(report.undecided)} interrupted move(s) still wait for your decision. pc-tidy won't touch "
              "those paths until you decide: run python pc_tidy.py recover again.")
        return 3
    return 0


def status(ctx, dismiss=False):
    notices = journal.notices(ctx)
    waiting = recovery.undecided(ctx)
    if not notices and not waiting:
        print("Nothing needs your attention.")
        return 0
    if waiting:
        print(f"{len(waiting)} interrupted move(s) wait for your decision (python pc_tidy.py recover):")
        for item in waiting:
            print(f"  {item.src}  ->  {item.dst}")
    if notices:
        print("Notices from runs nobody was watching:")
        for n in notices:
            print(f"  {n.get('time', '')}  {n.get('message', '')}")
        if dismiss:
            journal.dismiss_notices(ctx)
            print("Dismissed.")
        else:
            print("Clear these with: python pc_tidy.py status --dismiss")
    return 3 if waiting else 0


# ---------- settings ----------
def settings_show(ctx):
    print(settings.show_text(settings.load(ctx, create=True)))
    return 0


def settings_check(ctx):
    cfg = settings.load(ctx, create=True)
    print("\n".join(settings.check_text(cfg)))
    return settings.EXIT if cfg.errors() else 0


def settings_edit(ctx):
    """Notepad, then a check as soon as it closes, so a mistake shows now and not on Sunday."""
    cfg = settings.load(ctx, create=True)
    path = ctx.settings_file
    if not os.path.exists(path):
        print("\n".join(cfg.notes) or f"There is no {path}.")
        return 1
    editor = ctx.test_value("PCTIDY_TEST_EDITOR")      # the tests' stand-in for Notepad
    while True:
        print(f"Opening {path} in Notepad. Save it and close Notepad when you're done.")
        started = time.monotonic()
        try:
            subprocess.run([sys.executable, editor, path] if editor else ["notepad.exe", path])
        except OSError as e:
            print(f"Couldn't start Notepad ({e}). Open {path} in any text editor, then run: "
                  "python pc_tidy.py settings check")
            return 1
        if not editor and time.monotonic() - started < 2:
            # Windows 11's Notepad shows the file in a window that was already open, and returns at once
            ui.ask("Notepad opened it in a window that was already open. Press Enter here once you have saved it: ")
        cfg = settings.load(ctx)
        print("\n".join(settings.check_text(cfg)))
        if not cfg.errors():
            return 0
        if not ui.ask_yes("Type yes to open it again and fix it: "):
            return settings.EXIT


def settings_import_old(ctx, folder=None):
    """Read an old pc-tidy folder, show everything it would import, and write only after a yes."""
    folder = os.path.abspath(folder or os.path.join(os.path.dirname(ctx.app_dir), "pc-tidy-old"))
    if not os.path.isdir(folder):
        print(f"There is no folder {folder}. Give the old pc-tidy folder, for example:\n"
              "    python pc_tidy.py settings import D:\\pc-tidy-old")
        return 1
    cfg = settings.load(ctx, create=True)
    for note in cfg.notes:
        print(note)
    found = settings_import.read_folder(folder, ctx)
    if not found.files:
        print(f"{folder} has none of pc-tidy's files (cleanup.ps1, classify.py, tidy_folder.py, ...).")
        return 1
    if not cfg.from_defaults:
        found.drop_present(cfg)
    _print_found(found)
    if not found.anything:
        print(f"\nNothing to import: {cfg.name} already has every setting of the old folder that differs "
              "from the defaults." if not cfg.from_defaults else
              "\nNothing to import: the old folder uses the default settings.")
        return 0
    path = ctx.settings_file
    try:
        with open(path, encoding="utf-8-sig") as f:
            current = f.read()
    except FileNotFoundError:
        with open(settings.template_path(), encoding="utf-8-sig") as f:
            current = f.read()
    new_text = settings_import.apply(current, found)
    after = settings.parse(new_text.encode("utf-8"), ctx, name=os.path.basename(path), path=path)
    already = {(p.section, p.message) for p in cfg.errors()}
    new_errors = [p for p in after.errors() if (p.section, p.message) not in already]
    if new_errors:       # never expected: every value was checked on its own above
        print(f"\npc-tidy couldn't put these together without a mistake, so {path} wasn't changed:")
        for p in new_errors:
            print(f"  {after.describe(p)}")
        return 1
    if not ui.ask_yes(f"\nType yes to write these into {path}: "):
        print("Cancelled, settings.ini wasn't changed.")
        return 0
    backup = None
    if os.path.exists(path):
        backup = os.path.join(os.path.dirname(path), f"settings.before-import-{time.strftime('%Y%m%d-%H%M%S')}.ini")
        with open(path, "rb") as src, open(backup, "xb") as dst:
            dst.write(src.read())
    with open(path + ".new", "w", encoding="utf-8", newline="") as f:
        f.write(new_text)
    os.replace(path + ".new", path)
    print(f"Saved {path}." + (f" The one before is kept as {backup}." if backup else ""))
    print("\n".join(settings.check_text(settings.load(ctx))))
    return 0


def cleanup_settings(ctx):
    """[cleanup] for cleanup.ps1, as one line of JSON: the checked values, or ok=false and the refusal
    (exit code 2) if [cleanup] has a mistake. ASCII only, so PowerShell reads it in any code page."""
    cfg = settings.load(ctx, create=True)
    messages = [n for n in cfg.notes if n]
    messages += [f"{cfg.name} warning: {cfg.describe(p)}" for p in cfg.warnings("cleanup")]
    problems = cfg.errors("cleanup")
    if problems:
        messages.append(settings.refusal(cfg, problems, ".\\cleanup.ps1"))
        print(json.dumps({"ok": False, "messages": messages}))
        return settings.EXIT
    out = dict(vars(cfg.section("cleanup")))
    # problem 14: a TEMP that points at a drive root or a folder of yours is never emptied
    out.update(ok=True, messages=messages, temp_ok=safety.temp_problem(ctx) is None)
    print(json.dumps(out))
    return 0


def _print_found(found):
    col = settings._column
    print(f"Settings found in {found.folder}:")
    if found.anything:
        print("\nTo import (each differs from the default):")
    for v in found.values:
        if isinstance(v.raw, list):
            print(col(f"  [{v.section}] {v.key} =", v.source))
            for item in v.raw or ["(empty)"]:
                print(f"      {item}")
        else:
            print(col(f"  [{v.section}] {v.key} = {v.raw}", v.source))
    for e in found.table:
        print(col(f"  [tidy.extensions] {e.ext} = {e.folder}", e.source))
    for ext, source in found.removed_ext:
        print(col(f"  [tidy.extensions] {ext} left out (such files go to the AI)", source))
    for r in found.rules:
        print(col(f"  your own [{r.kind}.*] rule", r.source))
        for key, value in r.fields.items():
            print(f"      {key} = {value}")
    if found.refused:
        print("\nLeft out, because pc-tidy 2.0 refuses them:")
        for r in found.refused:
            hint = " Remove it by hand or through the app." if "extra_paths_to_delete" in r.reason else ""
            print(f"  {r.source}: {r.text}\n      Not imported: {r.reason.rstrip('.')}.{hint}")
    if found.unread:
        print("\nThese lines couldn't be read, so they are left out (their defaults are used):")
        for u in found.unread:
            print(f"  {u.source}: {u.text}")
    if found.kept_builtin:
        print("\nThe old classify.py doesn't have these built-in rules. They stay: your own rules can change "
              "their answers, but not remove them.")
        for what in found.kept_builtin:
            print(f"  {what}")


def main():
    ap = argparse.ArgumentParser(description="pc-tidy: see what fills C: and free space safely. With no command, "
                                             "it guides you through a scan, the report and the fixes.",
                                 epilog="Example: python pc_tidy.py   (or double-click pc-tidy.cmd)")
    ap.add_argument("--no-llm", action="store_true", help="the guided flow without the local AI")
    ap.add_argument("--no-open", action="store_true", help="the guided flow without opening the PDF report")
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("recover", help="check interrupted runs, and decide about half-done moves")
    st = sub.add_parser("status", help="notices from unattended runs, and unfinished work")
    st.add_argument("--dismiss", action="store_true", help="clear the notices after showing them")
    se = sub.add_parser("settings", help="show, check, edit or import settings.ini")
    actions = se.add_subparsers(dest="action")
    actions.add_parser("show", help="every setting, and where its value came from")
    actions.add_parser("check", help="every mistake in settings.ini, with its line number (exit code 2 if any)")
    actions.add_parser("edit", help="open settings.ini in Notepad, and check it when Notepad closes")
    imp = actions.add_parser("import", help="bring in the settings of a pc-tidy folder from before 2.0")
    imp.add_argument("folder", nargs="?", help="the old pc-tidy folder (default: pc-tidy-old next to this one)")
    sub.add_parser("cleanup-settings", help="the [cleanup] settings as JSON, for cleanup.ps1 (exit code 2 if "
                                            "[cleanup] has a mistake)")
    args = ap.parse_args()
    ctx = context.current()
    if args.command == "cleanup-settings":
        return cleanup_settings(ctx)
    if args.command == "recover":
        return recover(ctx)
    if args.command == "status":
        return status(ctx, args.dismiss)
    if args.command == "settings":
        if args.action == "show":
            return settings_show(ctx)
        if args.action == "check":
            return settings_check(ctx)
        if args.action == "edit":
            return settings_edit(ctx)
        if args.action == "import":
            return settings_import_old(ctx, args.folder)
        se.print_help()
        return 0
    return guided.run(no_llm=args.no_llm, no_open=args.no_open)


if __name__ == "__main__":
    sys.exit(main())
