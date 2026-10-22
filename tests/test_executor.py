"""The executor: it does exactly the plan held in memory, re-checks each item, never overwrites, and
keeps going on a full disk only for what can't be undone anyway."""

import json
import os
import sys
import time
from pathlib import Path

import pytest

from pctidy import context, executor, journal
from pctidy.lock import Lock
from pctidy.plan import Plan
from support import contents, write

MB = 1024 ** 2


def moves_plan(tmp_path, names, dest=None):
    source, dest = tmp_path / "Downloads", dest or tmp_path / "Sorted"
    plan = Plan("test", context.current())
    for name in names:
        plan.add_move(write(source / name, f"data of {name}"), dest / name)
    return plan, source, dest


def journal_lines(ctx=None):
    ctx = ctx or context.current()
    out = []
    for path in Path(journal.runs_dir(ctx)).glob("*.jsonl"):
        out += journal.read_lines(path)
    return out


# ---------- the plan in memory is the only list ----------

def test_the_saved_copy_of_a_plan_is_never_what_runs(tmp_path):
    plan, source, dest = moves_plan(tmp_path, ["a.txt", "b.txt"])
    copy = Path(plan.save_copy())
    text = copy.read_text(encoding="utf-8")
    edited = text.replace(str(dest).replace("\\", "\\\\"), str(tmp_path / "Elsewhere").replace("\\", "\\\\"))
    assert edited != text
    copy.write_text(edited, encoding="utf-8")      # someone edits the saved list by hand

    result = executor.execute(plan)

    assert len(result.done) == 2
    assert (dest / "a.txt").exists() and (dest / "b.txt").exists()
    assert not (tmp_path / "Elsewhere").exists()


def test_a_plan_older_than_24_hours_is_refused(tmp_path):
    plan, source, _dest = moves_plan(tmp_path, ["a.txt"])
    plan.created -= 25 * 3600
    with pytest.raises(executor.PlanRefused, match="more than 24 hours ago"):
        executor.execute(plan)
    assert (source / "a.txt").exists()


def test_a_plan_built_under_other_settings_is_refused(tmp_path):
    source = tmp_path / "Downloads"
    plan = Plan("test", context.current(), settings={"archive_after_days": 60})
    plan.add_move(write(source / "a.txt"), tmp_path / "Sorted" / "a.txt")
    with pytest.raises(executor.PlanRefused, match="settings changed"):
        executor.execute(plan, settings={"archive_after_days": 30})
    assert (source / "a.txt").exists()


def test_an_item_changed_since_the_dry_run_is_skipped(tmp_path):
    plan, source, dest = moves_plan(tmp_path, ["a.txt", "b.txt"])
    (source / "a.txt").write_text("edited after the dry run")

    result = executor.execute(plan)

    (skipped,) = result.skipped
    assert skipped.reason == "changed since the dry run"
    assert (source / "a.txt").read_text() == "edited after the dry run"
    assert (dest / "b.txt").exists()


def test_a_target_that_appears_after_the_plan_is_never_overwritten(tmp_path):
    plan, source, dest = moves_plan(tmp_path, ["a.txt"])
    write(dest / "a.txt", b"already there")

    result = executor.execute(plan)

    assert [o.reason for o in result.skipped] == ["the target already exists"]
    assert (dest / "a.txt").read_bytes() == b"already there"
    assert (source / "a.txt").exists()


# ---------- moves to another volume: copy, verify, then delete ----------

def test_a_folder_moves_to_another_volume_and_keeps_its_files(profile, tmp_path, ntfs_volume):
    lectures = profile / "Videos" / "Lectures"
    before = contents(lectures)
    dst = Path(ntfs_volume.root) / tmp_path.name / "Lectures"
    plan = Plan("test", context.current())
    action = plan.add_move(lectures, dst)
    assert not action["same_volume"]

    result = executor.execute(plan)

    assert [o.status for o in result.outcomes] == ["done"], result.outcomes
    assert not lectures.exists()
    assert contents(dst) == before
    assert not Path(str(dst) + executor.PARTIAL).exists()
    assert os.stat(dst / "slides.pdf").st_file_attributes & 1      # still read-only
    kinds = [r["t"] for r in journal_lines() if r.get("n") == 1]
    assert kinds == ["intent", "copied", "done"]


