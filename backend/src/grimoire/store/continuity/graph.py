"""The Story Graph (spec §19): one deterministic, read-only projection of the
continuity substrate as nodes and edges.

It composes readers that already exist -- the scene list and its frontmatter
histories, the chronicle, the effective records and links, pressure and the
chooser's drivers, events, ideas, relationships and the review findings -- and
adds no second copy of any record. It writes nothing and calls no mutator, so
it is in none of `locks.DOMAIN_MODULES`, `OUTSIDE_DOMAIN` or `UNREVIEWED`.

**Three phases** (`build`), because two rules pull in opposite directions:
every campaign file should be read inside one lock hold, so the payload never
pairs one writer's two files a moment apart, and calendar plugin code -- user
code that can do anything, including wait -- must never run under that hold
(spec §11.1; D's reconcile and B's drivers route keep the same rule).

- (A) `_temporal`, outside the hold: the primary provider and `pressure.build`
  (later the driver snapshot and the event list), each of which can run plugin
  code.
- (B) `_files`, inside one `best_effort_campaign_lock` hold: every campaign file
  the graph projects. Best effort, never `campaign_lock`, because a read that
  can raise `StoreBusy` is a new way for opening a page to fail (§19.7,
  `docs/store-guarantees.md`). Under contention the read proceeds unlocked, and
  (A) and (B) can be a moment apart: that is the stated trade of every
  best-effort reader, and refusing would turn a read into an error.
- (C) `_assemble`, outside again: the per-row `calendars.fixed_of` /
  `calendars.friendly` on scene dates (plugin code, so never inside `_files`
  beside the list they derive from), then one private builder per family.

**Play order is the sorted scene ids**, `timeline.build`'s rule: the scene id
grammar puts the number first so lexicographic filename order equals play
order (`store/scene_ids.py`). It is re-derived here rather than called, because
`timeline.build` takes the full `campaign_lock` (and raises `StoreBusy`) and
would be a second acquisition. `list_scenes` is used only as the set of scenes,
never for its order: it sorts by `updated`, which is recency, and a date sort
would put a flashback before the scene that remembers it.

**"Once per request" (§19.7) is read as a constant number of whole-file reads,
independent of the node count**, not literally one read of each file: that
would mean threading one `Ledgers` through `pressure`, `drivers`,
`involvement` and `effective.records`, a cross-slice refactor handed to the
performance audit. What is guaranteed is no N+1 -- no per-node card read, no
image scan, no per-record ledger read. The per-scene location history is one
frontmatter-head parse per scene, which never opens a transcript.

**Every edge names two nodes.** A builder keeps an edge only when both ends are
nodes in the payload; nothing synthesizes a node by parsing a ref back apart,
which would be a second definition of a ref grammar someone else owns.

**Fail soft (§3.9, §26).** Every source and every family builder runs through
`_attempt`, the module's one broad `except`: a source that raises costs its own
nodes and edges and adds its `PARTS` name to `omitted`, so a reader can tell an
empty campaign from a broken file. Per-row date softening passes `part=None`:
a free-text date such as "midsummer" is ordinary data, not a broken calendar.
Ids and order are deterministic, so two reads of an unchanged campaign are
equal.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any, TypeVar

from .. import calendars, chronicle, fieldtext, locks
from ..campaigns import paths as campaigns_paths
from ..scenes import read as scenes_read
from . import pressure

#: §20's node kinds: `canon.KIND_OF_PREFIX`'s kinds minus the two §19.2 leaves
#: optional (groups, standing facts). The order is the payload's node order.
NODE_KINDS = ("scene", "character", "pc", "location", "thread", "commitment", "event",
              "idea", "birthday", "holiday")

#: §20's edge kinds; the order is the payload's edge order.
EDGE_KINDS = ("appeared_in", "occurred_at", "opened_in", "advanced_in", "touched_in",
              "closed_in", "resolved_in", "involves", "serves", "anchored_to", "feeling",
              "bond", "birthday_of", "link", "merged_into", "possible_duplicate",
              "possible_relation")

#: Where an edge comes from: derived from the files, a reviewed link, a pending
#: review finding, or an alias's merge.
EDGE_SOURCES = ("structural", "reviewed", "candidate", "alias")

#: An event node's status, first match wins.
EVENT_STATUSES = ("scheduled", "fired", "passed", "undated")

#: The sources `omitted` may name, in the order it lists them.
PARTS = ("calendar", "scenes", "chronicle", "plot", "commitments", "events", "continuity",
         "candidates", "relationships", "scene_ideas", "names")

T = TypeVar("T")


def edge_id(kind: str, a: str, b: str) -> str:
    """A structural or alias edge's id: `canon.link_id`'s recipe, scoped by kind."""
    return "e" + hashlib.sha256(f"{kind}\0{a}\0{b}".encode()).hexdigest()[:20]


def _attempt(omitted: set[str], part: str | None, fn: Callable[..., T], fallback: T,
             *args: Any) -> T:
    """`fn(*args)`, or `fallback` -- recording `part` in `omitted` -- when it
    raises anything at all. `part=None` softens without recording."""
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 -- a source may be a hand-edited file or user calendar plugin code, which can raise anything; one source failing must cost only its own nodes and edges
        if part is not None:
            omitted.add(part)
        return fallback


def _int_or_none(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# ---- phase A: temporal, outside the hold ------------------------------------


def _primary(cid: str):
    provider = calendars.primary_provider(campaigns_paths.campaign_root(cid))
    if provider is None:
        # `primary_provider` answers None for an unknown id or a plugin that
        # fails to import: unavailable, which is what "calendar" reports.
        raise LookupError("the primary calendar could not be loaded")
    return provider


def _temporal(cid: str, omitted: set[str]) -> dict:
    return {
        "provider": _attempt(omitted, "calendar", _primary, None, cid),
        "pressure": _attempt(omitted, "calendar", pressure.build, None, cid),
    }


# ---- phase B: campaign files, inside one hold -------------------------------


def _files(cid: str, omitted: set[str]) -> dict:
    with locks.best_effort_campaign_lock(cid):
        scenes: list[dict] = _attempt(omitted, "scenes", scenes_read.list_scenes, [], cid)
        chron: dict = _attempt(omitted, "chronicle", chronicle.read_chronicle, {}, cid)
        histories: dict[str, list[str]] = {}
        for row in scenes:
            histories[row["id"]] = _attempt(omitted, "scenes",
                                            scenes_read.get_location_history, [], cid, row["id"])
    # Valid JSON of the wrong shape arrives without raising.
    return {"scenes": scenes, "chronicle": chron if isinstance(chron, dict) else {},
            "histories": histories}


# ---- phase C: assembly, outside ---------------------------------------------


def _now(temporal: dict) -> dict:
    p = temporal["pressure"]
    if not isinstance(p, dict):
        return {"native": "", "friendly": "", "fixed": None}
    return {"native": fieldtext.text(p.get("now")), "friendly": fieldtext.text(p.get("friendly")),
            "fixed": _int_or_none(p.get("fixed"))}


def _dated(temporal: dict, native: str, omitted: set[str]) -> dict:
    """`{native, friendly, fixed, in_days}` for one date, softened per row."""
    provider = temporal["provider"]
    friendly, fixed = "", None
    if native and provider is not None:
        friendly = fieldtext.text(_attempt(omitted, None, calendars.friendly, "",
                                           provider, native))
        fixed = _int_or_none(_attempt(omitted, None, calendars.fixed_of, None,
                                      provider, native))
    now_fixed = _now(temporal)["fixed"]
    in_days = fixed - now_fixed if fixed is not None and now_fixed is not None else None
    return {"native": native, "friendly": friendly, "fixed": fixed, "in_days": in_days}


def _scene_nodes(temporal: dict, files: dict, omitted: set[str]) -> tuple[list, list]:
    chron = files["chronicle"]
    rows = sorted(files["scenes"], key=lambda m: m["id"])
    nodes = []
    for order, meta in enumerate(rows):
        sid = meta["id"]
        rec = chron.get(sid)
        rec = rec if isinstance(rec, dict) else {}   # a per-scene entry can be wrong too
        native = fieldtext.text(meta.get("date")) or fieldtext.text(rec.get("date"))
        nodes.append({
            "id": f"scene:{sid}", "kind": "scene",
            "label": fieldtext.text(meta.get("title")) or sid,
            "order": order, "done": bool(meta.get("done")), "pcless": bool(meta.get("pcless")),
            "place": fieldtext.text(rec.get("location")),
            **_dated(temporal, native, omitted),
        })
    return nodes, []


def _node_key(node: dict) -> tuple:
    kind = node["kind"]
    return (NODE_KINDS.index(kind), node.get("order", 0) if kind == "scene" else 0, node["id"])


def _edge_key(edge: dict) -> tuple:
    return (EDGE_KINDS.index(edge["kind"]), edge["from"], edge["to"], edge["id"])


def _assemble(cid: str, temporal: dict, files: dict, names: dict[str, str],
              omitted: set[str]) -> dict:
    families: tuple[tuple[str, Callable[..., tuple[list, list]], tuple], ...] = (
        ("scenes", _scene_nodes, (temporal, files, omitted)),
    )
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}
    for part, builder, args in families:
        built: tuple[list, list] = _attempt(omitted, part, builder, ([], []), *args)
        built_nodes, built_edges = built
        for node in built_nodes:
            nodes.setdefault(node["id"], node)
        for edge in built_edges:
            edges.setdefault(edge["id"], edge)
    return {
        "now": _now(temporal),
        "nodes": sorted(nodes.values(), key=_node_key),
        "edges": sorted(edges.values(), key=_edge_key),
        "omitted": [p for p in PARTS if p in omitted],
    }


def build(cid: str) -> dict:
    """`{now, nodes, edges, omitted}` for one campaign. Never raises for an
    existing campaign: a source that fails costs its own part, named in
    `omitted`."""
    omitted: set[str] = set()
    temporal = _temporal(cid, omitted)
    files = _files(cid, omitted)
    return _assemble(cid, temporal, files, {}, omitted)
