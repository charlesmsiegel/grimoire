"""035 x 020 x 041: an effect accepted in play leaves its 012 event, and who saw it, in 020 exactly once.

Over the synthetic card library (``scene_fixtures``): Mara Vell's card chat with Anya Rook as the player's persona
(``human:ana`` plays her) and Tamsin Hale, a clerk attached to the chat, whom the director plays. Anya and Mara
stand in the hall; Tamsin is in the study behind its shut door. The app's stage comes from a ``StageBook``; a
restart gets a new book, which the app fills again. A crash is a ``Crash`` raised at the step named, which no
handler catches: the process is gone, and the next ``Table`` is the restart.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace

import pytest

from generation_fixtures import say
from scene_fixtures import ANA, LABEL_POLICY, Table, attach_tamsin, card_library
from store_helpers import OWNER

from grimoire.core import DIRECTOR, Event, Moment
from grimoire.core.identity import AcceptedRef
from grimoire.effects import Op, as_knowledge_event, revocation_fingerprint
from grimoire.effectstore import EffectChange, Endorsement
from grimoire.evidencestore import EvidenceLibrary, KnowledgeChange
from grimoire.play.stages import StageBook
from grimoire.scene import SceneRefused
from grimoire.staging import Absence, Area, Placement, Stage, Way

UNOPPOSED = json.dumps({"factors": [{"fact": "f1", "bearing": "favours"}], "stakes": []})
MET = "Anya and Mara shake hands."
NOW = 2**62


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


def table(folder, book, *, replies=2) -> Table:
    return Table(folder, policy=LABEL_POLICY, labels=[say(UNOPPOSED) for _ in range(replies)], adjudicate=True,
                 effects=True, stages=book)


def restart(folder, book) -> Table:
    """The library opened again after the process died: the same keys, no labeller."""
    return Table(folder, policy=LABEL_POLICY, adjudicate=True, effects=True, stages=book)


def stage(chat, anya, mara, tamsin, *, as_of=1, mara_in="hall", basis="established", tamsin_gone=False,
          door="closed") -> Stage:
    """The hall and the study, joined by the study door. Anya in the hall; Tamsin in the study or gone."""
    placed = [Placement(anya, area="hall", basis=basis), Placement(mara, area=mara_in)]
    if not tamsin_gone:
        placed.append(Placement(tamsin, area="study"))
    return Stage(chat.campaign_id, chat.chat_id, as_of, (Area("hall"), Area("study")),
                 (Way("study-door", ("hall", "study"), door),), (anya, mara, tamsin), tuple(placed),
                 (Absence(tamsin),) if tamsin_gone else ())


def cast(at: Table, chat):
    """Tamsin attached, everyone seated: Anya, Mara and Tamsin."""
    tamsin = attach_tamsin(at.store, at.owner, chat)
    at.runner.seat(chat, ANA)
    scene = at.runner.open(chat)
    (mara,) = [actor for actor in scene.cast if actor not in (scene.persona, tamsin)]
    return scene.persona, mara, tamsin


def decided(at: Table, chat, text: str = "*I offer Mara my hand*") -> str:
    """Anya's visible action, framed and decided by rule: its turn id."""
    record = at.runner.post(chat, at.player(), text)
    at.runner.label(chat, at.player())
    assert at.runner.confirm(chat, at.player()) is not None
    return record.turn_id


def accepted(at: Table, chat, anya, mara, turn: str, summary: str = MET, predicate: str = "has_met"):
    """Anya's own act, endorsed by her player and approved by the director (the last key)."""
    effect = at.runner.propose_effect(chat, at.player(), turn, summary, [Op("begin", predicate, anya, mara, anya,
                                                                            turn)])
    at.runner.endorse_effect(chat, at.player(), turn, effect.number, effect.proposal.fingerprint)
    return at.runner.approve_effect(chat, at.owner, turn, effect.number)


def evidence(at: Table) -> EvidenceLibrary:
    return EvidenceLibrary(at.store, at.owner)


def seen(at: Table, chat, actor) -> list[tuple[str, str]]:
    """What ``actor`` itself knows now: each observation's channel and content."""
    known = evidence(at).actor_knowledge(chat.campaign_id, actor, as_of=NOW)
    return [(item.channel, item.content) for item in known.evidence]


def stored(at: Table, chat):
    return evidence(at).read(chat.campaign_id, requested_by=DIRECTOR)


def unbridged(at: Table, chat) -> list[str]:
    return [detail for code, detail in at.runner.open(chat).problems if code == "unbridged"]


def bridge_of(at: Table, chat, event_id: str) -> Event:
    event, _ = at.effects.read(AcceptedRef(chat.campaign_id, event_id), requested_by=DIRECTOR)
    return as_knowledge_event(event)


