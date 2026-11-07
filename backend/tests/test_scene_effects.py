"""035 milestone 2: accepting a turn's structured effect into 021, through the runner, with 030's recovery.

Over the synthetic card library (``scene_fixtures``): Mara Vell's card chat with Anya Rook as the player's
persona (``human:ana`` plays her). The labeller and the adjudication key frame and decide Anya's action; the
owner's handle is both the runner's and the effects library's outcome key. A crash is a ``Crash`` raised at
the step named, which no handler catches: the process is gone, and the next ``Table`` is the restart.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from hashlib import sha256

import pytest

from generation_fixtures import say
from scene_fixtures import ANA, BOB, LABEL_POLICY, Table, Tagged, card_library
from store_helpers import OWNER

from grimoire.core import DIRECTOR
from grimoire.effects import EffectHoldings, Op
from grimoire.effectstore import EffectChange, TurnEffectsLibrary
from grimoire.holdings import Line
from grimoire.ledgers.codec import ControlChange
from grimoire.play import SceneRunner
from grimoire.play import accept as accepting
from grimoire.scene import MAX_EFFECTS, SceneRefused
from grimoire.store import StaleWrite
from grimoire.sync import identify

from chat_fixtures import Clock

UNOPPOSED = json.dumps({"factors": [{"fact": "f1", "bearing": "favours"}], "stakes": []})
HINDERED = json.dumps({"factors": [{"fact": "f1", "bearing": "favours"}, {"fact": "f2", "bearing": "hinders"}],
                       "stakes": ["She slips"]})
NOW = 2**62
MET = "Anya and Mara have met."


class Crash(BaseException):
    """The process dies here: nothing after it runs, and no handler sees it."""


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


def table(folder, *replies: str, steps=(), effects: bool = True) -> Table:
    return Table(folder, steps, policy=LABEL_POLICY, labels=[say(reply) for reply in replies or (UNOPPOSED,)],
                 adjudicate=True, effects=effects)


def restart(folder) -> Table:
    """The library opened again after the process died: the same keys, no labeller."""
    return Table(folder, policy=LABEL_POLICY, adjudicate=True, effects=True)


def decided(at: Table, chat, text: str = "*I wave to Mara*"):
    """Anya's action, framed and decided by rule: its turn id, Anya and Mara."""
    at.runner.seat(chat, ANA)
    record = at.runner.post(chat, at.player(), text)
    at.runner.label(chat, at.player())
    assert at.runner.confirm(chat, at.player()) is not None
    scene = at.runner.open(chat)
    (mara,) = [actor for actor in scene.cast if actor != scene.persona]
    return record.turn_id, scene.persona, mara


def meeting(anya, mara, turn: str) -> list[Op]:
    """Anya's own act (backed by her player's post): it needs Ana's key and the director's."""
    return [Op("begin", "has_met", anya, mara, anya, turn)]


def knowing(at: Table, chat) -> list[Op]:
    """Mara comes to know Anya: a director-only effect, no player character acts in it."""
    scene = at.runner.open(chat)
    (mara,) = [actor for actor in scene.cast if actor != scene.persona]
    return [Op("begin", "knows", mara, scene.persona)]


def proposed(at: Table, chat, ops_of=meeting, **kwargs):
    turn, anya, mara = decided(at, chat)
    effect = at.runner.propose_effect(chat, at.player(), turn, MET, ops_of(anya, mara, turn), **kwargs)
    return turn, effect


def events(at: Table, chat):
    return at.effects.continuity(chat.campaign_id, as_of=NOW, requested_by=DIRECTOR)


def unsettled(at: Table, chat) -> list[str]:
    return [detail for code, detail in at.runner.open(chat).problems if code == "effect_unsettled"]


def stored_effect(at: Table, chat, turn: str, number: int = 1):
    (record,) = [record for record in at.runner.open(chat).records if record.turn_id == turn]
    return record.effect(number)


def crash_after(monkeypatch, *, relations: bool, event: bool) -> None:
    """021's commit publishes only some of its files (or all of them), and then the process dies."""
    def commit(change, approvals=None):
        txn = change.library.store.transaction()
        puts = [put for item in change.relation_changes for put in item.puts] if relations else []
        puts += list(change.puts) if event else []
        for record, expected in puts:
            txn.put(record, expected)
        if puts:
            txn.commit(change.library.handle)
        raise Crash
    monkeypatch.setattr(EffectChange, "commit", commit)


# -- the keys ---------------------------------------------------------------------------------------

def test_the_players_key_then_the_directors_accepts_the_effect_exactly_as_endorsed(cards):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        assert (effect.number, effect.state, effect.endorsers) == (1, "proposed", ((turn, ANA),))
        assert effect.proposal.proposed_by == "player" and effect.proposal.moment.turn_id == turn
        endorsed = at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        assert endorsed.state == "proposed" and [item.principal for item in endorsed.endorsements] == [ANA]
        assert events(at, chat).events == ()  # one key is not enough
        accepted = at.runner.approve_effect(chat, at.owner, turn, 1)  # the last key commits
        assert (accepted.state, accepted.approved_by) == ("accepted", OWNER)
        found = events(at, chat)
        (event,) = found.events
        assert (event.event_id, event.summary, event.fingerprint) == (
            accepted.event_id, MET, effect.proposal.fingerprint)
        assert [(item.contribution_id, item.principal) for item in event.endorsements] == [(turn, ANA)]
        assert found.problems == () and unsettled(at, chat) == []
    with restart(folder) as again:  # the turn file keeps it whole
        assert stored_effect(again, chat, turn) == accepted
        refused("already_accepted", again.runner.approve_effect, chat, again.owner, turn, 1)


def test_the_directors_key_then_the_players_accepts_on_the_players_own_handle(cards):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        approved = at.runner.approve_effect(chat, at.owner, turn, 1)
        assert (approved.state, approved.event_id) == ("approved", None)
        accepted = at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        assert accepted.state == "accepted" and len(events(at, chat).events) == 1


