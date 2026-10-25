r"""What .\cleanup.ps1 does: every switch that is yes in the [cleanup] section of settings.ini, and the
three lists under it, as one plan that the executor runs (python pc_tidy.py cleanup [--dry-run]).

It is the guided flow's path, with the switches deciding what is picked:
- the dry run lists every item now, from the disk as it is, and O opens the whole list
- you type yes, and each high-risk fix (the Recycle Bin, your extra paths) asks once more
- the executor does only what is on the list, re-checking and journaling every item. Uninstalls run
  last, with the lock released, because their own windows may wait for you.
cleanup.ps1 itself only checks for an administrator window, finds Python and runs this, so one
engine deletes files, not two.

In extra_paths_to_delete only * and ? are wildcards: D:\Photos [2019] is that one folder.
"""

import json
import os
import shutil
import subprocess

from . import context, guided, safety, settings
from .fixes import installers_fix, temp_age

COMMAND = ".\\cleanup.ps1"
NOT_IN_TESTS = "not in the tests, where it would act on this PC itself"

# the switches that empty folders pc-tidy otherwise protects, in the order cleanup.ps1 always did them:
# (switch, its row of safety.ALLOWED, the part of that row's folders it covers or None for all of
# them, its own title or None for the row's)
_TABLE = (
    ("pip_cache", "caches", "\\pip\\", "pip download cache"),
    ("uv_cache", "caches", "\\uv\\", "uv download cache"),
    ("gpu_shader_cache", "shader-caches", None, None),
    ("temp_files", "temp-user", None, None),
    ("temp_files", "temp-windows", None, None),
    ("crash_dumps", "crash-dumps-user", None, None),
    ("crash_dumps", "crash-dumps-windows", None, None),
)


def _row(id, title, risk, action, scope="user"):
    return {"id": id, "title": title, "risk": risk, "action": action, "scope": scope}


def rows(ctx, cfg, notes):
    """The fixes the switches and lists of [cleanup] ask for. Anything switched on that can't be done
    becomes a line in `notes` instead."""
    c = cfg.section("cleanup")
    out = []
    allowed = {r.fix: r for r in safety.ALLOWED}
    values = {"temp": ctx.temp, "system_root": ctx.system_root, "local": ctx.local_appdata}
    for switch, fix, part, title in _TABLE:
        if not getattr(c, switch):
            continue
        if fix == "temp-user" and safety.temp_problem(ctx):
            notes.append(safety.temp_problem(ctx))
            continue
        row = allowed[fix]
        places = [p.format(**values) for p in row.patterns if part is None or part in p]
        kind = "contents" if row.kind == "contents" else "patterns"
        days = temp_age(fix, cfg)
        title = (title or row.title) + (f" older than {days} days" if days else "")
        out.append(_row(fix if part is None else switch.replace("_", "-"), title, "Low",
                        {"delete": kind, "places": places, "min_age_days": days}, row.scope))

    if c.installers:
        fix = installers_fix(ctx, cfg)
        if fix:
            out.append(_row("installers", fix.title, fix.risk, fix.action))
        else:
            notes.append(f"Installers: none older than {c.installers_min_age_days} days in "
                         f"{', '.join(c.installer_folders)}.")

    if c.conda_cache or c.conda_envs_to_remove:
        conda = shutil.which("conda")
        if not conda:
            notes.append("conda isn't available in this window, so the conda steps are left out. Run "
                         ".\\cleanup.ps1 in a window where conda works.")
        else:
            if c.conda_cache:
                out.append(_row("conda-pkgs", "conda package cache", "Low", {"tool": "conda_clean"}))
            envs = _conda_envs(conda) if c.conda_envs_to_remove and not ctx.sandbox else None
            for name in c.conda_envs_to_remove:
                if envs is not None and name.lower() not in envs:
                    notes.append(f"conda environment {name} isn't there ('conda env list' shows yours), so it "
                                 "is left out.")
                    continue
                out.append(_row(f"conda-env:{name}", f"conda environment {name}", "Medium",
                                {"tool": "conda_env_remove", "name": name}))

    if c.hibernation:
        out.append(_row("hibernation", "Turn off hibernation", "Medium", {"tool": "hibernation_off"}, "machine"))
    if c.recycle_bin:
        out.append(_row("recycle-bin", "Recycle Bin", "High", {"count_only": "empty_recycle_bin"}))
    for path in c.extra_paths_to_delete:
        out.append(_row(f"extra:{path}", f"Delete {path}", "High", {"delete": "matches", "places": [path]}))

    if c.apps_to_uninstall:
        if not shutil.which("winget"):
            notes.append("winget isn't available, so these apps are left out. Uninstall them in Settings > Apps: "
                         + ", ".join(c.apps_to_uninstall))
        else:
            installed = None if ctx.sandbox else _installed_apps()
            for name in c.apps_to_uninstall:
                if installed is not None and name.lower() not in installed:
                    notes.append(f"No app is installed under the exact name '{name}', so it is left out. Copy the "
                                 "name from Settings > Apps.")
                    continue
                out.append(_row(f"app:{name}", f"Uninstall {name}", "Medium",
                                {"tool": "winget_uninstall", "name": name}))
    return out


