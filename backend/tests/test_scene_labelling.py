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

from types import SimpleNamespace

import grimoire.context.actor as packer
from grimoire.adjudication import AdjudicationError
from grimoire.core import DIRECTOR, ControlAssignment
from grimoire.core.authority import PlayerContribution
from grimoire.core.knowledge import Belief, Event, Moment, Perception, Proposition, Subject
from grimoire.core.identity import InstanceRef
from grimoire.adjudicationstore import AdjudicationLibrary
from grimoire.evidencestore import EvidenceLibrary
from grimoire.core.inference import InferenceStage
from grimoire.generation import MAX_LINE_CHARS
from grimoire.play.adjudicate import outcome_line
from grimoire.store.errors import AuthorityUnavailable, LedgerInvalid
from grimoire.ledgers.codec import ControlChange
from grimoire.play import SceneRunner
from grimoire.play.stages import StageBook
from grimoire.providers import RouteChoice
from grimoire.runtime import LabelRuntime
from grimoire.scene import MAX_LABELS, SceneRefused
from grimoire.staging import Area, Placement, Stage, Way
from grimoire.testing.providers import ConnectFail, Status
from grimoire.tracing import TraceLog

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


def test_narration_reaches_the_table_but_never_the_labeller(built, tmp_path):
    folder, _, storm = built
    copy = tmp_path / "storm"
    shutil.copytree(folder, copy)
    unseen = "Unseen by Anya, someone slips into the lighthouse."
    with Table(copy, [say(unseen)], policy=LABEL_POLICY, labels=[say(REPLY)]) as table:
        table.runner.seat(storm, ANA)
        table.runner.post(storm, table.player(), '"Hello?"')
        (line,) = table.runner.generate(storm, table.player(), director=True).lines
        assert line.actor is None and line.audience == (table.runner.open(storm).persona,)  # the table's
        table.runner.keep(storm, table.player(), 1)
        acting(table, storm)
        table.runner.label(storm, table.player())
        (prompt,) = table.labels.prompts()
    assert "Hello?" in prompt and unseen not in prompt  # the character's window: its player saw it, it did not


# -- packed for the character's player (031) -------------------------------------------------------

GUILD = "The guild meets above the fish market."
#: Anya's three conclusions, each resting on her seeing the lamps go out: one the director drew while she
#: was an NPC, one Bob drew while he played her, and one Ana drew. Only Ana's is Ana's to cite.
INHERITED, PREVIOUS, OWN = ("the guild paid the lamplighter", "the lighthouse keeper took a bribe",
                            "the lamps were put out on purpose")


def remembers(table: Table, chat, *, controller: str = ANA) -> None:
    """Anya saw the harbor lamps go out (sequence 1), and holds the three conclusions (sequence 2)."""
    scene = table.runner.open(chat)
    anya, campaign = scene.persona, chat.campaign_id
    evidence = EvidenceLibrary(table.store, table.owner, owner=OWNER)
    lamps = (Subject("event", "e-lamps"),)
    evidence.append(campaign, [
        Event(campaign, "e-lamps", "the harbor lamps go out", Moment(1), "director"),
        Perception(campaign, "v-lamps", anya, "e-lamps", "witnessed", "the harbor lamps going dark", Moment(1),
                   "director", confirmed=True),
        *[Proposition(campaign, f"p-{n}", text, Moment(1), "director", about=lamps)
          for n, text in enumerate((INHERITED, PREVIOUS, OWN))],
    ]).commit()

    def concluded(n: int, author: str) -> None:
        moment = Moment(2, "scene-quay", f"t-{n}")
        held = Belief(campaign, f"b-{n}", anya, f"p-{n}", "believed", moment, author, ("v-lamps",),
                      contribution_id=None if author == DIRECTOR else f"k-{n}")
        contributions = {} if author == DIRECTOR else {held.belief_id: PlayerContribution(
            campaign, f"k-{n}", "scene-quay", f"t-{n}", anya.instance_id, author, "interiority",
            (INHERITED, PREVIOUS, OWN)[n])}
        evidence.append(campaign, [held], controls=[ControlAssignment(campaign, anya.instance_id, author)],
                        contributions=contributions).commit()

    concluded(0, DIRECTOR)  # while she was an NPC
    concluded(1, BOB)  # while Bob played her
    concluded(2, controller)


def offered(table: Table) -> list[str]:
    (prompt,) = table.labels.prompts()
    return prompt.split("Facts:\n", 1)[1].splitlines()