def no_bridge_append(monkeypatch) -> None:
    """The process dies just before 020's append commits: 021's commit and the accepted mark have landed."""
    def commit(change, approvals=None):
        raise Crash
    monkeypatch.setattr(KnowledgeChange, "commit", commit)


# -- the proving scenario -------------------------------------------------------------------------------

def test_an_accepted_effect_is_known_to_who_saw_it_and_to_no_one_else(cards):
    folder, chat = cards
    book = StageBook()
    with table(folder, book) as at:
        anya, mara, tamsin = cast(at, chat)
        book.put(chat, stage(chat, anya, mara, tamsin))
        effect = accepted(at, chat, anya, mara, decided(at, chat))
        assert effect.state == "accepted"
        assert seen(at, chat, mara) == [("witnessed", MET)]  # in the hall with Anya
        assert seen(at, chat, anya) == [("witnessed", MET)]  # her own act, seen
        assert seen(at, chat, tamsin) == []  # behind the shut door: nothing
        (own,) = evidence(at).player_knowledge(chat.campaign_id, ANA, at.runner.open(chat).controls,
                                               as_of=NOW).actors
        assert own.actor == anya and [item.content for item in own.evidence] == [MET]
        ledger = stored(at, chat)
        assert ledger.knowledge.events == (bridge_of(at, chat, effect.event_id),)  # 021's own bridge, once
        assert sorted(str(item.holder) for item in ledger.knowledge.evidence) == sorted([str(anya), str(mara)])
        assert all(item.event_id == effect.event_id and item.confirmed for item in ledger.knowledge.evidence)
        # 020 keeps events and evidence apart: the event file names no witness
        (event_file,) = (folder / "campaigns" / chat.campaign_id / "events").glob("*.json")
        assert str(tamsin) not in event_file.read_text() and "witnessed" not in event_file.read_text()
        assert unbridged(at, chat) == [] and at.runner.recover_effects(chat, at.owner) == ()
        assert len(stored(at, chat).knowledge.evidence) == 2  # a re-run writes nothing twice
    with restart(folder, StageBook()) as again:  # reopened: the same knowledge, nothing more
        assert seen(again, chat, mara) == [("witnessed", MET)] and seen(again, chat, tamsin) == []
        assert unbridged(again, chat) == []


def test_an_actor_off_stage_or_seen_through_an_open_door(cards):
    folder, chat = cards
    book = StageBook()
    with table(folder, book) as at:
        anya, mara, tamsin = cast(at, chat)
        book.put(chat, stage(chat, anya, mara, tamsin, mara_in="study", tamsin_gone=True, door="open"))
        accepted(at, chat, anya, mara, decided(at, chat))
        assert seen(at, chat, mara) == [("witnessed", MET)]  # from the study, through the open door
        assert seen(at, chat, tamsin) == []  # she had left the scene


@pytest.mark.parametrize("staging", ["assumed", "none", "newer"])
def test_without_a_stage_to_judge_by_the_event_is_written_and_nobody_saw_it(cards, staging):
    """Fail closed, never on a guess: an acting character placed only by assumption, a chat with no stage (the
    cast alone says who is present, not who could see), or a stage newer than the event (``Stage.at`` cannot go
    back to the event's sequence) leaves the event in 020 with no witness."""
    folder, chat = cards
    book = StageBook()
    with table(folder, book) as at:
        anya, mara, tamsin = cast(at, chat)
        if staging != "none":
            book.put(chat, stage(chat, anya, mara, tamsin))
        turn = decided(at, chat)
        if staging == "assumed":
            book.put(chat, stage(chat, anya, mara, tamsin, as_of=2, basis="assumed"))
        elif staging == "newer":
            book.put(chat, stage(chat, anya, mara, tamsin, as_of=99))
        effect = accepted(at, chat, anya, mara, turn)
        ledger = stored(at, chat)
        assert ledger.knowledge.events == (bridge_of(at, chat, effect.event_id),)
        assert ledger.knowledge.evidence == ()
        assert unbridged(at, chat) == []


# -- a crash between 021's commit and the 020 append ----------------------------------------------------

def test_a_crash_before_the_knowledge_append_is_completed_by_recovery_exactly_once(cards, monkeypatch):
    folder, chat = cards
    book = StageBook()
    with table(folder, book) as at:
        anya, mara, tamsin = cast(at, chat)
        first = stage(chat, anya, mara, tamsin)
        book.put(chat, first)
        turn = decided(at, chat)
        no_bridge_append(monkeypatch)
        with pytest.raises(Crash):
            accepted(at, chat, anya, mara, turn)
    monkeypatch.undo()
    restaged = StageBook()
    restaged.put(chat, first)  # the app stages the scene again after the restart
    with restart(folder, restaged) as at:
        (effect,) = [item for record in at.runner.open(chat).records for item in record.effects]
        assert effect.state == "accepted"  # 021 holds it; 020 does not yet
        assert unbridged(at, chat) == [f"{turn}:{effect.event_id}"]
        assert stored(at, chat).knowledge.events == () and seen(at, chat, mara) == []
        assert at.runner.recover_effects(chat, at.owner) == ()  # nothing was cut short in 021; 020 is written
        assert seen(at, chat, mara) == [("witnessed", MET)] and seen(at, chat, tamsin) == []
        assert unbridged(at, chat) == [] and at.runner.recover_effects(chat, at.owner) == ()
        assert len(stored(at, chat).knowledge.events) == 1 and len(stored(at, chat).knowledge.evidence) == 2


