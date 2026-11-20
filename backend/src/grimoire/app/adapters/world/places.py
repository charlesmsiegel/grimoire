"""059's places and routes on the World page: the director's topology, a route between two exact places, and a
road blocked or opened in one campaign through ``TopologyLibrary.change_path``, reviewed before it is made.

Nothing here keeps a record. Topologies are the library's pinned ``topology`` definitions, each read with the
campaign's own path replacements (``TopologyLibrary.view``). A place is named by its exact source-qualified ref:
a name search answers every candidate and never picks one, and a route between places is asked only by refs, so a
same-named place in another source is never substituted. 059 has no weighted routes, hazards, travel or maps.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from grimoire.app.adapter import AdapterContext
from grimoire.core import DIRECTOR
from grimoire.core.identity import SourceRef
from grimoire.textapi import ApiRefused
from grimoire.topology import Path
from grimoire.topologystore import TopologyLibrary

from . import common

#: At most this many condition tokens may be held for a route (059's own cap is 64).
MAX_CONDITIONS = 64
#: Provenance the campaign's replacement path is recorded with: the director's own act on this page.
PROVENANCE = "director:world-page"


def _library(ctx: AdapterContext) -> TopologyLibrary:
    return TopologyLibrary(ctx.store, ctx.handle)


def _topologies(ctx: AdapterContext) -> list[dict]:
    """Every pinned topology revision the library loads, in reference order."""
    view = ctx.store.load()
    return [{"topology": str(item.ref), "name": item.name or None}
            for item in view.index.definitions() if item.ref.kind == "topology"]


def _name(ctx: AdapterContext, ref: SourceRef) -> str | None:
    found = ctx.store.check_definition(ref)
    if isinstance(found, str):
        return None
    return found.envelope.name or None


def _etag(view) -> str:  # type: ignore[no-untyped-def]
    """The revision a change is reviewed against: the source graph's and the campaign's replacements together."""
    return f"{view.graph_etag}|{view.overlay_etag or ''}"


def _path_view(path: Path) -> dict:
    return {"path": path.path_id, "from": str(path.source), "to": str(path.target), "two_way": path.bidirectional,
            "requires": list(path.requires), "blocked": path.blocked}


def _view(ctx: AdapterContext, ref: SourceRef, campaign: str) -> dict:
    library = _library(ctx)
    view = library.view(ref, requested_by=DIRECTOR, campaign_id=campaign)
    source = {path.path_id: path for path in library.view(ref, requested_by=DIRECTOR).index.topology.paths}
    graph = view.index.topology
    missing = set(view.index.missing)
    names = {place.ref: _name(ctx, place.ref) for place in graph.places}
    return {"topology": str(ref), "name": _name(ctx, ref), "etag": _etag(view),
            "places": [{"place": str(place.ref), "name": names[place.ref], "aliases": list(place.aliases),
                        "parent": None if place.parent is None else str(place.parent),
                        "missing": place.ref in missing} for place in graph.places],
            "paths": [{**_path_view(path), "changed": source.get(path.path_id) != path}
                      for path in sorted(graph.paths, key=lambda item: item.path_id)],
            "missing": [str(item) for item in view.index.missing]}


def _places(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The library's topologies and one of them as this campaign has it (the one asked for, else the first)."""
    common.director(args)
    campaign = common.campaign(args)
    listed = _topologies(ctx)
    wanted = args.get("topology")
    if wanted is None:
        ref = SourceRef.parse(listed[0]["topology"]) if listed else None
    else:
        ref = common.pinned(wanted, "topology")
        if ref.kind != "topology":
            raise ApiRefused("invalid_field", "topology must name a topology")
    return {"campaign": campaign, "topologies": listed,
            "topology": None if ref is None else _view(ctx, ref, campaign)}


def _index(ctx: AdapterContext, args: Mapping[str, object]):  # type: ignore[no-untyped-def]
    common.director(args)
    campaign = common.campaign(args)
    ref = common.pinned(args.get("topology"), "topology")
    return campaign, ref, _library(ctx).view(ref, requested_by=DIRECTOR, campaign_id=campaign)


def _find(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Every place a name or alias matches, each by its exact ref: 059 never resolves an ambiguous name."""
    _, _, view = _index(ctx, args)
    label = common.text(args.get("name"), "name", maximum=200)
    missing = set(view.index.missing)
    found = view.index.matches(label, requested_by=DIRECTOR)
    return {"name": label, "matches": [{"place": str(ref), "name": _name(ctx, ref), "missing": ref in missing}
                                       for ref in found]}


def _conditions(args: Mapping[str, object]) -> tuple[str, ...]:
    value = args.get("conditions", [])
    if type(value) is not list or len(value) > MAX_CONDITIONS or not all(type(item) is str for item in value):
        raise ApiRefused("invalid_field", f"conditions must be a list of at most {MAX_CONDITIONS} tokens")
    return tuple(value)


def _route(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The shortest route by hops between two exact places, under the conditions held; 059 refuses a missing
    place (``missing_place``) and a search it could not finish (``search_limit``)."""
    _, _, view = _index(ctx, args)
    start, end = common.pinned(args.get("from"), "from"), common.pinned(args.get("to"), "to")
    conditions = _conditions(args)
    found = view.index.route(start, end, requested_by=DIRECTOR, conditions=conditions)
    if found is None:
        return {"found": False, "route": None, "conditions": list(conditions)}
    return {"found": True, "conditions": list(conditions),
            "route": {"places": [{"place": str(ref), "name": _name(ctx, ref)} for ref in found.places],
                      "paths": list(found.paths)}}


def _change(ctx: AdapterContext, args: Mapping[str, object]):  # type: ignore[no-untyped-def]
    """The 059 change the request names, built against the revision it was reviewed at (nothing is written)."""
    campaign, ref, view = _index(ctx, args)
    blocked = args.get("blocked")
    if type(blocked) is not bool:
        raise ApiRefused("invalid_field", "blocked must be true or false")
    path_id = common.segment(args, "path")
    operation_id = common.segment(args, "operation_id")
    graph_etag, _, overlay = common.expected(args).partition("|")
    if graph_etag != view.graph_etag:  # the source graph itself was replaced since the review
        raise ApiRefused("changed", "the topology changed since it was reviewed; read it again")
    current = next((path for path in view.index.topology.paths if path.path_id == path_id), None)
    if current is None:
        raise ApiRefused("no_path", "this topology has no such path")
    wanted = replace(current, blocked=blocked)
    change = _library(ctx).change_path(ref, campaign, wanted, operation_id=operation_id, provenance=PROVENANCE,
                                       expected=overlay or None, requested_by=DIRECTOR)
    files = sorted(change.preview())
    if files and wanted == current:  # not a retry of this change, and nothing to change
        raise ApiRefused("no_change", "this road is already " + ("blocked" if blocked else "open"))
    plan = {"operation": operation_id, "already_applied": not files, "before": _path_view(current),
            "after": _path_view(wanted), "files": files}
    return campaign, ref, change, plan


def _path_plan(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """What blocking or opening one road in this campaign would do; nothing is written."""
    campaign, ref, _, plan = _change(ctx, args)
    return {"plan": plan, "topology": _view(ctx, ref, campaign)}


def _path_commit(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The change reviewed with ``path_plan``, built again with the same id and revision and made once."""
    campaign, ref, change, plan = _change(ctx, args)
    receipt = change.commit()
    return {"done": {**plan, "already_applied": receipt.replayed}, "topology": _view(ctx, ref, campaign)}


OPS = {"places": _places, "find_place": _find, "route": _route, "path_plan": _path_plan, "path_commit": _path_commit}