def test_the_labeller_is_offered_only_what_the_characters_player_may_see(cards):
    folder, chat = cards
    with labelled(folder, REPLY) as table:
        remembers(table, chat)
        acting(table, chat)  # Ana takes Anya: nothing in the scene names the guild or the lighthouse
        table.runner.label(chat, table.player())
        facts = offered(table)
    assert any("the harbor lamps going dark" in line for line in facts)  # what Anya witnessed
    assert any(OWN in line for line in facts)  # what Ana concluded for her
    assert not any(INHERITED in line or PREVIOUS in line for line in facts)
    # neither conclusion that is not Ana's triggers lore through her character's pack
    assert not any(GUILD in line or LIGHTHOUSE in line for line in facts)


def test_another_players_conclusion_for_the_same_character_stays_theirs(cards):
    folder, chat = cards
    with labelled(folder, REPLY) as table:
        remembers(table, chat, controller=BOB)  # Bob drew two of the three; Ana plays Anya now
        acting(table, chat)
        table.runner.label(chat, table.player())
        facts = offered(table)
    assert not any(OWN in line or PREVIOUS in line or LIGHTHOUSE in line for line in facts)
    assert any("the harbor lamps going dark" in line for line in facts)


def test_a_players_conclusion_the_director_revised_meanwhile_is_still_offered(cards):
    folder, chat = cards
    with labelled(folder, REPLY) as table:
        remembers(table, chat)
        anya, campaign = table.runner.open(chat).persona, chat.campaign_id
        # while Anya was the director's again, the director revised Ana's conclusion into a doubt
        EvidenceLibrary(table.store, table.owner, owner=OWNER).append(campaign, [
            Belief(campaign, "b-revised", anya, "p-2", "suspected", Moment(3), DIRECTOR, ("v-lamps",),
                   supersedes="b-2")], controls=[ControlAssignment(campaign, anya.instance_id, DIRECTOR)]).commit()
        acting(table, chat)  # Ana plays her again
        table.runner.label(chat, table.player())
        facts = offered(table)
    assert any(f"You believe: {OWN}" in line for line in facts)  # Ana's own latest conclusion, as her own


def test_knowledge_that_cannot_be_projected_is_not_labelled(cards, monkeypatch):
    folder, chat = cards
    with labelled(folder, REPLY) as table:
        acting(table, chat)
        monkeypatch.setattr(packer, "controller_projection", lambda *args, **kwargs: None)  # 020 refuses it
        refused("knowledge_unprojected", table.runner.label, chat, table.player())
        assert table.runner.open(chat).pending.labels == () and table.labels.transport.sends == []


def test_a_withdrawn_line_is_never_in_the_labellers_window_or_lore(cards):
    folder, chat = cards
    with Table(folder, [say("The lighthouse key hangs by the door.")], policy=LABEL_POLICY,
               labels=[say(REPLY)]) as table:
        table.runner.seat(chat, ANA)
        table.runner.post(chat, table.player(), '"Where is the key?"')
        take = table.runner.generate(chat, table.player())
        table.runner.keep(chat, table.player(), take.take)
        table.runner.post(chat, table.player(), "skip")  # 016: the kept line is withdrawn
        acting(table, chat)
        table.runner.label(chat, table.player())
        (prompt,) = table.labels.prompts()
    assert "Where is the key?" in prompt and "lighthouse key" not in prompt and LIGHTHOUSE not in prompt


def test_a_line_spoken_behind_a_shut_door_is_never_in_the_labellers_window(cards):
    folder, chat = cards
    book = StageBook()
    with Table(folder, [say("The lighthouse key hangs by the door.")], policy=LABEL_POLICY, labels=[say(REPLY)],
               stages=book) as table:
        table.runner.seat(chat, ANA)
        scene = table.runner.open(chat)
        anya = scene.persona
        (mara,) = [actor for actor in scene.cast if actor != anya]
        book.put(chat, Stage(chat.campaign_id, chat.chat_id, 1, (Area("study"), Area("hall")),
                             (Way("study-door", ("study", "hall"), "closed"),), (anya, mara),
                             (Placement(anya, area="hall"), Placement(mara, area="study"))))
        table.runner.post(chat, table.player(), '"Hello?"')
        take = table.runner.generate(chat, table.player())
        assert take.lines[0].audience == (mara,)
        table.runner.keep(chat, table.player(), take.take)
        acting(table, chat)
        table.runner.label(chat, table.player())
        (prompt,) = table.labels.prompts()
    assert "Hello?" in prompt and "lighthouse key" not in prompt and LIGHTHOUSE not in prompt


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
        refused("not_proposed", table.runner.keep, chat, table.player(), early.take)  # withdrawn at confirm
        refused("not_director", table.runner.decide, chat, table.player(), "partial", cites=("f1", "f2"),
                consequences=("s1",))
        refused("uncited_result", table.runner.decide, chat, table.owner, "success")  # 029's own rules
        outcome = table.runner.decide(chat, table.owner, "partial", cites=("f1", "f2"), consequences=("s1",))
        assert (outcome.decision.basis, outcome.decision.result) == ("adjudicated", "partial")
        # The take written before the confirm never heard the outcome, so it stays withdrawn once decided.
        refused("not_proposed", table.runner.keep, chat, table.player(), early.take)
        withdrawn = next(t for t in table.runner.open(chat).pending.takes if t.take == early.take)
        assert withdrawn.state == "withdrawn" and not withdrawn.shown()
        take = table.runner.generate(chat, table.player())
        prompt = table.harness.prompts()[-1]
        # An NPC who saw the action hears the bare result: its causes are Anya's own facts, its stakes the
        # labeller's words, and her name is the action's own line's to give.
        assert "Anya Rook: *I climb the rigging*\nOutcome: The action succeeds at a cost.\n" in prompt
        assert "She falls to the deck" not in prompt and "Because" not in prompt
        table.runner.keep(chat, table.player(), take.take)


