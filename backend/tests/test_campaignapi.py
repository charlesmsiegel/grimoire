"""113: the campaign hub's API over a real library, through the real 035 runner, 029, 040 and 021.

Over the synthetic card library (``scene_fixtures``): the owner plays Anya Rook (the persona) and directs. Anya
wants into the cargo hold Mara Vell keeps; paying Mara off succeeds and brings its authored price, a debt chit,
onto the turn's effect. The owner endorses with the player's key and approves with the director's, each as its
own call; the effect is accepted once and is world truth for the director only. Tamsin Hale, a clerk attached
later who was never at the hatch, saw something privately: it is hers, never the table's, never world truth.
Every name and fact is synthetic; every model call is scripted.
"""

from __future__ import annotations

import shutil

import pytest

from chat_fixtures import Clock
from generation_fixtures import say
from scene_fixtures import LABEL_POLICY, Table, Tagged, attach_tamsin, card_library
from store_helpers import OWNER

from grimoire.campaignapi import CampaignApi
from grimoire.core import DIRECTOR, Event, Moment, Perception
from grimoire.effectstore import TurnEffectsLibrary
from grimoire.obstacles import (
    ACTOR, Approach, Cost, HoldingsChange, Obstacle, ObstacleFact, ObstacleStake, RelationChange,
)
from grimoire.play.scene import NOW
from grimoire.providers import RouteChoice
from grimoire.runtime import LabelRuntime
from grimoire.textapi import ApiRefused

DEBT = "Mara owes the harbour master a favour."
CHIT = "Anya takes on Mara's debt chit."
SECRET = "Tamsin sees a second ledger in the customs office."
BRIBE = '{"approach": "bribe", "factors": [], "stakes": []}'
SNEAK = '{"approach": "sneak", "factors": [], "stakes": []}'


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return card_library(tmp_path_factory.mktemp("hub"))


@pytest.fixture
def cards(built, tmp_path):
    folder, card_chat, _ = built
    copy = tmp_path / "cards"
    shutil.copytree(folder, copy)
    return copy, card_chat


def hold(mara) -> Obstacle:
    """The cargo hold: a bribe nothing hinders (or slipping past), priced with a debt chit and Mara knowing Anya."""
    debt = ObstacleFact("debt", "motive", DEBT, 0, "author:test", {"bribe": "favours"})
    lantern = ObstacleFact("lantern", "constraint", "A lantern hangs over the hatch.", 0, "author:test",
                           {"sneak": "hinders"})
    dark = ObstacleFact("dark", "constraint", "The night is moonless.", 0, "author:test", {"*": "favours"})
    chit = Cost("chit", CHIT, (HoldingsChange("add", ACTOR, "debt-chit", 1, agent=ACTOR),
                               RelationChange("begin", "knows", mara, ACTOR)))
    return Obstacle(obstacle_id="hold", campaign_id=mara.campaign_id, goal="Get into the cargo hold.",
                    actors=frozenset({mara}), facts=(debt, lantern, dark),
                    stakes=(ObstacleStake("seen", "Anya is seen at the hatch."),),
                    approaches=(Approach("bribe", "Pay Mara to look away.", frozenset(), (chit,)),
                                Approach("sneak", "Slip past while Mara counts.", frozenset({"seen"}))),
                    provenance="author:test")


class Hub:
    """One open library and its campaign hub: the owner plays and directs; the labeller is scripted."""

    def __init__(self, folder, *replies: str, labelling: bool = True) -> None:
        self.at = Table(folder, policy=LABEL_POLICY, labels=[say(text) for text in replies] if labelling else None)
        labels, labeller = self.at.labels, None
        if labelling:
            labeller = LabelRuntime(labels.adapter, (RouteChoice("primary", "model-a"),), labels.handle,
                                    approvals=labels.approvals)
        self.api = CampaignApi(self.at.store, self.at.authority, owner=OWNER, labeller=labeller,
                               label_handle=self.at.label_handle,
                               policy=LABEL_POLICY if labelling else None, clock=Clock(), new_id=Tagged("h"))

    def call(self, op: str, **args) -> dict:
        return self.api.call(op, args)

    def refused(self, reason: str, op: str, **args) -> ApiRefused:
        with pytest.raises(ApiRefused) as error:
            self.api.call(op, args)
        assert error.value.reason == reason, error.value
        return error.value

    def __enter__(self) -> Hub:
        return self

    def __exit__(self, *exc) -> None:
        self.at.close()


