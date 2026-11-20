"""Sprint 115's Chronicle page and its ``chronicle.*`` adapter ops, through 108's shell contract.

The adapter serves 055's ``CalendarLibrary`` and 053's ``MemoryLibrary`` over the app's open library. The proving
scenario runs as a screen would: the director starts the campaign's clock, schedules a deadline and advances time
past it, each change reviewed first (a plan that writes nothing) and then made exactly as reviewed, once, however
often the commit is retried; a change made meanwhile refuses the reviewed one. The director writes what an NPC
remembers, citing its evidence, and play then packs it; the table writes only its own character's memory, citing
only what that character holds. The page itself runs end to end under Node (``js/chronicle.e2e.mjs``) where Node is
installed. Synthetic library; no model is called.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from store_helpers import OWNER
from textapi_fixtures import Device, desktop_library, keep, say

from grimoire.app import AppServer
from grimoire.app.adapters.chronicle import FEATURES
from grimoire.app.appapi import AppApi
from grimoire.app.pages import load_pages
from grimoire.core import Event, Moment, Perception
from grimoire.core.identity import InstanceRef
from grimoire.evidencestore import EvidenceLibrary
from grimoire.textapi import ApiRefused

STATIC = Path(__file__).resolve().parents[1] / "src" / "grimoire" / "app" / "static"
NODE = shutil.which("node")
DIRECTOR = {"audience": "director"}
MASKED = "The hooded visitor says he is Brother Aldric."
UNMASKED = "The hood falls in the crypt: it is Sister Maren."
COIN = "Anya feels the weight of the coin Mara returns."


@pytest.fixture
def desk(tmp_path):
    return desktop_library(tmp_path / "desk")


def refused(app: AppApi, op: str, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        app.call(op, args)
    return caught.value


def scope_bytes(device, campaign: str) -> bytes:
    return (device.table.store.root / "campaigns" / campaign / "scope.json").read_bytes()


def played(device, chat) -> dict[str, str]:
    """The owner seated as the card chat's persona (Anya), one kept turn, and confirmed evidence for both cast
    members: Mara's two perceptions (the second corrects the first) and Anya's one. The cast's actor refs by name."""
    device.api.runner.seat(chat, OWNER)
    device("post", chat=str(chat), text="*I press a coin into Mara's hand*")
    device("generate", chat=str(chat))
    keep(device, str(chat))
    cast = {member["name"]: member["actor"] for member in
            AppApi(device.api).call("campaign.hub", {"chat": str(chat), **DIRECTOR})["cast"]}
    c = chat.campaign_id
    mara, anya = InstanceRef.parse(cast["Mara Vell"]), InstanceRef.parse(cast["Anya Rook"])
    records = []
    for n, (scene, evidence, actor, text) in enumerate((("chapel", "masked", mara, MASKED),
                                                        ("crypt", "unmasked", mara, UNMASKED),
                                                        ("quay", "coin", anya, COIN)), 1):
        records += [Event(c, f"ev-{evidence}", f"event {n}", Moment(n, scene, "t1"), "director"),
                    Perception(c, evidence, actor, f"ev-{evidence}", "sight", text, Moment(n, scene, "t1"), "director",
                               confirmed=True)]
    EvidenceLibrary(device.table.store, device.table.owner, owner=OWNER).append(c, records).commit()
    return cast


def test_the_page_registers_hidden_on_its_own_route_and_needs_its_ops():
    pages = {page.id: page for page in load_pages(STATIC)}
    page = pages["chronicle"]
    assert (page.route, page.slot, page.owner) == ("chronicle", "hidden", "115")
    assert set(page.requires) == {"campaign.chats", "campaign.hub", "chronicle.clock", "chronicle.memory",
                                  "chronicle.coverage"}
    # every other screen the page links to for a Wave 8 feature is a page this app registers
    routes = {item.route for item in pages.values()}
    assert {item["route"] for item in FEATURES if item["status"] == "screen"} <= routes