def test_a_run_in_flight_when_the_action_is_confirmed_lands_stale_and_is_never_kept(cards, monkeypatch):
    folder, chat = cards
    with judged(folder, UNOPPOSED, steps=[say("You reach the top."), say("The rope holds.")]) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        run_turn, decided = table.harness.runtime.run_turn, []

        def confirmed_meanwhile(*args, **kwargs):
            outcome = run_turn(*args, **kwargs)
            if not decided:  # the player confirms while the take's call is out; the rules decide it
                decided.append(table.runner.confirm(chat, table.player()))
            return outcome

        monkeypatch.setattr(table.harness.runtime, "run_turn", confirmed_meanwhile)
        early = table.runner.generate(chat, table.player())
        assert decided[0].decision.result == "success"
        assert early.state == "stale" and early.lines == ()  # written without the outcome: dropped
        refused("not_proposed", table.runner.keep, chat, table.player(), early.take)
        take = table.runner.generate(chat, table.player())
        assert "Outcome: The action succeeds." in table.harness.prompts()[-1]
        table.runner.keep(chat, table.player(), take.take)


def test_a_decided_outcome_stays_where_it_was_heard_in_later_turns(cards):
    folder, chat = cards
    steps = [say("Mara nods."), say("Mara answers."), say("The storm builds.")]
    with judged(folder, UNOPPOSED, steps=steps) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        table.runner.confirm(chat, table.player())
        table.runner.keep(chat, table.player(), table.runner.generate(chat, table.player()).take)
        table.runner.post(chat, table.player(), '"Did you see that?"')
        table.runner.generate(chat, table.player())
        npc = table.harness.prompts()[-1]
        assert "Anya Rook: *I climb the rigging*\nOutcome: The action succeeds.\nMara Vell: Mara nods." in npc
        assert "A harbor pilot" not in npc.split("Outcome:", 1)[1].split("\n", 1)[0]  # still the bare result


def test_the_player_may_consent_to_a_setback_for_their_own_action(cards):
    folder, chat = cards
    with judged(folder, HINDERED) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        table.runner.confirm(chat, table.player())
        shown = table.runner.framed(chat).fingerprint
        refused("not_controller", table.runner.decide, chat, table.owner, "failure", basis="consent",
                fingerprint=shown)
        outcome = table.runner.decide(chat, table.player(), "failure", basis="consent", consequences=("s1",),
                                      fingerprint=shown)
        assert (outcome.decision.basis, outcome.consented_by) == ("consent", ANA)


# -- consent is the player's own live choice, for the frame they were shown (029) --------------------

def framed_hindered(table: Table, chat, text: str = "*I climb the rigging*"):
    """Post, label and confirm an uncertain action; what the table is shown of its frame."""
    table.runner.post(chat, table.player(), text)
    table.runner.label(chat, table.player())
    assert table.runner.confirm(chat, table.player()) is None
    return table.runner.framed(chat)


