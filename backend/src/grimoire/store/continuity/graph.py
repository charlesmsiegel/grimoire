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

- (A) `_temporal`, outside the hold: the primary provider, `pressure.build`
  and one `_probe` of the present through the provider (later the driver
  snapshot and the event list), each of which can run plugin code.
- (B) `_files`, inside one `best_effort_campaign_lock` hold: every campaign file
  the graph projects. Best effort, never `campaign_lock`, because a read that
  can raise `StoreBusy` is a new way for opening a page to fail (§19.7,
  `docs/store-guarantees.md`). Under contention the read proceeds unlocked, and
  (A) and (B) can be a moment apart: that is the stated trade of every
  best-effort reader, and refusing would turn a read into an error.
- (C) `_names` and `_assemble`, outside again: the roster summaries that label
  actors and locations, then the per-row `calendars.fixed_of` /
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
A plugin that loads but cannot read a date is caught once, by `_probe`.
Ids and order are deterministic, so two reads of an unchanged campaign are
equal.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any, TypeVar

from .. import calendars, chronicle, fieldtext, locks, overlay, relationships
from ..campaigns import paths as campaigns_paths
from ..scenes import read as scenes_read
from . import canon, involvement, pressure

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

#: The two node kinds an actor token can name (`_actor_kind`).
_ACTOR_KINDS = ("character", "pc")

#: A feeling's three meters, each drawn as five pips: the ledger's and the
#: case file's axes (`routes.campaigns.FEELING_AXES`, `casefile.FEELING_AXES`),
#: restated because neither is a module `continuity` may import.
_FEELING_AXES = ("trust", "affection", "tension")


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


def _edge(kind: str, a: str, b: str, *, source: str = "structural", **extra: Any) -> dict:
    """One §20 edge with a derived id; `extra` carries a kind's typed fields."""
    return {"id": edge_id(kind, a, b), "kind": kind, "from": a, "to": b, "source": source,
            "relation": None, "candidate_id": None, **extra}


def _label(ref: str, names: dict[str, str]) -> str:
    """The roster's name for `ref`, else the id after its first `:`."""
    return names.get(ref) or ref.partition(":")[2]


def _actor_kind(token) -> str | None:
    """`"character"`, `"pc"` or None for an actor token; never raises.

    Not `canon.split_ref`: `involvement.scene_actors` passes every non-empty
    chronicle `cast` string through `canon.actor_ref`, which leaves `"Mara"` as
    `"Mara"` and turns `"characters/"` into `"characters:"`, and `split_ref`
    raises on both. A hand edit costs that token's edges, never the family."""
    if not isinstance(token, str):
        return None
    prefix, sep, rest = token.partition(":")
    kind = canon.KIND_OF_PREFIX.get(prefix)
    return kind if sep and rest and kind in _ACTOR_KINDS else None


def _actor_node(token: str, names: dict[str, str]) -> dict:
    return {"id": token, "kind": _actor_kind(token), "label": _label(token, names)}


# ---- phase A: temporal, outside the hold ------------------------------------


def _primary(cid: str):
    provider = calendars.primary_provider(campaigns_paths.campaign_root(cid))
    if provider is None:
        # `primary_provider` answers None for an unknown id or a plugin that
        # fails to import: unavailable, which is what "calendar" reports.
        raise LookupError("the primary calendar could not be loaded")
    return provider


def _probe(provider, native: str) -> None:
    """Run the present through the provider once, so a plugin that loads but
    cannot read a date reports as a broken calendar rather than as a campaign
    with no dates. `pressure.build` softens that failure into `fixed: None`
    and the per-row reads soften theirs on purpose (`part=None`), so without
    this nothing would name it. `CalendarError` is the contract's answer to
    text the calendar cannot read -- an unclocked campaign's present is seeded
    from the chronicle's free-text date -- and is data; anything else escapes
    to `_attempt`, which records "calendar"."""
    try:
        calendars.fixed_of(provider, native)
        calendars.friendly(provider, native)
    except calendars.CalendarError:
        pass


def _temporal(cid: str, omitted: set[str]) -> dict:
    provider = _attempt(omitted, "calendar", _primary, None, cid)
    p = _attempt(omitted, "calendar", pressure.build, None, cid)
    native = fieldtext.text(p.get("now")) if isinstance(p, dict) else ""
    if provider is not None and native:
        _attempt(omitted, "calendar", _probe, None, provider, native)
    return {"provider": provider, "pressure": p}


# ---- phase B: campaign files, inside one hold -------------------------------


def _files(cid: str, omitted: set[str]) -> dict:
    with locks.best_effort_campaign_lock(cid):
        scenes: list[dict] = _attempt(omitted, "scenes", scenes_read.list_scenes, [], cid)
        chron: dict = _attempt(omitted, "chronicle", chronicle.read_chronicle, {}, cid)
        histories: dict[str, list[str]] = {}
        for row in scenes:
            histories[row["id"]] = _attempt(omitted, "scenes",
                                            scenes_read.get_location_history, [], cid, row["id"])
        actors: dict = _attempt(omitted, "chronicle", involvement.scene_actors, {}, cid)
        # `read` raises on bad JSON, and on a list top level from `setdefault`.
        rels: dict = _attempt(omitted, "relationships", relationships.read, {}, cid)
    # Valid JSON of the wrong shape arrives without raising.
    return {"scenes": scenes, "chronicle": chron if isinstance(chron, dict) else {},
            "histories": histories, "actors": actors if isinstance(actors, dict) else {},
            "relationships": rels if isinstance(rels, dict) else {}}


