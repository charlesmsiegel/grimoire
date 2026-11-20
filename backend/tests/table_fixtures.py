"""Sprint 115's table (058, 051, 050) over one synthetic library: Mara Vell's card chat with Anya Rook as the owner's
persona, Tamsin Hale attached and handed to Bob, a second local player at this device, and the card's greeting as
its first turn. For 050, two individualized goblins of one species (Snik and Grub) and a few ordinary balls in
Mara's and Tamsin's holdings. Scripted generation only; no model is called."""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
from pathlib import Path

from entity_fixtures import EMBER, goblin
from scene_fixtures import BOB, POLICY, Table, attach_tamsin, card_library, say
from store_helpers import OWNER

from grimoire.app import AppServer
from grimoire.app.appapi import AppApi
from grimoire.core import DIRECTOR, CampaignRoster, DefinitionIndex, InstanceRef, SourceDescriptor
from grimoire.core.entity import EntityInstance
from grimoire.core.entity_codec import ENTITY_SCHEMA, definition_to_payload, instance_to_payload
from grimoire.core.individual import IndividualizationDecision, individualize
from grimoire.holdings import Line, Operation
from grimoire.holdingstore import HoldingsLibrary
from grimoire.store import RecordEnvelope, SourceManifest
from grimoire.textapi import TextApi

BALL = "ordinary-ball"
SPECIES = goblin()
DIRECTOR_VIEW = {"audience": "director"}
__all__ = ["BALL", "BOB", "DIRECTOR_VIEW", "OWNER", "Played", "balls", "node_e2e", "played", "say"]


class Played:
    """The open table: the library, the text API with Bob at this device, the app over it and the cast by name."""

    def __init__(self, table: Table, api: TextApi, chat, storm, cast: dict[str, str]) -> None:
        self.table, self.api, self.app = table, api, AppApi(api)
        self.chat, self.storm, self.cast = chat, storm, cast
        campaign = chat.campaign_id
        self.snik = InstanceRef(campaign, "creature", "goblin-1")
        self.grub = InstanceRef(campaign, "creature", "goblin-2")

    def __call__(self, op: str, **args):
        return self.app.call(op, args)


def _individual(ref: InstanceRef, name: str) -> EntityInstance:
    """A generic goblin made an individual in place with 024: its own creature, of the goblin species."""
    generic = EntityInstance(ref, SPECIES.ref.unpinned())
    index = DefinitionIndex([SourceDescriptor(EMBER, owner=OWNER)], [SPECIES], {SPECIES.ref.unpinned(): "r1"})
    decision = IndividualizationDecision(ref.campaign_id, f"d-{name.lower()}", ref, name, DIRECTOR, "s1")
    return individualize(CampaignRoster(ref.campaign_id, (generic,)), decision, index, ()).instance


def _creatures(played_: Played) -> None:
    store = played_.table.store
    txn = store.transaction()
    txn.put(SourceManifest(EMBER, OWNER, "Emberfall", {"species/goblin": "r1"}), None)
    txn.put(RecordEnvelope(SPECIES.ref, definition_to_payload(SPECIES), name=SPECIES.name,
                           extra={"schema": ENTITY_SCHEMA}), None)
    for ref, name in ((played_.snik, "Snik"), (played_.grub, "Grub")):
        txn.put(RecordEnvelope(ref, instance_to_payload(_individual(ref, name)), owner=OWNER,
                               extra={"schema": ENTITY_SCHEMA}), None)
    txn.commit(played_.table.owner)
    HoldingsLibrary(store, played_.table.owner, clock=lambda: "2026-01-01T00:00:00Z", owner=OWNER).apply(
        Operation("seed-balls", (Line.add(InstanceRef.parse(played_.cast["Mara Vell"]), BALL, 3),
                                 Line.add(InstanceRef.parse(played_.cast["Tamsin Hale"]), BALL, 1)))).commit()


def balls(played_: Played, name: str) -> int:
    ref = InstanceRef.parse(played_.cast[name])
    return HoldingsLibrary(played_.table.store, played_.table.owner, clock=lambda: "x").read(ref).counts().get(BALL, 0)


@contextlib.contextmanager
def played(tmp_path: Path, steps=(), *, creatures: bool = False, bob: bool = True):
    """The card chat at its greeting, with Tamsin handed to Bob (``bob``; else she is a second NPC beside Mara);
    ``steps`` script the generations after it."""
    folder, chat, storm = card_library(tmp_path)
    with Table(folder, list(steps)) as table:
        tamsin = str(attach_tamsin(table.store, table.owner, chat))
        api = TextApi(table.store, table.authority, owner=OWNER, local_players=[BOB],
                      runtime=table.harness.runtime, policy=POLICY)
        app = AppApi(api)
        scene = app.call("continue", {"chat": str(chat)})  # the card's greeting is the chat's first turn
        if scene["state"]["open_turn"]:
            app.call("keep", {"chat": str(chat), "take": 1, "turn": scene["state"]["open_turn"]})
        if bob:
            scene = app.call("seat", {"chat": str(chat), "actor": tamsin, "player": BOB})
        cast = {member["name"]: member["actor"] for member in scene["cast"]}
        result = Played(table, api, chat, storm, cast)
        if creatures:
            _creatures(result)
        yield result


def node_e2e(played_: Played, script: str, env: dict[str, str], node: str) -> subprocess.CompletedProcess:
    """Serve the library on loopback and run one fakedom page test (``js/<script>``) against it."""
    server = AppServer(played_.api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.address
        full = {**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": server.token,
                "GRIMOIRE_CHAT": str(played_.chat), "GRIMOIRE_STORM": str(played_.storm),
                "GRIMOIRE_CAMPAIGN": played_.chat.campaign_id, **env}
        return subprocess.run([node, "--test", "--test-reporter=tap", str(Path(__file__).with_name("js") / script)],
                              capture_output=True, text=True, timeout=180, check=False, env=full)
    finally:
        server.close()
        thread.join(10)
