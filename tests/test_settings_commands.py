"""settings.ini (M3) as the commands use it: made on first run, flags win over it, and a mistake
stops only the commands that read its section."""

import shutil
import subprocess
import time

from pctidy import settings
from support import FIXTURES, ROOT, Runner, make_fake_profile, snapshot, write
from test_known_problems import FAKE_LLM
from test_tidy_folder import EXPECTED

TEMPLATE = ROOT / "settings.template.ini"


def template_with(old, new):
    """The template with one line changed, and the number of that line."""
    lines = TEMPLATE.read_text(encoding="utf-8").splitlines()
    (n,) = [i for i, line in enumerate(lines, 1) if line == old]
    lines[n - 1] = new
    return "\n".join(lines) + "\n", n


def test_a_missing_settings_file_is_made_from_the_template(run, tmp_path):
    assert not (tmp_path / "settings.ini").exists()

    result = run.python("pc_tidy.py", "settings", "check")

    assert result.returncode == 0, result.output
    assert (tmp_path / "settings.ini").read_bytes() == TEMPLATE.read_bytes()
    assert f"Made {tmp_path / 'settings.ini'} from settings.template.ini" in result.stdout
    assert "settings.ini is fine: no mistakes." in result.stdout


def test_settings_show_says_where_each_value_comes_from(run, tmp_path):
    write(tmp_path / "settings.ini", "[move]\nmin_gb = 5\n[report]\nmax_depth = deep\n")

    result = run.python("pc_tidy.py", "settings", "show")

    assert result.returncode == 0, result.output
    lines = [" ".join(line.split()) for line in result.stdout.splitlines()]
    assert "min_gb = 5.0 settings.ini line 2" in lines
    assert "min_gb = 1.0 default" in lines                       # [report] min_gb
    assert "max_depth = 8 default (settings.ini line 4 has a mistake)" in lines
    assert "settings.ini line 4 in [report]: max_depth must be a whole number, got 'deep'" in lines


def test_a_flag_wins_over_settings_ini_which_wins_over_the_default(run, tmp_path):
    dest = tmp_path / "dest"
    write(tmp_path / "settings.ini", "[move]\nmin_gb = 5\n")
    from_file = run.python("move_off_c.py", "--to", dest)
    from_flag = run.python("move_off_c.py", "--to", dest, "--min-gb", "2")
    (tmp_path / "settings.ini").write_text("[move]\n", encoding="utf-8")
    default = run.python("move_off_c.py", "--to", dest)

    assert "Nothing of 5 GB or more that is safe to move." in from_file.stdout, from_file.output
    assert "Nothing of 2 GB or more that is safe to move." in from_flag.stdout, from_flag.output
    assert "Nothing of 1 GB or more that is safe to move." in default.stdout, default.output


def test_tidy_sorts_the_folders_named_in_settings_ini_unless_given(run, profile, tmp_path):
    inbox, out = tmp_path / "Inbox", tmp_path / "Out"
    write(inbox / "photo.jpg", b"jpeg")
    write(tmp_path / "settings.ini", f"[tidy]\nsource = {inbox}\ndest = {out}\n")

    from_file = run.python("tidy_folder.py", "--no-llm")
    from_flags = run.python("tidy_folder.py", "--no-llm", "--source", profile / "Downloads", "--dest", out)

    assert from_file.returncode == 0, from_file.output
    assert "photo.jpg  ->  Pictures\\photo.jpg" in from_file.stdout
    assert f"Preview only, 1 items. Run again with --apply to move them into {out}." in from_file.stdout
    assert f"Preview only, {len(EXPECTED)} items" in from_flags.stdout, from_flags.output


