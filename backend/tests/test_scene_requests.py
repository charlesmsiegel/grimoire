"""035 milestone 2, 016's Wave 5 obligation: typed pause, skip and redirect through the intent router, an
authenticated audience, and the guarantee that a skip never erases or replaces an accepted effect or event.

Over the synthetic card library (``scene_fixtures``): Mara Vell's card chat with Anya Rook as the player's
persona (``human:ana`` plays her). Every model call is a scripted fake.
"""

from __future__ import annotations

import json
import shutil

import pytest

from generation_fixtures import say
from scene_fixtures import ANA, BOB, LABEL_POLICY, Table, attach_tamsin, card_library
from store_helpers import OWNER

from grimoire.core import DIRECTOR
from grimoire.effects import Op
from grimoire.ledgers.codec import ControlChange
from grimoire.play import runner as running
from grimoire.play.stages import StageBook
from grimoire.scene import SceneRefused, segments
from grimoire.staging import Area, Placement, Stage, Way

UNOPPOSED = json.dumps({"factors": [{"fact": "f1", "bearing": "favours"}], "stakes": []})
NOW = 2**62
MET = "Anya and Mara have met."


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


def files(folder) -> dict[str, bytes]:
    return {str(path.relative_to(folder)): path.read_bytes() for path in sorted(folder.rglob("*.json"))}


def played(table: Table, chat):
    """Anya asks; Mara answers once (a proposed take). Returns that take."""
    table.runner.seat(chat, ANA)
    table.runner.post(chat, table.player(), '"Well?"')
    return table.runner.generate(chat, table.player())


# -- typed requests: the same path as the click -------------------------------------------------------------

def test_a_typed_pause_and_resume_run_as_the_requests_do(cards):
    folder, chat = cards
    with Table(folder) as table:
        table.runner.seat(chat, ANA)
        refused("nothing_to_pause", table.runner.post, chat, table.player(), "Grimoire, pause")
        table.runner.post(chat, table.player(), '"Wait."')
        paused = table.runner.post(chat, table.player(), "pause")
        assert paused.paused and paused.paused_by == ANA and paused.post.text == "Wait."  # no new turn
        assert len(table.runner.open(chat).records) == 1
        refused("already_paused", table.runner.post, chat, table.player(), "Grimoire, pause.")
        refused("paused", table.runner.post, chat, table.player(), '"Onward."')  # the fiction still waits
        resumed = table.runner.post(chat, table.player(), "resume")
        assert not resumed.paused and not table.runner.open(chat).paused
        refused("not_paused", table.runner.post, chat, table.player(), "unpause")


def test_a_typed_skip_withdraws_the_lines_on_show_and_nothing_else(cards):
    folder, chat = cards
    with Table(folder, [say("First answer."), say("Second answer.")]) as table:
        first = played(table, chat)
        second = table.runner.generate(chat, table.player())  # a reroll: both takes are proposed
        turn = table.runner.post(chat, table.player(), "Grimoire, skip that")
        by_take = {take.take: take for take in turn.takes}
        assert by_take[second.take].state == "withdrawn" and by_take[second.take].lines[0].by == ANA
        assert by_take[first.take].state == "proposed"  # only the newest take was on show
        kept = table.runner.keep(chat, table.player(), first.take)
        skipped = table.runner.post(chat, table.player(), "skip")  # the closed turn: its kept lines
        assert skipped.kept.take == first.take and not skipped.kept.shown()
        assert skipped.turn_id == kept.turn_id
        refused("no_targets", table.runner.post, chat, table.player(), "skip")  # nothing shown is left


def test_a_typed_skip_never_reaches_past_the_last_turn(cards):
    folder, chat = cards
    with Table(folder, [say("Said before.")]) as table:
        take = played(table, chat)
        table.runner.keep(chat, table.player(), take.take)
        table.runner.post(chat, table.player(), '"And now?"')  # a new turn, with nothing generated yet
        before = files(folder)
        refused("no_targets", table.runner.post, chat, table.player(), "skip")
        assert files(folder) == before
        assert table.runner.open(chat).records[0].kept.shown()  # the earlier line is untouched


