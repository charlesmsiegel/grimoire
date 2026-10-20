"""sync: display refs responsibilities."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

from .. import characters, entities, entity_schema, group_refs, overlay, pcs


def _incoming_identity(path: Path) -> tuple[int, int] | None:
    # samefile's native device/inode proof, indexed once rather than probing
    # every pair. A missing alias stays unresolved; denied proof is not absence.
    try:
        result = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise group_refs.GroupIdentityConflict("Cannot determine campaign reference identity; repair actor or record access.") from exc
    return result.st_dev, result.st_ino


def _incoming_native_names(v: overlay.View, kind: str, owned: set[str], refs: set[str],
                           names: dict[str, str]) -> None:
    needed = {ref: aid for ref in refs for prefix, sep, aid in [ref.partition(":")]
              if sep and prefix == kind and entity_schema.referenceable(aid) and aid not in owned}
    if not owned or not needed:
        return
    actor = kind in group_refs.ACTOR_KINDS

    def path(eid):
        return v.croot / kind / eid if actor else overlay._flat_path(v.croot, kind, eid)

    aliases = {ref: identity for ref, aid in needed.items()
               if (identity := _incoming_identity(path(aid))) is not None}
    if not aliases:
        return
    # Owned directory/file names, not inherited raw IDs that happen to resolve
    # through them. One identity read per owned slot and per queried alias.
    identities = {identity: eid for eid in sorted(owned)
                  if (identity := _incoming_identity(path(eid))) is not None}
    for ref, identity in aliases.items():
        own = identities.get(identity)
        if own is not None:
            names[ref] = names.get(f"{kind}:{own}", ref)


def _incoming_names(cid: str, v: overlay.View, kinds: set[str], refs: set[str]) -> dict[str, str]:
    names: dict[str, str] = {}
    for kind in sorted(kinds):
        if kind == "characters":
            rows = overlay.character_roster(cid, v=v)
            owned = set(characters.listed_ids(v.croot))
        elif kind == "pcs":
            rows = overlay.pc_roster(cid, v=v)
            owned = set(pcs.listed_ids(v.croot))
        else:
            rows = overlay.list_entities(cid, kind, v=v)
            owned = set(entities.entity_ids(v.croot, kind))
        names.update({f"{kind}:{row['id']}": row.get("name", row["id"]) for row in rows})
        # Exact owned IDs already have the correct name. Native alias matching
        # applies to actors AND flat records, without another content/card read.
        _incoming_native_names(v, kind, owned, refs, names)
    return names


def _incoming_ref_rows(items: list[dict]) -> list[tuple[str, dict, list[str]]]:
    entries: list[tuple[str, dict, list[str]]] = []
    for item in items:
        descriptors = {field["key"]: field for field in entity_schema.FIELDS.get(item["ref"]["kind"], ())}
        entries.extend((side, row, entity_schema.parse_refs(row["value"]))
            for side in ("world", "mine") for row in item.get(side, {}).get("fields", ())
            if descriptors[row["key"]]["widget"] == entity_schema.REF)
    return entries


def _show_incoming_fields(cid: str, items: list[dict], v: overlay.View) -> None:
    entries = _incoming_ref_rows(items)
    kinds: set[str] = set()
    actors: set[str] = set()
    needed_refs = {ref for _, _, refs in entries for ref in refs}
    for ref in needed_refs:
        kind, _, rid = ref.partition(":")
        if kind in entity_schema.REF_KINDS and entity_schema.referenceable(rid):
            kinds.add(kind)
        actor = group_refs.actor_ref(ref)
        if actor is not None:
            actors.add(actor)
    severed: set[str] = set()
    if actors:
        # These proofs and the display roster use the same captured roots and,
        # when they read, complete strict sets: a changed data directory cannot
        # pair another store's detachments with this sweep's names (spec 07).
        # But `shown` is display text nothing compares (5.3) and `incoming` is
        # an ordinary read (7.7), so a ledger that cannot be verified degrades
        # to the fail-soft sets -- at worst a stranger loses its marker -- and
        # never takes the whole sync panel down with a 409.
        with suppress(group_refs.GroupIdentityConflict):
            v._off = frozenset(overlay._strict_ledger(v.croot / "detached.json", visibility=False))
        with suppress(group_refs.GroupIdentityConflict):
            v._gone = frozenset(overlay._strict_ledger(v.croot / "deleted.json", visibility=True))
        try:
            severed = overlay.actor_detachments(v.croot, v.wroot, actors, set(v.off))
        except group_refs.GroupIdentityConflict:
            severed = set(v.off)   # alias proof denied: exact markers only
    names = _incoming_names(cid, v, kinds, needed_refs)
    for side, row, refs in entries:
        shown = []
        for ref in refs:
            value = names.get(ref, ref)
            if side == "world" and group_refs.actor_ref(ref) is not None and ref.replace(":", "/", 1) in severed:
                value += " (this campaign's own; not the library's)"
            shown.append(value)
        row["shown"] = ", ".join(shown)
