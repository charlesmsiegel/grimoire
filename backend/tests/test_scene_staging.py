"""035 x 041: with a stage, the stage (not the cast alone) decides who hears, sees and speaks in play.

Over the synthetic card library: Mara Vell's card chat, with Anya Rook as the player's persona. The stage
has two rooms, the study and the hall, joined by the study door. The app keeps each chat's latest stage in a
``StageBook`` (``play.stages``) and hands it to the runner; nothing persists a stage yet (041 milestone 2).
Every model call is a scripted fake.
"""

from __future__ import annotations

import shutil
from dataclasses import replace

import pytest

from chat_fixtures import Clock, Ids
from scene_fixtures import ANA, Table, attach_tamsin, card_library, say
from store_helpers import OWNER

import grimoire.play.runner as runner_module
from grimoire.core.authority import DIRECTOR
from grimoire.core.identity import InstanceRef
from grimoire.core import Moment
from grimoire.goals import Agenda, Approach, Goal, Present
from grimoire.ledgers.codec import ControlChange
from grimoire.partystore import PartyLibrary
from grimoire.play import SceneRunner
from grimoire.play.stages import StageBook, succession
from grimoire.scene import SceneRefused, table_lines, turn_from_payload, turn_to_payload
from grimoire.scene.codec import TURN_SCHEMA
from grimoire.staging import Absence, Area, Placement, Stage, Way

SECRET = "the vault code is 1234"


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


def cast_of(table: Table, chat):
    scene = table.runner.open(chat)
    (mara,) = [actor for actor in scene.cast if actor != scene.persona]
    return scene.persona, mara


def staged(chat, actors, *, as_of=1, door="closed", anya_in="hall", mara_in="study", absent=(), basis="established"):
    """The study and the hall, joined by the study door. ``absent`` actors are off stage, the rest placed."""
    anya, mara = actors
    rooms = {anya: anya_in, mara: mara_in}
    return Stage(chat.campaign_id, chat.chat_id, as_of, (Area("study"), Area("hall")),
                 (Way("study-door", ("study", "hall"), door),), actors,
                 tuple(Placement(who, area=rooms[who], basis=basis) for who in actors if who not in absent),
                 tuple(Absence(who) for who in absent))


def opened(folder, book, steps=()):
    table = Table(folder, list(steps), stages=book)
    return table


def party(table: Table) -> PartyLibrary:
    return PartyLibrary(table.store, table.owner, clock=Clock(), new_id=Ids(), owner=OWNER)


# -- the stage is carried through -----------------------------------------------------------------

def test_the_latest_stage_is_carried_through_the_scene(cards):
    folder, chat = cards
    book = StageBook()
    with opened(folder, book) as table:
        anya, mara = cast_of(table, chat)
        assert table.runner.open(chat).stage is None  # no stage yet: the cast alone decides, as before
        first = staged(chat, (anya, mara))
        book.put(chat, first)
        scene = table.runner.open(chat)
        assert scene.stage == first and scene.on_stage == (anya, mara) == scene.cast
        later = staged(chat, (anya, mara), as_of=3, absent=(mara,))
        book.put(chat, later)
        scene = table.runner.open(chat)
        assert scene.stage == later and scene.on_stage == (anya,) and scene.cast == (anya, mara)
        assert not [p for p in scene.problems if p[0] == "stage"]


def test_the_book_keeps_only_a_later_stage():
    from grimoire.core.identity import ChatRef, InstanceRef
    chat = ChatRef("c1", "chat-1")
    actors = (InstanceRef("c1", "character", "anya"), InstanceRef("c1", "character", "mara"))
    book = StageBook()
    book.put(chat, staged(chat, actors, as_of=2))
    with pytest.raises(ValueError, match="stale_stage"):
        book.put(chat, staged(chat, actors, as_of=1))
    with pytest.raises(ValueError, match="stage_diverged"):
        book.put(chat, staged(chat, actors, as_of=2, door="open"))
    with pytest.raises(ValueError, match="scene_mismatch"):
        book.put(ChatRef("c1", "chat-2"), staged(chat, actors, as_of=3))
    book.put(chat, staged(chat, actors, as_of=2))  # the same facts again
    book.put(chat, staged(chat, actors, as_of=2).at(5))  # Stage.at: nothing changed since
    assert book.latest(chat).as_of == 5
    assert succession(None, staged(chat, actors)) is None


# -- who hears ------------------------------------------------------------------------------------

