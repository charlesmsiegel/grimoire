"""035 milestone 2 / 040: an action against an open obstacle, through the runner, and what it makes due.

Over the synthetic card library (``scene_fixtures``): Anya Rook (``human:ana``'s persona) wants into the cargo
hold Mara Vell keeps. The hold suggests paying Mara off or slipping past her lantern; a moonless night favours
every approach. The labeller chooses the approach (or a novel one); the obstacle's own facts and stakes for it
are added by rule; the player confirms; 029 decides; and the costs and stakes that came true are structured 021
changes on the turn's effect, accepted with every key. Every name and fact is synthetic.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace

import pytest

from generation_fixtures import say
from scene_fixtures import ANA, LABEL_POLICY, Table, card_library

from grimoire.core import DIRECTOR
from grimoire.effects import EffectProposal, Op
from grimoire.holdings import Line
from grimoire.obstacles import (
    ACTOR, Approach, Cost, HoldingsChange, Obstacle, ObstacleFact, ObstacleStake, RelationChange,
)
from grimoire.play import SceneRunner
from grimoire.scene import TURN_SCHEMA, Against, SceneRefused, Waiver, turn_from_payload, turn_to_payload

NOW = 2**62
DEBT = "Mara owes the harbour master a favour."
DARK = "The night is moonless."
REPORTED = "Mara tells the harbour master who came to the hatch."
CHIT = "Anya takes on Mara's debt chit."


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return card_library(tmp_path_factory.mktemp("built"))


@pytest.fixture
def cards(built, tmp_path):
    folder, card_chat, _ = built
    copy = tmp_path / "cards"
    shutil.copytree(folder, copy)
    return copy, card_chat


def refused(reason: str, call, *args, **kwargs) -> SceneRefused:
    with pytest.raises(SceneRefused) as error:
        call(*args, **kwargs)
    assert error.value.reason == reason, error.value
    return error.value


def reply(approach: str, factors=(), stakes=()) -> str:
    return json.dumps({"approach": approach, "factors": [{"fact": f, "bearing": b} for f, b in factors],
                       "stakes": list(stakes)})


def hold(mara, **changes) -> Obstacle:
    """The cargo hold: two suggestions nothing prevents, a fact for every approach, and authored changes."""
    debt = ObstacleFact("debt", "motive", DEBT, 0, "author:test", {"bribe": "favours"})
    lantern = ObstacleFact("lantern", "constraint", "A lantern hangs over the hatch.", 0, "author:test",
                           {"sneak": "hinders"})
    dark = ObstacleFact("dark", "constraint", DARK, 0, "author:test", {"*": "favours"})
    reported = ObstacleStake("reported", REPORTED, {"bribe": "hinders"},
                             (RelationChange("begin", "knows", mara, ACTOR),))
    seen = ObstacleStake("seen", "Anya is seen at the hatch.")
    chit = Cost("chit", CHIT, (HoldingsChange("add", ACTOR, "debt-chit", 1, agent=ACTOR),
                               RelationChange("begin", "knows", mara, ACTOR)))
    fields = dict(obstacle_id="hold", campaign_id=mara.campaign_id, goal="Get into the cargo hold.",
                  actors=frozenset({mara}), facts=(debt, lantern, dark), stakes=(reported, seen),
                  approaches=(Approach("bribe", "Pay Mara to look away.", frozenset({"reported"}), (chit,)),
                              Approach("sneak", "Slip past the lantern.", frozenset({"seen", "reported"}))),
                  provenance="author:test")
    fields.update(changes)
    return Obstacle(**fields)


def table(folder, *replies: str, obstacles: bool = True, propose: bool = True) -> Table:
    return Table(folder, policy=LABEL_POLICY, labels=[say(text) for text in replies], adjudicate=True,
                 effects=True, obstacles=obstacles, propose=propose)


def acting(at: Table, chat, text: str = "*I press five coins into Mara's hand*", obstacle=None):
    """Anya's action at the hold (recorded first, with ``obstacle``'s changes): the turn, Anya and Mara."""
    at.runner.seat(chat, ANA)
    scene = at.runner.open(chat)
    (mara,) = [actor for actor in scene.cast if actor != scene.persona]
    if at.obstacles is not None:
        at.obstacles.record(obstacle(mara) if obstacle is not None else hold(mara))
    record = at.runner.post(chat, at.player(), text)
    return record.turn_id, scene.persona, mara


def stored(at: Table, chat, turn: str):
    (record,) = [record for record in at.runner.open(chat).records if record.turn_id == turn]
    return record


# -- labelling against an obstacle -----------------------------------------------------------------

def test_the_labeller_chooses_a_suggestion_and_the_authors_facts_and_stakes_frame_it(cards):
    folder, chat = cards
    with table(folder, reply("bribe", [("f1", "favours")])) as at:
        turn, anya, mara = acting(at, chat)
        label = at.runner.label(chat, at.player(), obstacle="hold")
        assert label.state == "proposed" and label.against == Against("hold", "bribe", False)
        by_id = {factor.factor_id: factor for factor in label.factors}
        assert sorted(by_id) == ["dark", "debt", "f1"]  # the author's two by rule, the labeller's own beside them
        assert (by_id["debt"].bearing, by_id["debt"].text, by_id["dark"].bearing) == ("favours", DEBT, "favours")
        assert [(stake.stake_id, stake.text) for stake in label.stakes] == [("reported", REPORTED)]
        (prompt,) = at.labels.prompts()
        assert "Obstacle: Get into the cargo hold." in prompt and "bribe: Pay Mara to look away." in prompt
        assert DEBT not in prompt  # the obstacle's facts are added by rule, never offered to be cited
    with Table(folder, policy=LABEL_POLICY, adjudicate=True, obstacles=True) as again:  # the turn file keeps it
        assert stored(again, chat, turn).labels == (label,)


def test_a_novel_approach_is_framed_with_every_approachs_facts_and_its_own_stakes(cards):
    folder, chat = cards
    with table(folder, reply("novel", [("f1", "favours")], ["Mara hears the deck creak"])) as at:
        turn, _, _ = acting(at, chat, "*I climb down through the cargo chute*")
        label = at.runner.label(chat, at.player(), obstacle="hold")
        assert label.against == Against("hold", "novel", True)
        assert sorted(factor.factor_id for factor in label.factors) == ["dark", "f1"]  # the "*" fact, and its own
        assert [(stake.stake_id, stake.text) for stake in label.stakes] == [("s1", "Mara hears the deck creak")]
        assert at.runner.confirm(chat, at.player()).decision.result == "success"  # nothing hinders it
        state = at.obstacles.state(chat.campaign_id, "hold")
        (attempt,) = state.attempts
        assert (attempt.attempt.attempt_id, attempt.attempt.listed, attempt.status) == (turn, False, "overcame")
        assert attempt.attempt.approach.risks == frozenset({"s1"}) and state.due() == ()  # no authored price


def test_a_reply_that_breaks_the_obstacles_rules_is_refused_whole(cards):
    folder, chat = cards
    replies = (reply("bribe", stakes=["She drops the coins"]), reply("climb"),
               json.dumps({"factors": [], "stakes": []}))
    with table(folder, *replies) as at:
        acting(at, chat)
        for reason in ("suggested_stakes", "unknown_approach", "unknown_approach"):
            label = at.runner.label(chat, at.player(), obstacle="hold")
            assert (label.state, label.reason, label.factors, label.against) == (
                "refused", reason, (), Against("hold"))


def test_no_attempt_is_labelled_at_an_obstacle_that_cannot_take_one(cards):
    folder, chat = cards
    with table(folder, obstacles=False) as at:
        acting(at, chat)
        refused("no_obstacles", at.runner.label, chat, at.player(), obstacle="hold")
    with table(folder, propose=False) as at:  # the labeller's grant is on record already
        refused("unknown_obstacle", at.runner.label, chat, at.player(), obstacle="vault")
        refused("invalid_field", at.runner.label, chat, at.player(), obstacle=7)
        scene = at.runner.open(chat)
        (mara,) = [actor for actor in scene.cast if actor != scene.persona]
        clash = hold(mara, obstacle_id="crate", facts=(ObstacleFact("f1", "clue", DARK, 0, "author:test", {}),))
        at.obstacles.record(clash)
        refused("obstacle_ids_clash", at.runner.label, chat, at.player(), obstacle="crate")
        late = hold(mara, obstacle_id="late", facts=(ObstacleFact("dark", "clue", DARK, 50, "author:test", {}),))
        at.obstacles.record(late)
        refused("premature", at.runner.label, chat, at.player(), obstacle="late")
        assert at.labels.prompts() == [] and at.runner.open(chat).pending.labels == ()  # no call, nothing written


def test_an_overcome_obstacle_takes_no_further_attempt(cards):
    folder, chat = cards
    with table(folder, reply("bribe")) as at:
        acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")
        at.runner.confirm(chat, at.player())
        at.runner.pass_turn(chat, at.player())
        at.runner.post(chat, at.player(), "*I try the hatch again*")
        refused("already_overcome", at.runner.label, chat, at.player(), obstacle="hold")


def test_a_runner_takes_only_this_librarys_obstacles_on_the_adjudication_key(cards):
    folder, _ = cards
    with table(folder) as at:
        with pytest.raises(SceneRefused) as error:
            SceneRunner(at.store, at.authority, at.owner, owner=at.runner.owner, obstacles=at.obstacles)
        assert error.value.reason == "bad_obstacles"  # no adjudication key to share


class Crash(BaseException):
    """The process dies here: nothing after it runs, and no handler sees it."""


def test_confirming_checks_the_obstacle_as_it_stands_then_and_a_mismatch_frames_nothing(cards):
    folder, chat = cards
    with table(folder, reply("bribe"), reply("bribe")) as at:
        turn, _, mara = acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")
        # the author adds a fact before anyone confirmed: the labelling no longer carries all that bears on it
        wary = ObstacleFact("wary", "motive", "Mara was cheated once.", 0, "author:test", {"bribe": "hinders"})
        at.obstacles.record(hold(mara, facts=(*hold(mara).facts, wary)))
        refused("missing_fact", at.runner.confirm, chat, at.player())
        assert stored(at, chat, turn).labels[-1].state == "proposed"  # as for 029: a refused check writes nothing
        assert at.obstacles.state(chat.campaign_id, "hold").attempts == ()  # nothing linked, nothing framed
        assert at.adjudication.state(chat.campaign_id).frames == {}
        relabelled = at.runner.label(chat, at.player(), obstacle="hold")
        assert "wary" in {factor.factor_id for factor in relabelled.factors}
        assert at.runner.confirm(chat, at.player()) is None  # hindered now: the director decides


def test_a_confirm_cut_short_after_the_link_finishes_on_the_next_confirm(cards, monkeypatch):
    folder, chat = cards
    with table(folder, reply("bribe")) as at:
        turn, _, _ = acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")

        def crash(*args, **kwargs):
            raise Crash
        monkeypatch.setattr(at.obstacles.adjudication, "frame", crash)
        with pytest.raises(Crash):
            at.runner.confirm(chat, at.player())
    monkeypatch.undo()
    with table(folder, propose=False) as again:  # the restart: the link is on record, the frame is not
        (waiting,) = again.obstacles.state(chat.campaign_id, "hold").attempts
        assert (waiting.attempt.attempt_id, waiting.status, waiting.frame) == (turn, "pending", None)
        assert stored(again, chat, turn).framed is not None
        assert again.runner.confirm(chat, again.player()).decision.result == "success"
        (attempt,) = again.obstacles.state(chat.campaign_id, "hold").attempts
        assert attempt.status == "overcame"


# -- what the attempt makes due ------------------------------------------------------------------------

def test_a_success_brings_its_approachs_authored_price_into_the_turns_effect(cards):
    folder, chat = cards
    with table(folder, reply("bribe")) as at:
        turn, anya, mara = acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")
        outcome = at.runner.confirm(chat, at.player())
        assert outcome.decision.result == "success"  # straightforward: nothing hinders a bribe
        (due,) = at.obstacles.state(chat.campaign_id, "hold").due()
        assert (due.kind, due.consequence_id, due.text) == ("cost", "chit", CHIT)
        effect = at.runner.propose_effect(chat, at.owner, turn, "Anya pays Mara off.")
        proposal = effect.proposal
        assert proposal.operations == (Op("begin", "knows", mara, anya),)  # befalls her: the director's alone
        (line,) = proposal.holdings.lines
        assert (line.line, line.agent, line.backed_by) == (Line.add(anya, "debt-chit", 1), anya, turn)
        assert effect.endorsers == ((turn, ANA),)  # her own act: her player's key too
        at.runner.endorse_effect(chat, at.player(), turn, 1, proposal.fingerprint)
        accepted = at.runner.approve_effect(chat, at.owner, turn, 1)
        assert accepted.state == "accepted"
        (event,) = at.effects.continuity(chat.campaign_id, as_of=NOW, requested_by=DIRECTOR).events
        assert event.event_id == accepted.event_id and event.holdings is not None


def test_a_stake_that_comes_true_is_applied_and_what_the_proposer_adds_comes_after_it(cards):
    folder, chat = cards
    with table(folder, reply("sneak")) as at:
        turn, anya, mara = acting(at, chat, "*I slip past the lantern*")
        label = at.runner.label(chat, at.player(), obstacle="hold")
        assert {factor.factor_id: factor.bearing for factor in label.factors} == {"dark": "favours",
                                                                                   "lantern": "hinders"}
        assert at.runner.confirm(chat, at.player()) is None  # uncertain: the director decides
        at.runner.decide(chat, at.owner, "failure", cites=("lantern",), consequences=("reported",))
        state = at.obstacles.state(chat.campaign_id, "hold")
        assert state.status == "open" and "reported" in {fact.fact_id for fact in state.facts}  # it escalated
        met = Op("begin", "has_met", anya, mara, anya, turn)
        effect = at.runner.propose_effect(chat, at.owner, turn, "Mara reports Anya; they have met.", [met])
        assert effect.proposal.operations == (Op("begin", "knows", mara, anya), met)
        assert effect.proposal.holdings is None
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        assert at.runner.approve_effect(chat, at.owner, turn, 1).state == "accepted"


def test_an_effect_that_drops_what_was_made_due_is_never_accepted(cards):
    folder, chat = cards
    with table(folder, reply("sneak")) as at:
        turn, anya, mara = acting(at, chat, "*I slip past the lantern*")
        at.runner.label(chat, at.player(), obstacle="hold")
        at.runner.confirm(chat, at.player())
        at.runner.decide(chat, at.owner, "failure", cites=("lantern",), consequences=("reported",))
        effect = at.runner.propose_effect(chat, at.owner, turn, "Mara reports Anya.")
        # the turn file is edited by hand: the proposal now makes Anya know Mara, and not the report
        (current,) = [s for s in at.runner.scenes.turns(chat).turns if s.record.turn_id == turn]
        original = effect.proposal
        forged = EffectProposal(original.campaign_id, original.chat, original.moment, original.summary,
                                (Op("begin", "knows", anya, mara),), original.proposed_by)
        at.runner._write(current.record.with_effect(replace(effect, proposal=forged)), current)
        refusal = at.runner.approve_effect(chat, at.owner, turn, 1)
        assert (refusal.state, refusal.reason) == ("refused", "due_missing")
        assert at.effects.continuity(chat.campaign_id, as_of=NOW, requested_by=DIRECTOR).events == ()


def test_a_turn_against_no_obstacle_proposes_as_before(cards):
    folder, chat = cards
    with table(folder, json.dumps({"factors": [{"fact": "f1", "bearing": "favours"}], "stakes": []})) as at:
        turn, anya, mara = acting(at, chat, "*I wave to Mara*")
        assert at.runner.label(chat, at.player()).against is None
        at.runner.confirm(chat, at.player())
        effect = at.runner.propose_effect(chat, at.owner, turn, "Mara knows Anya.", [Op("begin", "knows", mara, anya)])
        assert effect.proposal.operations == (Op("begin", "knows", mara, anya),)
        assert at.runner.approve_effect(chat, at.owner, turn, 1).state == "accepted"


# -- the turn file -----------------------------------------------------------------------------------

def test_a_labelling_against_an_obstacle_is_tamper_checked_on_read(cards):
    folder, chat = cards
    with table(folder, reply("bribe")) as at:
        turn, _, _ = acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")
        record = stored(at, chat, turn)
    payload = turn_to_payload(record)
    assert payload["labels"][0]["against"] == {"obstacle": "hold", "approach": "bribe", "novel": False}
    assert turn_from_payload(record.ref, TURN_SCHEMA, payload) == record
    for broken in ({"obstacle": "hold", "approach": None, "novel": False},  # a proposal with no approach chosen
                   {"obstacle": "hold", "approach": None, "novel": True},
                   {"obstacle": "hold/x", "approach": "bribe", "novel": False},
                   {"obstacle": "hold", "approach": "bribe", "novel": 1},
                   {"obstacle": "hold", "approach": "bribe"}):
        bad = json.loads(json.dumps(payload))
        bad["labels"][0]["against"] = broken
        with pytest.raises(SceneRefused) as error:
            turn_from_payload(record.ref, TURN_SCHEMA, bad)
        assert error.value.reason == "invalid_field", broken
    bad = json.loads(json.dumps(payload))
    bad["labels"][0]["unknown"] = 1
    with pytest.raises(SceneRefused):
        turn_from_payload(record.ref, TURN_SCHEMA, bad)
    old = json.loads(json.dumps(payload))
    del old["labels"][0]["against"]  # a labelling written before obstacles reads as one against none
    assert turn_from_payload(record.ref, TURN_SCHEMA, old).labels[0].against is None


# -- the obstacle ledger is the record of what an action attempts (review F1) -------------------------------

PLAIN = json.dumps({"factors": [], "stakes": []})


def test_a_turn_file_that_drops_against_still_meets_the_obstacle_ledgers_link(cards):
    folder, chat = cards
    with table(folder, reply("bribe")) as at:
        turn, anya, mara = acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")
        assert at.runner.confirm(chat, at.player()).decision.result == "success"
        effect = at.runner.propose_effect(chat, at.owner, turn, "Anya pays Mara off.")
        # edited by hand: the due changes dropped, and the framed labelling's ``against`` removed with them
        (current,) = [s for s in at.runner.scenes.turns(chat).turns if s.record.turn_id == turn]
        original = effect.proposal
        forged = EffectProposal(original.campaign_id, original.chat, original.moment, original.summary,
                                (Op("begin", "knows", anya, mara),), original.proposed_by)
        record = current.record.with_label(replace(current.record.framed, against=None))
        at.runner._write(record.with_effect(replace(effect, proposal=forged, endorsers=())), current)
        result = at.runner.approve_effect(chat, at.owner, turn, 1)
        assert (result.state, result.reason) == ("refused", "obstacle_link_mismatch")
        assert at.effects.continuity(chat.campaign_id, as_of=NOW, requested_by=DIRECTOR).events == ()
        (due,) = at.obstacles.state(chat.campaign_id, "hold").due()  # the obstacle still holds what came due
        assert due.consequence_id == "chit"
        refused("obstacle_link_mismatch", at.runner.propose_effect, chat, at.owner, turn, "Again.",
                [Op("begin", "knows", anya, mara)])


def test_a_turn_that_claims_an_attempt_the_ledger_never_linked_takes_no_effect(cards):
    folder, chat = cards
    with table(folder, PLAIN) as at:
        turn, anya, mara = acting(at, chat, "*I wave to Mara*")
        at.runner.label(chat, at.player())
        at.runner.confirm(chat, at.player())
        (current,) = [s for s in at.runner.scenes.turns(chat).turns if s.record.turn_id == turn]
        claimed = replace(current.record.framed, against=Against("hold", "bribe", False))
        at.runner._write(current.record.with_label(claimed), current)
        refused("obstacle_link_mismatch", at.runner.propose_effect, chat, at.owner, turn, "Mara knows Anya.",
                [Op("begin", "knows", mara, anya)])


# -- a due change the world cannot take is waived by the director (review F2, F4) -----------------------

def test_a_due_relation_that_already_holds_is_waived_and_the_turn_takes_its_effect(cards):
    folder, chat = cards
    with table(folder, PLAIN, reply("bribe")) as at:
        first, anya, mara = acting(at, chat, "*I wave to Mara*")
        at.runner.label(chat, at.player())
        at.runner.confirm(chat, at.player())
        known = at.runner.propose_effect(chat, at.owner, first, "Mara knows Anya.", [Op("begin", "knows", mara, anya)])
        assert at.runner.approve_effect(chat, at.owner, first, known.number).state == "accepted"
        at.runner.pass_turn(chat, at.player())
        second = at.runner.post(chat, at.player(), "*I press five coins into Mara's hand*").turn_id
        at.runner.label(chat, at.player(), obstacle="hold")
        assert at.runner.confirm(chat, at.player()).decision.result == "success"
        effect = at.runner.propose_effect(chat, at.owner, second, "Anya pays Mara off.")
        at.runner.endorse_effect(chat, at.player(), second, effect.number, effect.fingerprint)
        failed = at.runner.approve_effect(chat, at.owner, second, effect.number)
        assert (failed.state, failed.reason) == ("refused", "precondition_failed")  # she knows Anya already
        waiver = Waiver("cost.chit.2", "Mara already knows Anya.")
        refused("not_director", at.runner.propose_effect, chat, at.player(), second, "Anya pays.", waive=[waiver])
        refused("unknown_due_change", at.runner.propose_effect, chat, at.owner, second, "Anya pays.",
                waive=[Waiver("cost.purse.1", "No purse.")])
        again = at.runner.propose_effect(chat, at.owner, second, "Anya takes the chit.", waive=[waiver])
        assert again.waivers == (waiver,) and again.proposal.operations == ()
        assert again.fingerprint != again.proposal.fingerprint  # what is shown and keyed covers the waiver
        refused("fingerprint_mismatch", at.runner.endorse_effect, chat, at.player(), second, again.number,
                again.proposal.fingerprint)
        at.runner.endorse_effect(chat, at.player(), second, again.number, again.fingerprint)
        accepted = at.runner.approve_effect(chat, at.owner, second, again.number)  # the recorded key is presented
        assert accepted.state == "accepted"
        (due,) = at.obstacles.state(chat.campaign_id, "hold").due()
        assert len(due.changes) == 2  # a waiver is the effect's: the obstacle's record of what came due stands
    with table(folder, propose=False) as reread:  # the turn file keeps the waiver
        (kept,) = [e for e in stored(reread, chat, second).effects if e.number == again.number]
        assert kept.waivers == (waiver,) and kept.state == "accepted"


def test_a_cost_the_actor_cannot_pay_is_waived_and_the_turn_takes_an_effect(cards):
    folder, chat = cards

    def purse(mara):
        pay = Cost("purse", "Twelve crowns change hands.",
                   (HoldingsChange("move", ACTOR, "crown", 12, target=mara, agent=ACTOR),))
        base = hold(mara)
        return hold(mara, approaches=(replace(base.approaches[0], costs=(pay,)), base.approaches[1]))
    with table(folder, reply("bribe")) as at:
        turn, anya, mara = acting(at, chat, obstacle=purse)
        at.runner.label(chat, at.player(), obstacle="hold")
        assert at.runner.confirm(chat, at.player()).decision.result == "success"
        effect = at.runner.propose_effect(chat, at.owner, turn, "Anya pays Mara off.")
        at.runner.endorse_effect(chat, at.player(), turn, effect.number, effect.fingerprint)
        failed = at.runner.approve_effect(chat, at.owner, turn, effect.number)
        assert (failed.state, failed.reason) == ("refused", "insufficient")  # Anya holds no crowns
        waiver = Waiver("cost.purse.1", "Anya has no crowns; the debt stands in the story.")
        again = at.runner.propose_effect(chat, at.owner, turn, "Mara lets Anya by.",
                                         [Op("begin", "knows", mara, anya)], waive=[waiver])
        assert again.proposal.holdings is None and again.endorsers == ()  # the director's alone now
        assert at.runner.approve_effect(chat, at.owner, turn, again.number).state == "accepted"
        (due,) = at.obstacles.state(chat.campaign_id, "hold").due()
        assert due.consequence_id == "purse"  # still due by the obstacle's record


def test_two_consequences_making_the_same_change_due_take_one_waiver(cards):
    folder, chat = cards
    with table(folder, reply("bribe", [("f1", "hinders")])) as at:
        turn, anya, mara = acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")
        assert at.runner.confirm(chat, at.player()) is None  # hindered: the director decides
        at.runner.decide(chat, at.owner, "partial", cites=("debt", "f1"), consequences=("reported",))
        # the report and the chit both begin Mara knowing Anya: 021 refuses the same operation twice
        refused("conflicting_operations", at.runner.propose_effect, chat, at.owner, turn, "Anya pays.")
        effect = at.runner.propose_effect(chat, at.owner, turn, "Anya pays; Mara reports her.",
                                          waive=[Waiver("stake.reported.1", "The chit makes Mara know her.")])
        assert effect.proposal.operations == (Op("begin", "knows", mara, anya),)
        at.runner.endorse_effect(chat, at.player(), turn, effect.number, effect.fingerprint)
        assert at.runner.approve_effect(chat, at.owner, turn, effect.number).state == "accepted"


def test_a_hand_over_since_the_attempt_is_waived_by_the_director(cards):
    from grimoire.ledgers.codec import ControlChange
    from scene_fixtures import BOB
    folder, chat = cards
    with table(folder, reply("bribe")) as at:
        turn, anya, mara = acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")
        assert at.runner.confirm(chat, at.player()).decision.result == "success"
        at.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, anya.instance_id, BOB),
                                     at.owner)
        # the chit is Anya's own act, backed by a post her player no longer stands behind
        refused("invalid_expression", at.runner.propose_effect, chat, at.owner, turn, "Anya pays Mara off.")
        effect = at.runner.propose_effect(chat, at.owner, turn, "Mara knows Anya.",
                                          waive=[Waiver("cost.chit.1", "Anya changed hands since.")])
        assert effect.proposal.holdings is None and effect.endorsers == ()
        assert at.runner.approve_effect(chat, at.owner, turn, effect.number).state == "accepted"


def test_a_waiver_is_tamper_checked_and_bound_to_the_endorsement(cards):
    folder, chat = cards
    with table(folder, PLAIN, reply("bribe")) as at:
        first, anya, mara = acting(at, chat, "*I wave to Mara*")
        at.runner.label(chat, at.player())
        at.runner.confirm(chat, at.player())
        known = at.runner.propose_effect(chat, at.owner, first, "Mara knows Anya.", [Op("begin", "knows", mara, anya)])
        at.runner.approve_effect(chat, at.owner, first, known.number)
        at.runner.pass_turn(chat, at.player())
        turn = at.runner.post(chat, at.player(), "*I press five coins into Mara's hand*").turn_id
        at.runner.label(chat, at.player(), obstacle="hold")
        at.runner.confirm(chat, at.player())
        effect = at.runner.propose_effect(chat, at.owner, turn, "Anya takes the chit.",
                                          waive=[Waiver("cost.chit.2", "Mara already knows Anya.")])
        at.runner.endorse_effect(chat, at.player(), turn, effect.number, effect.fingerprint)
        record = stored(at, chat, turn)
        payload = turn_to_payload(record)
        (written,) = [e for e in payload["effects"] if e["effect"] == effect.number]
        assert written["waivers"] == [{"change": "cost.chit.2", "reason": "Mara already knows Anya."}]
        assert turn_from_payload(record.ref, TURN_SCHEMA, payload) == record
        for broken in ([], [{"change": "chit", "reason": "x"}], [{"change": "cost.chit.2", "reason": " "}],
                       [{"change": "cost.chit.2", "reason": "x", "extra": 1}],
                       [{"change": "cost.chit.2", "reason": "x"}, {"change": "cost.chit.1", "reason": "x"}]):
            bad = json.loads(json.dumps(payload))
            [e for e in bad["effects"] if e["effect"] == effect.number][0]["waivers"] = broken
            with pytest.raises(SceneRefused):
                turn_from_payload(record.ref, TURN_SCHEMA, bad)
        bad = json.loads(json.dumps(payload))
        [e for e in bad["effects"] if e["effect"] == effect.number][0]["proposal"]["proposed_by"] = "player"
        with pytest.raises(SceneRefused):  # a player's proposal never waives
            turn_from_payload(record.ref, TURN_SCHEMA, bad)
        # edited by hand after the endorsement: another reason. Neither the director's recorded waivers nor the
        # player's recorded key covers it, so the waiver does not count and the dropped change is missing
        (current,) = [s for s in at.runner.scenes.turns(chat).turns if s.record.turn_id == turn]
        (live,) = [e for e in current.record.effects if e.number == effect.number]
        edited = replace(live, waivers=(Waiver("cost.chit.2", "Nobody minds."),))
        at.runner._write(current.record.with_effect(edited), current)
        result = at.runner.approve_effect(chat, at.owner, turn, effect.number)
        assert (result.state, result.reason) == ("refused", "due_missing")


# -- an undecided attempt is decided before its turn passes (review F3) --------------------------------------

def test_an_undecided_attempt_is_decided_before_its_turn_is_passed(cards):
    folder, chat = cards
    with table(folder, reply("sneak"), reply("bribe")) as at:
        acting(at, chat, "*I slip past the lantern*")
        at.runner.label(chat, at.player(), obstacle="hold")
        assert at.runner.confirm(chat, at.player()) is None  # uncertain: waits for the director
        refused("attempt_undecided", at.runner.pass_turn, chat, at.player())
        at.runner.decide(chat, at.owner, "failure", cites=("lantern",), consequences=("seen",))
        at.runner.pass_turn(chat, at.player())
        at.runner.post(chat, at.player(), "*I press five coins into Mara's hand*")
        assert at.runner.label(chat, at.player(), obstacle="hold").state == "proposed"  # the obstacle is open


def test_no_call_is_spent_on_an_obstacle_whose_last_attempt_is_undecided(cards):
    from grimoire.adjudication import ActionFrame
    from grimoire.core.knowledge import Moment
    folder, chat = cards
    with table(folder, reply("bribe")) as at:
        _, _, mara = acting(at, chat)
        required = at.obstacles.required(chat.campaign_id, "hold", "sneak", 1)
        stakes = tuple(at.obstacles.state(chat.campaign_id, "hold").obstacle.stake(s).stake
                       for s in ("reported", "seen"))
        elsewhere = ActionFrame("director:npc-1", mara, chat, Moment(1, chat.chat_id, "npc-1"), "Mara slips below.",
                                required, stakes, "test")
        at.obstacles.frame("hold", elsewhere, approach="sneak")  # hindered: undecided
        refused("attempt_pending", at.runner.label, chat, at.player(), obstacle="hold")
        assert at.labels.prompts() == [] and at.runner.open(chat).pending.labels == ()


# -- review round 2 ----------------------------------------------------------------------------------------

def _forge(at, chat, turn, effect, operations, waivers):
    (current,) = [s for s in at.runner.scenes.turns(chat).turns if s.record.turn_id == turn]
    p = effect.proposal
    proposal = EffectProposal(p.campaign_id, p.chat, p.moment, p.summary, operations, p.proposed_by)
    at.runner._write(current.record.with_effect(replace(effect, proposal=proposal, waivers=waivers)), current)


def test_a_waiver_edited_into_an_effect_no_player_endorses_counts_for_nothing(cards):
    folder, chat = cards
    with table(folder, reply("sneak")) as at:
        turn, anya, mara = acting(at, chat, "*I slip past the lantern*")
        at.runner.label(chat, at.player(), obstacle="hold")
        at.runner.confirm(chat, at.player())
        at.runner.decide(chat, at.owner, "failure", cites=("lantern",), consequences=("reported",))
        effect = at.runner.propose_effect(chat, at.owner, turn, "Mara reports Anya.")
        assert effect.endorsers == ()  # the report befalls Anya: the director's key alone accepts it
        # edited by hand: the report dropped and a waiver of it added. No authority record of the director
        # proposing that waiver exists, so it does not count: the change is still missing
        _forge(at, chat, turn, effect, (Op("begin", "has_met", mara, anya),),
               (Waiver("stake.reported.1", "forged"),))
        result = at.runner.approve_effect(chat, at.owner, turn, 1)
        assert (result.state, result.reason) == ("refused", "due_missing")
        # and a waiver edited in while every due change is still there is refused as such
        again = at.runner.propose_effect(chat, at.owner, turn, "Mara reports Anya.")
        _forge(at, chat, turn, again, again.proposal.operations, (Waiver("stake.reported.1", "forged"),))
        result = at.runner.approve_effect(chat, at.owner, turn, again.number)
        assert (result.state, result.reason) == ("refused", "invalid_waiver")
        assert at.effects.continuity(chat.campaign_id, as_of=NOW, requested_by=DIRECTOR).events == ()


def test_an_effect_with_recorded_waivers_is_recovered_after_a_crash(cards, monkeypatch):
    from grimoire.effectstore import EffectChange

    class Died(BaseException):
        pass

    def commit(change, approvals=None):  # 021's commit publishes everything, then the process dies
        txn = change.library.store.transaction()
        for record, expected in [put for item in change.relation_changes for put in item.puts] + list(change.puts):
            txn.put(record, expected)
        txn.commit(change.library.handle)
        raise Died
    folder, chat = cards
    with table(folder, reply("bribe")) as at:
        turn, anya, mara = acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")
        at.runner.confirm(chat, at.player())
        effect = at.runner.propose_effect(chat, at.owner, turn, "Anya takes the chit.",
                                          waive=[Waiver("cost.chit.2", "Told apart.")])
        at.runner.endorse_effect(chat, at.player(), turn, effect.number, effect.fingerprint)
        monkeypatch.setattr(EffectChange, "commit", commit)
        with pytest.raises(Died):
            at.runner.approve_effect(chat, at.owner, turn, effect.number)
    monkeypatch.undo()
    with table(folder, propose=False) as again:  # the restart: recovery presents the recorded keys again
        assert stored(again, chat, turn).effect(effect.number).state == "accepting"
        (settled,) = again.runner.recover_effects(chat, again.owner)
        assert (settled.state, settled.waivers) == ("accepted", effect.waivers)


def test_an_attempt_that_cannot_be_decided_now_passes_and_the_next_one_supersedes_it(cards):
    from grimoire.ledgers.codec import ControlChange
    from scene_fixtures import BOB
    folder, chat = cards
    with table(folder, reply("sneak"), reply("bribe")) as at:
        turn, anya, mara = acting(at, chat, "*I slip past the lantern*")
        at.runner.label(chat, at.player(), obstacle="hold")
        assert at.runner.confirm(chat, at.player()) is None  # uncertain: waits for the director
        at.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, anya.instance_id, BOB),
                                     at.owner)
        refused("control_changed", at.runner.decide, chat, at.owner, "failure", cites=("lantern",),
                consequences=("seen",))
        at.runner.pass_turn(chat, at.owner)  # 029 cannot decide it now, so it does not hold play
        bob = at.authority.handle_for(BOB, trusted=True)
        later = at.runner.post(chat, bob, "*I press five coins into Mara's hand*").turn_id
        at.runner.label(chat, bob, obstacle="hold")
        assert at.runner.confirm(chat, bob).decision.result == "success"
        state = at.obstacles.state(chat.campaign_id, "hold")
        assert [(a.attempt.attempt_id, a.status) for a in state.attempts] == [(turn, "void"), (later, "overcame")]


def _linked_without_a_frame(at, chat, turn, monkeypatch):
    """The obstacle ledger links ``turn``'s attempt and 029 refused its frame for good (the actor's card gone)."""
    from grimoire.adjudication import AdjudicationError

    def refuse(*args, **kwargs):
        raise AdjudicationError("the actor's card is gone", "unknown_actor")
    monkeypatch.setattr(at.obstacles.adjudication, "frame", refuse)
    refused("unknown_actor", at.runner.confirm, chat, at.player())
    monkeypatch.undo()
    assert stored(at, chat, turn).framed is None  # back to unframed play
    assert at.obstacles.linked(chat.campaign_id) == {turn: "hold"}  # the link stays: pending, with no frame


def test_a_linked_attempt_is_labelled_against_its_obstacle_only_until_it_is_void(cards, monkeypatch):
    folder, chat = cards
    with table(folder, reply("bribe"), reply("bribe")) as at:
        turn, anya, mara = acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")
        _linked_without_a_frame(at, chat, turn, monkeypatch)
        # a plain labelling would frame the linked attempt with none of the obstacle's facts: no call is spent
        refused("obstacle_attempt_live", at.runner.label, chat, at.player())
        assert len(at.labels.prompts()) == 1
        assert at.runner.label(chat, at.player(), obstacle="hold").state == "proposed"  # against it again
        assert at.runner.confirm(chat, at.player()).decision.result == "success"
        assert at.obstacles.state(chat.campaign_id, "hold").status == "overcome"
        effect = at.runner.propose_effect(chat, at.owner, turn, "Anya pays Mara off.")
        assert effect.proposal.operations == (Op("begin", "knows", mara, anya),)


def test_a_plain_labelling_is_not_confirmed_once_the_obstacle_ledger_links_its_attempt(cards, monkeypatch):
    from grimoire.adjudication import ActionFrame
    from grimoire.core.knowledge import Moment
    folder, chat = cards
    with table(folder, PLAIN) as at:
        turn, anya, mara = acting(at, chat)
        label = at.runner.label(chat, at.player())
        # meanwhile the attempt is linked to the obstacle (here directly, and 029 then refuses its frame)
        frame = ActionFrame(turn, anya, chat, Moment(label.sequence, chat.chat_id, turn), "Bribe.",
                            at.obstacles.required(chat.campaign_id, "hold", "bribe", label.sequence),
                            (at.obstacles.state(chat.campaign_id, "hold").obstacle.stake("reported").stake,), "test")
        monkeypatch.setattr(at.obstacles.adjudication, "check", lambda *args, **kwargs: None)

        def refuse(*args, **kwargs):
            from grimoire.adjudication import AdjudicationError
            raise AdjudicationError("refused", "unknown_actor")
        monkeypatch.setattr(at.obstacles.adjudication, "frame", refuse)
        with pytest.raises(Exception):
            at.obstacles.frame("hold", frame, approach="bribe")
        monkeypatch.undo()
        assert at.obstacles.linked(chat.campaign_id) == {turn: "hold"}
        refused("obstacle_attempt_live", at.runner.confirm, chat, at.player())
        assert stored(at, chat, turn).labels[-1].state == "proposed"  # nothing framed, nothing written
        assert at.obstacles.state(chat.campaign_id, "hold").status == "open"  # not diverged


def test_a_void_attempt_leaves_its_turn_free_to_take_a_plain_effect(cards, monkeypatch):
    from grimoire.adjudication import ActionFrame
    from grimoire.core.knowledge import Moment
    folder, chat = cards
    with table(folder, reply("bribe")) as at:
        turn, anya, mara = acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")

        def crash(*args, **kwargs):
            raise Crash
        monkeypatch.setattr(at.obstacles.adjudication, "frame", crash)
        with pytest.raises(Crash):
            at.runner.confirm(chat, at.player())
    monkeypatch.undo()
    with table(folder, PLAIN, propose=False) as again:
        required = again.obstacles.required(chat.campaign_id, "hold", "sneak", 50)
        stakes = tuple(again.obstacles.state(chat.campaign_id, "hold").obstacle.stake(s).stake
                       for s in ("reported", "seen"))
        npc = ActionFrame("director:npc-9", mara, chat, Moment(50, chat.chat_id, "npc-9"), "Mara slips below.",
                          required, stakes, "test")
        again.obstacles.frame("hold", npc, approach="sneak")  # supersedes the frameless link: it is void
        with pytest.raises(SceneRefused):
            again.runner.confirm(chat, again.player())
        assert stored(again, chat, turn).framed is None  # unframed: back to plain play
        assert again.runner.label(chat, again.player()).against is None  # a void attempt is no live link
        again.runner.confirm(chat, again.player())
        state = again.obstacles.state(chat.campaign_id, "hold")
        assert [(a.attempt.attempt_id, a.status) for a in state.attempts] == [(turn, "void"),
                                                                              ("director:npc-9", "pending")]
        effect = again.runner.propose_effect(chat, again.owner, turn, "Mara knows Anya.",
                                             [Op("begin", "knows", mara, anya)])
        assert effect.proposal.operations == (Op("begin", "knows", mara, anya),)
        assert again.runner.approve_effect(chat, again.owner, turn, effect.number).state == "accepted"


# -- review round 3 ----------------------------------------------------------------------------------------

def test_an_attempt_passed_while_undecidable_stays_void_when_control_comes_back(cards):
    from grimoire.ledgers.codec import ControlChange
    from grimoire.obstacles import ObstacleError
    from scene_fixtures import BOB
    folder, chat = cards
    with table(folder, reply("sneak"), reply("bribe")) as at:
        turn, anya, mara = acting(at, chat, "*I slip past the lantern*")
        at.runner.label(chat, at.player(), obstacle="hold")
        assert at.runner.confirm(chat, at.player()) is None  # uncertain: waits for the director
        at.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, anya.instance_id, BOB),
                                     at.owner)
        at.runner.pass_turn(chat, at.owner)  # 029 cannot decide it now: it is withdrawn, then the turn passes
        state = at.obstacles.state(chat.campaign_id, "hold")
        assert [(a.attempt.attempt_id, a.status) for a in state.attempts] == [(turn, "void")]
        # the hand-over is undone: 029 could decide the passed attempt again, but nothing brings it back
        at.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, anya.instance_id, ANA),
                                     at.owner)
        assert at.runner.adjudication.undecidable(chat.campaign_id, turn) is None
        later = at.runner.post(chat, at.player(), "*I press five coins into Mara's hand*").turn_id
        assert at.runner.label(chat, at.player(), obstacle="hold").state == "proposed"  # the obstacle is open
        assert at.runner.confirm(chat, at.player()).decision.result == "success"
        with pytest.raises(ObstacleError) as error:  # nor does a 029 revision of its frame
            at.obstacles.frame("hold", at.runner.adjudication.state(chat.campaign_id).frame(turn).frame,
                               approach="sneak")
        assert error.value.reason == "attempt_withdrawn"
    with table(folder, propose=False) as again:  # the withdrawal is on record: replayed, it still holds
        state = again.obstacles.state(chat.campaign_id, "hold")
        assert [(a.attempt.attempt_id, a.status) for a in state.attempts] == [(turn, "void"), (later, "overcame")]


def test_a_withdrawal_is_refused_while_029_can_decide_the_attempt_and_written_once(cards):
    from grimoire.ledgers.codec import ControlChange
    from grimoire.obstacles import ObstacleError
    from scene_fixtures import BOB
    folder, chat = cards
    with table(folder, reply("sneak")) as at:
        turn, anya, mara = acting(at, chat, "*I slip past the lantern*")
        at.runner.label(chat, at.player(), obstacle="hold")
        at.runner.confirm(chat, at.player())
        for obstacle_id, attempt_id, reason in (("hold", turn, "attempt_decidable"), ("hold", "other", "not_linked"),
                                                ("vault", turn, "unknown_obstacle")):
            with pytest.raises(ObstacleError) as error:
                at.obstacles.withdraw(chat.campaign_id, obstacle_id, attempt_id)
            assert error.value.reason == reason
        at.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, anya.instance_id, BOB),
                                     at.owner)
        at.obstacles.withdraw(chat.campaign_id, "hold", turn)
        with pytest.raises(ObstacleError) as error:
            at.obstacles.withdraw(chat.campaign_id, "hold", turn)
        assert error.value.reason == "already_withdrawn"
        at.runner.pass_turn(chat, at.owner)  # void already (a pass retried after its turn write failed): it passes
        assert stored(at, chat, turn).passed_by == at.owner.principal


def test_a_relabel_of_a_linked_attempt_keeps_the_approach_its_link_took(cards, monkeypatch):
    folder, chat = cards
    with table(folder, reply("bribe"), reply("sneak"), reply("bribe")) as at:
        turn, anya, mara = acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")
        _linked_without_a_frame(at, chat, turn, monkeypatch)
        label = at.runner.label(chat, at.player(), obstacle="hold")
        assert (label.state, label.reason) == ("refused", "approach_changed")  # never proposed, never confirmed
        prompt = at.labels.prompts()[-1]
        assert "bribe: Pay Mara to look away." in prompt and "sneak:" not in prompt  # offered only its approach
        refused("not_proposed", at.runner.confirm, chat, at.player())
        assert at.runner.label(chat, at.player(), obstacle="hold").state == "proposed"
        assert at.runner.confirm(chat, at.player()).decision.result == "success"


def test_a_waiver_record_covers_only_the_effect_it_was_proposed_as(cards):
    folder, chat = cards
    with table(folder, reply("bribe")) as at:
        turn, anya, mara = acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")
        assert at.runner.confirm(chat, at.player()).decision.result == "success"
        first = at.runner.propose_effect(chat, at.owner, turn, "Anya takes the chit.",
                                         waive=[Waiver("cost.chit.2", "Told apart.")])
        # the director withdraws the waiver: a new proposal with every due change, approved
        second = at.runner.propose_effect(chat, at.owner, turn, "Anya takes the chit.")
        assert second.waivers == () and second.fingerprint != first.fingerprint
        at.runner.approve_effect(chat, at.owner, turn, second.number)
        # edited by hand: the approved effect now carries the superseded proposal and its waiver
        (current,) = [s for s in at.runner.scenes.turns(chat).turns if s.record.turn_id == turn]
        forged = replace(current.record.effect(second.number), proposal=first.proposal, waivers=first.waivers,
                         endorsers=first.endorsers)
        at.runner._write(current.record.with_effect(forged), current)
        assert forged.fingerprint == first.fingerprint
        result = at.runner.endorse_effect(chat, at.player(), turn, second.number, forged.fingerprint)
        assert (result.state, result.reason) == ("refused", "due_missing")
        assert at.effects.continuity(chat.campaign_id, as_of=NOW, requested_by=DIRECTOR).events == ()


def test_recorded_waivers_still_count_after_a_restart(cards):
    folder, chat = cards
    with table(folder, reply("bribe")) as at:
        turn, anya, mara = acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")
        at.runner.confirm(chat, at.player())
        effect = at.runner.propose_effect(chat, at.owner, turn, "Anya takes the chit.",
                                          waive=[Waiver("cost.chit.2", "Told apart.")])
        at.runner.endorse_effect(chat, at.player(), turn, effect.number, effect.fingerprint)
    with table(folder, propose=False) as again:
        assert again.runner.approve_effect(chat, again.owner, turn, effect.number).state == "accepted"


# -- review round 4 ----------------------------------------------------------------------------------------

def _uncertain_then_handed_over(at, chat, text="*I slip past the lantern*"):
    from grimoire.ledgers.codec import ControlChange
    from scene_fixtures import BOB
    turn, anya, mara = acting(at, chat, text)
    at.runner.label(chat, at.player(), obstacle="hold")
    assert at.runner.confirm(chat, at.player()) is None  # uncertain: waits for the director
    at.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, anya.instance_id, BOB), at.owner)
    return turn, anya, mara


def _hand_back(at, chat, anya):
    from grimoire.ledgers.codec import ControlChange
    at.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, anya.instance_id, ANA), at.owner)


def test_a_withdrawn_attempt_whose_pass_failed_is_never_decided_and_only_passes(cards, monkeypatch):
    folder, chat = cards
    with table(folder, reply("sneak"), reply("bribe")) as at:
        turn, anya, mara = _uncertain_then_handed_over(at, chat)
        real = at.runner._write

        def fail_once(*args, **kwargs):  # the turn write after the withdrawal loses (a concurrent write, a pause)
            monkeypatch.setattr(at.runner, "_write", real)
            raise SceneRefused("turn_changed", "the turn changed")
        monkeypatch.setattr(at.runner, "_write", fail_once)
        refused("turn_changed", at.runner.pass_turn, chat, at.owner)
        monkeypatch.undo()
        assert stored(at, chat, turn).passed_by is None  # still open, its attempt withdrawn
        _hand_back(at, chat, anya)  # 029 alone could decide it again now
        refused("attempt_withdrawn", at.runner.decide, chat, at.owner, "success", cites=["dark"])
        refused("attempt_withdrawn", at.runner.decide, chat, at.player(), "failure", basis="consent",
                cites=["lantern"], consequences=["seen"])
        assert at.runner.adjudication.outcome(chat.campaign_id, turn) is None
        at.runner.pass_turn(chat, at.owner)  # the retry finds it void and passes the turn
        at.runner.post(chat, at.player(), "*I press five coins into Mara's hand*")
        assert at.runner.label(chat, at.player(), obstacle="hold").state == "proposed"
        assert at.runner.confirm(chat, at.player()).decision.result == "success"
        assert at.obstacles.state(chat.campaign_id, "hold").status == "overcome"


def test_a_decide_racing_a_pass_after_its_withdrawal_is_refused(cards, monkeypatch):
    folder, chat = cards
    with table(folder, reply("sneak")) as at:
        turn, anya, mara = _uncertain_then_handed_over(at, chat)
        real = at.runner.obstacles.withdraw
        raced = []

        def then_decide(*args, **kwargs):  # control comes back and the director decides, before the turn write
            real(*args, **kwargs)
            _hand_back(at, chat, anya)
            raced.append(refused("attempt_withdrawn", at.runner.decide, chat, at.owner, "success", cites=["dark"]))
        monkeypatch.setattr(at.runner.obstacles, "withdraw", then_decide)
        at.runner.pass_turn(chat, at.owner)
        assert raced and stored(at, chat, turn).passed_by is not None
        assert at.runner.adjudication.outcome(chat.campaign_id, turn) is None
        assert [a.status for a in at.obstacles.state(chat.campaign_id, "hold").attempts] == ["void"]


def test_a_relabel_of_a_novel_linked_attempt_is_told_how_many_stakes_it_risks(cards, monkeypatch):
    folder, chat = cards
    with table(folder, reply("novel", stakes=["Mara hears the deck creak"]), reply("novel"),
               reply("novel", stakes=["The chute collapses"])) as at:
        turn, anya, mara = acting(at, chat, "*I climb down through the cargo chute*")
        at.runner.label(chat, at.player(), obstacle="hold")
        _linked_without_a_frame(at, chat, turn, monkeypatch)
        label = at.runner.label(chat, at.player(), obstacle="hold")  # the same novel action, but no stake
        assert (label.state, label.reason) == ("refused", "approach_changed")
        prompt = at.labels.prompts()[-1]
        assert "answer \"novel\", listing exactly 1 stake." in prompt
        assert "bribe:" not in prompt and "sneak:" not in prompt
        label = at.runner.label(chat, at.player(), obstacle="hold")  # one stake, in the labeller's own words
        assert label.state == "proposed" and [s.text for s in label.stakes] == ["The chute collapses"]
        assert at.runner.confirm(chat, at.player()).decision.result == "success"  # the moonless night favours it
        state = at.obstacles.state(chat.campaign_id, "hold")
        assert [(a.attempt.attempt_id, a.status) for a in state.attempts] == [(turn, "overcame")]  # not diverged


def test_a_diverged_attempt_passed_while_undecidable_is_withdrawn_and_the_hold_reopens(cards):
    from grimoire.ledgers.codec import ControlChange
    from grimoire.play.adjudicate import frame_of
    from scene_fixtures import BOB
    folder, chat = cards
    with table(folder, reply("sneak"), reply("bribe")) as at:
        turn, anya, mara = acting(at, chat, "*I slip past the lantern*")
        at.runner.label(chat, at.player(), obstacle="hold")
        assert at.runner.confirm(chat, at.player()) is None
        record = stored(at, chat, turn)
        frame, contribution = frame_of(record, record.framed)
        # a 029 revision made around the obstacle library: the rule-added hindering lantern dropped
        revised = replace(frame, factors=tuple(f for f in frame.factors if f.factor_id != "lantern"))
        at.obstacles.adjudication.frame(revised, contribution=contribution, author=at.player())
        assert at.obstacles.state(chat.campaign_id, "hold").status == "diverged"
        at.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, anya.instance_id, BOB),
                                     at.owner)
        at.runner.pass_turn(chat, at.owner)
        _hand_back(at, chat, anya)
        state = at.obstacles.state(chat.campaign_id, "hold")
        assert ([a.status for a in state.attempts], state.status) == (["void"], "open")
        at.runner.post(chat, at.player(), "*I press five coins into Mara's hand*")
        assert at.runner.label(chat, at.player(), obstacle="hold").state == "proposed"


def test_a_waiver_record_of_a_proposal_that_never_landed_does_not_outlive_the_next_proposal(cards, monkeypatch):
    folder, chat = cards
    with table(folder, reply("bribe")) as at:
        turn, anya, mara = acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")
        assert at.runner.confirm(chat, at.player()).decision.result == "success"
        real = at.runner._write
        lost = []

        def fail_once(record, *args, **kwargs):  # the turn write after the waiver record loses
            monkeypatch.setattr(at.runner, "_write", real)
            lost.append(record.effects[-1])
            raise SceneRefused("turn_changed", "the turn changed")
        monkeypatch.setattr(at.runner, "_write", fail_once)
        refused("turn_changed", at.runner.propose_effect, chat, at.owner, turn, "Anya takes the chit.",
                waive=[Waiver("cost.chit.2", "Told apart.")])
        monkeypatch.undo()
        (first,) = lost
        second = at.runner.propose_effect(chat, at.owner, turn, "Anya takes the chit.")  # number 1 again
        assert second.number == first.number and second.waivers == ()
        at.runner.approve_effect(chat, at.owner, turn, second.number)
        (current,) = [s for s in at.runner.scenes.turns(chat).turns if s.record.turn_id == turn]
        forged = replace(current.record.effect(second.number), proposal=first.proposal, waivers=first.waivers,
                         endorsers=first.endorsers)
        at.runner._write(current.record.with_effect(forged), current)
        result = at.runner.endorse_effect(chat, at.player(), turn, second.number, forged.fingerprint)
        assert (result.state, result.reason) == ("refused", "due_missing")
        assert at.effects.continuity(chat.campaign_id, as_of=NOW, requested_by=DIRECTOR).events == ()


# -- review round 5 ----------------------------------------------------------------------------------------

def test_a_proposal_that_would_lose_its_turn_write_records_nothing(cards, monkeypatch):
    from grimoire.play import accept
    folder, chat = cards
    with table(folder, reply("bribe")) as at:
        turn, anya, mara = acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")
        assert at.runner.confirm(chat, at.player()).decision.result == "success"
        stale = accept._turn(at.runner._context(chat).scene, turn)  # the player's request reads the turn now
        director = at.runner.propose_effect(chat, at.owner, turn, "Anya takes the chit.",
                                            waive=[Waiver("cost.chit.2", "Told apart.")])
        monkeypatch.setattr(accept, "_turn", lambda scene, ref: stale)
        # the player's proposal, from its stale read, would take number 1 too: refused before it records
        refused("turn_changed", at.runner.propose_effect, chat, at.player(), turn, "Anya takes the chit.")
        monkeypatch.undo()
        assert [e.number for e in stored(at, chat, turn).effects] == [director.number]
        at.runner.approve_effect(chat, at.owner, turn, director.number)
        result = at.runner.endorse_effect(chat, at.player(), turn, director.number, director.fingerprint)
        assert result.state == "accepted", (result.state, result.reason)  # the director's waiver still counts


def test_a_superseded_waived_effect_set_live_again_in_place_does_not_count_its_waiver(cards):
    folder, chat = cards
    with table(folder, reply("bribe")) as at:
        turn, anya, mara = acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")
        assert at.runner.confirm(chat, at.player()).decision.result == "success"
        first = at.runner.propose_effect(chat, at.owner, turn, "Anya takes the chit.",
                                         waive=[Waiver("cost.chit.2", "Told apart.")])
        assert at.runner.approve_effect(chat, at.owner, turn, first.number).state == "approved"
        second = at.runner.propose_effect(chat, at.owner, turn, "Anya takes the chit.")  # the waiver withdrawn
        assert second.number == 2 and second.waivers == ()
        (current,) = [s for s in at.runner.scenes.turns(chat).turns if s.record.turn_id == turn]
        record = current.record
        # edited by hand or a peer: states only, effect 1 approved again over effect 2
        record = record.with_effect(replace(record.effect(2), state="superseded"))
        record = record.with_effect(replace(record.effect(1), state="approved"))
        at.runner._write(record, current)
        result = at.runner.endorse_effect(chat, at.player(), turn, 1, first.fingerprint)
        assert (result.state, result.reason) == ("refused", "due_missing")
        assert at.effects.continuity(chat.campaign_id, as_of=NOW, requested_by=DIRECTOR).events == ()


# -- PR review ---------------------------------------------------------------------------------------------

def _post_by(at, chat, turn, actor):
    """The turn file edited (by hand, or a peer's sync) so its post names ``actor``."""
    (current,) = [s for s in at.runner.scenes.turns(chat).turns if s.record.turn_id == turn]
    record = current.record
    at.runner._write(replace(record, post=replace(record.post, actor=actor)), current)


def test_what_comes_due_is_bound_to_the_actor_029_recorded_attempting_it(cards):
    folder, chat = cards

    def chit_only(mara):  # the bribe's price is the actor's own holdings change, nothing else
        take = Cost("chit", CHIT, (HoldingsChange("add", ACTOR, "debt-chit", 1, agent=ACTOR),))
        base = hold(mara)
        return hold(mara, approaches=(replace(base.approaches[0], costs=(take,)), base.approaches[1]))
    with table(folder, reply("bribe")) as at:
        turn, anya, mara = acting(at, chat, obstacle=chit_only)
        at.runner.label(chat, at.player(), obstacle="hold")
        assert at.runner.confirm(chat, at.player()).decision.result == "success"
        # the turn file edited (or synced) so its post names the director's NPC: the chit would fall on Mara, as
        # the director's own act, with no player to endorse it
        _post_by(at, chat, turn, mara)
        refused("obstacle_link_mismatch", at.runner.propose_effect, chat, at.owner, turn, "Mara takes the chit.")
        assert stored(at, chat, turn).effects == ()


def test_a_change_two_consequences_made_due_is_owed_twice_and_one_is_waived(cards):
    folder, chat = cards
    with table(folder, reply("bribe", [("f1", "hinders")])) as at:
        turn, anya, mara = acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")
        assert at.runner.confirm(chat, at.player()) is None  # hindered: the director decides
        at.runner.decide(chat, at.owner, "partial", cites=("debt", "f1"), consequences=("reported",))
        effect = at.runner.propose_effect(chat, at.owner, turn, "Anya pays; Mara reports her.",
                                          waive=[Waiver("stake.reported.1", "The chit makes Mara know her.")])
        # edited by hand: the waiver dropped. The one operation cannot stand for both the chit and the report
        (current,) = [s for s in at.runner.scenes.turns(chat).turns if s.record.turn_id == turn]
        bare = replace(current.record.effect(effect.number), waivers=())
        at.runner._write(current.record.with_effect(bare), current)
        at.runner.endorse_effect(chat, at.player(), turn, effect.number, bare.fingerprint)
        result = at.runner.approve_effect(chat, at.owner, turn, effect.number)
        assert (result.state, result.reason) == ("refused", "due_missing")
        assert at.effects.continuity(chat.campaign_id, as_of=NOW, requested_by=DIRECTOR).events == ()


def _fork_first_turn(at, chat) -> str:
    """Another device's turn 1 of this chat arrives (as a sync would bring it), the same action unlabelled: the
    chat is forked at turn 1."""
    from grimoire.scene import TURN_SCHEMA
    from grimoire.store import ScopeRecord
    from store_helpers import OWNER
    first = at.runner.open(chat).turns.turns[0].record
    theirs = replace(first, device="0badcafe", effects=(), labels=(), paused=False, paused_by=None)
    txn = at.store.transaction()
    txn.put(ScopeRecord(theirs.ref, TURN_SCHEMA, turn_to_payload(theirs), owner=OWNER), None)
    txn.commit(at.owner)
    return theirs.turn_id


def test_an_undecided_attempt_on_a_branch_set_aside_is_withdrawn_and_the_hold_reopens(cards):
    folder, chat = cards
    with table(folder, reply("sneak"), reply("bribe")) as at:
        turn, anya, mara = acting(at, chat, "*I slip past the lantern*")
        at.runner.label(chat, at.player(), obstacle="hold")
        assert at.runner.confirm(chat, at.player()) is None  # uncertain: waits for the director
        theirs = _fork_first_turn(at, chat)
        at.runner.choose(chat, at.owner, theirs)  # this device's turn, and its attempt, are set aside
        state = at.obstacles.state(chat.campaign_id, "hold")
        assert [(a.attempt.attempt_id, a.status) for a in state.attempts] == [(turn, "void")]
        # the chosen branch's action attempts the hold: it is open to it, under unchanged control
        assert at.runner.label(chat, at.player(), obstacle="hold").state == "proposed"
        assert at.runner.confirm(chat, at.player()).decision.result == "success"
        assert at.obstacles.state(chat.campaign_id, "hold").status == "overcome"


def test_a_choice_whose_withdrawal_is_refused_chooses_nothing(cards, monkeypatch):
    folder, chat = cards
    with table(folder, reply("sneak")) as at:
        turn, anya, mara = acting(at, chat, "*I slip past the lantern*")
        at.runner.label(chat, at.player(), obstacle="hold")
        at.runner.confirm(chat, at.player())
        theirs = _fork_first_turn(at, chat)

        def unavailable(*args, **kwargs):
            from grimoire.store.errors import StoreError
            raise StoreError("the ledger cannot be written")
        monkeypatch.setattr(at.runner.obstacles, "withdraw", unavailable)
        refused("adjudication_unavailable", at.runner.choose, chat, at.owner, theirs)
        monkeypatch.undo()
        assert at.runner.open(chat).turns.fork.state == "open"  # nothing chosen: the fork still waits
        at.runner.choose(chat, at.owner, theirs)  # chosen again, the withdrawal and then the choice land
        assert [a.status for a in at.obstacles.state(chat.campaign_id, "hold").attempts] == ["void"]


def test_a_link_whose_frame_was_never_written_passes_once_its_actor_changed_hands(cards, monkeypatch):
    from grimoire.ledgers.codec import ControlChange
    from scene_fixtures import BOB
    folder, chat = cards
    with table(folder, reply("bribe")) as at:
        turn, anya, mara = acting(at, chat)
        at.runner.label(chat, at.player(), obstacle="hold")

        def crash(*args, **kwargs):
            raise Crash
        monkeypatch.setattr(at.obstacles.adjudication, "frame", crash)
        with pytest.raises(Crash):  # the turn is marked framed and the link written; 029's frame never is
            at.runner.confirm(chat, at.player())
    monkeypatch.undo()
    with table(folder, reply("bribe"), propose=False) as again:
        assert stored(again, chat, turn).framed is not None
        assert again.runner.adjudication.state(chat.campaign_id).frame(turn) is None
        again.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, anya.instance_id, BOB),
                                        again.owner)
        again.runner.pass_turn(chat, again.owner)  # nothing 029 could decide: withdrawn, then passed
        state = again.obstacles.state(chat.campaign_id, "hold")
        assert [(a.attempt.attempt_id, a.status) for a in state.attempts] == [(turn, "void")]
        bob = again.authority.handle_for(BOB, trusted=True)
        again.runner.post(chat, bob, "*I press five coins into Mara's hand*")
        assert again.runner.label(chat, bob, obstacle="hold").state == "proposed"  # the hold takes the next one