def test_an_effect_no_player_character_acts_in_needs_only_the_directors_key(cards):
    folder, chat = cards
    with table(folder) as at:
        turn, anya, mara = decided(at, chat)
        found = EffectHoldings((Line.add(anya, "coin", 3),), "found on the quay")
        effect = at.runner.propose_effect(chat, at.owner, turn, "Mara knows Anya; Anya finds three coins.",
                                          [Op("begin", "knows", mara, anya)], found)
        assert (effect.endorsers, effect.proposal.proposed_by, effect.proposal.attempt_id) == ((), "director", turn)
        accepted = at.runner.approve_effect(chat, at.owner, turn, 1)
        assert accepted.state == "accepted"
        (event,) = events(at, chat).events
        assert event.holdings is not None and event.endorsements == ()
    with restart(folder) as again:
        assert stored_effect(again, chat, turn).proposal == effect.proposal  # the holdings round-trip too


def test_a_new_proposal_supersedes_the_live_one_and_keys_follow_the_proposal_shown(cards):
    folder, chat = cards
    with table(folder) as at:
        turn, first = proposed(at, chat)
        at.runner.approve_effect(chat, at.owner, turn, 1)
        scene = at.runner.open(chat)
        second = at.runner.propose_effect(chat, at.player(), turn, "Anya greets Mara.",
                                          meeting(scene.persona, [a for a in scene.cast if a != scene.persona][0],
                                                  turn))
        assert second.number == 2 and stored_effect(at, chat, turn).state == "superseded"
        refused("not_proposed", at.runner.endorse_effect, chat, at.player(), turn, 1, first.proposal.fingerprint)
        refused("fingerprint_mismatch", at.runner.endorse_effect, chat, at.player(), turn, 2,
                first.proposal.fingerprint)
        assert second.proposal.moment.sequence > first.proposal.moment.sequence
        assert at.runner.endorse_effect(chat, at.player(), turn, 2, second.proposal.fingerprint).state == "proposed"


# -- who may, and what is never proposed -------------------------------------------------------------

def test_only_the_posting_player_or_the_director_proposes_and_only_the_acting_player_endorses(cards):
    folder, chat = cards
    with table(folder) as at:
        turn, anya, mara = decided(at, chat)
        at.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, mara.instance_id, BOB),
                                     at.owner)  # Bob plays Mara: at the table, but not Anya's player
        refused("not_your_turn", at.runner.propose_effect, chat, at.player(BOB), turn, MET, meeting(anya, mara, turn))
        effect = at.runner.propose_effect(chat, at.player(), turn, MET, meeting(anya, mara, turn))
        refused("not_endorser", at.runner.endorse_effect, chat, at.player(BOB), turn, 1, effect.proposal.fingerprint)
        refused("not_director", at.runner.approve_effect, chat, at.player(), turn, 1)
        refused("not_director", at.runner.recover_effects, chat, at.player())


def test_a_change_of_control_since_the_proposal_voids_its_endorsement(cards):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        scene = at.runner.open(chat)
        (mara,) = [actor for actor in scene.cast if actor != scene.persona]
        for actor, player in ((scene.persona, BOB), (mara, ANA)):  # the players swap characters
            at.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, actor.instance_id, player),
                                         at.owner)
        refused("control_changed", at.runner.endorse_effect, chat, at.player(), turn, 1, effect.proposal.fingerprint)
        assert events(at, chat).events == ()


def test_an_action_is_eligible_only_once_decided(cards):
    folder, chat = cards
    with table(folder, HINDERED) as at:
        at.runner.seat(chat, ANA)
        record = at.runner.post(chat, at.player(), "*I leap the gap*")
        scene = at.runner.open(chat)
        (mara,) = [actor for actor in scene.cast if actor != scene.persona]
        ops = meeting(scene.persona, mara, record.turn_id)
        refused("not_kept", at.runner.propose_effect, chat, at.player(), record.turn_id, MET, ops)
        at.runner.label(chat, at.player())
        assert at.runner.confirm(chat, at.player()) is None  # uncertain: it waits for the director
        refused("undecided_attempt", at.runner.propose_effect, chat, at.player(), record.turn_id, MET, ops)
        at.runner.decide(chat, at.owner, "success", cites=("f1",))
        assert at.runner.propose_effect(chat, at.player(), record.turn_id, MET, ops).state == "proposed"


def test_ability_uses_and_a_malformed_proposal_are_refused(cards):
    folder, chat = cards
    with table(folder) as at:
        turn, anya, mara = decided(at, chat)
        refused("not_in_this_slice", at.runner.propose_effect, chat, at.player(), turn, MET,
                meeting(anya, mara, turn), abilities=("a use",))
        refused("invalid_field", at.runner.propose_effect, chat, at.player(), turn, "", meeting(anya, mara, turn))
        refused("unknown_turn", at.runner.propose_effect, chat, at.player(), "t-none", MET, ())
        refused("unknown_effect", at.runner.approve_effect, chat, at.owner, turn, 1)


def test_a_turn_holds_at_most_max_effects_proposals(cards):
    folder, chat = cards
    with table(folder) as at:
        turn, anya, mara = decided(at, chat)
        for _ in range(MAX_EFFECTS):
            at.runner.propose_effect(chat, at.player(), turn, MET, meeting(anya, mara, turn))
        refused("too_many_effects", at.runner.propose_effect, chat, at.player(), turn, MET, meeting(anya, mara, turn))


def test_nothing_is_proposed_without_an_effects_library_and_it_must_be_this_runners(cards):
    folder, chat = cards
    with table(folder, effects=False) as at:
        turn, anya, mara = decided(at, chat)
        refused("no_effects", at.runner.propose_effect, chat, at.player(), turn, MET, meeting(anya, mara, turn))
        other = TurnEffectsLibrary(at.store, at.authority.handle_for(OWNER, trusted=True), issued=at.authority.issued,
                                   clock=Clock(), new_id=Tagged("x"), owner=OWNER)  # another handle object
        refused("bad_effects", SceneRunner, at.store, at.authority, at.owner, owner=OWNER, effects=other)
        refused("bad_effects", SceneRunner, at.store, at.authority, at.owner, owner=OWNER, effects=object())


# -- what 021 and 029 refuse ------------------------------------------------------------------------