def test_a_whisper_through_a_shut_door_is_not_delivered_in_play(cards):
    folder, chat = cards
    book = StageBook()
    with opened(folder, book, [say("Who goes there?")]) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
        whisper = f'I whisper to Mara Vell, "{SECRET}"'
        book.put(chat, staged(chat, (anya, mara)))  # Anya in the hall, Mara in the study, the door shut
        refused("addressee_absent", table.runner.post, chat, table.player(), whisper)
        book.put(chat, staged(chat, (anya, mara), as_of=2, door="open"))
        refused("addressee_absent", table.runner.post, chat, table.player(), whisper)  # a whisper stays in the room
        assert table.runner.open(chat).records == ()  # nothing was written
        book.put(chat, staged(chat, (anya, mara), as_of=3, door="open", anya_in="study"))
        turn = table.runner.post(chat, table.player(), whisper)
        assert set(turn.post.audience) == {anya, mara} and turn.post.channel == "whisper"


def test_room_speech_stops_at_a_shut_door_and_the_npc_never_hears_it(cards):
    folder, chat = cards
    book = StageBook()
    with opened(folder, book, [say("Quiet in here.")]) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
        book.put(chat, staged(chat, (anya, mara)))
        turn = table.runner.post(chat, table.player(), f'"{SECRET}"')
        assert turn.post.audience == (anya,)  # nobody in earshot: the speaker alone
        take = table.runner.generate(chat, table.player())
        (line,) = take.lines
        assert line.actor == mara and line.audience == (mara,)  # Mara's reply stays in the study too
        (prompt,) = table.harness.prompts()
        assert SECRET not in prompt


def test_through_an_open_door_speech_and_an_npc_line_carry(cards):
    folder, chat = cards
    book = StageBook()
    with opened(folder, book, [say("I hear you.")]) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
        book.put(chat, staged(chat, (anya, mara), door="open"))
        turn = table.runner.post(chat, table.player(), f'"{SECRET}"')
        assert set(turn.post.audience) == {anya, mara}
        (line,) = table.runner.generate(chat, table.player()).lines
        assert set(line.audience) == {anya, mara}
        assert SECRET in table.harness.prompts()[0]


def test_an_action_is_seen_only_by_those_who_can_see_its_actor(cards):
    folder, chat = cards
    book = StageBook()
    with opened(folder, book) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
        book.put(chat, staged(chat, (anya, mara)))
        turn = table.runner.post(chat, table.player(), "*I shake the rain from my coat*")
        assert turn.post.kind == "action" and turn.post.audience == (anya,)


def test_a_take_is_kept_only_while_its_lines_are_heard_as_written(cards):
    folder, chat = cards
    book = StageBook()
    with opened(folder, book, [say("Come in."), say("Come in, then.")]) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
        book.put(chat, staged(chat, (anya, mara), door="open"))
        table.runner.post(chat, table.player(), '"May I?"')
        written = table.runner.generate(chat, table.player())
        book.put(chat, staged(chat, (anya, mara), as_of=2))  # the door shut before the take was kept
        refused("cast_changed", table.runner.keep, chat, table.player(), written.take)
        again = table.runner.generate(chat, table.player())
        assert again.lines[0].audience == (mara,)
        assert table.runner.keep(chat, table.player(), again.take).takes[-1].state == "kept"


def test_who_hears_is_never_decided_on_a_guess(cards):
    folder, chat = cards
    book = StageBook()
    with opened(folder, book, [say("Unreachable.")]) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
        book.put(chat, staged(chat, (anya, mara), door="open", basis="assumed"))
        refused("rests_on_assumption", table.runner.post, chat, table.player(), '"Anyone?"')
        refused("rests_on_assumption", table.runner.post, chat, table.player(), "*I knock*")
        refused("rests_on_assumption", table.runner.generate, chat, table.player())
        assert table.harness.prompts() == [] and table.runner.open(chat).records == ()


# -- who speaks -----------------------------------------------------------------------------------

def test_an_absent_cast_member_is_not_a_speaker(cards):
    folder, chat = cards
    book = StageBook()
    with opened(folder, book, [say("Unreachable.")]) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
        book.put(chat, staged(chat, (anya, mara), absent=(mara,)))  # Mara is in the cast, but has left
        scene = table.runner.open(chat)
        assert mara in scene.cast and mara in scene.npcs and mara not in scene.on_stage
        refused("no_opening", table.runner.begin, chat, table.player())  # her greeting is spoken in no room
        refused("not_on_stage", table.runner.begin, chat, table.player(), opening="first")
        table.runner.post(chat, table.player(), '"Mara?"')
        refused("nothing_to_generate", table.runner.generate, chat, table.player())
        refused("not_on_stage", table.runner.generate, chat, table.player(), speakers=[mara])
        assert table.harness.prompts() == []