def test_a_retried_typed_skip_never_walks_back_to_an_older_take(cards):
    folder, chat = cards
    with Table(folder, [say("First answer."), say("Second answer.")]) as table:
        first = played(table, chat)
        second = table.runner.generate(chat, table.player())
        table.runner.post(chat, table.player(), "skip")  # take 2, the newest
        before = files(folder)
        refused("no_targets", table.runner.post, chat, table.player(), "skip")  # a double Enter: nothing more
        refused("no_targets", table.runner.post, chat, table.player(), "redirect: kinder")
        assert files(folder) == before
        states = {take.take: take.state for take in table.runner.open(chat).last.takes}
        assert states == {first.take: "proposed", second.take: "withdrawn"}
        table.runner.keep(chat, table.player(), first.take)
        table.runner.post(chat, table.player(), "skip")  # the closed turn: its kept take, once
        before = files(folder)
        refused("no_targets", table.runner.post, chat, table.player(), "skip")
        assert files(folder) == before


def test_a_comma_after_redirect_is_never_run_as_a_command(cards):
    folder, chat = cards
    with Table(folder, [say("The harbour bell rings.")]) as table:
        take = played(table, chat)
        before = files(folder)
        refused("uncertain", table.runner.post, chat, table.player(), "Redirect, you fool!")
        assert files(folder) == before and table.runner.open(chat).last.takes[-1].state == "proposed"
        turn = table.runner.post(chat, table.player(), "Redirect: you fool")
        assert [t.direction for t in turn.takes if t.take == take.take] == ["you fool"]


def rooms(chat, placed):
    """A study and a hall behind a shut door."""
    return Stage(chat.campaign_id, chat.chat_id, 1, (Area("study"), Area("hall")),
                 (Way("study-door", ("study", "hall"), "closed"),), tuple(placed),
                 tuple(Placement(who, area=room) for who, room in placed.items()))


def test_a_player_withdraws_only_lines_their_own_character_heard(cards):
    folder, chat = cards
    book = StageBook()
    with Table(folder) as table:
        tamsin = attach_tamsin(table.store, table.owner, chat)
        table.runner.seat(chat, ANA)
        scene = table.runner.open(chat)
        anya = scene.persona
        (mara,) = [actor for actor in scene.cast if actor not in (anya, tamsin)]
        table.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, tamsin.instance_id, BOB),
                                        table.owner)  # Bob plays Tamsin
    with Table(folder, [say("Psst, the code is under the third stone.")], stages=book) as table:
        book.put(chat, rooms(chat, {anya: "study", mara: "study", tamsin: "hall"}))
        take = table.runner.generate(chat, table.player(), speakers=[mara])
        (line,) = take.lines
        assert tamsin not in line.audience and anya in line.audience  # Tamsin, in the hall, heard nothing
        before = files(folder)
        refused("no_targets", table.runner.post, chat, table.player(BOB), "skip")
        refused("no_targets", table.runner.post, chat, table.player(BOB), "redirect: louder")
        for kind in ("skip", "redirect", "rephrase"):  # the click follows the same rule
            refused("not_heard", table.runner.request, chat, table.player(BOB), kind, [line.line_id])
        assert files(folder) == before
        turn = table.runner.post(chat, table.player(), "skip")  # Anya heard it: Ana may
        (changed,) = [each for each in turn.takes if each.take == take.take]
        assert changed.lines[0].state == "withdrawn" and changed.lines[0].by == ANA


def cast_of(table: Table, chat):
    """Anya (Ana's), Mara and Tamsin, both seated to the director."""
    tamsin = attach_tamsin(table.store, table.owner, chat)
    table.runner.seat(chat, ANA)
    scene = table.runner.open(chat)
    anya = scene.persona
    (mara,) = [actor for actor in scene.cast if actor not in (anya, tamsin)]
    return anya, mara, tamsin


def hand(table: Table, chat, actor, player) -> None:
    table.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, actor.instance_id, player),
                                    table.owner)


def test_strike_withdraws_only_a_line_the_striker_heard(cards):
    folder, chat = cards
    book = StageBook()
    with Table(folder) as table:
        anya, mara, tamsin = cast_of(table, chat)
        hand(table, chat, tamsin, BOB)
    with Table(folder, [say("Psst, the code is under the third stone.")], stages=book) as table:
        book.put(chat, rooms(chat, {anya: "study", mara: "study", tamsin: "hall"}))
        take = table.runner.generate(chat, table.player(), speakers=[mara])
        (line,) = take.lines
        assert tamsin not in line.audience
        table.runner.keep(chat, table.player(), take.take)
        before = files(folder)
        refused("not_heard", table.runner.strike, chat, table.player(BOB), line.line_id)  # behind the shut door
        assert files(folder) == before
        struck = table.runner.strike(chat, table.owner, line.line_id)  # the owner still may
        assert struck.kept.lines[0].state == "struck" and struck.kept.lines[0].by == OWNER