def acting(hub: Hub, chat, text: str = "*I press five coins into Mara's hand*"):
    """The owner seated as Anya, the hold recorded, and Anya's action posted: the turn, Anya and Mara."""
    runner = hub.api.runner
    runner.seat(chat, OWNER)
    scene = runner.open(chat)
    (mara,) = [actor for actor in scene.cast if actor != scene.persona]
    hub.api.obstacles.record(hold(mara))
    record = runner.post(chat, hub.api.handle, text)
    return record.turn_id, scene.persona, mara


def accepted_through_the_hub(hub: Hub, chat):
    """Label, confirm, propose, endorse and approve, each as its own call: the turn and the accepted effect."""
    turn, anya, mara = acting(hub, chat)
    label = hub.call("label", chat=str(chat), obstacle="hold")["done"]["label"]
    assert label["state"] == "proposed" and label["against"] == {"obstacle": "hold", "approach": "bribe",
                                                                 "novel": False}
    outcome = hub.call("confirm", chat=str(chat))["done"]["outcome"]
    assert outcome["result"] == "success"
    proposed = hub.call("propose_effect", chat=str(chat), turn=turn, summary="Anya pays Mara off.")
    effect = proposed["done"]["effect"]
    assert effect["state"] == "proposed" and effect["endorsers"] == [{"contribution": turn, "principal": OWNER,
                                                                     "given": False}]
    endorsed = hub.call("endorse_effect", chat=str(chat), turn=turn, number=1, fingerprint=effect["fingerprint"])
    assert endorsed["done"]["effect"]["endorsers"][0]["given"] is True
    assert endorsed["done"]["effect"]["state"] != "accepted"  # the player's key alone accepts nothing
    approved = hub.call("approve_effect", chat=str(chat), turn=turn, number=1)["done"]["effect"]
    assert approved["state"] == "accepted" and approved["event"]
    return turn, anya, mara, approved


# -- the proving scenario -----------------------------------------------------------------------------------

def test_a_recorded_consequence_is_reviewed_accepted_and_committed_once(cards):
    folder, chat = cards
    with Hub(folder, BRIBE) as hub:
        turn, anya, mara, approved = accepted_through_the_hub(hub, chat)
        (line,) = approved["holdings"]["lines"]
        assert (line["kind"], line["holder"]["ref"], line["item"], line["amount"]) == ("add", str(anya), "debt-chit", 1)
        assert [(op["kind"], op["predicate"]) for op in approved["operations"]] == [("begin", "knows")]
        hub.refused("already_accepted", "approve_effect", chat=str(chat), turn=turn, number=1)
    with Hub(folder, labelling=False) as again:  # reopened: one accepted event, the same one
        director = again.call("hub", chat=str(chat), audience="director")
        (event,) = director["truth"]["events"]
        assert event["event"] == approved["event"] and event["kind"] == "accepted_event"
        assert event["holdings"][0]["item"] == "debt-chit"
        knows = [item for item in director["truth"]["relations"] if item["predicate"] == "knows"]
        assert [(item["subject"]["ref"], item["object"]["ref"]) for item in knows] == [(str(mara), str(anya))]
        (shown,) = [effect for record in director["turns"] for effect in record["effects"]]
        assert shown["state"] == "accepted" and shown["event"] == event["event"]
        assert director["holdings"][str(anya)]["counts"] == {"debt-chit": 1}
        table = again.call("hub", chat=str(chat))
        assert "truth" not in table  # world truth is the director's view
        assert set(table["holdings"]) == {str(anya)}  # the table sees its own characters' holdings
        (obstacle,) = table["obstacles"]
        assert obstacle["status"] == "overcome"
        (due,) = obstacle["due"]  # what the attempt brought, as 040 says; the turn's effect says it was applied
        assert (due["kind"], due["id"], due["changes"]) == ("cost", "chit", ["cost.chit.1", "cost.chit.2"])
        assert again.api.effects.continuity(chat.campaign_id, as_of=NOW, requested_by=DIRECTOR).events[0].event_id \
            == event["event"]


