"""035 milestone 2: labelling a player character's action, through the runner, one rule at a time.

Over the synthetic card library (``scene_fixtures``): Mara Vell's card chat with Anya Rook as the player's
persona. The labeller is a scripted fake on its own untrusted provider handle; every attempt needs an
approval, and every labelling that holds anything read from a reply must be proposable by the library's
untrusted label handle before the owner's handle writes it.
"""

from __future__ import annotations

import json
import shutil

import pytest

from generation_fixtures import say
from scene_fixtures import ADJUDICATOR, ANA, BOB, LABEL_POLICY, LABELLER, LIGHTHOUSE, POLICY, Table, card_library
from store_helpers import OWNER

from grimoire.core.inference import InferenceStage
from grimoire.ledgers.codec import ControlChange
from grimoire.play import SceneRunner
from grimoire.providers import RouteChoice
from grimoire.runtime import LabelRuntime
from grimoire.scene import MAX_LABELS, SceneRefused
from grimoire.testing.providers import ConnectFail, Status

REPLY = json.dumps({"factors": [{"fact": "f1", "bearing": "favours"}], "stakes": ["She falls to the deck"]})


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


def acting(table: Table, chat, text: str = "*I climb the rigging*"):
    table.runner.seat(chat, ANA)
    return table.runner.post(chat, table.player(), text)


def labelled(folder, *replies, propose: bool = True, policy=LABEL_POLICY) -> Table:
    return Table(folder, policy=policy, labels=[say(reply) for reply in replies], propose=propose)


# -- a proposal -----------------------------------------------------------------------------------

def test_a_labelling_cites_only_the_acting_characters_own_facts_and_is_written_as_a_proposal(cards):
    folder, chat = cards
    with labelled(folder, REPLY) as table:
        acting(table, chat)
        label = table.runner.label(chat, table.player())
        assert (label.label, label.state, label.by, label.reason) == (1, "proposed", ANA, None)
        (factor,) = label.factors
        assert (factor.factor_id, factor.bearing) == ("f1", "favours")
        assert factor.text == "A harbor pilot who knows every channel."
        assert factor.source.startswith("description:card:anya-rook/") and factor.established < label.sequence
        assert [(stake.stake_id, stake.text) for stake in label.stakes] == [("s1", "She falls to the deck")]
        (call,) = label.calls
        assert (call.stage, call.actor_id, call.status) == (InferenceStage.LABELLING, None, "accepted")
        (attempt,) = label.attempts()
        assert attempt.call_id.turn_id.endswith(".l1") and attempt.usage.output_tokens > 0
        assert len(table.labels.approved) == 1  # the labeller's one attempt was approved
        (prompt,) = table.labels.prompts()
        assert "Action: I climb the rigging" in prompt and "f1: A harbor pilot who knows every channel." in prompt
        assert table.runner.open(chat).pending.labels == (label,)
    with Table(folder, policy=LABEL_POLICY) as again:  # it round-trips through the turn file
        assert again.runner.open(chat).pending.labels == (label,)


def test_a_labelling_offers_nothing_the_player_may_not_see(cards):
    folder, chat = cards
    with labelled(folder, REPLY) as table:
        acting(table, chat, '*I squint at the lighthouse and ask whether it is still dark*')
        table.runner.label(chat, table.player())
        (prompt,) = table.labels.prompts()
    offered = prompt.split("Facts:\n", 1)[1].splitlines()
    assert any(LIGHTHOUSE in line for line in offered)  # triggered lore is everyone's
    assert all("Mara" not in line for line in offered)  # the NPC's own card is not the player's to cite


def test_the_owner_may_ask_for_labels_and_another_player_may_not(cards):
    folder, chat = cards
    with labelled(folder, REPLY) as table:
        acting(table, chat)
        scene = table.runner.open(chat)
        (mara,) = [actor for actor in scene.cast if actor != scene.persona]
        table.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, mara.instance_id, BOB),
                                        table.owner)  # Bob plays Mara: at the table, but not Anya's player
        refused("not_controller", table.runner.label, chat, table.player(BOB))
        assert table.runner.label(chat, table.owner).by == OWNER


# -- what is never labelled -----------------------------------------------------------------------

def test_nothing_is_labelled_without_a_labeller_or_a_label_handle(cards):
    folder, chat = cards
    with Table(folder, policy=LABEL_POLICY) as table:
        acting(table, chat)
        refused("no_labeller", table.runner.label, chat, table.player())


def test_only_the_open_turns_action_is_labelled(cards):
    folder, chat = cards
    with labelled(folder, REPLY) as table:
        table.runner.seat(chat, ANA)
        refused("not_an_action", table.runner.label, chat, table.player())  # no turn at all
        table.runner.post(chat, table.player(), '"Is the cargo safe?"')
        refused("not_an_action", table.runner.label, chat, table.player())
        assert table.labels.transport.sends == []


def test_a_policy_without_labelling_plans_no_call(cards):
    folder, chat = cards
    with labelled(folder, REPLY, policy=POLICY) as table:
        acting(table, chat)
        refused("labelling_declined", table.runner.label, chat, table.player())
        assert table.runner.open(chat).pending.labels == ()


