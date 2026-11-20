"""035 x 020 x 041: an effect accepted in play leaves its 012 event, and who saw it, in 020 exactly once.

Over the synthetic card library (``scene_fixtures``): Mara Vell's card chat with Anya Rook as the player's persona
(``human:ana`` plays her) and Tamsin Hale, a clerk attached to the chat, whom Ana plays too unless a test gives
her to Bob or to no one; the director plays Mara. Anya and Mara stand in the hall; Tamsin is in the study behind
its shut door. Only a character whose player holds a key on an effect may witness it: Mara never does. The app's stage comes from a ``StageBook``; a
restart gets a new book, which the app fills again. A crash is a ``Crash`` raised at the step named, which no
handler catches: the process is gone, and the next ``Table`` is the restart.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace

import pytest

from chat_fixtures import Clock
from generation_fixtures import say
from scene_fixtures import ANA, ANYA, BOB, LABEL_POLICY, MARA_VELL, Table, Tagged, attach_tamsin, card_library
from store_helpers import OWNER

from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.core import DIRECTOR, Event, Moment
from grimoire.core.identity import AcceptedRef, ScopeRef
from grimoire.effects import Op, as_knowledge_event, revocation_fingerprint
from grimoire.effectstore import EffectChange, Endorsement
from grimoire.evidencestore import EvidenceLibrary, KnowledgeChange
from grimoire.ledgers.codec import ControlChange
from grimoire.play.stages import StageBook
from grimoire.scene import SceneRefused
from grimoire.staging import Absence, Area, Placement, Stage, Way
from grimoire.sync import NoDeviceIdentity, device_of, identify
from grimoire.sync.documents import render_keeps

UNOPPOSED = json.dumps({"factors": [{"fact": "f1", "bearing": "favours"}], "stakes": []})
MET = "Anya and Mara shake hands."
SECRET = "Tamsin, unseen, passes Mara the Duke's letter: Tamsin is the Duke's spy."
SPY = "Anya grips Mara's hand; Mara is the Duke's spy."
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
          door="closed", tamsin_in="study") -> Stage:
    """The hall and the study, joined by the study door. Anya in the hall; Tamsin in the study, elsewhere or gone."""
    placed = [Placement(anya, area="hall", basis=basis), Placement(mara, area=mara_in)]
    if not tamsin_gone:
        placed.append(Placement(tamsin, area=tamsin_in))
    return Stage(chat.campaign_id, chat.chat_id, as_of, (Area("hall"), Area("study")),
                 (Way("study-door", ("hall", "study"), door),), (anya, mara, tamsin), tuple(placed),
                 (Absence(tamsin),) if tamsin_gone else ())


def give(at: Table, chat, actor, player) -> None:
    at.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, actor.instance_id, player),
                                 at.owner)


def cast(at: Table, chat, tamsin_to: str | None = ANA):
    """Tamsin attached (played by ``tamsin_to``, or the director's), everyone seated: Anya, Mara and Tamsin."""
    tamsin = attach_tamsin(at.store, at.owner, chat)
    if tamsin_to is not None:
        give(at, chat, tamsin, tamsin_to)
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
    """What ``open`` (which first writes whatever pending knowledge it can) still counts for this chat."""
    return [detail for code, detail in at.runner.open(chat).problems if code == "unbridged"]


def raw_effect(at: Table, chat, turn: str):
    """A turn's effect as its file holds it, read without ``open`` (which would write its knowledge)."""
    (stored_turn,) = [each for each in at.runner.scenes.turns(chat).turns if each.record.turn_id == turn]
    return stored_turn.record.effect(1)


def bridge_of(at: Table, chat, event_id: str) -> Event:
    event, _ = at.effects.read(AcceptedRef(chat.campaign_id, event_id), requested_by=DIRECTOR)
    return as_knowledge_event(event)


def paired(store, peer: str = "laptop") -> None:
    """The library paired with another device (identified, if it was not), as a sync with no conflict leaves it:
    that pair's kept-version record, holding nothing."""
    try:
        device = device_of(store)
    except NoDeviceIdentity:
        identify(store, device := "desk")
    found = store.root / ".sync" / "peers" / peer
    found.mkdir(parents=True, exist_ok=True)
    (found / "keeps.json").write_bytes(render_keeps(tuple(sorted((device, peer))), {}))


def full_disk(monkeypatch) -> None:
    """020's append fails before its commit point with the raw error a full disk gives."""
    def commit(change, approvals=None):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(KnowledgeChange, "commit", commit)


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
        assert seen(at, chat, anya) == [("witnessed", MET)]  # her own act, seen
        assert seen(at, chat, mara) == []  # in the hall, but an NPC: its knowledge would outlive a hand-over
        assert seen(at, chat, tamsin) == []  # Ana's too, but behind the shut door: nothing
        own = evidence(at).player_knowledge(chat.campaign_id, ANA, at.runner.open(chat).controls, as_of=NOW).actors
        assert {item.actor: [known.content for known in item.evidence] for item in own} == {anya: [MET], tamsin: []}
        ledger = stored(at, chat)
        assert ledger.knowledge.events == (bridge_of(at, chat, effect.event_id),)  # 021's own bridge, once
        assert [item.holder for item in ledger.knowledge.evidence] == [anya]
        assert all(item.event_id == effect.event_id and item.confirmed for item in ledger.knowledge.evidence)
        # 020 keeps events and evidence apart: the event file names no witness
        (event_file,) = (folder / "campaigns" / chat.campaign_id / "events").glob("*.json")
        assert str(tamsin) not in event_file.read_text() and "witnessed" not in event_file.read_text()
        assert unbridged(at, chat) == [] and at.runner.recover_effects(chat, at.owner) == ()
        assert len(stored(at, chat).knowledge.evidence) == 1  # a re-run writes nothing twice
    with restart(folder, StageBook()) as again:  # reopened: the same knowledge, nothing more
        assert seen(again, chat, anya) == [("witnessed", MET)] and seen(again, chat, tamsin) == []
        assert unbridged(again, chat) == []


def test_a_director_only_effect_is_known_to_no_witness_and_the_tables_own_is(cards):
    """The director's own NPC act (no player holds a key on it, so the table's turn view hides it) leaves its event
    in 020 and no perception: its summary is the director's words, which no witness, and so no player, may read.
    Tamsin is the director's here too."""
    folder, chat = cards
    book = StageBook()
    with table(folder, book) as at:
        anya, mara, tamsin = cast(at, chat, tamsin_to=None)
        book.put(chat, stage(chat, anya, mara, tamsin, tamsin_in="hall"))  # all three in the hall
        turn = decided(at, chat)
        secret = at.runner.propose_effect(chat, at.owner, turn, SECRET, [Op("begin", "allied_with", tamsin, mara,
                                                                               tamsin, None)])
        assert secret.endorsers == ()
        done = at.runner.approve_effect(chat, at.owner, turn, 1)
        assert done.state == "accepted", done.reason
        assert stored(at, chat).knowledge.events == (bridge_of(at, chat, done.event_id),)
        assert stored(at, chat).knowledge.evidence == ()
        assert seen(at, chat, anya) == [] and seen(at, chat, mara) == [] and seen(at, chat, tamsin) == []
        table_view = evidence(at).player_knowledge(chat.campaign_id, ANA, at.runner.open(chat).controls, as_of=NOW)
        assert SECRET not in repr(table_view)
        assert unbridged(at, chat) == []  # written, as the event alone: nothing waits
        at.runner.pass_turn(chat, at.player())
        later = accepted(at, chat, anya, mara, decided(at, chat, "*I offer Mara my hand*"))
        assert later.state == "accepted", later.reason
        assert seen(at, chat, anya) == [("witnessed", MET)]  # Ana's own character, shown it
        assert seen(at, chat, mara) == seen(at, chat, tamsin) == []  # the director's


@pytest.mark.parametrize("gone", [False, True])
def test_an_actor_seen_through_an_open_door_or_off_stage(cards, gone):
    folder, chat = cards
    book = StageBook()
    with table(folder, book) as at:
        anya, mara, tamsin = cast(at, chat)
        book.put(chat, stage(chat, anya, mara, tamsin, mara_in="study", tamsin_gone=gone, door="open"))
        accepted(at, chat, anya, mara, decided(at, chat))
        # from the study, through the open door; nothing once she has left the scene
        assert seen(at, chat, tamsin) == ([] if gone else [("witnessed", MET)])
        assert seen(at, chat, mara) == []


# -- whose player was shown it --------------------------------------------------------------------------

def test_another_players_character_holds_no_key_and_sees_nothing(cards):
    """Bob plays Tamsin, in the hall with Anya: Anya's act is Ana's alone to endorse, so Bob was never shown its
    summary, and Tamsin's evidence (Bob's to read) gets none of it."""
    folder, chat = cards
    book = StageBook()
    with table(folder, book) as at:
        anya, mara, tamsin = cast(at, chat, tamsin_to=BOB)
        book.put(chat, stage(chat, anya, mara, tamsin, tamsin_in="hall"))
        assert accepted(at, chat, anya, mara, decided(at, chat)).state == "accepted"
        assert seen(at, chat, anya) == [("witnessed", MET)] and seen(at, chat, tamsin) == []
        bob = evidence(at).player_knowledge(chat.campaign_id, BOB, at.runner.open(chat).controls, as_of=NOW)
        assert MET not in repr(bob)


def test_a_directors_proposal_carrying_a_players_key_is_seen_by_that_players_characters_only(cards):
    """The director proposes Anya's act, so Ana's key is on it: Ana's characters there witness it. Mara, the NPC
    beside them, gets nothing, and handed to Bob afterwards she carries nothing of it to him."""
    folder, chat = cards
    book = StageBook()
    with table(folder, book) as at:
        anya, mara, tamsin = cast(at, chat)
        book.put(chat, stage(chat, anya, mara, tamsin, tamsin_in="hall"))
        turn = decided(at, chat)
        effect = at.runner.propose_effect(chat, at.owner, turn, SPY, [Op("begin", "has_met", anya, mara, anya, turn)])
        assert [who for _, who in effect.endorsers] == [ANA]
        at.runner.endorse_effect(chat, at.player(), turn, effect.number, effect.proposal.fingerprint)
        assert at.runner.approve_effect(chat, at.owner, turn, effect.number).state == "accepted"
        assert seen(at, chat, anya) == seen(at, chat, tamsin) == [("witnessed", SPY)]
        assert seen(at, chat, mara) == []
        give(at, chat, mara, BOB)  # the hand-over
        bob = evidence(at).player_knowledge(chat.campaign_id, BOB, at.runner.open(chat).controls, as_of=NOW)
        assert "Duke's spy" not in repr(bob)


@pytest.mark.parametrize("then, now", [(ANA, BOB), (BOB, ANA)])
def test_a_character_handed_over_before_its_knowledge_is_written_gets_none(cards, monkeypatch, then, now):
    """Tamsin changed hands between the acceptance and the writing of its knowledge: one of her two players was
    never shown the effect (Bob holds no key on it), so she gets nothing. Control is judged both then and now."""
    folder, chat = cards
    book = StageBook()
    with table(folder, book) as at:
        anya, mara, tamsin = cast(at, chat, tamsin_to=then)
        book.put(chat, stage(chat, anya, mara, tamsin, tamsin_in="hall"))
        turn = decided(at, chat)
        no_bridge_append(monkeypatch)
        with pytest.raises(Crash):
            accepted(at, chat, anya, mara, turn)
        monkeypatch.undo()
        give(at, chat, tamsin, now)
        assert unbridged(at, chat) == []  # written now
        assert seen(at, chat, anya) == [("witnessed", MET)] and seen(at, chat, tamsin) == []


@pytest.mark.parametrize("staging", ["assumed", "none", "newer", "refused"])
def test_without_a_stage_to_judge_by_the_event_is_written_and_nobody_saw_it(cards, staging):
    """Fail closed, never on a guess and never waiting: an acting character placed only by assumption, a chat with
    no stage (the cast alone says who is present, not who could see), a stage newer than the event (``Stage.at``
    cannot go back to the event's sequence), or one the runner refused leaves the event in 020 at once, with no
    witness."""
    folder, chat = cards
    book = StageBook()
    with table(folder, book) as at:
        anya, mara, tamsin = cast(at, chat)
        if staging not in ("none", "refused"):
            book.put(chat, stage(chat, anya, mara, tamsin))
        turn = decided(at, chat)
        if staging == "assumed":
            book.put(chat, stage(chat, anya, mara, tamsin, as_of=2, basis="assumed"))
        elif staging == "newer":
            book.put(chat, stage(chat, anya, mara, tamsin, as_of=99))
        elif staging == "refused":  # Tamsin is in the cast but not on it
            book.put(chat, Stage(chat.campaign_id, chat.chat_id, 1, (Area("hall"),), (), (anya, mara),
                                 (Placement(anya, area="hall"), Placement(mara, area="hall"))))
            assert ("stage", "cast_mismatch") in at.runner.open(chat).problems
        effect = accepted(at, chat, anya, mara, turn)
        ledger = stored(at, chat)
        assert ledger.knowledge.events == (bridge_of(at, chat, effect.event_id),)
        assert ledger.knowledge.evidence == ()
        assert unbridged(at, chat) == []


# -- a crash between 021's commit and the 020 append ----------------------------------------------------

def test_a_crash_before_the_knowledge_append_is_completed_on_open_exactly_once(cards, monkeypatch):
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
        assert raw_effect(at, chat, turn).state == "accepted"  # 021 holds it; 020 does not yet
        assert stored(at, chat).knowledge.events == () and seen(at, chat, anya) == []
        assert unbridged(at, chat) == []  # the first open writes it, with the stage it was played on
        assert seen(at, chat, anya) == [("witnessed", MET)] and seen(at, chat, tamsin) == []
        assert unbridged(at, chat) == [] and at.runner.recover_effects(chat, at.owner) == ()
        assert len(stored(at, chat).knowledge.events) == 1 and len(stored(at, chat).knowledge.evidence) == 1


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
        assert seen(at, chat, anya) == [("witnessed", MET)] and seen(at, chat, tamsin) == []


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
        assert len(stored(at, chat).knowledge.events) == 1 and seen(at, chat, anya) == [("witnessed", MET)]


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
        assert seen(at, chat, anya) == [("witnessed", MET), ("witnessed", "Anya offers Mara a seat.")]


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
        (stored_first,) = [each for each in at.runner.scenes.turns(chat).turns if each.record.turn_id == first]
        theirs = replace(stored_first.record.effect(1), state="accepting", approved_by=OWNER, event_id="e-elsewhere",
                         accepted_on="0badcafe")
        at.runner.scenes.write(stored_first.record.with_effect(theirs), stored_first)
        assert theirs.proposal.moment.sequence == proposal.proposal.moment.sequence
        refused("accepted_elsewhere", at.runner.recover_effects, chat, at.owner)
        # written now, the later knowledge would leave the first acceptance's own behind 020's mark for good
        assert stored(at, chat).knowledge.events == () and seen(at, chat, anya) == []
        assert unbridged(at, chat) == ["1:acceptance_unsettled"]  # counted, never named


def test_another_devices_acceptance_whose_knowledge_never_arrived_is_counted_never_written(cards, monkeypatch):
    """Its own device writes its knowledge, judged on the stage played there, and sync brings it: here it is only
    counted, so a device retired before it could (or a replaced identity) never strands it unseen."""
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
        paired(at.store)  # paired, so an acceptance under a tag never this library's arrived by sync
        (stored_turn,) = [each for each in at.runner.scenes.turns(chat).turns if each.record.turn_id == turn]
        theirs = replace(stored_turn.record.effect(1), accepted_on="0badcafe")
        at.runner.scenes.write(stored_turn.record.with_effect(theirs), stored_turn)
        assert unbridged(at, chat) == ["1:elsewhere"]
        assert stored(at, chat).knowledge.events == () and seen(at, chat, anya) == []
        assert unbridged(at, chat) == ["1:elsewhere"]  # still counted, still not written here


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
        ref = AcceptedRef(chat.campaign_id, raw_effect(at, chat, turn).event_id)
        event, etag = at.effects.read(ref, requested_by=DIRECTOR)
        undo = [Endorsement(item.contribution_id, at.player(), revocation_fingerprint(ref, event.fingerprint))
                for item in event.endorsements]
        at.effects.revoke(ref, reason="a mistake", expected=etag, endorsements=undo).commit()
        assert unbridged(at, chat) == [] and at.runner.recover_effects(chat, at.owner) == ()
        assert stored(at, chat).knowledge.events == () and seen(at, chat, anya) == []


def test_an_effect_behind_knowledge_written_since_its_proposal_is_accepted_and_its_knowledge_counted(cards):
    """020 never takes a backdated record, so an effect whose sequence knowledge has passed since it was proposed
    (020 records sync too) is still accepted, and its knowledge, which can never be written, is counted for the
    director as ``backdated``: play is never refused over 020."""
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
        done = at.runner.approve_effect(chat, at.owner, turn, 1)
        assert done.state == "accepted", done.reason
        assert [event.event_id for event in stored(at, chat).knowledge.events] == ["e-rain"]
        assert unbridged(at, chat) == ["1:backdated"] and seen(at, chat, anya) == []
        assert at.runner.recover_effects(chat, at.owner) == ()



def test_ordinary_play_between_a_proposal_and_its_acceptance_never_refuses_it(cards):
    """Labelling, confirming and deciding a later turn write no 020 record, so an effect proposed before them is
    still accepted, and its knowledge written, at its own sequence."""
    folder, chat = cards
    book = StageBook()
    with table(folder, book) as at:
        anya, mara, tamsin = cast(at, chat)
        book.put(chat, stage(chat, anya, mara, tamsin))
        first = decided(at, chat)
        effect = at.runner.propose_effect(chat, at.player(), first, MET, [Op("begin", "has_met", anya, mara, anya,
                                                                               first)])
        at.runner.endorse_effect(chat, at.player(), first, 1, effect.proposal.fingerprint)
        at.runner.pass_turn(chat, at.player())
        decided(at, chat, "*I offer Mara a seat*")  # the director's label and confirmation, at later sequences
        done = at.runner.approve_effect(chat, at.owner, first, 1)
        assert done.state == "accepted", done.reason
        assert seen(at, chat, anya) == [("witnessed", MET)]


# -- 020 never holds play up ---------------------------------------------------------------------------

def test_a_damaged_knowledge_ledger_holds_no_acceptance_up_and_its_knowledge_is_written_once_repaired(cards):
    folder, chat = cards
    book = StageBook()
    with table(folder, book) as at:
        anya, mara, tamsin = cast(at, chat)
        book.put(chat, stage(chat, anya, mara, tamsin))
        turn = decided(at, chat)
        junk = folder / "campaigns" / chat.campaign_id / "events" / "e-junk.json"
        junk.parent.mkdir(parents=True, exist_ok=True)
        junk.write_text("{not json", encoding="utf-8")
        effect = accepted(at, chat, anya, mara, turn)
        assert effect.state == "accepted", effect.reason  # 021 never waits for 020
        assert unbridged(at, chat) == ["1:ledger_damaged"]  # a count and a reason: no turn, no event
        assert at.runner.recover_effects(chat, at.owner) == ()  # recovery refuses nothing over it either
        junk.unlink()  # repaired
        assert at.runner.recover_effects(chat, at.owner) == ()  # and now writes it
        assert seen(at, chat, anya) == [("witnessed", MET)] and unbridged(at, chat) == []
        assert len(stored(at, chat).knowledge.events) == 1


def test_another_chats_unwritten_knowledge_never_stalls_this_one(cards, monkeypatch):
    """A second chat of the campaign, played with no stage, whose acceptance died before its 020 append: this
    chat's next acceptance goes ahead, and both events are written in sequence order, the other chat's as the
    event alone (its stage cannot say who saw it), this one's with its witnesses."""
    folder, chat = cards
    book = StageBook()
    with table(folder, book, replies=2) as at:
        anya, mara, tamsin = cast(at, chat)
        book.put(chat, stage(chat, anya, mara, tamsin))
        chats = ChatLibrary(at.store, at.owner, clock=Clock(), new_id=Tagged("b"), owner=OWNER)
        other = chats.start_chat(Launch("character", MARA_VELL), persona=ANYA, scope=ScopeRef(chat.campaign_id),
                                 reuse_actors={("persona", ANYA.entity_key): anya,
                                               ("character", MARA_VELL.entity_key): mara}).commit().chat
        at.runner.seat(other, ANA)
        elsewhere = decided(at, other)
        no_bridge_append(monkeypatch)
        with pytest.raises(Crash):
            accepted(at, other, anya, mara, elsewhere)
        monkeypatch.undo()
        here = accepted(at, chat, anya, mara, decided(at, chat, "*I offer Mara a seat*"), "Anya offers Mara a seat.",
                        "allied_with")
        assert here.state == "accepted", here.reason
        events = stored(at, chat).knowledge.events
        assert [event.summary for event in events] == [MET, "Anya offers Mara a seat."]  # in sequence order
        assert seen(at, chat, anya) == [("witnessed", "Anya offers Mara a seat.")]
        assert unbridged(at, chat) == [] and unbridged(at, other) == []


# -- storage failures, a replaced identity, an unreadable turn ----------------------------------------------

@pytest.mark.parametrize("last", ["approve", "endorse"])
def test_a_full_disk_at_the_knowledge_append_refuses_nothing_and_is_written_later(cards, monkeypatch, last):
    folder, chat = cards
    book = StageBook()
    with table(folder, book) as at:
        anya, mara, tamsin = cast(at, chat)
        book.put(chat, stage(chat, anya, mara, tamsin))
        turn = decided(at, chat)
        effect = at.runner.propose_effect(chat, at.player(), turn, MET, [Op("begin", "has_met", anya, mara, anya,
                                                                              turn)])
        full_disk(monkeypatch)
        if last == "approve":
            at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
            done = at.runner.approve_effect(chat, at.owner, turn, 1)
        else:
            at.runner.approve_effect(chat, at.owner, turn, 1)
            done = at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
        assert done.state == "accepted"  # 021 and the accepted mark stand; the disk refused only 020
        assert unbridged(at, chat) == ["1:OSError"]  # open reads on, and counts it for the director
        assert at.runner.recover_effects(chat, at.owner) == ()
        monkeypatch.undo()  # space again
        assert unbridged(at, chat) == [] and seen(at, chat, anya) == [("witnessed", MET)]


@pytest.mark.parametrize("pairing", ["unpaired", "paired"])
def test_an_acceptance_under_a_replaced_identity_is_still_this_librarys_to_write(cards, monkeypatch, pairing):
    """A copied library given a new device ID (``identify(..., replace=True)``) keeps what landed under the old
    one: never paired, nothing here arrived by sync; paired, the replace retires the pair's records, which keep
    the old ID."""
    folder, chat = cards
    book = StageBook()
    with table(folder, book) as at:
        identify(at.store, "phone")
        if pairing == "paired":
            paired(at.store)
        anya, mara, tamsin = cast(at, chat)
        book.put(chat, stage(chat, anya, mara, tamsin))
        turn = decided(at, chat)
        no_bridge_append(monkeypatch)
        with pytest.raises(Crash):
            accepted(at, chat, anya, mara, turn)
    monkeypatch.undo()
    with restart(folder, book) as at:
        identify(at.store, "tablet", replace=True)  # retires the pair's record, naming the old ID
        assert raw_effect(at, chat, turn).accepted_on not in (None, at.runner.scenes.device())
        assert at.runner.recover_effects(chat, at.owner) == ()
        assert seen(at, chat, anya) == [("witnessed", MET)] and unbridged(at, chat) == []


def test_an_unreadable_turn_holds_every_later_bridge_until_it_is_repaired(cards, monkeypatch):
    """A turn that cannot be read could hold an earlier pending effect: writing a later bridge would leave that one
    behind for good, so nothing is written (and play goes on) until it is repaired; then both are, in order."""
    folder, chat = cards
    book = StageBook()
    with table(folder, book, replies=2) as at:
        anya, mara, tamsin = cast(at, chat)
        book.put(chat, stage(chat, anya, mara, tamsin))
        full_disk(monkeypatch)
        first = decided(at, chat)
        assert accepted(at, chat, anya, mara, first).state == "accepted"
        at.runner.pass_turn(chat, at.player())
        second = decided(at, chat, "*I offer Mara a seat*")
        assert accepted(at, chat, anya, mara, second, "Anya offers Mara a seat.", "allied_with").state == "accepted"
        monkeypatch.undo()
        path = folder / "campaigns" / chat.campaign_id / "turns" / f"{first}.json"
        kept = path.read_bytes()
        path.write_bytes(b"{not json")
        assert unbridged(at, chat) == ["1:turns_unreadable"]  # the second waits; the first cannot be listed
        assert stored(at, chat).knowledge.events == ()
        path.write_bytes(kept)  # repaired
        assert unbridged(at, chat) == []
        assert [event.summary for event in stored(at, chat).knowledge.events] == [MET, "Anya offers Mara a seat."]
