"""Sprint 116's Rules page and its ``rules.*`` adapter ops, through 108's shell contract.

The adapter serves 086's ``MechanicsStore`` and pure mechanics, and 075's ``RollLibrary``, over the app's open
library. The proving scenario runs as the screen does: an author starts a module from the d20 template, saves a
mistake (a draft keeps any text), sees the problem by its path with a did-you-mean hint, fixes it and publishes it,
each write reviewed first and made as reviewed; then fills a sheet and grades a check from entered dice under the
published revision, never saved. On a campaign, the player rolls a Stealth check for their own character backed by
the action they typed: the review draws nothing, the roll fails on a 4, and asking again reads the same failure back
with no new die. The director rolls for an NPC under ``director:<request id>``; the table can neither roll for an NPC
nor see the director's rolls. The draw is the test's (``rules.configure``), never the request's. The page runs end
to end under Node (``js/rules.e2e.mjs``). Synthetic library; no model is called.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from prep_app_fixtures import DIRECTOR, cast_of
from store_helpers import OWNER
from textapi_fixtures import Device, desktop_library, keep, say

from grimoire.app import AppServer
from grimoire.app.adapters import rules
from grimoire.app.appapi import AppApi
from grimoire.app.pages import load_pages
from grimoire.mechanicsstore import MechanicsStore
from grimoire.rollstore import RollLibrary
from grimoire.testing.fileops import snapshot as files_of
from grimoire.textapi import ApiRefused

STATIC = Path(__file__).resolve().parents[1] / "src" / "grimoire" / "app" / "static"
NODE = shutil.which("node")
STEALTH = {"ability": "dexterity", "skill": "stealth", "dc": 15, "modifier": 3}
SLIP = "I slip past the guard while he yawns."


class Dice:
    """A scripted draw that records every die it was asked for."""

    def __init__(self, *faces: int) -> None:
        self.faces, self.asked = list(faces), []

    def __call__(self, sides: int) -> int:
        self.asked.append(sides)
        return self.faces.pop(0)


@pytest.fixture
def desk(tmp_path):
    return desktop_library(tmp_path / "desk")


def refused(app: AppApi, op: str, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        app.call(op, args)
    return caught.value


def seated(device, chat) -> dict[str, str]:
    device.api.runner.seat(chat, OWNER)
    device("post", chat=str(chat), text="*I press a coin into Mara's hand*")
    device("generate", chat=str(chat))
    keep(device, str(chat))
    return cast_of(device, chat)


def test_the_page_registers_in_the_library_and_needs_only_ops_the_app_serves():
    pages = {page.id: page for page in load_pages(STATIC)}
    page = pages["rules"]
    assert (page.route, page.slot, page.owner) == ("rules", "library", "116")
    served = {f"rules.{op}" for op in rules.ADAPTER.ops} | {"campaign.chats"}
    assert set(page.requires) <= served and "rules.roll" in page.requires and "rules.publish" in page.requires


# -- 086: authoring --------------------------------------------------------------------------------------------
def test_a_module_is_authored_checked_by_path_and_published_only_as_reviewed(desk):
    folder, _ = desk
    with Device(folder) as device:
        app = AppApi(device.api)
        root = device.table.store.root
        info = app.call("rules.authoring", {})
        assert info["listed"] == {"drafts": False, "modules": False, "revisions": True}
        assert {item["template"] for item in info["templates"]} == {"d20_basic", "pool_basic", "blank"}
        assert app.call("rules.module", {"module": "duel"})["draft"] is None

        # starting from the d20 template: the plan says what it writes, and writes nothing
        text = app.call("rules.template", {"module": "duel", "template": "d20_basic"})["text"]
        assert json.loads(text)["id"] == "duel"
        before = files_of(root)
        plan = app.call("rules.save_plan", {"module": "duel", "text": text, "expected": None})
        assert plan["plan"]["file"] == "config/mechanics/drafts/duel.json" and plan["plan"]["creates"] is True
        assert plan["check"]["problems"] == [] and files_of(root) == before
        started = app.call("rules.save", {"module": "duel", "text": text, "expected": None})
        assert started["done"]["already"] is False and started["draft"]["text"] == text
        # a start retried after a lost answer finds its own text and writes nothing; another start is refused
        written = files_of(root)
        assert app.call("rules.save", {"module": "duel", "text": text, "expected": None})["done"]["already"] is True
        assert files_of(root) == written
        assert refused(app, "rules.save", module="duel", text="{}", expected=None).reason == "draft_exists"

        # a mistake is saved as it is (a draft keeps any text), and checked by its path with a hint
        etag = started["draft"]["etag"]
        typo = text.replace("natural + stealth + dex_mod", "natural + stelth + dex_mod")
        saved = app.call("rules.save", {"module": "duel", "text": typo, "expected": etag})
        (problem,) = app.call("rules.check", {"module": "duel"})["problems"]
        assert problem == {"path": "checks.stealth.total", "hint": "stealth",
                           "message": "unknown name 'stelth' (did you mean 'stealth'?)"}
        assert refused(app, "rules.publish_plan", module="duel", expected=saved["draft"]["etag"]).reason == "invalid_module"
        assert refused(app, "rules.publish", module="duel", expected=saved["draft"]["etag"],
                       revision="sha256:" + "0" * 64).reason == "invalid_module"
        assert files_of(root)["config/mechanics/drafts/duel.json"] == typo.encode()
        assert not any(path.startswith("config/mechanics/modules/") for path in files_of(root))
        # unfinished JSON is kept too, and checking the text as typed writes nothing
        half = app.call("rules.check", {"module": "duel", "text": '{"format": '})
        assert half["of"] == "text" and half["problems"][0]["path"] == "$" and half["publishable"] is False

        # fixed, reviewed and published: the plan names the revision and its file, and writes nothing
        fixed = app.call("rules.save", {"module": "duel", "text": text, "expected": saved["draft"]["etag"]})
        etag = fixed["draft"]["etag"]
        before = files_of(root)
        review = app.call("rules.publish_plan", {"module": "duel", "expected": etag})["plan"]
        revision = review["revision"]
        assert review["file"] == f"config/mechanics/modules/duel/{revision[7:]}.json" and review["already"] is False
        assert files_of(root) == before
        assert refused(app, "rules.publish", module="duel", expected=etag).reason == "review_required"
        assert refused(app, "rules.publish", module="duel", expected=etag, revision="sha256:" + "1" * 64).reason \
            == "changed"
        done = app.call("rules.publish", {"module": "duel", "expected": etag, "revision": revision})
        assert done["done"] == {"module": "duel", "revision": revision, "already": False, "file": review["file"]}
        assert [item["revision"] for item in done["revisions"]] == [revision]
        published = files_of(root)
        again = app.call("rules.publish", {"module": "duel", "expected": etag, "revision": revision})
        assert again["done"]["already"] is True and files_of(root) == published

        # a save reviewed, then overtaken by another save: the reviewed one is refused and nothing it planned is
        # written; a publish of the overtaken draft is refused the same way
        edited = text.replace('"version": "0.1"', '"version": "0.2"')
        app.call("rules.save_plan", {"module": "duel", "text": edited, "expected": etag})
        meanwhile = app.call("rules.save", {"module": "duel", "text": text + "\n", "expected": etag})
        overtaken = files_of(root)
        assert refused(app, "rules.save", module="duel", text=edited, expected=etag).reason == "changed"
        assert refused(app, "rules.save_plan", module="duel", text=edited, expected=etag).reason == "changed"
        assert refused(app, "rules.publish_plan", module="duel", expected=etag).reason == "changed"
        assert refused(app, "rules.publish", module="duel", expected=etag, revision=revision).reason == "changed"
        assert files_of(root) == overtaken
        # the same content laid out differently is the same revision: publishing it writes nothing
        layout = app.call("rules.publish_plan", {"module": "duel", "expected": meanwhile["draft"]["etag"]})["plan"]
        assert layout["revision"] == revision and layout["already"] is True

        assert refused(app, "rules.module", module="Not An Id").reason == "invalid_id"
        assert refused(app, "rules.template", module="duel", template="mystery").reason == "invalid_field"
        assert refused(app, "rules.save", module="duel", text="x").reason == "review_required"
        assert refused(app, "rules.check", module="nothing_here").reason == "no_draft"


def test_a_published_revision_is_read_only_and_trying_it_saves_nothing(desk):
    folder, _ = desk
    with Device(folder) as device:
        app = AppApi(device.api)
        root = device.table.store.root
        text = app.call("rules.template", {"module": "duel", "template": "d20_basic"})["text"]
        etag = app.call("rules.save", {"module": "duel", "text": text, "expected": None})["draft"]["etag"]
        revision = app.call("rules.publish_plan", {"module": "duel", "expected": etag})["plan"]["revision"]
        app.call("rules.publish", {"module": "duel", "expected": etag, "revision": revision})
        shown = app.call("rules.revision", {"module": "duel", "revision": revision})
        assert json.loads(shown["text"])["id"] == "duel" and shown["view"]["revision"] == revision
        assert {sheet["id"] for sheet in shown["view"]["sheets"]} == {"warrior", "adept"}
        stealth = next(item for item in shown["view"]["checks"] if item["id"] == "stealth")
        assert stealth["dice"] == {"count": "1", "sides": 20, "target": None} and stealth["difficulty"] == 12

        before = files_of(root)
        ask = {"module": "duel", "revision": revision, "sheet_type": "warrior"}
        wrong = app.call("rules.sheet", {**ask, "values": {"dexterity": 40, "luck": 3}})
        assert wrong["valid"] is False and wrong["saved"] is False
        assert {item["path"] for item in wrong["problems"]} == {"values.dexterity", "values.luck"}
        sheet = app.call("rules.sheet", {**ask, "values": {"dexterity": 14, "stealth": 3}})
        assert sheet["valid"] is True and sheet["derived"]["dex_mod"] == 2 and sheet["values"]["hp"] == 12
        graded = app.call("rules.grade", {**ask, "values": {"dexterity": 14, "stealth": 3}, "check": "stealth",
                                          "dice": [9], "modifier": 0})
        assert graded["record"] == {"check": "stealth", "dice": [9], "modifier": 0, "difficulty": 12, "total": 14,
                                    "successes": None, "outcome": "success"}
        assert graded["confirmed"] is True and graded["saved"] is False and "Not saved" in graded["not_saved"]
        assert app.call("rules.grade", {**ask, "values": {}, "check": "stealth", "dice": [20], "modifier": 0,
                                        "difficulty": 30})["record"]["outcome"] == "critical success"
        assert refused(app, "rules.grade", **ask, values={}, check="stealth", dice=[9, 9], modifier=0).reason \
            == "check_refused"
        assert refused(app, "rules.grade", **ask, values={}, check="arcana", dice=[9], modifier=0).reason \
            == "check_refused"  # an adept's check on a warrior's sheet
        assert refused(app, "rules.grade", **ask, values={"dexterity": 99}, check="stealth", dice=[9],
                       modifier=0).reason == "invalid_sheet"
        assert files_of(root) == before  # nothing filled or graded is kept

        assert refused(app, "rules.revision", module="duel", revision="sha256:" + "2" * 64).reason == "unpublished"
        assert refused(app, "rules.revision", module="duel", revision="latest").reason == "invalid_revision"
        # a revision file changed by hand is refused, never served under its name
        path = root / MechanicsStore.revision_path("duel", revision)
        path.write_text(path.read_text().replace('"Basic d20"', '"Edited d20"'))
        assert refused(app, "rules.revision", module="duel", revision=revision).reason == "altered"


# -- 075: recorded rolls ---------------------------------------------------------------------------------------
def _ask(chat, actor: str, **extra) -> dict:
    return {"campaign": chat.campaign_id, "chat": str(chat), "actor": actor, "family": "d20",
            "request_id": "sneak-1", "action": STEALTH, **extra}


def test_a_players_roll_is_backed_by_their_words_reviewed_drawn_once_and_read_back(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        dice = Dice(4, 20, 11)
        rules.configure(device.api, dice)
        cast = seated(device, chat)
        app = AppApi(device.api)
        root = device.table.store.root
        anya = cast["Anya Rook"]
        listed = app.call("rules.rolls", {"campaign": chat.campaign_id, "chat": str(chat)})
        assert listed["rolls"] == [] and [item["actor"] for item in listed["actors"]] == [anya]
        assert listed["pinned"] is None and "reference profile" in listed["module_note"]
        assert [item["family"] for item in listed["profiles"]] == ["d20", "wod", "pbta"]

        ask = _ask(chat, anya, text=SLIP)
        before = files_of(root)
        plan = app.call("rules.roll_plan", ask)["plan"]
        assert (plan["attempt"], plan["dice"], plan["already"]) == ("sneak-1", [20], None)
        assert plan["module"] == "reference:062/ruleset/d20@5e-2024"
        assert plan["backing"] == {"text": SLIP, "turn": plan["turn"]} and plan["turn"]
        assert plan["files"] == [f"authority/rolls/{chat.campaign_id}/"]
        assert files_of(root) == before and dice.asked == []  # the review draws nothing and writes nothing

        reviewed = {**ask, "turn": plan["turn"], "review": plan["review"]}
        assert refused(app, "rules.roll", **{**reviewed, "review": None}).reason == "review_required"
        assert refused(app, "rules.roll", **{**reviewed, "text": "I charge the guard."}).reason == "changed"
        assert files_of(root) == before and dice.asked == []
        # the request names no draw: a face it offers is no part of the roll
        done = app.call("rules.roll", {**reviewed, "faces": [20], "draw": 20})
        roll = done["roll"]
        assert done["done"] == {"attempt": "sneak-1", "drawn": True} and dice.asked == [20]
        assert (roll["faces"], roll["result"], roll["outcome"]) == ([4], {"die": 4, "total": 7, "success": False},
                                                                     "failure")
        assert roll["resolution"] == {"campaign": chat.campaign_id, "attempt": "sneak-1", "result": "failure"}
        assert roll["backing"]["text"] == SLIP and roll["whose"] == "you"

        # asking again reads the same failure back and draws no die, even though the next face is a 20
        recorded = files_of(root)
        again = app.call("rules.roll", reviewed)
        assert again["done"]["drawn"] is False and again["roll"] == roll and dice.asked == [20]
        assert files_of(root) == recorded
        assert app.call("rules.roll_plan", ask)["plan"]["already"] == roll
        # any other request under the same attempt is refused, at review and at the roll
        easier = _ask(chat, anya, text=SLIP, action={**STEALTH, "dc": 5})
        assert refused(app, "rules.roll_plan", **easier).reason == "already_rolled"
        assert refused(app, "rules.roll_plan", **{**ask, "text": "I charge."}).reason == "already_rolled"
        assert files_of(root) == recorded and dice.asked == [20]
        # the roll is on record: a fresh library over the same authority reads the same failure
        state = RollLibrary(device.table.store, device.api.authority, device.api.owner_handle).state(chat.campaign_id)
        assert state.rolls["sneak-1"].outcome == "failure"
        assert app.call("rules.rolls", {"campaign": chat.campaign_id})["rolls"] == [{**roll, "name": None}]


def test_an_npcs_roll_is_the_directors_and_the_table_never_sees_or_makes_one(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        dice = Dice(4, 11)
        rules.configure(device.api, dice)
        cast = seated(device, chat)
        app = AppApi(device.api)
        root = device.table.store.root
        anya, mara = cast["Anya Rook"], cast["Mara Vell"]
        pc = app.call("rules.roll_plan", _ask(chat, anya, text=SLIP))["plan"]
        app.call("rules.roll", {**_ask(chat, anya, text=SLIP), "turn": pc["turn"], "review": pc["review"]})

        # the table: no NPC to roll for, refused if asked, and the director's rolls never listed
        before = files_of(root)
        npc = _ask(chat, mara, request_id="notice", action={**STEALTH, "ability": "wisdom", "skill": "perception"})
        assert refused(app, "rules.roll_plan", **npc).reason == "director_only"
        assert refused(app, "rules.roll", **npc, review="sha256:" + "0" * 64).reason == "director_only"
        assert files_of(root) == before
        table = app.call("rules.rolls", {"campaign": chat.campaign_id, "chat": str(chat)})
        assert [item["actor"] for item in table["actors"]] == [anya]

        plan = app.call("rules.roll_plan", {**npc, **DIRECTOR})["plan"]
        assert (plan["attempt"], plan["backing"], plan["turn"]) == ("director:notice", None, None)
        assert refused(app, "rules.roll_plan", **{**npc, **DIRECTOR, "text": "words"}).reason == "invalid_field"
        rolled = app.call("rules.roll", {**npc, **DIRECTOR, "review": plan["review"]})["roll"]
        assert (rolled["attempt"], rolled["faces"], rolled["outcome"], rolled["whose"]) == (
            "director:notice", [11], "failure", "director")
        assert rolled["resolution"]["attempt"] == "director:notice"  # it never binds a player's attempt

        director = app.call("rules.rolls", {"campaign": chat.campaign_id, "chat": str(chat), **DIRECTOR})
        assert [item["attempt"] for item in director["rolls"]] == ["sneak-1", "director:notice"]
        assert {item["actor"] for item in director["actors"]} == {anya, mara}
        table = app.call("rules.rolls", {"campaign": chat.campaign_id, "chat": str(chat)})
        assert [item["attempt"] for item in table["rolls"]] == ["sneak-1"]
        assert "Mara" not in json.dumps(table["rolls"]) and mara not in json.dumps(table)


def test_rolls_the_domain_or_the_page_would_not_take_are_refused_before_a_die_is_drawn(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        dice = Dice(4)
        rules.configure(device.api, dice)
        with pytest.raises(ValueError):
            rules.configure(device.api, Dice(20))  # the draw is set once, by whoever builds the app
        cast = seated(device, chat)
        app = AppApi(device.api)
        root = device.table.store.root
        anya = cast["Anya Rook"]
        before = files_of(root)
        assert refused(app, "rules.roll_plan", **_ask(chat, anya)).reason == "invalid_field"  # no words of the player
        assert refused(app, "rules.roll_plan", **_ask(chat, anya, text=SLIP, family="ars")).reason == "no_roll"
        assert refused(app, "rules.roll_plan", **_ask(chat, anya, text=SLIP, family="gurps")).reason == "invalid_field"
        assert refused(app, "rules.roll_plan", **_ask(chat, anya, text=SLIP,
                                                      action={**STEALTH, "ability": "luck"})).reason == "invalid_action"
        assert refused(app, "rules.roll_plan", **_ask(chat, anya, text=SLIP, action={"dc": 15})).reason \
            == "invalid_field"
        assert refused(app, "rules.roll_plan", **_ask(chat, anya, text=SLIP, request_id="Not An Id")).reason \
            == "invalid_field"
        stranger = f"campaign:{chat.campaign_id}/character/nobody-here"
        assert refused(app, "rules.roll_plan", **_ask(chat, stranger, text=SLIP)).reason == "no_actor"
        assert refused(app, "rules.roll_plan", **_ask(chat, "campaign:elsewhere/character/x", text=SLIP)).reason \
            == "invalid_field"
        plan = app.call("rules.roll_plan", _ask(chat, anya, text=SLIP))["plan"]
        assert refused(app, "rules.roll", **_ask(chat, anya, text=SLIP), review=plan["review"]).reason \
            == "review_required"  # the turn the action was given in, as reviewed
        assert refused(app, "rules.roll", **_ask(chat, anya, text=SLIP), turn="t-elsewhere",
                       review=plan["review"]).reason == "invalid_field"
        assert refused(app, "rules.rolls", campaign="../x").reason == "invalid_field"
        assert files_of(root) == before and dice.asked == []


def test_a_review_shows_the_actors_earlier_attempts_in_the_turn_and_adds_no_limit(desk):
    """075 does not limit attempts in a turn (ROLLS.md): the review lists the earlier ones instead."""
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        dice = Dice(4, 17)
        rules.configure(device.api, dice)
        cast = seated(device, chat)
        app = AppApi(device.api)
        anya = cast["Anya Rook"]
        first = _ask(chat, anya, text=SLIP)
        plan = app.call("rules.roll_plan", first)["plan"]
        assert plan["earlier"] == []
        rolled = app.call("rules.roll", {**first, "turn": plan["turn"], "review": plan["review"]})["roll"]
        second = _ask(chat, anya, text="I try again, slower.", request_id="sneak-2")
        again = app.call("rules.roll_plan", second)["plan"]
        assert [item["attempt"] for item in again["earlier"]] == ["sneak-1"] and again["earlier"][0] == rolled
        assert again["already"] is None  # a second attempt is still the caller's to make
        made = app.call("rules.roll", {**second, "turn": again["turn"], "review": again["review"]})["roll"]
        assert made["faces"] == [17] and dice.asked == [20, 20]
        # the same attempt read back lists the other one, never itself
        assert [item["attempt"] for item in app.call("rules.roll_plan", first)["plan"]["earlier"]] == ["sneak-2"]


LONE = "\ud800"  # a lone surrogate: no UTF-8 encodes it


def test_text_that_cannot_be_stored_and_malformed_args_are_refused_not_server_errors(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        dice = Dice()
        rules.configure(device.api, dice)
        cast = seated(device, chat)
        app = AppApi(device.api)
        root = device.table.store.root
        anya = cast["Anya Rook"]
        before = files_of(root)
        assert refused(app, "rules.roll_plan", **_ask(chat, anya, text="I sneak " + LONE)).reason == "invalid_field"
        sneaky = {**STEALTH, "skill": "st" + LONE}
        assert refused(app, "rules.roll_plan", **_ask(chat, anya, text=SLIP, action=sneaky)).reason == "invalid_field"
        pbta = {"trigger": "Anya crosses " + LONE, "stat": "dexterity", "modifier": 1}
        assert refused(app, "rules.roll_plan", **_ask(chat, anya, text=SLIP, family="pbta", action=pbta)).reason \
            == "invalid_field"
        assert refused(app, "rules.roll_plan", **_ask(chat, anya, text=SLIP, turn="t" + LONE)).reason == "invalid_field"
        for bad in (["d20_basic"], {"x": 1}, 3, None):
            assert refused(app, "rules.template", module="duel", template=bad).reason == "invalid_field"
        assert refused(app, "rules.check", module="duel", text="x" + LONE).reason == "invalid_field"
        assert refused(app, "rules.save_plan", module="duel", text="x" + LONE, expected=None).reason == "invalid_field"
        assert files_of(root) == before
        start = app.call("rules.template", {"module": "duel", "template": "d20_basic"})["text"]
        app.call("rules.save", {"module": "duel", "text": start, "expected": None})
        etag = app.call("rules.module", {"module": "duel"})["draft"]["etag"]
        revision = app.call("rules.publish_plan", {"module": "duel", "expected": etag})["plan"]["revision"]
        app.call("rules.publish", {"module": "duel", "expected": etag, "revision": revision})
        published = files_of(root)
        tried = {"module": "duel", "revision": revision, "sheet_type": "warrior"}
        assert refused(app, "rules.sheet", **tried, values={"gear": ["rope " + LONE]}).reason == "invalid_field"
        assert refused(app, "rules.sheet", **{**tried, "sheet_type": "w" + LONE}, values={}).reason == "invalid_field"
        assert refused(app, "rules.grade", **tried, values={}, check="st" + LONE, dice=[9], modifier=0).reason \
            == "invalid_field"
        assert files_of(root) == published and dice.asked == []


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_rules_page_end_to_end(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it.")]) as device:
        dice = Dice(4, 18)
        rules.configure(device.api, dice)
        cast = seated(device, chat)
        app = AppServer(device.api)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            env = {**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token,
                   "GRIMOIRE_CHAT": str(chat), "GRIMOIRE_CAMPAIGN": chat.campaign_id, "GRIMOIRE_NPC": cast["Mara Vell"]}
            result = subprocess.run([NODE, "--test", "--test-reporter=tap",
                                     str(Path(__file__).with_name("js") / "rules.e2e.mjs")],
                                    capture_output=True, text=True, timeout=240, check=False, env=env)
        finally:
            app.close()
            thread.join(10)
        assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
        assert "# pass 1" in result.stdout
        # the player's roll drew one die (the 4), asking again drew none, and the director's NPC roll drew the next
        assert dice.asked == [20, 20] and dice.faces == []
        state = RollLibrary(device.table.store, device.api.authority, device.api.owner_handle).state(chat.campaign_id)
        assert sorted(roll.outcome for roll in state.rolls.values()) == ["failure", "success"]
