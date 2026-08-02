"""Turning the parsed sections into StagedEdits — the diff the reviewer sees.

The file is named for the role, not the function, because `materialize` is a
public function this package re-exports: a submodule spelled the same way
would be overwritten by that export, and a later `from ..absorb import
materialize` would bind the function rather than the module.
"""

from __future__ import annotations

from .. import (characters, entities, groupstate, overlay, pcs, playstate,
                plot, relationships)
from ..appearances import paths as appearances_paths, versions as appearances_versions
from ..campaigns import paths as campaigns_paths
from ..paths import slugify
from . import conflicts, parse, weather

_CARD_FIELDS = ("description", "personality", "scenario")


def _char_name(cid: str, char_id: str) -> str:
    """Overlay-aware: a thin campaign's NPC is usually still inherited (never
    materialized croot-side), so the display name must resolve across the union."""
    try:
        return characters.read_character(overlay.char_root(cid, char_id), char_id)["meta"].get("name", char_id)
    except characters.CharacterNotFound:
        return char_id


def _actor_exists(cid: str, token: str) -> bool:
    """Overlay-aware: a thin campaign's cast is mostly inherited (never
    materialized croot-side), so existence must be checked across the union,
    not just the campaign's own copy."""
    kind, _, aid = token.partition(":")
    try:
        if kind == "pcs":
            pcs.read_pc(overlay.pc_root(cid, aid), aid)
        elif kind == "characters":
            characters.read_character(overlay.char_root(cid, aid), aid)
        else:
            return False
        return True
    except (characters.CharacterNotFound, pcs.PCNotFound):
        return False


def _entity_kind(cid: str, eid: str) -> str | None:
    for kind in ("lore", "locations"):
        try:
            overlay.read_entity(cid, kind, eid)
            return kind
        except entities.EntityNotFound:
            continue
    return None


