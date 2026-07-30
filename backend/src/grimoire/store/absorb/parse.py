"""Parsing the model's extraction reply into the fixed set of edit sections.

`parse_output` rebuilds a dict of known keys rather than passing the model's
object through, so the section list below *is* the absorb contract:
`evals/graders.py` derives the graded contract from `parse_output("{}")` rather
than restating it, and `materializer.py` reads exactly these keys.
"""

from __future__ import annotations

import json

from .. import groupstate, plot


def _int05(v) -> int:
    try:
        return max(0, min(5, int(v)))
    except (ValueError, TypeError):
        return 0


def _truthy(v) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() == "true")


def _confidence(v: str) -> str:
    c = (v or "").strip().lower()
    return c if c in {"thin", "sketched", "established"} else "thin"


def extract_object(text: str) -> dict | None:
    """The JSON object embedded in a reply, tolerating prose or a markdown
    fence around it. None when there is no decodable object at all.

    None rather than {} — and public rather than private — because "the model
    returned no JSON" (a format failure: it refused, or wrote prose, or got
    truncated) and "the model returned an empty object" (an extraction failure:
    it understood the format and found nothing to say) have different causes
    and different fixes. parse_output cannot tell them apart on its own; both
    arrive as a dict of empty defaults. evals/graders.py reports them
    separately, which is only possible if this function keeps the difference.
    """
    start, end = text.find("{"), text.rfind("}")
    raw = text[start:end + 1] if start != -1 and end > start else ""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def parse_output(text: str) -> dict:
    obj = extract_object(text) or {}

    def _str(e, f):
        # A JSON `null` is a present-but-empty value the model uses interchangeably with
        # omitting the key (e.g. "no existing id, this is new"); str(e.get(f, "")) turns
        # that null into the literal text "None" instead of "", corrupting ids/titles
        # downstream. `.get(f) or ""` collapses null and absent to the same "".
        return str(e.get(f) or "").strip()

    def _list(key, fields):
        out = []
        for e in obj.get(key, []):
            if isinstance(e, dict):
                out.append({f: _str(e, f) for f in fields})
        return out

    # Preserve key PRESENCE for knowledge: a field the model omitted must be left
    # untouched at materialize time (keep-on-omit), while an explicit "" clears it. So we
    # only carry knows/suspects into the row when the model actually returned them.
    cs_edits = []
    for e in obj.get("character_state_edits", []):
        if not isinstance(e, dict):
            continue
        row = {"id": _str(e, "id"), "current_state": _str(e, "current_state")}
        for k in ("knows", "suspects"):
            if k in e:
                row[k] = _str(e, k)
        cs_edits.append(row)

    # Same key-presence rule as character_state_edits, for all five sections.
    gs_edits = []
    for e in obj.get("group_state_edits", []):
        if not isinstance(e, dict):
            continue
        row = {"id": _str(e, "id")}
        for k in groupstate.FIELDS:
            if k in e:
                row[k] = _str(e, k)
        gs_edits.append(row)

    rel_deltas = []
    for e in obj.get("relationship_deltas", []):
        if isinstance(e, dict):
            rel_deltas.append({"from": _str(e, "from"), "to": _str(e, "to"),
                               "trust": _int05(e.get("trust")), "affection": _int05(e.get("affection")),
                               "tension": _int05(e.get("tension")), "note": _str(e, "note")})

    plot_moves = []
    for e in obj.get("plot_movements", []):
        if isinstance(e, dict):
            status = _str(e, "status").lower()
            plot_moves.append({"id": _str(e, "id"), "title": _str(e, "title"),
                               "status": status if status in plot.STATUSES else "open",
                               "beat": _str(e, "beat")})

    new_characters = _list("new_characters",
                           ("name", "description", "history", "personality",
                            "mes_example", "evidence", "confidence",
                            "open_questions", "sd_prompt"))
    for e in new_characters:
        e["confidence"] = _confidence(e.get("confidence", ""))

    new_locations = []
    for e in obj.get("new_locations", []):
        if not isinstance(e, dict):
            continue
        new_locations.append({
            "name": _str(e, "name"), "body": _str(e, "body"), "keys": _str(e, "keys"),
            "sd_prompt": _str(e, "sd_prompt"), "current_setting": _truthy(e.get("current_setting")),
        })

    new_lore = _list("new_lore", ("name", "body", "keys"))

    return {
        "one_line": str(obj.get("one_line", "")).strip(),
        "summary": str(obj.get("summary", "")).strip(),
        "keywords": [str(k).strip() for k in obj.get("keywords", []) if str(k).strip()],
        "timeline_events": _list("timeline_events", ("date", "text")),
        "character_state_edits": cs_edits,
        "group_state_edits": gs_edits,
        "lore_edits": _list("lore_edits", ("id", "append")),
        "authored_edits": _list("authored_edits", ("id", "field", "text")),
        "relationship_deltas": rel_deltas,
        "bond_changes": _list("bond_changes", ("a", "b", "type")),
        "plot_movements": plot_moves,
        "new_characters": new_characters,
        "new_locations": new_locations,
        "new_lore": new_lore,
        # Listed explicitly because this function rebuilds a dict of known keys
        # rather than passing the model's object through: an unlisted key is
        # dropped on the floor here, and the materialize and apply branches in
        # materializer.py and apply.py become unreachable no matter how well
        # the prompt performs.
        "weather_edits": _list("weather_edits",
                               ("location", "condition", "temperature", "wind",
                                "duration_blocks", "note")),
    }
