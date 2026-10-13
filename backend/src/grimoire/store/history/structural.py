"""Independent structural reasons, with current affiliation labelled as such.

Ledger projections own history and aliases; membership owns leaders and
detachment. The joins intersect with eligible documents before ranking. Plain
co-presence above the cap orders candidates but cannot admit them.
"""

from __future__ import annotations

from itertools import combinations

from .. import chronicle, membership, relationship_history
from ..continuity import involvement
from ..scenes import read as scenes_read
from ..searchdocs import live
from . import model, refs
from . import query as query_mod

STRUCTURAL_PLAIN_CAP = 12


def _chronicle(cid: str) -> dict:
    try:
        chron = chronicle.read_chronicle(cid)
        return chron if isinstance(chron, dict) else {}
    except Exception:  # noqa: BLE001 -- appearances and heads can still answer
        return {}


def _touched(cid: str) -> dict[str, dict[str, set[str]]]:
    touched = {}
    for kind in ("thread", "commitment"):
        try:
            touched[kind] = involvement.group_touched(cid, kind)
        except Exception:  # noqa: BLE001 -- isolate each ledger
            touched[kind] = {}
    return touched


def _relationships(cid: str, present: frozenset[str], actor: str = "") -> dict[str, set[str]]:
    """Scenes where a present pair's relationship changed. An actor reads
    only pairs it is in: two others' history is not its to rank by."""
    changed: dict[str, set[str]] = {}
    for a, b in combinations(sorted(present), 2):
        if actor and actor not in (a, b):
            continue
        try:
            rows = relationship_history.for_pair(
                cid, a.replace(":", "/", 1), b.replace(":", "/", 1)
            )
        except Exception:  # noqa: BLE001 -- a garbled relationship history costs its reason only
            continue
        for row in rows:
            if not row.get("scene_gone") and isinstance(row.get("scene"), str):
                changed.setdefault(row["scene"], set()).update((a, b))
    return changed


def _locations(cid: str, doc: live.LiveDocument, cat: query_mod.Catalog, chron: dict) -> set[str]:
    locations = {refs.norm(r) for r in doc.doc.meta.get("locations", [])}
    try:
        history = scenes_read.get_location_history(cid, doc.sid)
    except Exception:  # noqa: BLE001 -- document metadata still answers
        history = []
    locations.update("locations:" + loc for loc in history)
    record = chron.get(doc.sid)
    historical = record.get("location") if isinstance(record, dict) else None
    if isinstance(historical, str):
        locations.update(
            ref
            for ref, names in cat.names.items()
            if ref.startswith("locations:")
            and any(query_mod.normalized(historical) == n for n in names)
        )
    return locations


def _relations(
    cid: str,
    doc: live.LiveDocument,
    cat: query_mod.Catalog,
    seeds: frozenset[str],
    actors: dict[str, set[str]],
    chron: dict,
    touched: dict,
    changed: dict,
) -> tuple[model.Relation, ...]:
    scene_actors = {refs.norm(a) for a in actors.get(doc.sid, set())} - cat.excluded
    shared = cat.present.intersection(scene_actors)
    threads = []
    for seed in seeds:
        kind, _, rid = seed.partition(":")
        if kind in touched and doc.sid in touched[kind].get(rid, set()):
            threads.append(seed)
    related = {
        "shared_cast": shared if len(shared) >= 2 else (),
        "actor": (seeds - cat.present).intersection(scene_actors),
        "location": seeds.intersection(_locations(cid, doc, cat, chron)),
        "thread": threads,
        "relationship": changed.get(doc.sid, ()),
        "group": [
            g
            for g in seeds
            if g.startswith("groups:")
            and len(scene_actors.intersection(membership.affiliated(cat.groups, g))) >= 2
        ],
    }
    return tuple(
        model.Relation(kind, tuple(sorted(found))) for kind, found in related.items() if found
    )


def _rank(candidate: model.Candidate) -> tuple[int, int]:
    signal = candidate.rounds[0].structural
    assert signal is not None
    return (
        -len({r.kind for r in signal.relations}),
        -len({ref for r in signal.relations for ref in r.refs}),
    )


def candidates(
    cid: str,
    sid: str,
    query: model.Query,
    docs: tuple[live.LiveDocument, ...],
    *,
    catalog: query_mod.Catalog | None = None,
) -> list[model.Candidate]:
    cat = query_mod.scoped(catalog if catalog is not None else query_mod.catalog(cid, sid),
                           query.perspective)
    seeds = frozenset(
        query_mod.subjects(cid, cat, cat.clean("\n".join(query.texts)), query.subjects)
    )
    actors = involvement.scene_actors(cid)
    chron, touched, changed = _chronicle(cid), _touched(cid), _relationships(cid, cat.present, cat.actor)
    relations = {
        doc.sid: tuple(
            r
            for r in _relations(cid, doc, cat, seeds, actors, chron, touched, changed)
            if r.kind in query.scopes
        )
        for doc in docs
    }
    plain = sum({r.kind for r in rs} == {"shared_cast"} for rs in relations.values())
    out = []
    for doc in docs:
        found = relations[doc.sid]
        if not found:
            continue
        admits = {r.kind for r in found} != {"shared_cast"} or plain < STRUCTURAL_PLAIN_CAP
        out.append(
            model.Candidate(
                model.SceneRef(doc.sid, doc.doc.meta.get("identity") or None),
                (model.Signals(structural=model.StructuralSignal(found)),),
                {},
                1,
                frozenset({"structural"}) if admits else frozenset(),
            )
        )
    # Stable two-pass sorting implements the archive's newest-first tie rule.
    out.sort(key=lambda c: c.scene.sid, reverse=True)
    out.sort(key=_rank)
    return out
