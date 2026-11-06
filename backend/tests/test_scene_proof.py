"""035's proof: play, save and reopen a migrated V1 campaign and a world-less card chat with separate voices.

Each table is opened, played, closed and opened again with a new store, authority and model runtime, as an
app restart would. Every NPC call is checked for its own voice and for hearing only what it was present
for; withdrawn and struck lines are heard by no one. Synthetic fixtures, scripted model calls.
"""

from __future__ import annotations

from scene_fixtures import (
    ANA, LIGHTHOUSE, MARA, MARA_EXAMPLE, MARA_VOICE, SERAPHINE, SERAPHINE_EXAMPLE, V1_CHAT, VELL_EXAMPLE, WINIFRED,
    Table, attach_tamsin, card_library, say, v1_library,
)

WHISPER = "The ledger is under the floor."
TAMSIN_EXAMPLE = "Every crate has a story, and a stamp."


def the(prompts: list[str], marker: str) -> str:
    """The one prompt that carries ``marker`` (a speaker's own voice example)."""
    (found,) = [prompt for prompt in prompts if marker in prompt]
    return found


def usage(record) -> int:
    return sum(attempt.usage.input_tokens or 0 for take in record.takes for call in take.calls
               for attempt in call.attempts)


def test_a_migrated_v1_campaign_plays_saves_and_reopens(tmp_path):
    folder = v1_library(tmp_path)
    turn_one = [say("Counted, and short by three."), say("Ask the lamps, they saw it go.")]
    turn_two = [say("Then we dig at dawn."), say("Something stirs below the quay.")]
    with Table(folder, turn_one + turn_two, voices=MARA_VOICE) as table:
        runner, ana = table.runner, table.player()
        runner.seat(V1_CHAT, ANA)
        scene = runner.open(V1_CHAT)
        assert scene.players == (WINIFRED,) and set(scene.npcs) == {MARA, SERAPHINE}
        assert not [problem for problem in scene.problems if problem[0] == "no_voice"]

        runner.post(V1_CHAT, ana, '"Where is the ledger?"')
        take = runner.generate(V1_CHAT, ana)
        assert [line.actor for line in take.lines] == [MARA, SERAPHINE]
        prompts = table.harness.prompts()
        mara, seraphine = the(prompts, MARA_EXAMPLE), the(prompts, SERAPHINE_EXAMPLE)
        assert "Clipped, dry" in mara and SERAPHINE_EXAMPLE not in mara  # each speaks with its own voice
        assert MARA_EXAMPLE not in seraphine and "Where is the ledger?" in seraphine
        runner.keep(V1_CHAT, ana, 1)

        whisper = runner.post(V1_CHAT, ana, f'I whisper to Mara, "{WHISPER}"')
        assert set(whisper.post.audience) == {WINIFRED, MARA}
        runner.request(V1_CHAT, ana, "pause")
        runner.resume(V1_CHAT, ana)
        take = runner.generate(V1_CHAT, ana)
        prompts = table.harness.prompts()[2:]
        assert WHISPER in the(prompts, MARA_EXAMPLE) and WHISPER not in the(prompts, SERAPHINE_EXAMPLE)
        seraphine_line = next(line for line in take.lines if line.actor == SERAPHINE)
        turn = runner.request(V1_CHAT, ana, "redirect", [seraphine_line.line_id], direction="Quieter.")
        assert turn.takes[0].direction == "Quieter."
        runner.keep(V1_CHAT, ana, 1)  # only Mara's line is kept
        runner.post(V1_CHAT, ana, '"And the debts?"')  # saved; then the app closes before any call

    replies = [say("Paid in salt, every one."), say("The lamps keep their counsel.")]
    with Table(folder, replies, voices=MARA_VOICE) as table:  # a restart: new store, authority and runtime
        runner, ana = table.runner, table.player()
        scene = runner.open(V1_CHAT)
        assert [record.number for record in scene.records] == [1, 2, 3] and scene.pending.post.text == "And the debts?"
        assert all(usage(record) > 0 for record in scene.records[:2])  # every call's usage survived
        take = runner.generate(V1_CHAT, ana)
        prompts = table.harness.prompts()
        mara, seraphine = the(prompts, MARA_EXAMPLE), the(prompts, SERAPHINE_EXAMPLE)
        assert WHISPER in mara and WHISPER not in seraphine
        assert "Something stirs below the quay." not in seraphine + mara  # a withdrawn line is heard by no one
        assert "Then we dig at dawn." in seraphine  # a kept line everyone present heard
        runner.keep(V1_CHAT, ana, 1)
        runner.request(V1_CHAT, ana, "skip", [take.lines[0].line_id])
        assert runner.open(V1_CHAT).records[-1].takes[0].lines[0].state == "withdrawn"