def test_an_absent_npcs_private_evidence_stays_hers(cards):
    folder, chat = cards
    with Hub(folder, labelling=False) as hub:
        tamsin = attach_tamsin(hub.at.store, hub.api.handle, chat)
        hub.api.runner.seat(chat, OWNER)
        campaign = chat.campaign_id
        seen = Event(campaign, "e-ledger", "A second ledger lies in the customs office.", Moment(3), "director")
        private = Perception(campaign, "ev-ledger", tamsin, "e-ledger", "sight", SECRET, Moment(3), "director", True)
        hub.api.evidence.append(campaign, (seen, private)).commit()

        table = hub.call("knowledge", chat=str(chat))
        assert table["perspective"] == "table"
        assert SECRET not in repr(table) and "ev-ledger" not in repr(table)
        assert SECRET not in repr(hub.call("hub", chat=str(chat)))
        assert SECRET not in repr(hub.call("hub", chat=str(chat), audience="director"))  # not world truth either
        hub.refused("director_only", "knowledge", chat=str(chat), actor=str(tamsin))

        director = hub.call("knowledge", chat=str(chat), actor=str(tamsin), audience="director")
        (hers,) = director["actors"]
        (observation,) = hers["observations"]
        assert observation["kind"] == "observation" and observation["holder"]["ref"] == str(tamsin)
        assert observation["content"] == SECRET and director["audience"] == "director"
        anya = hub.api.runner.open(chat).persona
        own = hub.call("knowledge", chat=str(chat), actor=str(anya))
        assert own["audience"] == "table" and SECRET not in repr(own)  # Anya was not there


def test_a_consent_names_the_frame_it_was_shown_and_only_the_acting_player_gives_it(cards):
    folder, chat = cards
    with Hub(folder, SNEAK) as hub:
        acting(hub, chat, "*I slip past the lantern*")
        hub.call("label", chat=str(chat), obstacle="hold")
        confirmed = hub.call("confirm", chat=str(chat))
        assert confirmed["done"]["outcome"] is None  # uncertain: a decision is still owed
        framed = confirmed["hub"]["open_action"]["framed"]
        assert framed["classification"] == "uncertain" and "failure" in framed["allowed"]
        hub.refused("fingerprint_mismatch", "decide", chat=str(chat), result="failure", basis="consent",
                    fingerprint="0" * len(framed["fingerprint"]))
        assert hub.call("hub", chat=str(chat))["open_action"]["outcome"] is None  # the refusal decided nothing
        decided = hub.call("decide", chat=str(chat), result="failure", basis="consent", cites=["lantern"],
                           consequences=["seen"],
                           fingerprint=framed["fingerprint"])
        outcome = decided["done"]["outcome"]
        assert (outcome["result"], outcome["consented_by"]) == ("failure", OWNER)
        assert decided["hub"]["open_action"]["outcome"] == outcome


def test_recovery_with_nothing_cut_short_settles_nothing(cards):
    folder, chat = cards
    with Hub(folder, labelling=False) as hub:
        hub.api.runner.seat(chat, OWNER)
        assert hub.call("recover_effects", chat=str(chat))["done"]["settled"] == []
        assert hub.call("recover_effects", chat=str(chat), abort=True)["done"]["settled"] == []


# -- refusals that write nothing ------------------------------------------------------------------------------