def test_the_clock_is_the_directors_and_each_change_is_made_once_as_reviewed(desk):
    folder, chat = desk
    campaign = chat.campaign_id
    with Device(folder) as device:
        app = AppApi(device.api)
        assert refused(app, "chronicle.clock", campaign=campaign).reason == "director_only"
        clock = app.call("chronicle.clock", {"campaign": campaign, **DIRECTOR})
        assert clock["started"] is False and clock["now"] is None
        before = scope_bytes(device, campaign)

        # a plan writes nothing, and says what the commit will do
        start = {"campaign": campaign, **DIRECTOR, "operation": "initialize", "operation_id": "start",
                 "expected": clock["etag"], "provider": "gregorian", "date": "1024-3-1", "minute": 600}
        plan = app.call("chronicle.clock_plan", start)["plan"]
        assert plan["before"] is None and plan["after"]["date"] == "1024-03-01" and plan["after"]["time"] == "10:00"
        assert plan["files"] == [f"campaigns/{campaign}/scope.json"] and scope_bytes(device, campaign) == before
        assert refused(app, "chronicle.clock_plan", **{**start, "audience": "table"}).reason == "director_only"
        assert refused(app, "chronicle.clock_commit", **{**start, "expected": None}).reason == "review_required"

        done = app.call("chronicle.clock_commit", start)
        assert done["done"]["already_applied"] is False and done["clock"]["now"]["date"] == "1024-03-01"
        started = scope_bytes(device, campaign)
        # a commit retried after a lost answer finds its own receipt and writes nothing again
        again = app.call("chronicle.clock_commit", start)
        assert again["done"]["already_applied"] is True and scope_bytes(device, campaign) == started
        assert again["clock"] == done["clock"]

        etag = done["clock"]["etag"]
        deadline = {"campaign": campaign, **DIRECTOR, "operation": "schedule", "operation_id": "sail",
                    "expected": etag, "deadline": "ship-sails", "label": "The ship sails", "date": "1024-03-05"}
        assert refused(app, "chronicle.clock_commit", **{**deadline, "date": "1024-02-01"}).reason == "past_deadline"
        etag = app.call("chronicle.clock_commit", deadline)["clock"]["etag"]

        # reviewed, then overtaken: the reviewed change is refused and nothing it planned is written
        five = {"campaign": campaign, **DIRECTOR, "operation": "advance", "operation_id": "five-days",
                "expected": etag, "minutes": 5 * 1440}
        reviewed = app.call("chronicle.clock_plan", five)["plan"]
        assert reviewed["flags"] == [{"deadline": "ship-sails", "label": "The ship sails"}]
        meanwhile = app.call("chronicle.clock_commit", {**five, "operation_id": "an-hour", "minutes": 60})
        overtaken = scope_bytes(device, campaign)
        assert refused(app, "chronicle.clock_commit", **five).reason == "changed"
        assert scope_bytes(device, campaign) == overtaken

        # reviewed again, at the clock as it now is, it flags the deadline once
        final = app.call("chronicle.clock_commit", {**five, "expected": meanwhile["clock"]["etag"]})
        assert final["done"]["before"]["time"] == "11:00" and final["clock"]["now"]["date"] == "1024-03-06"
        (sails,) = final["clock"]["deadlines"]
        assert sails["flagged_by"] == "five-days" and sails["due"]["date"] == "1024-03-05"
        later = app.call("chronicle.clock_commit", {**five, "operation_id": "later", "minutes": 1440,
                                                    "expected": final["clock"]["etag"]})
        assert later["done"]["flags"] == [] and later["clock"]["deadlines"] == final["clock"]["deadlines"]
        # the same id for another request is refused, and nothing is advanced
        assert refused(app, "chronicle.clock_commit", **{**five, "minutes": 7,
                                                         "expected": later["clock"]["etag"]}).reason == "operation_mismatch"
        assert app.call("chronicle.clock", {"campaign": campaign, **DIRECTOR})["now"] == later["clock"]["now"]


def test_clock_requests_the_domain_would_not_take_are_refused_before_anything_is_written(desk):
    folder, chat = desk
    campaign = chat.campaign_id
    with Device(folder) as device:
        app = AppApi(device.api)
        etag = app.call("chronicle.clock", {"campaign": campaign, **DIRECTOR})["etag"]
        before = scope_bytes(device, campaign)
        base = {"campaign": campaign, **DIRECTOR, "operation_id": "op", "expected": etag}
        assert refused(app, "chronicle.clock_plan", **base, operation="advance", minutes=60).reason == "uninitialized"
        assert refused(app, "chronicle.clock_plan", **base, operation="initialize", provider="custom",
                       date="1-First-1").reason == "invalid_field"
        assert refused(app, "chronicle.clock_plan", **base, operation="initialize", date="1024-03-01",
                       minute=1440).reason == "invalid_field"
        assert refused(app, "chronicle.clock_plan", **base, operation="initialize", date="soon").reason == "invalid_field"
        assert refused(app, "chronicle.clock_plan", **{**base, "operation_id": "Not An Id"}, operation="initialize",
                       date="1024-03-01").reason == "invalid_field"
        assert refused(app, "chronicle.clock", campaign="../elsewhere", **DIRECTOR).reason == "invalid_field"
        assert scope_bytes(device, campaign) == before