def test_a_card_chat_with_lore_plays_saves_and_reopens_with_a_late_joiner(tmp_path):
    folder, chat, _storm = card_library(tmp_path)
    with Table(folder, [say("Dark, and the harbor pays for it.")]) as table:
        runner, ana = table.runner, table.player()
        runner.seat(chat, ANA)
        runner.begin(chat, ana)
        runner.keep(chat, ana, 1)
        runner.post(chat, ana, '"Is the lighthouse still dark?"')
        runner.generate(chat, ana)
        (prompt,) = table.harness.prompts()
        assert LIGHTHOUSE in prompt and VELL_EXAMPLE in prompt  # the card's lore and its own voice
        runner.keep(chat, ana, 1)
        whisper = runner.post(chat, ana, f'I whisper to Mara Vell, "{WHISPER}"')
        assert len(whisper.post.audience) == 2
        runner.pass_turn(chat, ana)

    with Table(folder, [say("Noted, and filed."), say("Papers first, then gossip.")]) as table:
        runner, ana = table.runner, table.player()
        tamsin = attach_tamsin(table.store, table.owner, chat)
        runner.seat(chat, ANA)
        assert tamsin in runner.open(chat).npcs
        runner.post(chat, ana, '"Who keeps the customs house now?"')
        take = runner.generate(chat, ana)
        assert len(take.lines) == 2
        prompts = table.harness.prompts()
        late, mara = the(prompts, TAMSIN_EXAMPLE), the(prompts, VELL_EXAMPLE)
        assert WHISPER in mara and WHISPER not in late  # the late joiner never heard the whisper
        assert "Is the lighthouse still dark?" not in late and "Who keeps the customs house now?" in late
        assert VELL_EXAMPLE not in late and TAMSIN_EXAMPLE not in mara
        runner.keep(chat, ana, 1)

    with Table(folder) as table:
        scene = table.runner.open(chat)
        assert [r.number for r in scene.records] == [1, 2, 3, 4] and not scene.pending
        assert scene.records[2].passed_by == ANA and usage(scene.records[3]) > 0


def test_a_scenario_only_chat_is_narrated_by_an_explicit_director_reply(tmp_path):
    folder, _chat, storm = card_library(tmp_path)
    with Table(folder, [say("Lightning shows a ship with no lamps.")]) as table:
        runner, ana = table.runner, table.player()
        runner.seat(storm, ANA)
        runner.begin(storm, ana)
        runner.keep(storm, ana, 1)
        runner.post(storm, ana, "*I shake the rain from my coat*")
        take = runner.generate(storm, ana, director=True)
        (line,) = take.lines
        assert line.actor is None and line.audience == runner.open(storm).players
        (prompt,) = table.harness.prompts()
        assert "shake the rain" in prompt
        runner.keep(storm, ana, 1)
    with Table(folder) as table:
        records = table.runner.open(storm).records
        assert [r.takes[0].state for r in records] == ["kept", "kept"] and usage(records[1]) > 0


def test_a_post_saved_before_generation_survives_a_restart_and_is_answered(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Table(folder) as table:
        table.runner.seat(chat, ANA)
        table.runner.post(chat, table.player(), '"Hold that thought."')
    with Table(folder, [say("Held, and no longer.")]) as table:
        assert table.runner.open(chat).pending.post.text == "Hold that thought."
        take = table.runner.generate(chat, table.player())
        assert take.lines and "Hold that thought." in table.harness.prompts()[0]