def test_a_stale_or_wrong_key_changes_no_canon(cards):
    folder, chat = cards
    with Hub(folder, BRIBE) as hub:
        turn, anya, _ = acting(hub, chat)
        hub.call("label", chat=str(chat), obstacle="hold")
        hub.call("confirm", chat=str(chat))
        effect = hub.call("propose_effect", chat=str(chat), turn=turn, summary="Anya pays Mara off.")["done"]["effect"]
        before = hub.call("hub", chat=str(chat), audience="director")
        stale = hub.refused("fingerprint_mismatch", "endorse_effect", chat=str(chat), turn=turn, number=1,
                            fingerprint="0" * len(effect["fingerprint"]))
        assert stale.status == 409
        hub.refused("unknown_effect", "approve_effect", chat=str(chat), turn=turn, number=2)
        hub.refused("invalid_field", "approve_effect", chat=str(chat), turn=turn, number="1")
        after = hub.call("hub", chat=str(chat), audience="director")
        assert after == before and after["truth"]["events"] == []  # nothing was written


def test_only_the_librarys_owner_has_a_hub(cards):
    folder, chat = cards
    with Hub(folder, labelling=False) as hub:
        with pytest.raises(ValueError):  # the director view and every write are the owner's
            CampaignApi(hub.at.store, hub.at.authority, owner="human:bob", clock=Clock(), new_id=Tagged("b"))


def test_an_effect_no_character_of_the_players_acts_in_is_the_directors_alone(cards):
    folder, chat = cards
    with Hub(folder, BRIBE) as hub:
        turn, _, mara = acting(hub, chat)
        tamsin = attach_tamsin(hub.at.store, hub.api.handle, chat)
        hub.api.runner.seat(chat, OWNER)
        hub.call("label", chat=str(chat), obstacle="hold")
        hub.call("confirm", chat=str(chat))
        secret = {"kind": "begin", "predicate": "knows", "subject": str(mara), "object": str(tamsin)}
        waive = [{"change": f"cost.chit.{n}", "reason": "Mara forgives the debt."} for n in (1, 2)]
        effect = hub.call("propose_effect", chat=str(chat), turn=turn, summary="Mara and Tamsin meet in secret.",
                          operations=[secret], waive=waive)["done"]["effect"]
        assert effect["endorsers"] == []  # the director's alone: no player key
        hub.call("approve_effect", chat=str(chat), turn=turn, number=1)
        table = hub.call("hub", chat=str(chat))
        assert [effect for record in table["turns"] for effect in record["effects"]] == []
        assert "Mara and Tamsin" not in repr(table)
        director = hub.call("hub", chat=str(chat), audience="director")
        (event,) = director["truth"]["events"]
        assert event["summary"] == "Mara and Tamsin meet in secret."
        codes = [problem["code"] for problem in director["problems"]]
        assert len(codes) == len(set(map(repr, director["problems"])))  # each problem once


def test_the_table_sees_only_its_own_characters_party_standing(cards):
    folder, chat = cards
    with Hub(folder, labelling=False) as hub:
        hub.api.runner.seat(chat, OWNER)
        scene = hub.api.runner.open(chat)
        (mara,) = [actor for actor in scene.cast if actor != scene.persona]
        tamsin = attach_tamsin(hub.at.store, hub.api.handle, chat)
        hub.api.party.join(mara, tamsin, at=Moment(2), provenance="director").commit()
        assert hub.call("hub", chat=str(chat))["party"]["memberships"] == []
        (member,) = hub.call("hub", chat=str(chat), audience="director")["party"]["memberships"]
        assert (member["member"]["ref"], member["party"]["ref"]) == (str(tamsin), str(mara))


def test_a_character_set_in_reserve_keeps_its_players_party_standing(cards):
    folder, chat = cards
    with Hub(folder, labelling=False) as hub:
        hub.api.runner.seat(chat, OWNER)
        scene = hub.api.runner.open(chat)
        anya = scene.persona
        (mara,) = [actor for actor in scene.cast if actor != anya]
        hub.api.party.join(mara, anya, at=Moment(2), provenance="director").commit()
        hub.api.party.store_in_reserve(anya, at=Moment(3), provenance="director").commit()
        table = hub.call("hub", chat=str(chat))
        assert str(anya) not in {item["actor"] for item in table["cast"]}  # reserve leaves the present cast
        (member,) = table["party"]["memberships"]
        assert (member["member"]["ref"], member["status"]) == (str(anya), "reserve")
        assert str(anya) in table["holdings"]