def test_an_effect_021_refuses_is_recorded_refused_with_its_reason(cards):
    folder, chat = cards
    with table(folder) as at:
        turn, anya, mara = decided(at, chat)
        effect = at.runner.propose_effect(chat, at.owner, turn, "Mara forgets Anya.", [Op("end", "knows", mara, anya)])
        refused_effect = at.runner.approve_effect(chat, at.owner, turn, 1)  # nothing to end
        assert refused_effect.state == "refused" and refused_effect.reason
        assert events(at, chat).events == () and stored_effect(at, chat, turn) == refused_effect
        assert at.runner.propose_effect(chat, at.owner, turn, MET, meeting(anya, mara, turn)).number == 2
        assert effect.state == "proposed"


def test_a_029_refusal_is_recorded_and_an_unavailable_ledger_is_retried(cards, monkeypatch):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)

        def unavailable(proposal):
            raise accepting.StoreError("down")
        monkeypatch.setattr(at.adjudication, "check_effect", unavailable)
        refused("adjudication_unavailable", at.runner.approve_effect, chat, at.owner, turn, 1)
        assert stored_effect(at, chat, turn).state == "proposed"  # the keys wait

        def mismatch(proposal):
            raise SceneRefused("assessment_mismatch", "synthetic")
        monkeypatch.setattr(accepting, "_029", lambda call, proposal: mismatch(proposal))
        assert at.runner.approve_effect(chat, at.owner, turn, 1).reason == "assessment_mismatch"


def test_a_library_changed_between_preview_and_commit_puts_the_keys_back(cards, monkeypatch):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        original = EffectChange.commit

        def stale(change, approvals=None):
            monkeypatch.setattr(EffectChange, "commit", original)
            raise StaleWrite([("campaigns/x", "changed")])
        monkeypatch.setattr(EffectChange, "commit", stale)
        refused("effects_changed", at.runner.approve_effect, chat, at.owner, turn, 1)
        current = stored_effect(at, chat, turn)
        assert (current.state, current.event_id, current.approved_by) == ("approved", None, OWNER)
        assert events(at, chat).events == () and unsettled(at, chat) == []
        assert at.runner.approve_effect(chat, at.owner, turn, 1).state == "accepted"


# -- sequences ---------------------------------------------------------------------------------------

def test_labellings_and_effects_take_one_strictly_increasing_campaign_sequence(cards):
    folder, chat = cards
    with table(folder, UNOPPOSED, UNOPPOSED) as at:
        turn, effect = proposed(at, chat)
        (first_label,) = at.runner.open(chat).last.labels
        at.runner.approve_effect(chat, at.owner, turn, 1)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        at.runner.pass_turn(chat, at.player())
        second_turn, _, _ = decided(at, chat, "*I bow*")
        (second_label,) = at.runner.open(chat).last.labels
        later = at.runner.propose_effect(chat, at.owner, second_turn, "Anya bows.", knowing(at, chat))
        sequences = [first_label.sequence, effect.proposal.moment.sequence, second_label.sequence,
                     later.proposal.moment.sequence]
        assert sequences == sorted(set(sequences))
        assert events(at, chat).events[0].moment.sequence == effect.proposal.moment.sequence


# -- the commit order, and what a crash leaves ---------------------------------------------------------

def test_a_crash_before_the_commit_leaves_an_unpublished_acceptance_that_recovery_puts_back(cards, monkeypatch):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        crash_after(monkeypatch, relations=False, event=False)
        with pytest.raises(Crash):
            at.runner.approve_effect(chat, at.owner, turn, 1)
    monkeypatch.undo()
    with restart(folder) as at:  # the restart
        current = stored_effect(at, chat, turn)
        assert current.state == "accepting" and current.event_id
        assert unsettled(at, chat) == [f"{turn}:{current.event_id}:unpublished"]
        refused("effect_accepting", at.runner.approve_effect, chat, at.owner, turn, 1)
        (settled,) = at.runner.recover_effects(chat, at.owner)
        assert (settled.state, settled.event_id, settled.approved_by) == ("approved", None, OWNER)
        assert unsettled(at, chat) == [] and at.runner.recover_effects(chat, at.owner) == ()
        assert at.runner.approve_effect(chat, at.owner, turn, 1).state == "accepted"  # the keys still hold


def test_a_crash_after_the_commit_is_recovered_as_accepted_and_blocks_every_other_acceptance(cards, monkeypatch):
    folder, chat = cards
    with table(folder, UNOPPOSED, UNOPPOSED) as at:
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        crash_after(monkeypatch, relations=True, event=True)
        with pytest.raises(Crash):
            at.runner.approve_effect(chat, at.owner, turn, 1)
        monkeypatch.undo()
        event_id = stored_effect(at, chat, turn).event_id
        assert unsettled(at, chat) == [f"{turn}:{event_id}:landed"]
        at.runner.pass_turn(chat, at.player())
        second, _, _ = decided(at, chat, "*I bow*")
        at.runner.propose_effect(chat, at.owner, second, "Anya bows.", knowing(at, chat))
        refused("recovery_needed", at.runner.approve_effect, chat, at.owner, second, 1)
        (settled,) = at.runner.recover_effects(chat, at.owner)
        assert (settled.state, settled.event_id) == ("accepted", event_id)
        assert at.runner.approve_effect(chat, at.owner, second, 1).state == "accepted"
        assert [event.event_id for event in events(at, chat).events][0] == event_id


def test_an_event_that_landed_without_its_changes_is_completed(cards, monkeypatch):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        crash_after(monkeypatch, relations=False, event=True)  # shape A
        with pytest.raises(Crash):
            at.runner.approve_effect(chat, at.owner, turn, 1)
    monkeypatch.undo()
    with restart(folder) as at:
        event_id = stored_effect(at, chat, turn).event_id
        assert unsettled(at, chat) == [f"{turn}:{event_id}:complete"]
        assert ("effects", f"receipt_mismatch:{event_id}") in at.runner.open(chat).problems
        (settled,) = at.runner.recover_effects(chat, at.owner)
        assert settled.state == "accepted" and events(at, chat).problems == ()


