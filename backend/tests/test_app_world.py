"""Sprint 115's World page and its ``world.*`` adapter ops, through 108's shell contract.

The adapter serves 059's ``TopologyLibrary``, 063's rumor ledger with 020's ``EvidenceLibrary``, 057's ``decide``
and 060's ``admit`` over the app's open library, and keeps no record of its own. Everything is the director's: the
table is refused. The proving scenario runs as a screen would: the director reads the realm's roads, finds every
place a name matches without one being picked, asks a route between exact places and blocks a road in this
campaign, reviewed first and made once however often it is retried; a change made meanwhile refuses the reviewed
one. The director traces a faithful telling and a distorted letter that took three weeks on the campaign's
calendar, and admits the distant NPC's belief in it, reviewed. A faction's leader decides against a dissenting
member, and an NPC's mistaken theory of another's mind proposes a bluff, then a revised one an open question, with
nothing saved and the truth unchanged. The page itself runs end to end under Node (``js/world.e2e.mjs``). Synthetic
library; no model is called.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from scene_fixtures import ADJUDICATOR
from store_helpers import OWNER
from textapi_fixtures import Device, desktop_library, keep, say

from grimoire.app import AppServer
from grimoire.app.adapters.chronicle import FEATURES
from grimoire.app.adapters.world import ADAPTER as WORLD
from grimoire.app.appapi import AppApi
from grimoire.app.pages import load_pages
from grimoire.calendars import CalendarDefinition, Duration
from grimoire.calendarstore import CalendarLibrary
from grimoire.core import (
    DIRECTOR,
    Belief,
    Event,
    Moment,
    Perception,
    Proposition,
    Ruling,
    Subject,
)
from grimoire.core.identity import InstanceRef, ScopeRef, SourceRef
from grimoire.evidencestore import EvidenceLibrary
from grimoire.ledgers import ControlChange, SovereigntyLedger
from grimoire.rumorledger import output_id
from grimoire.rumors import CampaignTime, DeliveryConditions, Rumor
from grimoire.rumorstore import RumorLibrary
from grimoire.security import Grant, Permission, ResourcePath
from grimoire.store import RecordEnvelope, SourceManifest
from grimoire.textapi import ApiRefused
from grimoire.topology import Path as Road
from grimoire.topology import Place, Topology
from grimoire.topologystore import TopologyLibrary

STATIC = Path(__file__).resolve().parents[1] / "src" / "grimoire" / "app" / "static"
NODE = shutil.which("node")
D = {"audience": "director"}
DAY = 1440

# -- the realm's roads (059) ---------------------------------------------------------------------------------
GRAPH = SourceRef("world:saltcoast", "topology", "roads", "r1")
TOWN, GATE, WOOD, PORT, RUIN = (SourceRef("world:saltcoast", "location", x, "r1") for x in ("town", "gate", "wood", "port",
                                                                                       "ruin"))
#: an imported town of the same name, in another source: never the realm's town
OTHER = SourceRef("import:harbor", "location", "town", "r1")
PLACE_NAMES = {TOWN: "Saltmarch", GATE: "North Gate", WOOD: "Greywood", PORT: "Port Vell", OTHER: "Saltmarch"}

# -- the rumors (063) ----------------------------------------------------------------------------------------
TOLD = "Anya Rook laughed at the Vell banner"
LETTER = "Anya Rook tore down the Vell banner and spat on it"
NEAR = DeliveryConditions(proximity=True, affiliation=False, channel=False, attention=True)
LETTERED = DeliveryConditions(proximity=False, affiliation=False, channel=True, attention=True)

# -- Mara's mind (060) ---------------------------------------------------------------------------------------
GLANCE = "Anya glances at the sealed letter on Mara's desk."
ASKED = "Anya asks who could have opened a letter."
SECRET = "Anya knows what the letter says."


def refused(app: AppApi, op: str, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        app.call(op, args)
    return caught.value


@pytest.fixture
def desk(tmp_path):
    return desktop_library(tmp_path / "desk")


def roads() -> Topology:
    return Topology(GRAPH, (Place(TOWN, ("Old Town",)), Place(GATE, parent=TOWN), Place(WOOD), Place(PORT),
                            Place(OTHER), Place(RUIN)),
                    (Road("road", TOWN, GATE, bidirectional=True), Road("bridge", GATE, PORT),
                     Road("trail", TOWN, WOOD), Road("ferry", WOOD, PORT, requires=("ticket",)),
                     Road("causeway", PORT, RUIN), Road("drove", OTHER, PORT)), "human:owner")


def seed_roads(device) -> None:
    """The realm's places (the ruin's definition is not in this library) and its published roads."""
    store, owner = device.table.store, device.table.owner
    txn = store.transaction()
    txn.put(SourceManifest("world:saltcoast", OWNER), None)
    txn.put(SourceManifest("import:harbor", OWNER), None)
    for ref, name in PLACE_NAMES.items():
        txn.put(RecordEnvelope(ref, {}, name=name), None)
    txn.commit(owner)
    TopologyLibrary(store, owner).publish(roads(), requested_by=DIRECTOR).commit()


class Coast:
    """The campaign's rumors on one open device: an insult's witness, the faithful telling and the letter."""

    def __init__(self, device, chat) -> None:
        table = device.table
        self.table, self.campaign = table, chat.campaign_id
        scene = device.api.runner.open(chat)
        self.anya = scene.persona
        (self.mara,) = [actor for actor in scene.cast if actor != scene.persona]
        self.oswin, self.bryn = (InstanceRef(self.campaign, "character", name) for name in ("oswin", "bryn"))
        handle = table.authority.handle_for(ADJUDICATOR, trusted=True)
        self.rumors = RumorLibrary(table.store, table.authority, handle)
        self.evidence = EvidenceLibrary(table.store, handle)
        self.calendar = CalendarLibrary(table.store, handle)
        self.scope = ScopeRef(self.campaign)

    def next(self) -> Moment:
        knowledge = self.evidence.read(self.campaign, requested_by=DIRECTOR).knowledge
        records = (*knowledge.events, *knowledge.propositions, *knowledge.evidence, *knowledge.beliefs,
                   *knowledge.rulings)
        return Moment(max((record.moment.sequence for record in records), default=0) + 1)

    def now(self) -> CampaignTime:
        now = self.calendar.read(self.scope).state.now
        return CampaignTime(now.fixed_day, now.minute)

    def later(self, operation: str, days: int) -> None:
        view = self.calendar.read(self.scope)
        self.calendar.advance(self.scope, operation, Duration(days * DAY), expected=view.etag).commit()

    def send(self, rid, sender, recipient, source, text, distortion, days, route, channel) -> None:
        at = self.next()
        sent = self.now()
        self.rumors.enqueue(Rumor(rid, sender, recipient, source, text, channel, sender, 80, distortion,
                                  "director:rumor", at, sent, sent.advance(days * DAY), route),
                            moment=at, requested_by=DIRECTOR)

    def deliver(self, conditions):
        return self.rumors.advance(self.campaign, moment=self.next(), conditions=conditions, requested_by=DIRECTOR)


def seed_world(device, chat) -> dict[str, str]:
    """Everything the World page shows, on the card chat's campaign: the roads; Oswin's sight of the insult, Oswin's
    faithful telling to Bryn and Bryn's distorted letter to Mara (21 days, delivered) and Bryn's gossip to Anya (still on
    the road); and Mara's two clues about Anya and the director's ruling that Anya knows nothing. Answers refs."""
    device.api.runner.seat(chat, OWNER)
    device("post", chat=str(chat), text="*I set the letter down*")
    device("generate", chat=str(chat))
    keep(device, str(chat))
    seed_roads(device)
    table = device.table
    coast = Coast(device, chat)
    campaign = coast.campaign
    txn = table.store.transaction()  # two offstage campaign characters, in no chat's cast
    for actor, name in ((coast.oswin, "Oswin"), (coast.bryn, "Bryn")):
        txn.put(RecordEnvelope(actor, {}, name=name), None)
    txn.commit(table.owner)
    for actor in (coast.oswin, coast.bryn):
        SovereigntyLedger(table.authority).record(campaign, ControlChange(campaign, actor.instance_id, DIRECTOR),
                                                  table.owner)
    table.authority.add_grant(Grant("rumors", ADJUDICATOR, frozenset({Permission.APPLY}),
                                    ResourcePath.parse("campaigns"), OWNER))
    coast.calendar.initialize(coast.scope, "start", CalendarDefinition(), "1203-03-01", minute=17 * 60,
                              expected=coast.calendar.read(coast.scope).etag).commit()
    at = coast.next()
    coast.evidence.append(campaign, [
        Event(campaign, "banner-insult", "Anya Rook laughed at the Vell banner on the quay", at, "scene:quay",
              participants=(coast.anya,)),
        Perception(campaign, "oswin-saw", coast.oswin, "banner-insult", "sight", "a laugh at the banner", at,
                   "scene:quay", True)]).commit()
    coast.send("evening", coast.oswin, coast.bryn, "oswin-saw", TOLD, "none", 0, NEAR, "spoken")
    assert coast.deliver(NEAR)["delivered"] == ("evening",)
    coast.send("letter", coast.bryn, coast.mara, output_id(campaign, "evening", "report"), LETTER, "exaggerated", 21,
               LETTERED, "letter")
    coast.send("gossip", coast.bryn, coast.anya, output_id(campaign, "evening", "report"), TOLD, "none", 40,
               LETTERED, "letter")
    coast.later("three-weeks", 21)
    assert coast.deliver(LETTERED)["delivered"] == ("letter",)
    # Mara's own clues about Anya, and the truth: Anya does not know, and Mara does not believe Anya does
    first, second = coast.next(), Moment(coast.next().sequence + 2)
    coast.evidence.append(campaign, [
        Event(campaign, "glance", "Anya glanced at the sealed letter", first, "scene:study"),
        Perception(campaign, "mara-glance", coast.mara, "glance", "sight", GLANCE, first, "scene:study", True),
        Proposition(campaign, "anya-knows", SECRET, first, "director", about=(Subject("event", "glance"),)),
        Ruling(campaign, "truth", "anya-knows", "refuted", first, "director"),
        Event(campaign, "asked", "Anya asked who opened the letter", second, "scene:study"),
        Perception(campaign, "mara-asked", coast.mara, "asked", "hearing", ASKED, second, "scene:study", True),
    ]).commit()
    coast.evidence.append(campaign, [Belief(campaign, "mara-doubts", coast.mara, "anya-knows", "rejected",
                                            Moment(second.sequence + 1), DIRECTOR, ("mara-glance",))],
                          controls=SovereigntyLedger(table.authority).load(campaign).controls).commit()
    return {"mara": str(coast.mara), "anya": str(coast.anya), "oswin": str(coast.oswin), "bryn": str(coast.bryn),
            "campaign": campaign}


def scope_bytes(device, campaign: str) -> dict[str, bytes]:
    root = device.table.store.root / "campaigns" / campaign
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


# -- the page ------------------------------------------------------------------------------------------------
def test_the_page_registers_hidden_on_its_own_route_and_the_chronicle_links_it():
    pages = {page.id: page for page in load_pages(STATIC)}
    page = pages["world"]
    assert (page.route, page.slot, page.owner) == ("world", "hidden", "115")
    assert {"world.places", "world.rumors", "world.cast", "world.mind"} <= set(page.requires)
    shown = {item["slice"]: item for item in FEATURES}
    for slice_ in ("057", "059", "060", "063"):
        assert (shown[slice_]["status"], shown[slice_]["route"], shown[slice_]["campaign"]) == ("screen", "world", True)


def test_every_world_op_is_the_directors(desk):
    folder, chat = desk
    with Device(folder, [say("Mara reads it.")]) as device:
        refs = seed_world(device, chat)
        app = AppApi(device.api)
        campaign = refs["campaign"]
        asks = {"world.places": {"campaign": campaign}, "world.rumors": {"campaign": campaign},
                "world.find_place": {"campaign": campaign, "topology": str(GRAPH), "name": "Saltmarch"},
                "world.route": {"campaign": campaign, "topology": str(GRAPH), "from": str(TOWN), "to": str(PORT)},
                "world.path_plan": {"campaign": campaign, "topology": str(GRAPH), "path": "road", "blocked": True,
                                    "operation_id": "x", "expected": "x"},
                "world.path_commit": {"campaign": campaign, "topology": str(GRAPH), "path": "road", "blocked": True,
                                      "operation_id": "x", "expected": "x"},
                "world.belief_plan": {"campaign": campaign, "rumor": "letter", "stance": "believed",
                                      "operation_id": "x", "expected": "x"},
                "world.belief_commit": {"campaign": campaign, "rumor": "letter", "stance": "believed",
                                        "operation_id": "x", "expected": "x"},
                "world.cast": {"chat": str(chat)}, "world.mind": {"chat": str(chat), "owner": refs["mara"]},
                "world.faction_preview": {"chat": str(chat)}, "world.mind_preview": {"chat": str(chat)},
                "world.topologies": {"campaign": campaign}}
        assert {op.removeprefix("world.") for op in asks} == set(WORLD.ops), "every world op is asked here"
        before = scope_bytes(device, campaign)
        for op, args in asks.items():
            assert refused(app, op, **args).reason == "director_only", op
            assert refused(app, op, **args, audience="table").reason == "director_only", op
            assert refused(app, op, **args, audience="everyone").reason == "invalid_field", op
        assert scope_bytes(device, campaign) == before


# -- places and routes (059) ---------------------------------------------------------------------------------
def test_places_routes_and_a_road_blocked_once_as_reviewed(desk):
    folder, chat = desk
    with Device(folder, [say("Mara reads it.")]) as device:
        campaign = seed_world(device, chat)["campaign"]
        app = AppApi(device.api)
        shown = app.call("world.places", {"campaign": campaign, **D})
        assert shown["topologies"] == [{"topology": str(GRAPH), "name": None}]
        view = shown["topology"]
        places = {item["place"]: item for item in view["places"]}
        assert places[str(GATE)]["parent"] == str(TOWN) and places[str(TOWN)]["aliases"] == ["Old Town"]
        assert places[str(RUIN)]["missing"] is True and view["missing"] == [str(RUIN)]
        assert places[str(OTHER)]["name"] == places[str(TOWN)]["name"] == "Saltmarch"
        assert [item["path"] for item in view["paths"]] == ["bridge", "causeway", "drove", "ferry", "road", "trail"]

        # a name finds every place it matches, each by its exact ref, and never picks one
        ask = {"campaign": campaign, **D, "topology": str(GRAPH)}
        found = app.call("world.find_place", {**ask, "name": "saltmarch"})["matches"]
        assert sorted(item["place"] for item in found) == sorted([str(TOWN), str(OTHER)])
        assert [item["place"] for item in app.call("world.find_place", {**ask, "name": "old town"})["matches"]] \
            == [str(TOWN)]

        route = app.call("world.route", {**ask, "from": str(TOWN), "to": str(PORT)})
        assert route["found"] and route["route"]["paths"] == ["road", "bridge"]
        assert [item["name"] for item in route["route"]["places"]] == ["Saltmarch", "North Gate", "Port Vell"]
        # the imported namesake is its own place: its route is its own road, never the realm town's
        assert app.call("world.route", {**ask, "from": str(OTHER), "to": str(PORT)})["route"]["paths"] == ["drove"]
        assert app.call("world.route", {**ask, "from": str(PORT), "to": str(TOWN)})["found"] is False
        # a missing place keeps 059's reason, whichever end it is
        assert refused(app, "world.route", **ask, **{"from": str(TOWN), "to": str(RUIN)}).reason == "missing_place"
        assert refused(app, "world.route", **ask, **{"from": str(RUIN), "to": str(TOWN)}).reason == "missing_place"
        assert refused(app, "world.route", **ask, **{"from": "world:realm/location/town", "to": str(PORT)}).reason \
            == "invalid_field"
        assert refused(app, "world.route", **ask, **{"from": str(TOWN), "to": str(PORT),
                                                    "conditions": ["Not A Token"]}).reason == "invalid_conditions"

        # block the bridge: reviewed (nothing written), then made once however often the commit is retried
        before = scope_bytes(device, campaign)
        block = {**ask, "path": "bridge", "blocked": True, "operation_id": "block-bridge", "expected": view["etag"]}
        plan = app.call("world.path_plan", block)["plan"]
        assert (plan["already_applied"], plan["before"]["blocked"], plan["after"]["blocked"]) == (False, False, True)
        assert len(plan["files"]) == 1 and plan["files"][0].startswith(f"campaigns/{campaign}/")
        assert scope_bytes(device, campaign) == before
        done = app.call("world.path_commit", block)
        assert done["done"]["already_applied"] is False
        bridge = next(item for item in done["topology"]["paths"] if item["path"] == "bridge")
        assert bridge["blocked"] and bridge["changed"]
        written = scope_bytes(device, campaign)
        again = app.call("world.path_commit", block)
        assert again["done"]["already_applied"] is True and scope_bytes(device, campaign) == written
        # with the bridge blocked the route goes round, by the ferry, only with a ticket
        assert app.call("world.route", {**ask, "from": str(TOWN), "to": str(PORT)})["found"] is False
        assert app.call("world.route", {**ask, "from": str(TOWN), "to": str(PORT),
                                        "conditions": ["ticket"]})["route"]["paths"] == ["trail", "ferry"]

        # the same id for another road is refused; blocking a blocked road changes nothing and is refused
        etag = done["topology"]["etag"]
        assert refused(app, "world.path_commit", **{**block, "path": "road", "expected": etag}).reason \
            == "operation_conflict"
        assert refused(app, "world.path_plan", **{**block, "operation_id": "again", "expected": etag}).reason \
            == "no_change"
        # reviewed, then overtaken by another change: the reviewed one is refused and writes nothing
        trail = {**ask, "path": "trail", "blocked": True, "operation_id": "block-trail", "expected": etag}
        app.call("world.path_plan", trail)
        app.call("world.path_commit", {**ask, "path": "road", "blocked": True, "operation_id": "block-road",
                                       "expected": etag})
        overtaken = scope_bytes(device, campaign)
        assert refused(app, "world.path_commit", **trail).reason == "changed"
        assert refused(app, "world.path_commit", **{**trail, "expected": None}).reason == "review_required"
        assert scope_bytes(device, campaign) == overtaken
        # opening the bridge again is its own reviewed change; the source roads never change
        latest = app.call("world.places", {**ask})["topology"]["etag"]
        opened = app.call("world.path_commit", {**ask, "path": "bridge", "blocked": False, "operation_id": "open-bridge",
                                                "expected": latest})["topology"]
        assert not next(item for item in opened["paths"] if item["path"] == "bridge")["changed"]
        source = TopologyLibrary(device.table.store, device.table.owner).view(GRAPH, requested_by=DIRECTOR)
        assert source.index.topology == roads()
        # the first block retried after other changes were made (its answer lost): made already, nothing changes
        settled = scope_bytes(device, campaign)
        retried = app.call("world.path_commit", block)
        assert retried["done"]["already_applied"] is True and scope_bytes(device, campaign) == settled
        assert not next(item for item in retried["topology"]["paths"] if item["path"] == "bridge")["blocked"]
        assert app.call("world.path_plan", block)["plan"]["already_applied"] is True


def test_topology_requests_the_domain_would_not_take_are_refused_with_its_reason(desk):
    folder, chat = desk
    with Device(folder, [say("Mara reads it.")]) as device:
        campaign = seed_world(device, chat)["campaign"]
        app = AppApi(device.api)
        ask = {"campaign": campaign, **D}
        assert refused(app, "world.places", **ask, topology=str(SourceRef("world:saltcoast", "topology", "none", "r1"))) \
            .reason == "unavailable_topology"
        assert refused(app, "world.places", **ask, topology=str(TOWN)).reason == "invalid_field"
        assert refused(app, "world.places", campaign="nowhere", **D, topology=str(GRAPH)).reason == "missing_campaign"
        # every campaign-scoped read says so the same way, never with a store path
        for op in ("world.topologies", "world.rumors"):
            missing = refused(app, op, campaign="nowhere", **D)
            assert missing.reason == "missing_campaign" and "/" not in str(missing), op
        assert refused(app, "world.belief_plan", campaign="nowhere", **D, rumor="letter", stance="believed",
                       operation_id="o", expected="x").reason == "missing_campaign"
        assert app.call("world.topologies", ask)["topologies"] == [{"topology": str(GRAPH), "name": None}]
        etag = app.call("world.places", ask)["topology"]["etag"]
        base = {**ask, "topology": str(GRAPH), "blocked": True, "operation_id": "op", "expected": etag}
        assert refused(app, "world.path_plan", **base, path="tunnel").reason == "no_path"
        assert refused(app, "world.path_plan", **{**base, "operation_id": "Not An Id"}, path="road").reason \
            == "invalid_field"
        assert refused(app, "world.path_plan", **{**base, "blocked": "yes"}, path="road").reason == "invalid_field"
        # a road id keeps the domain's own limit (128); only the operation id this adapter builds ids from is shorter
        assert refused(app, "world.path_plan", **base, path="r" * 100).reason == "no_path"
        assert refused(app, "world.path_plan", **base, path="r" * 129).reason == "invalid_field"
        assert refused(app, "world.path_plan", **{**base, "operation_id": "o" * 81}, path="road").reason \
            == "invalid_field"
        assert refused(app, "world.path_plan", **{**base, "expected": "elsewhere|"}, path="road").reason == "changed"


def test_a_road_change_retried_after_its_road_was_changed_again_is_already_applied(desk):
    """059 keeps what each operation recorded: the retry is recognized by that, not by the road as it is now."""
    folder, chat = desk
    with Device(folder, [say("Mara reads it.")]) as device:
        campaign = seed_world(device, chat)["campaign"]
        app = AppApi(device.api)
        ask = {"campaign": campaign, **D, "topology": str(GRAPH)}
        etag = app.call("world.places", ask)["topology"]["etag"]
        block = {**ask, "path": "bridge", "blocked": True, "operation_id": "block-bridge", "expected": etag}
        app.call("world.path_commit", block)
        # another change to the same road, made through 059 itself: the bridge now needs a toll paid
        library = TopologyLibrary(device.table.store, device.table.owner)
        now = library.view(GRAPH, requested_by=DIRECTOR, campaign_id=campaign)
        bridge = next(path for path in now.index.topology.paths if path.path_id == "bridge")
        library.change_path(GRAPH, campaign, replace(bridge, requires=("toll",)), operation_id="toll-bridge",
                            provenance="director:elsewhere", expected=now.overlay_etag,
                            requested_by=DIRECTOR).commit()
        settled = scope_bytes(device, campaign)
        # the first block retried (its answer lost) is the change already made, and changes nothing
        assert app.call("world.path_plan", block)["plan"]["already_applied"] is True
        retried = app.call("world.path_commit", block)
        assert retried["done"]["already_applied"] is True and scope_bytes(device, campaign) == settled
        shown = next(item for item in retried["topology"]["paths"] if item["path"] == "bridge")
        assert (shown["blocked"], shown["requires"]) == (True, ["toll"])
        # the id reused for another change to that road is still refused
        assert refused(app, "world.path_commit", **{**block, "blocked": False}).reason == "operation_conflict"
        assert scope_bytes(device, campaign) == settled


def test_an_unreadable_campaign_override_is_refused_with_its_reason_never_a_server_error(desk):
    folder, chat = desk
    with Device(folder, [say("Mara reads it.")]) as device:
        campaign = seed_world(device, chat)["campaign"]
        app = AppApi(device.api)
        ask = {"campaign": campaign, **D, "topology": str(GRAPH)}
        etag = app.call("world.places", ask)["topology"]["etag"]
        app.call("world.path_commit", {**ask, "path": "road", "blocked": True, "operation_id": "b", "expected": etag})
    (override,) = [path for path in (folder / "campaigns" / campaign).rglob("*.json") if "topology-" in path.name]
    override.write_text(override.read_text().replace("grimoire.topology-overlay/1", "grimoire.topology-overlay/9"))
    with Device(folder) as device:
        app = AppApi(device.api)
        assert refused(app, "world.places", **ask).reason == "unsupported_schema"
        assert refused(app, "world.route", **ask, **{"from": str(TOWN), "to": str(PORT)}).reason == "unsupported_schema"
        # the page can still list the topologies, and every other section still reads
        assert app.call("world.topologies", {"campaign": campaign, **D})["topologies"] == [
            {"topology": str(GRAPH), "name": None}]
        assert [item["rumor"] for item in app.call("world.rumors", {"campaign": campaign, "chat": str(chat), **D})[
            "rumors"]] == ["evening", "letter", "gossip"]
        mara = next(item["actor"] for item in app.call("world.cast", {"chat": str(chat), **D})["cast"]
                    if item["role"] == "npc")
        assert app.call("world.mind", {"chat": str(chat), **D, "owner": mara})["evidence"]
        assert app.call("world.faction_preview", {"chat": str(chat), **D, "group": "House Vell", "leader": mara,
                                                  "goals": ["Hold the quay"], "policy": "The head decides",
                                                  "proposal": "Sell passage", "choice": "refuse",
                                                  "members": []})["decision"]["choice"] == "refuse"


# -- rumors (063) --------------------------------------------------------------------------------------------
def test_rumors_are_traced_and_the_directors_admission_of_a_belief_is_reviewed(desk):
    folder, chat = desk
    with Device(folder, [say("Mara reads it.")]) as device:
        refs = seed_world(device, chat)
        campaign = refs["campaign"]
        app = AppApi(device.api)
        shown = app.call("world.rumors", {"campaign": campaign, "chat": str(chat), **D})
        assert shown["now"]["date"] == "1203-03-22" and shown["now"]["time"] == "17:00"
        rumors = {item["rumor"]: item for item in shown["rumors"]}
        evening, letter, gossip = rumors["evening"], rumors["letter"], rumors["gossip"]
        assert [item["rumor"] for item in shown["rumors"]] == ["evening", "letter", "gossip"]  # by arrival
        assert (evening["sender"]["name"], evening["recipient"]["name"], evening["faithful"]) == ("Oswin", "Bryn", True)
        assert evening["source"] == {"evidence": "oswin-saw", "content": "a laugh at the banner"}
        assert (letter["faithful"], letter["distortion"], letter["text"]) == (False, "exaggerated", LETTER)
        assert letter["source"]["content"] == TOLD  # the letter rests on what Bryn was told, not on the insult
        assert (letter["sent"]["date"], letter["due"]["date"], letter["channel"]) == ("1203-03-01", "1203-03-22", "letter")
        assert letter["route"] == {"proximity": False, "affiliation": False, "channel": True, "attention": True}
        assert (letter["status"], letter["belief"], letter["can_admit"]) == ("delivered", None, True)
        assert letter["recipient"] == {"actor": refs["mara"], "name": "Mara Vell", "role": "npc"}
        assert (gossip["status"], gossip["due"]["date"], gossip["can_admit"]) == ("on_the_way", "1203-04-10", False)
        assert gossip["recipient"]["role"] == "player"

        # a review overtaken by anything appended to the evidence ledger, even about something else, is refused
        coast = Coast(device, chat)
        stale = {"campaign": campaign, "chat": str(chat), **D, "rumor": "letter", "stance": "believed",
                 "operation_id": "too-late", "expected": shown["etag"]}
        assert app.call("world.belief_plan", stale)["plan"]["files"]
        coast.evidence.append(campaign, [Event(campaign, "gulls", "Gulls wheeled over the quay", coast.next(),
                                               "scene:quay")]).commit()
        appended = scope_bytes(device, campaign)
        assert refused(app, "world.belief_commit", **stale).reason == "changed"
        assert refused(app, "world.belief_plan", **stale).reason == "changed"
        assert scope_bytes(device, campaign) == appended
        shown = app.call("world.rumors", {"campaign": campaign, "chat": str(chat), **D})

        # admitting Mara's belief: a plan writes nothing, the commit writes it once
        before = scope_bytes(device, campaign)
        admit = {"campaign": campaign, "chat": str(chat), **D, "rumor": "letter", "stance": "believed", "operation_id": "mara-hears",
                 "expected": shown["etag"]}
        plan = app.call("world.belief_plan", admit)["plan"]
        assert (plan["already_applied"], plan["holder"], plan["belief"]) == (False, refs["mara"], "admit-mara-hears")
        assert plan["files"] and scope_bytes(device, campaign) == before
        assert refused(app, "world.belief_plan", **{**admit, "audience": "table"}).reason == "director_only"
        done = app.call("world.belief_commit", admit)
        believed = {item["rumor"]: item for item in done["rumors"]["rumors"]}["letter"]
        assert believed["belief"] == {"belief": "admit-mara-hears", "stance": "believed"} and not believed["can_admit"]
        written = scope_bytes(device, campaign)
        again = app.call("world.belief_commit", admit)
        assert again["done"]["already_applied"] is True and scope_bytes(device, campaign) == written
        assert refused(app, "world.belief_commit", **{**admit, "stance": "suspected"}).reason == "operation_conflict"
        # the belief is Mara's alone; canon never took the letter's word
        evidence = EvidenceLibrary(device.table.store)
        mara = InstanceRef.parse(refs["mara"])
        assert [item.text for item in evidence.actor_knowledge(campaign, mara, as_of=1000).current
                if item.belief_id == "admit-mara-hears"] == [LETTER]
        assert evidence.standing(campaign, output_id(campaign, "letter", "proposition"), as_of=1000) == "reported"

        # a fresh review is needed after any change; a rumor not delivered, a player's and a believed one refused
        latest = done["rumors"]["etag"]
        assert refused(app, "world.belief_plan", **{**admit, "operation_id": "late"}).reason == "changed"
        for rumor, reason in (("gossip", "not_delivered"), ("letter", "already_admitted"), ("nothing", "no_rumor")):
            assert refused(app, "world.belief_plan", **{**admit, "rumor": rumor, "operation_id": "next",
                                                        "expected": latest}).reason == reason
        # Bryn heard the faithful telling; Bryn is an NPC too, so the director admits that belief as well
        bryn = app.call("world.belief_commit", {**admit, "rumor": "evening", "operation_id": "bryn-hears",
                                                "stance": "suspected", "expected": latest})
        assert {item["rumor"]: item for item in bryn["rumors"]["rumors"]}["evening"]["belief"]["stance"] == "suspected"
        # Mara's admission retried after that other change (its answer lost): made already, nothing changes
        settled = scope_bytes(device, campaign)
        assert app.call("world.belief_commit", admit)["done"]["already_applied"] is True
        assert app.call("world.belief_plan", admit)["plan"]["already_applied"] is True
        assert scope_bytes(device, campaign) == settled
        assert refused(app, "world.belief_plan", **{**admit, "stance": "certain"}).reason == "invalid_field"
        # once the gossip reaches Anya, a player character's belief is still not this page's: 020 refuses it
        coast = Coast(device, chat)
        coast.later("weeks-more", 20)
        assert coast.deliver(LETTERED)["delivered"] == ("gossip",)
        delivered = app.call("world.rumors", {"campaign": campaign, "chat": str(chat), **D})
        reached = {item["rumor"]: item for item in delivered["rumors"]}["gossip"]
        assert (reached["status"], reached["can_admit"]) == ("delivered", False)
        before = scope_bytes(device, campaign)
        assert refused(app, "world.belief_plan", **{**admit, "rumor": "gossip", "operation_id": "anya-hears",
                                                    "expected": delivered["etag"]}).reason == "pc_belief_authorship"
        assert scope_bytes(device, campaign) == before
        # a chat of another campaign names nobody here
        assert refused(app, "world.rumors", campaign=campaign, chat="campaign:elsewhere#chat/t1", **D).reason \
            == "invalid_field"


def test_an_unreadable_rumor_ledger_is_refused_never_a_server_error(desk):
    folder, chat = desk
    with Device(folder, [say("Mara reads it.")]) as device:
        campaign = seed_world(device, chat)["campaign"]
    batches = sorted(path for path in (folder / "authority" / "rumors" / campaign).iterdir() if path.is_file())
    assert batches
    batches[-1].write_bytes(batches[-1].read_bytes().replace(b"letter", b"lettre"))
    with Device(folder) as device:
        app = AppApi(device.api)
        assert refused(app, "world.rumors", campaign=campaign, **D).reason == "ledger_unreadable"
        assert refused(app, "world.belief_plan", campaign=campaign, **D, rumor="letter", stance="believed",
                       operation_id="o", expected="x").reason == "ledger_unreadable"


def test_a_quarantined_evidence_record_is_refused_never_shown_as_never_admitted(desk):
    folder, chat = desk
    with Device(folder, [say("Mara reads it.")]) as device:
        refs = seed_world(device, chat)
    doubts = [path for path in folder.rglob("mara-doubts.json")]
    assert len(doubts) == 1
    doubts[0].unlink()
    with Device(folder) as device:
        app = AppApi(device.api)
        mind = refused(app, "world.mind", chat=str(chat), **D, owner=refs["mara"])
        assert mind.reason == "ledger_unreadable" and "belief:mara-doubts" in str(mind)
        assert refused(app, "world.mind_preview", chat=str(chat), **D, owner=refs["mara"],
                       hypotheses=[]).reason == "ledger_unreadable"
        assert refused(app, "world.rumors", campaign=refs["campaign"], chat=str(chat), **D).reason == "ledger_unreadable"


# -- faction decisions (057) ---------------------------------------------------------------------------------
def test_a_leaders_choice_keeps_the_members_dissent_and_nothing_is_saved(desk):
    folder, chat = desk
    with Device(folder, [say("Mara reads it.")]) as device:
        refs = seed_world(device, chat)
        campaign = refs["campaign"]
        app = AppApi(device.api)
        cast = app.call("world.cast", {"chat": str(chat), **D})["cast"]
        assert {(item["name"], item["role"], item["agent"]) for item in cast} \
            == {("Mara Vell", "npc", True), ("Anya Rook", "player", True)}
        before = scope_bytes(device, campaign)
        layout = {"chat": str(chat), **D, "group": "House Vell", "leader": refs["mara"],
                  "goals": ["Keep the Vell name feared along the coast"], "policy": "The head of the house decides",
                  "proposal": "Sell passage north to the pilot", "choice": "refuse",
                  "members": [{"actor": refs["mara"], "choice": "refuse"}], "told": [refs["anya"]]}
        answer = app.call("world.faction_preview", layout)
        assert answer["saved"] is False and answer["decision"] == {"choice": "refuse",
                                                                   "leader": {"actor": refs["mara"], "name": "Mara Vell"}}
        assert answer["dissent"] == [] and answer["agreeing"] == [{"actor": refs["mara"], "name": "Mara Vell",
                                                                    "choice": "refuse"}]
        assert answer["told"] == [{"actor": refs["anya"], "name": "Anya Rook"}]
        # the leader decides against what a member (here the leader, of its own choice) prefers: the dissent is kept beside it
        torn = app.call("world.faction_preview", {**layout, "members": [{"actor": refs["mara"], "choice": "sell passage"},
                                                                        {"actor": refs["anya"], "choice": None}]})
        assert torn["decision"]["choice"] == "refuse" and torn["agreeing"] == []
        assert torn["dissent"] == [{"actor": refs["mara"], "name": "Mara Vell", "choice": "sell passage"}]
        assert [item["name"] for item in torn["members"]] == ["Mara Vell", "Anya Rook"]
        # a player's character neither decides nor prefers: 057's npc_authority, kept
        assert refused(app, "world.faction_preview", **{**layout, "leader": refs["anya"]}).reason == "npc_authority"
        assert refused(app, "world.faction_preview", **{**layout, "members": [
            {"actor": refs["anya"], "choice": "sell"}]}).reason == "npc_authority"
        assert refused(app, "world.faction_preview", **{**layout, "members": [
            {"actor": refs["oswin"], "choice": "sell"}]}).reason == "no_actor"
        assert refused(app, "world.faction_preview", **{**layout, "goals": []}).reason == "invalid_field"
        assert refused(app, "world.faction_preview", **{**layout, "choice": "a\nb"}).reason == "invalid_field"
        assert scope_bytes(device, campaign) == before


# -- what an NPC thinks others think (060) -------------------------------------------------------------------
def test_an_npcs_mistaken_theory_proposes_a_tactic_and_its_revision_another_with_the_truth_unchanged(desk):
    folder, chat = desk
    with Device(folder, [say("Mara reads it.")]) as device:
        refs = seed_world(device, chat)
        campaign = refs["campaign"]
        app = AppApi(device.api)
        mind = app.call("world.mind", {"chat": str(chat), **D, "owner": refs["mara"]})
        assert mind["owner"]["role"] == "npc"
        held = {item["evidence"]: item for item in mind["evidence"]}
        assert held["mara-glance"]["content"] == GLANCE and held["mara-asked"]["content"] == ASKED
        assert "oswin-saw" not in held  # Oswin's sight, never Mara's
        (ruled,) = mind["truth"]["rulings"]
        assert {key: ruled[key] for key in ("proposition", "text", "standing", "ruling", "earlier")} == {
            "proposition": "anya-knows", "text": SECRET, "standing": "refuted", "ruling": "truth", "earlier": []}
        beliefs = {item["actor"]: item["beliefs"] for item in mind["truth"]["beliefs"]}
        assert [(item["belief"], item["stance"]) for item in beliefs[refs["mara"]]] == [("mara-doubts", "rejected")]

        before = scope_bytes(device, campaign)
        glance, asked = held["mara-glance"]["at"], held["mara-asked"]["at"]
        first = {"id": "h1", "about": [refs["anya"]], "claim": SECRET, "confidence": 0.8,
                 "evidence": ["mara-glance"], "at": glance}
        tactic = {"hypothesis": "h1", "minimum": 0.5, "credited": "Bluff that the letter was already read aloud.",
                  "fallback": "Ask an open question."}
        ask = {"chat": str(chat), **D, "owner": refs["mara"]}
        bluff = app.call("world.mind_preview", {**ask, "hypotheses": [first], "tactic": tactic})
        assert bluff["saved"] is False and bluff["unchanged"] is True
        assert (bluff["tactic"]["tactic"], bluff["tactic"]["credited"]) == ("Bluff that the letter was already read aloud.",
                                                                            True)
        assert [item["id"] for item in bluff["current"]] == ["h1"]
        revised = {"id": "h2", "about": [refs["anya"]], "claim": SECRET, "confidence": 0.1, "evidence": ["mara-asked"],
                   "at": asked, "supersedes": "h1"}
        question = app.call("world.mind_preview", {**ask, "hypotheses": [first, revised],
                                                   "tactic": {**tactic, "hypothesis": "h2"}})
        assert (question["tactic"]["tactic"], question["tactic"]["credited"]) == ("Ask an open question.", False)
        assert [item["id"] for item in question["current"]] == ["h2"] and question["admitted"] == ["h1", "h2"]
        # the truth and Mara's actual belief are what they were, and nothing was written
        assert question["truth"] == mind["truth"] and question["unchanged"] is True
        assert scope_bytes(device, campaign) == before

        # 060's refusals keep their reasons: evidence Mara does not hold, a player as the owner, a stale revision
        assert refused(app, "world.mind_preview", **ask, hypotheses=[{**first, "evidence": ["oswin-saw"]}]).reason \
            == "unavailable_evidence"
        assert refused(app, "world.mind_preview", **{**ask, "owner": refs["anya"]}, hypotheses=[
            {**first, "evidence": ["mara-glance"]}]).reason == "npc_authority_required"
        assert refused(app, "world.mind_preview", **ask, hypotheses=[first, {**revised, "at": glance,
                                                                        "evidence": ["mara-glance"]}]).reason \
            == "nonmonotonic_revision"
        # the page holds a same-evidence revision one moment after what it replaces, which 060 admits
        same = app.call("world.mind_preview", {**ask, "hypotheses": [first, {**revised, "at": glance + 1,
                                                                          "evidence": ["mara-glance"]}]})
        assert [item["id"] for item in same["current"]] == ["h2"]
        assert refused(app, "world.mind_preview", **ask, hypotheses=[first, {**revised, "about": [refs["mara"]]}]) \
            .reason in ("different_chain", "unresolved_actor")
        assert refused(app, "world.mind_preview", **ask, hypotheses=[{**first, "confidence": 2}]).reason \
            == "invalid_confidence"
        assert refused(app, "world.mind_preview", **ask, hypotheses=[first],
                       tactic={**tactic, "hypothesis": "h9"}).reason == "not_current_hypothesis"
        assert refused(app, "world.mind_preview", **ask, hypotheses=[{**first, "about": [refs["oswin"]]}]).reason \
            == "unresolved_actor"
        assert refused(app, "world.mind", **{**ask, "owner": refs["oswin"]}).reason == "no_actor"
        assert scope_bytes(device, campaign) == before

        # the director rules again, later: the proposition stands as ruled last, and the first ruling is earlier
        coast = Coast(device, chat)
        coast.evidence.append(campaign, [Ruling(campaign, "truth-after", "anya-knows", "established", coast.next(),
                                                "director", grounds=(Subject("event", "asked"),))]).commit()
        (ruled,) = app.call("world.mind", ask)["truth"]["rulings"]
        assert (ruled["standing"], ruled["ruling"]) == ("established", "truth-after")
        assert [(item["ruling"], item["standing"]) for item in ruled["earlier"]] == [("truth", "refuted")]
        assert ruled["at"] > ruled["earlier"][0]["at"]


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_world_page_end_to_end(desk):
    folder, chat = desk
    with Device(folder, [say("Mara reads it.")]) as device:
        refs = seed_world(device, chat)
        app = AppServer(device.api)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            env = {**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token,
                   "GRIMOIRE_CHAT": str(chat), "GRIMOIRE_CAMPAIGN": refs["campaign"], "GRIMOIRE_NPC": refs["mara"],
                   "GRIMOIRE_PC": refs["anya"], "GRIMOIRE_TOWN": str(TOWN), "GRIMOIRE_OTHER": str(OTHER),
                   "GRIMOIRE_PORT": str(PORT)}
            result = subprocess.run([NODE, "--test", "--test-reporter=tap",
                                     str(Path(__file__).with_name("js") / "world.e2e.mjs")],
                                    capture_output=True, text=True, timeout=240, check=False, env=env)
        finally:
            app.close()
            thread.join(10)
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
    assert "# pass 1" in result.stdout
