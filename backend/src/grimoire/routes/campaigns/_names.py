"""Source-aware campaign record display names."""

from __future__ import annotations

from ... import store


def _record_name(cid: str, kind: str, eid: str) -> str | None:
    """Display name for a campaign record (materialized or still inherited
    from the world), or None if it no longer exists anywhere."""
    try:
        if kind == "characters":
            return store.overlay.read_character(cid, eid)["meta"].get("name", eid)
        if kind == "pcs":
            # A player character's standing state is written back too (absorb's
            # `character_state` edit takes a PC id), so its rows are named here
            # rather than dropped as a record that no longer exists.
            root = store.overlay.pc_root(cid, eid)
            return store.pcs.read_pc(root, eid)["meta"].get("name", eid)
        if kind in store.entities.ENTITY_KINDS:
            return store.overlay.read_entity(cid, kind, eid)["meta"].get("name", eid)
    except (store.characters.CharacterNotFound, store.entities.EntityNotFound,
            store.pcs.PCNotFound):
        return None
    return None
