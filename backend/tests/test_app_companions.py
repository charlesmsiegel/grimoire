"""Sprint 115's captured companions (050), through the ``companions.*`` adapter ops and the Companions page.

The card chat's campaign holds two individualized goblins of one species (Snik and Grub), Mara Vell (an NPC, the
director's) with three ordinary balls and Tamsin Hale (Bob's character) with one. The director reviews a capture
(``companions.capture_plan`` writes nothing) and makes it as reviewed (``companions.capture_commit``): the
``trained_by`` link, the party or reserve membership and one ball spent, once, however often the commit is retried;
the same attempt id for another capture is refused; a change in between refuses the reviewed one ``changed``. A
character a person plays takes that person's own key, from their post in the turn. Everything is the director's.
The page runs end to end under Node (``js/companions.e2e.mjs``). Synthetic library; no model is called.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from chat_fixtures import Clock, Ids
from store_helpers import OWNER
from table_fixtures import BALL, BOB, DIRECTOR_VIEW, balls, node_e2e, played

from grimoire.app.pages import load_pages
from grimoire.core.identity import InstanceRef
from grimoire.core.knowledge import Moment
from grimoire.partystore import PartyLibrary
from grimoire.textapi import ApiRefused

NODE = shutil.which("node")
STATIC = Path(__file__).resolve().parents[1] / "src" / "grimoire" / "app" / "static"


def refused(table, op: str, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        table(op, **args)
    return caught.value


def roster(table) -> dict:
    return table("companions.roster", chat=str(table.chat), **DIRECTOR_VIEW)


def capture(table, attempt: str, trainer: str, creature, turn: str, **more) -> dict:
    return {"chat": str(table.chat), **DIRECTOR_VIEW, "attempt_id": attempt, "trainer": table.cast[trainer],
            "creature": str(creature), "outcome": "success", "device": BALL, "capacity": 1, "placement": "auto",
            "turn": turn, **more}


def reviewed(args: dict, plan: dict, **more) -> dict:
    return {**args, "sequence": plan["sequence"], "expected": plan["etag"], **more}


def shared_turn(table) -> str:
    """One turn with Anya's post (the owner's) and Tamsin's (Bob's): a player's key rests on their own post."""
    chat, anya, tamsin = str(table.chat), table.cast["Anya Rook"], table.cast["Tamsin Hale"]
    table("draft", chat=chat, actor=anya, text='"Look, a goblin."')
    table("draft", chat=chat, actor=tamsin, by=BOB, text="*I throw a ball at the goblin*")
    return table("submit", chat=chat)["turns"][-1]["turn"]


def test_the_page_registers_hidden_on_its_own_route_and_needs_its_ops():
    page = {item.id: item for item in load_pages(STATIC)}["companions"]
    assert (page.route, page.slot, page.owner) == ("companions", "hidden", "115")
    assert set(page.requires) == {"companions.roster", "companions.capture_plan", "companions.capture_commit"}


def test_the_roster_is_the_directors_and_names_each_creature_and_its_species(tmp_path):
    with played(tmp_path, creatures=True) as table:
        chat = str(table.chat)
        for op in ("companions.roster", "companions.capture_plan", "companions.capture_commit"):
            assert refused(table, op, chat=chat).reason == "director_only"
        found = roster(table)
        assert found["trainers"] == [] and found["problems"] == []
        assert [(item["name"], item["species"]) for item in found["creatures"]] == [("Snik", "Goblin"),
                                                                                    ("Grub", "Goblin")]
        assert {item["name"] for item in found["people"]} == {"Anya Rook", "Mara Vell", "Tamsin Hale"}
        assert found["holdings"][table.cast["Mara Vell"]] == {BALL: 3}
        assert found["players"] == [OWNER, BOB] and found["turns"][0]["accepted"] is False