def test_an_absent_persona_does_not_post(cards):
    folder, chat = cards
    book = StageBook()
    with opened(folder, book) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
        book.put(chat, staged(chat, (anya, mara), absent=(anya,)))
        refused("not_on_stage", table.runner.post, chat, table.player(), '"Hello?"')
        refused("not_on_stage", table.runner.post, chat, table.player(), "*I knock*")
        assert table.runner.open(chat).records == ()


def test_the_opening_greeting_is_heard_only_in_earshot(cards):
    folder, chat = cards
    book = StageBook()
    with opened(folder, book) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
        book.put(chat, staged(chat, (anya, mara)))
        (line,) = table.runner.begin(chat, table.player()).takes[0].lines
        assert line.actor == mara and line.audience == (mara,)


# -- the stage against the cast, every call -------------------------------------------------------

def test_a_stage_that_disagrees_with_the_cast_is_refused(cards):
    folder, chat = cards
    book = StageBook()
    with opened(folder, book, [say("Unreachable.")]) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
        lone = Stage(chat.campaign_id, chat.chat_id, 1, (Area("hall"),), (), (anya,), (Placement(anya, area="hall"),))
        book.put(chat, lone)  # a stage that has lost Mara, who is still cast
        scene = table.runner.open(chat)
        assert ("stage", "cast_mismatch") in scene.problems and scene.stage is None
        refused("stage_mismatch", table.runner.post, chat, table.player(), f'I whisper to Mara Vell, "{SECRET}"')
        refused("stage_mismatch", table.runner.generate, chat, table.player())
        refused("stage_mismatch", table.runner.begin, chat, table.player())
        assert table.harness.prompts() == [] and table.runner.open(chat).records == ()


def test_the_stage_is_checked_against_the_cast_as_it_is_now(cards):
    folder, chat = cards
    book = StageBook()
    with opened(folder, book, [say("Agreed.")]) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
        book.put(chat, staged(chat, (anya, mara), door="open"))
        table.runner.post(chat, table.player(), '"Shall we?"')
        written = table.runner.generate(chat, table.player())
        library = party(table)  # Mara is boxed after the stage was made: 033's cast no longer has her
        library.join(anya, mara, at=Moment(1), provenance="director").commit()
        library.store_in_reserve(mara, at=Moment(2), provenance="director").commit()
        assert ("stage", "cast_mismatch") in table.runner.open(chat).problems
        refused("stage_mismatch", table.runner.keep, chat, table.player(), written.take)
        book.put(chat, Stage(chat.campaign_id, chat.chat_id, 2, (Area("study"), Area("hall")), (), (anya,),
                             (Placement(anya, area="hall"),)))  # recast: a new stage of the cast as it is now
        assert table.runner.open(chat).stage is not None
        refused("cast_changed", table.runner.keep, chat, table.player(), written.take)


def test_a_card_attached_mid_chat_waits_for_a_new_stage(cards):
    folder, chat = cards
    book = StageBook()
    with opened(folder, book) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
        book.put(chat, staged(chat, (anya, mara), door="open"))
        tamsin = attach_tamsin(table.store, table.owner, chat)
        table.runner.seat(chat, ANA)
        assert tamsin in table.runner.open(chat).cast
        refused("stage_mismatch", table.runner.post, chat, table.player(), '"Who is that?"')


class Source:
    """A stage source the test drives directly, wrong answers included."""

    def __init__(self, stage=None, error=None):
        self.stage, self.error = stage, error

    def latest(self, chat):
        if self.error is not None:
            raise self.error
        return self.stage