# ---------- an error stops only what it affects ----------
def test_a_typo_in_cleanup_stops_nothing_that_does_not_read_it(run, profile, tmp_path):
    """M3's isolation test. The part where .\\cleanup.ps1 exits 2 naming the line is in
    test_cleanup_ps1.py (it needs admin), and through pc_tidy.py cleanup-settings here."""
    text, line = template_with("pip_cache = yes", "pip_cahce = no")
    write(tmp_path / "settings.ini", text)
    downloads, sorted_dir = profile / "Downloads", tmp_path / "Sorted"

    check = run.python("pc_tidy.py", "settings", "check")
    tidy = run.python("tidy_folder.py", "--source", downloads, "--dest", sorted_dir, "--no-llm", "--apply",
                      stdin=subprocess.DEVNULL)          # the weekly task: nobody to answer
    move = run.python("move_off_c.py", "--path", profile / "Videos" / "Lectures", "--to", tmp_path / "dest",
                      input="no\n")

    assert check.returncode == settings.EXIT
    assert f"settings.ini line {line} in [cleanup]: unknown key 'pip_cahce'. Did you mean 'pip_cache'?" \
        in check.stdout, check.output
    assert ".\\cleanup.ps1 won't run until it's fixed." in check.stdout
    cleanup = run.python("pc_tidy.py", "cleanup-settings")
    assert cleanup.returncode == settings.EXIT, cleanup.output
    assert f"line {line} in [cleanup]: unknown key 'pip_cahce'" in cleanup.stdout
    assert tidy.returncode == 0, tidy.output
    assert f"Moved {len(EXPECTED)} items" in tidy.stdout
    assert move.returncode == 0, move.output
    assert "Cancelled, nothing moved." in move.stdout
    # the guided flow (M6) offers a fix unless a key it reads has a mistake: the Temp fix stays
    cfg = settings.load()
    assert cfg.key_errors("cleanup", "temp_files") == [] and cfg.key_errors("cleanup", "pip_cache")


def test_a_mistake_in_tidy_stops_the_weekly_sort_with_a_notice(run, profile, tmp_path):
    write(tmp_path / "settings.ini", "[tidy]\narchive_after_days = sixty\n")
    downloads = profile / "Downloads"
    before = snapshot(downloads)

    result = run.python("tidy_folder.py", "--source", downloads, "--dest", tmp_path / "Sorted", "--no-llm",
                        "--apply", stdin=subprocess.DEVNULL)
    status = run.python("pc_tidy.py", "status")
    undo = run.python("tidy_folder.py", "--source", downloads, "--dest", tmp_path / "Sorted", "--undo")

    assert result.returncode == settings.EXIT, result.output
    assert "settings.ini line 2 in [tidy]: archive_after_days must be a whole number, got 'sixty'" in result.stdout
    assert snapshot(downloads) == before
    assert (f"The sort didn't run on {time.strftime('%Y-%m-%d')}: settings.ini line 2 in [tidy]: "
            "archive_after_days must be a whole number" in status.stdout), status.output
    assert undo.returncode == 0 and "Nothing to undo." in undo.stdout, undo.output     # undo only needs dest


def test_a_mistake_in_ai_sorts_by_the_rules_only_with_a_notice(run, profile, tmp_path):
    """The stand-in LLM files report [1].pdf (no rule for .pdf) under Other, but only when [ai] is fine."""
    driver = write(tmp_path / "fake_llm.py", FAKE_LLM)
    downloads, sorted_dir = profile / "Downloads", tmp_path / "Sorted"
    write(tmp_path / "settings.ini", "[ai]\nurl = http://example.com:11434\n")

    broken = run.python(driver, "--source", downloads, "--dest", sorted_dir, "--apply", stdin=subprocess.DEVNULL)
    status = run.python("pc_tidy.py", "status")

    assert broken.returncode == 0, broken.output
    assert "settings.ini has a mistake in [ai], so files are sorted by the rules only" in broken.stdout
    assert (downloads / "report [1].pdf").exists()
    assert (sorted_dir / "Pictures" / "photo.jpg").exists()
    assert "used the rules only, without the AI: settings.ini line 2 in [ai]: url must point at this PC" \
        in status.stdout, status.output

    (tmp_path / "settings.ini").write_text("[ai]\nurl = http://localhost:11434\n", encoding="utf-8")
    fixed = run.python(driver, "--source", downloads, "--dest", sorted_dir, "--apply", stdin=subprocess.DEVNULL)

    assert fixed.returncode == 0, fixed.output
    assert (sorted_dir / "Other" / "report [1].pdf").exists()