def test_a_malformed_write_is_refused_before_it_writes(cards):
    folder, chat = cards
    with Hub(folder, BRIBE) as hub:
        turn, _, _ = acting(hub, chat)
        hub.refused("invalid_field", "label", chat=str(chat), obstacle="hold", audience="everyone")
        assert hub.at.labels.prompts() == []  # no model call was made
        hub.call("label", chat=str(chat), obstacle="hold")
        hub.call("confirm", chat=str(chat))
        hub.refused("invalid_field", "propose_effect", chat=str(chat), turn=turn, summary="Anya pays Mara off.",
                    audience="everyone")
        assert hub.call("hub", chat=str(chat), audience="director")["turns"][0]["effects"] == []


def test_a_paused_chat_offers_no_step_of_play(cards):
    folder, chat = cards
    with Hub(folder, BRIBE) as hub:
        acting(hub, chat)
        hub.api.runner.request(chat, hub.api.handle, "pause")
        actions = {item["op"]: item for item in hub.call("hub", chat=str(chat))["actions"]}
        assert actions["confirm"] == {"op": "confirm", "available": False, "reason": "play is paused"}
        assert actions["recover_effects"]["available"] is True
        hub.refused("paused", "label", chat=str(chat), obstacle="hold")


def test_recovery_gives_the_players_key_only_when_asked(cards):
    folder, chat = cards
    with Hub(folder, labelling=False) as hub:
        hub.api.runner.seat(chat, OWNER)
        assert hub.call("recover_effects", chat=str(chat), player_key=True)["done"]["settled"] == []
        assert hub.call("recover_effects", chat=str(chat), abort=True, player_key=True)["done"]["settled"] == []


def test_malformed_and_foreign_arguments_are_refused_before_any_write(cards):
    folder, chat = cards
    with Hub(folder, BRIBE) as hub:
        turn, anya, _ = acting(hub, chat)
        hub.refused("unknown_op", "revoke_effect", chat=str(chat))
        hub.refused("invalid_field", "hub", chat="not a chat")
        hub.refused("invalid_field", "hub", chat=str(chat), audience="everyone")
        foreign = str(anya).replace(chat.campaign_id, "elsewhere", 1)
        hub.refused("invalid_field", "propose_effect", chat=str(chat), turn=turn, summary="x",
                    operations=[{"kind": "begin", "predicate": "knows", "subject": foreign, "object": str(anya)}])
        hub.refused("invalid_field", "propose_effect", chat=str(chat), turn=turn, summary="   ")
        hub.refused("invalid_field", "knowledge", chat=str(chat), actor=foreign)
        assert hub.call("hub", chat=str(chat), audience="director")["turns"][0]["effects"] == []


# -- what the hub says is and is not available ---------------------------------------------------------------

def test_the_hub_offers_only_what_is_implemented_and_names_each_blocker(cards):
    folder, chat = cards
    with Hub(folder, labelling=False) as hub:
        hub.api.runner.seat(chat, OWNER)
        view = hub.call("hub", chat=str(chat))
        (label,) = [action for action in view["actions"] if action["op"] == "label"]
        assert label["available"] is False and label["reason"]
        assert {action["op"] for action in view["actions"]} <= set(hub.api.ops)
        unavailable = {item["feature"]: item["blocker"] for item in view["unavailable"]}
        assert unavailable["revoke_effect"] == "035" and unavailable["stage"] == "041"
        assert not set(unavailable) & set(hub.api.ops)
        hub.refused("no_labeller", "label", chat=str(chat))
        anya = hub.api.runner.open(chat).persona
        (seat,) = [item for item in view["cast"] if item["actor"] == str(anya)]
        assert (seat["role"], seat["controller"]) == ("player", OWNER)
        assert all(item["role"] == "npc" for item in view["cast"] if item["actor"] != str(anya))


def test_effects_library_is_shared_with_the_runner(cards):
    folder, _ = cards
    with Hub(folder, labelling=False) as hub:
        assert isinstance(hub.api.effects, TurnEffectsLibrary) and hub.api.runner.effects is hub.api.effects