def test_an_npc_handed_to_a_player_later_brings_none_of_what_it_heard(cards):
    folder, chat = cards
    book = StageBook()
    with Table(folder) as table:
        anya, mara, tamsin = cast_of(table, chat)
    with Table(folder, [say("Between the two of us: the duke is a fraud.")], stages=book) as table:
        book.put(chat, rooms(chat, {anya: "study", mara: "hall", tamsin: "hall"}))
        take = table.runner.generate(chat, table.owner, speakers=[mara])
        (line,) = take.lines
        assert tamsin in line.audience and anya not in line.audience and take.players == (anya,)
        hand(table, chat, tamsin, BOB)  # later, Bob is given Tamsin: as of the take, no player character heard it
        before = files(folder)
        refused("no_targets", table.runner.post, chat, table.player(BOB), "skip")
        refused("not_heard", table.runner.request, chat, table.player(BOB), "skip", [line.line_id])
        assert files(folder) == before


def test_a_player_whose_character_went_to_the_director_no_longer_withdraws_what_it_heard(cards):
    # a known limit (SCENES): who heard a line is by control now, so Ana's Anya's line is not Ana's to take away
    # once the director holds Anya; the owner can still withdraw it
    folder, chat = cards
    book = StageBook()
    with Table(folder) as table:
        anya, mara, tamsin = cast_of(table, chat)
    with Table(folder, [say("Something only Anya heard.")], stages=book) as table:
        book.put(chat, rooms(chat, {anya: "study", mara: "study", tamsin: "hall"}))
        take = table.runner.generate(chat, table.player(), speakers=[mara])
        (line,) = take.lines
        assert anya in line.audience and take.players == (anya,)
        hand(table, chat, tamsin, ANA)
        hand(table, chat, anya, DIRECTOR)  # Ana now plays Tamsin; the director holds Anya
        refused("not_heard", table.runner.request, chat, table.player(), "skip", [line.line_id])
        turn = table.runner.request(chat, table.owner, "skip", [line.line_id])
        assert turn.takes[-1].lines[0].by == OWNER


def test_the_owner_may_withdraw_any_shown_line(cards):
    folder, chat = cards
    book = StageBook()
    with Table(folder) as table:
        table.runner.seat(chat, ANA)
        scene = table.runner.open(chat)
        anya = scene.persona
        (mara,) = [actor for actor in scene.cast if actor != anya]
    with Table(folder, [say("Only for the walls."), say("Again, to the walls.")], stages=book) as table:
        book.put(chat, rooms(chat, {anya: "study", mara: "hall"}))
        first = table.runner.generate(chat, table.owner, speakers=[mara])
        assert anya not in first.lines[0].audience
        refused("not_heard", table.runner.request, chat, table.player(), "skip", [first.lines[0].line_id])
        refused("no_targets", table.runner.post, chat, table.player(), "skip")
        assert table.runner.request(chat, table.owner, "skip", [first.lines[0].line_id]).takes[0].state == "withdrawn"
        table.runner.generate(chat, table.owner, speakers=[mara])
        assert table.runner.post(chat, table.owner, "skip").takes[-1].lines[0].by == OWNER


def test_a_typed_redirect_records_the_players_direction_on_its_take(cards):
    folder, chat = cards
    with Table(folder, [say("Too cruel.")]) as table:
        take = played(table, chat)
        turn = table.runner.post(chat, table.player(), "Redirect: kinder, please.")
        (changed,) = [each for each in turn.takes if each.take == take.take]
        assert (changed.state, changed.direction) == ("withdrawn", "kinder, please")
        assert changed.lines[0].state == "withdrawn"


def test_typed_requests_need_a_player_at_the_table_or_the_owner(cards):
    folder, chat = cards
    with Table(folder, [say("Hush.")]) as table:
        played(table, chat)
        before = files(folder)
        refused("not_at_table", table.runner.post, chat, table.player(BOB), "skip")
        refused("not_authenticated", table.runner.post, chat, table.authority.handle_for(ANA, trusted=False), "skip")
        refused("not_authenticated", table.runner.post, chat, table.harness.handle, "Grimoire, pause")
        assert files(folder) == before
        turn = table.runner.post(chat, table.owner, "skip")  # the owner may, as with the click
        assert turn.takes[-1].lines[0].by == OWNER


