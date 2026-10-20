"""suggest: view responsibilities."""

from __future__ import annotations

from collections.abc import Callable

from ..continuity import drivers as continuity_drivers
from ..continuity import pressure
from . import _contracts

#: How many drivers the prompt's index lists (spec §15), and how many dated
#: items its timeline lists (Decision 11). Pinned refs render past it; the rest
#: is "and N more". A bound on prompt length, not a measurement: the index is
#: one short line per driver, and forty is far more than one batch of three or
#: four suggestions can serve -- tune it against real prompts later.
DRIVER_PROMPT_CAP = 40


def _when(in_days: int | None, precision: str | None = None, dated: bool = False) -> str:
    """Decision 10's phrase for an item's distance from now. `dated` says the
    item has a fixed day: with no present it has no distance, but it is not
    undated (§19.2's rule, kept by the prompt and the chooser alike)."""
    if precision == "month":
        return "day unknown"
    if in_days is None:
        return "no current date" if dated else "undated"
    if in_days == 0:
        return "today"
    if in_days > 0:
        return "in 1 day" if in_days == 1 else f"in {in_days} days"
    return "1 day ago" if in_days == -1 else f"{-in_days} days ago"


def _select(rows: list[dict], rank: Callable[[dict], tuple],
            always: Callable[[dict], bool]) -> list[int]:
    """The indices of `rows` to render, ascending: every row `always` keeps,
    plus the best-ranked rest up to `DRIVER_PROMPT_CAP` in all."""
    kept = [i for i, r in enumerate(rows) if always(r)]
    rest = sorted((i for i, r in enumerate(rows) if not always(r)), key=lambda i: rank(rows[i]))
    return sorted([*kept, *rest[:max(0, DRIVER_PROMPT_CAP - len(kept))]])


def _index_rank(row: dict) -> tuple:
    """Pressure in `SORT_ORDER` (Decision 9: not `PRESSURE_STATES`, which ranks
    `passed` above `today`), then the coldest first, then Slice B's ties."""
    dormancy, in_days = row.get("dormancy"), row["pressure"]["in_days"]
    return (pressure.SORT_ORDER.index(row["pressure"]["state"]), dormancy is None,
            -(dormancy or 0), continuity_drivers.DRIVER_KINDS.index(row["kind"]),
            in_days is None, in_days or 0, row["ref"])


def _timeline_rank(item: dict) -> tuple:
    in_days = item["in_days"]
    return (pressure.SORT_ORDER.index(item["state"]), in_days is None, in_days or 0,
            pressure.KINDS.index(item["kind"]), item["ref"])


def _index_rows(snapshot: dict, pinned: set[str]) -> tuple[list[dict], int]:
    ordered = sorted(snapshot.get("driver_index", []), key=_index_rank)
    chosen = _select(ordered, _index_rank, lambda r: r["ref"] in pinned)
    day = {i["ref"]: i for i in snapshot.get("timeline", []) if i["kind"] in _contracts.TEMPORAL_KINDS}
    rows = []
    for i in chosen:
        d = ordered[i]
        in_days = d["pressure"]["in_days"]
        item = day.get(d["ref"], {})
        when = (_when(in_days, item.get("precision"), item.get("fixed") is not None)
                if d["kind"] in _contracts.TEMPORAL_KINDS
                else _when(in_days) if in_days is not None else "")
        rows.append({"ref": d["ref"], "label": d["label"], "kind": d["kind"],
                     "state": d["pressure"]["state"], "when": when})
    return rows, len(ordered) - len(rows)


def _timeline_rows(snapshot: dict, pinned: set[str]) -> tuple[list[dict], int]:
    """Decision 11: pinned refs (on `ref` or `subject`) and the Upcoming pick
    always render, the rest is ranked and capped, and rows keep the
    snapshot's own (calendar) order."""
    items = snapshot.get("timeline", [])
    sooner = snapshot.get("sooner_ref", "")

    def always(item: dict) -> bool:
        return (item["ref"] in pinned or item.get("subject") in pinned
                or (bool(sooner) and item["ref"] == sooner and item["kind"] in _contracts._SOONER_KINDS))

    chosen = _select(items, _timeline_rank, always)
    rows = [{**items[i], "when": _when(items[i]["in_days"], items[i].get("precision"),
                                       items[i].get("fixed") is not None)}
            for i in chosen]
    return rows, len(items) - len(rows)


def _rendered_links(snapshot: dict, refs: set[str]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for link in snapshot.get("links", []):
        if link["id"] in seen or link["a"] not in refs or link["b"] not in refs:
            continue
        seen.add(link["id"])
        out.append({"a": link["a"], "relation": link["relation"], "b": link["b"]})
    return out


def _anchor_view(snapshot: dict, controls: _contracts.Controls, labels: dict[str, str]) -> dict | None:
    if not controls.anchor:
        return None
    found: dict = next((a for a in snapshot.get("anchors", []) if a["ref"] == controls.anchor), {})
    return {"ref": controls.anchor,
            "label": found.get("label") or labels.get(controls.anchor, controls.anchor),
            "friendly": found.get("friendly") or "", "relation": controls.relation}


def driver_view(snapshot: dict, controls: _contracts.Controls) -> dict:
    """What the prompt renders of the snapshot's drivers under `controls`: the
    capped, pinned index (Decision 9), the capped timeline (Decision 11), the
    links among rendered drivers, the action vocabulary, and the controls with
    their labels. A snapshot without driver keys yields an empty view."""
    pinned = set(controls.pinned)
    index, more = _index_rows(snapshot, pinned)
    timeline, timeline_more = _timeline_rows(snapshot, pinned)
    labels = {d["ref"]: d["label"] for d in snapshot.get("driver_index", [])}

    def named(refs: tuple[str, ...]) -> list[dict]:
        return [{"ref": r, "label": labels.get(r, r)} for r in refs]

    return {
        "index": index, "more": more, "timeline": timeline, "timeline_more": timeline_more,
        "links": _rendered_links(snapshot, {r["ref"] for r in index}),
        "actions": [{"kind": k, "actions": list(continuity_drivers.ACTIONS_BY_KIND[k])}
                    for k in continuity_drivers.DRIVER_KINDS],
        "high_pressure": [s for s in pressure.SORT_ORDER if s in pressure.HIGH_PRESSURE],
        "relations": list(_contracts.ANCHOR_RELATIONS),
        "focus": named(controls.focus), "avoid": named(controls.avoid),
        "must": named(controls.must),
        "time_mode": controls.time_mode,
        # the date addendum (what defines the "date" key) renders only under a
        # `now`, so near/move may not ask for a date without one (§26)
        "dated": bool(snapshot.get("now")),
        "near_days": snapshot.get("near_days", _contracts.NEAR_MIN_DAYS),
        "anchor": _anchor_view(snapshot, controls, labels),
        "active": controls.active,
    }