def test_changes_that_landed_without_their_event_are_adopted_on_the_recorded_keys(cards, monkeypatch):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        crash_after(monkeypatch, relations=True, event=False)  # shape B
        with pytest.raises(Crash):
            at.runner.approve_effect(chat, at.owner, turn, 1)
    monkeypatch.undo()
    with restart(folder) as at:  # Ana is not here: her recorded key, on that fingerprint, still counts
        event_id = stored_effect(at, chat, turn).event_id
        assert unsettled(at, chat) == [f"{turn}:{event_id}:adopt"]
        (settled,) = at.runner.recover_effects(chat, at.owner)
        assert settled.state == "accepted"
        found = events(at, chat)
        assert [event.event_id for event in found.events] == [event_id] and found.problems == ()
        assert [item.principal for item in found.events[0].endorsements] == [ANA]


@pytest.mark.parametrize("landed", [dict(relations=False, event=True), dict(relations=True, event=False)])
def test_an_abort_undoes_what_landed_with_every_endorser_present(cards, monkeypatch, landed):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        crash_after(monkeypatch, **landed)
        with pytest.raises(Crash):
            at.runner.approve_effect(chat, at.owner, turn, 1)
    monkeypatch.undo()
    with restart(folder) as at:
        refused("not_endorsed", at.runner.recover_effects, chat, at.owner, abort=True)
        refused("bad_handle", at.runner.recover_effects, chat, at.owner, abort=True,
                endorsers=[at.authority.handle_for(ANA, trusted=False)])
        (settled,) = at.runner.recover_effects(chat, at.owner, abort=True, endorsers=[at.player()])
        assert (settled.state, settled.reason) == ("revoked", "aborted")
        found = events(at, chat)
        assert found.events == () and [event.revocation.reason for event in found.revoked] == [accepting.ABORTED]
        assert unsettled(at, chat) == []
        assert stored_effect(at, chat, turn).state == "revoked"
        again = at.runner.propose_effect(chat, at.owner, turn, "Mara knows Anya.", knowing(at, chat))
        assert again.number == 2 and at.runner.approve_effect(chat, at.owner, turn, 2).state == "accepted"


def test_an_abort_of_an_unpublished_acceptance_drops_it(cards, monkeypatch):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        crash_after(monkeypatch, relations=False, event=False)
        with pytest.raises(Crash):
            at.runner.approve_effect(chat, at.owner, turn, 1)
        monkeypatch.undo()
        (settled,) = at.runner.recover_effects(chat, at.owner, abort=True)
        assert (settled.state, settled.reason, settled.event_id) == ("refused", "aborted", None)
        assert events(at, chat).events == ()


def test_a_crash_during_recovery_is_recovered_again_exactly_once(cards, monkeypatch):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        crash_after(monkeypatch, relations=False, event=True)  # shape A
        with pytest.raises(Crash):
            at.runner.approve_effect(chat, at.owner, turn, 1)
    monkeypatch.undo()
    with restart(folder) as at:
        def dies(*args, **kwargs):
            raise Crash
        monkeypatch.setattr(accepting, "_rewrite", dies)  # 030's completion commits; the turn is never marked
        with pytest.raises(Crash):
            at.runner.recover_effects(chat, at.owner)
    monkeypatch.undo()
    with restart(folder) as at:
        event_id = stored_effect(at, chat, turn).event_id
        assert unsettled(at, chat) == [f"{turn}:{event_id}:landed"]  # completed: only the mark is left
        (settled,) = at.runner.recover_effects(chat, at.owner)
        assert settled.state == "accepted" and len(events(at, chat).events) == 1 and events(at, chat).problems == ()


def test_an_unreadable_turn_file_anywhere_in_the_campaign_blocks_acceptance(cards):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        damaged = at.store.root / "campaigns" / chat.campaign_id / "turns" / "t-elsewhere.000001.json"
        damaged.write_text("{not json", encoding="utf-8")  # another chat's turn: it could hide an acceptance
        refused("recovery_needed", at.runner.approve_effect, chat, at.owner, turn, 1)
        damaged.unlink()
        assert at.runner.approve_effect(chat, at.owner, turn, 1).state == "accepted"


# -- races ---------------------------------------------------------------------------------------------

def test_two_keys_given_at_once_never_lose_one(cards, monkeypatch):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        write = at.runner.scenes.write
        raced = []

        def racing(record, base, **kwargs):  # the director's key lands while Ana's endorsement is written
            if not raced:
                raced.append(True)
                at.runner.approve_effect(chat, at.owner, turn, 1)
            return write(record, base, **kwargs)
        monkeypatch.setattr(at.runner.scenes, "write", racing)
        refused("turn_changed", at.runner.endorse_effect, chat, at.player(), turn, 1, effect.proposal.fingerprint)
        monkeypatch.setattr(at.runner.scenes, "write", write)
        assert stored_effect(at, chat, turn).state == "approved"  # the approval stands; nothing committed
        assert at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint).state == "accepted"
        assert len(events(at, chat).events) == 1


def test_a_turn_changed_while_the_last_key_accepts_commits_nothing(cards, monkeypatch):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        accept = at.effects.accept

        def meanwhile(*args, **kwargs):  # another key or a pause lands between 021's preview and the CAS
            change = accept(*args, **kwargs)
            at.runner.request(chat, at.player(), "pause")
            return change
        monkeypatch.setattr(at.effects, "accept", meanwhile)
        refused("turn_changed", at.runner.approve_effect, chat, at.owner, turn, 1)
        assert events(at, chat).events == () and stored_effect(at, chat, turn).state == "proposed"


# -- who hears it ----------------------------------------------------------------------------------------

def test_an_effect_proposal_never_reaches_a_model_prompt(cards):
    folder, chat = cards
    secret = "Anya quietly pockets the ledger key."
    with table(folder, steps=[say("Mara counts the coins.")]) as at:
        turn, anya, mara = decided(at, chat)
        at.runner.propose_effect(chat, at.owner, turn, secret, [Op("begin", "knows", mara, anya)])
        at.runner.generate(chat, at.player(), speakers=[mara])
        (prompt,) = at.harness.prompts()
        (label_prompt,) = at.labels.prompts()
    assert secret not in prompt and secret not in label_prompt