def test_a_stage_source_that_fails_or_goes_back_stops_play(cards):
    folder, chat = cards
    source = Source()
    with Table(folder, stages=source) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
        source.stage = staged(chat, (anya, mara), as_of=4, door="open")
        assert table.runner.open(chat).stage == source.stage
        source.stage = staged(chat, (anya, mara), as_of=3, door="open")  # older than the one already played
        assert ("stage", "stale_stage") in table.runner.open(chat).problems
        refused("stale_stage", table.runner.post, chat, table.player(), '"Hello?"')
        source.stage = staged(chat, (anya, mara), as_of=4)  # another staging of the same moment
        refused("stage_diverged", table.runner.post, chat, table.player(), '"Hello?"')
        source.stage = "not a stage"
        refused("stage_unreadable", table.runner.post, chat, table.player(), '"Hello?"')
        source.stage, source.error = None, OSError("gone")
        refused("stage_unreadable", table.runner.post, chat, table.player(), '"Hello?"')
        source.error = None
        refused("stage_lost", table.runner.post, chat, table.player(), '"Hello?"')  # never the cast alone again
        source.stage = staged(chat, (anya, mara), as_of=5, door="open")
        source.stage = Stage(chat.campaign_id, "another-scene", 5, source.stage.areas, source.stage.ways,
                             source.stage.actors, source.stage.placements)
        refused("stage_mismatch", table.runner.post, chat, table.player(), '"Hello?"')
        assert ("stage", "scene_mismatch") in table.runner.open(chat).problems
        source.stage = staged(chat, (anya, mara), as_of=5, door="open")
        assert set(table.runner.post(chat, table.player(), '"Hello?"').post.audience) == {anya, mara}


def test_a_stage_source_must_have_latest(cards):
    folder, _ = cards
    with Table(folder) as table:
        refused("bad_stages", SceneRunner, table.store, table.authority, table.owner, owner=OWNER, stages=object())


# -- who is present to an NPC: its pack, 039's choice, the director ------------------------------

GREET, WAIT = "Ask the pilot whether the cargo is aboard", "Count the crates again"


def greeter(mara, anya) -> Agenda:
    """Mara greets the pilot when the pilot is present, and counts crates otherwise."""
    return Agenda(mara.campaign_id, mara, (Goal("cargo", "Get the cargo aboard", 50, (
        Approach("greet", GREET, needs=(Present(anya),)), Approach("wait", WAIT))),))


def gone(chat, anya, mara, *, as_of=1):
    """The study, whose front door leads out; Anya has left through it and it is shut behind her."""
    return Stage(chat.campaign_id, chat.chat_id, as_of, (Area("study"),),
                 (Way("front-door", ("study", None), "closed"),), (anya, mara),
                 (Placement(mara, area="study"),), (Absence(anya, via="front-door"),))


def back(chat, anya, mara, *, as_of):
    return Stage(chat.campaign_id, chat.chat_id, as_of, (Area("study"),),
                 (Way("front-door", ("study", None), "closed"),), (anya, mara),
                 (Placement(anya, area="study"), Placement(mara, area="study")), ())


def spy(monkeypatch, name):
    """Record every ``present`` the runner hands ``name`` (actor_context, director_brief, ...)."""
    seen = []
    original = getattr(runner_module, name)

    def wrapped(*args, **kwargs):
        seen.append(tuple(kwargs["present"]))
        return original(*args, **kwargs)
    monkeypatch.setattr(runner_module, name, wrapped)
    return seen


def test_an_absent_member_is_not_present_in_an_npcs_pack_or_brief(cards, monkeypatch):
    folder, chat = cards
    packs = spy(monkeypatch, "actor_context")
    book = StageBook()
    with opened(folder, book, [say("Nobody.")]) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
    with Table(folder, [say("Nobody."), say("Still nobody.")], stages=book, agendas=[greeter(mara, anya)]) as table:
        book.put(chat, gone(chat, anya, mara))
        take = table.runner.generate(chat, table.player())
        assert packs == [(mara,)]  # Anya has left: she ranks none of Mara's memory as present
        (intention,) = table.runner.open(chat).pending.intentions
        assert intention.scene == frozenset({mara}) and anya not in intention.present + intention.unrecognised
        assert take.on_stage == (mara,) and take.cast == (anya, mara)
        book.put(chat, staged(chat, (anya, mara), as_of=2))  # back on stage, but behind the shut study door
        table.runner.generate(chat, table.player())
        assert packs[-1] == (mara,)  # Mara cannot see her: not in her pack's present


def test_the_director_brief_has_only_the_actors_on_stage(built, tmp_path, monkeypatch):
    original, _, storm = built
    folder = tmp_path / "storm"
    shutil.copytree(original, folder)
    briefs = spy(monkeypatch, "director_brief")
    book = StageBook()
    with Table(folder, [say("The storm breaks.")], stages=book) as table:
        table.runner.seat(storm, ANA)
        scene = table.runner.open(storm)
        (anya,) = scene.cast
        book.put(storm, Stage(storm.campaign_id, storm.chat_id, 1, (Area("quay"),), (), (anya,),
                              (Placement(anya, area="quay"),)))
        table.runner.generate(storm, table.player(), director=True)
        assert briefs == [(anya.instance_id,)]


