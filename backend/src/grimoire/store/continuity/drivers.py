"""Scene drivers and date anchors (capstone spec §14): what can motivate a next
scene, composed from `pressure`, `effective` and `involvement`.

A **driver** is a live canonical thread or commitment, or an event, holiday or
birthday pressure item. Each carries one pressure reading, the actors who stood
in a scene it touched, the events its temporal links tie it to
(`time_anchors`) and its reviewed links. **Anchors** are the temporal drivers a
suggestion may date itself against: every dated event that has not passed (an
event is the campaign's own finite list), and every holiday and birthday
(already bounded by pressure's horizon). An undated event is a driver and never
an anchor -- nothing can be derived against a day nobody can name.

Pressure (Decision 10): a thread is `stale` when aging says so, else `ok`; a
commitment takes the most urgent of its own pressure items and aging's `stale`;
a temporal driver is its item. Aging reads the *effective* rows, so it measures
the merged `last_scene`, not the canonical's alone.

`offscreen` drops the player's tokens from every `actors` list -- every `pcs:*`
and every roster actor seated as `player`, the set `suggest.build_snapshot`
strips, recomputed from the roster because this module may not import
`suggest`. A PC's birthday stays a driver, with no actors. When `offscreen` is
set and the roster cannot be read, every `actors` list is empty: the filter
fails closed rather than open.

Read-only and pure: nothing here writes, takes a lock, or calls a model or an
embedding endpoint (`matching` only reads config). Every input is read through
`_soft`, because a calendar provider can be plugin code that raises anything
(`aging.prepare` resolves one outside any `try`), and a garbled ledger must
cost its own drivers rather than the read.

`DRIVER_ACTIONS` is §20's action vocabulary: what a scene suggestion may claim
to do to a driver, with `ACTIONS_BY_KIND` saying which of them a driver of each
kind accepts. `snapshot` takes an already computed `pressure_result` so that a
caller which needs the pressure items too (the suggestion snapshot's timeline
and its driver index) gets both from one computation at one `now`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from .. import aging, embed_space
from ..appearances import cast as appearances_cast
from . import effective, involvement, pressure

#: The driver kinds, which are also the tie-break order between drivers in one
#: pressure state.
DRIVER_KINDS = ("thread", "commitment", "event", "birthday", "holiday")

#: What a suggestion may claim to do to a driver (spec §20).
DRIVER_ACTIONS = ("advance", "close_candidate", "address", "fulfill_candidate",
                  "break_candidate", "expire_candidate", "anchor")

#: The actions a driver of each kind accepts; keyed by `DRIVER_KINDS`, in order.
ACTIONS_BY_KIND: dict[str, tuple[str, ...]] = {
    "thread": ("advance", "close_candidate"),
    "commitment": ("address", "fulfill_candidate", "break_candidate", "expire_candidate"),
    "event": ("anchor",),
    "birthday": ("anchor",),
    "holiday": ("anchor",),
}

_RECORD_KINDS = ("thread", "commitment")
_TEMPORAL_KINDS = frozenset({"event", "holiday", "birthday"})

#: The link relations that tie a record to an event's day.
_TIME_RELATIONS = frozenset({"before", "on", "after", "by"})

_NO_PRESSURE: dict[str, Any] = {"now": "", "friendly": "", "fixed": None, "items": []}

_ANCHOR_FIELDS = ("ref", "kind", "label", "native", "friendly", "fixed", "in_days",
                  "precision")

T = TypeVar("T")


def _soft(fn: Callable[..., T], fallback: T, *args: Any) -> T:
    """`fn(*args)`, or `fallback` when it raises anything at all."""
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 -- an input may run user calendar plugin code or read a garbled ledger; one input failing must cost only what it feeds
        return fallback


def matching() -> str:
    """``"semantic"`` iff an embeddings connection and model resolve, else
    ``"basic"`` (spec §3.3). The one definition of the field, so the continuity
    read and the drivers read cannot disagree. Reads config only."""
    return "semantic" if _soft(embed_space.resolve, None) is not None else "basic"


def _reading(item: dict) -> dict:
    return {"state": item["state"], "in_days": item["in_days"], "friendly": item["friendly"]}


def _record_pressure(ref: str, items: list[dict], stale: bool) -> dict:
    """The most urgent of the record's own pressure items and aging's `stale`;
    a bare `stale`/`ok` carries no day. Items arrive on the fixed-day axis, so
    among equally urgent ones the earliest answers."""
    own = [i for i in items if i["subject"] == ref]
    best = min(own, key=lambda i: pressure.PRESSURE_STATES.index(i["state"]), default=None)
    rank = pressure.PRESSURE_STATES.index
    if best is not None and not (stale and rank(best["state"]) > rank("stale")):
        return _reading(best)
    return {"state": "stale" if stale else "ok", "in_days": None, "friendly": ""}


#: The player tokens when the roster cannot be read: anyone may be the player.
Players = frozenset[str] | None


def _dropped(cid: str, offscreen: bool) -> Players:
    """The roster's player tokens (the `pcs:*` ones are dropped by prefix), or
    None when `offscreen` is set and the roster cannot be read -- a garbled
    appearances.json, or one hand-edited entry with no `role`. The filter then
    fails closed: the chronicle's cast still names a player-seated character,
    and nothing left can say which one she is."""
    if not offscreen:
        return frozenset()
    roster: list[dict] | None = _soft(appearances_cast.roster, None, cid)
    if roster is None:
        return None
    return frozenset(f"{a['kind']}:{a['id']}" for a in roster if a.get("role") == "player")


def _keep(actors: list[str], offscreen: bool, players: Players) -> list[str]:
    if not offscreen:
        return actors
    if players is None:
        return []
    return [a for a in actors if not a.startswith("pcs:") and a not in players]


def _is_stale(actx: dict | None, row: dict) -> bool:
    if actx is None:
        return False
    block: dict = _soft(aging.age, {}, actx, row)
    return block.get("state") == aging.STALE


def _record_drivers(cid: str, now: dict, offscreen: bool,
                    players: Players) -> list[dict]:
    """A driver per live canonical thread and commitment."""
    rows: list[tuple[str, dict]] = []
    for kind, read in zip(_RECORD_KINDS, (effective.threads_or_physical,
                                          effective.commitments_or_physical), strict=True):
        live: list[dict] = _soft(read, [], cid)
        rows.extend((kind, row) for row in live)
    refs = [f"{kind}:{row['id']}" for kind, row in rows]
    involved: dict[str, dict] = _soft(involvement.of, {}, cid, refs)
    actx = _soft(aging.prepare, None, cid, now["now"])
    out = []
    for (kind, row), ref in zip(rows, refs, strict=True):
        actors = involved.get(ref, {}).get("actors", [])
        out.append({
            "ref": ref, "kind": kind, "label": row["title"], "summary": row["latest_beat"],
            "actors": _keep(actors, offscreen, players), "status": row["status"],
            "pressure": _record_pressure(ref, now["items"], _is_stale(actx, row)),
            "time_anchors": [], "links": []})
    return out


def _temporal_drivers(items: list[dict], offscreen: bool,
                      players: Players) -> list[dict]:
    """A driver per event, holiday and birthday pressure item."""
    out = []
    for item in items:
        if item["kind"] not in _TEMPORAL_KINDS:
            continue
        actors = [item["actor"]] if item["kind"] == "birthday" and item["actor"] else []
        out.append({
            "ref": item["ref"], "kind": item["kind"], "label": item["label"], "summary": "",
            "actors": _keep(actors, offscreen, players), "status": "",
            "pressure": _reading(item), "time_anchors": [], "links": []})
    return out


def _attach_links(found: list[dict], links: list[dict]) -> None:
    """Each effective link on both of its drivers (`out` on `a`, `in` on `b`,
    `both` for an undirected relation), and each temporal link's event among
    its record's `time_anchors`."""
    by_ref = {d["ref"]: d for d in found}
    anchors: dict[str, set[str]] = {}
    for link in links:
        relation = link["relation"]
        directed = effective.RELATIONS.get(relation, ((), (), True))[2]
        for end, other, direction in ((link["a"], link["b"], "out"),
                                      (link["b"], link["a"], "in")):
            driver = by_ref.get(end)
            if driver is not None:
                driver["links"].append({"id": link["id"], "relation": relation, "other": other,
                                        "direction": direction if directed else "both"})
        if relation in _TIME_RELATIONS:
            anchors.setdefault(link["a"], set()).add(link["b"])
    for ref, events in anchors.items():
        if ref in by_ref and by_ref[ref]["kind"] in _RECORD_KINDS:
            by_ref[ref]["time_anchors"] = sorted(events)


