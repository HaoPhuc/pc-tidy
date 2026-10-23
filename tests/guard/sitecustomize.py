"""Loaded by Python at startup when tests/guard is on PYTHONPATH. Does nothing unless a test set
PCTIDY_TEST_GUARD_ROOTS, in which case pc-tidy may only change files inside those folders."""

import os

if os.environ.get("PCTIDY_TEST_GUARD_ROOTS"):
    import pctidy_guard

    pctidy_guard.install()
    pctidy_guard.set_roots(os.environ["PCTIDY_TEST_GUARD_ROOTS"].split(os.pathsep),
                           os.environ.get("PCTIDY_TEST_GUARD_LOG"))
    if os.environ.get("PCTIDY_TEST_NO_NETWORK"):
        pctidy_guard.install_network()
