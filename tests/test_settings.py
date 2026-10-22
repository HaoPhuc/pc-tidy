"""settings.ini (M3): the loader, its checks and your own rules, read in this process."""

import ast
import os
from types import SimpleNamespace

import pytest

import classify
import llm
from pctidy import context, settings
from support import FIXTURES, ROOT


def parse(text, ctx=None, data=None):
    return settings.parse(data if data is not None else text.encode("utf-8"), ctx or context.current())


def errors(s, *sections):
    return [s.describe(p) for p in s.errors(*sections)]


def warnings(s):
    return [s.describe(p) for p in s.warnings()]


def plain_ctx(tmp_path):
    """A context outside the test sandbox, for checks that only read (the safety check would refuse
    anything outside the sandbox before it got to the reason the test is about)."""
    home = tmp_path / "profile"
    return context.Context({"USERPROFILE": str(home), "LOCALAPPDATA": str(home / "AppData" / "Local"),
                            "SystemDrive": "C:", "SystemRoot": "C:\\Windows"})


# ---------- the template ----------
def test_the_template_has_no_mistakes_and_equals_the_defaults():
    s = settings.parse((ROOT / "settings.template.ini").read_bytes(), context.current())

    assert s.problems == []
    for section, keys in settings.SECTIONS.items():
        for k in keys:
            assert (section, k.name) in s.entries, f"[{section}] {k.name} is missing from the template"
            assert s.value(section, k.name) == s.default(section, k.name), (section, k.name)
    assert s.tables["tidy.extensions"][0] == settings.DEFAULT_EXTENSIONS
    assert s.rules == []         # the rule examples are comments


def test_every_key_in_the_template_has_a_comment():
    lines = (ROOT / "settings.template.ini").read_text(encoding="utf-8").splitlines()
    _preamble, chunks = settings._split(lines)
    for c in chunks:
        if c.key in settings.TABLES:
            continue
        for key, line, _first, _more in c.keys:
            above = lines[line - 2].strip()
            assert above.startswith("#"), f"line {line}: {key} has no comment above it"


