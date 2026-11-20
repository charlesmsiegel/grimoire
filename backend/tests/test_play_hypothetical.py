"""073: a hypothetical reply. How an NPC *might* answer a line the player has not posted, with nothing saved.

Over the synthetic card library (``scene_fixtures``): Mara Vell's card chat with Anya Rook as the persona
(``human:ana`` plays her), and Tamsin Hale attached where a second NPC is needed. Every model call is a scripted
fake; every name is a placeholder.
"""

from __future__ import annotations

import shutil

import pytest

from generation_fixtures import say
from scene_fixtures import ANA, Table, attach_tamsin, card_library
from test_scene_session_zero import files, setting

from grimoire.play.hypothetical import imagine
from grimoire.scene import SceneRefused
from grimoire.testing.providers import Status

CONFESSION = '"I was the one who sank the Gull."'
HYPOTHETICAL = "Then you owe me a ship."
REAL = "Fair winds tonight."


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


def seated(table: Table, chat):
    """Ana seated, and one kept exchange so there is a story to answer from: Mara's actor."""
    table.runner.seat(chat, ANA)
    table.runner.post(chat, table.player(), '"Evening, Mara."')
    take = table.runner.generate(chat, table.player())
    table.runner.keep(chat, table.player(), take.take)
    (mara,) = table.runner.open(chat).npcs
    return mara


def test_a_hypothetical_reply_is_briefed_like_the_next_turn_and_writes_nothing(cards):
    folder, chat = cards
    with Table(folder, [say("Evening yourself."), say(HYPOTHETICAL), say(REAL)]) as table:
        mara = seated(table, chat)
        before, scene = files(folder), table.runner.open(chat)
        found = imagine(table.runner, chat, table.player(), mara, CONFESSION)
        assert files(folder) == before  # no turn, take, decision or knowledge: nothing at all
        after = table.runner.open(chat)
        assert after.turns.last.etag == scene.turns.last.etag and after.pending is None
        assert [line.text for line in found.take.lines] == [HYPOTHETICAL]
        assert (found.take.lines[0].actor, found.speaker, found.post.kind) == (mara, mara, "speech")
        assert found.take.outcome == "complete" and [call.status for call in found.take.calls] == ["accepted"]
        assert len(found.take.calls[0].attempts) == 1  # its usage is on the answer, since no take holds it
        prompt = table.harness.prompts()[-1]
        # briefed as the next turn would be: the story so far, then the line, in Mara's own brief
        assert "I was the one who sank the Gull." in prompt and "Evening yourself." in prompt

        # the actual scene goes on unchanged: nobody heard the confession or the reply
        table.runner.post(chat, table.player(), '"Any news from the harbor?"')
        take = table.runner.generate(chat, table.player())
        prompt = table.harness.prompts()[-1]
        assert "sank the Gull" not in prompt and HYPOTHETICAL not in prompt
        assert [line.text for line in take.lines] == [REAL]
        assert table.runner.open(chat).turns.last.record.number == scene.turns.last.record.number + 1


def test_the_brief_is_the_one_the_next_turn_would_send(cards):
    folder, chat = cards
    with Table(folder, [say("Evening yourself."), say(HYPOTHETICAL), say(REAL)]) as table:
        mara = seated(table, chat)
        imagine(table.runner, chat, table.player(), mara, CONFESSION)
        imagined = table.harness.prompts()[-1]
        # the same line posted for real, and the same NPC asked: the very same prompt
        table.runner.post(chat, table.player(), CONFESSION)
        table.runner.generate(chat, table.player(), speakers=(mara,))
        assert table.harness.prompts()[-1] == imagined


def test_a_reply_that_did_not_arrive_says_why_and_writes_nothing(cards):
    folder, chat = cards
    with Table(folder, [say("Evening yourself."), Status(500)]) as table:
        mara = seated(table, chat)
        before = files(folder)
        found = imagine(table.runner, chat, table.player(), mara, CONFESSION)
        assert files(folder) == before
        assert found.take.lines == () and found.take.calls[0].status == "failed"
        assert found.take.calls[0].attempts  # the attempt that was sent is still reported


def test_the_speaker_must_hear_the_line(cards):
    folder, chat = cards
    with Table(folder, [say("Evening yourself.")]) as table:
        mara = seated(table, chat)
        tamsin = attach_tamsin(table.store, table.owner, chat)
        table.runner.seat(chat, ANA)  # Tamsin is the director's
        before = files(folder)
        # whispered to Tamsin: Mara never hears it, so it is never what she answers
        refused("not_heard", imagine, table.runner, chat, table.player(), mara,
                'I whisper to Tamsin Hale: "I sank the Gull."', mode="action")
        assert files(folder) == before and len(table.harness.prompts()) == 1  # nothing billed
        assert tamsin in table.runner.open(chat).npcs


def test_what_a_real_reply_would_refuse_a_hypothetical_refuses_before_any_call(cards):
    folder, chat = cards
    with Table(folder, [say("Evening yourself.")]) as table:
        mara = seated(table, chat)
        persona = table.runner.open(chat).persona
        refused("not_an_npc", imagine, table.runner, chat, table.player(), persona, CONFESSION)
        refused("table_request", imagine, table.runner, chat, table.player(), mara, "Grimoire, skip")
        refused("no_content", imagine, table.runner, chat, table.player(), mara, "   ")
        table.runner.post(chat, table.player(), '"And the cargo?"')  # a turn waiting for its answer
        refused("turn_open", imagine, table.runner, chat, table.player(), mara, CONFESSION)
        table.runner.pass_turn(chat, table.player())
        table.runner.request(chat, table.player(), "pause")
        refused("paused", imagine, table.runner, chat, table.player(), mara, CONFESSION)
        assert len(table.harness.prompts()) == 1


def test_no_runtime_means_no_hypothetical(cards):
    folder, chat = cards
    with Table(folder, [say("Evening yourself.")]) as table:
        mara = seated(table, chat)
    with Table(folder, runtime=False) as table:
        refused("no_runtime", imagine, table.runner, chat, table.player(), mara, CONFESSION)


def test_a_session_zero_limit_holds_a_hypothetical_like_any_narration(cards):
    folder, chat = cards
    with Table(folder, [say("Evening yourself."), say(HYPOTHETICAL)]) as table:
        mara = seated(table, chat)
        table.runner.change_boundaries(chat, table.player(), setting(ANA, "b-sea", "drowning", "avoid"))
        before = files(folder)
        refused("held_by_boundary", imagine, table.runner, chat, table.player(), mara, CONFESSION)
        assert files(folder) == before and len(table.harness.prompts()) == 1  # held before any call


def test_a_limit_set_while_the_call_is_in_flight_holds_its_reply(cards, monkeypatch):
    folder, chat = cards
    with Table(folder, [say("Evening yourself."), say(HYPOTHETICAL)]) as table:
        mara = seated(table, chat)
        run_turn = table.harness.runtime.run_turn

        def then_limited(*args, **kwargs):
            outcome = run_turn(*args, **kwargs)
            table.runner.change_boundaries(chat, table.player(), setting(ANA, "b-late", "drowning", "avoid"))
            return outcome

        monkeypatch.setattr(table.harness.runtime, "run_turn", then_limited)
        found = imagine(table.runner, chat, table.player(), mara, CONFESSION)
        assert found.take.lines == ()
        assert [(call.status, call.reason) for call in found.take.calls] == [("rejected", "held_by_boundary")]
        assert found.take.calls[0].attempts
