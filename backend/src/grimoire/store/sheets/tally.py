"""The cast-wide view of sheets and the bulk operations over it: coverage
tallies (how many of a campaign's/world's entities have a valid sheet), the
per-member ``roster`` behind them, ``seed`` -- the one-time copy of a world's
starting sheets -- and ``create_missing``, its counterpart for a campaign that
acquired its cast after the fact.

Named ``tally`` and not ``coverage``: ``coverage`` is a public function of
this package, so a submodule of that name would be overwritten the moment
``__init__`` re-exports the function.
"""

from __future__ import annotations

from .. import atomic, characters, entities, locks, overlay, pcs
from ..campaigns import read as campaigns_read
from ..modules import binding as modules_binding
from ..modules import pack as modules_pack
from ..worlds import paths as worlds_paths
from . import paths, schema, writer

# Aliased: `_tally` takes a parameter named `reader`, which would shadow the
# submodule inside it.
from . import reader as sheet_reader
from .paths import FILE_KINDS, SheetError


def seed(cid: str) -> int:
    """Copy world starting sheets for the campaign's resolved module.
    Called once from create_campaign; changing the module later never
    re-seeds (spec).

    Takes the campaign lock even though it runs during creation. "Nobody else
    has this cid yet" is true only *in process*: ``create_campaign`` publishes
    ``campaign.md`` several operations before it gets here, and
    ``list_campaigns`` reports any directory holding that file — so a second
    grimoire process (the case ``campaign_lock`` exists for, #234) can discover
    the campaign mid-creation and write a sheet this copy would then overwrite.
    """
    with locks.campaign_lock(cid):
        mid = modules_binding.resolve(cid)
        if mid is None:
            return 0
        src = campaigns_read.world_root_of(cid) / "sheets" / mid
        if not src.is_dir():
            return 0
        dst = paths._campaign_dir(cid)
        dst.mkdir(parents=True, exist_ok=True)
        n = 0
        for p in sorted(src.glob("*.json")):
            # through the helper, not shutil.copy2: a partial copy must never
            # appear under a real sheet name
            atomic.write_bytes(dst / p.name, p.read_bytes())
            n += 1
        return n


def _type_kinds(sheets_def: dict) -> set[str]:
    return {st.get("kind") for st in sheets_def.get("sheet_types", {}).values()
            if isinstance(st, dict)}


def _sheetable_kinds(pack: dict) -> list[str]:
    """The FILE_KINDS this pack has at least one sheet type for, in FILE_KINDS
    order -- so ``coverage``, ``roster`` and ``create_missing`` all agree on
    which kinds are part of the cast and in what order they are reported."""
    kinds = _type_kinds(pack["sheets"])
    return [k for k in FILE_KINDS if paths.sheet_kind(k) in kinds]


def _cast(cid: str, kind: str) -> list[dict]:
    """One kind of the campaign's cast, through the overlay -- so an inherited
    world record counts and a tombstoned one does not."""
    if kind == "characters":
        return overlay.list_characters(cid)
    if kind == "pcs":
        return overlay.list_pcs(cid)
    return overlay.list_entities(cid, kind)


def _kind_types(sheets_def: dict, kind: str) -> list[str]:
    """The sheet types a file kind can take, sorted for a stable message."""
    want = paths.sheet_kind(kind)
    return sorted(tid for tid, st in sheets_def.get("sheet_types", {}).items()
                  if isinstance(st, dict) and st.get("kind") == want)


def _tally(ids: list[str], reader) -> dict:
    sheeted = invalid = 0
    for eid in ids:
        s = reader(eid)
        if s is None:
            continue
        sheeted += 1
        if s["errors"]:
            invalid += 1
    return {"total": len(ids), "sheeted": sheeted, "invalid": invalid}


def coverage(cid: str) -> dict:
    mid = modules_binding.resolve(cid)
    if mid is None:
        return {}
    out: dict = {}
    for kind in _sheetable_kinds(modules_pack.load_pack(mid)):
        ids = [e["id"] for e in _cast(cid, kind)]
        out[kind] = _tally(ids, lambda eid, k=kind: sheet_reader.read(cid, k, eid))
    return out


def world_coverage(wid: str, mid: str) -> dict:
    try:
        modules_pack.pack_root(mid)
    except modules_pack.ModuleNotFound:
        return {}
    pack = modules_pack.load_pack(mid)
    if pack["errors"]:
        return {}
    root = worlds_paths.world_root(wid)
    out: dict = {}
    for kind in _sheetable_kinds(pack):
        if kind == "characters":
            ids = [c["id"] for c in characters.list_characters(root)]
        elif kind == "pcs":
            ids = [p["id"] for p in pcs.list_pcs(root)]
        else:
            ids = [e["id"] for e in entities.list_entities(root, kind)]
        out[kind] = _tally(ids, lambda eid, k=kind: sheet_reader.read_world(wid, mid, k, eid))
    return out