def test_a_mistake_in_move_stops_move_off_c_but_not_its_undo(run, profile, tmp_path):
    write(tmp_path / "settings.ini", "[move]\nmin_gb = big\n")
    lectures = profile / "Videos" / "Lectures"

    result = run.python("move_off_c.py", "--path", lectures, "--to", tmp_path / "dest", input="yes\n")
    undo = run.python("move_off_c.py", "--undo")

    assert result.returncode == settings.EXIT
    assert "move_off_c.py didn't start" in result.stdout
    assert "settings.ini line 2 in [move]: min_gb must be a number, got 'big'" in result.stdout
    assert lectures.exists()
    assert undo.returncode == 0 and "Nothing to undo." in undo.stdout, undo.output


def test_the_report_goes_on_with_a_banner_and_uses_your_rules(tmp_path):
    """A rule of yours overrides a built-in one in a fixture scan, and a broken rule is skipped."""
    home = make_fake_profile(tmp_path / "big", big_cache=True)
    write(tmp_path / "settings.ini",
          "[label.pip]\npattern = /appdata/local/pip/cache$\nwhat = My pip cache\ntodo = Clear it once a year\n"
          "kind = clean\n"
          "[label.broken]\npattern = (\nwhat = Never shown\n"
          "[report]\ntop_files = lots\n")

    result = Runner(home, tmp_path, [tmp_path]).python(
        "disk_report.py", "--path", home, "--out", "report.txt", "--no-llm", "--no-open")

    assert result.returncode == 0, result.output
    text = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert "SETTINGS.INI HAS MISTAKES" in text
    assert "settings.ini line 7 in [label.broken]: pattern isn't a valid pattern" in text
    assert "settings.ini line 10 in [report]: top_files must be a whole number, got 'lots'" in text
    clean_up = text.split("THINGS YOU CAN CLEAN UP")[1].split("\n\n")[0]
    assert "My pip cache. Clear it once a year" in clean_up        # instead of the built-in label
    assert "pip download cache" not in text
    assert "Never shown" not in text


# ---------- settings edit ----------
EDITOR = r'''
import sys
from pathlib import Path

path = Path(sys.argv[1])
runs = path.parent / "editor-runs"
n = len(runs.read_text()) + 1 if runs.exists() else 1
runs.write_text("x" * n)
text = path.read_text(encoding="utf-8")
if n == 1:
    text = text.replace("pip_cache = yes", "pip_cahce = no")     # a typo
else:
    text = text.replace("pip_cahce = no", "pip_cache = no")      # fixed
path.write_text(text, encoding="utf-8")
'''


def test_settings_edit_checks_the_file_when_the_editor_closes(run, tmp_path):
    editor = write(tmp_path / "editor.py", EDITOR)

    result = run.python("pc_tidy.py", "settings", "edit", input="yes\n", env={"PCTIDY_TEST_EDITOR": str(editor)})

    assert result.returncode == 0, result.output
    first, second = result.stdout.split("Type yes to open it again and fix it: ")
    assert "unknown key 'pip_cahce'. Did you mean 'pip_cache'?" in first
    assert "settings.ini is fine: no mistakes." in second
    assert "pip_cache = no" in (tmp_path / "settings.ini").read_text(encoding="utf-8")


# ---------- settings import ----------
def old_folder(tmp_path):
    """A copy of tests/fixtures/old_folder: the scripts of the last release before 2.0, edited the way a
    user would (inline comments in the SETTINGS block, the Steam example as a real entry, a rule added to
    classify.py, an app rule changed, constants changed)."""
    folder = tmp_path / "pc-tidy-old"
    shutil.copytree(FIXTURES / "old_folder", folder)
    return folder


