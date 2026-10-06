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

- (A) `_temporal`, outside the hold: the primary provider, `pressure.build`,
  one `_probe` of the present through the provider, the chooser's
  `drivers.snapshot` over that same pressure result, and the event list
  (`events.list_events` dates each row through the provider), each of which
  can run plugin code.
- (B) `_files`, inside one `best_effort_campaign_lock` hold: every campaign file
  the graph projects. Best effort, never `campaign_lock`, because a read that
  can raise `StoreBusy` is a new way for opening a page to fail (§19.7,
  `docs/store-guarantees.md`). Under contention the read proceeds unlocked, and
  (A) and (B) can be a moment apart: that is the stated trade of every
  best-effort reader, and refusing would turn a read into an error.
- (C) `_names` and `_assemble`, outside again: the roster summaries that label
  actors and locations, then the per-row `calendars.fixed_of` /
  `calendars.friendly` on scene, event and idea dates (plugin code, so never
  inside `_files`
  beside the lists they derive from), then one private builder per family.

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
from collections.abc import Callable, Collection
from typing import Any, TypeVar

from .. import (
    calendars,
    chronicle,
    events,
    fieldtext,
    locks,
    overlay,
    relationships,
    scene_ideas,
)
from ..campaigns import paths as campaigns_paths
from ..scenes import read as scenes_read
from . import canon, drivers, effective, involvement, pressure

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

#: The two record kinds, each with the `PARTS` name of its own ledger.
_RECORD_PARTS = (("thread", "plot"), ("commitment", "commitments"))

#: Per record kind, the movement edge kinds (Decision 8): opened, moved, done.
_MOVEMENTS = {"thread": ("opened_in", "advanced_in", "closed_in"),
              "commitment": ("opened_in", "touched_in", "resolved_in")}

#: The pressure item kinds that become occurrence nodes (Decision 11): only
#: what pressure dated inside its horizon, never a ref parsed back apart.
_OCCURRENCE_KINDS = ("holiday", "birthday")

#: A commitment's dated fields when it carries no deadline item.
_UNDATED = {"native": "", "friendly": "", "fixed": None, "in_days": None}

#: `drivers.snapshot`'s shape when it cannot be read: nothing is a driver, so
#: nothing is offered as one.
_NO_SNAPSHOT: dict = {"now": "", "friendly": "", "fixed": None, "matching": "basic",
                      "drivers": [], "anchors": []}

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
    # The chooser's own read over the same pressure result the dated nodes
    # use, so "Focus" and "Anchor" never offer a ref the chooser would drop.
    snapshot: dict = _attempt(omitted, "calendar",
                              lambda: drivers.snapshot(cid, pressure_result=p), _NO_SNAPSHOT)
    rows, event_provider = _events(cid, provider, p, omitted)
    return {"provider": provider, "pressure": p,
            "snapshot": snapshot if isinstance(snapshot, dict) else _NO_SNAPSHOT,
            "events": rows, "event_provider": event_provider}


def _events(cid: str, provider, p, omitted: set[str]) -> tuple[list[dict], Any]:
    """Every stored event through `events.list_events`, whose `passed` reading
    is reused rather than restated, and the provider its rows are dated by.
    A call that raises falls back to the provider-less list -- every event
    undated, none dated by guesswork -- and reports "calendar"."""
    now_fixed = _int_or_none(p.get("fixed")) if isinstance(p, dict) else None
    rows: list[dict] | None = _attempt(omitted, "calendar", events.list_events, None, cid,
                                       provider, now_fixed)
    if rows is None:
        provider = None
        none: list[dict] = []
        rows = _attempt(omitted, "events", events.list_events, none, cid)
    return (rows if isinstance(rows, list) else []), provider


# ---- phase B: campaign files, inside one hold -------------------------------


def _scene_actors(cid: str, unread: set[str]) -> dict[str, set[str]]:
    return involvement.scene_actors(cid, unreadable=unread)


def _all_unreadable() -> effective.Ledgers:
    return effective.Ledgers(None, None, None, ["plot", "commitments", "events"])


def _records(cid: str, ledgers: effective.Ledgers, omitted: set[str]) -> dict:
    """The effective records per kind, the live canon, and `involvement.of`
    over every canonical ref -- closed and resolved ones included."""
    out: dict[str, dict] = {}
    for kind, part in _RECORD_PARTS:
        found: dict[str, dict] = _attempt(omitted, part, effective.records, {}, cid, kind)
        out[kind] = found if isinstance(found, dict) else {}
    live: dict[str, str] = _attempt(omitted, "continuity", effective.live_canon, {}, cid,
                                    ledgers)
    refs = [ref for kind, _ in _RECORD_PARTS for ref in out[kind]]
    # `of` is the actor join; a failure there costs `involves` edges, which
    # belong to the actor family's part.
    involved: dict[str, dict] = _attempt(omitted, "chronicle", involvement.of, {}, cid, refs)
    return {"records": out, "live": live if isinstance(live, dict) else {},
            "involved": involved if isinstance(involved, dict) else {}}