def test_a_copy_that_doesnt_match_is_refused_and_the_source_kept(profile, tmp_path, ntfs_volume, monkeypatch):
    lectures = profile / "Videos" / "Lectures"
    before = contents(lectures)
    dst = Path(ntfs_volume.root) / tmp_path.name / "Lectures"
    plan = Plan("test", context.current())
    plan.add_move(lectures, dst)
    real_write = executor._write

    def flip_one_byte(f, chunk):
        if chunk:
            chunk = bytes([chunk[0] ^ 0xFF]) + chunk[1:]
        real_write(f, chunk)
    monkeypatch.setattr(executor, "_write", flip_one_byte)

    result = executor.execute(plan)

    (skipped,) = result.skipped
    assert "the copy didn't match the original" in skipped.reason
    assert contents(lectures) == before
    assert not dst.exists() and not Path(str(dst) + executor.PARTIAL).exists()


def test_a_target_that_appears_on_the_other_volume_is_never_overwritten(profile, tmp_path, ntfs_volume):
    clip = write(profile / "Videos" / "clip.mp4", b"the clip")
    dst = Path(ntfs_volume.root) / tmp_path.name / "clip.mp4"
    plan = Plan("test", context.current())
    plan.add_move(clip, dst)
    write(dst, b"someone else's clip")

    result = executor.execute(plan)

    assert [o.reason for o in result.skipped] == ["the target already exists"]
    assert dst.read_bytes() == b"someone else's clip"
    assert clip.read_bytes() == b"the clip"


# ---------- deletes ----------

def test_deleting_temp_contents_removes_a_junction_but_never_its_target(profile):
    temp = profile / "AppData" / "Local" / "Temp"
    plan = Plan("test", context.current())
    plan.add_delete_contents(temp)
    assert sorted(Path(a["path"]).name for a in plan.actions) == ["link-to-precious", "tmp1.tmp"]

    result = executor.execute(plan)

    assert len(result.done) == 2
    assert list(temp.iterdir()) == []
    assert (profile / "Precious" / "keep.txt").read_bytes() == b"k" * 1000


def test_a_folder_tree_with_a_junction_inside_is_deleted_without_following_it(profile, tmp_path):
    from support import make_junction
    cache = tmp_path / "node_modules"
    write(cache / "pkg" / "index.js", b"js")
    make_junction(cache / "pkg" / "linked", profile / "Precious")
    plan = Plan("test", context.current())
    plan.add_delete(cache)

    result = executor.execute(plan)

    assert [o.status for o in result.outcomes] == ["done"]
    assert not cache.exists()
    assert (profile / "Precious" / "keep.txt").exists()


def test_read_only_files_are_cleared_and_deleted(profile):
    readonly = profile / "Downloads" / "readonly.zip"
    plan = Plan("test", context.current())
    plan.add_delete(readonly)
    result = executor.execute(plan)
    assert [o.status for o in result.outcomes] == ["done"]
    assert not readonly.exists()


def test_a_file_in_use_is_skipped_and_counted(tmp_path):
    busy = write(tmp_path / "busy.log", b"open in another program")
    plan = Plan("test", context.current())
    plan.add_delete(busy)
    with open(busy, "rb"):        # Python opens files without letting others delete them
        result = executor.execute(plan)
    assert [o.reason for o in result.skipped] == ["it is in use by another program"]
    assert result.in_use == 1
    assert busy.exists()


def test_twenty_io_errors_in_a_row_stop_the_run(tmp_path, monkeypatch):
    plan = Plan("test", context.current())
    for i in range(25):
        plan.add_delete(write(tmp_path / "files" / f"{i}.tmp"))

    def failing_disk(path):
        raise OSError(5, "the disk can't be read")
    monkeypatch.setattr(executor, "_remove_file", failing_disk)

    result = executor.execute(plan)

    assert len(result.with_status("failed")) == 20
    stopped = result.with_status("skipped")
    assert len(stopped) == 5 and "stopped after 20 errors in a row" in stopped[0].reason
    assert len(list((tmp_path / "files").iterdir())) == 25


# ---------- the journal on a full disk ----------

