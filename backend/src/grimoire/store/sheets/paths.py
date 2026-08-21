"""Sheet file locations, the error types every writer raises, and the two
low-level file primitives (gen minting and the atomic JSON write)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from .. import atomic, entities
from ..campaigns import paths as campaigns_paths
from ..paths import safe_id
from ..worlds import paths as worlds_paths


class SheetError(Exception):
    """Rejected sheet write (no module, bad kind/type/values)."""


class SheetConflict(SheetError):
    """CAS rejection: the sheet changed since the caller last read it."""


FILE_KINDS: tuple[str, ...] = ("characters", "pcs") + entities.ENTITY_KINDS


def _next_gen(path: Path, sheet_type: str) -> str:
    """Sheet identity nonce: preserved across same-type value writes, minted
    on creation and on type changes (a type change is logically a new sheet).
    Legacy files without a gen mint one on their next whole-sheet write."""
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            data = {}
        if isinstance(data, dict) and data.get("sheet_type") == sheet_type \
                and isinstance(data.get("gen"), str) and data["gen"]:
            return data["gen"]
    return uuid.uuid4().hex


def _creation_mark(path: Path, sheet_type: str) -> bool:
    """Whether the stored sheet went through the module's creation step.

    Carried across value writes on exactly ``_next_gen``'s rule, and for the
    same reason: it is a fact about this sheet's identity, not about its
    current numbers, so a later edit keeps it and a TYPE CHANGE drops it (a
    different sheet type has its own creation step, which nobody has run).

    Stored rather than derived because it cannot be recovered from the values.
    A creation spend is free to land exactly on the schema defaults -- spend
    ``pool-basic``'s attribute pool evenly at its minimum and the result is
    byte-identical to a sheet nobody touched -- so any test over the fields
    alone must call one of those two cases wrong. This one was tried and did.
    """
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return False
    return (isinstance(data, dict) and data.get("sheet_type") == sheet_type
            and data.get("creation") is True)


def _sheet_doc(sheet_type: str, fields: dict, gen: str | None, *, creation: bool) -> dict:
    """The stored form of a sheet. ``creation`` is omitted rather than written
    false, so a store full of sheets predating the mark reads identically to
    one written today -- absent and false are the same answer here."""
    doc: dict = {"sheet_type": sheet_type, "fields": fields, "gen": gen}
    if creation:
        doc["creation"] = True
    return doc


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write a sheet through the shared crash-safe writer (store.atomic), which
    keeps the mkdir this module has always done before it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps(data, indent=2))


def sheet_kind(kind: str) -> str:
    """Module sheet-type kind for a file kind (pcs share characters types)."""
    return "characters" if kind == "pcs" else kind


def _campaign_dir(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "sheets"


def _campaign_path(cid: str, kind: str, eid: str) -> Path:
    return _campaign_dir(cid) / f"{kind}--{eid}.json"


def _world_dir(wid: str, mid: str) -> Path:
    return worlds_paths.world_root(wid) / "sheets" / mid


def _world_path(wid: str, mid: str, kind: str, eid: str) -> Path:
    return _world_dir(wid, mid) / f"{kind}--{eid}.json"


def _repoint_in(directory: Path, mapping: dict[str, str]) -> None:
    """Rename every `<kind>--<eid>.json` in one sheet directory per `mapping`.

    A sheet is keyed by FILENAME, not by a field, so following a reclassified
    record means moving the file -- the same shape `alternates` is in for scene
    renames, and the reason it sits outside the field-rewriting fan-out.

    A destination that already exists is left alone rather than overwritten: the
    reclassify only lands on a free record slug, so a sheet already filed there
    belongs to a record this move knows nothing about, and losing somebody's
    sheet is worse than leaving one behind. Missing sources are simply skipped —
    most records have no sheet at all.
    """
    if not directory.is_dir():
        return
    for old, new in mapping.items():
        okind, _, oid = old.partition("/")
        nkind, _, nid = new.partition("/")
        # Every component through `safe_id` before it becomes a filename: this
        # is an id-to-path resolver like any other (#240), and its ids arrive
        # off a ledger rather than from a caller who has already checked them.
        if not all(safe_id(part) for part in (okind, oid, nkind, nid)):
            continue
        src = directory / f"{okind}--{oid}.json"
        dst = directory / f"{nkind}--{nid}.json"
        if src.exists() and not dst.exists():
            src.replace(dst)


def repoint_records(cid: str, mapping: dict[str, str]) -> None:
    """Follow reclassified records (#119) through this campaign's sheets."""
    mapping = {old: new for old, new in mapping.items() if old != new}
    if mapping:
        _repoint_in(_campaign_dir(cid), mapping)


def repoint_world_records(wid: str, mapping: dict[str, str]) -> None:
    """`repoint_records` for a world's sheets, which are filed per module id —
    so this walks every module directory rather than one."""
    mapping = {old: new for old, new in mapping.items() if old != new}
    if not mapping:
        return
    root = worlds_paths.world_root(wid) / "sheets"
    if not root.is_dir():
        return
    for module_dir in sorted(root.iterdir()):
        _repoint_in(module_dir, mapping)