def _files(cid: str, omitted: set[str]) -> dict:
    with locks.best_effort_campaign_lock(cid):
        ledgers: effective.Ledgers = _attempt(omitted, None, effective.Ledgers.load,
                                              _all_unreadable(), cid)
        omitted.update(ledgers.unreadable)
        recs = _records(cid, ledgers, omitted)
        scenes: list[dict] = _attempt(omitted, "scenes", scenes_read.list_scenes, [], cid)
        chron: dict = _attempt(omitted, "chronicle", chronicle.read_chronicle, {}, cid)
        histories: dict[str, list[str]] = {}
        for row in scenes:
            histories[row["id"]] = _attempt(omitted, "scenes",
                                            scenes_read.get_location_history, [], cid, row["id"])
        # `scene_actors` steps over a source that will not read rather than
        # raising, so it names what it stepped over; the family's part is
        # "chronicle" either way (the appearance record has no part of its own).
        unread: set[str] = set()
        actors: dict = _attempt(omitted, "chronicle", _scene_actors, {}, cid, unread)
        if unread:
            omitted.add("chronicle")
        # `read` raises on bad JSON, and on a list top level from `setdefault`.
        rels: dict = _attempt(omitted, "relationships", relationships.read, {}, cid)
        # `records` raises on a file whose top level is not an object.
        ideas: list[dict] = _attempt(omitted, "scene_ideas", scene_ideas.records, [], cid)
    # Valid JSON of the wrong shape arrives without raising.
    return {"scenes": scenes, "chronicle": chron if isinstance(chron, dict) else {},
            "histories": histories, "actors": actors if isinstance(actors, dict) else {},
            "relationships": rels if isinstance(rels, dict) else {},
            "ideas": ideas if isinstance(ideas, list) else [],
            "ledgers": ledgers, **recs}


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


def _drivers_by_ref(temporal: dict,
                    kinds: tuple[str, ...] = effective.ALIASABLE) -> dict[str, dict]:
    """The chooser's drivers of `kinds` (by default threads and commitments),
    by ref."""
    found = temporal["snapshot"].get("drivers")
    return {d["ref"]: d for d in (found if isinstance(found, list) else ())
            if isinstance(d, dict) and d.get("kind") in kinds}


def _anchor_refs(temporal: dict) -> set[str]:
    """The refs the chooser accepts as a date anchor (`drivers.snapshot`'s
    `anchors`), so "Anchor" is never offered on one it would drop."""
    found = temporal["snapshot"].get("anchors")
    return {a["ref"] for a in (found if isinstance(found, list) else ())
            if isinstance(a, dict) and isinstance(a.get("ref"), str)}


def _items(temporal: dict) -> list[dict]:
    p = temporal["pressure"]
    items = p.get("items") if isinstance(p, dict) else None
    return [i for i in (items if isinstance(items, list) else ()) if isinstance(i, dict)]


def _item_dated(item: dict) -> dict:
    """A pressure item's four dated fields, as pressure computed them."""
    return {"native": fieldtext.text(item.get("native")),
            "friendly": fieldtext.text(item.get("friendly")),
            "fixed": _int_or_none(item.get("fixed")),
            "in_days": _int_or_none(item.get("in_days"))}


def _deadline(temporal: dict, ref: str) -> dict:
    """A commitment's dated fields from its own deadline item: the pressure
    item whose subject it is and whose relation is not `after` (at most one)."""
    for item in _items(temporal):
        if item.get("subject") == ref and item.get("relation") != "after":
            return _item_dated(item)
    return dict(_UNDATED)


def _record_node(kind: str, ref: str, rec: dict, temporal: dict,
                 by_driver: dict[str, dict]) -> dict:
    """A canonical thread or commitment: its pressure and `focusable` are the
    chooser's driver reading, so a non-driver is never offered as one."""
    status = rec.get("status")
    driver = by_driver.get(ref)
    reading = driver.get("pressure") if driver is not None else None
    node = {"id": ref, "kind": kind, "label": fieldtext.text(rec.get("title")),
            "status": status, "live": effective.is_live(kind, status), "merged_into": None,
            "aliases": rec.get("aliases") or [],
            "latest_beat": fieldtext.text(rec.get("latest_beat")),
            "pressure": dict(reading) if isinstance(reading, dict) else None,
            "focusable": driver is not None, "findings": []}
    if kind == "commitment":
        node.update(commitment_kind=fieldtext.text(rec.get("kind")),
                    due=fieldtext.text(rec.get("due")), **_deadline(temporal, ref))
    return node