def test_the_framed_action_is_read_from_029_with_the_fingerprint_a_consent_names(cards):
    from grimoire.play.adjudicate import frame_fingerprint
    folder, chat = cards
    with judged(folder, HINDERED) as table:
        table.runner.seat(chat, ANA)
        assert table.runner.framed(chat) is None  # nothing posted, nothing framed
        table.runner.post(chat, table.player(), "*I climb the rigging*")
        table.runner.label(chat, table.player())
        assert table.runner.framed(chat) is None  # labelled, not yet confirmed
        assert table.runner.confirm(chat, table.player()) is None
        shown = table.runner.framed(chat)
        record = table.runner.open(chat).pending
        entry = table.adjudication.state(chat.campaign_id).frame(record.turn_id)
        assert shown.frame == entry.frame and shown.assessment.classification == "uncertain"
        assert shown.fingerprint == frame_fingerprint(entry.frame, entry.contribution, entry.revision)
        assert shown.fingerprint.startswith("sha256:") and len(shown.fingerprint) == 71
    log = TraceLog()
    with Table(folder, policy=LABEL_POLICY, adjudicate=True, tracer=log.record) as again:  # the same after a restart
        assert again.runner.framed(chat).fingerprint == shown.fingerprint
    assert [trace.operation for trace in log.traces] == ["framed"]  # one trace (048)


def test_a_consent_that_names_no_frame_decides_nothing(cards):
    folder, chat = cards
    with judged(folder, HINDERED) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        assert table.runner.confirm(chat, table.player()) is None
        # a setback with nothing to say which frame it was chosen for (before this slice, it decided the action)
        refused("fingerprint_mismatch", table.runner.decide, chat, table.player(), "failure", basis="consent",
                consequences=("s1",))
        for fingerprint in (None, "sha256:" + "0" * 64):
            refused("fingerprint_mismatch", table.runner.decide, chat, table.player(), "failure", basis="consent",
                    consequences=("s1",), fingerprint=fingerprint)
        refused("invalid_field", table.runner.decide, chat, table.player(), "failure", basis="consent",
                consequences=("s1",), fingerprint=7)
        turn_id = table.runner.open(chat).pending.turn_id
        assert table.adjudication.outcome(chat.campaign_id, turn_id) is None  # nothing reached 029


def test_a_consent_given_for_an_earlier_action_never_decides_a_later_one(cards):
    folder, chat = cards
    with judged(folder, HINDERED, HINDERED) as table:
        table.runner.seat(chat, ANA)
        first = framed_hindered(table, chat)
        table.runner.pass_turn(chat, table.player())  # left undecided; the table plays on
        second = framed_hindered(table, chat, "*I leap for the yard*")
        assert first.frame.attempt_id != second.frame.attempt_id and first.fingerprint != second.fingerprint
        # the player's setback for the first action, replayed now, would land on the open (second) action
        refused("fingerprint_mismatch", table.runner.decide, chat, table.player(), "failure", basis="consent",
                consequences=("s1",), fingerprint=first.fingerprint)
        state = table.adjudication.state(chat.campaign_id)
        assert first.frame.attempt_id not in state.decisions and second.frame.attempt_id not in state.decisions
        outcome = table.runner.decide(chat, table.player(), "failure", basis="consent", consequences=("s1",),
                                      fingerprint=second.fingerprint)
        assert (outcome.frame.attempt_id, outcome.consented_by) == (second.frame.attempt_id, ANA)


def test_a_consent_given_for_an_earlier_revision_never_decides_the_frame_now_on_record(cards):
    from dataclasses import replace as _replace
    from grimoire.play.adjudicate import frame_of
    folder, chat = cards
    with judged(folder, HINDERED) as table:
        table.runner.seat(chat, ANA)
        shown = framed_hindered(table, chat)
        record = table.runner.open(chat).pending
        frame, contribution = frame_of(record, record.framed)
        # Another holder of the adjudication key revises the frame (it drops the stake) after the player saw it.
        table.adjudication.frame(_replace(frame, stakes=()), contribution=contribution, author=table.player())
        refused("fingerprint_mismatch", table.runner.decide, chat, table.player(), "failure", basis="consent",
                fingerprint=shown.fingerprint)
        assert table.adjudication.outcome(chat.campaign_id, record.turn_id) is None
        now = table.runner.framed(chat)
        assert now.fingerprint != shown.fingerprint and now.frame.stakes == ()
        refused("fingerprint_mismatch", table.runner.decide, chat, table.owner, "success", cites=("f1",),
                fingerprint=shown.fingerprint)  # a director who names a stale frame is refused too
        assert table.adjudication.outcome(chat.campaign_id, record.turn_id) is None


