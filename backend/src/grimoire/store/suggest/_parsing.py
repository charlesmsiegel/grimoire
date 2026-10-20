"""suggest: parsing responsibilities."""

from __future__ import annotations

import json

from .. import clock, fieldtext
from ..campaigns import paths as campaigns_paths
from ..scenes import focus as scenes_focus
from . import _claims, _contracts, _dates, _references


def _extract_json(text: str):
    """Tolerant of the model wrapping JSON in prose and of a bare top-level array
    (a common LLM deviation from the requested {"suggestions": [...]} object). Tries the
    whole reply first (clean object or array), then a brace slice, then a bracket slice."""
    candidates = [text.strip()]
    for lo, hi in (("{", "}"), ("[", "]")):
        s, e = text.find(lo), text.rfind(hi)
        if s != -1 and e > s:
            candidates.append(text[s:e + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def raw_suggestions(text: str) -> list | None:
    """The reply's suggestion list: a dict's `"suggestions"`, or a bare array (a
    common LLM deviation). None when nothing in the text decodes, `[]` when what
    decoded holds no list. Shared by `parse_output` and the eval grader."""
    parsed = _extract_json(text)
    if parsed is None:
        return None
    suggestions = parsed.get("suggestions", []) if isinstance(parsed, dict) else parsed
    return suggestions if isinstance(suggestions, list) else []


def _cast_of(entry: dict, ids: tuple, offscreen: bool) -> list[str]:
    char_ids, player_tokens, _loc_ids = ids
    raw_cast = entry.get("cast", [])
    return ([t for t in (str(x).strip() for x in raw_cast)
             if _references.token_ok(t, char_ids, player_tokens, offscreen)]
            if isinstance(raw_cast, list) else [])


def is_card(entry) -> bool:
    """Is this raw entry one `parse_output` keeps -- an object with a title and
    a premise? Shared with the eval grader, so it judges the cards the player
    is shown rather than every entry the reply holds."""
    return (isinstance(entry, dict) and bool(str(entry.get("title", "")).strip())
            and bool(str(entry.get("premise", "")).strip()))


# JSON null, numbers and objects are missing text, never literal titles such as "None".
def _card(entry, ctx: dict) -> dict | None:
    """One parsed suggestion with its validated provenance (spec §15.2), or
    None when `is_card` says the app does not keep it."""
    if not is_card(entry):
        return None
    title, premise = str(entry.get("title", "")).strip(), str(entry.get("premise", "")).strip()
    snap, controls, provider = ctx["snapshot"], ctx["controls"], ctx["provider"]
    claimed = _claims.claim(entry, snap, controls)
    date = _dates.normalize_date(provider, snap.get("now") or "", entry.get("date", ""))
    date, rejected_by = _dates.judge_date(provider, snap, controls, claimed["time_anchor"], date)
    fixed = _dates._fixed(provider, date)
    loc = str(entry.get("location", "")).strip()
    labels = ctx["labels"]

    def named(refs: list[str]) -> list[dict]:
        return [{"ref": r, "label": labels.get(r, r)} for r in refs]

    return {"title": title, "premise": premise,
            "focus": scenes_focus.normalize(fieldtext.text(entry.get("focus", ""))),
            "cast": _cast_of(entry, ctx["ids"], ctx["offscreen"]),
            "location": loc if loc in ctx["ids"][2] else "",
            "date": date, "date_friendly": _dates._friendly_of(provider, fixed),
            "in_days": (fixed - ctx["now"] if fixed is not None and ctx["now"] is not None
                        else None),
            "date_rejected": rejected_by is not None, "date_rejected_by": rejected_by,
            "drivers": [{"ref": d["ref"], "kind": ctx["kinds"].get(d["ref"], d["ref"].split(":")[0]),
                         "action": d["action"], "label": labels.get(d["ref"], d["ref"])}
                        for d in claimed["drivers"]],
            "time_anchor": _anchor_card(claimed["time_anchor"], snap, labels),
            "unmet_must": named(claimed["unmet_must"]), "avoided": named(claimed["avoided"])}


def _anchor_card(anchor: dict | None, snapshot: dict, labels: dict[str, str]) -> dict | None:
    if anchor is None:
        return None
    ref = anchor["ref"]
    opt: dict = next((a for a in snapshot.get("anchors", []) if a["ref"] == ref), {})
    return {"ref": ref, "kind": opt.get("kind") or ref.split(":", 1)[0],
            "relation": anchor["relation"], "label": opt.get("label") or labels.get(ref, ref),
            "friendly": opt.get("friendly") or "", "in_days": opt.get("in_days")}


def parse_output(text: str, cid: str, offscreen: bool = False, *,
                 snapshot: dict | None = None, controls: _contracts.Controls | None = None) -> list[dict]:
    """The reply's suggestions, every id and date validated, each with its
    claimed drivers and time anchor resolved against `snapshot` -- the capture
    the prompt was rendered from (spec §16.3) -- and in `order_cards` order.

    Without a snapshot the index is empty and no control applies: the rows
    carry today's fields plus empty provenance. The calendar is resolved once,
    softly (Decision 25): a plugin that cannot load blanks every date and
    rejects none (§26). Store failures under `valid_ids` still surface as the
    run's ordinary failure."""
    suggestions = raw_suggestions(text)
    if not suggestions:
        return []
    snap: dict = snapshot if snapshot is not None else {"now": clock.now(cid)}
    controls = controls or _contracts.NO_CONTROLS
    provider = _dates._soft_provider(campaigns_paths.campaign_root(cid))
    ctx = {"snapshot": snap, "controls": controls, "provider": provider,
           "now": _dates._now_fixed(provider, snap) if provider is not None else None,
           "ids": _references.valid_ids(cid), "offscreen": offscreen,
           "labels": {d["ref"]: d["label"] for d in snap.get("driver_index", [])},
           "kinds": {d["ref"]: d["kind"] for d in snap.get("driver_index", [])}}
    rows = [row for row in (_card(e, ctx) for e in suggestions) if row is not None]
    return _claims.order_cards(rows, snap, controls)


def parse_intent(reply: str, cid: str, offscreen: bool = False) -> dict:
    """Metadata extracted from the user's own description, every field validated
    against the campaign.

    Malformed or semantically invalid model output never raises — extraction is
    a convenience, and a miss must leave the user a blank form rather than an
    error. Store failures underneath (`valid_ids` reads entities) are NOT
    covered by that and surface as the route's ordinary 500, exactly as they do
    for `parse_output`. A calendar plugin that cannot load is: the provider
    resolves softly (Decision 25), so the date comes back blank."""
    empty = {"title": "", "date": "", "location": "", "cast": [], "focus": ""}
    parsed = _extract_json(reply)
    if isinstance(parsed, list):   # a bare array is a common LLM deviation
        parsed = next((e for e in parsed if isinstance(e, dict)), None)
    if not isinstance(parsed, dict):
        return empty
    char_ids, player_tokens, loc_ids = _references.valid_ids(cid)
    raw_cast = parsed.get("cast", [])
    cast = ([t for t in (str(x).strip() for x in raw_cast)
             if _references.token_ok(t, char_ids, player_tokens, offscreen)]
            if isinstance(raw_cast, list) else [])
    loc = fieldtext.text(parsed.get("location", ""))
    return {"title": fieldtext.text(parsed.get("title", "")),
            "focus": scenes_focus.normalize(fieldtext.text(parsed.get("focus", ""))),
            "date": _dates.date_normalizer(cid, tolerant=True)(fieldtext.text(parsed.get("date", ""))),
            "location": loc if loc in loc_ids else "",
            "cast": cast}


def parse_next_date(text: str, cid: str, *, snapshot: dict | None = None,
                    controls: _contracts.Controls | None = None) -> str:
    """The model's general next-scene date estimate, validated; "" when
    absent/bad. `near`/`move` check it; an anchor does not, since it answers
    "if none is used" and an anchor constrains suggestions (Decision 12)."""
    parsed = _extract_json(text)
    raw = parsed.get("next_date", "") if isinstance(parsed, dict) else ""
    provider = _dates._soft_provider(campaigns_paths.campaign_root(cid))
    snap: dict = snapshot if snapshot is not None else {"now": clock.now(cid)}
    date = _dates.normalize_date(provider, snap.get("now") or "", raw)
    return _dates.check_date(provider, snap, controls or _contracts.NO_CONTROLS, None, date)[0]


def parse_greeting_picks(text: str, allowed: set[str]) -> list[str]:
    """The model's greeting_picks, kept in order: unknown ids and duplicates drop."""
    parsed = _extract_json(text)
    picks = parsed.get("greeting_picks", []) if isinstance(parsed, dict) else []
    if not isinstance(picks, list):
        return []
    out: list[str] = []
    for p in picks:
        if isinstance(p, str) and p in allowed and p not in out:
            out.append(p)
    return out