def test_a_capture_is_reviewed_made_once_and_its_ball_is_spent_once(tmp_path):
    with played(tmp_path, creatures=True) as table:
        turn = roster(table)["turns"][-1]["turn"]
        args = capture(table, "catch-snik", "Mara Vell", table.snik, turn)
        plan = table("companions.capture_plan", **args)["plan"]
        assert (plan["placement"], plan["spends"], plan["writes"], plan["already_applied"]) == ("party", 1, True, False)
        assert plan["creature"]["name"] == "Snik" and plan["creature"]["species"] == "Goblin" and plan["keys"] == []
        assert balls(table, "Mara Vell") == 3 and roster(table)["trainers"] == [], "a review writes nothing"
        assert refused(table, "companions.capture_commit", **args).reason == "review_required"

        done = table("companions.capture_commit", **reviewed(args, plan))
        assert done["done"]["already_applied"] is False and balls(table, "Mara Vell") == 2
        (mara,) = done["roster"]["trainers"]
        assert mara["name"] == "Mara Vell"
        assert [(item["name"], item["species"], item["placement"]) for item in mara["companions"]] == [
            ("Snik", "Goblin", "active")]
        assert [item["name"] for item in done["roster"]["creatures"]] == ["Grub"]
        assert done["roster"]["turns"][-1]["accepted"] is True, "the turn now holds an accepted change"

        # the commit retried after its answer was lost: 050's own receipt, nothing written or spent again
        again = table("companions.capture_commit", **reviewed(args, plan))
        assert again["done"]["already_applied"] is True and balls(table, "Mara Vell") == 2
        assert again["roster"]["trainers"] == done["roster"]["trainers"]
        replanned = table("companions.capture_plan", **args)["plan"]
        assert replanned["already_applied"] is True
        # the same attempt id for another capture is refused, and nothing is spent
        assert refused(table, "companions.capture_plan",
                       **{**args, "creature": str(table.grub)}).reason == "attempt_conflict"
        assert refused(table, "companions.capture_commit",
                       **reviewed({**args, "creature": str(table.grub)}, plan)).reason == "attempt_conflict"
        assert balls(table, "Mara Vell") == 2
        # 021 accepts one change a turn: another capture at the same turn is refused with its reason
        other = capture(table, "catch-grub", "Mara Vell", table.grub, turn)
        assert refused(table, "companions.capture_plan", **other).reason == "turn_already_accepted"


def test_a_players_character_takes_their_own_key_and_a_stale_review_is_refused(tmp_path):
    with played(tmp_path, creatures=True) as table:
        first = roster(table)["turns"][-1]["turn"]
        # Tamsin made no post in the greeting turn: Bob's key has nothing to rest on there
        assert refused(table, "companions.capture_plan", **capture(table, "c1", "Tamsin Hale", table.grub, first)
                       ).reason == "no_backing"
        turn = shared_turn(table)
        args = capture(table, "catch-grub", "Tamsin Hale", table.grub, turn)
        plan = table("companions.capture_plan", **args)["plan"]
        assert [(item["name"], item["player"]) for item in plan["keys"]] == [("Tamsin Hale", BOB)]
        assert refused(table, "companions.capture_commit", **reviewed(args, plan)).reason == "player_key_required"
        assert refused(table, "companions.capture_commit", **reviewed(args, plan, keys=["human:carol"])
                       ).reason == "not_at_this_table"
        assert balls(table, "Tamsin Hale") == 1

        # the party moves on after the review: the reviewed capture is refused and nothing is written
        parties = PartyLibrary(table.table.store, table.table.owner, clock=Clock(), new_id=Ids(), owner=OWNER)
        parties.join(InstanceRef.parse(table.cast["Tamsin Hale"]), table.snik,
                     at=Moment(plan["sequence"], table.chat.chat_id, turn), provenance="test").commit()
        assert refused(table, "companions.capture_commit", **reviewed(args, plan, keys=[BOB])).reason == "changed"
        assert balls(table, "Tamsin Hale") == 1

        # reviewed again, with Bob's key given at this device, it is made once
        fresh = table("companions.capture_plan", **args)["plan"]
        assert fresh["placement"] == "reserve", "Snik now fills Tamsin's one active place"
        done = table("companions.capture_commit", **reviewed(args, fresh, keys=[BOB]))
        assert done["done"]["already_applied"] is False and balls(table, "Tamsin Hale") == 0
        (tamsin,) = [item for item in done["roster"]["trainers"] if item["name"] == "Tamsin Hale"]
        assert ("Grub", "reserve") in [(item["name"], item["placement"]) for item in tamsin["companions"]]
        retry = table("companions.capture_commit", **reviewed(args, fresh, keys=[BOB]))
        assert retry["done"]["already_applied"] is True and balls(table, "Tamsin Hale") == 0