def test_a_consent_for_revision_1_does_not_decide_revision_3_after_a_round_trip(cards):
    from dataclasses import replace as _replace
    from grimoire.play.adjudicate import frame_of
    folder, chat = cards
    with judged(folder, HINDERED) as table:
        table.runner.seat(chat, ANA)
        shown = framed_hindered(table, chat)  # revision 1: the player sees this and chooses a setback
        record = table.runner.open(chat).pending
        frame, contribution = frame_of(record, record.framed)
        favour = _replace(frame.factors[0], factor_id="f9", text="a rope hangs within reach")
        assert favour.bearing == "favours"
        # 029 compares each revision with the first, so A, then A with a favour, then A again is allowed
        table.adjudication.frame(_replace(frame, factors=frame.factors + (favour,)), contribution=contribution,
                                 author=table.player())  # revision 2
        table.adjudication.frame(frame, contribution=contribution, author=table.player())  # revision 3 reads as 1
        entry = table.adjudication.state(chat.campaign_id).frame(record.turn_id)
        assert entry.revision == 3 and entry.frame == shown.frame
        now = table.runner.framed(chat)
        assert now.fingerprint != shown.fingerprint  # the revision is part of what a consent names
        refused("fingerprint_mismatch", table.runner.decide, chat, table.player(), "failure", basis="consent",
                consequences=("s1",), fingerprint=shown.fingerprint)
        assert table.adjudication.outcome(chat.campaign_id, record.turn_id) is None
        outcome = table.runner.decide(chat, table.player(), "failure", basis="consent", consequences=("s1",),
                                      fingerprint=now.fingerprint)  # chosen again, for revision 3 as shown
        assert outcome.consented_by == ANA


def test_no_revision_lands_between_the_fingerprint_check_and_the_decision(cards):
    import threading
    from dataclasses import replace as _replace
    from grimoire.play.adjudicate import frame_of
    folder, chat = cards
    with judged(folder, HINDERED) as table:
        table.runner.seat(chat, ANA)
        shown = framed_hindered(table, chat)
        assert table.adjudication.authority.lock is table.runner.store.lock
        record = table.runner.open(chat).pending
        frame, contribution = frame_of(record, record.framed)
        better = _replace(frame, factors=frame.factors + (_replace(frame.factors[0], factor_id="f9"),))
        state, outcome = table.adjudication.state, {}

        def revise():
            try:
                table.adjudication.frame(better, contribution=contribution, author=table.player())
                outcome["revised"] = True
            except AdjudicationError as error:
                outcome["revised"] = error.reason

        def racing_state(*args, **kwargs):  # another key holder revises while the consent is checked
            if "thread" not in outcome:
                outcome["thread"] = worker = threading.Thread(target=revise)
                worker.start()
                worker.join(0.5)
                outcome["blocked"] = worker.is_alive()
            return state(*args, **kwargs)
        table.adjudication.state = racing_state
        decided = table.runner.decide(chat, table.player(), "failure", basis="consent", consequences=("s1",),
                                      fingerprint=shown.fingerprint)
        outcome["thread"].join(5)
        assert outcome["blocked"] is True  # it waited for the lock the check held
        assert decided.frame == frame and outcome["revised"] == "already_decided"


def test_a_confirm_cut_short_leaves_nothing_to_consent_to(cards):
    from grimoire.store.errors import StoreError
    folder, chat = cards
    with judged(folder, HINDERED) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        failing_once(table.adjudication, "frame", StoreError("down"))
        refused("adjudication_unavailable", table.runner.confirm, chat, table.player())
        assert table.runner.open(chat).pending.framed is not None  # the turn says framed; 029 holds nothing
        assert table.runner.framed(chat) is None
        refused("unknown_attempt", table.runner.decide, chat, table.player(), "failure", basis="consent",
                consequences=("s1",), fingerprint="sha256:" + "0" * 64)
        refused("unknown_attempt", table.runner.decide, chat, table.owner, "failure", cites=("f2",),
                consequences=("s1",), fingerprint="sha256:" + "0" * 64)


def test_only_the_acting_players_own_trusted_handle_consents_and_it_reaches_029_as_given(cards):
    folder, chat = cards
    with judged(folder, HINDERED) as table:
        table.runner.seat(chat, ANA)
        shown = framed_hindered(table, chat).fingerprint
        scene = table.runner.open(chat)
        (mara,) = [actor for actor in scene.cast if actor != scene.persona]
        table.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, mara.instance_id, BOB),
                                        table.owner)  # Bob plays Mara: at the table, but not Anya's player
        setback = dict(basis="consent", consequences=("s1",), fingerprint=shown)
        refused("not_controller", table.runner.decide, chat, table.player(BOB), "failure", **setback)
        refused("not_controller", table.runner.decide, chat, table.owner, "failure", **setback)  # not on her behalf
        untrusted = table.authority.handle_for(ANA, trusted=False)
        refused("not_authenticated", table.runner.decide, chat, untrusted, "failure", **setback)
        decide, given = table.adjudication.decide, []

        def recorded(*args, consent=None, **kwargs):
            given.append(consent)
            return decide(*args, consent=consent, **kwargs)
        table.adjudication.decide = recorded
        mine = table.player()
        outcome = table.runner.decide(chat, mine, "failure", **setback)
        assert given == [mine] and given[0] is mine and outcome.consented_by == ANA  # this call's own handle