# -- review fixes: recovery after a control change, retractions, forged keys, revocations, pauses -------------

def _hand_over(at: Table, chat) -> None:
    """Ana hands Anya to Bob and plays Mara instead: Ana is still at the table, Bob now plays the actor."""
    scene = at.runner.open(chat)
    (mara,) = [actor for actor in scene.cast if actor != scene.persona]
    for actor, player in ((scene.persona, BOB), (mara, ANA)):
        at.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, actor.instance_id, player),
                                     at.owner)


@pytest.mark.parametrize("abort", [False, True])
def test_shape_b_is_settled_under_the_control_it_was_accepted_under_after_control_changed(cards, monkeypatch, abort):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        crash_after(monkeypatch, relations=True, event=False)  # shape B
        with pytest.raises(Crash):
            at.runner.approve_effect(chat, at.owner, turn, 1)
    monkeypatch.undo()
    with restart(folder) as at:
        assert type(stored_effect(at, chat, turn).controls_at) is int  # the control it was authorized under
        _hand_over(at, chat)
        event_id = stored_effect(at, chat, turn).event_id
        assert unsettled(at, chat) == [f"{turn}:{event_id}:adopt"]
        if abort:  # the original endorser consents to the undo, whatever they play now
            (settled,) = at.runner.recover_effects(chat, at.owner, abort=True, endorsers=[at.player()])
            assert (settled.state, settled.reason) == ("revoked", "aborted") and events(at, chat).events == ()
        else:  # recovering it as accepted needs only the director
            (settled,) = at.runner.recover_effects(chat, at.owner)
            assert settled.state == "accepted"
            (event,) = events(at, chat).events
            assert event.event_id == event_id and [item.principal for item in event.endorsements] == [ANA]
            assert [(snap.actor_id, snap.controller_id) for snap in event.controls] == [
                (effect.proposal.operations[0].agent.instance_id, ANA)]  # control as it was accepted
        assert unsettled(at, chat) == []


def test_a_017_retraction_of_an_accepted_relation_does_not_block_later_acceptances(cards):
    folder, chat = cards
    with table(folder, UNOPPOSED, UNOPPOSED) as at:
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        at.runner.approve_effect(chat, at.owner, turn, 1)
        (event,) = events(at, chat).events
        relation = event.receipts[0].relation
        relations = at.effects._relations()
        _, etag = relations.read(relation)
        relations.retract(relation, provenance="human:owner fixed a mistake", expected=etag).commit()
        assert ("effects", f"receipt_mismatch:{event.event_id}") in at.runner.open(chat).problems  # still told
        at.runner.pass_turn(chat, at.player())
        second, _, _ = decided(at, chat, "*I bow*")
        at.runner.propose_effect(chat, at.owner, second, "Mara knows Anya.", knowing(at, chat))
        assert at.runner.approve_effect(chat, at.owner, second, 1).state == "accepted"  # 021 lets it proceed


def _forge_endorsement(at: Table, chat, turn: str, fingerprint: str) -> None:
    path = at.store.root / "campaigns" / chat.campaign_id / "turns" / f"{turn}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    body = data.get("payload", data)
    body["effects"][0]["endorsements"] = [{"contribution_id": turn, "principal": ANA, "fingerprint": fingerprint}]
    path.write_text(json.dumps(data), encoding="utf-8")


def test_an_endorsement_written_into_the_turn_file_is_not_presented_to_021(cards):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)  # Ana never endorses
        _forge_endorsement(at, chat, turn, effect.proposal.fingerprint)
        assert [item.principal for item in stored_effect(at, chat, turn).endorsements] == [ANA]
        refused("invalid_endorsement", at.runner.approve_effect, chat, at.owner, turn, 1)
        assert events(at, chat).events == () and stored_effect(at, chat, turn).state == "proposed"
        # her own key, given now, is recorded by the authority and counts, after a restart too
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
    with restart(folder) as again:
        assert again.runner.approve_effect(chat, again.owner, turn, 1).state == "accepted"
        (event,) = events(again, chat).events
        assert [item.principal for item in event.endorsements] == [ANA]


def test_a_turn_whose_accepted_effect_was_revoked_in_021_takes_another(cards):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        accepted = at.runner.approve_effect(chat, at.owner, turn, 1)
        ref = accepting.AcceptedRef(chat.campaign_id, accepted.event_id)
        event, etag = at.effects.read(ref, requested_by=DIRECTOR)
        undo = [accepting.Endorsement(item.contribution_id, at.player(),
                                      accepting.revocation_fingerprint(ref, event.fingerprint))
                for item in event.endorsements]
        at.effects.revoke(ref, reason="a mistake", expected=etag, endorsements=undo).commit()
        refused("already_accepted", at.runner.approve_effect, chat, at.owner, turn, 1)  # the old one takes no key
        again = at.runner.propose_effect(chat, at.owner, turn, "Mara knows Anya.", knowing(at, chat))
        assert again.number == 2 and stored_effect(at, chat, turn).state == "revoked"
        assert stored_effect(at, chat, turn).event_id == accepted.event_id
        assert at.runner.approve_effect(chat, at.owner, turn, 2).state == "accepted"


def test_a_pause_landing_mid_acceptance_stops_an_earlier_turns_effect(cards, monkeypatch):
    folder, chat = cards
    with table(folder, UNOPPOSED, UNOPPOSED) as at:
        first, _, _ = decided(at, chat)
        at.runner.propose_effect(chat, at.owner, first, "Mara knows Anya.", knowing(at, chat))
        at.runner.pass_turn(chat, at.player())
        decided(at, chat, "*I bow*")
        accept = at.effects.accept

        def meanwhile(*args, **kwargs):  # the pause lands on the last turn, not on the effect's
            change = accept(*args, **kwargs)
            at.runner.request(chat, at.player(), "pause")
            return change
        monkeypatch.setattr(at.effects, "accept", meanwhile)
        refused("turn_changed", at.runner.approve_effect, chat, at.owner, first, 1)
        assert at.runner.open(chat).paused
        assert events(at, chat).events == () and stored_effect(at, chat, first).state == "proposed"


