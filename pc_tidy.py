r"""
pc_tidy.py - pc-tidy's own commands. The guided flow that will start with no arguments comes later.

Usage:
    python pc_tidy.py recover            # check runs that were interrupted, and decide about half-done moves
    python pc_tidy.py status             # what unattended runs couldn't do, and anything left unfinished
    python pc_tidy.py status --dismiss   # ... and then clear those notices
"""

import argparse
import sys

from pctidy import context, journal, lock, recovery, ui

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


def main():
    ap = argparse.ArgumentParser(description="pc-tidy: see what fills C: and free space safely.")
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("recover", help="check interrupted runs, and decide about half-done moves")
    st = sub.add_parser("status", help="notices from unattended runs, and unfinished work")
    st.add_argument("--dismiss", action="store_true", help="clear the notices after showing them")
    args = ap.parse_args()
    ctx = context.current()
    if args.command == "recover":
        return recover(ctx)
    if args.command == "status":
        return status(ctx, args.dismiss)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