def test_an_agendas_present_condition_is_unmet_while_its_actor_is_gone(cards):
    folder, chat = cards
    book = StageBook()
    with opened(folder, book) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
    with Table(folder, [say("One crate, two crates."), say("Pilot! Is it aboard?")], stages=book,
               agendas=[greeter(mara, anya)]) as table:
        book.put(chat, gone(chat, anya, mara))  # Anya left through the front door, shut behind her
        table.runner.generate(chat, table.player())
        (intention,) = table.runner.open(chat).pending.intentions
        assert intention.approach_id == "wait"
        weighed = [(option.approach_id, option.outcome) for option in intention.considered]
        assert weighed[:2] == [("greet", "unmet"), ("wait", "chosen")]
        book.put(chat, back(chat, anya, mara, as_of=2))  # she comes back in
        table.runner.generate(chat, table.player())
        assert [i.approach_id for i in table.runner.open(chat).pending.intentions] == ["wait", "greet"]


def test_an_intention_recorded_with_a_stage_round_trips(cards):
    folder, chat = cards
    book = StageBook()
    with opened(folder, book) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
    with Table(folder, [say("One crate, two crates.")], stages=book, agendas=[greeter(mara, anya)]) as table:
        book.put(chat, gone(chat, anya, mara))
        table.runner.generate(chat, table.player())
        record = table.runner.open(chat).pending
    payload = turn_to_payload(record)
    assert payload["takes"][0]["on_stage"] == [str(mara)]
    assert payload["intentions"][0]["perceived"] == [str(mara)]
    assert turn_from_payload(record.ref, TURN_SCHEMA, payload) == record
    blind = turn_to_payload(record)
    blind["intentions"][0]["perceived"] = []  # claims Mara chose without seeing herself
    with pytest.raises(SceneRefused):
        turn_from_payload(record.ref, TURN_SCHEMA, blind)
    unstaged = turn_to_payload(record)
    del unstaged["intentions"][0]["perceived"]  # an intention chosen with no stage reads as such
    assert turn_from_payload(record.ref, TURN_SCHEMA, unstaged).intentions[0].perceived is None
    with Table(folder) as table:  # a restart, with no stage source at all: the record still reads as written
        reread = table.runner.open(chat).pending
        assert reread == record and reread.takes[0].present == (mara,)
    old = turn_to_payload(record)
    del old["takes"][0]["on_stage"]  # a take written before stages: the whole cast was present for it
    with pytest.raises(SceneRefused) as error:  # so a choice made for the stage alone no longer fits it
        turn_from_payload(record.ref, TURN_SCHEMA, old)
    assert error.value.reason == "invalid_field"
    tampered = turn_to_payload(record)
    tampered["takes"][0]["on_stage"] = [str(anya), str(mara)]  # claims Anya was there
    with pytest.raises(SceneRefused):
        turn_from_payload(record.ref, TURN_SCHEMA, tampered)
    outsider = turn_to_payload(record)
    outsider["takes"][0]["on_stage"] = [str(InstanceRef(chat.campaign_id, "character", "nobody"))]
    with pytest.raises(SceneRefused):
        turn_from_payload(record.ref, TURN_SCHEMA, outsider)


# -- within a take: an earlier line reaches only those who heard it ------------------------------

def rooms(chat, placed, *, as_of=1, door="closed"):
    """The study and the hall joined by the study door; ``placed`` maps each actor to its room."""
    return Stage(chat.campaign_id, chat.chat_id, as_of, (Area("study"), Area("hall")),
                 (Way("study-door", ("study", "hall"), door),), tuple(placed),
                 tuple(Placement(who, area=room) for who, room in placed.items()))


def test_an_earlier_line_of_a_take_never_reaches_a_later_speaker_behind_a_shut_door(cards):
    folder, chat = cards
    book = StageBook()
    with Table(folder) as table:
        tamsin = attach_tamsin(table.store, table.owner, chat)
        table.runner.seat(chat, ANA)
        scene = table.runner.open(chat)
        anya = scene.persona
        (mara,) = [actor for actor in scene.cast if actor not in (anya, tamsin)]
    with Table(folder, [say(f"Psst, {SECRET}."), say("Anyone there?")], stages=book) as table:
        book.put(chat, rooms(chat, {anya: "study", mara: "study", tamsin: "hall"}))
        take = table.runner.generate(chat, table.player(), speakers=[mara, tamsin])
        first, second = take.lines
        assert first.actor == mara and set(first.audience) == {anya, mara}
        assert second.actor == tamsin and second.audience == (tamsin,)
        assert SECRET not in table.harness.prompts()[1]  # Tamsin, alone in the hall, is never given Mara's line
        assert table.runner.keep(chat, table.player(), take.take).takes[-1].state == "kept"
    with Table(folder, [say(f"Psst, {SECRET}."), say("Is that you, Mara?")], stages=book) as table:
        book.put(chat, rooms(chat, {anya: "study", mara: "study", tamsin: "study"}, as_of=2))
        table.runner.generate(chat, table.player(), speakers=[mara, tamsin])
        assert SECRET in table.harness.prompts()[1]  # in the same room she does hear it