def roster(cid: str) -> dict[str, list[dict]]:
    """``coverage`` with names: one row per cast member rather than one count
    per kind.

    ``coverage`` answers "how many", which is all the mechanics panel ever
    needed. A screen that offers to *fix* the gap has to answer "which", and
    "which" is not derivable from a tally -- so this is the same sweep,
    reporting per entity instead of summing.

    Each row: ``id``, ``name``, ``sheeted``, the ``sheet_type`` (None when
    there is no sheet), the stored sheet's ``errors``, and ``unspent`` --
    ``schema.unspent_pools``, so a sheet created from schema defaults says what
    it still owes its creation pools rather than passing as finished.
    """
    mid = modules_binding.resolve(cid)
    if mid is None:
        return {}
    pack = modules_pack.load_pack(mid)
    sheets_def = pack["sheets"] if isinstance(pack.get("sheets"), dict) else {}
    out: dict[str, list[dict]] = {}
    for kind in _sheetable_kinds(pack):
        rows: list[dict] = []
        for member in _cast(cid, kind):
            eid = member["id"]
            row: dict = {"id": eid, "name": member.get("name") or eid, "sheeted": False,
                         "sheet_type": None, "errors": [], "unspent": {}}
            sheet = sheet_reader.read(cid, kind, eid)
            if sheet is not None:
                row["sheeted"] = True
                row["sheet_type"] = sheet["sheet_type"]
                row["errors"] = sheet["errors"]
                if isinstance(sheet["sheet_type"], str):
                    row["unspent"] = schema.unspent_pools(
                        sheets_def, sheet["sheet_type"], sheet["fields"])
            rows.append(row)
        out[kind] = rows
    return out


def _bulk_type(sheets_def: dict, kind: str, want: str | None) -> tuple[str | None, str]:
    """The sheet type a bulk create uses for one kind, or ``None`` and the
    reason it cannot pick one.

    A module may define several sheet types for the same kind (``medium`` and
    ``shifter`` both target ``characters``), and there is no rule that picks
    between them -- so the caller passes one. Guessing would give half a cast
    the wrong sheet, which is worse than the gap it filled.
    """
    types = _kind_types(sheets_def, kind)
    if want:
        if want in types:
            return want, ""
        return None, (f"{want!r} is not a sheet type for {kind} in this module "
                      f"({', '.join(types)})")
    if len(types) == 1:
        return types[0], ""
    return None, (f"this module has {len(types)} sheet types for {kind} "
                  f"({', '.join(types)}) -- choose one")


def create_missing(cid: str, types: dict[str, str] | None = None) -> dict:
    """Create a schema-default sheet for every cast member that has none.

    ``types`` names the sheet type to use per file kind; a kind the module has
    exactly one type for needs no entry. One campaign-lock hold covers the
    whole sweep, so "has no sheet" cannot go stale between the check and the
    write -- and each write still asserts creation (``expected=None``), so a
    sheet that appears from another process is a recorded failure rather than
    an overwrite.

    Returns ``{"created", "skipped", "failed"}``, and every cast member ends up
    in exactly one of them or already had a sheet. That is the point: a bulk
    create that quietly did less than it claimed is the one outcome this must
    not have.

    - ``created``: ``{kind, id, name, sheet_type, unspent}``. ``unspent`` is
      non-empty for a type whose ``creation`` block the schema defaults do not
      balance -- the sheet exists and is explicitly incomplete, which the
      roster then flags. Skipping those entities silently was the alternative,
      and it is the one option #201 rules out.
    - ``skipped``: ``{kind, reason}`` -- a whole kind, because the only reason
      to skip one is that no sheet type could be chosen for it.
    - ``failed``: ``{kind, id, detail}`` -- one entity whose write was rejected
      (a sheet raced in, or the module refused the values).
    """
    chosen = {k: v for k, v in (types or {}).items() if isinstance(v, str) and v}
    created: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []
    with locks.campaign_lock(cid):
        mid = modules_binding.resolve(cid)
        if mid is None:
            raise SheetError("no module resolved for this campaign")
        pack = modules_pack.load_pack(mid)
        sheets_def = pack["sheets"] if isinstance(pack.get("sheets"), dict) else {}
        kinds = _sheetable_kinds(pack)
        # Reported, not ignored: a choice naming a kind this module does not
        # sheet means the client is working from a roster the module has moved
        # on from, and answering "created 0" without saying so would read as
        # "nothing to do" rather than "we no longer agree about the cast".
        skipped.extend({"kind": kind,
                        "reason": "this module has no sheet type for this kind"}
                       for kind in sorted(set(chosen) - set(kinds)))
        for kind in kinds:
            sheet_type, reason = _bulk_type(sheets_def, kind, chosen.get(kind))
            if sheet_type is None:
                skipped.append({"kind": kind, "reason": reason})
                continue
            # Once per kind, not once per entity: every sheet created for this
            # kind is written from the same schema defaults.
            unspent = schema.unspent_pools(
                sheets_def, sheet_type, schema.default_fields(sheets_def, sheet_type))
            for member in _cast(cid, kind):
                eid = member["id"]
                if sheet_reader.read(cid, kind, eid) is not None:
                    continue
                try:
                    writer.write(cid, kind, eid, sheet_type, None, expected=None)
                except SheetError as exc:
                    failed.append({"kind": kind, "id": eid, "detail": str(exc)})
                    continue
                created.append({"kind": kind, "id": eid,
                                "name": member.get("name") or eid,
                                "sheet_type": sheet_type, "unspent": unspent})
    return {"created": created, "skipped": skipped, "failed": failed}
