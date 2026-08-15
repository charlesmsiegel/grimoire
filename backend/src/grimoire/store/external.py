"""What the app can notice about *external* writers, without a watcher (#35).

Reads already cope with external edits: every request re-reads from
``paths.home()``, and ``statcache`` keys its memos on ``(path, mtime_ns,
size)`` precisely so another process syncing the store folder invalidates
them. What no read notices is the wreckage a sync client leaves behind when
two devices wrote the same record: Syncthing renames the loser to
``pact.sync-conflict-<date>-<id>.md``, Dropbox to ``pact (Winifred's
conflicted copy 2026-01-01).md``, and a hand merge leaves ``pact.md.orig``. None of those
names is a record id the app will ever resolve, so the file sits in the store
being read by nothing and shown by nothing while the user believes their edit
survived.

This module finds them and says where they are. It never opens, moves,
renames or deletes one: which side of a conflict to keep is a question only
the person who made both edits can answer, so this mirrors ``sync.py`` --
flag, and let the user choose -- rather than resolving anything.

Deliberately on demand, not resident. The rebuilt app runs no background
machinery, and a scan cheap enough to run when the Configuration page asks
needs no daemon to stay useful. Its cost is one directory walk of the store,
which is why both the walk and its results are bounded (``MAX_ENTRIES`` /
``MAX_RESULTS``) and why a truncated answer says so: "found nothing" and
"stopped looking" must not read the same.

What it does *not* detect, stated so nobody reads a clean scan as a
guarantee: renamed-away copies with no marker in the name (iCloud's ``pact
2.md``, Drive's ``pact (1).md``) are indistinguishable from records a user
deliberately named that way, and flagging them would cry wolf on an ordinary
library. Two devices whose edits the sync client merged silently, or clobbered
without leaving a copy, leave nothing on disk to find at all.
"""

from __future__ import annotations

import fnmatch
import os
from datetime import datetime, timezone
from pathlib import Path

from . import backups, paths

# Patterns are matched case-folded against the file name, so a store synced
# from a case-insensitive volume matches the same way a case-sensitive one
# does. `fnmatchcase` on a lowered name rather than `fnmatch`, whose own
# case-folding follows the *host* platform -- the answer must not depend on
# which device is asking.
RULES: tuple[tuple[str, str], ...] = (
    ("syncthing", "*.sync-conflict-*"),
    ("dropbox", "*conflicted copy*"),
    ("merge", "*.orig"),
)

# Never walked, at the store root only: `.cache/` is derived data the app writes
# itself, and `backups/` is where #32 puts archives unless told otherwise -- a
# conflict artifact caught inside a backup is a copy of one already reported
# from the live tree. Deeper down these are ordinary names: a world may
# legitimately be called `backups`, so the skip does not apply recursively.
# `.cache` is named here even though the dot-directory rule below already covers
# it -- this list is the statement about reserved store-root names, and it
# should not quietly stop being true if that rule is ever narrowed.
#
# The *configured* backup directory is pruned separately (`_pruned`), because
# it is a setting and need not be either of these names.
SKIP_AT_ROOT = frozenset({"backups", ".cache"})

MAX_RESULTS = 200
MAX_ENTRIES = 100_000


def _tool(name: str) -> str | None:
    low = name.lower()
    for tool, pattern in RULES:
        if fnmatch.fnmatchcase(low, pattern):
            return tool
    return None