def test_only_the_table_requests_run_from_text_and_guesses_never(cards):
    folder, chat = cards
    with Table(folder, [say("Stay.")]) as table:
        take = played(table, chat)
        before = files(folder)
        for text in ("undo", "Open the inventory", "reroll"):
            refused("not_in_this_slice", table.runner.post, chat, table.player(), text)
        refused("uncertain", table.runner.post, chat, table.player(), "Skip the formalities, Mara")
        refused("uncertain", table.runner.post, chat, table.player(), "could you skip that")
        assert files(folder) == before
        # quoted, or in speech mode, the words are Anya's own: a post, refused while her turn is open
        refused("turn_open", table.runner.post, chat, table.player(), '"Skip it."')
        refused("turn_open", table.runner.post, chat, table.player(), "skip", mode="speech")
        assert table.runner.open(chat).last.takes[-1].take == take.take
        assert table.runner.open(chat).last.takes[-1].state == "proposed"


def test_a_typed_skip_over_a_damaged_turn_file_writes_nothing(cards):
    folder, chat = cards
    with Table(folder, [say("Lost words.")]) as table:
        take = played(table, chat)
        (path,) = [path for path in folder.rglob("*.json") if path.parent.name == "turns"]
        path.write_bytes(b"{ not json")
        before = files(folder)
        refused("turns_unreadable", table.runner.post, chat, table.player(), "skip")
        refused("turns_unreadable", table.runner.request, chat, table.player(), "skip", [take.lines[0].line_id])
        assert files(folder) == before


def test_a_typed_skip_goes_on_while_play_is_paused_as_marks_do(cards):
    folder, chat = cards
    with Table(folder, [say("Paused words.")]) as table:
        played(table, chat)
        table.runner.post(chat, table.player(), "pause")
        turn = table.runner.post(chat, table.player(), "skip")
        assert turn.paused and turn.takes[-1].state == "withdrawn"


# -- the authenticated audience ---------------------------------------------------------------------------

def test_the_request_audience_is_who_the_ledger_seats_never_the_caller_alone(cards, monkeypatch):
    folder, chat = cards
    seen = []
    decide = running.decide_request

    def spy(request, context, found):
        seen.append(context.audience)
        return decide(request, context, found)

    monkeypatch.setattr(running, "decide_request", spy)
    with Table(folder, [say("One."), say("Two.")]) as table:
        take = played(table, chat)
        table.runner.request(chat, table.owner, "skip", [take.lines[0].line_id])
        table.runner.post(chat, table.player(), "pause")
        assert seen == [frozenset({OWNER, ANA}), frozenset({ANA})]
        table.runner.resume(chat, table.player())
        # once the director holds Anya, her player is no longer at the table: the audience follows the ledger
        scene = table.runner.open(chat)
        table.runner.sovereignty.record(chat.campaign_id, ControlChange(
            chat.campaign_id, scene.persona.instance_id, DIRECTOR), table.owner)
        refused("not_at_table", table.runner.request, chat, table.player(), "pause")
        table.runner.request(chat, table.owner, "pause")
        assert seen[2:] == [frozenset({OWNER})]


# -- a skip never erases or replaces an accepted effect -------------------------------------------------------

def accepted_table(folder, *steps) -> Table:
    return Table(folder, list(steps), policy=LABEL_POLICY, labels=[say(UNOPPOSED)], adjudicate=True, effects=True)


def accepted(at: Table, chat):
    """Anya's action, framed, decided and its effect accepted with both keys, its turn still open."""
    at.runner.seat(chat, ANA)
    record = at.runner.post(chat, at.player(), "*I wave to Mara*")
    at.runner.label(chat, at.player())
    assert at.runner.confirm(chat, at.player()) is not None
    scene = at.runner.open(chat)
    (mara,) = [actor for actor in scene.cast if actor != scene.persona]
    turn = record.turn_id
    effect = at.runner.propose_effect(chat, at.player(), turn, MET, [Op("begin", "has_met", scene.persona, mara,
                                                                       scene.persona, turn)])
    at.runner.endorse_effect(chat, at.player(), turn, 1, effect.proposal.fingerprint)
    done = at.runner.approve_effect(chat, at.owner, turn, 1)
    assert done.state == "accepted"
    return turn, done