def test_a_withheld_action_takes_no_effect(cards):
    folder, chat = cards
    with table(folder) as at:
        turn, anya, mara = decided(at, chat)
        scene = at.runner.open(chat)
        (stored,) = [each for each in scene.turns.turns if each.record.turn_id == turn]
        post = stored.record.post  # as if 032 had withheld it: its sender alone heard it
        at.runner.scenes.write(replace(stored.record, post=replace(post, withheld=True, audience=(post.actor,))),
                               stored)
        refused("private_action", at.runner.propose_effect, chat, at.owner, turn, MET, meeting(anya, mara, turn))
        refused("private_action", at.runner.propose_effect, chat, at.player(), turn, MET, meeting(anya, mara, turn))


def test_recovery_needed_names_no_other_chats_turn(cards):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        at.runner.approve_effect(chat, at.owner, turn, 1)
        damaged = at.store.root / "campaigns" / chat.campaign_id / "turns" / "t-secret.000001.json"
        damaged.write_text("{not json", encoding="utf-8")  # another chat's turn file
        error = refused("recovery_needed", at.runner.endorse_effect, chat, at.player(), turn, 1,
                        effect.proposal.fingerprint)  # the player's key is the last one
        assert "t-secret" not in str(error) and "1 elsewhere" in str(error)  # a count, never the other chat


@pytest.mark.parametrize("abort", [False, True])
def test_shape_b_without_the_endorsement_record_is_settled_with_the_endorser_present(cards, monkeypatch, tmp_path,
                                                                                       abort):
    folder, chat = cards
    saved = tmp_path / "authority-before"
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        at.authority.flush()
        shutil.copytree(folder / "authority", saved)  # the ledger as it was before Ana endorsed
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        crash_after(monkeypatch, relations=True, event=False)  # shape B
        with pytest.raises(Crash):
            at.runner.approve_effect(chat, at.owner, turn, 1)
    monkeypatch.undo()
    shutil.rmtree(folder / "authority")
    shutil.copytree(saved, folder / "authority")  # restored from before: the endorsement is not in its audit
    with restart(folder) as at:
        assert unsettled(at, chat)[0].endswith(":adopt")
        refused("invalid_endorsement", at.runner.recover_effects, chat, at.owner)  # the director alone cannot
        (settled,) = at.runner.recover_effects(chat, at.owner, abort=abort, endorsers=[at.player()])
        assert settled.state == ("revoked" if abort else "accepted")
        found = events(at, chat)
        assert len(found.revoked if abort else found.events) == 1 and unsettled(at, chat) == []


# -- 028's forks, set-aside turns and sync conflicts --------------------------------------------------------

OTHER_DEVICE = "0badcafe"


def _fork_first_turn(at: Table, chat) -> str:
    """Another device's turn 1 of this chat arrives (as a sync would bring it): the chat is forked at turn 1."""
    from grimoire.scene import TURN_SCHEMA, turn_to_payload
    from grimoire.store import ScopeRecord
    first = at.runner.open(chat).turns.turns[0].record
    theirs = replace(first, device=OTHER_DEVICE, effects=(), labels=(), paused=False, paused_by=None)
    txn = at.store.transaction()
    txn.put(ScopeRecord(theirs.ref, TURN_SCHEMA, turn_to_payload(theirs), owner=OWNER), None)
    txn.commit(at.owner)
    return theirs.turn_id


def _second_turn(at: Table, chat):
    """Anya's second action, decided: a turn played after turn 1, so a fork at turn 1 sets it aside unchanged."""
    decided(at, chat)
    at.runner.pass_turn(chat, at.player())
    return decided(at, chat, "*I bow*")


def test_an_effect_is_never_proposed_on_a_turn_a_fork_choice_set_aside_meanwhile(cards, monkeypatch):
    folder, chat = cards
    with table(folder, UNOPPOSED, UNOPPOSED) as at:
        turn, anya, mara = _second_turn(at, chat)
        allocate = accepting.next_sequence

        def meanwhile(*args, **kwargs):  # after propose read the turn: a sync forks it, the owner chooses theirs
            at.runner.choose(chat, at.owner, _fork_first_turn(at, chat))
            return allocate(*args, **kwargs)
        monkeypatch.setattr(accepting, "next_sequence", meanwhile)
        refused("turn_changed", at.runner.propose_effect, chat, at.player(), turn, MET, meeting(anya, mara, turn))
        aside = {each.record.turn_id: each.record for each in at.runner.open(chat).turns.set_aside}
        assert aside[turn].effects == ()


def test_an_effect_is_never_accepted_on_a_turn_a_fork_choice_set_aside_meanwhile(cards, monkeypatch):
    folder, chat = cards
    with table(folder, UNOPPOSED, UNOPPOSED) as at:
        turn, anya, mara = _second_turn(at, chat)
        effect = at.runner.propose_effect(chat, at.player(), turn, MET, meeting(anya, mara, turn))
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        accept = at.effects.accept

        def meanwhile(*args, **kwargs):  # between 021's preview and the accepting write
            change = accept(*args, **kwargs)
            at.runner.choose(chat, at.owner, _fork_first_turn(at, chat))
            return change
        monkeypatch.setattr(at.effects, "accept", meanwhile)
        refused("turn_changed", at.runner.approve_effect, chat, at.owner, turn, 1)
        assert events(at, chat).events == ()
        aside = {each.record.turn_id: each.record for each in at.runner.open(chat).turns.set_aside}
        assert aside[turn].effect(1).state == "proposed"  # nothing accepted where no one hears it


def test_an_acceptance_cut_short_on_a_turn_later_set_aside_is_recovered(cards, monkeypatch):
    folder, chat = cards
    with table(folder, UNOPPOSED, UNOPPOSED) as at:
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        crash_after(monkeypatch, relations=True, event=True)
        with pytest.raises(Crash):
            at.runner.approve_effect(chat, at.owner, turn, 1)
        monkeypatch.undo()
        theirs = _fork_first_turn(at, chat)
        # the chat is forked: plays wait, but a committed acceptance is still settled
        refused("turn_fork", at.runner.approve_effect, chat, at.owner, turn, 1)
        event_id = stored_effect(at, chat, turn).event_id
        at.runner.choose(chat, at.owner, theirs)  # the turn holding it is set aside
        assert unsettled(at, chat) == [f"{turn}:{event_id}:landed"]  # still reported
        (settled,) = at.runner.recover_effects(chat, at.owner)
        assert settled.state == "accepted"
        (aside,) = at.runner.open(chat).turns.set_aside
        assert aside.record.effect(1).state == "accepted" and unsettled(at, chat) == []


