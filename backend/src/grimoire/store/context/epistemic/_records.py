"""Versioned, validated frozen-view encoding/decoding for round resume without rereading live knowledge."""

from __future__ import annotations

from dataclasses import asdict

from ...history import model
from . import _contracts

# ---------------------------------------------------------------- the record

#: `to_record`'s format. A record of another version reads as nothing.
RECORD_VERSION = 1


def _post_record(p: model.EvidencePost) -> list:
    return [p.index, p.key, p.part, p.speaker, p.text]


def _post_of(r) -> model.EvidencePost:
    index, key, part, speaker, text = r
    if not (type(index) is int and isinstance(key, str) and (part is None or type(part) is int)
            and isinstance(speaker, str) and isinstance(text, str)):
        raise ValueError("not a post")
    return model.EvidencePost(index, key, part, speaker, text)


def _strings(value) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("not a list of strings")
    return tuple(value)


def _ints(value) -> tuple[int, ...]:
    if not isinstance(value, list) or not all(type(v) is int for v in value):
        raise ValueError("not a list of integers")
    return tuple(value)


def to_record(view: _contracts.EpistemicView) -> dict:
    """`view` as JSON, everything a compose renders from it: what a roll
    resume in a later request composes with (spec 11 §5.5), stored in the
    response's frozen snapshot. The retrieval keeps its sizing and coverage;
    its items and candidates are left out, since every section renders from
    the classified items alone."""
    p = view.perspective
    ev = view.evidence
    return {
        "version": RECORD_VERSION,
        "perspective": {"cid": p.cid, "sid": p.sid, "identity": p.identity,
                        "actor": p.actor, "present": list(p.present)},
        "decided": view.decided,
        "pending": [[k, part, list(posts)] for k, part, posts in view.pending],
        "round": dict(view.round_cost),
        "evidence": {"ceiling": ev.ceiling, "depth": ev.depth, "digest": ev.query_digest,
                     "coverage": {**asdict(ev.coverage), "tiers": list(ev.coverage.tiers)},
                     # Roadmap 10's trace, so a resumed compose still says how
                     # its evidence was planned. Absent when nothing planned.
                     **({"plan": ev.plan.projection()} if ev.plan is not None else {})},
        "items": [{
            "scene": [c.item.scene.sid, c.item.scene.identity, c.item.scene.campaign],
            "title": c.item.title, "date": c.item.date, "location": c.item.location,
            "summary": c.item.summary,
            "excerpts": [{"posts": [_post_record(q) for q in e.posts], "text": e.text}
                         for e in c.item.excerpts],
            "evidence_ids": list(c.item.evidence_ids), "tokens": c.item.tokens,
            "summary_omission": c.item.summary_omission,
            # A withheld part keeps its class and keys, never its text: the
            # record is what the contribution was shown.
            "slices": [{"part": s.part, "posts": list(s.posts), "access": s.access,
                        "basis": list(s.basis),
                        "text": s.text if s.access in _contracts.VISIBLE else "",
                        "source": [_post_record(q) for q in s.source]
                        if s.access in _contracts.VISIBLE else [],
                        "keys": [dict(k) for k in s.keys]} for s in c.slices],
            "header": dict(c.header), "state": c.state, "known_to": list(c.known_to),
            "suspected_by": list(c.suspected_by), "admitted": c.admitted,
            "by_ref": {ref: {part: list(cls) for part, cls in parts.items()}
                       for ref, parts in c.by_ref.items()},
            "sid": c.sid, "stood": c.stood,
        } for c in view.items],
    }


def _slice_of(r: dict) -> _contracts.Slice:
    if r["access"] not in _contracts.ACCESS or r["part"] not in ("posts", "summary"):
        raise ValueError("not a slice")
    keys = r["keys"]
    if not isinstance(keys, list) or not all(
            isinstance(k, dict) and all(isinstance(v, str) for v in k.values()) for k in keys):
        raise ValueError("not a slice's keys")
    if not isinstance(r["text"], str):
        raise ValueError("not a slice's text")
    return _contracts.Slice(r["part"], _ints(r["posts"]), r["access"], _strings(r["basis"]), r["text"],
                 tuple(_post_of(q) for q in r["source"]), tuple(dict(k) for k in keys))


def _scene_ref_of(r) -> model.SceneRef:
    sid, identity, campaign = r
    if not (isinstance(sid, str) and (identity is None or isinstance(identity, str))
            and isinstance(campaign, str)):
        raise ValueError("not a scene ref")
    return model.SceneRef(sid, identity, campaign)


def _header_of(r) -> dict:
    if not isinstance(r, dict) or not all(isinstance(v, str) for v in r.values()):
        raise ValueError("not a header")
    return dict(r)


def _item_of(r: dict) -> _contracts.Classified:
    scene = _scene_ref_of(r["scene"])
    for name in ("title", "date", "location", "summary", "summary_omission", "state", "sid"):
        if not isinstance(r[name], str):
            raise ValueError(f"not an item's {name}")
    item = model.EvidenceItem(
        scene, r["title"], date=r["date"],
        location=r["location"], summary=r["summary"],
        excerpts=tuple(model.Excerpt(tuple(_post_of(q) for q in e["posts"]), e["text"])
                       for e in r["excerpts"]),
        evidence_ids=_strings(r["evidence_ids"]), tokens=int(r["tokens"]),
        summary_omission=r["summary_omission"])
    header = _header_of(r["header"])
    by_ref = {str(ref): {str(part): (str(cls[0]), str(cls[1])) for part, cls in parts.items()}
              for ref, parts in r["by_ref"].items()}
    return _contracts.Classified(item, tuple(_slice_of(s) for s in r["slices"]), header,
                      r["state"], _strings(r["known_to"]), _strings(r["suspected_by"]),
                      admitted=r["admitted"] is True, by_ref=by_ref, sid=r["sid"],
                      stood=r["stood"] is True)


def from_record(record) -> _contracts.EpistemicView | None:
    """`to_record`'s view again, or None for anything that is not one (an
    older format, a hand edit): a resume then composes with no history, which
    narrows. Compose still checks the view against its own scene and speaker."""
    try:
        if not isinstance(record, dict) or record.get("version") != RECORD_VERSION:
            return None
        p = record["perspective"]
        actor = p["actor"]
        if actor is not None and not isinstance(actor, str):
            return None
        perspective = _contracts.Perspective(str(p["cid"]), str(p["sid"]),
                                  p["identity"] if isinstance(p["identity"], str) else None,
                                  actor, _strings(p["present"]))
        ev = record["evidence"]
        coverage = dict(ev["coverage"])
        coverage["tiers"] = _ints(coverage.get("tiers", [1]))
        evidence = model.Evidence(coverage=model.Coverage(**coverage), ceiling=int(ev["ceiling"]),
                                  depth=int(ev["depth"]), query_digest=str(ev["digest"]),
                                  plan=model.PlanTrace.from_projection(ev.get("plan")))
        pending = tuple((str(k), str(part), _ints(posts)) for k, part, posts in record["pending"])
        round_cost = {str(k): int(v) for k, v in record["round"].items()}
        return _contracts.EpistemicView(perspective, tuple(_item_of(c) for c in record["items"]),
                             decided=int(record["decided"]), pending=pending,
                             evidence=evidence, round_cost=round_cost)
    except Exception:  # noqa: BLE001 -- a record that does not read is no history, never a failed resume
        _contracts.log.warning("A stored history view could not be read")
        return None
