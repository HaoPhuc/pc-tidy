"""M8's polish: --help everywhere, one error handler with a log, progress that stays out of logs,
a scan of any depth, reports never saved where OneDrive uploads them, the self-test, and nothing
on the network but this PC."""

import os

import pytest

import disk_report
import scanner
from pctidy import context, errors
from support import ROOT, long, write

COMMANDS = ["pc_tidy.py", "disk_report.py", "move_off_c.py", "tidy_folder.py"]


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_has_help_with_examples(run, command):
    result = run.python(command, "--help")
    assert result.returncode == 0, result.output
    assert "examples:" in result.stdout and f"python {command}" in result.stdout
    assert "--debug" in result.stdout


def test_cleanup_ps1_has_help(run):
    result = run.powershell(ROOT / "cleanup.ps1", "--help")
    assert result.returncode == 0, result.output
    assert "SYNOPSIS" in result.output and ".\\cleanup.ps1 -DryRun" in result.output


def data_log(profile):
    return profile / "AppData" / "Local" / "pc-tidy" / "logs" / "pc-tidy.log"


@pytest.mark.parametrize("command", COMMANDS)
def test_a_crash_says_what_happened_without_a_traceback(run, profile, command):
    result = run.python(command, env={"PCTIDY_TEST_FAIL": "crash"})

    assert result.returncode == 1
    assert "Traceback" not in result.output
    assert (f"{command} stopped: something went wrong that pc-tidy didn't expect (RuntimeError: a test failure). "
            "That is a mistake in pc-tidy, not something you did.") in result.stdout
    assert f"The details are in {data_log(profile)}. To see them here, run it again with --debug." in result.stdout
    log = data_log(profile).read_text(encoding="utf-8")
    assert "Traceback" in log and "RuntimeError: a test failure" in log and command in log


def test_debug_shows_the_traceback(run):
    result = run.python("disk_report.py", "--debug", env={"PCTIDY_TEST_FAIL": "crash"})
    assert result.returncode == 1 and "Traceback (most recent call last)" in result.output


def test_a_missing_package_says_to_run_setup(run):
    result = run.python("tidy_folder.py", env={"PCTIDY_TEST_FAIL": "missing"})
    assert "a part it needs isn't installed (reportlab). Double-click setup.cmd in the pc-tidy folder" in result.stdout


def test_when_the_log_cant_be_written_it_says_so_in_one_line(run, profile):
    write(profile / "AppData" / "Local" / "pc-tidy" / "logs", "a file where the logs folder should be")
    result = run.python("move_off_c.py", env={"PCTIDY_TEST_FAIL": "crash"})

    assert result.returncode == 1 and "Traceback" not in result.output
    assert "(The details couldn't be saved in pc-tidy's log either. Run it again with --debug" in result.stdout


def test_the_log_keeps_a_few_old_ones(profile, monkeypatch):
    monkeypatch.setattr(errors, "LOG_BYTES", 10)        # every entry starts a new log
    for i in range(4):
        assert errors.log("test", RuntimeError(f"error {i}"))
    logs = sorted(p.name for p in data_log(profile).parent.iterdir())
    assert logs == ["pc-tidy.log", "pc-tidy.log.1", "pc-tidy.log.2", "pc-tidy.log.3"]


def test_progress_stays_out_of_output_that_isnt_a_console(run, profile):
    result = run.python("disk_report.py", "--path", profile, "--out", "r.txt", "--no-llm", "--no-open")
    assert result.returncode == 0, result.output
    assert "\r" not in result.stderr and "files," not in result.stderr


def test_a_folder_tree_of_any_depth_is_scanned(tmp_path):
    """Problem 12: the walk used to be recursive, and a deep enough tree overflowed the stack."""
    folder = tmp_path / "deep"
    path = long(folder)
    os.mkdir(path)
    for _ in range(1200):           # deeper than Python's recursion limit; os.makedirs is recursive itself
        path += "\\d"
        os.mkdir(path)
    with open(path + "\\bottom.bin", "wb") as f:
        f.write(b"x" * 1234)

    scan = scanner.Scan([long(folder)])

    assert scan.sizes[long(folder)] == 1234 and scan.stats["files"] == 1 and scan.stats["denied"] == 0


