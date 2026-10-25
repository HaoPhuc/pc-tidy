r"""
pc_tidy.py - see what fills C: and free space on it, safely. With no command it guides you: a scan of C:,
the report with the fixes for C: ranked lowest risk first, the fixes you pick, the exact list (the dry
run), your yes, and a summary. Double-clicking pc-tidy.cmd does the same.

Usage:
    python pc_tidy.py                    # the guided flow
    python pc_tidy.py --no-llm --no-open # ... without the local AI, and without opening the PDF
    python pc_tidy.py recover            # check runs that were interrupted, and decide about half-done moves
    python pc_tidy.py undo               # put the last moves off C: back
    python pc_tidy.py status             # what unattended runs couldn't do, and anything left unfinished
    python pc_tidy.py status --dismiss   # ... and then clear those notices
    python pc_tidy.py settings show      # every setting, and where its value came from
    python pc_tidy.py settings check     # every mistake in settings.ini, with its line number
    python pc_tidy.py settings edit      # open settings.ini in Notepad, and check it when Notepad closes
    python pc_tidy.py settings import ..\pc-tidy-old
                                         # bring in the settings of a pc-tidy folder from before 2.0
    python pc_tidy.py cleanup --dry-run  # what .\cleanup.ps1 -DryRun shows: the exact list for [cleanup]
    python pc_tidy.py cleanup            # what .\cleanup.ps1 does: that list, then your yes, then the run
    python pc_tidy.py selftest           # check that pc-tidy can work on this PC (setup.cmd runs it)
"""

import argparse
import os
import subprocess
import sys
import time

from pctidy import cleanup, context, errors, guided, journal, lock, recovery, safety, settings, settings_import, ui

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
              f"those paths until you decide: run {context.command('recover')} again.")
        return 3
    return 0


def undo(ctx):
    """Put the last moves off C: back, as python move_off_c.py --undo does (pc-tidy.exe has no scripts)."""
    import move_off_c
    try:      # finish what an interrupted run left first, as move_off_c.py does
        recovery.at_start(ctx, "pc_tidy.py undo", ask=ui.ask)
    except lock.Busy:
        print(lock.BUSY_MESSAGE)
        return lock.BUSY_EXIT
    return move_off_c.undo(ctx)


def status(ctx, dismiss=False):
    notices = journal.notices(ctx)
    waiting = recovery.undecided(ctx)
    if not notices and not waiting:
        print("Nothing needs your attention.")
        return 0
    if waiting:
        print(f"{len(waiting)} interrupted move(s) wait for your decision ({context.command('recover')}):")
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
            print(f"Clear these with: {context.command('status --dismiss')}")
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
                  f"{context.command('settings check')}")
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