def test_a_crash_after_the_021_commit_is_recovered_and_bridged_in_one_step(cards, monkeypatch):
    folder, chat = cards
    book = StageBook()
    with table(folder, book) as at:
        anya, mara, tamsin = cast(at, chat)
        first = stage(chat, anya, mara, tamsin)
        book.put(chat, first)
        turn = decided(at, chat)
        commit = EffectChange.commit

        def published(change, approvals=None):  # every 021 file lands; then the process dies
            commit(change, approvals)
            raise Crash
        monkeypatch.setattr(EffectChange, "commit", published)
        with pytest.raises(Crash):
            accepted(at, chat, anya, mara, turn)
    monkeypatch.undo()
    restaged = StageBook()
    restaged.put(chat, first)
    with restart(folder, restaged) as at:
        (settled,) = at.runner.recover_effects(chat, at.owner)
        assert settled.state == "accepted"
        assert stored(at, chat).knowledge.events == (bridge_of(at, chat, settled.event_id),)
        assert seen(at, chat, mara) == [("witnessed", MET)] and seen(at, chat, tamsin) == []


def test_a_crash_after_the_knowledge_append_never_writes_it_twice(cards, monkeypatch):
    folder, chat = cards
    book = StageBook()
    with table(folder, book) as at:
        anya, mara, tamsin = cast(at, chat)
        book.put(chat, stage(chat, anya, mara, tamsin))
        turn = decided(at, chat)
        commit = KnowledgeChange.commit

        def appended(change, approvals=None):  # 020's append lands whole; then the process dies
            commit(change, approvals)
            raise Crash
        monkeypatch.setattr(KnowledgeChange, "commit", appended)
        with pytest.raises(Crash):
            accepted(at, chat, anya, mara, turn)
    monkeypatch.undo()
    with restart(folder, StageBook()) as at:  # no stage now: nothing is re-judged, nothing is written again
        assert unbridged(at, chat) == [] and at.runner.recover_effects(chat, at.owner) == ()
        assert len(stored(at, chat).knowledge.events) == 1 and seen(at, chat, mara) == [("witnessed", MET)]


def test_the_next_acceptance_writes_an_earlier_unwritten_bridge_first(cards, monkeypatch):
    folder, chat = cards
    book = StageBook()
    with table(folder, book, replies=2) as at:
        anya, mara, tamsin = cast(at, chat)
        book.put(chat, stage(chat, anya, mara, tamsin))
        first = decided(at, chat)
        no_bridge_append(monkeypatch)
        with pytest.raises(Crash):
            accepted(at, chat, anya, mara, first)
        monkeypatch.undo()
        at.runner.pass_turn(chat, at.player())
        second = decided(at, chat, "*I offer Mara a seat*")
        later = accepted(at, chat, anya, mara, second, "Anya offers Mara a seat.", "allied_with")
        assert later.state == "accepted", later.reason
        events = stored(at, chat).knowledge.events
        assert [event.summary for event in events] == [MET, "Anya offers Mara a seat."]  # in sequence order
        assert seen(at, chat, mara) == [("witnessed", MET), ("witnessed", "Anya offers Mara a seat.")]


def test_knowledge_waits_for_an_earlier_acceptance_still_cut_short_on_another_device(cards, monkeypatch):
    folder, chat = cards
    book = StageBook()
    with table(folder, book, replies=2) as at:
        anya, mara, tamsin = cast(at, chat)
        book.put(chat, stage(chat, anya, mara, tamsin))
        first = decided(at, chat)
        proposal = at.runner.propose_effect(chat, at.player(), first, MET, [Op("begin", "has_met", anya, mara, anya,
                                                                                first)])
        at.runner.endorse_effect(chat, at.player(), first, 1, proposal.proposal.fingerprint)
        at.runner.pass_turn(chat, at.player())
        second = decided(at, chat, "*I offer Mara a seat*")
        no_bridge_append(monkeypatch)
        with pytest.raises(Crash):
            accepted(at, chat, anya, mara, second, "Anya offers Mara a seat.", "allied_with")
        monkeypatch.undo()
        # the first turn's acceptance arrives from another device, cut short there (as a sync brings turns)
        (stored_first,) = [each for each in at.runner.open(chat).turns.turns if each.record.turn_id == first]
        theirs = replace(stored_first.record.effect(1), state="accepting", approved_by=OWNER, event_id="e-elsewhere",
                         accepted_on="0badcafe")
        at.runner.scenes.write(stored_first.record.with_effect(theirs), stored_first)
        assert theirs.proposal.moment.sequence == proposal.proposal.moment.sequence
        refused("accepted_elsewhere", at.runner.recover_effects, chat, at.owner)
        # written now, the later knowledge would leave the first acceptance's own behind 020's mark for good
        assert stored(at, chat).knowledge.events == () and seen(at, chat, mara) == []
        (pending,) = unbridged(at, chat)
        assert pending.startswith(f"{second}:") and not pending.endswith(":backdated")