# ---------- reports never where OneDrive uploads them ----------
def test_a_report_isnt_saved_into_a_folder_onedrive_syncs(run, profile, tmp_path):
    onedrive = tmp_path / "OneDrive"
    run.work = onedrive / "Documents"
    run.work.mkdir(parents=True)

    result = run.python("disk_report.py", "--path", profile, "--no-llm", "--no-open",
                        env={"OneDrive": str(onedrive)})

    assert result.returncode == 0, result.output
    reports = profile / "AppData" / "Local" / "pc-tidy" / "reports"
    assert (f"The report is saved to {reports} instead of here: this folder is synced by OneDrive "
            f"({onedrive})") in result.stdout
    assert len(list(reports.glob("disk_report-*.pdf"))) == 1
    assert not list(run.work.glob("*.pdf"))


def test_an_out_you_give_inside_onedrive_is_written_with_a_warning(run, profile, tmp_path):
    onedrive = tmp_path / "OneDrive"
    onedrive.mkdir()
    out = onedrive / "report.txt"
    result = run.python("disk_report.py", "--path", profile, "--out", out, "--no-llm", "--no-open",
                        env={"OneDrive": str(onedrive)})

    assert result.returncode == 0, result.output
    assert f"Note: {out} is in {onedrive}, which OneDrive uploads" in result.stdout
    assert out.exists()


def test_a_report_isnt_saved_in_system32(tmp_path, monkeypatch, profile):
    windows = tmp_path / "Windows"
    (windows / "System32").mkdir(parents=True)
    monkeypatch.chdir(windows / "System32")
    ctx = context.Context(dict(os.environ, SystemRoot=str(windows)))

    (out,) = disk_report.report_files(["disk_report.pdf"], False, ctx)

    assert os.path.dirname(out) == ctx.folder("reports")


# ---------- the self-test, and nothing on the network but this PC ----------
def test_selftest(run):
    result = run.python("pc_tidy.py", "selftest")
    assert result.returncode == 0, result.output
    assert result.stdout.count("  ok    ") == 7
    assert "Everything works." in result.stdout


DIALS = r'''
import socket, sys
for host in ("8.8.8.8", "localhost"):
    try:
        socket.create_connection((host, 9), timeout=2).close()
        print("CONNECTED", host)
    except PermissionError:
        print("BLOCKED", host)
    except OSError:
        print("REFUSED", host)       # nothing listens there: but the connection was allowed
'''


def test_the_network_tripwire_works(run, tmp_path):
    driver = write(tmp_path / "dial.py", DIALS)
    result = run.python(driver, env={"PCTIDY_TEST_NO_NETWORK": "1"}, expect_violations=True)
    assert "BLOCKED 8.8.8.8" in result.stdout and "BLOCKED localhost" not in result.stdout
    assert len(result.violations) == 1


def test_nothing_goes_on_the_network_but_this_pc(run, profile, tmp_path):
    """The whole guided flow and the report, with the AI switched on (pointed at a port of this PC
    where nothing listens): every connection must stay on this PC."""
    write(tmp_path / "settings.ini", "[ai]\nenabled = yes\nurl = http://127.0.0.1:9\n")
    env = {"PCTIDY_TEST_NO_NETWORK": "1"}
    report = run.python("disk_report.py", "--path", profile, "--out", "r.txt", "--no-open", env=env)
    guided = run.python("pc_tidy.py", "--no-open", input="\n\n", env=env)

    assert report.returncode == 0 and guided.returncode == 0, report.output + guided.output
    assert "Ollama isn't running" in report.stdout          # it tried, on this PC only