def selftest(ctx):
    """Check that pc-tidy can work on this PC. Each check says ok or what to do. Exit code 1 if one fails."""
    import platform
    import tempfile
    from pctidy import executor
    from pctidy.plan import Plan
    failed = []

    class Problem(Exception):
        """A check that found something to fix: its message is shown as it is."""

    def check(name, test):
        try:
            detail = test()
        except Problem as e:
            failed.append(name)
            print(f"  FAIL  {name}: {e}")
        except Exception as e:
            failed.append(name)
            print(f"  FAIL  {name}: {errors.explain(e)}")
        else:
            print(f"  ok    {name}" + (f" ({detail})" if detail else ""))

    def python():
        if sys.version_info < (3, 9):
            raise Problem(f"this is Python {platform.python_version()}; pc-tidy needs 3.9 or newer")
        return f"{platform.python_version()}, {sys.executable}"

    def windows():
        if os.name != "nt":
            raise Problem("pc-tidy runs on Windows 10 and 11 only")
        return platform.platform()

    def packages():
        if context.FROZEN:
            return "none needed: pc-tidy.exe saves its report as a web page"
        import docx  # noqa: F401
        import pypdf  # noqa: F401
        import reportlab  # noqa: F401
        return "reportlab, pypdf, python-docx"

    def data_folder():
        folder = ctx.folder()
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, f"selftest-{os.getpid()}.tmp")
        with open(probe, "x", encoding="utf-8") as f:
            f.write("pc-tidy self-test")
        os.remove(probe)
        return folder

    def the_lock():
        lock.Lock(ctx, "pc_tidy.py selftest").acquire(wait=0, say=lambda *_: None).release()

    def the_settings():
        cfg = settings.load(ctx, create=True)
        problems = cfg.errors()
        return f"{len(problems)} mistake(s): {context.command('settings check')}" if problems else cfg.path

    def dry_run_and_run():
        problem = safety.temp_problem(ctx)
        if problem:
            raise Problem(problem)
        folder = tempfile.mkdtemp(prefix="pc-tidy-selftest-", dir=ctx.temp)
        probe = os.path.join(folder, "probe.txt")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("pc-tidy deletes this file to check that it can")
        plan = Plan("selftest", ctx)
        plan.add_delete(probe)
        result = executor.execute(plan, ctx, say=lambda *_: None)
        os.rmdir(folder)
        if [o.status for o in result.outcomes] != ["done"] or os.path.exists(probe):
            raise Problem(f"the test file wasn't deleted: {result.outcomes}")
        return "a test file of its own in your Temp folder, listed, then deleted"

    print("pc-tidy self-test")
    check("Python", python)
    check("Windows", windows)
    check("the packages for the PDF report", packages)
    check("its data folder", data_folder)
    check("the lock that keeps two runs apart", the_lock)
    check("settings.ini", the_settings)
    check("a dry run and a run", dry_run_and_run)
    if failed:
        print(f"\n{len(failed)} check(s) failed. Fix them, then run: {context.command('selftest')}")
        return 1
    print("\nEverything works. " + ("Double-click pc-tidy.exe to start." if context.FROZEN
                                   else "Double-click pc-tidy.cmd to start."))
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
                                 epilog=ui.examples(__doc__), formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-llm", action="store_true", help="the guided flow without the local AI")
    ap.add_argument("--no-open", action="store_true", help="the guided flow without opening the PDF report")
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("recover", help="check interrupted runs, and decide about half-done moves")
    sub.add_parser("undo", help="put the last moves off C: back (what python move_off_c.py --undo does)")
    st = sub.add_parser("status", help="notices from unattended runs, and unfinished work")
    st.add_argument("--dismiss", action="store_true", help="clear the notices after showing them")
    se = sub.add_parser("settings", help="show, check, edit or import settings.ini")
    actions = se.add_subparsers(dest="action")
    actions.add_parser("show", help="every setting, and where its value came from")
    actions.add_parser("check", help="every mistake in settings.ini, with its line number (exit code 2 if any)")
    actions.add_parser("edit", help="open settings.ini in Notepad, and check it when Notepad closes")
    imp = actions.add_parser("import", help="bring in the settings of a pc-tidy folder from before 2.0")
    imp.add_argument("folder", nargs="?", help="the old pc-tidy folder (default: pc-tidy-old next to this one)")
    cl = sub.add_parser("cleanup", help="what .\\cleanup.ps1 does: every [cleanup] switch that is yes, after the "
                                        "exact list and your yes (exit code 2 if [cleanup] has a mistake)")
    cl.add_argument("--dry-run", action="store_true", help="only show the list, and change nothing")
    sub.add_parser("selftest", help="check that pc-tidy can work on this PC (setup.cmd runs it)")
    args = ap.parse_args()
    ctx = context.current()
    if args.command == "cleanup":
        return cleanup.run(dry_run=args.dry_run, ctx=ctx)
    if args.command == "selftest":
        return selftest(ctx)
    if args.command == "recover":
        return recover(ctx)
    if args.command == "undo":
        return undo(ctx)
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
    code = errors.run(main, "pc_tidy.py")
    if context.FROZEN and len(sys.argv) == 1:
        # pc-tidy.exe double-clicked: its window closes when it ends (pc-tidy.cmd has pause for this)
        ui.ask("\nPress Enter to close.")
    sys.exit(code)