def test_an_action_is_labelled_at_most_max_labels_times(cards):
    folder, chat = cards
    with labelled(folder, *[REPLY] * MAX_LABELS) as table:
        acting(table, chat)
        for _ in range(MAX_LABELS):
            table.runner.label(chat, table.player())
        refused("too_many_labels", table.runner.label, chat, table.player())
        labels = table.runner.open(chat).pending.labels
        assert [label.state for label in labels] == ["superseded"] * (MAX_LABELS - 1) + ["proposed"]
        # each labelling plans its own turn, so no two share a meter
        assert len({label.attempts()[0].call_id.turn_id for label in labels}) == MAX_LABELS


# -- a reply that is not labels -------------------------------------------------------------------

@pytest.mark.parametrize(("reply", "reason"), [
    ("The rigging is wet, so it is hard.", "unparseable"),
    (json.dumps({"factors": [{"fact": "f99", "bearing": "hinders"}], "stakes": []}), "unknown_fact"),
    (json.dumps({"factors": [{"fact": "f1", "bearing": "hinders", "text": "The mast is rotten"}]}), "unparseable"),
    (json.dumps({"factors": [], "stakes": [""]}), "invalid_stake"),
    (json.dumps({"factors": [], "stakes": [], "result": "success"}), "unparseable"),
])
def test_a_reply_that_is_not_labels_is_refused_whole_and_its_usage_kept(cards, reply, reason):
    folder, chat = cards
    with labelled(folder, reply) as table:
        acting(table, chat)
        label = table.runner.label(chat, table.player())
        assert (label.state, label.reason, label.factors, label.stakes) == ("refused", reason, (), ())
        (call,) = label.calls
        assert call.status == "rejected" and len(call.attempts) == 1


@pytest.mark.parametrize("step", [Status(500), ConnectFail()])
def test_a_failed_call_is_recorded_refused_with_its_status(cards, step):
    folder, chat = cards
    with Table(folder, policy=LABEL_POLICY, labels=[step]) as table:
        acting(table, chat)
        label = table.runner.label(chat, table.player())
        assert label.state == "refused" and label.reason and label.calls[0].status == "failed"
        assert table.runner.open(chat).pending.labels == (label,)


def test_a_labelling_the_label_handle_may_not_propose_is_refused(cards):
    folder, chat = cards
    with labelled(folder, REPLY, propose=False) as table:
        acting(table, chat)
        label = table.runner.label(chat, table.player())
        assert (label.state, label.reason, label.factors) == ("refused", "not_authorized", ())
        assert label.calls[0].status == "accepted"  # the call's usage is still recorded


# -- relabelling ----------------------------------------------------------------------------------

def test_a_relabelling_supersedes_the_unconfirmed_one(cards):
    folder, chat = cards
    second = json.dumps({"factors": [{"fact": "f1", "bearing": "hinders"}], "stakes": []})
    with labelled(folder, REPLY, second) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        latest = table.runner.label(chat, table.player())
        first, now = table.runner.open(chat).pending.labels
        assert (first.state, first.factors[0].bearing) == ("superseded", "favours")
        assert now == latest and (now.label, now.state, now.factors[0].bearing) == (2, "proposed", "hinders")


def test_a_labelling_superseded_while_its_call_ran_keeps_only_its_usage(cards):
    folder, chat = cards
    with labelled(folder, REPLY, REPLY) as table:
        acting(table, chat)
        inner: list = []

        def approvals(label, attempt, payload):  # another press lands while the first call is out
            approval = table.labels.approvals(label, attempt, payload)
            if not inner:
                inner.append(None)
                inner.append(table.runner.label(chat, table.player()))
            return approval

        table.runner.labeller = LabelRuntime(table.labels.adapter, (RouteChoice("primary", "model-a"),),
                                             table.labels.handle, approvals=approvals)
        outer = table.runner.label(chat, table.player())
        assert (outer.label, outer.state, outer.factors) == (1, "superseded", ())
        assert outer.calls[0].status == "accepted" and outer.attempts()
        first, second = table.runner.open(chat).pending.labels
        assert first == outer and second == inner[1] and second.state == "proposed"


# -- the runner's handles -------------------------------------------------------------------------

def test_the_label_handle_is_an_untrusted_handle_the_librarys_authority_issued(cards):
    folder, _ = cards
    with labelled(folder) as table:
        for handle in (table.authority.handle_for(LABELLER, trusted=True), table.labels.handle):
            refused("bad_handle", SceneRunner, table.store, table.authority, table.owner, owner=OWNER,
                    label_handle=handle)
        refused("bad_labeller", SceneRunner, table.store, table.authority, table.owner, owner=OWNER,
                labeller=object())


# -- confirming and deciding (the requester's call: the player confirms, unedited) -----------------

HINDERED = json.dumps({"factors": [{"fact": "f1", "bearing": "favours"}, {"fact": "f2", "bearing": "hinders"}],
                       "stakes": ["She falls to the deck"]})