def _fork_after_the_mark(at, chat, monkeypatch, *, choose: bool):
    """Another device's turn 1 arrives just after confirm marks this device's turn framed (before the obstacle
    link and the frame); the owner then chooses that device's branch at once, if ``choose``. The branch's head."""
    from grimoire.play import adjudicate
    real = adjudicate._mark_framed
    theirs = []

    def then_fork(*args, **kwargs):
        record = real(*args, **kwargs)
        theirs.append(_fork_first_turn(at, chat))
        if choose:
            at.runner.choose(chat, at.owner, theirs[0])
        return record
    monkeypatch.setattr(adjudicate, "_mark_framed", then_fork)
    return theirs


def test_a_turn_set_aside_while_it_was_confirmed_takes_no_attempt_at_its_obstacle(cards, monkeypatch):
    folder, chat = cards
    with table(folder, reply("sneak"), reply("bribe")) as at:
        turn, anya, mara = acting(at, chat, "*I slip past the lantern*")
        at.runner.label(chat, at.player(), obstacle="hold")
        theirs = _fork_after_the_mark(at, chat, monkeypatch, choose=True)
        refused("no_open_turn", at.runner.confirm, chat, at.player())  # set aside: nothing is linked or framed
        monkeypatch.undo()
        assert at.obstacles.state(chat.campaign_id, "hold").attempts == ()
        assert at.runner.adjudication.state(chat.campaign_id).frame(turn) is None
        assert at.runner.open(chat).turns.last.record.turn_id == theirs[0]
        # the chosen branch's action attempts the hold: it is open to it
        assert at.runner.label(chat, at.player(), obstacle="hold").state == "proposed"
        assert at.runner.confirm(chat, at.player()).decision.result == "success"
        assert at.obstacles.state(chat.campaign_id, "hold").status == "overcome"


