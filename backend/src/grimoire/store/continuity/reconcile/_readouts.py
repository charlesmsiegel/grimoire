"""Fail-soft pressure readouts and stable scene play ordering shared by reviewers and prompt construction."""

from __future__ import annotations

from ... import aging, fieldtext, scene_ids
from ...scenes import read as scenes_read
from .. import effective, pressure, tolerance

_DEADLINE_SOURCES = frozenset({"deadline", "linked_deadline"})
_NO_DEADLINE = {"state": "ok", "in_days": None, "friendly": ""}

def _reading(item: dict | None) -> dict:
    if item is None:
        return dict(_NO_DEADLINE)
    return {"state": item["state"], "in_days": item["in_days"], "friendly": item["friendly"]}


def _pressure_by_ref(cid: str) -> dict[str, dict]:
    built = pressure.build(cid, sources=_DEADLINE_SOURCES)
    rank = pressure.PRESSURE_STATES.index
    best: dict[str, dict] = {}
    for item in built["items"]:
        subject = item["subject"]
        if subject and (subject not in best or rank(item["state"]) < rank(best[subject]["state"])):
            best[subject] = item
    owed: list[dict] = tolerance.call(effective.commitments, [], cid)
    out = {f"commitment:{row['id']}": _reading(best.get(f"commitment:{row['id']}"))
           for row in owed}
    actx = tolerance.call(aging.prepare, None, cid, built["now"])
    threads: list[dict] = tolerance.call(effective.threads, [], cid)
    for row in threads:
        block: dict = tolerance.call(aging.age, {}, actx, row) if actx is not None else {}
        state = "stale" if block.get("state") == aging.STALE else "ok"
        out[f"thread:{row['id']}"] = {"state": state, "in_days": None, "friendly": ""}
    return out


def pressure_by_ref(cid: str) -> dict[str, dict]:
    """``{ref: {state, in_days, friendly}}`` for every live canonical record: a
    commitment's most urgent deadline item (``ok`` with no day when it has
    none), a thread's aging -- ``stale`` or ``ok``, with no day. ``{}`` when
    the read fails; discovery and the candidates read both use it."""
    return tolerance.call(_pressure_by_ref, {}, cid)


def _play_key(row: dict) -> tuple:
    """A scene's place in play order: legacy ids (outside `scene_ids`' grammar,
    which replaced them) first by creation stamp, then numbered ids by number."""
    parsed = scene_ids.parse_sid(row["id"])
    if parsed is None:
        return (0, 0, fieldtext.text(row.get("created")), row["id"])
    return (1, parsed["number"], "", row["id"])


def play_order(rows: list[dict]) -> list[dict]:
    """`scenes_read.list_scenes` rows (newest-updated first) in play order --
    what the prompt's recent window and the candidates read's scene list are
    both ordered by."""
    return sorted(rows, key=_play_key)


def _live_scenes(cid: str) -> list[str]:
    """The scenes that exist, in play order -- the set persist 1's
    `pending.evidence_ok` checks a proposal against, so nothing outside it is
    shown as evidence. Empty when the scene list cannot be read."""
    none: list[dict] = []
    rows = tolerance.call(scenes_read.list_scenes, none, cid)
    return [row["id"] for row in play_order(rows)]