def test_a_consent_fails_closed_when_029_cannot_be_read(cards):
    folder, chat = cards
    with judged(folder, HINDERED) as table:
        table.runner.seat(chat, ANA)
        shown = framed_hindered(table, chat).fingerprint
        failing_once(table.adjudication, "state", LedgerInvalid("poisoned"))
        refused("adjudication_unreadable", table.runner.decide, chat, table.player(), "failure", basis="consent",
                consequences=("s1",), fingerprint=shown)
        turn_id = table.runner.open(chat).pending.turn_id
        assert table.adjudication.outcome(chat.campaign_id, turn_id) is None
        outcome = table.runner.decide(chat, table.player(), "failure", basis="consent", consequences=("s1",),
                                      fingerprint=shown)
        assert outcome.consented_by == ANA


def test_the_turn_file_never_says_what_frame_a_consent_is_for(cards):
    from dataclasses import replace as _replace
    folder, chat = cards
    with judged(folder, HINDERED) as table:
        table.runner.seat(chat, ANA)
        shown = framed_hindered(table, chat)
        stored = table.runner.open(chat).turns.last
        # the turn file is rewritten to say the frame had no stake: the fingerprint is still 029's
        table.runner._write(stored.record.with_label(_replace(stored.record.framed, stakes=())), stored)
        assert table.runner.open(chat).pending.framed.stakes == ()
        assert table.runner.framed(chat).fingerprint == shown.fingerprint
        outcome = table.runner.decide(chat, table.player(), "failure", basis="consent", consequences=("s1",),
                                      fingerprint=shown.fingerprint)
        assert [stake.stake_id for stake in outcome.frame.stakes] == ["s1"]


def _passed_meanwhile(table: Table, chat, monkeypatch, passed: dict) -> None:
    """The director passes the turn after a decision read it and before 029 decides, under the lock."""
    import grimoire.play.adjudicate as adjudicate
    original = adjudicate._refuse_void

    def pass_first(runner, record):
        if not passed:
            passed["record"] = table.runner.pass_turn(chat, table.owner)
        return original(runner, record)
    monkeypatch.setattr(adjudicate, "_refuse_void", pass_first)


@pytest.mark.parametrize("basis", ["consent", "adjudicated"])
def test_a_decision_never_lands_on_a_turn_passed_after_it_was_read(cards, monkeypatch, basis):
    folder, chat = cards
    with judged(folder, HINDERED) as table:
        table.runner.seat(chat, ANA)
        shown = framed_hindered(table, chat)
        turn_id = table.runner.open(chat).pending.turn_id
        passed: dict = {}
        _passed_meanwhile(table, chat, monkeypatch, passed)
        if basis == "consent":
            call = (table.player(), "failure")
            options = dict(basis="consent", consequences=("s1",), fingerprint=shown.fingerprint)
        else:
            call = (table.owner, "partial")
            options = dict(cites=("f1", "f2"), consequences=("s1",))
        refused("no_open_turn", table.runner.decide, chat, *call, **options)
        assert passed["record"].passed_by == OWNER  # the pass landed, "nothing kept"
        assert table.adjudication.outcome(chat.campaign_id, turn_id) is None  # and 029 records no outcome for it


def test_a_rule_decision_never_lands_on_a_turn_passed_after_it_was_read(cards, monkeypatch):
    folder, chat = cards
    with judged(folder, UNOPPOSED) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        turn_id = table.runner.open(chat).pending.turn_id
        passed: dict = {}
        _passed_meanwhile(table, chat, monkeypatch, passed)
        refused("no_open_turn", table.runner.confirm, chat, table.player())
        assert passed["record"].passed_by == OWNER
        assert table.adjudication.outcome(chat.campaign_id, turn_id) is None


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


def test_the_adjudication_key_is_the_apps_own_never_a_players_or_the_labellers(cards):
    folder, _ = cards
    with judged(folder) as table:
        for principal in (ANA, OWNER, LABELLER):  # the acting player, the owner, the labeller's principal
            key = AdjudicationLibrary(table.store, table.authority, table.authority.handle_for(principal, trusted=True))
            refused("bad_adjudication", SceneRunner, table.store, table.authority, table.owner, owner=OWNER,
                    label_handle=table.authority.handle_for(LABELLER, trusted=False), adjudication=key)