def test_an_acceptance_cut_short_is_recovered_while_the_chat_is_forked(cards, monkeypatch):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        crash_after(monkeypatch, relations=True, event=True)
        with pytest.raises(Crash):
            at.runner.approve_effect(chat, at.owner, turn, 1)
        monkeypatch.undo()
        _fork_first_turn(at, chat)
        (settled,) = at.runner.recover_effects(chat, at.owner)  # it only marks what 021 already holds
        assert settled.state == "accepted" and stored_effect(at, chat, turn).state == "accepted"


def test_a_proposed_effect_on_a_set_aside_turn_blocks_nothing(cards):
    folder, chat = cards
    with table(folder, UNOPPOSED) as at:
        turn, effect = proposed(at, chat)
        at.runner.approve_effect(chat, at.owner, turn, 1)
        theirs = _fork_first_turn(at, chat)
        at.runner.choose(chat, at.owner, theirs)
        refused("unknown_turn", at.runner.endorse_effect, chat, at.player(), turn, 1, effect.proposal.fingerprint)
        assert accepting.blockers(at.runner, chat) == ([], 0)


def test_a_turn_in_sync_conflict_holds_effects_and_their_recovery(cards, monkeypatch):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        crash_after(monkeypatch, relations=True, event=True)
        with pytest.raises(Crash):
            at.runner.approve_effect(chat, at.owner, turn, 1)
        monkeypatch.undo()
        turns = at.runner.scenes.turns
        monkeypatch.setattr(at.runner.scenes, "turns",  # sync left the turn in a keep-both conflict
                            lambda chat_ref: replace(turns(chat_ref), conflicted=(turn,)))
        refused("turn_conflict", at.runner.recover_effects, chat, at.owner)  # each device reads its own version
        refused("turn_conflict", at.runner.propose_effect, chat, at.owner, turn, MET, knowing(at, chat))
        monkeypatch.undo()
        assert at.runner.recover_effects(chat, at.owner)[0].state == "accepted"


# -- the device that started an acceptance ------------------------------------------------------------

def _tag(device: str) -> str:
    """028's documented tag: the first eight hex digits of the SHA-256 of the device ID."""
    return sha256(device.encode()).hexdigest()[:8]


def _accepting_arrives(at: Table, chat, turn: str) -> str:
    """Another device's acceptance of this turn's effect arrives cut short (as a sync brings turns, never 021's
    accepted/): the turn's effect is ``accepting`` under that device's tag, its event in no library here."""
    (stored,) = [each for each in at.runner.open(chat).turns.turns if each.record.turn_id == turn]
    effect = stored.record.effect(1)
    theirs = replace(effect, state="accepting", approved_by=OWNER, event_id="e-elsewhere", accepted_on=OTHER_DEVICE)
    at.runner.scenes.write(stored.record.with_effect(theirs), stored)
    return theirs.event_id


@pytest.mark.parametrize("identified", [False, True])
def test_an_acceptance_cut_short_on_another_device_is_settled_only_there(cards, identified):
    folder, chat = cards
    with table(folder, UNOPPOSED, UNOPPOSED) as at:
        if identified:
            identify(at.store, "phone")
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        event_id = _accepting_arrives(at, chat, turn)
        problems = at.runner.open(chat).problems
        assert ("effect_elsewhere", f"{turn}:{event_id}:{OTHER_DEVICE}") in problems
        assert unsettled(at, chat) == []  # never read as unpublished here: 021's files of it are on that device
        error = refused("accepted_elsewhere", at.runner.recover_effects, chat, at.owner)
        assert OTHER_DEVICE in str(error)
        refused("accepted_elsewhere", at.runner.recover_effects, chat, at.owner, abort=True, endorsers=[at.player()])
        current = stored_effect(at, chat, turn)
        assert (current.state, current.event_id, current.accepted_on) == ("accepting", event_id, OTHER_DEVICE)
        refused("effect_accepting", at.runner.propose_effect, chat, at.owner, turn, MET, knowing(at, chat))
        at.runner.pass_turn(chat, at.player())
        second, _, _ = decided(at, chat, "*I bow*")
        at.runner.propose_effect(chat, at.owner, second, "Anya bows.", knowing(at, chat))
        assert accepting.blockers(at.runner, chat) == ([], 0)  # this device's own acceptances go on
        assert at.runner.approve_effect(chat, at.owner, second, 1).state == "accepted"
        assert stored_effect(at, chat, turn).state == "accepting"  # still that device's to settle


def test_an_acceptance_records_its_device_and_is_recovered_there_as_before(cards, monkeypatch):
    folder, chat = cards
    with table(folder) as at:
        identify(at.store, "laptop")
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        crash_after(monkeypatch, relations=False, event=False)
        with pytest.raises(Crash):
            at.runner.approve_effect(chat, at.owner, turn, 1)
    monkeypatch.undo()
    with restart(folder) as at:
        current = stored_effect(at, chat, turn)
        assert current.state == "accepting" and current.accepted_on == _tag("laptop")
        assert unsettled(at, chat) == [f"{turn}:{current.event_id}:unpublished"]
        (settled,) = at.runner.recover_effects(chat, at.owner)
        assert (settled.state, settled.event_id, settled.accepted_on) == ("approved", None, None)
        accepted = at.runner.approve_effect(chat, at.owner, turn, 1)
        assert (accepted.state, accepted.accepted_on) == ("accepted", _tag("laptop"))
        assert stored_effect(at, chat, turn).accepted_on == _tag("laptop")  # kept, like controls_at


