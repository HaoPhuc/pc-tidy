"""One progress line on stderr, for the steps that take minutes: the scan, measuring the fixes,
checking duplicates, copying to another drive, asking the AI.

It updates at most once a second, and shows nothing at all when stderr isn't a console (a log file,
the task scheduler, a test), so it never fills a file with lines.
"""

import sys
import time

WIDTH = 78


def on_console():
    try:
        return sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


class Line:
    def __init__(self, every=1.0):
        self.every, self.last, self.shown = every, 0.0, False
        self.on = on_console()

    def show(self, text, force=False):
        """Show text in place of the last line (at most once a second unless force)."""
        if not self.on:
            return
        now = time.monotonic()
        if not force and now - self.last < self.every:
            return
        self.last, self.shown = now, True
        sys.stderr.write("\r  " + text[:WIDTH - 2].ljust(WIDTH - 2))
        sys.stderr.flush()

    def clear(self):
        if self.on and self.shown:
            sys.stderr.write("\r" + " " * WIDTH + "\r")
            sys.stderr.flush()
            self.shown = False


def duration(seconds):
    """'40 s', '3 min', '1 h 5 min'."""
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds} s"
    minutes = round(seconds / 60)
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60} min"