def failing_once(owner, name: str, error: Exception) -> None:
    """``owner.name`` raises ``error`` on its next call, then works as before."""
    original = getattr(owner, name)

    def call(*args, **kwargs):
        setattr(owner, name, original)
        raise error
    setattr(owner, name, call)


def test_a_keep_closing_the_turn_while_it_is_confirmed_leaves_nothing_framed(cards):
    folder, chat = cards
    with judged(folder, UNOPPOSED, steps=[say("You reach the top.")]) as table:
        acting(table, chat)
        early = table.runner.generate(chat, table.player())
        table.runner.label(chat, table.player())
        check = table.adjudication.check

        def kept_meanwhile(*args, **kwargs):  # the table keeps the take while 029 checks the frame
            table.adjudication.check = check
            result = check(*args, **kwargs)
            table.runner.keep(chat, table.player(), early.take)
            return result
        table.adjudication.check = kept_meanwhile
        refused("no_open_turn", table.runner.confirm, chat, table.player())
        record = table.runner.open(chat).turns.last.record
        assert record.kept.take == early.take and record.framed is None
        assert table.adjudication.state(chat.campaign_id).frame(record.turn_id) is None


def test_a_pause_landing_while_an_action_is_confirmed_frames_nothing_and_nothing_is_decided_over_it(cards):
    folder, chat = cards
    with judged(folder, UNOPPOSED) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        check = table.adjudication.check

        def paused_meanwhile(*args, **kwargs):  # a player stops play while 029 checks the frame
            table.adjudication.check = check
            result = check(*args, **kwargs)
            table.runner.request(chat, table.player(), "pause")
            return result
        table.adjudication.check = paused_meanwhile
        refused("paused", table.runner.confirm, chat, table.player())
        record = table.runner.open(chat).pending
        assert record.framed is None and record.labels[-1].state == "proposed"
        assert table.adjudication.state(chat.campaign_id).frame(record.turn_id) is None
        refused("paused", table.runner.confirm, chat, table.player())  # and none while it stands
        table.runner.resume(chat, table.player())
        assert table.runner.confirm(chat, table.player()).decision.result == "success"


def test_nothing_is_decided_while_play_is_paused(cards):
    folder, chat = cards
    with judged(folder, HINDERED) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        assert table.runner.confirm(chat, table.player()) is None
        table.runner.request(chat, table.player(), "pause")
        refused("paused", table.runner.decide, chat, table.owner, "partial", cites=("f1", "f2"),
                consequences=("s1",))
        table.runner.resume(chat, table.player())
        table.runner.decide(chat, table.owner, "partial", cites=("f1", "f2"), consequences=("s1",))


def test_a_confirm_cut_short_after_framing_finishes_on_the_next_confirm(cards):
    folder, chat = cards
    with judged(folder, UNOPPOSED) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        failing_once(table.adjudication, "frame", LedgerInvalid("poisoned"))
        refused("adjudication_unreadable", table.runner.confirm, chat, table.player())
        assert table.runner.open(chat).pending.framed.label == 1  # framed on the turn first, so never relabelled
        refused("already_framed", table.runner.label, chat, table.player())
        refused("undecided_attempt", table.runner.generate, chat, table.player())
        failing_once(table.adjudication, "decide", AuthorityUnavailable("gone"))
        refused("adjudication_unavailable", table.runner.confirm, chat, table.player())
        failing_once(table.adjudication, "assess", AdjudicationError("refused", "not_in_chat"))
        refused("not_in_chat", table.runner.confirm, chat, table.player())
        outcome = table.runner.confirm(chat, table.player())  # the frame is 029's already; the rules decide it
        assert (outcome.decision.result, outcome.decision.basis) == ("success", "rule")
        turn_id = table.runner.open(chat).pending.turn_id
        assert len(table.adjudication.state(chat.campaign_id).frames[turn_id]) == 1  # one revision, never two
        refused("already_framed", table.runner.confirm, chat, table.player())


def test_a_relabel_landing_while_a_confirm_writes_is_never_framed_by_it(cards):
    folder, chat = cards
    with judged(folder, HINDERED, UNOPPOSED) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        write, relabelled = table.runner.scenes.write, []

        def racing(record, base, **kwargs):
            if not relabelled:
                relabelled.append(None)
                relabelled.append(table.runner.label(chat, table.owner))  # the owner relabels meanwhile
            return write(record, base, **kwargs)
        table.runner.scenes.write = racing
        refused("turn_changed", table.runner.confirm, chat, table.player())
        table.runner.scenes.write = write
        record = table.runner.open(chat).pending
        assert record.framed is None and table.adjudication.state(chat.campaign_id).frame(record.turn_id) is None
        outcome = table.runner.confirm(chat, table.player())  # the player confirms what they now see
        assert outcome.frame.provenance.startswith("035 labelling 2")


