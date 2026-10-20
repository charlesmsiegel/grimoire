"""suggest: claims responsibilities."""

from __future__ import annotations

from ..continuity import drivers as continuity_drivers
from ..continuity import pressure
from . import _contracts, _dates


def _canon_map(snapshot: dict) -> dict[str, str]:
    """Alias source -> canonical, from the captured thread and commitment rows'
    `aliases` (no store read: what the prompt showed is what the reply is
    held to)."""
    rows = (*snapshot.get("open_threads", []), *snapshot.get("commitments", []))
    return {a["ref"]: row["ref"] for row in rows if "ref" in row
            for a in row.get("aliases") or [] if isinstance(a, dict) and "ref" in a}


def _pair(e, kinds: dict[str, str], canon: dict[str, str]) -> dict | None:
    """One model driver entry as a canonical `{ref, action}`, or None when it is
    malformed, unknown to the captured index, or wrong for its kind."""
    if not isinstance(e, dict):
        return None
    ref, action = e.get("ref"), e.get("action")
    if not isinstance(ref, str) or not isinstance(action, str):
        return None
    ref = canon.get(ref.strip(), ref.strip())
    allowed = continuity_drivers.ACTIONS_BY_KIND.get(kinds.get(ref, ""), ())
    return {"ref": ref, "action": action} if action in allowed else None


def _valid_pairs(entry: dict, snapshot: dict) -> list[dict]:
    """The entry's claimed drivers, validated against the captured index and
    de-duplicated by canonical ref (the first action wins)."""
    kinds = {d["ref"]: d["kind"] for d in snapshot.get("driver_index", [])}
    canon = _canon_map(snapshot)
    raw = entry.get("drivers")
    out: list[dict] = []
    for e in raw if isinstance(raw, list) else []:
        pair = _pair(e, kinds, canon)
        if pair is not None and pair["ref"] not in {p["ref"] for p in out}:
            out.append(pair)
    return out


def _anchor_of(entry: dict, snapshot: dict, controls: _contracts.Controls) -> dict | None:
    """`{ref, relation}`: the request's anchor when the batch has one (Decision
    14), else the model's when it names an anchor option (Decision 5: a passed
    or undated event is a driver, never an anchor). The relation is the
    request's, else the model's if valid, else `on`; a month-only birthday only
    ever takes `on` (Decision 6)."""
    model = entry.get("time_anchor")
    model = model if isinstance(model, dict) else {}
    relation = model.get("relation")
    relation = relation if isinstance(relation, str) and relation in _contracts.ANCHOR_RELATIONS else ""
    if controls.anchor:
        ref, relation = controls.anchor, controls.relation or relation
    else:
        raw_ref = model.get("ref")
        ref = raw_ref.strip() if isinstance(raw_ref, str) else ""
        if ref not in {a["ref"] for a in snapshot.get("anchors", [])}:
            return None
    if _dates._month_of(ref) is not None:
        relation = "on"
    return {"ref": ref, "relation": relation or "on"}


def _misses(refs: set[str], controls: _contracts.Controls) -> tuple[list[str], list[str]]:
    """`(unmet_must, avoided)` over the claimed refs, in request order."""
    return ([r for r in controls.must if r not in refs],
            [r for r in controls.avoid if r in refs])


def claim(entry: dict, snapshot: dict, controls: _contracts.Controls) -> dict:
    """What one suggestion validly claims (spec §15.2, Decision 14):
    `{drivers: [{ref, action}], time_anchor: {ref, relation} | None,
    unmet_must: [ref], avoided: [ref]}`.

    Model refs canonicalize through the captured rows' aliases, and an unknown
    ref or a wrong action for its kind is dropped -- and nothing else is: a
    temporal driver claimed as `anchor` in `drivers` stays, so a passed or
    undated event (a driver, never an anchor) can be claimed at all. The time
    anchor is added to `drivers` as `anchor` when missing. "A record never
    claims two anchors" is the save's rule (`idea_provenance`, Decision 16),
    not the card's. Avoided refs are reported and left out of `drivers`; an
    avoided anchor stays the anchor. Nothing here drops the suggestion."""
    anchor = _anchor_of(entry, snapshot, controls)
    kept = anchor["ref"] if anchor else None
    pairs = _valid_pairs(entry, snapshot)
    if kept is not None and kept not in {p["ref"] for p in pairs}:
        pairs.append({"ref": kept, "action": "anchor"})
    claimed = {p["ref"] for p in pairs}
    unmet, avoided = _misses(claimed, controls)
    return {"drivers": [p for p in pairs if p["ref"] not in controls.avoid],
            "time_anchor": anchor, "unmet_must": unmet, "avoided": avoided}


def _claimed_refs(row: dict) -> list[str]:
    anchor = row.get("time_anchor")
    return [*(d["ref"] for d in row.get("drivers", [])), *([anchor["ref"]] if anchor else [])]


def order_cards(rows: list[dict], snapshot: dict, controls: _contracts.Controls) -> list[dict]:
    """A stable re-ordering (Decision 13): the first card claiming a
    high-pressure driver, then the earliest card covering each focus ref not
    yet covered, then the rest in model order."""
    states = {d["ref"]: d["pressure"]["state"] for d in snapshot.get("driver_index", [])}
    remaining = list(rows)
    hot = next((i for i, r in enumerate(remaining)
                if any(states.get(ref) in pressure.HIGH_PRESSURE for ref in _claimed_refs(r))),
               None)
    out = [remaining.pop(hot)] if hot is not None else []
    covered = {ref for row in out for ref in _claimed_refs(row)}
    focus = set(controls.focus)
    while True:
        nxt = next((i for i, r in enumerate(remaining)
                    if (focus & set(_claimed_refs(r))) - covered), None)
        if nxt is None:
            return out + remaining
        out.append(remaining.pop(nxt))
        covered.update(_claimed_refs(out[-1]))