def test_a_bridge_the_stage_cannot_judge_now_waits_until_the_scene_is_staged_again(cards):
    folder, chat = cards
    book = StageBook()
    with table(folder, book, replies=2) as at:
        anya, mara, tamsin = cast(at, chat)
        turn = decided(at, chat)
        effect = at.runner.propose_effect(chat, at.player(), turn, MET, [Op("begin", "has_met", anya, mara, anya,
                                                                              turn)])
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        book.put(chat, Stage(chat.campaign_id, chat.chat_id, 1, (Area("hall"),), (), (anya, mara),
                             (Placement(anya, area="hall"), Placement(mara, area="hall"))))  # Tamsin missing
        assert ("stage", "cast_mismatch") in at.runner.open(chat).problems
        done = at.runner.approve_effect(chat, at.owner, turn, 1)
        assert done.state == "accepted" and stored(at, chat).knowledge.events == ()  # who saw it cannot be told
        assert unbridged(at, chat) == [f"{turn}:{done.event_id}"]
        refused("unbridged", at.runner.recover_effects, chat, at.owner)
        # the scene staged again, as it stood at the event's sequence
        book.put(chat, stage(chat, anya, mara, tamsin, as_of=effect.proposal.moment.sequence))
        assert at.runner.recover_effects(chat, at.owner) == ()
        assert seen(at, chat, mara) == [("witnessed", MET)] and unbridged(at, chat) == []


# -- revoked, and behind the knowledge ledger -----------------------------------------------------------

def test_an_effect_revoked_before_its_bridge_is_never_written_as_fact(cards, monkeypatch):
    folder, chat = cards
    book = StageBook()
    with table(folder, book) as at:
        anya, mara, tamsin = cast(at, chat)
        book.put(chat, stage(chat, anya, mara, tamsin))
        turn = decided(at, chat)
        no_bridge_append(monkeypatch)
        with pytest.raises(Crash):
            accepted(at, chat, anya, mara, turn)
        monkeypatch.undo()
        (effect,) = [item for record in at.runner.open(chat).records for item in record.effects]
        ref = AcceptedRef(chat.campaign_id, effect.event_id)
        event, etag = at.effects.read(ref, requested_by=DIRECTOR)
        undo = [Endorsement(item.contribution_id, at.player(), revocation_fingerprint(ref, event.fingerprint))
                for item in event.endorsements]
        at.effects.revoke(ref, reason="a mistake", expected=etag, endorsements=undo).commit()
        assert unbridged(at, chat) == [] and at.runner.recover_effects(chat, at.owner) == ()
        assert stored(at, chat).knowledge.events == () and seen(at, chat, mara) == []


def test_an_effect_behind_knowledge_written_since_its_proposal_is_proposed_again(cards):
    """020 never takes a backdated record, so an effect whose sequence knowledge has passed since it was proposed
    is refused ``stale_sequence`` before 021 commits it: proposed again, it takes a later sequence."""
    folder, chat = cards
    book = StageBook()
    with table(folder, book) as at:
        anya, mara, tamsin = cast(at, chat)
        book.put(chat, stage(chat, anya, mara, tamsin))
        turn = decided(at, chat)
        effect = at.runner.propose_effect(chat, at.player(), turn, MET, [Op("begin", "has_met", anya, mara, anya,
                                                                              turn)])
        at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        later = effect.proposal.moment.sequence + 5
        evidence(at).append(chat.campaign_id, [Event(chat.campaign_id, "e-rain", "Rain starts.", Moment(later),
                                                     "director")]).commit()
        assert at.runner.approve_effect(chat, at.owner, turn, 1).reason == "stale_sequence"
        assert at.effects.continuity(chat.campaign_id, as_of=NOW, requested_by=DIRECTOR).events == ()
        book.put(chat, stage(chat, anya, mara, tamsin, as_of=later + 1))
        again = accepted(at, chat, anya, mara, turn)
        assert again.state == "accepted" and again.proposal.moment.sequence > later
        assert seen(at, chat, mara) == [("witnessed", MET)]