def test_an_effect_recorded_without_its_device_is_this_devices(cards, monkeypatch):
    folder, chat = cards
    with table(folder) as at:  # no identity: the acceptance names no device, as every older record
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        crash_after(monkeypatch, relations=True, event=True)
        with pytest.raises(Crash):
            at.runner.approve_effect(chat, at.owner, turn, 1)
        monkeypatch.undo()
        assert stored_effect(at, chat, turn).accepted_on is None
        identify(at.store, "desktop")  # identified since: still settled here
        assert accepting.blockers(at.runner, chat)[0] != []
        assert at.runner.recover_effects(chat, at.owner)[0].state == "accepted"


def test_a_damaged_device_identity_starts_no_acceptance_and_settles_none(cards, monkeypatch):
    folder, chat = cards
    with table(folder) as at:
        identify(at.store, "laptop")
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        (folder / ".sync" / "device.json").write_bytes(b"{not json")
        refused("device_identity", at.runner.approve_effect, chat, at.owner, turn, 1)
        assert stored_effect(at, chat, turn).state == "proposed" and events(at, chat).events == ()
        identify(at.store, "laptop")  # repaired
        crash_after(monkeypatch, relations=True, event=True)
        with pytest.raises(Crash):
            at.runner.approve_effect(chat, at.owner, turn, 1)
        monkeypatch.undo()
        (folder / ".sync" / "device.json").write_bytes(b"{not json")
        refused("device_identity", at.runner.recover_effects, chat, at.owner)  # whose it is cannot be told
        assert stored_effect(at, chat, turn).state == "accepting"


def test_a_turn_whose_effect_another_device_accepted_takes_no_other_here(cards):
    folder, chat = cards
    with table(folder) as at:
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        _accepting_arrives(at, chat, turn)
        (stored,) = [each for each in at.runner.open(chat).turns.turns if each.record.turn_id == turn]
        landed = replace(stored.record.effect(1), state="accepted")  # that device's mark, synced here
        at.runner.scenes.write(stored.record.with_effect(landed), stored)
        assert at.runner.open(chat).problems == () and accepting.blockers(at.runner, chat) == ([], 0)
        error = refused("accepted_elsewhere", at.runner.propose_effect, chat, at.owner, turn, MET, knowing(at, chat))
        assert OTHER_DEVICE in str(error) and stored_effect(at, chat, turn) == landed  # never read as revoked


def _cut_short_shape_b(at: Table, chat, monkeypatch) -> str:
    turn, effect = proposed(at, chat)
    at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
    crash_after(monkeypatch, relations=True, event=False)  # shape B: the changes landed, not the event file
    with pytest.raises(Crash):
        at.runner.approve_effect(chat, at.owner, turn, 1)
    monkeypatch.undo()
    return turn


@pytest.mark.parametrize("how", ["reidentified", "identity_lost"])
def test_this_devices_partial_acceptance_is_still_settled_here_after_its_identity_changed(cards, monkeypatch, how):
    folder, chat = cards
    with table(folder, UNOPPOSED, UNOPPOSED) as at:
        identify(at.store, "laptop")
        turn = _cut_short_shape_b(at, chat, monkeypatch)
        if how == "reidentified":
            identify(at.store, "desktop", replace=True)  # the fix for a library copied with its .sync
        else:
            shutil.rmtree(folder / ".sync")  # reads as never identified
        assert stored_effect(at, chat, turn).accepted_on == _tag("laptop")
        assert unsettled(at, chat)[0].endswith(":adopt")  # what landed of it is in this library's 021
        assert not any(code == "effect_elsewhere" for code, _ in at.runner.open(chat).problems)
        at.runner.pass_turn(chat, at.player())
        second, _, _ = decided(at, chat, "*I bow*")
        at.runner.propose_effect(chat, at.owner, second, "Anya bows.", knowing(at, chat))
        refused("recovery_needed", at.runner.approve_effect, chat, at.owner, second, 1)  # never over the partial
        (settled,) = at.runner.recover_effects(chat, at.owner)
        assert settled.state == "accepted" and unsettled(at, chat) == []
        refused("already_accepted", at.runner.propose_effect, chat, at.owner, turn, MET, knowing(at, chat))
        assert at.runner.approve_effect(chat, at.owner, second, 1).state == "accepted"


def test_an_untagged_acceptance_with_nothing_of_it_here_waits_for_the_owners_word(cards):
    folder, chat = cards
    with table(folder, UNOPPOSED, UNOPPOSED) as at:
        identify(at.store, "phone")
        turn, effect = proposed(at, chat)
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        event_id = _accepting_arrives(at, chat, turn)
        (stored,) = [each for each in at.runner.open(chat).turns.turns if each.record.turn_id == turn]
        untagged = replace(stored.record.effect(1), accepted_on=None)  # a device with no identity, or an old record
        at.runner.scenes.write(stored.record.with_effect(untagged), stored)
        assert ("effect_untagged", f"{turn}:{event_id}") in at.runner.open(chat).problems
        assert unsettled(at, chat) == [] and accepting.blockers(at.runner, chat) == ([], 0)
        refused("untagged_acceptance", at.runner.recover_effects, chat, at.owner)
        refused("untagged_acceptance", at.runner.recover_effects, chat, at.owner, abort=True,
                endorsers=[at.player()])
        assert stored_effect(at, chat, turn) == untagged  # never put back or dropped on a guess
        refused("invalid_field", at.runner.recover_effects, chat, at.owner, own="yes")
        (settled,) = at.runner.recover_effects(chat, at.owner, own=True)  # the owner says it is this library's
        assert (settled.state, settled.event_id) == ("approved", None)


def test_the_refusal_of_acceptances_left_elsewhere_counts_first_and_caps_its_list(cards):
    folder, chat = cards
    with table(folder, *[UNOPPOSED] * 4) as at:
        for number in range(4):
            if number:
                at.runner.pass_turn(chat, at.player())
            turn, _, _ = decided(at, chat, f"*I wave {number}*")
            at.runner.propose_effect(chat, at.owner, turn, "Mara notices.", knowing(at, chat))
            _accepting_arrives(at, chat, turn)
        error = str(refused("accepted_elsewhere", at.runner.recover_effects, chat, at.owner))
        assert "0 settled here" in error and "2 more" in error and len(error) <= 240