def _anchors(items: list[dict]) -> list[dict]:
    """The temporal items that can constrain a date, nearest first."""
    out = [{k: item[k] for k in _ANCHOR_FIELDS} for item in items
           if item["kind"] in {"holiday", "birthday"}
           or (item["kind"] == "event" and item["fixed"] is not None
               and item["state"] != "passed")]
    out.sort(key=lambda a: (a["in_days"] is None, a["in_days"] or 0,
                            DRIVER_KINDS.index(a["kind"]), a["ref"]))
    return out


def _order(driver: dict) -> tuple:
    in_days = driver["pressure"]["in_days"]
    return (pressure.PRESSURE_STATES.index(driver["pressure"]["state"]),
            DRIVER_KINDS.index(driver["kind"]), in_days is None, in_days or 0, driver["ref"])


def snapshot(cid: str, offscreen: bool = False, *,
             pressure_result: dict | None = None) -> dict:
    """`{now, friendly, fixed, matching, drivers, anchors}` for this campaign's
    present. `now`/`friendly`/`fixed` are `pressure.build`'s -- or
    `pressure_result`'s when the caller already built it, in which case
    pressure is not computed again. Drivers are in pressure order
    (`PRESSURE_STATES`), then `DRIVER_KINDS`, then `in_days` (nulls last), then
    ref. Never raises for an existing campaign."""
    now = (_soft(pressure.build, _NO_PRESSURE, cid) if pressure_result is None
           else pressure_result)
    players = _dropped(cid, offscreen)
    found = [*_record_drivers(cid, now, offscreen, players),
             *_temporal_drivers(now["items"], offscreen, players)]
    _attach_links(found, _soft(effective.links, [], cid))
    found.sort(key=_order)
    return {"now": now["now"], "friendly": now["friendly"], "fixed": now["fixed"],
            "matching": matching(), "drivers": found, "anchors": _anchors(now["items"])}