class DiskFull:
    """A stand-in for open() in journal.py: journal files are on a disk with no room left, until a test
    sets DiskFull.full = False."""
    full = True

    def __init__(self, path, mode, buffering=-1):
        self.f = open(path, mode, buffering=buffering)

    def write(self, data):
        if DiskFull.full:
            raise OSError(None, "There is not enough space on the disk", None, 112)     # ERROR_DISK_FULL
        return self.f.write(data)

    def __getattr__(self, name):
        return getattr(self.f, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.f.close()


def test_when_the_journal_cant_be_written_moves_stop_and_deletes_continue(tmp_path, monkeypatch):
    source = tmp_path / "Downloads"
    plan = Plan("test", context.current())
    plan.add_move(write(source / "photo.jpg", b"photo"), tmp_path / "Sorted" / "photo.jpg")
    plan.add_delete(write(tmp_path / "Temp" / "cache.tmp", b"cache"))
    monkeypatch.setattr(journal, "open", DiskFull, raising=False)

    result = executor.execute(plan)

    move, delete = result.outcomes
    assert move.status == "skipped"
    assert move.reason == "C: is too full to record the move safely. Clear some low-risk items first " \
                          "(they're at the top of the list)."
    assert (source / "photo.jpg").exists() and not (tmp_path / "Sorted").exists()
    assert delete.status == "done" and not (tmp_path / "Temp" / "cache.tmp").exists()
    assert not result.journal_on_disk


def test_buffered_delete_records_reach_the_disk_once_a_write_succeeds(tmp_path, monkeypatch):
    ctx = context.current()
    jr = journal.Journal(ctx, {"run": "20260101-000000-1-abcd", "command": "test"})
    monkeypatch.setattr(journal, "open", DiskFull, raising=False)
    monkeypatch.setattr(DiskFull, "full", True)
    assert not jr.start()
    for i in range(3):
        jr.note({"t": "done", "n": i})
    assert Path(jr.path).read_bytes() == b""

    DiskFull.full = False      # a delete made room
    assert jr.flush()
    jr.close_file()
    assert [r["t"] for r in journal.read_lines(jr.path)] == ["run", "done", "done", "done"]


def test_a_journal_line_cut_short_is_ignored(tmp_path):
    path = write(tmp_path / "run.jsonl", b'{"t": "run", "seq": 0}\n{"t": "intent", "seq": 1, "n"')
    assert [r["t"] for r in journal.read_lines(path)] == ["run"]


# ---------- tools: closed stdin, a time limit, re-measured numbers ----------

def test_a_tool_that_never_ends_is_stopped_at_its_time_limit_and_the_lock_released(monkeypatch):
    monkeypatch.setitem(executor.TOOLS, "conda_clean", executor.Tool(
        "conda", lambda a: [sys.executable, "-c", "import time; time.sleep(120)"], False, 2, None))
    plan = Plan("test", context.current())
    plan.add_tool("conda_clean")

    started = time.monotonic()
    result = executor.execute(plan)

    assert time.monotonic() - started < 60
    assert [o.reason for o in result.skipped] == ["conda didn't finish in 2 seconds, so pc-tidy stopped it"]
    Lock(context.current(), "test").acquire(wait=0).release()      # free again


def test_a_non_interactive_tool_gets_closed_stdin(tmp_path, monkeypatch):
    seen = tmp_path / "stdin.txt"
    code = f"import sys; open({str(seen)!r}, 'w').write(repr(sys.stdin.read()))"
    monkeypatch.setitem(executor.TOOLS, "conda_clean", executor.Tool(
        "conda", lambda a: [sys.executable, "-c", code], False, 60, None))
    plan = Plan("test", context.current())
    plan.add_tool("conda_clean")

    result = executor.execute(plan)

    assert [o.status for o in result.outcomes] == ["done"]
    assert seen.read_text() == "''"


def test_a_count_only_action_whose_numbers_changed_is_skipped_with_the_new_numbers(monkeypatch):
    emptied = []
    monkeypatch.setitem(executor.COUNT_ONLY, "empty_recycle_bin", (
        "Recycle Bin", lambda a: {"items": 320, "bytes": int(2.6 * 1024 ** 3)}, emptied.append))
    plan = Plan("test", context.current())
    plan.add_count_only("empty_recycle_bin", {"items": 12, "bytes": 5 * MB})

    result = executor.execute(plan)

    assert [o.reason for o in result.skipped] == [
        "Recycle Bin changed since the dry run (now 320 items, 2.6 GB). Run the fix again to see it."]
    assert emptied == []


def test_the_plan_record_holds_who_where_and_when(tmp_path):
    plan, _source, _dest = moves_plan(tmp_path, ["a.txt"])
    record = json.loads(Path(plan.save_copy()).read_text(encoding="utf-8").split("\n", 2)[2])
    assert record["computer"] == os.environ.get("COMPUTERNAME")
    assert record["sid"].startswith("S-1-5-")
    assert record["elevated"] in (True, False)
    assert len(record["settings_hash"]) == 64
    assert abs(record["created"] - time.time()) < 60
    assert record["actions"][0]["fp"]["file_id"]