def test_an_npcs_memory_is_the_directors_and_play_packs_what_the_director_wrote(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        cast = played(device, chat)
        app = AppApi(device.api)
        mara = cast["Mara Vell"]
        ask = {"chat": str(chat), "actor": mara}
        assert refused(app, "chronicle.memory", **ask).reason == "director_only"
        assert refused(app, "chronicle.remember", **ask, operation_id="a", topic="visitor", text="x",
                       evidence=["masked"]).reason == "director_only"

        memory = app.call("chronicle.memory", {**ask, **DIRECTOR})
        assert (memory["whose"], memory["editor"], memory["topics"]) == ("npc", "director", [])
        assert [item["evidence"] for item in memory["citable"]] == ["unmasked", "masked"]  # newest first
        assert "coin" not in {item["evidence"] for item in memory["citable"]}  # Anya's, not Mara's

        recap = {**ask, **DIRECTOR, "operation_id": "recap", "topic": "visitor-identity",
                 "text": "The hooded visitor is Brother Aldric.", "evidence": ["masked"]}
        first = app.call("chronicle.remember", recap)["memory"]
        (topic,) = first["topics"]
        assert [(item["account"], item["status"]) for item in topic["current"]] == [("recap", "current")]
        assert topic["current"][0]["evidence"][0]["content"] == MASKED
        # an exact retry is no new account; the same id for other words is refused
        assert app.call("chronicle.remember", recap)["memory"] == first
        assert refused(app, "chronicle.remember", **{**recap, "text": "Someone else."}).reason == "operation_conflict"
        # another character's evidence, or evidence that does not exist, is never cited
        assert refused(app, "chronicle.remember", **{**recap, "operation_id": "x", "evidence": ["coin"]}).reason \
            == "unavailable_evidence"

        corrected = app.call("chronicle.remember", {**recap, "operation_id": "corrected", "supersedes": "recap",
                                                    "text": "The visitor was Sister Maren in disguise.",
                                                    "evidence": ["unmasked"]})["memory"]
        (topic,) = corrected["topics"]
        assert [item["account"] for item in topic["current"]] == ["corrected"]
        assert [(item["account"], item["status"]) for item in topic["history"]] == [("recap", "superseded")]

        lines = [item["line"] for item in device.api.runner.inspect_context(chat, InstanceRef.parse(mara))["items"]]
        assert "You remember: The visitor was Sister Maren in disguise." in lines
        assert not any("Brother Aldric" in line and line.startswith("You remember") for line in lines)


def test_the_table_keeps_only_its_own_characters_memory(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        cast = played(device, chat)
        app = AppApi(device.api)
        anya = {"chat": str(chat), "actor": cast["Anya Rook"]}
        memory = app.call("chronicle.memory", anya)
        assert (memory["whose"], memory["editor"]) == ("player", "you")
        assert [item["evidence"] for item in memory["citable"]] == ["coin"]
        assert refused(app, "chronicle.remember", **anya, operation_id="spy", topic="visitor", text="I know who it is.",
                       evidence=["unmasked"]).reason == "unavailable_evidence"
        done = app.call("chronicle.remember", {**anya, "operation_id": "coin", "topic": "the-coin",
                                               "text": "Mara gave the coin back.", "evidence": ["coin"]})
        assert [item["text"] for item in done["memory"]["topics"][0]["current"]] == ["Mara gave the coin back."]
        # the director's view of the same character shows the same player's accounts: control decides, not the view
        assert app.call("chronicle.memory", {**anya, **DIRECTOR})["topics"] == done["memory"]["topics"]
        # and the NPC's memory, written by the director, holds none of it
        npc = app.call("chronicle.memory", {"chat": str(chat), "actor": cast["Mara Vell"], **DIRECTOR})
        assert npc["topics"] == []
        assert refused(app, "chronicle.memory", chat=str(chat), actor="campaign:elsewhere/character/x").reason \
            == "invalid_field"


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_chronicle_page_end_to_end(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        cast = played(device, chat)
        app = AppServer(device.api)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            env = {**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token,
                   "GRIMOIRE_CHAT": str(chat), "GRIMOIRE_CAMPAIGN": chat.campaign_id, "GRIMOIRE_NPC": cast["Mara Vell"]}
            result = subprocess.run([NODE, "--test", "--test-reporter=tap",
                                     str(Path(__file__).with_name("js") / "chronicle.e2e.mjs")],
                                    capture_output=True, text=True, timeout=180, check=False, env=env)
        finally:
            app.close()
            thread.join(10)
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
    assert "# pass 1" in result.stdout