def test_a_framed_turn_is_read_only_with_its_key_and_ids_are_collections(cards):
    folder, chat = cards
    with judged(folder, HINDERED) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        table.runner.confirm(chat, table.player())
        for cites in ("f1", [1], 7):
            refused("invalid_field", table.runner.decide, chat, table.owner, "partial", cites=cites,
                    consequences=("s1",))
        table.runner.adjudication = None
        refused("no_adjudication", table.runner.generate, chat, table.player())


def test_the_tables_outcome_line_names_what_came_of_it_and_fits_one_heard_line():
    actor = InstanceRef("c1", "character", "anya")
    factors = tuple(SimpleNamespace(factor_id=f"f{n}", text="x" * 300) for n in range(1, 9))
    stakes = (SimpleNamespace(stake_id="s1", text="She falls to the deck"),)
    frame = SimpleNamespace(actor=actor, intent="I climb", factors=factors, stakes=stakes)
    decision = SimpleNamespace(result="partial", cites=frozenset(f.factor_id for f in factors),
                               consequences=frozenset({"s1"}))
    line = outcome_line(SimpleNamespace(frame=frame, decision=decision), {actor: "Anya Rook"})
    assert line.text.startswith("Anya Rook's action (I climb) succeeds at a cost. What comes of it: She falls")
    assert len(line.text) <= MAX_LINE_CHARS and line.text.endswith("…")


def test_a_frame_029_refuses_for_good_sends_the_action_back_to_unframed_play(cards):
    folder, chat = cards
    with judged(folder, UNOPPOSED, UNOPPOSED, steps=[say("Mara looks up.")]) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        failing_once(table.adjudication, "check", AdjudicationError("refused", "unresolved_actor"))
        refused("unresolved_actor", table.runner.confirm, chat, table.player())
        assert table.runner.open(chat).pending.labels[-1].state == "proposed"  # nothing was marked
        failing_once(table.adjudication, "frame", AdjudicationError("refused", "unresolved_actor"))
        refused("unresolved_actor", table.runner.confirm, chat, table.player())  # refused after the mark
        record = table.runner.open(chat).pending
        assert record.framed is None and (record.labels[-1].state, record.labels[-1].reason) == (
            "refused", "unresolved_actor")
        table.runner.generate(chat, table.player())  # never framed: play goes on
        table.runner.label(chat, table.player())
        assert table.runner.confirm(chat, table.player()).decision.result == "success"


def test_a_rollback_never_unframes_a_frame_029_recorded_meanwhile(cards):
    from grimoire.play.adjudicate import _unframe
    folder, chat = cards
    with judged(folder, HINDERED) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        assert table.runner.confirm(chat, table.player()) is None  # framed in 029, waiting on a decision
        record = table.runner.open(chat).pending
        _unframe(table.runner, chat, record, record.framed.label, "card_missing")  # a losing confirm's rollback
        assert table.runner.open(chat).pending.framed is not None  # the recorded frame keeps its turn marker
        assert table.runner.decide(chat, table.owner, "partial", cites=("f1", "f2"), consequences=("s1",))


def test_ids_given_as_a_generator_are_all_kept(cards):
    folder, chat = cards
    with judged(folder, HINDERED) as table:
        acting(table, chat)
        table.runner.label(chat, table.player())
        table.runner.confirm(chat, table.player())
        outcome = table.runner.decide(chat, table.owner, "partial", cites=(c for c in ("f1", "f2")),
                                      consequences=(s for s in ("s1",)))
        assert outcome.decision.consequences == frozenset({"s1"})


def test_nothing_is_labelled_while_a_session_zero_cannot_be_read(cards):
    from chat_fixtures import Clock
    from grimoire.chatstore import ChatLibrary
    from scene_fixtures import Tagged
    folder, chat = cards
    with labelled(folder, REPLY) as table:
        acting(table, chat)
        chats = ChatLibrary(table.store, table.owner, clock=Clock(), new_id=Tagged("sz"), owner=OWNER)
        _, etag = chats.read_scope(chat.scope)  # the chat's scope holds a session zero no codec can read
        chats.set_session_zero(chat.scope, {"lines": []}, expected=etag).commit()
        refused("session_zero_unreadable", table.runner.label, chat, table.player())
        assert not table.runner.open(chat).pending.labels and not table.labels.prompts()