def _stamp(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm(path: Path) -> Path:
    """Absolute and lexically normalized, for comparison only — the same
    treatment `backups._norm` gives both sides of its own containment test.
    Lexical rather than `resolve()`: the walk builds paths from `home()` as it
    was handed to us, and resolving one side but not the other disagrees on any
    store reached through a symlink."""
    return Path(os.path.normpath(os.path.abspath(str(path))))


def _pruned(base: Path) -> frozenset[Path]:
    """Directories to skip wherever they sit, as normalized absolute paths.

    Just the configured backup directory today, and only when it is **inside**
    the store — the same condition, for the same reason, that `backups._skips`
    applies. Pointing it at an ancestor of the store (`~`, the obvious choice
    for "somewhere else") would otherwise match everything and report a library
    as clean without looking at it. Outside the store there is nothing to
    prune, because nothing out there is being walked.

    `SKIP_AT_ROOT` covers the default location by name; this covers a user who
    moved it. Both are needed: the default folder can still hold archives from
    before the setting was changed.

    A backup directory that cannot be resolved is not a reason to refuse the
    whole scan -- the worst case is listing a conflicted copy twice, which is
    noise, where failing here would be silence.
    """
    try:
        target = _norm(backups.backup_dir())
    except (OSError, ValueError):
        return frozenset()  # a bad setting is not a reason to refuse the scan
    nbase = _norm(base)
    return frozenset({target}) if nbase in target.parents else frozenset()


def scan() -> dict:
    """Sync-tool conflict artifacts in the store, sorted by path.

    ``{"conflicts": [{"path", "name", "tool", "kind", "size", "modified"},
    ...], "truncated": bool}``. ``path`` is relative to the store root and
    slash-separated, so it reads the same on every platform and names
    something the user can find in their own file browser. ``kind`` is
    ``"file"`` or ``"directory"``; ``size`` is None for a directory.

    ``truncated`` covers both bounds -- too many artifacts to list, or a store
    too large to finish walking. A directory *inside* the store that cannot be
    read is skipped rather than fatal: a synced volume routinely holds a folder
    the client has locked, and one such folder must not cost the user the
    report on all the others. The root is the exception and raises: nothing was
    examined at all there, and an empty list would read as a clean library. A
    root that merely does not exist yet is clean, and truthfully so.
    """
    base = paths.home()
    prune = _pruned(base)
    conflicts: list[dict] = []
    truncated = False
    seen = 0
    stack: list[tuple[Path, int]] = [(base, 0)]

    while stack:
        directory, depth = stack.pop()
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda e: e.name)
        except FileNotFoundError:
            continue  # a store not created yet holds no conflicts; nor does a
                      # directory that vanished mid-walk
        except OSError:
            if depth == 0:
                raise  # the root itself: nothing was looked at, so "clean" would be a lie
            continue
        # Deepest-last so `stack.pop()` walks siblings in name order.
        subdirs: list[tuple[Path, int]] = []
        for entry in entries:
            seen += 1
            if seen > MAX_ENTRIES:
                return _result(conflicts, True)
            try:
                # `follow_symlinks=False` throughout: a symlinked directory
                # reads as "not a directory" and is never descended, which is
                # what keeps a link pointing back up the tree from looping the
                # walk forever. A store on a synced volume is exactly where
                # somebody has symlinked one library into another.
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir and (entry.name.startswith(".")
                           or (depth == 0 and entry.name in SKIP_AT_ROOT)
                           or _norm(Path(entry.path)) in prune):
                continue  # sync metadata (.stversions, .dropbox.cache), our own
                          # derived data, and wherever backups are configured to go
            tool = _tool(entry.name)
            if tool is None:
                if is_dir:
                    subdirs.append((Path(entry.path), depth + 1))
                continue
            # A conflicted DIRECTORY is reported and not descended. Dropbox
            # renames whole folders too ("Realm (Winifred's conflicted copy …)"),
            # which is a shadow world the app resolves nothing inside of -- and
            # everything under it is a copy of a record already reported from
            # the live tree, so walking in would bury the one line that matters
            # under a hundred that do not.
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue  # vanished mid-walk; an entry we cannot stat is not a report
            conflicts.append({
                "path": Path(entry.path).relative_to(base).as_posix(),
                "name": entry.name,
                "tool": tool,
                "kind": "directory" if is_dir else "file",
                # Meaningless for a directory, and summing the tree to make it
                # mean something would be a second walk of the thing we just
                # decided not to walk.
                "size": None if is_dir else st.st_size,
                "modified": _stamp(st.st_mtime),
            })
            if len(conflicts) >= MAX_RESULTS:
                return _result(conflicts, True)
        stack.extend(reversed(subdirs))

    return _result(conflicts, truncated)


def _result(conflicts: list[dict], truncated: bool) -> dict:
    return {"conflicts": sorted(conflicts, key=lambda c: c["path"]), "truncated": truncated}