def materialize(cid: str, sid: str, parsed: dict) -> list[dict]:
    """Turn the parsed edit lists into before/after StagedEdits against the campaign
    copies. Targets that don't exist are dropped (tolerated, not an error)."""
    croot = campaigns_paths.campaign_root(cid)
    out: list[dict] = []

    for e in parsed.get("character_state_edits", []):
        raw_id = e.get("id", "")
        if not raw_id:
            continue
        # The model echoes ids from the "Present: <kind>/<id>, ..." context line (or,
        # less reliably, a bare id) — strip any "characters/" or "characters:" prefix so
        # both forms resolve. playstate.py only tracks "characters" (not "pcs"), matching
        # its own docstring scope, so a pcs-prefixed id is dropped rather than misfiled.
        kind, sep, rest = raw_id.partition("/")
        if not sep:
            kind, _, rest = raw_id.partition(":")
        char_id = rest if kind in ("characters", "pcs") else raw_id
        if kind == "pcs":
            continue
        try:
            # overlay-aware: a thin campaign's NPC is usually still inherited
            # (never appeared/materialized), and a state edit for it must not
            # be silently dropped just because croot lacks the character dir
            characters.read_character(overlay.char_root(cid, char_id), char_id)
        except characters.CharacterNotFound:
            continue
        st = playstate.read_state(croot, char_id)
        cur_knows = st["knows"] if st else ""
        cur_suspects = st["suspects"] if st else ""
        # Keep-on-omit: an omitted knows/suspects preserves the stored value; an explicit
        # "" clears it. Prevents an absorb that only touches current_state from silently
        # erasing established knowledge.
        knows = e["knows"] if "knows" in e else cur_knows
        suspects = e["suspects"] if "suspects" in e else cur_suspects
        after = playstate.compose_body(e.get("current_state", ""), knows, suspects)
        if not after:
            continue
        before = playstate.compose_body(st["current_state"], cur_knows, cur_suspects) if st else ""
        if before == after:
            continue
        out.append({"id": f"character_state:{char_id}", "kind": "character_state",
                    "target": {"kind": "characters", "id": char_id},
                    "label": f"{_char_name(cid, char_id)} — current state",
                    "field": "current_state",
                    "before": before, "after": after, "authored": False})

    for e in parsed.get("group_state_edits", []):
        raw_id = e.get("id", "")
        if not raw_id:
            continue
        kind, sep, rest = raw_id.partition("/")
        if not sep:
            kind, _, rest = raw_id.partition(":")
        gid = rest if kind == "groups" else raw_id
        try:
            name = overlay.read_entity(cid, "groups", gid)["meta"].get("name", gid)
        except entities.EntityNotFound:
            continue
        st = groupstate.read_state(croot, gid)
        cur = {k: (st[k] if st else "") for k in groupstate.FIELDS}
        new = {k: (e[k] if k in e else cur[k]) for k in groupstate.FIELDS}
        after = groupstate.compose_body(new)
        if not after:
            continue
        before = groupstate.compose_body(cur) if st else ""
        if before == after:
            continue
        out.append({"id": f"group_state:{gid}", "kind": "group_state",
                    "target": {"kind": "groups", "id": gid},
                    "label": f"{name} — group state", "field": "group_state",
                    "before": before, "after": after, "authored": False})

    for e in parsed.get("lore_edits", []):
        eid, append = e.get("id", ""), (e.get("append", "") or "").strip()
        if not eid or not append:
            continue
        kind = _entity_kind(cid, eid)
        if not kind:
            continue
        ent = overlay.read_entity(cid, kind, eid)
        before = ent["body"].strip()
        after = (before + "\n\n" + append).strip()
        out.append({"id": f"lore:{eid}", "kind": "lore", "target": {"kind": kind, "id": eid},
                    "label": f"{ent['meta'].get('name', eid)} — {kind}", "field": "body",
                    "before": before, "after": after, "authored": False})

    for e in parsed.get("authored_edits", []):
        char_id, field, text = e.get("id", ""), e.get("field", ""), (e.get("text", "") or "").strip()
        if not char_id or field not in _CARD_FIELDS or not text:
            continue
        vid = appearances_versions.locked_version(cid, "characters", char_id)
        if not vid:
            continue
        try:
            # locked_version returned a version, so the actor is in the appearance
            # record and its card is materialized campaign-side
            before = characters.read_card(appearances_paths.locked_actor_root(cid),
                                          char_id, vid)["data"].get(field, "").strip()
        except (characters.CharacterNotFound, characters.VersionNotFound):
            continue
        out.append({"id": f"authored:{char_id}:{field}", "kind": "authored",
                    "target": {"kind": "characters", "id": char_id},
                    "label": f"{_char_name(cid, char_id)} — {field} (card edit)",
                    "field": field, "before": before, "after": text, "authored": True})

    for e in parsed.get("relationship_deltas", []):
        frm, to = e.get("from", ""), e.get("to", "")
        if not _actor_exists(cid, frm) or not _actor_exists(cid, to):
            continue
        payload = {"from": frm, "to": to, "trust": e.get("trust", 0), "affection": e.get("affection", 0),
                   "tension": e.get("tension", 0), "note": e.get("note", "")}
        after = relationships._render_feeling(payload)
        cur = relationships.get_feeling(cid, frm, to)
        before = relationships._render_feeling(cur) if cur else ""
        if before == after:
            continue
        out.append({"id": f"feeling:{relationships.feeling_key(frm, to)}", "kind": "relationship",
                    "target": {"kind": "relationships", "id": relationships.feeling_key(frm, to)},
                    "label": f"{relationships.actor_name(cid, frm)} → {relationships.actor_name(cid, to)}",
                    "field": "feeling", "before": before, "after": after, "authored": False,
                    "payload": payload})

    for e in parsed.get("bond_changes", []):
        a_tok, b_tok, typ = e.get("a", ""), e.get("b", ""), (e.get("type", "") or "").strip()
        if not typ or not _actor_exists(cid, a_tok) or not _actor_exists(cid, b_tok):
            continue
        cur = relationships.get_bond(cid, a_tok, b_tok)
        before = cur["type"] if cur else ""
        if before == typ:
            continue
        out.append({"id": f"bond:{relationships.bond_key(a_tok, b_tok)}", "kind": "bond",
                    "target": {"kind": "relationships", "id": relationships.bond_key(a_tok, b_tok)},
                    "label": f"{relationships.actor_name(cid, a_tok)} & {relationships.actor_name(cid, b_tok)}",
                    "field": "bond", "before": before, "after": typ, "authored": False,
                    "payload": {"a": a_tok, "b": b_tok, "type": typ}})

    try:
        threads = plot.read(cid)
    except Exception:  # noqa: BLE001 — garbled plot.json: skip plot movements, don't 500
        threads = {}
    seen_pids: set[str] = set()
    for e in parsed.get("plot_movements", []):
        beat = (e.get("beat", "") or "").strip()
        if not beat:
            continue
        mid = (e.get("id", "") or "").strip()
        title = (e.get("title", "") or "").strip()
        status = e.get("status", "open")
        if mid:
            pid = mid
        elif any(c.isalnum() for c in title):
            pid = slugify(title)  # new thread — needs a title with real content
        else:
            continue  # no id and no usable title -> drop
        if pid in seen_pids:
            continue  # one edit per thread per scene (avoids duplicate ids / double-apply)
        seen_pids.add(pid)
        cur = threads.get(pid)
        if isinstance(cur, dict):  # existing thread (by id, or a new title that collides)
            # Rendered by `conflicts`, not here: the staleness check recomputes
            # this same line at save time, and two copies of the format would
            # let a harmless reformat read as a contradiction (#111).
            before = conflicts.plot_line(cur)
            disp_title = cur.get("title") or title or pid  # keep the stored title
        else:
            before, disp_title = "", title or pid
        out.append({"id": f"plot:{pid}", "kind": "plot",
                    "target": {"kind": "plot", "id": pid},
                    "label": f"{disp_title} — {status}",
                    "field": "beat", "before": before, "after": beat, "authored": False,
                    "payload": {"id": pid, "title": disp_title, "status": status, "scene": sid}})

    existing_char_names = {c["name"].strip().lower() for c in overlay.list_characters(cid)}
    for e in parsed.get("new_characters", []):
        name = (e.get("name", "") or "").strip()
        description = (e.get("description", "") or "").strip()
        if not name or not description:
            continue
        if name.lower() in existing_char_names:
            continue
        candidate_id = slugify(name)
        try:
            characters.read_character(overlay.char_root(cid, candidate_id), candidate_id)
            continue  # id already taken -- treat as the same character
        except characters.CharacterNotFound:
            pass
        # The reviewed description is the W++ block plus the generated history, so the
        # staged diff shows the full text that lands in the card's description field.
        history = (e.get("history", "") or "").strip()
        after = f"{description}\n\n{history}" if history else description
        out.append({"id": f"new_character:{candidate_id}", "kind": "new_character",
                    "target": {"kind": "characters", "id": ""},
                    "label": f"New character — {name}", "field": "description",
                    "before": "", "after": after, "authored": False,
                    "payload": {"name": name, "sd_prompt": e.get("sd_prompt", ""),
                                "personality": e.get("personality", ""),
                                "mes_example": e.get("mes_example", ""),
                                "evidence": e.get("evidence", ""),
                                "confidence": parse._confidence(e.get("confidence", "")),
                                "open_questions": e.get("open_questions", "")}})

    for kind, parsed_key, prefix, label_noun in (
        ("locations", "new_locations", "new_location", "location"),
        ("lore", "new_lore", "new_lore", "lore entry"),
    ):
        existing_names = {ent["name"].strip().lower() for ent in overlay.list_entities(cid, kind)}
        for e in parsed.get(parsed_key, []):
            name = (e.get("name", "") or "").strip()
            body = (e.get("body", "") or "").strip()
            if not name or not body:
                continue
            if name.lower() in existing_names:
                continue
            candidate_id = slugify(name)
            try:
                overlay.read_entity(cid, kind, candidate_id)
                continue
            except entities.EntityNotFound:
                pass
            payload = {"name": name, "keys": e.get("keys", "")}
            if kind == "locations":
                payload["sd_prompt"] = e.get("sd_prompt", "")
                payload["current_setting"] = e.get("current_setting", False)
            out.append({"id": f"{prefix}:{candidate_id}", "kind": prefix,
                        "target": {"kind": kind, "id": ""},
                        "label": f"New {label_noun} — {name}", "field": "body",
                        "before": "", "after": body, "authored": False,
                        "payload": payload})

    out.extend(weather._weather_edits(cid, sid, parsed))
    return out


class _DossierTargetGone(Exception):
    """A dossier edit whose character disappeared between staging and saving.
    Distinguished from an I/O failure so the reviewer is told which happened."""


def _new_character_provenance(after: str, payload: dict) -> str:
    lines = []
    evidence = (payload.get("evidence", "") or "").strip()
    confidence = parse._confidence(payload.get("confidence", ""))
    open_questions = (payload.get("open_questions", "") or "").strip()
    if evidence:
        lines.append(f"Evidence: {evidence}")
    lines.append(f"Confidence: {confidence}")
    if open_questions:
        lines.append(f"Open questions: {open_questions}")
    return (after.rstrip() + "\n\n## Play Provenance\n" + "\n".join(lines)).strip()


def _new_character_dossier(name: str, payload: dict) -> str:
    confidence = parse._confidence(payload.get("confidence", ""))
    evidence = (payload.get("evidence", "") or "").strip()
    open_questions = (payload.get("open_questions", "") or "").strip()
    parts = [f"{name} was introduced through play as a {confidence} emergent character."]
    if evidence:
        parts.append(f"Scene evidence: {evidence}")
    if open_questions:
        parts.append(f"Open questions: {open_questions}")
    return " ".join(parts)
