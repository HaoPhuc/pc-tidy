"""pc-tidy's safety core.

Every change goes through one path: a dry run builds an exact plan (plan.py), you type yes, and the
executor (executor.py) does only what is on that plan, re-checking each item (safety.py, volume.py)
and recording each step (journal.py) while it holds the lock (lock.py). recovery.py settles runs
that were interrupted, and undo.py puts moves back. settings.py reads settings.ini, and
settings_import.py brings in the settings of a pc-tidy folder from before 2.0.

Everything here uses the standard library only, and runs on Python 3.9 and newer.
"""