# ---- phase C: names, outside ------------------------------------------------


def _locations(cid: str) -> list[dict]:
    return overlay.list_entities(cid, "locations")


#: Each roster summary, read in its own `_attempt`: `entities.list_entities`
#: raises on a record it cannot parse rather than skipping it, so one bad
#: location file must cost location labels, not every actor's name.
_ROSTERS: tuple[tuple[str, Callable[[str], list[dict]]], ...] = (
    (canon.PREFIX_OF_KIND["character"], overlay.character_roster),
    (canon.PREFIX_OF_KIND["pc"], overlay.pc_roster),
    (canon.PREFIX_OF_KIND["location"], _locations),
)


def _names(cid: str, omitted: set[str]) -> dict[str, str]:
    """`"<prefix>:<id>" -> name` from the roster summaries (§19.7): no card read,
    no image scan. A ref no roster names is labelled with its id (`_label`)."""
    names: dict[str, str] = {}
    for prefix, read in _ROSTERS:
        rows: list[dict] = _attempt(omitted, "names", read, [], cid)
        for row in rows if isinstance(rows, list) else ():
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            name = fieldtext.text(row.get("name"))
            if name:
                names[f"{prefix}:{row['id']}"] = name
    return names


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


def _appearances(files: dict) -> list[dict]:
    """`appeared_in`: actor -> listed scene, from the appearance roster and the
    chronicle cast, unioned by `involvement.scene_actors`."""
    listed = {row["id"] for row in files["scenes"]}
    edges: list[dict] = []
    for sid, actors in files["actors"].items():
        if sid not in listed or not isinstance(actors, (set, list, tuple)):
            continue
        edges.extend(_edge("appeared_in", token, f"scene:{sid}")
                     for token in actors if _actor_kind(token) is not None)
    return edges


def _places(files: dict, names: dict[str, str]) -> tuple[list, list]:
    """`occurred_at`: scene -> each distinct location in its history, and the
    location nodes those edges name."""
    prefix = canon.PREFIX_OF_KIND["location"]
    nodes, edges = [], []
    for sid, history in files["histories"].items():
        for eid in dict.fromkeys(h for h in history if isinstance(h, str) and h):
            ref = f"{prefix}:{eid}"
            nodes.append({"id": ref, "kind": "location", "label": _label(ref, names)})
            edges.append(_edge("occurred_at", f"scene:{sid}", ref))
    return nodes, edges


def _meter(value) -> int:
    """A stored meter clamped to the five pips drawn; a non-int reads 0."""
    return min(5, max(0, value)) if isinstance(value, int) and not isinstance(value, bool) else 0


def _meters(rec: dict) -> dict[str, Any]:
    return {axis: _meter(rec.get(axis)) for axis in _FEELING_AXES}


def _pairs(table, sep: str) -> list[tuple[str, str, dict]]:
    """`(a, b, record)` per relationship key that names two actors."""
    out = []
    for key, rec in (table.items() if isinstance(table, dict) else ()):
        if not isinstance(key, str) or not isinstance(rec, dict):
            continue
        a, found, b = key.partition(sep)
        if found and _actor_kind(a) is not None and _actor_kind(b) is not None:
            out.append((a, b, rec))
    return out


def _relationship_edges(rels: dict) -> list[dict]:
    """`feeling` (directed, metered) and `bond` (`lo -> hi`, named, dated),
    parsed as the ledger's relationships section parses them, names aside."""
    edges = [_edge("feeling", a, b, **_meters(rec), note=fieldtext.text(rec.get("note")))
             for a, b, rec in _pairs(rels.get("feelings"), "->")]
    edges += [_edge("bond", a, b, bond_type=fieldtext.text(rec.get("type")),
                    since_scene=fieldtext.text(rec.get("since_scene")))
              for a, b, rec in _pairs(rels.get("bonds"), "|")]
    return edges


def _actor_parts(files: dict, names: dict[str, str]) -> tuple[list, list]:
    """Appearances, places and relationships, and the actor and location nodes
    their edges name -- never the whole roster (§19.2, "do not dump")."""
    place_nodes, place_edges = _places(files, names)
    edges = _appearances(files) + place_edges + _relationship_edges(files["relationships"])
    actors = {end for e in edges for end in (e["from"], e["to"])
              if _actor_kind(end) is not None}
    return [_actor_node(a, names) for a in sorted(actors)] + place_nodes, edges


def _node_key(node: dict) -> tuple:
    kind = node["kind"]
    return (NODE_KINDS.index(kind), node.get("order", 0) if kind == "scene" else 0, node["id"])


def _edge_key(edge: dict) -> tuple:
    return (EDGE_KINDS.index(edge["kind"]), edge["from"], edge["to"], edge["id"])


def _assemble(cid: str, temporal: dict, files: dict, names: dict[str, str],
              omitted: set[str]) -> dict:
    families: tuple[tuple[str, Callable[..., tuple[list, list]], tuple], ...] = (
        ("scenes", _scene_nodes, (temporal, files, omitted)),
        ("chronicle", _actor_parts, (files, names)),
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
    names = _names(cid, omitted)
    return _assemble(cid, temporal, files, names, omitted)