def test_the_director_follows_only_lines_a_player_character_heard(built, tmp_path):
    original, _, storm = built
    folder = tmp_path / "storm"
    shutil.copytree(original, folder)
    book = StageBook()
    with Table(folder) as table:
        tamsin = attach_tamsin(table.store, table.owner, storm)
        table.runner.seat(storm, ANA)
        (anya,) = [actor for actor in table.runner.open(storm).cast if actor != tamsin]
    with Table(folder, [say(f"Psst, {SECRET}."), say("The house is quiet.")], stages=book) as table:
        book.put(storm, rooms(storm, {anya: "hall", tamsin: "study"}))  # the only PC is in the hall
        take = table.runner.generate(storm, table.player(), speakers=[tamsin], director=True)
        assert take.lines[0].actor == tamsin and anya not in take.lines[0].audience
        assert SECRET not in table.harness.prompts()[-1]  # the director sees only what the table saw
    with Table(folder, [say(f"Psst, {SECRET}."), say("The house is quiet.")], stages=book) as table:
        book.put(storm, rooms(storm, {anya: "hall", tamsin: "study"}, as_of=2, door="open"))
        table.runner.generate(storm, table.player(), speakers=[tamsin], director=True)
        assert SECRET in table.harness.prompts()[-1]  # the door open, Anya heard it, so the director follows it


# -- 039's conditions see only whom the NPC perceives ---------------------------------------------

def test_an_actor_the_npc_cannot_perceive_is_not_present_to_its_choice(cards, monkeypatch):
    folder, chat = cards
    packs = spy(monkeypatch, "actor_context")
    book = StageBook()
    with opened(folder, book) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
    wardrobe = InstanceRef(chat.campaign_id, "item", "wardrobe")
    hidden = Stage(chat.campaign_id, chat.chat_id, 1, (Area("study"),), (), (anya, mara),
                   (Placement(wardrobe, area="study"), Placement(anya, holder=wardrobe),
                    Placement(mara, area="study")))
    for stage in (hidden, staged(chat, (anya, mara), as_of=2)):  # in the wardrobe; then behind the shut door
        with Table(folder, [say("One crate, two crates.")], stages=book, agendas=[greeter(mara, anya)]) as table:
            book.put(chat, stage)
            table.runner.generate(chat, table.player())
            pending = table.runner.open(chat).pending
            intention = pending.intentions[-1]
            assert packs[-1] == (mara,)
            assert intention.approach_id == "wait" and GREET not in table.harness.prompts()[0]
            assert intention.present == (mara,) and intention.unrecognised == (anya,)
            # the intention's scene is still the take's on-stage cast, as 027's plan and 047's referee hold it
            assert set(pending.takes[-1].present) == {anya, mara} == set(intention.present + intention.unrecognised)


# -- a chat played on a stage stays played on one -------------------------------------------------

def test_a_restart_does_not_fall_back_to_the_cast_alone(cards):
    folder, chat = cards
    book = StageBook()
    with Table(folder, stages=book) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
        book.put(chat, staged(chat, (anya, mara), door="open", anya_in="study"))
        table.runner.begin(chat, table.player())
        table.runner.keep(chat, table.player(), 1)
    whisper = f'I whisper to Mara Vell, "{SECRET}"'
    for source in (StageBook(), None):  # a restarted source that lost the stage, or no source at all
        with Table(folder, stages=source) as table:
            scene = table.runner.open(chat)
            assert scene.records[0].takes[0].on_stage is not None
            assert ("stage", "stage_lost") in scene.problems
            refused("stage_lost", table.runner.post, chat, table.player(), whisper)
            refused("stage_lost", table.runner.generate, chat, table.player())


