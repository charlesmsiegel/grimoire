"""The scene-absorption extraction: one deterministic-primed LLM call producing the
chronicle summary plus proposed state/lore/authored edits, their diff materialization,
and their application. Prompt/parse only here + pure materialize/apply; the LLM call
lives in the route layer (mirrors briefs.py).
"""

from __future__ import annotations

import json

from . import appearances, campaigns, characters, chronicle, entities, pcs, playstate, relationships

EXTRACT_INSTRUCTION = (
    "You are absorbing a completed role-play scene into a campaign chronicle and "
    "evolving its records. Read the transcript and reply with ONLY a JSON object, no "
    "prose around it, with keys: "
    '"one_line" (a one-sentence summary), "summary" (one self-contained paragraph), '
    '"keywords" (list of significant nouns/concepts, lowercase), '
    '"timeline_events" (list of {"date","text"} for concrete datable HAPPENINGS; [] if none), '
    '"character_state_edits" (list of {"id","current_state"} — for each present character '
    "whose standing condition changed, the FULL rewritten snapshot of who they are now, "
    "dropping what is no longer true; standing conditions only, not events), "
    '"lore_edits" (list of {"id","append"} — a paragraph to add to a lore/location entry), '
    '"authored_edits" (list of {"id","field","text"} — ONLY when a character\'s core '
    "card field (description/personality/scenario) fundamentally and durably changed; rare), "
    '"relationship_deltas" (list of {"from","to","trust","affection","tension","note"} — '
    "for each directed pair whose feelings changed, the FULL updated values; use the "
    '"<kind>:<id>" tokens from the context block; trust/affection/tension are 0-5), '
    'and "bond_changes" (list of {"a","b","type"} — a shared relationship type for a pair). '
    "Write in third person, past tense. Use the ids given in the context block."
)


def _int05(v) -> int:
    try:
        return max(0, min(5, int(v)))
    except (ValueError, TypeError):
        return 0


def build_prompt(transcript: str, facts: dict, state_snapshot: dict | None = None,
                 rel_snapshot: str | None = None) -> list[dict]:
    head = []
    if facts.get("location"):
        head.append(f"Location: {facts['location']}")
    if facts.get("date"):
        head.append(f"Date: {facts['date']}")
    if facts.get("cast"):
        head.append("Present: " + ", ".join(facts["cast"]))
    if state_snapshot:
        head.append("Current character state:\n" +
                    "\n".join(f"- {name}: {s}" for name, s in state_snapshot.items()))
    if rel_snapshot:
        head.append("Current relationships:\n" + rel_snapshot)
    prefix = ("\n".join(head) + "\n\n") if head else ""
    return [{"role": "system", "content": EXTRACT_INSTRUCTION},
            {"role": "user", "content": prefix + transcript}]