def canon(at: Table, chat, turn: str):
    """What 021 holds, the turn's effect and 029's outcome: everything a skip must leave as it was."""
    (record,) = [each for each in at.runner.open(chat).records if each.turn_id == turn]
    continuity = at.effects.continuity(chat.campaign_id, as_of=NOW, requested_by=DIRECTOR)
    return continuity.events, continuity.problems, record.effects, at.runner.outcome(chat) if record.open else None


def test_a_kept_take_over_an_accepted_effect_depicts_canon(cards):
    folder, chat = cards
    with accepted_table(folder, say("Mara waves back.")) as at:
        turn, effect = accepted(at, chat)
        take = at.runner.generate(chat, at.player())
        record = at.runner.open(chat).last
        assert {segment.status for segment in segments(record)} == {"proposed"}  # narration not yet kept
        record = at.runner.keep(chat, at.player(), take.take)
        (segment,) = segments(record)
        assert (segment.status, segment.event_id) == ("accepted", effect.event_id)


def test_skipping_canons_depiction_leaves_the_effect_its_event_and_the_outcome(cards):
    folder, chat = cards
    with accepted_table(folder, say("Mara grips Anya's hand, hard."), say("Mara nods.")) as at:
        turn, effect = accepted(at, chat)
        first = at.runner.generate(chat, at.player())
        before = canon(at, chat, turn)
        skipped = at.runner.post(chat, at.player(), "skip")  # a proposed take over accepted canon
        assert skipped.takes[-1].state == "withdrawn" and canon(at, chat, turn) == before
        again = at.runner.generate(chat, at.player())  # the reroll: the same authority, a fresh proposal
        assert again.take == first.take + 1 and canon(at, chat, turn) == before
        assert "grips" not in at.harness.prompts()[-1]  # the reroll never hears the skipped take
        outcome = at.runner.outcome(chat)
        at.runner.keep(chat, at.player(), again.take)
        events, problems, effects, _ = before
        line = again.lines[0].line_id
        refused("accepted_canon", at.runner.request, chat, at.player(), "redirect", [line], direction="Less.")
        refused("accepted_canon", at.runner.post, chat, at.player(), "redirect: less")
        after = at.runner.request(chat, at.player(), "skip", [line])  # presentation only
        assert after.kept.lines[0].state == "withdrawn" and after.effects == effects == (effect,)
        found = at.effects.continuity(chat.campaign_id, as_of=NOW, requested_by=DIRECTOR)
        assert (found.events, found.problems) == (events, problems)
        assert len(found.events) == 1 and found.events[0].event_id == effect.event_id
        assert outcome is not None and at.runner.open(chat).last.framed is not None
    with Table(folder, policy=LABEL_POLICY, adjudicate=True, effects=True) as again:  # reopened: as written
        record = again.runner.open(chat).last
        assert record.effects == (effect,) and not record.kept.shown()
        assert again.effects.continuity(chat.campaign_id, as_of=NOW, requested_by=DIRECTOR).events == events


def test_a_redirect_naming_canon_and_a_proposal_withdraws_the_proposal_alone(cards):
    folder, chat = cards
    with accepted_table(folder, say("Mara laughs."), say("Mara frowns.")) as at:
        turn, effect = accepted(at, chat)
        kept = at.runner.generate(chat, at.player())
        other = at.runner.generate(chat, at.player())  # left proposed beside the kept take
        at.runner.keep(chat, at.player(), kept.take)
        record = at.runner.open(chat).last
        by_id = {segment.segment_id: segment for segment in segments(record)}
        canon_line, proposal = kept.lines[0].line_id, other.lines[0].line_id
        assert (by_id[canon_line].status, by_id[proposal].status) == ("accepted", "proposed")
        mixed = at.runner.request(chat, at.player(), "redirect", [canon_line, proposal], direction="Less.")
        by_take = {take.take: take for take in mixed.takes}
        assert by_take[other.take].state == "withdrawn" and by_take[other.take].direction == "Less."
        assert by_take[kept.take].shown() and by_take[kept.take].direction is None
        before = files(folder)  # canon alone left: the redirect would withdraw nothing, and writes nothing
        refused("accepted_canon", at.runner.request, chat, at.player(), "redirect", [canon_line])
        assert files(folder) == before
        rephrased = at.runner.request(chat, at.player(), "rephrase", [canon_line], direction="Softer.")
        assert rephrased.kept.direction == "Softer." and rephrased.effects == (effect,)
        assert at.effects.continuity(chat.campaign_id, as_of=NOW, requested_by=DIRECTOR).events[0].event_id == (
            effect.event_id)
