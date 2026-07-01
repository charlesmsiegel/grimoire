"""The scene-absorption extraction: one deterministic-primed LLM call producing the
chronicle summary plus proposed state/lore/authored edits, their diff materialization,
and their application. Prompt/parse only here + pure materialize/apply; the LLM call
lives in the route layer (mirrors briefs.py).
"""

from __future__ import annotations

import json

from . import appearances, campaigns, characters, chronicle, entities, playstate

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
    'and "authored_edits" (list of {"id","field","text"} — ONLY when a character\'s core '
    "card field (description/personality/scenario) fundamentally and durably changed; rare). "
    "Write in third person, past tense. Use the ids given in the context block."
)


def build_prompt(transcript: str, facts: dict, state_snapshot: dict | None = None) -> list[dict]:
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

    return {
        "one_line": str(obj.get("one_line", "")).strip(),
        "summary": str(obj.get("summary", "")).strip(),
        "keywords": [str(k).strip() for k in obj.get("keywords", []) if str(k).strip()],
        "timeline_events": _list("timeline_events", ("date", "text")),
        "character_state_edits": _list("character_state_edits", ("id", "current_state")),
        "lore_edits": _list("lore_edits", ("id", "append")),
        "authored_edits": _list("authored_edits", ("id", "field", "text")),
    }


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