def test_a_take_is_kept_only_while_its_npc_sees_whom_it_chose_by(cards):
    folder, chat = cards
    book = StageBook()
    with opened(folder, book) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
    wardrobe = InstanceRef(chat.campaign_id, "item", "wardrobe")

    def study(as_of, hidden):
        return Stage(chat.campaign_id, chat.chat_id, as_of, (Area("study"),), (), (anya, mara),
                     (Placement(wardrobe, area="study"),
                      Placement(anya, holder=wardrobe) if hidden else Placement(anya, area="study"),
                      Placement(mara, area="study")))
    with Table(folder, [say("One crate, two crates."), say("Pilot! Is it aboard?"), say("One crate, two crates.")],
               stages=book, agendas=[greeter(mara, anya)]) as table:
        book.put(chat, study(1, hidden=True))
        take = table.runner.generate(chat, table.player())
        chosen = table.runner.open(chat).pending.intentions[-1]
        assert (chosen.approach_id, chosen.unrecognised, chosen.perceived) == ("wait", (anya,), (mara,))
        book.put(chat, study(2, hidden=False))  # she steps out: same on-stage cast, same audience, now seen
        refused("cast_changed", table.runner.keep, chat, table.player(), take.take)
        take = table.runner.generate(chat, table.player())
        assert table.runner.open(chat).pending.intentions[-1].approach_id == "greet"
        book.put(chat, study(3, hidden=True))  # and back in: the greeting was chosen for someone now unseen
        refused("cast_changed", table.runner.keep, chat, table.player(), take.take)
        take = table.runner.generate(chat, table.player())
        book.put(chat, study(4, hidden=True))  # nothing seen changed: kept
        assert table.runner.keep(chat, table.player(), take.take).takes[-1].state == "kept"


def test_the_director_never_sees_a_kept_line_no_player_character_heard(built, tmp_path):
    original, _, storm = built
    folder = tmp_path / "storm"
    shutil.copytree(original, folder)
    book = StageBook()
    with Table(folder) as table:
        tamsin = attach_tamsin(table.store, table.owner, storm)
        table.runner.seat(storm, ANA)
        (anya,) = [actor for actor in table.runner.open(storm).cast if actor != tamsin]
    steps = [say(f"Psst, {SECRET}."), say("The house is quiet."), say("Night falls.")]
    with Table(folder, steps, stages=book) as table:
        book.put(storm, rooms(storm, {anya: "hall", tamsin: "study"}))
        take = table.runner.generate(storm, table.player(), speakers=[tamsin], director=True)
        table.runner.keep(storm, table.player(), take.take)
        table.runner.generate(storm, table.player(), speakers=[], director=True)
        prompt = table.harness.prompts()[-1]
        assert SECRET not in prompt and "The house is quiet." in prompt  # the narration the table heard stays


def test_an_unstaged_chats_director_brief_is_unchanged(built, tmp_path, monkeypatch):
    original, _, storm = built
    folder = tmp_path / "storm"
    shutil.copytree(original, folder)
    with Table(folder) as table:
        tamsin = attach_tamsin(table.store, table.owner, storm)
        table.runner.seat(storm, ANA)
    with Table(folder, [say(f"Psst, {SECRET}."), say("The house is quiet.")]) as table:
        take = table.runner.generate(storm, table.player(), speakers=[tamsin], director=True)
        table.runner.keep(storm, table.player(), take.take)
    twin = tmp_path / "twin"
    shutil.copytree(folder, twin)
    prompts = []
    for where in (folder, twin):
        with Table(where, [say("Night falls.")]) as table:
            table.runner.generate(storm, table.player(), speakers=[], director=True)
            prompts.append(table.harness.prompts()[-1])
        if where == folder:  # the twin's brief comes from table_lines as it was, with no players filter
            unfiltered = runner_module.table_lines
            monkeypatch.setattr(runner_module, "table_lines",
                                lambda *args, as_heard=False, **kwargs: unfiltered(*args, **kwargs))
    assert prompts[0] == prompts[1] and SECRET in prompts[0]


def handed(table: Table, chat, actor, controller) -> None:
    """Who plays ``actor`` from now on (033's sovereignty ledger)."""
    table.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, actor.instance_id, controller),
                                    table.owner)