def _merged_nodes(kind: str, ref: str, rec: dict) -> tuple[list, list]:
    """Each alias source as its own node, merged into `ref`: title and status
    only, since the canonical answers for the group."""
    nodes, edges = [], []
    for alias in rec.get("aliases") or ():
        src = alias["ref"]
        node = {"id": src, "kind": kind, "label": fieldtext.text(alias.get("title")),
                "status": alias.get("status"),
                "live": effective.is_live(kind, alias.get("status")), "merged_into": ref,
                "aliases": [], "latest_beat": "", "pressure": None, "focusable": False,
                "findings": []}
        if kind == "commitment":
            node.update(commitment_kind="", due="", **_UNDATED)
        nodes.append(node)
        edges.append(_edge("merged_into", src, ref, source="alias"))
    return nodes, edges


def _movements(kind: str, ref: str, rec: dict, listed: set[str]) -> list[dict]:
    """Decision 8: `opened_in` the first merged beat's scene; the moved kind
    to every other beat scene; the done kind to `last_scene` once not live.
    "First" is first in play order, not in the order the beats were saved, so
    an unmerged record agrees with a merged one and creating an alias never
    moves the opening. Only listed scenes are targets, and a play-order-first
    beat in a deleted scene opens nothing rather than moving the opening to
    the next beat."""
    opened, moved, done = _MOVEMENTS[kind]
    beats = [b for b in rec.get("beats") or () if isinstance(b, dict)]
    scenes = [fieldtext.text(b.get("scene")) for b in effective.play_ordered(beats)]
    edges = []
    first = scenes[0] if scenes else ""
    if first in listed:
        edges.append(_edge(opened, ref, f"scene:{first}"))
    edges += [_edge(moved, ref, f"scene:{sid}")
              for sid in dict.fromkeys(scenes) if sid != first and sid in listed]
    last = fieldtext.text(rec.get("last_scene"))
    if not effective.is_live(kind, rec.get("status")) and last in listed:
        edges.append(_edge(done, ref, f"scene:{last}"))
    return edges


def _involves(ref: str, files: dict, names: dict[str, str]) -> tuple[list, list]:
    row = files["involved"].get(ref)
    actors = row.get("actors") if isinstance(row, dict) else None
    tokens = [a for a in (actors if isinstance(actors, list) else ())
              if _actor_kind(a) is not None]
    return [_actor_node(a, names) for a in tokens], [_edge("involves", ref, a) for a in tokens]


def _record_parts(files: dict, temporal: dict, names: dict[str, str]) -> tuple[list, list]:
    """Threads and commitments: canonical and merged nodes, movements,
    merges and involvement."""
    listed = {row["id"] for row in files["scenes"]}
    by_driver = _drivers_by_ref(temporal)
    nodes, edges = [], []
    for kind, _ in _RECORD_PARTS:
        for ref, rec in files["records"][kind].items():
            nodes.append(_record_node(kind, ref, rec, temporal, by_driver))
            merged_nodes, merged_edges = _merged_nodes(kind, ref, rec)
            actor_nodes, actor_edges = _involves(ref, files, names)
            nodes += merged_nodes + actor_nodes
            edges += merged_edges + _movements(kind, ref, rec, listed) + actor_edges
    return nodes, edges


def _event_status(row: dict, fixed: int | None) -> str:
    """`EVENT_STATUSES`, first match: a fire stamp, `events`' own `passed`
    reading, a day nobody can name, else scheduled."""
    if row.get("fired"):
        return "fired"
    if row.get("passed"):
        return "passed"
    return "undated" if fixed is None else "scheduled"