def _obj(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    raw = text[start:end + 1] if start != -1 and end > start else ""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        obj = {}
    return obj if isinstance(obj, dict) else {}


def parse_output(text: str) -> dict:
    obj = _obj(text)

    def _list(key, fields):
        out = []
        for e in obj.get(key, []):
            if isinstance(e, dict):
                out.append({f: str(e.get(f, "")).strip() for f in fields})
        return out

    rel_deltas = []
    for e in obj.get("relationship_deltas", []):
        if isinstance(e, dict):
            rel_deltas.append({"from": str(e.get("from", "")).strip(), "to": str(e.get("to", "")).strip(),
                               "trust": _int05(e.get("trust")), "affection": _int05(e.get("affection")),
                               "tension": _int05(e.get("tension")), "note": str(e.get("note", "")).strip()})

    return {
        "one_line": str(obj.get("one_line", "")).strip(),
        "summary": str(obj.get("summary", "")).strip(),
        "keywords": [str(k).strip() for k in obj.get("keywords", []) if str(k).strip()],
        "timeline_events": _list("timeline_events", ("date", "text")),
        "character_state_edits": _list("character_state_edits", ("id", "current_state")),
        "lore_edits": _list("lore_edits", ("id", "append")),
        "authored_edits": _list("authored_edits", ("id", "field", "text")),
        "relationship_deltas": rel_deltas,
        "bond_changes": _list("bond_changes", ("a", "b", "type")),
    }


_CARD_FIELDS = ("description", "personality", "scenario")


def _char_name(croot, cid: str) -> str:
    try:
        return characters.read_character(croot, cid)["meta"].get("name", cid)
    except characters.CharacterNotFound:
        return cid


def _entity_kind(croot, eid: str) -> str | None:
    for kind in ("lore", "locations"):
        try:
            entities.read_entity(croot, kind, eid)
            return kind
        except entities.EntityNotFound:
            continue
    return None


def materialize(cid: str, sid: str, parsed: dict) -> list[dict]:
    """Turn the parsed edit lists into before/after StagedEdits against the campaign
    copies. Targets that don't exist are dropped (tolerated, not an error)."""
    croot = campaigns.campaign_root(cid)
    out: list[dict] = []

    for e in parsed.get("character_state_edits", []):
        char_id, after = e.get("id", ""), (e.get("current_state", "") or "").strip()
        if not char_id or not after:
            continue
        try:
            characters.read_character(croot, char_id)
        except characters.CharacterNotFound:
            continue
        st = playstate.read_state(croot, char_id)
        out.append({"id": f"character_state:{char_id}", "kind": "character_state",
                    "target": {"kind": "characters", "id": char_id},
                    "label": f"{_char_name(croot, char_id)} — current state",
                    "field": "current_state",
                    "before": st["current_state"] if st else "", "after": after,
                    "authored": False})

    for e in parsed.get("lore_edits", []):
        eid, append = e.get("id", ""), (e.get("append", "") or "").strip()
        if not eid or not append:
            continue
        kind = _entity_kind(croot, eid)
        if not kind:
            continue
        ent = entities.read_entity(croot, kind, eid)
        before = ent["body"].strip()
        after = (before + "\n\n" + append).strip()
        out.append({"id": f"lore:{eid}", "kind": "lore", "target": {"kind": kind, "id": eid},
                    "label": f"{ent['meta'].get('name', eid)} — {kind}", "field": "body",
                    "before": before, "after": after, "authored": False})

    for e in parsed.get("authored_edits", []):
        char_id, field, text = e.get("id", ""), e.get("field", ""), (e.get("text", "") or "").strip()
        if not char_id or field not in _CARD_FIELDS or not text:
            continue
        vid = appearances.locked_version(cid, "characters", char_id)
        if not vid:
            continue
        try:
            before = characters.read_card(croot, char_id, vid)["data"].get(field, "").strip()
        except (characters.CharacterNotFound, characters.VersionNotFound):
            continue
        out.append({"id": f"authored:{char_id}:{field}", "kind": "authored",
                    "target": {"kind": "characters", "id": char_id},
                    "label": f"{_char_name(croot, char_id)} — {field} (card edit)",
                    "field": field, "before": before, "after": text, "authored": True})

    return out


def apply_edits(cid: str, edits: list[dict]) -> list[str]:
    """Apply each approved StagedEdit to the campaign copies. Best-effort: a missing or
    broken target is skipped. Returns the ids actually applied."""
    croot = campaigns.campaign_root(cid)
    applied: list[str] = []
    for e in edits:
        try:
            kind, target, after = e["kind"], e["target"], e.get("after", "")
            if kind == "character_state":
                playstate.write_state(croot, target["id"], after)
            elif kind == "lore":
                entities.update_entity(croot, target["kind"], target["id"], body=after)
            elif kind == "authored":
                if e["field"] not in _CARD_FIELDS:
                    continue  # re-guard: PUT edits are client-supplied, not re-materialized
                vid = appearances.locked_version(cid, "characters", target["id"])
                card = characters.read_card(croot, target["id"], vid)
                card["data"][e["field"]] = after
                characters.update_version(croot, target["id"], vid, card)
            else:
                continue
            applied.append(e["id"])
        except Exception:  # noqa: BLE001 — best-effort per edit
            continue
    return applied


def relationships_snapshot(cid: str, sid: str) -> str:
    """Rendered present-cast feelings/bonds block (feeds the prompt)."""
    croot = campaigns.campaign_root(cid)
    tokens = [f"{a['kind']}:{a['id']}" for a in appearances.scene_cast(cid, sid)]
    return "\n".join(relationships.render_present(cid, tokens, lambda t: relationships.actor_name(croot, t)))


def state_snapshot(cid: str, sid: str) -> dict:
    """Present NPCs' existing current_state, keyed by display name (feeds the prompt)."""
    croot = campaigns.campaign_root(cid)
    out: dict[str, str] = {}
    for a in appearances.scene_cast(cid, sid):
        if a["role"] != "npc" or a["kind"] != "characters":
            continue
        st = playstate.read_state(croot, a["id"])
        if st and st["current_state"]:
            try:
                name = characters.read_character(croot, a["id"])["meta"].get("name", a["id"])
            except characters.CharacterNotFound:
                name = a["id"]
            out[name] = st["current_state"]
    return out
