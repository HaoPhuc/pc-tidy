"""Path helpers shared by the safety core: comparing Windows paths, resolving links, and matching
wildcards.

Only * and ? are wildcards anywhere in pc-tidy. [ and ] are part of a name, as they are in Windows:
D:\\Photos [2019] is that one folder. fnmatch, glob and PowerShell's -Path all read [2019] as "one
of 2, 0, 1 or 9", so every match goes through match() and expand() here, which never do.
"""

import fnmatch
import os

from ._win import long_path, plain_path

WILDCARDS = "*?"


def norm(path):
    """An absolute, case-folded path without a trailing backslash (except for a drive root)."""
    text = plain_path(os.fspath(path)).replace("/", "\\")
    if len(text) == 2 and text[1] == ":":
        text += "\\"       # 'D:' alone means 'the current folder on D:', never what anyone meant
    return os.path.normcase(os.path.abspath(text))


def inside(path, folder, strict=False):
    """True if path is folder or is somewhere below it. Both are normalised first."""
    path, folder = norm(path), norm(folder)
    if path == folder:
        return not strict
    return path.startswith(folder.rstrip("\\") + "\\")


def is_root(path):
    """A drive root ('D:\\'), a volume root ('\\\\?\\Volume{...}\\') or a share ('\\\\server\\share')."""
    p = norm(path)
    if p.endswith(":\\") and len(p) == 3:
        return True
    if p.startswith("\\\\?\\volume{") and p.count("\\") <= 4:
        return True
    if p.startswith("\\\\") and not p.startswith("\\\\?\\") and len([x for x in p[2:].split("\\") if x]) <= 2:
        return True
    try:
        return os.path.ismount(p)       # a volume mounted in a folder is that volume's root
    except (OSError, ValueError):
        return False


def has_wildcard(path):
    return any(c in os.fspath(path) for c in WILDCARDS)


def part_matches(name, pattern):
    """One name against one part of a pattern, ignoring case. A [ is just a [."""
    return fnmatch.fnmatchcase(name.lower(), pattern.lower().replace("[", "[[]"))


def match(path, pattern):
    """A path against a pattern, one part at a time, so * never matches a backslash."""
    a, b = path.split("\\"), pattern.split("\\")
    return len(a) == len(b) and all(part_matches(x, y) for x, y in zip(a, b))


def expand(pattern):
    """The paths that exist and match a pattern, sorted. A path without a wildcard is returned as
    it is, if it exists."""
    drive, rest = os.path.splitdrive(os.path.abspath(os.fspath(pattern)))
    found = [drive + "\\"]
    for part in [x for x in rest.split("\\") if x]:
        step = []
        for base in found:
            if has_wildcard(part):
                try:
                    names = os.listdir(lp(base))
                except OSError:
                    continue
                step += [os.path.join(base, n) for n in names if part_matches(n, part)]
            elif os.path.lexists(lp(os.path.join(base, part))):
                step.append(os.path.join(base, part))
        found = step
    return sorted(found, key=str.lower)


def real(path):
    """The path with junctions, symlinks and subst letters resolved, or the path itself if that fails."""
    try:
        return plain_path(os.path.realpath(plain_path(os.fspath(path))))
    except (OSError, ValueError):
        return os.fspath(path)


def real_parent(path):
    """The path with its parent folders resolved but not the last part, so a link is the link itself."""
    path = plain_path(os.fspath(path))
    parent, name = os.path.split(os.path.abspath(path))
    if not name:
        return path
    return os.path.join(real(parent), name)


def lp(path):
    return long_path(path)