PREVENTED = json.dumps({"factors": [{"fact": "f1", "bearing": "prevents"}], "stakes": []})
UNOPPOSED = json.dumps({"factors": [{"fact": "f1", "bearing": "favours"}], "stakes": []})


def judged(folder, *replies, steps=()) -> Table:
    return Table(folder, steps, policy=LABEL_POLICY, labels=[say(reply) for reply in replies], adjudicate=True)


def test_the_player_confirms_the_labels_unedited_and_the_rules_decide_an_unopposed_action(cards):
    folder, chat = cards
    with judged(folder, UNOPPOSED) as table:
        acting(table, chat)
        label = table.runner.label(chat, table.player())
        outcome = table.runner.confirm(chat, table.player())
        assert (outcome.classification, outcome.decision.result, outcome.decision.basis) == (
            "straightforward", "success", "rule")
        assert outcome.frame.factors == label.factors and outcome.frame.stakes == label.stakes
        assert outcome.frame.moment.sequence == label.sequence
        assert outcome.decided_by == ADJUDICATOR  # the app's key: never the labeller's, never the player's
        assert table.runner.open(chat).pending.framed.label == 1
        assert table.runner.outcome(chat) == outcome
        refused("already_framed", table.runner.confirm, chat, table.player())
        refused("already_framed", table.runner.label, chat, table.player())


def test_a_prevented_action_fails_by_rule_citing_what_prevents_it(cards):
    folder, chat = cards
    with judged(folder, PREVENTED) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        outcome = table.runner.confirm(chat, table.player())
        assert (outcome.classification, outcome.decision.result, outcome.decision.cites) == (
            "impossible", "failure", frozenset({"f1"}))


def test_only_the_acting_player_confirms(cards):
    folder, chat = cards
    with judged(folder, HINDERED) as table:
        acting(table, chat)
        refused("not_proposed", table.runner.confirm, chat, table.player())  # nothing labelled yet
        table.runner.label(chat, table.player())
        refused("not_controller", table.runner.confirm, chat, table.owner)  # the director never confirms for her
        assert table.adjudication.state(chat.campaign_id).frame(table.runner.open(chat).pending.turn_id) is None


def test_a_refused_or_superseded_labelling_is_never_confirmed(cards):
    folder, chat = cards
    with judged(folder, "not json", UNOPPOSED) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        refused("not_proposed", table.runner.confirm, chat, table.player())
        table.runner.label(chat, table.player())  # the relabel is the only fix
        assert table.runner.confirm(chat, table.player()).frame.provenance.startswith("035 labelling 2")


def test_nothing_is_framed_without_an_adjudication_key(cards):
    folder, chat = cards
    with labelled(folder, UNOPPOSED) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        refused("no_adjudication", table.runner.confirm, chat, table.player())


def test_an_uncertain_action_waits_and_no_narration_is_written_or_kept_until_it_is_decided(cards):
    folder, chat = cards
    with judged(folder, HINDERED, steps=[say("Mara looks up."), say("Mara steadies the line.")]) as table:
        acting(table, chat)
        early = table.runner.generate(chat, table.player())  # before framing, play goes on
        table.runner.label(chat, table.player())
        assert table.runner.confirm(chat, table.player()) is None
        refused("undecided_attempt", table.runner.generate, chat, table.player())
        refused("undecided_attempt", table.runner.keep, chat, table.player(), early.take)
        refused("not_director", table.runner.decide, chat, table.player(), "partial", cites=("f1", "f2"),
                consequences=("s1",))
        refused("uncited_result", table.runner.decide, chat, table.owner, "success")  # 029's own rules
        outcome = table.runner.decide(chat, table.owner, "partial", cites=("f1", "f2"), consequences=("s1",))
        assert (outcome.decision.basis, outcome.decision.result) == ("adjudicated", "partial")
        take = table.runner.generate(chat, table.player())
        prompt = table.harness.prompts()[-1]
        assert "Outcome: Anya Rook's action (I climb the rigging) succeeds at a cost." in prompt
        assert "She falls to the deck" in prompt
        table.runner.keep(chat, table.player(), take.take)


def test_the_player_may_consent_to_a_setback_for_their_own_action(cards):
    folder, chat = cards
    with judged(folder, HINDERED) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        table.runner.confirm(chat, table.player())
        refused("not_controller", table.runner.decide, chat, table.owner, "failure", basis="consent")
        outcome = table.runner.decide(chat, table.player(), "failure", basis="consent", consequences=("s1",))
        assert (outcome.decision.basis, outcome.consented_by) == ("consent", ANA)


def test_an_action_that_may_carry_a_private_message_is_not_labelled(cards):
    folder, chat = cards
    with judged(folder, UNOPPOSED) as table:
        acting(table, chat, "*I whisper the vault code 1234 under my breath*")
        assert table.runner.open(chat).pending.post.private
        refused("private_action", table.runner.label, chat, table.player())


def test_the_adjudication_key_must_be_this_librarys(cards, tmp_path):
    folder, _ = cards
    with judged(folder) as table:
        refused("bad_adjudication", SceneRunner, table.store, table.authority, table.owner, owner=OWNER,
                adjudication=object())
