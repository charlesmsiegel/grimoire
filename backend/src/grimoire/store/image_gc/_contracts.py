"""Bounded GC vocabulary, grace judgments, exceptions and disposal outcomes."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

GRACE_HOURS = 1
"""How old an unreachable object's sidecar and blob must be before it is
collected: a structural margin over the longest request or run that ingests a
picture and has not placed it yet, not a measured one."""

BATCH_OBJECTS = 100
"""The most objects one hold of the ingest/GC lock deletes."""

BATCH_SECONDS = 2.0
"""The candidate-processing budget per ingest/GC hold, give or take an object.
The sidecar listing and refcount rebuild happen first under that same hold
and are not bounded by this clock (#479); an upload waits for both."""

STAGING_MIN_AGE_SECONDS = 3600
"""A staged work directory touched more recently than this blocks deletion."""

TOKEN_HOURS = 24
"""How long a dry run's token stays good."""

STAGING_DIRS = (".world-staging", ".module-staging")
"""Where whole trees are built before they are published
(`worlds.staging.staging_root`, `module_edit.staging`)."""

FORMAT = 2

_GC_DIR = (".cache", "image-store", "gc")
_THUMBS_DIR = (".cache", "thumbs")
_RECYCLE_DIR = (".cache", "image-store", "recycle")
_BLOBS_DIR = ("assets", "image-store", "blobs")
_TOKEN = re.compile(r"[0-9a-f]{32}")
_BLOB_NAME = re.compile(r"([0-9a-f]{64})\.([a-z]+)")
#: The only trees a collection deletes in, relative to the root. The outbox is
#: deliberately not one: a picture waiting there leaves only for the bin.
_DELETABLE = (("assets", "image-store", "objects"), _BLOBS_DIR,
              (".cache", "image-store", "blob-index"), _THUMBS_DIR)

#: What a bin name may carry of a description, in characters.
BIN_DESCRIPTION_CHARS = 60
#: ... and in UTF-8 bytes, so the whole name (about 30 bytes besides) stays
#: under the 255 a file name may take: 60 four-byte characters would not.
BIN_DESCRIPTION_BYTES = 200
#: Characters a Windows file name may not hold, every control character, and
#: lone surrogates (which no file system can encode).
_NOT_IN_A_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f-\x9f\ud800-\udfff]')

MOVE_TRIES = 100
"""How many names one move into the outbox tries -- the bin name, then its
`` (2)``, `` (3)``, ... -- when each it picks is taken by the time it lands
(something syncing files into the outbox). Past that the run stops with the
blob still in ``blobs/``."""

#: Whether `os.rename` refuses a target that exists (Windows), so it is the
#: no-clobber move; elsewhere it replaces one, and the move is a hard link and
#: an unlink. A module attribute so a test can take the Windows branch.
_RENAME_REFUSES_EXISTING = sys.platform == "win32"

#: A report's `state`.
COMPLETE = "complete"
BLOCKED = "blocked"
CANCELLED = "cancelled"
REFUSED = "refused"
ROOT_CHANGED = "root-changed"
FAILED = "failed"
#: The bin refused a file: it waits in the outbox, and nothing more is collected.
TRASH_UNAVAILABLE = "trash-unavailable"
#: Windows did not confirm a batch reached the bin (`recycle_bin.send_batch`).
RECYCLE_BIN_UNVERIFIED = "recycle-bin-unverified"


class _RootChangedError(Exception):
    """The live store root is no longer the one this run pinned."""


class _UnsafePathError(Exception):
    """A path the collector was about to delete runs through a link or is not
    a regular file. Stops the run: the tree is not what the scan saw."""

    def __init__(self, path: Path) -> None:
        super().__init__(str(path))
        self.path = path


# ---- judging ---------------------------------------------------------------------

def _number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _as_number(v: object) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


@dataclass
class _Verdict:
    why: str | None             # None: collectable now; else "grace" or "clock-skew"
    at: float | None            # when it becomes collectable (None: cannot say)
    skew: str | None = None     # "mtime"


def _judge(now: float, grace: float, mtimes: list[float]) -> _Verdict:
    """M13 for one object or blob, on its mtimes alone."""
    if any(m > now for m in mtimes):
        return _Verdict("clock-skew", None, "mtime")
    due = max((m + grace for m in mtimes), default=0.0)
    return _Verdict(None if now >= due else "grace", due)


# ---- reports and tokens ----------------------------------------------------------

#: The ``kind`` a stored collection report carries (`maintenance_reports`).
KIND = "image-gc"