def test_a_line_no_player_character_heard_stays_unseen_when_its_listener_becomes_one(built, tmp_path):
    original, _, storm = built
    folder = tmp_path / "storm"
    shutil.copytree(original, folder)
    book = StageBook()
    with Table(folder) as table:
        tamsin = attach_tamsin(table.store, table.owner, storm)
        table.runner.seat(storm, ANA)
        (anya,) = [actor for actor in table.runner.open(storm).cast if actor != tamsin]
    with Table(folder, [say(f"Psst, {SECRET}."), say("The house is quiet."), say("Night falls.")],
               stages=book) as table:
        book.put(storm, rooms(storm, {anya: "hall", tamsin: "study"}))  # Tamsin, an NPC, alone behind the door
        take = table.runner.generate(storm, table.player(), speakers=[tamsin], director=True)
        record = table.runner.keep(storm, table.player(), take.take)
        assert record.takes[-1].players == (anya,)
        claimed = turn_to_payload(record)
        claimed["takes"][-1]["players"] = [str(anya), str(tamsin)]  # claims Tamsin was a player character then
        with pytest.raises(SceneRefused):  # but the narration was heard by Anya alone
            turn_from_payload(record.ref, TURN_SCHEMA, claimed)
        handed(table, storm, tamsin, "human:bob")  # a second player takes Tamsin over afterwards
        book.put(storm, rooms(storm, {anya: "hall", tamsin: "study"}, as_of=2))
        assert set(table.runner.open(storm).players) == {anya, tamsin}
        table.runner.generate(storm, table.player(), speakers=[], director=True)
        assert SECRET not in table.harness.prompts()[-1]  # no player character heard it as it was spoken


def test_a_line_a_player_character_heard_stays_seen_when_that_character_goes_to_the_director(built, tmp_path,
                                                                                               monkeypatch):
    monkeypatch.setattr(runner_module, "_scenario_text", lambda context: "A quiet house.")
    original, chat, _ = built
    folder = tmp_path / "cards"
    shutil.copytree(original, folder)
    book = StageBook()
    with Table(folder) as table:
        tamsin = attach_tamsin(table.store, table.owner, chat)
        table.runner.seat(chat, ANA)
        handed(table, chat, tamsin, "human:bob")  # Bob plays Tamsin
        scene = table.runner.open(chat)
        anya = scene.persona
        (mara,) = [actor for actor in scene.cast if actor not in (anya, tamsin)]
    with Table(folder, [say(f"Psst, {SECRET}."), say("Night falls."), say("Night falls.")], stages=book) as table:
        book.put(chat, rooms(chat, {anya: "hall", mara: "study", tamsin: "study"}))
        take = table.runner.generate(chat, table.player(), speakers=[mara])
        assert tamsin in take.lines[0].audience and anya not in take.lines[0].audience
        table.runner.keep(chat, table.player(), take.take)
        table.runner.generate(chat, table.player(), speakers=[], director=True)
        assert SECRET in table.harness.prompts()[-1]  # Bob's Tamsin heard it, so the table did
        handed(table, chat, tamsin, DIRECTOR)  # Bob hands Tamsin back to the director
        book.put(chat, rooms(chat, {anya: "hall", mara: "study", tamsin: "study"}, as_of=2))
        table.runner.generate(chat, table.player(), speakers=[], director=True)
        assert SECRET in table.harness.prompts()[-1]  # still: it was heard by a player character in its turn


def test_a_takes_player_characters_are_checked_and_a_staged_take_without_them_is_unseen(cards):
    folder, chat = cards
    book = StageBook()
    with Table(folder, stages=book) as table:
        table.runner.seat(chat, ANA)
        anya, mara = cast_of(table, chat)
        book.put(chat, staged(chat, (anya, mara), door="open", anya_in="study"))
        record = table.runner.begin(chat, table.player())
    payload = turn_to_payload(record)
    assert payload["takes"][0]["players"] == [str(anya)]
    assert turn_from_payload(record.ref, TURN_SCHEMA, payload) == record
    outsider = turn_to_payload(record)
    outsider["takes"][0]["players"] = [str(InstanceRef(chat.campaign_id, "character", "nobody"))]
    with pytest.raises(SceneRefused):
        turn_from_payload(record.ref, TURN_SCHEMA, outsider)
    unstaged = turn_to_payload(record)
    del unstaged["takes"][0]["on_stage"]  # players only beside an on-stage cast
    with pytest.raises(SceneRefused):
        turn_from_payload(record.ref, TURN_SCHEMA, unstaged)
    earlier = turn_from_payload(record.ref, TURN_SCHEMA, {**payload, "takes": [
        {key: value for key, value in payload["takes"][0].items() if key != "players"}]})
    kept = replace(earlier, takes=(replace(earlier.takes[0], state="kept"),))
    assert table_lines([kept], {}, as_heard=True) == ()  # whose players are unknown: left out, fail closed
    assert len(table_lines([kept], {})) == 1