def test_the_defaults_are_todays_values():
    """The values the scripts had before M3, from the old folder fixture (made from the release before 2.0,
    with a user's edits, which are taken out again here)."""
    old = {}
    for name in ("disk_report.py", "move_off_c.py", "tidy_folder.py", "llm.py"):
        tree = ast.parse((FIXTURES / "old_folder" / name).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                try:
                    old[(name, node.targets[0].id)] = ast.literal_eval(node.value)
                except ValueError:
                    pass
    s = parse("")
    rules = dict(old[("tidy_folder.py", "RULES")])
    del rules[".dwg"]                                       # the user's edit
    assert settings.DEFAULT_EXTENSIONS == rules
    assert s.value("tidy", "categories") == [c for c in old[("tidy_folder.py", "CATEGORIES")]
                                             if c not in ("Recipes", "To-Delete")]
    assert s.value("tidy", "skip_newer_than_min") == old[("tidy_folder.py", "SKIP_NEWER_THAN_MIN")]
    assert s.value("tidy", "rename_with_ai") is old[("tidy_folder.py", "RENAME_WITH_LLM")]
    assert s.value("tidy", "archive_after_days") == 60
    for key, const in (("min_gb", "MIN_GB"), ("top_files", "TOP_FILES"), ("cleanup_min_gb", "CLEANUP_MIN_GB")):
        assert s.value("report", key) == old[("disk_report.py", const)]
    assert s.value("report", "max_depth") == 8
    assert s.value("ai", "max_folders") == old[("disk_report.py", "LLM_MAX_FOLDERS")]
    assert s.value("ai", "max_apps") == old[("disk_report.py", "LLM_MAX_APPS")]
    assert s.value("ai", "url") == old[("llm.py", "OLLAMA")]
    assert s.value("ai", "model") == "llama3.2:3b"
    assert s.value("move", "dest_folder_name") == old[("move_off_c.py", "DEST_FOLDER_NAME")]
    assert s.value("move", "min_gb") == 1.0
    assert s.value("tidy", "source") == os.path.join(os.environ["USERPROFILE"], "Downloads")


# ---------- reading ----------
def test_every_mistake_is_listed_at_once_with_its_line(tmp_path):
    data = b"\xef\xbb\xbf" + (FIXTURES / "settings" / "every_mistake.ini").read_bytes()     # a BOM from Notepad

    s = parse(None, plain_ctx(tmp_path), data=data)

    found = errors(s)
    assert found[:8] == [
        "settings.ini line 4 in [report]: max_depth is set twice, on lines 3 and 4. Delete one of them.",
        "settings.ini line 8 in [cleanup]: unknown key 'pip_cahce'. Did you mean 'pip_cache'?",
        "settings.ini line 10 in [cleanup]: extra_paths_to_delete 'D:\\*' is refused: a wildcard directly under a "
        "drive root could empty the drive. List the folders you mean, e.g. D:\\old-backups.",
        "settings.ini line 17 in [tidy]: %NOPE% in '%NOPE%\\Downloads' isn't set on this PC. "
        "(Write %% for a % sign in a name.)",
        "settings.ini line 20 in [tidy]: categories can't include To-Delete: cleanup deletes installers there, "
        "so the AI must never file anything there.",
        "settings.ini line 23 in [ai]: url must point at this PC (localhost, 127.0.0.1 or [::1]), got "
        "'http://192.168.1.20:11434'. pc-tidy never sends your file names off this PC.",
        "settings.ini line 28 in [move]: [move] appears twice, on lines 25 and 28. Merge them into one.",
        "settings.ini line 36 in [category.lab]: pattern isn't a valid pattern: missing ), unterminated "
        "subpattern at character 8",
    ]
    assert found[8].startswith("settings.ini line 37 in [category.lab]: category must be one of: System, ")
    assert len(found) == 9
    assert warnings(s) == ["settings.ini line 13 in [cleanup]: if '#old' is a comment, put it on its own line."]
    # what has no mistake is still read, exactly as written
    assert s.value("cleanup", "temp_files") is False
    assert s.value("cleanup", "installer_folders") == ["D:\\C# projects\\installers", "D:\\Setups #old"]
    assert s.value("tidy", "dest") == "C:\\new"
    (games,) = s.good_rules()
    assert games.fields["pattern"].pattern == "^[a-z]:/games/(old|new)=1$"
    assert games.fields["what"] == "Games: old = done"


def test_a_bom_is_harmless():
    s = parse(None, data=b"\xef\xbb\xbf[report]\r\nmax_depth = 5\r\n")
    assert s.problems == []
    assert s.value("report", "max_depth") == 5


def test_a_duplicate_key_is_a_mistake_in_its_section_only():
    s = parse("[cleanup]\npip_cache = no\npip_cache = yes\n[tidy]\narchive_after_days = 5\n")

    assert errors(s) == ["settings.ini line 3 in [cleanup]: pip_cache is set twice, on lines 2 and 3. "
                         "Delete one of them."]
    assert s.value("tidy", "archive_after_days") == 5
    assert s.value("cleanup", "pip_cache") is True        # the default, not the last line


def test_a_duplicate_section_is_a_mistake_in_that_section_only():
    s = parse("[move]\nmin_gb = 2\n[tidy]\narchive_after_days = 5\n[move]\nmin_gb = 3\n")

    assert errors(s) == ["settings.ini line 5 in [move]: [move] appears twice, on lines 1 and 5. Merge them into one."]
    assert errors(s, "tidy") == []
    assert s.value("tidy", "archive_after_days") == 5
    assert s.value("move", "min_gb") == 1.0


def test_a_typo_names_the_line_and_the_key_it_was_meant_to_be():
    s = parse("[cleanup]\ntemp_files = no\npip_cahce = no\n")

    assert errors(s) == ["settings.ini line 3 in [cleanup]: unknown key 'pip_cahce'. Did you mean 'pip_cache'?"]
    # what the guided flow (M6) needs: a typo in one switch doesn't make another one unavailable
    assert s.key_errors("cleanup", "temp_files") == []
    assert s.key_errors("cleanup", "pip_cache")
    assert s.value("cleanup", "temp_files") is False


def test_a_backslash_is_just_a_backslash():
    s = parse("[tidy]\ndest = C:\\new\n")
    assert s.problems == []
    assert s.value("tidy", "dest") == "C:\\new"


def test_environment_variables_are_filled_in_and_an_undefined_one_is_a_mistake():
    s = parse("[tidy]\nsource = %NOPE%\\Downloads\ndest = %userprofile%\\100%% done\n")

    assert errors(s) == ["settings.ini line 2 in [tidy]: %NOPE% in '%NOPE%\\Downloads' isn't set on this PC. "
                         "(Write %% for a % sign in a name.)"]
    assert s.value("tidy", "dest") == os.path.join(os.environ["USERPROFILE"], "100% done")
    assert s.value("tidy", "source") == s.default("tidy", "source")


def test_a_wildcard_directly_under_a_drive_root_is_refused(tmp_path):
    s = parse("[cleanup]\nextra_paths_to_delete =\n    D:\\old-backups\\*.zip\n    D:\\*\n", plain_ctx(tmp_path))

    assert errors(s) == ["settings.ini line 4 in [cleanup]: extra_paths_to_delete 'D:\\*' is refused: a wildcard "
                         "directly under a drive root could empty the drive. List the folders you mean, "
                         "e.g. D:\\old-backups."]


def test_a_path_inside_program_files_is_refused(tmp_path):
    steam = "C:\\Program Files (x86)\\Steam\\userdata\\*\\3472040\\local\\tmp\\replay.bin"
    s = parse(f"[cleanup]\nextra_paths_to_delete =\n    {steam}\n", plain_ctx(tmp_path))
    assert errors(s) == [f"settings.ini line 3 in [cleanup]: extra_paths_to_delete '{steam}' is inside "
                         "Program Files, which pc-tidy doesn't change"]


def test_only_an_ai_on_this_pc_is_allowed():
    s = parse("[ai]\nurl = http://example.com:11434\n")
    assert errors(s) == ["settings.ini line 2 in [ai]: url must point at this PC (localhost, 127.0.0.1 or [::1]), "
                         "got 'http://example.com:11434'. pc-tidy never sends your file names off this PC."]
    assert s.value("ai", "url") == "http://localhost:11434"
    for url in ("http://127.0.0.1:11434", "http://[::1]:11434", "http://LOCALHOST:8080/"):
        assert errors(parse(f"[ai]\nurl = {url}\n")) == [], url
    with pytest.raises(ValueError):          # llm.py refuses it too, whoever calls it
        llm.configure(SimpleNamespace(model="m", url="http://10.0.0.5:11434", timeout_seconds=5))


def test_to_delete_can_never_be_a_category_for_the_ai():
    s = parse("[tidy]\ncategories =\n    Work\n    to-delete\n")
    assert errors(s) == ["settings.ini line 4 in [tidy]: categories can't include To-Delete: cleanup deletes "
                         "installers there, so the AI must never file anything there."]
    assert "To-Delete" not in s.value("tidy", "categories")


def test_a_comment_after_yes_no_or_a_number():
    s = parse("[cleanup]\ntemp_files = no  # later\n[report]\nmax_depth = 5 ; five\n")
    assert s.problems == []
    assert s.value("cleanup", "temp_files") is False
    assert s.value("report", "max_depth") == 5


def test_a_hash_in_a_path_is_kept_and_a_space_before_it_is_warned():
    s = parse("[tidy]\nsource = D:\\C# projects\ndest = D:\\Code #2\\inbox\n")

    assert s.errors() == []
    assert s.value("tidy", "source") == "D:\\C# projects"
    assert s.value("tidy", "dest") == "D:\\Code #2\\inbox"
    assert warnings(s) == ["settings.ini line 3 in [tidy]: if '#2\\inbox' is a comment, put it on its own line."]


def test_wrong_values_say_what_was_expected():
    s = parse("[cleanup]\ntemp_files = ture\n[report]\nmax_depth = 500\nmin_gb = 1,5\ntop_files = eight\n"
              "[tidy]\nsource = Downloads\ndest = $env:USERPROFILE\\Sorted\n[move]\ndest_folder_name = D:\\x\n")
    assert errors(s) == [
        "settings.ini line 2 in [cleanup]: temp_files must be yes or no, got 'ture'",
        "settings.ini line 4 in [report]: max_depth must be from 1 to 100, got 500",
        "settings.ini line 5 in [report]: min_gb must be written with a dot, like 1.5, got '1,5'",
        "settings.ini line 6 in [report]: top_files must be a whole number, got 'eight'",
        "settings.ini line 8 in [tidy]: source must be a full path that starts with a drive, like D:\\Stuff, "
        "got 'Downloads'",
        "settings.ini line 9 in [tidy]: '$env:USERPROFILE\\Sorted' is PowerShell. In settings.ini write "
        "%USERPROFILE%\\Sorted",
        "settings.ini line 11 in [move]: dest_folder_name must be a folder name, so it can't contain "
        "\\ / : * ? \" < > |, got 'D:\\x'",
    ]


def test_a_line_above_the_first_section_is_reported_and_ignored():
    s = parse("temp_files = no\n[cleanup]\npip_cache = no\n")
    assert errors(s) == ["settings.ini line 1: 'temp_files = no' is above the first [section] line, so it is "
                         "ignored. Move it into the section it belongs to."]
    assert s.errors("cleanup") == []
    assert s.value("cleanup", "temp_files") is True
    assert s.value("cleanup", "pip_cache") is False


def test_a_misspelt_section_breaks_the_section_it_was_meant_to_be():
    s = parse("[cleanpu]\ntemp_files = no\n[tidy]\narchive_after_days = 5\n")
    assert errors(s, "cleanup") == ["settings.ini line 1 in [cleanup]: unknown section [cleanpu]. "
                                    "Did you mean [cleanup]?"]
    assert errors(s, "tidy") == []


def test_a_line_that_is_not_utf8_is_a_mistake_in_its_section():
    s = parse(None, data=b"[report]\nmax_depth = 5\n[tidy]\nsource = C:\\Users\\Jos\xe9\\Downloads\n")
    assert errors(s) == ["settings.ini line 4 in [tidy]: this line isn't UTF-8 text. In Notepad: File > Save as, "
                         "Encoding: UTF-8."]
    assert s.value("report", "max_depth") == 5


def test_a_list_item_that_is_not_indented():
    s = parse("[cleanup]\ninstaller_folders =\nD:\\Installers\n")
    assert errors(s) == ["settings.ini line 3 in [cleanup]: 'D:\\Installers' isn't 'key = value'. List items go on "
                         "indented lines under their key."]


def test_where_each_value_comes_from():
    s = parse("[report]\nmax_depth = deep\ntop_files = 5\n[cleanup]\ninstaller_folders =\n    D:\\a\n    D:\\b\n")
    assert s.where("report", "max_depth") == "default (settings.ini line 2 has a mistake)"
    assert s.value("report", "max_depth") == 8               # a read-only command uses the default
    assert s.where("report", "top_files") == "settings.ini line 3"
    assert s.where("report", "min_gb") == "default"
    assert s.where("cleanup", "installer_folders") == "settings.ini lines 6-7"
    assert s.value("cleanup", "installer_folders") == ["D:\\a", "D:\\b"]


# ---------- your own rules ----------
def test_a_rule_pattern_keeps_its_colons_and_equals_signs():
    s = parse("[label.games]\npattern = ^[a-z]:/games/(old|new)=1$\nwhat = Games: old = done\nkind = clean\n")
    assert s.problems == []
    (rule,) = s.good_rules()
    assert rule.fields["pattern"].pattern == "^[a-z]:/games/(old|new)=1$"
    assert rule.fields["what"] == "Games: old = done"
    assert (rule.kind, rule.name, rule.fields["kind"], rule.fields["todo"]) == ("label", "games", "clean", "")


def test_a_bad_rule_is_a_mistake_with_its_line_and_is_skipped():
    s = parse("[category.lab]\npattern = ^d:/lab(\ncategory = Personal files\n"
              "[apptype.cad]\nmatch = onshape\ntype = Dev tools\n"
              "[label.empty]\nwhat = Nothing to match\n")
    assert errors(s) == [
        "settings.ini line 2 in [category.lab]: pattern isn't a valid pattern: missing ), unterminated subpattern "
        "at character 8",
        "settings.ini line 6 in [apptype.cad]: type must be one of: " + ", ".join(classify.APP_TYPES)
        + ". Got 'Dev tools'. Did you mean 'Dev & science tools'?",
        "settings.ini line 7 in [label.empty]: [label.empty] needs a line 'pattern = ...'.",
    ]
    assert s.good_rules() == []


def test_an_unknown_category_suggests_the_right_one():
    s = parse("[category.lab]\npattern = ^d:/lab$\ncategory = personal\n")
    (message,) = errors(s)
    assert message.endswith("Got 'personal'. Did you mean 'Personal files'?")


def test_capital_letters_in_a_pattern_are_warned_about():
    s = parse("[category.lab]\npattern = ^D:/Lab$\ncategory = Personal files\n"
              "[category.digits]\npattern = /\\d+\\D$\ncategory = Other\n")
    assert warnings(s) == ["settings.ini line 2 in [category.lab]: pattern has capital letters, but paths are "
                           "matched in lower case, so they never match. Write it in lower case."]
    assert len(s.good_rules()) == 2          # a warning doesn't skip the rule


@pytest.fixture
def user_rules():
    yield classify.use_rules
    classify.use_rules([])


def test_your_rules_come_before_the_built_in_ones(user_rules):
    s = parse("[label.pip]\npattern = /appdata/local/pip/cache$\nwhat = My pip cache\nkind = info\n"
              "[category.lectures]\npattern = /videos/lectures$\ncategory = Dev & science tools\n"
              "[apptype.chrome]\nmatch = chrome\ntype = Utilities\n"
              "[apptype.cad]\nmatch = onshape\ntype = Dev & science tools\n")
    assert s.problems == []
    pip_cache = "C:\\Users\\me\\AppData\\Local\\pip\\cache"
    assert classify.known(pip_cache).what == "pip download cache"
    assert classify.app_type("Google Chrome") == "Browsers"
    assert classify.app_type("Onshape") is None

    user_rules(s.good_rules())

    assert classify.known(pip_cache).what == "My pip cache"
    assert classify.known(pip_cache).kind == "info"
    assert classify.app_type("Google Chrome") == "Utilities"
    assert classify.app_type("Onshape", "PTC") == "Dev & science tools"
    assert classify.known("C:\\Users\\me\\AppData\\Local\\Temp").what == "Temporary files"     # the rest stay


def test_your_category_rule_changes_the_category_in_a_scan(profile, user_rules):
    import scanner
    scan = scanner.Scan([str(profile)])
    lectures = str(profile / "Videos" / "Lectures")
    assert classify.folder_categories(scan)[1][lectures] != classify.DEV

    user_rules(parse("[category.lectures]\npattern = /videos/lectures$\ncategory = Dev & science tools\n")
               .good_rules())

    assert classify.folder_categories(scan)[1][lectures] == classify.DEV