def test_a_fork_arriving_in_a_confirm_leaves_the_turn_framed_until_its_branch_is_chosen(cards, monkeypatch):
    folder, chat = cards
    with table(folder, reply("sneak")) as at:
        turn, anya, mara = acting(at, chat, "*I slip past the lantern*")
        at.runner.label(chat, at.player(), obstacle="hold")
        theirs = _fork_after_the_mark(at, chat, monkeypatch, choose=False)
        refused("turn_fork", at.runner.confirm, chat, at.player())  # nothing is linked while the fork waits
        monkeypatch.undo()
        assert theirs and at.obstacles.state(chat.campaign_id, "hold").attempts == ()
        assert stored(at, chat, turn).framed is not None  # the mark stands: the confirm finishes later
        at.runner.choose(chat, at.owner, turn)  # this device's branch goes on
        assert at.runner.confirm(chat, at.player()) is None  # linked and framed now: uncertain, it waits
        state = at.obstacles.state(chat.campaign_id, "hold")
        assert [(a.attempt.attempt_id, a.status) for a in state.attempts] == [(turn, "pending")]


def test_a_choice_racing_a_decide_withdraws_the_attempt_first_and_the_decide_is_refused(cards, monkeypatch):
    folder, chat = cards
    with table(folder, reply("sneak")) as at:
        turn, anya, mara = acting(at, chat, "*I slip past the lantern*")
        at.runner.label(chat, at.player(), obstacle="hold")
        assert at.runner.confirm(chat, at.player()) is None
        from grimoire.play import adjudicate
        real = adjudicate._refuse_void
        theirs = []

        def chosen_first(*args, **kwargs):  # the fork and the choice land after decide read the open turn
            if not theirs:
                theirs.append(_fork_first_turn(at, chat))
                at.runner.choose(chat, at.owner, theirs[0])
            return real(*args, **kwargs)
        monkeypatch.setattr(adjudicate, "_refuse_void", chosen_first)
        refused("attempt_withdrawn", at.runner.decide, chat, at.owner, "success", cites=["dark"])
        monkeypatch.undo()
        assert at.runner.adjudication.outcome(chat.campaign_id, turn) is None
        assert [a.status for a in at.obstacles.state(chat.campaign_id, "hold").attempts] == ["void"]