def test_an_outcome_with_nothing_to_write_writes_nothing(tmp_path):
    with played(tmp_path, creatures=True) as table:
        turn = roster(table)["turns"][-1]["turn"]
        declined = table("companions.capture_plan", **capture(table, "d1", "Mara Vell", table.snik, turn,
                                                             outcome="declined"))["plan"]
        assert declined["writes"] is False and declined["spends"] == 0
        missed = table("companions.capture_plan", **capture(table, "f1", "Mara Vell", table.snik, turn,
                                                           outcome="failed"))["plan"]
        assert missed["writes"] is True and missed["spends"] == 1, "a ball thrown and missed is still spent"
        done = table("companions.capture_commit", **reviewed(capture(table, "f1", "Mara Vell", table.snik, turn,
                                                                     outcome="failed"), missed))
        assert done["roster"]["trainers"] == [] and balls(table, "Mara Vell") == 2


def test_a_bad_capture_request_is_refused_with_its_reason_never_a_server_error(tmp_path):
    with played(tmp_path, creatures=True) as table:
        turn = roster(table)["turns"][-1]["turn"]
        args = capture(table, "bad", "Mara Vell", table.snik, turn)
        for change, reason in (({"capacity": -1}, "invalid_field"), ({"capacity": 1001}, "invalid_field"),
                               ({"capacity": "1"}, "invalid_field"), ({"capacity": True}, "invalid_field"),
                               ({"outcome": "caught"}, "invalid_field"), ({"placement": "bag"}, "invalid_field"),
                               ({"turn": "nope"}, "unknown_turn"), ({"turn": None}, "invalid_field"),
                               ({"trainer": "garbage"}, "invalid_field"), ({"attempt_id": "a/b"}, "invalid_field"),
                               ({"device": 5}, "invalid_field"),
                               ({"creature": table.cast["Anya Rook"]}, "invalid_field"),
                               ({"trainer": str(table.snik)}, "invalid_field"),
                               ({"device": "great-ball"}, None), ({"audience": "everyone"}, "invalid_field")):
            error = refused(table, "companions.capture_plan", **{**args, **change})
            assert error.reason and (reason is None or error.reason == reason), (change, error.reason)
        assert refused(table, "companions.capture_commit", **{**args, "sequence": "1", "expected": "k"}
                       ).reason == "review_required"
        assert balls(table, "Mara Vell") == 3
        missing = str(table.chat).rsplit("/", 1)[0] + "/t-none"
        assert refused(table, "companions.roster", chat=missing, **DIRECTOR_VIEW).reason
        assert refused(table, "companions.roster", chat="nonsense", **DIRECTOR_VIEW).reason == "invalid_field"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_director_records_a_capture_on_the_page(tmp_path):
    with played(tmp_path, creatures=True) as table:
        result = node_e2e(table, "companions.e2e.mjs", {"GRIMOIRE_TRAINER": table.cast["Mara Vell"]}, NODE)
        assert balls(table, "Mara Vell") == 2, "one ball spent, however often the page asked"
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
    assert "# pass 1" in result.stdout