def test_an_old_folder_is_imported_and_the_result_passes_the_check(run, profile, tmp_path):
    folder = old_folder(tmp_path)
    program_files = tmp_path / "Program Files (x86)"      # where the Steam example points, in the sandbox
    program_files.mkdir()

    result = run.python("pc_tidy.py", "settings", "import", folder, input="yes\n",
                        env={"ProgramFiles(x86)": str(program_files)})

    assert result.returncode == 0, result.output
    steam = program_files / "Steam" / "userdata" / "*" / "3472040" / "local" / "tmp" / "replay.bin"
    assert (f"Not imported: extra_paths_to_delete '{steam}' is inside Program Files, which pc-tidy doesn't "
            "change. Remove it by hand or through the app.") in result.stdout, result.output
    assert "Not imported: categories can't include To-Delete" in result.stdout
    assert "doesn't have these built-in rules" not in result.stdout      # it changed one, it removed none
    assert "settings.ini is fine: no mistakes." in result.stdout

    cfg = settings.load()
    assert cfg.errors() == [] and cfg.warnings() == []
    cleanup = cfg.section("cleanup")
    assert (cleanup.temp_files, cleanup.hibernation, cleanup.pip_cache) == (False, True, True)
    assert cleanup.installer_folders == [str(profile / "Downloads"), str(profile / "Installers")]
    assert cleanup.conda_envs_to_remove == ["old-env", "test-env"]
    assert cleanup.apps_to_uninstall == ["Docker Desktop"]
    assert cleanup.extra_paths_to_delete == [str(profile / "old-backups" / "*.zip")]
    assert cfg.value("report", "max_depth") == 6
    assert cfg.value("ai", "model") == "qwen2.5:7b"
    assert cfg.value("move", "min_gb") == 2.0
    tidy = cfg.section("tidy")
    assert tidy.archive_after_days == 30
    assert tidy.dest == str(profile / "Sorted Files")
    assert tidy.categories == settings.DEFAULT_CATEGORIES[:-1] + ["Recipes", "Other"]
    assert cfg.table("tidy.extensions") == dict(settings.DEFAULT_EXTENSIONS, **{".dwg": "3D models"})
    rules = {r.kind: r.fields for r in cfg.good_rules()}
    assert rules["label"]["pattern"].pattern == "^[a-z]:/games/old$"
    assert (rules["label"]["what"], rules["label"]["kind"]) == ("Old game installers", "clean")
    assert (rules["category"]["pattern"].pattern, rules["category"]["category"]) == ("^d:/lab$", "Personal files")
    assert rules["apptype"]["match"].pattern.endswith("|onshape")
    assert rules["apptype"]["type"] == "Dev & science tools"

    text = (tmp_path / "settings.ini").read_text(encoding="utf-8")
    assert "# pip download cache\npip_cache = yes\n" in text            # the template's comments stay
    assert [line for line in text.splitlines() if line.startswith("temp_files")] == ["temp_files = no"]
    assert run.python("pc_tidy.py", "settings", "check").returncode == 0

    again = run.python("pc_tidy.py", "settings", "import", folder, input="yes\n",
                       env={"ProgramFiles(x86)": str(program_files)})
    assert "Nothing to import: settings.ini already has every setting" in again.stdout, again.output
    assert (tmp_path / "settings.ini").read_text(encoding="utf-8") == text


def test_an_import_writes_nothing_without_a_yes(run, tmp_path):
    folder = old_folder(tmp_path)

    result = run.python("pc_tidy.py", "settings", "import", folder, input="no\n")

    assert result.returncode == 0, result.output
    assert "Cancelled, settings.ini wasn't changed." in result.stdout
    assert (tmp_path / "settings.ini").read_bytes() == TEMPLATE.read_bytes()


def test_lines_that_cannot_be_read_are_listed_not_guessed(tmp_path):
    from pctidy import context, settings_import
    folder = tmp_path / "old"
    write(folder / "cleanup.ps1", '$TempFiles = $ture\n$PipCache = $false  # "quoted" # comment\n'
                                  '$AppsToUninstall = $myApps\n$InstallerFolders = @("$HOME\\Downloads")\n'
                                  "$CondaEnvsToRemove = @('it''s')\n")
    write(folder / "tidy_folder.py", 'import os\nSOURCE = Path(os.environ["X"])\nSKIP_NEWER_THAN_MIN = 5\n')

    found = settings_import.read_folder(str(folder), context.current())

    assert [(u.source, u.text) for u in found.unread] == [
        ("cleanup.ps1 line 1", "$TempFiles = $ture"),
        ("cleanup.ps1 line 3", "$AppsToUninstall = $myApps"),
        ("cleanup.ps1 line 4", '$InstallerFolders = @("$HOME\\Downloads")'),
        ("tidy_folder.py line 2", 'SOURCE = Path(os.environ["X"])'),
    ]
    assert [(v.key, v.raw) for v in found.values] == [("pip_cache", "no"), ("conda_envs_to_remove", ["it's"]),
                                                      ("skip_newer_than_min", "5")]