def _conda_envs(conda):
    """The names of conda's environments, in lower case, or None if conda can't say."""
    try:
        proc = subprocess.run([conda, "env", "list", "--json"], stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=120)
        return {os.path.basename(p).lower() for p in json.loads(proc.stdout)["envs"]}
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return None


def _installed_apps():
    """The names of the installed apps, in lower case (the registry, as the report reads it: offline
    and instant), or None if they can't be read."""
    try:
        import classify
        return {a["name"].lower() for a in classify.installed_apps()}
    except (ImportError, OSError):
        return None


def _why_not_here(ctx, row):
    """Why this run leaves a fix out, or None. Run as another account than the person signed in,
    everything but the Windows folders belongs to someone else (problem 23). The tests also leave
    out what would act on this PC itself: a tool, the Recycle Bin, folders outside their own."""
    other = ctx.other_account()
    if other and row["scope"] == "user":
        return f"it belongs to {other[1]}, and this window runs as {other[0]}"
    if not ctx.sandbox:
        return None
    a = row["action"]
    if "tool" in a or "count_only" in a or not any(ctx.in_sandbox(p) for p in a.get("places", [])):
        return NOT_IN_TESTS
    return None


def run(dry_run=False, ctx=None):
    """All of .\\cleanup.ps1 after its admin check. Returns the exit code: 0 if everything was done
    (or there was nothing to do), 3 if something was skipped, 1 on an error, 2 if [cleanup] has a
    mistake, 4 if another pc-tidy run kept the lock."""
    ctx = ctx or context.current()
    cfg = settings.load(ctx, create=True)
    for note in cfg.notes:
        print(note)
    for p in cfg.warnings("cleanup"):
        print(f"{cfg.name} warning: {cfg.describe(p)}")
    problems = cfg.errors("cleanup")
    if problems:
        print(settings.refusal(cfg, problems, COMMAND))
        return settings.EXIT
    other = ctx.other_account()
    if other:
        print(f"This window runs as {other[0]}, but {other[1]} is signed in, so only the Windows folders are "
              f"cleaned. For the rest, run .\\cleanup.ps1 from {other[1]}'s own account.")
    notes = []
    wanted = rows(ctx, cfg, notes)
    if not wanted and not notes:
        print("Nothing is switched on in the [cleanup] section of settings.ini. Change it with: "
              "python pc_tidy.py settings edit")
        return 0
    doable = []
    for row in wanted:
        why = _why_not_here(ctx, row)
        if why:
            notes.append(f"Left out {row['title']}: {why}.")
        else:
            doable.append(row)
    for note in notes:
        print(note)
    if not doable:
        print("Nothing was changed.")
        return 0

    dry = guided.DryRun(ctx, cfg, doable)
    empty = [r["title"] + (f" ({guided.newer_stay(dry.young[r['id']])})" if dry.young[r["id"]] else "")
             for r in dry.rows if not dry.by_fix.get(r["id"])]
    if empty:
        print(f"Nothing to remove in: {', '.join(empty)}.")
    if dry.empty:
        for path, why in dry.refused:
            print(f"Left out {path}: {why}.")
        print("Nothing was changed: there is nothing to remove.")
        return 0
    dry.rows = [dict(r, n=n) for n, r in enumerate([r for r in dry.rows if dry.by_fix.get(r["id"])], 1)]
    header = ("\nDRY RUN: nothing will be changed. This is exactly what .\\cleanup.ps1 would do:" if dry_run
              else "\nDRY RUN: this is exactly what will happen. Nothing has changed yet.")
    listing = guided.show_dry_run(dry, header)
    if any(a["op"] == "run_tool" for a in dry.deletes.actions):
        print("conda, powercfg and winget decide for themselves what they remove, so that number leaves them out.")
    if dry_run:
        print("\nDry run done. Nothing was changed. Run .\\cleanup.ps1 without -DryRun to do it: it shows this "
              "list again, from the disk as it is then, and asks you to type yes.")
        return 0
    return guided.confirm_and_run(ctx, dry, listing)
