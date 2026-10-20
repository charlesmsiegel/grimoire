"""Migration plan values, pinned occurrences, metadata vocabulary and stable run outcomes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import image_collections, image_subjects, image_surfaces

#: The most `description_conflicts` entries an object keeps (M9).
MAX_CONFLICTS = 20
#: Identities that are not a picture anybody can show: left where they are.
_NOT_PICTURES = frozenset({"unsniffable", "unsanitizable", "undecodable"})
_OBJECT = "object"
_MEMBER_NAME = re.compile(re.escape(image_collections.MEMBER_PREFIX) + r"[0-9a-f]{64}\Z")

Occurrence = image_surfaces.Occurrence


@dataclass(frozen=True)
class Item:
    """One legacy file the plan places."""
    occurrence: Occurrence
    #: The sha256 of its sanitized bytes: the blob it would be.
    stream: str
    image_id: str
    #: Its sanitized size, and its size on disk when hashed.
    size: int
    raw_size: int
    #: ``(st_dev, st_ino, st_size, st_mtime_ns)`` when it was hashed, so a run
    #: can tell the file it planned from one changed since.
    stat: tuple[int, int, int, int]
    #: The image-bearing placement already holding this very picture under
    #: the name, if one does (the file is then redundant, not placed).
    placed: str | None = None


@dataclass
class Group:
    """One picture: every stream and every occurrence that is it."""
    image_id: str
    #: Its object already exists under the root.
    existing: bool = False
    #: The stream the object keeps; None for a group no file of this plan
    #: belongs to (only metadata folds into it) whose object is not there.
    retained: str | None = None
    #: stream -> how many placements use it.
    streams: dict[str, int] = field(default_factory=dict)
    #: The previewed description: ``{}`` (absent), ``{"text": t}`` or
    #: ``{"conflicts": [{text, from}, ...]}``.
    description: dict = field(default_factory=dict)
    #: The previewed subjects, per scope, sorted.
    subjects: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class MigrationPlan:
    root: Path
    items: list[Item] = field(default_factory=list)
    #: Metadata-only occurrences, each with the image id its target places
    #: (None when the target holds no picture this run can name).
    metadata: list[tuple[Occurrence, str | None]] = field(default_factory=list)
    groups: dict[str, Group] = field(default_factory=dict)
    untouched: list[dict] = field(default_factory=list)
    case_aliases: list[dict] = field(default_factory=list)
    legacy_differs: list[dict] = field(default_factory=list)
    overcap_keys: list[dict] = field(default_factory=list)
    subject_disagreements: list[dict] = field(default_factory=list)
    format1_collections: list[str] = field(default_factory=list)
    #: Legacy files inventoried (one per name per directory).
    legacy_files: int = 0
    descriptions_merged: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    cancelled: bool = False

    def rel(self, p: Path) -> str:
        """`p` as the report spells it: relative to the root, POSIX."""
        try:
            return p.relative_to(self.root).as_posix()
        except ValueError:
            return p.as_posix()


def choose_retained(streams: dict[str, tuple[int, int]]) -> str:
    """The stream a NEW object keeps, from ``{sha: (placements, size)}``:
    most placements, then the smaller file, then the lowest hash (M7)."""
    return min(streams, key=lambda sha: (-streams[sha][0], streams[sha][1], sha))


# ---- the real run (M8-M10) ---------------------------------------------------
#
# Everything below writes. The one rule it is arranged around: the only working
# copy of a picture is never deleted. A legacy file goes only once a placement
# naming its picture has been read back under the PINNED root and its blob
# re-hashed there; it goes by identity (the exact file that was hashed, by
# dev/inode/size/mtime), never by name; and the live root is asked to still be
# the pinned one before every destructive step (M4), so a data-dir move mid-run
# stops it with nothing deleted from then on, in either tree.

#: How a run ended (``outcome`` in its report).
DONE, CANCELLED, FAILED, ROOT_CHANGED = "done", "cancelled", "failed", "root-changed"
#: The ``kind`` a stored migration report carries (`maintenance_reports`).
KIND = "image-migration"
#: How many times one key's fold is retried when its value moves under it.
_FOLD_TRIES = 4
_SUBJECTS = image_subjects.SUBJECTS_FILE
_KIND, _RELATION = "character", "subject"


class _RootChangedError(Exception):
    """The live store root is no longer the pinned one: stop, delete nothing."""

    def __init__(self, step: str):
        super().__init__(step)
        self.step = step