def _event_nodes(temporal: dict, anchors: set[str], omitted: set[str]) -> list[dict]:
    """Every stored event -- fired, passed and undated ones too (Decision 10).
    Its pressure is its driver's reading, which only an unfired event (or one
    fired today) has; `findings` is filled by the review family."""
    readings = _drivers_by_ref(temporal, ("event",))
    dating = {**temporal, "provider": temporal["event_provider"]}
    nodes = []
    for row in temporal["events"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        ref = f"event:{row['id']}"
        dated = _dated(dating, fieldtext.text(row.get("date")), omitted)
        reading = readings.get(ref, {}).get("pressure")
        nodes.append({"id": ref, "kind": "event",
                      "label": fieldtext.text(row.get("name")) or row["id"], **dated,
                      "status": _event_status(row, dated["fixed"]),
                      "pressure": dict(reading) if isinstance(reading, dict) else None,
                      "anchorable": ref in anchors, "findings": []})
    return nodes


def _occurrence_node(item: dict, anchors: set[str]) -> dict:
    ref = item["ref"]
    node = {"id": ref, "kind": item["kind"], "label": fieldtext.text(item.get("label")),
            **_item_dated(item),
            "pressure": {"state": item.get("state"),
                         "in_days": _int_or_none(item.get("in_days")),
                         "friendly": fieldtext.text(item.get("friendly"))},
            "anchorable": ref in anchors}
    if item["kind"] == "birthday":
        actor = item.get("actor")
        node.update(actor=actor if isinstance(actor, str) else None,
                    precision=item.get("precision"), age=_int_or_none(item.get("age")))
    return node


def _temporal_parts(temporal: dict, names: dict[str, str],
                    omitted: set[str]) -> tuple[list, list]:
    """Events, and the holiday and birthday occurrences pressure dated inside
    its horizon (Decision 11), with `birthday_of` to each birthday's actor."""
    anchors = _anchor_refs(temporal)
    nodes, edges = _event_nodes(temporal, anchors, omitted), []
    for item in _items(temporal):
        if item.get("kind") not in _OCCURRENCE_KINDS or not isinstance(item.get("ref"), str):
            continue
        node = _occurrence_node(item, anchors)
        nodes.append(node)
        actor = node.get("actor")
        if isinstance(actor, str) and _actor_kind(actor) is not None:
            nodes.append(_actor_node(actor, names))
            edges.append(_edge("birthday_of", node["id"], actor))
    return nodes, edges


def _idea_edges(ref: str, idea: dict, live: dict[str, str],
                node_ids: Collection[str]) -> list[dict]:
    """Decision 12: `serves` to each stored driver's live canonical and
    `anchored_to` the stored anchor, each only when its target is a node; one
    edge per target, the first stored entry winning."""
    served: dict[str, str] = {}
    for d in idea.get("drivers") or ():
        target = live.get(d["ref"], d["ref"])
        if target in node_ids:
            served.setdefault(target, d["action"])
    edges = [_edge("serves", ref, target, relation=action) for target, action in served.items()]
    anchor = idea.get("time_anchor")
    if isinstance(anchor, dict) and anchor.get("ref") in node_ids:
        edges.append(_edge("anchored_to", ref, anchor["ref"], relation=anchor.get("relation")))
    return edges


def _idea_parts(files: dict, temporal: dict, live: dict[str, str],
                node_ids: Collection[str], omitted: set[str]) -> tuple[list, list]:
    """Active saved ideas only (Decision 6): a used idea is already a scene on
    the spine, and a dismissed one is the reader's own "no". An idea keeps its
    stored `time_anchor` even when the anchor has no node to draw to."""
    nodes, edges = [], []
    for idea in files["ideas"]:
        if not isinstance(idea, dict) or idea.get("status") != scene_ideas.ACTIVE:
            continue
        ref = f"idea:{idea['id']}"
        nodes.append({"id": ref, "kind": "idea", "label": idea["title"],
                      "premise": idea["premise"], "source": idea["source"],
                      "pcless": idea["pcless"], "time_anchor": idea.get("time_anchor"),
                      **_dated(temporal, fieldtext.text(idea.get("date")), omitted)})
        edges += _idea_edges(ref, idea, live, node_ids)
    return nodes, edges


def _node_key(node: dict) -> tuple:
    kind = node["kind"]
    return (NODE_KINDS.index(kind), node.get("order", 0) if kind == "scene" else 0, node["id"])


def _edge_key(edge: dict) -> tuple:
    return (EDGE_KINDS.index(edge["kind"]), edge["from"], edge["to"], edge["id"])


def _assemble(cid: str, temporal: dict, files: dict, names: dict[str, str],
              omitted: set[str]) -> dict:
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}
    # Families run in order, and `nodes.keys()` is a live view: a family that
    # keeps an edge only when its target is a node (ideas) runs after every
    # family whose nodes it may name.
    families: tuple[tuple[str, Callable[..., tuple[list, list]], tuple], ...] = (
        ("scenes", _scene_nodes, (temporal, files, omitted)),
        ("chronicle", _actor_parts, (files, names)),
        ("plot", _record_parts, (files, temporal, names)),
        ("events", _temporal_parts, (temporal, names, omitted)),
        ("scene_ideas", _idea_parts, (files, temporal, files["live"], nodes.keys(), omitted)),
    )
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
