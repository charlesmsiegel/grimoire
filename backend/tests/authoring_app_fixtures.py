"""A synthetic world for Sprint 116's Authoring page (``authoring.*``): no real library, no paid calls.

``seed_vale`` gives the desktop library a world, ``world:vale``: the Saltmarch Coast (a region), Port Lantern (a town
on it) and the Lamplighters Guild, whose hall Port Lantern's description names without a reference (so 082's audit
has a cited ``missing_link`` to find). ``towns`` is the proving proposal: three linked towns, a namesake of the guild
and a brainstorm. ``settings`` is a model settings file (112) with world suggestions switched on; the transport is
always ``grimoire.testing.providers.ScriptedTransport``.
"""

from __future__ import annotations

import json
from pathlib import Path

from store_helpers import OWNER

from grimoire.core.entity_codec import ENTITY_SCHEMA
from grimoire.core.identity import SourceRef
from grimoire.store import RecordEnvelope, SourceManifest
from grimoire.testing.providers import Reply

VALE = "world:vale"
COAST = SourceRef(VALE, "location", "saltmarch-coast", "r1")
LANTERN = SourceRef(VALE, "location", "port-lantern", "r1")
GUILD = SourceRef(VALE, "organization", "lamplighters-guild", "r1")
MANIFEST = "sources/world/vale/_source/manifest.json"
HALL = "The Lamplighters Guild keeps its hall by the harbor."
USAGE = {"prompt_tokens": 380, "completion_tokens": 140}


def entity(ref: SourceRef, name: str, *, spatial: bool, description: str = "") -> RecordEnvelope:
    payload = {"name": name, "nature": "individual", "traits": [{"facet": "spatial"}] if spatial else []}
    if description:
        payload["description"] = description
    return RecordEnvelope(ref, payload, name=name, owner=OWNER, extra={"schema": ENTITY_SCHEMA})


def seed_vale(device) -> None:
    store = device.table.store
    txn = store.transaction()
    txn.put(entity(COAST, "Saltmarch Coast", spatial=True), None)
    txn.put(entity(LANTERN, "Port Lantern", spatial=True, description=HALL), None)
    txn.put(entity(GUILD, "Lamplighters Guild", spatial=False), None)
    txn.put(SourceManifest(VALE, OWNER, "The Vale", {"location/saltmarch-coast": "r1", "location/port-lantern": "r1",
                                                     "organization/lamplighters-guild": "r1"}), None)
    txn.commit(device.table.owner)


def candidate(key: str, kind: str, name: str, role: str = "individual", description: str = "") -> dict:
    return {"key": key, "kind": kind, "name": name, "role": role, "description": description}


def link(link_id: str, relation: str, subject: str, target: str) -> dict:
    return {"id": link_id, "relation": relation, "subject": subject, "object": target}


def towns() -> dict:
    """Three linked towns on the coast, a namesake of the world's guild and a loose brainstorm."""
    return {
        "candidates": [
            candidate("brinewick", "location", "Brinewick", description="A harbor town of smokehouses."),
            candidate("gullhaven", "location", "Gullhaven", description="A cliff town above the shoals."),
            candidate("tidemoor", "location", "Tidemoor", description="A marsh town reached at low tide."),
            candidate("lamplighters", "organization", "The Lamplighters' Guild", description="Keepers of the lights."),
            candidate("bell", "location", "A drowned bell tower", "brainstorm", "Maybe it rings under the sea."),
        ],
        "links": [
            link("shore-road", "road", "brinewick", "gullhaven"),
            link("marsh-road", "road", "gullhaven", "tidemoor"),
            link("brinewick-in", "within", "brinewick", str(COAST)),
            link("gullhaven-in", "within", "gullhaven", str(COAST)),
            link("tidemoor-in", "within", "tidemoor", str(COAST)),
            link("bell-road", "road", "bell", "tidemoor"),
        ],
        "rationale": "The shore needs towns to visit.",
    }


def settings(path: Path, **extra) -> Path:
    raw = {"provider_id": "vale", "base_url": "https://vale.example/v1", "model": "vale-model",
           "max_output_tokens": 900, "price_prompt": "3", "price_reply": "15", "authoring": 1, **extra}
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def reply(body: dict, **extra) -> Reply:
    return Reply(deltas=(json.dumps(body),), usage=dict(USAGE), **extra)
