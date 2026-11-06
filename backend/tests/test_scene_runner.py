"""035's scene runner, one rule at a time: who may act, what each call writes, and every refusal.

Over the synthetic card library (``scene_fixtures``): Mara Vell's card chat with Anya Rook as the player's
persona, and the Storm over Port Vell scenario chat. Every model call is a scripted fake.
"""

from __future__ import annotations

import shutil
import threading

import pytest

from chat_fixtures import Clock
from generation_fixtures import Harness
from scene_fixtures import (
    ANA, BOB, LIGHTHOUSE, MARA_VELL, POLICY, STORM, TAMSIN, VELL_EXAMPLE, Table, Tagged, attach_tamsin, card_library,
    say,
)
from store_helpers import OWNER

from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.core import DIRECTOR
from grimoire.core.identity import InstanceRef, ScopeRef
from grimoire.core.inference import InferencePolicy
from grimoire.runtime import RunFence
from grimoire.play import SceneRunner
from grimoire.ledgers.codec import ControlChange
from grimoire.scene import SceneRefused, TurnRecord, Take, heard_for, table_lines
from grimoire.testing.providers import Supersede


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return card_library(tmp_path_factory.mktemp("built"))


@pytest.fixture
def cards(built, tmp_path):
    folder, card_chat, storm = built
    copy = tmp_path / "cards"
    shutil.copytree(folder, copy)
    return copy, card_chat, storm


def refused(reason: str, call, *args, **kwargs) -> SceneRefused:
    with pytest.raises(SceneRefused) as error:
        call(*args, **kwargs)
    assert error.value.reason == reason, error.value
    return error.value


def seated(table: Table, *chats) -> None:
    for chat in chats:
        table.runner.seat(chat, ANA)


def actors(table: Table, chat):
    scene = table.runner.open(chat)
    (mara,) = [actor for actor in scene.cast if actor != scene.persona]
    return scene.persona, mara


# -- the runner and its principals ------------------------------------------------------------------

def test_the_runner_writes_only_with_a_trusted_human_handle_its_authority_issued(cards):
    folder, _, _ = cards
    with Table(folder) as table:
        for handle in (table.authority.handle_for(OWNER, trusted=False), table.authority.handle_for("app:x", trusted=True),
                       table.harness.handle):
            refused("bad_handle", SceneRunner, table.store, table.authority, handle, owner=OWNER)
        refused("bad_runtime", SceneRunner, table.store, table.authority, table.owner, owner=OWNER, runtime=object())


def test_seat_gives_the_persona_to_its_player_and_the_cast_to_the_director_once(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        anya, mara = actors(table, chat)
        assert table.runner.open(chat).controller(mara) is None
        table.runner.seat(chat, ANA)
        table.runner.seat(chat, BOB)  # a recorded controller is never changed here
        scene = table.runner.open(chat)
        assert scene.controller(anya) == ANA and scene.controller(mara) == DIRECTOR
        assert scene.players == (anya,) and scene.npcs == (mara,)
        refused("invalid_field", table.runner.seat, chat, "app:x")


def test_only_a_player_at_the_table_or_the_owner_acts(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        seated(table, chat)
        refused("not_at_table", table.runner.begin, chat, table.player(BOB))
        refused("not_authenticated", table.runner.begin, chat, table.authority.handle_for(ANA, trusted=False))
        refused("not_authenticated", table.runner.begin, chat, table.harness.handle)  # another authority's handle
        table.runner.begin(chat, table.owner)  # the owner may open play
        table.runner.keep(chat, table.player(), 1)
        refused("not_your_character", table.runner.post, chat, table.owner, '"Speaking for Anya."')


def test_an_unseated_chat_never_generates(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        refused("unassigned_actor", table.runner.generate, chat, table.owner)


# -- opening ---------------------------------------------------------------------------------------

def test_a_card_chat_opens_with_its_first_greeting_spoken_by_its_character(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        seated(table, chat)
        anya, mara = actors(table, chat)
        opening = table.runner.begin(chat, table.player())
        (take,) = opening.takes
        assert (opening.number, opening.origin, take.state) == (1, "opening", "proposed")
        (line,) = take.lines
        assert line.actor == mara and line.speaker == "Mara Vell" and "{{" not in line.text
        assert set(line.audience) == {anya, mara}
        table.runner.keep(chat, table.player(), 1)
        assert not table.runner.open(chat).last.open
        refused("already_begun", table.runner.begin, chat, table.player())


def test_a_scenario_chat_opens_narrated_to_the_players(cards):
    folder, _, storm = cards
    with Table(folder) as table:
        seated(table, storm)
        (line,) = table.runner.begin(storm, table.player()).takes[0].lines
        assert line.actor is None and line.audience == (table.runner.open(storm).persona,)


# -- posting ---------------------------------------------------------------------------------------

def test_a_post_is_saved_before_anything_is_generated_and_a_retry_returns_it(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        seated(table, chat)
        anya, mara = actors(table, chat)
        turn = table.runner.post(chat, table.player(), '"Is the lighthouse still dark?"')
        assert (turn.number, turn.origin, turn.post.kind) == (1, "post", "speech")
        assert turn.post.actor == anya and set(turn.post.audience) == {anya, mara}
        assert table.runner.open(chat).pending == turn  # on disk before any call
        assert table.runner.post(chat, table.player(), '"Is the lighthouse still dark?"') == turn
        refused("turn_open", table.runner.post, chat, table.player(), '"Something else."')
        assert len(table.runner.open(chat).records) == 1


def test_an_action_everyone_present_sees(cards):
    folder, _, storm = cards
    with Table(folder) as table:
        seated(table, storm)
        turn = table.runner.post(storm, table.player(), "*I shake the rain from my coat*")
        assert turn.post.kind == "action" and turn.post.audience == table.runner.open(storm).cast


@pytest.mark.parametrize(("text", "mode", "reason"), [
    ("   ", "auto", "no_content"),
    ('"..."', "auto", "no_content"),  # nothing anyone could hear
    ("*...*", "auto", "no_content"),
    ('"' + "." * 2100 + ' The real words."', "auto", "no_content"),  # nothing a listener would hear, once clipped
    # 032 transmits nothing on an Unread: a message it cannot read is refused, never made a public action
    ('I whisper to Mara, "the vault code is 1234"', "auto", "unknown_addressee"),
    ("I whisper to Mara Vell: the vault code is 1234", "auto", "unclear_content"),
    ("Could you pause?", "command", "uncertain"),
])
def test_a_post_that_cannot_be_played_writes_nothing(cards, text, mode, reason):
    folder, chat, _ = cards
    with Table(folder) as table:
        seated(table, chat)
        refused(reason, table.runner.post, chat, table.player(), text, mode=mode)
        assert table.runner.open(chat).records == ()


def test_a_chat_without_a_persona_has_no_one_to_post_for(cards):
    folder, _, _ = cards
    with Table(folder) as table:
        chats = ChatLibrary(table.store, table.owner, clock=Clock(), new_id=Tagged("bare"), owner=OWNER)
        bare = chats.start_chat(Launch("character", MARA_VELL)).commit().chat
        table.runner.seat(bare, ANA)
        refused("no_persona", table.runner.post, bare, table.owner, '"Hello?"')


# -- generating ------------------------------------------------------------------------------------

def test_generate_proposes_a_take_with_its_usage_and_keep_closes_the_turn(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Dark as a debt, and as long.")]) as table:
        seated(table, chat)
        anya, mara = actors(table, chat)
        table.runner.post(chat, table.player(), '"Is the lighthouse still dark?"')
        take = table.runner.generate(chat, table.player())
        assert (take.take, take.state, take.outcome) == (1, "proposed", "complete")
        (line,) = take.lines
        assert line.actor == mara and line.text == "Dark as a debt, and as long." and set(line.audience) == {anya, mara}
        (call,) = take.calls
        assert call.status == "accepted" and call.attempts and call.attempts[0].usage.output_tokens is not None
        prompt = table.harness.prompts()[0]
        assert LIGHTHOUSE in prompt and "Is the lighthouse still dark?" in prompt and VELL_EXAMPLE in prompt
        refused("not_proposed", table.runner.keep, chat, table.player(), 2)
        kept = table.runner.keep(chat, table.player(), 1)
        assert kept.takes[0].state == "kept" and not kept.open
        refused("no_open_turn", table.runner.keep, chat, table.player(), 1)


def test_generate_with_no_open_turn_opens_a_prompt_turn(cards):
    folder, chat, _ = cards
    with Table(folder, [say("The tide turns.")]) as table:
        seated(table, chat)
        take = table.runner.generate(chat, table.player())
        turn = table.runner.open(chat).pending
        assert turn.origin == "prompt" and turn.post is None and turn.takes == (take,)


def test_another_take_on_the_same_turn_and_a_pass(cards):
    folder, chat, _ = cards
    with Table(folder, [say("First."), say("Second.")]) as table:
        seated(table, chat)
        table.runner.post(chat, table.player(), '"Well?"')
        table.runner.generate(chat, table.player())
        second = table.runner.generate(chat, table.player())
        assert second.take == 2 and [t.state for t in table.runner.open(chat).pending.takes] == ["proposed"] * 2
        passed = table.runner.pass_turn(chat, table.player())
        assert passed.passed_by == ANA and not passed.open
        assert table.runner.post(chat, table.player(), '"Again, then."').number == 2


def test_speakers_are_present_voiced_npcs(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        seated(table, chat)
        anya, mara = actors(table, chat)
        stranger = InstanceRef(chat.campaign_id, "character", "nobody")
        refused("not_present", table.runner.generate, chat, table.player(), speakers=[stranger])
        refused("not_an_npc", table.runner.generate, chat, table.player(), speakers=[anya])
        refused("invalid_field", table.runner.generate, chat, table.player(), speakers=[mara, mara])
        refused("nothing_to_generate", table.runner.generate, chat, table.player(), speakers=[])


def test_without_a_model_nothing_is_generated(cards):
    folder, chat, _ = cards
    with Table(folder, runtime=False) as table:
        seated(table, chat)
        refused("no_runtime", table.runner.generate, chat, table.player())


def test_a_director_reply_needs_the_policy_and_a_scenario(cards):
    folder, chat, storm = cards
    with Table(folder, policy=InferencePolicy()) as table:
        seated(table, storm)
        refused("director_declined", table.runner.generate, storm, table.player(), director=True)
    with Table(folder) as table:
        seated(table, chat)
        refused("no_scenario", table.runner.generate, chat, table.player(), speakers=[], director=True)
        refused("nothing_to_generate", table.runner.generate, storm, table.player())  # nobody speaks there
    with Table(folder, [say("Thunder rolls over the quay.")]) as table:
        take = table.runner.generate(storm, table.player(), director=True)
        (line,) = take.lines
        assert line.actor is None and line.audience == table.runner.open(storm).players


def test_a_run_made_stale_mid_stream_keeps_its_usage_and_no_lines(cards):
    folder, chat, _ = cards
    fence = RunFence()
    with Table(folder, [Supersede(1, say("Too late."))], fence=fence) as table:
        seated(table, chat)
        table.harness.transport.on_supersede = lambda: fence.claim(str(chat))
        take = table.runner.generate(chat, table.player())
        assert (take.state, take.outcome, take.lines) == ("stale", "stale", ())
        stored = table.runner.open(chat).pending.takes[0]
        assert stored.state == "stale" and stored.calls[0].attempts  # its usage is on disk


def test_a_run_made_stale_after_the_call_never_writes_its_lines(cards, monkeypatch):
    folder, chat, _ = cards
    fence = RunFence()
    with Table(folder, [say("Late words.")], fence=fence) as table:
        seated(table, chat)
        run_turn = table.harness.runtime.run_turn

        def then_superseded(*args, **kwargs):
            outcome = run_turn(*args, **kwargs)
            fence.claim(str(chat))  # a newer run of the same chat started
            return outcome

        monkeypatch.setattr(table.harness.runtime, "run_turn", then_superseded)
        take = table.runner.generate(chat, table.player())
        assert take.state == "stale" and take.lines == () and take.calls[0].attempts
        assert table.runner.open(chat).pending.takes[0].state == "stale"


def test_a_reroll_that_supersedes_a_running_take_keeps_its_lines_when_the_old_run_lands_first(cards):
    folder, chat, _ = cards
    fence = RunFence()
    with Table(folder, [say("Old words.")], fence=fence) as table:
        seated(table, chat)
        table.runner.post(chat, table.player(), '"Is the cargo safe?"')
        newer = Harness([say("New words.")])
        reroll = SceneRunner(table.store, table.authority, table.owner, owner=OWNER, runtime=newer.runtime,
                             policy=POLICY, fence=fence)
        old_in_call, old_may_return, old_landed = threading.Event(), threading.Event(), threading.Event()
        old_run, new_run = table.harness.runtime.run_turn, newer.runtime.run_turn

        def slow_old(*args, **kwargs):
            outcome = old_run(*args, **kwargs)
            old_in_call.set()
            old_may_return.wait(10)
            return outcome

        def new_after_the_old_lands(*args, **kwargs):
            old_may_return.set()
            old_landed.wait(10)  # the old run, now stale, writes its turn while this call is in flight
            return new_run(*args, **kwargs)

        table.harness.runtime.run_turn, newer.runtime.run_turn = slow_old, new_after_the_old_lands
        results = {}

        def old():
            try:
                results["old"] = table.runner.generate(chat, table.player())
            finally:
                old_landed.set()

        thread = threading.Thread(target=old)
        thread.start()
        assert old_in_call.wait(10)
        results["new"] = reroll.generate(chat, table.player())
        thread.join(10)
        assert (results["old"].state, results["old"].lines) == ("stale", ())
        assert results["new"].state == "proposed" and [l.text for l in results["new"].lines] == ["New words."]
        assert [t.state for t in table.runner.open(chat).pending.takes] == ["stale", "proposed"]


def test_a_persona_the_director_controls_is_reported_and_never_played(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        seated(table, chat)
        _, mara = actors(table, chat)
        chats = ChatLibrary(table.store, table.owner, clock=Clock(), new_id=Tagged("p"), owner=OWNER)
        taken = chats.start_chat(Launch("scenario", STORM), persona=MARA_VELL, scope=ScopeRef(chat.campaign_id),
                                 reuse_actors={("persona", MARA_VELL.entity_key): mara}).commit().chat
        table.runner.seat(taken, ANA)  # Mara is already the director's: seating never changes that
        scene = table.runner.open(taken)
        assert scene.persona == mara and ("persona_not_player", str(mara)) in scene.problems
        assert scene.players == () and mara in scene.npcs
        refused("persona_not_player", table.runner.begin, taken, table.owner)
        refused("persona_not_player", table.runner.post, taken, table.owner, '"Hello."')
        refused("persona_not_player", table.runner.generate, taken, table.owner)
        assert table.runner.open(taken).records == ()


def test_an_interrupted_run_is_reported_and_superseded_by_the_next(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Back again.")]) as table:
        seated(table, chat)
        scene = table.runner.open(chat)
        crashed = TurnRecord(chat, 1, "prompt", takes=(Take(1, "running", "generated", scene.cast, by=ANA),))
        table.runner.scenes.write(crashed, None)
        assert ("interrupted", crashed.turn_id) in table.runner.open(chat).problems
        take = table.runner.generate(chat, table.player())
        assert take.take == 2
        assert [t.state for t in table.runner.open(chat).pending.takes] == ["superseded", "proposed"]


def test_an_unreadable_turn_of_this_chat_stops_every_write(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        seated(table, chat)
        table.runner.begin(chat, table.player())
        broken = folder / "campaigns" / chat.campaign_id / "turns" / f"{chat.chat_id}.000002.json"
        broken.write_bytes(b"{not json")
        refused("turns_unreadable", table.runner.post, chat, table.player(), '"Hello."')
        refused("turns_unreadable", table.runner.keep, chat, table.player(), 1)
        line = f"{chat.chat_id}.000001.1.1"
        refused("turns_unreadable", table.runner.request, chat, table.player(), "skip", [line])
        refused("turns_unreadable", table.runner.request, chat, table.player(), "pause")
        refused("turns_unreadable", table.runner.strike, chat, table.player(), line)
        refused("turns_unreadable", table.runner.resume, chat, table.player())
        assert broken.read_bytes() == b"{not json"


# -- the table's choices -----------------------------------------------------------------------------

def _played(table, chat):
    seated(table, chat)
    table.runner.post(chat, table.player(), '"Well?"')
    take = table.runner.generate(chat, table.player())
    return take


def test_strike_takes_a_kept_line_out_of_what_everyone_heard(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Words to forget.")]) as table:
        (line,) = _played(table, chat).lines
        refused("not_kept", table.runner.strike, chat, table.player(), line.line_id)
        table.runner.keep(chat, table.player(), 1)
        struck = table.runner.strike(chat, table.player(), line.line_id)
        assert struck.takes[0].lines[0].state == "struck" and struck.takes[0].lines[0].by == ANA
        refused("not_kept", table.runner.strike, chat, table.player(), line.line_id)
        refused("unknown_line", table.runner.strike, chat, table.player(), "no-such-line")


def test_redirect_withdraws_the_lines_and_records_the_direction(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Too cruel.")]) as table:
        (line,) = _played(table, chat).lines
        turn = table.runner.request(chat, table.player(), "redirect", [line.line_id], direction="Kinder, please.")
        (take,) = turn.takes
        assert take.state == "withdrawn" and take.direction == "Kinder, please." and take.lines[0].state == "withdrawn"
        refused("not_proposed", table.runner.keep, chat, table.player(), 1)


def test_skip_withdraws_a_kept_line_too(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Skip me.")]) as table:
        (line,) = _played(table, chat).lines
        table.runner.keep(chat, table.player(), 1)
        turn = table.runner.request(chat, table.player(), "skip", [line.line_id])
        assert turn.takes[0].state == "kept" and turn.takes[0].lines[0].state == "withdrawn"
        refused("no_targets", table.runner.request, chat, table.player(), "skip")
        refused("unknown_line", table.runner.request, chat, table.player(), "skip", ["nope"])


def test_pause_stops_play_until_the_player_who_paused_or_the_owner_resumes(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Paused talk.")]) as table:
        seated(table, chat)
        refused("nothing_to_pause", table.runner.request, chat, table.player(), "pause")
        table.runner.post(chat, table.player(), '"Wait."')
        paused = table.runner.request(chat, table.player(), "pause")
        assert paused.paused and paused.paused_by == ANA
        refused("already_paused", table.runner.request, chat, table.player(), "pause")
        refused("paused", table.runner.generate, chat, table.player())
        table.runner.resume(chat, table.player())
        refused("not_paused", table.runner.resume, chat, table.player())
        table.runner.request(chat, table.owner, "pause")
        refused("not_yours", table.runner.resume, chat, table.player())  # the owner paused it
        table.runner.resume(chat, table.owner)
        table.runner.request(chat, table.player(), "pause")
        table.runner.resume(chat, table.owner)  # the owner may always lift a pause
        assert table.runner.generate(chat, table.player()).state == "proposed"


def test_a_take_is_kept_only_by_the_cast_it_was_written_for(cards):
    folder, chat, _ = cards
    with Table(folder, [say("She is the informer."), say("Then we wait.")]) as table:
        seated(table, chat)
        tamsin = attach_tamsin(table.store, table.owner, chat)
        table.runner.seat(chat, ANA)
        table.runner.post(chat, table.player(), '"Who do we trust?"')
        written = table.runner.generate(chat, table.player(), speakers=[actors_without(table, chat, tamsin)])
        assert tamsin in written.lines[0].audience
        chats = ChatLibrary(table.store, table.owner, clock=Clock(), new_id=Tagged("d"), owner=OWNER)
        _, etag = chats.read_chat(chat)
        chats.detach_source(chat, TAMSIN, "character", expected=etag).commit()  # she leaves before it is kept
        refused("cast_changed", table.runner.keep, chat, table.player(), written.take)
        again = table.runner.generate(chat, table.player())
        assert tamsin not in again.lines[0].audience
        table.runner.keep(chat, table.player(), again.take)


def actors_without(table: Table, chat, *left_out):
    scene = table.runner.open(chat)
    (mara,) = [actor for actor in scene.cast if actor != scene.persona and actor not in left_out]
    return mara


def test_a_pause_written_while_a_post_is_saved_is_never_lost(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        seated(table, chat)
        table.runner.begin(chat, table.player())
        table.runner.keep(chat, table.player(), 1)
        write = table.runner.scenes.write

        def pause_first(record, base, **kwargs):  # the owner pauses after the post read the chat
            if base is None and record.number == 2:
                table.runner.scenes.write = write
                table.runner.request(chat, table.owner, "pause")
            return write(record, base, **kwargs)

        table.runner.scenes.write = pause_first
        refused("turn_changed", table.runner.post, chat, table.player(), '"Onward."')
        scene = table.runner.open(chat)
        assert len(scene.records) == 1 and scene.paused
        refused("paused", table.runner.post, chat, table.player(), '"Onward."')


def test_a_pause_stops_keeping_and_passing_too(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Not now.")]) as table:
        (line,) = _played(table, chat).lines
        table.runner.request(chat, table.player(), "pause")
        refused("paused", table.runner.keep, chat, table.owner, 1)
        refused("paused", table.runner.pass_turn, chat, table.owner)
        table.runner.resume(chat, table.player())
        assert table.runner.keep(chat, table.player(), 1).kept.lines == (line,)


def test_keeping_a_take_while_another_runs_lands_the_other_stale(cards, monkeypatch):
    folder, chat, _ = cards
    with Table(folder, [say("First take."), say("Second take.")]) as table:
        _played(table, chat)
        run_turn = table.harness.runtime.run_turn

        def keep_first(*args, **kwargs):
            table.runner.keep(chat, table.player(), 1)  # the table keeps take 1 while take 2 is generating
            return run_turn(*args, **kwargs)

        monkeypatch.setattr(table.harness.runtime, "run_turn", keep_first)
        second = table.runner.generate(chat, table.player())
        assert (second.state, second.lines) == ("stale", ()) and second.calls[0].attempts
        record = table.runner.open(chat).records[-1]
        assert not record.open and [t.state for t in record.takes] == ["kept", "stale"]


def test_an_action_that_may_carry_a_private_message_is_heard_by_its_sender_alone(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Well then.")]) as table:
        seated(table, chat)
        anya, mara = actors(table, chat)
        turn = table.runner.post(chat, table.player(), "*I whisper the vault code 1234 under my breath*")
        assert turn.post.withheld and turn.post.audience == (anya,) and turn.post.private
        scene = table.runner.open(chat)
        assert not [h for h in heard_for(scene.records, mara, scene.names) if "1234" in h.text]
        assert not [h for h in table_lines(scene.records, scene.names) if "1234" in h.text]
        assert [h.text for h in heard_for(scene.records, anya, scene.names)] == [
            "*I whisper the vault code 1234 under my breath*"]
        table.runner.generate(chat, table.player())
        assert "1234" not in table.harness.prompts()[0]
        table.runner.keep(chat, table.player(), 1)
        plain = table.runner.post(chat, table.player(), "*I light a lamp*")
        assert not plain.post.withheld and set(plain.post.audience) == {anya, mara}


def test_an_opening_is_asked_again_when_the_cast_changed_before_it_was_kept(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        seated(table, chat)
        table.runner.begin(chat, table.player())
        tamsin = attach_tamsin(table.store, table.owner, chat)
        table.runner.seat(chat, ANA)
        refused("cast_changed", table.runner.keep, chat, table.player(), 1)
        again = table.runner.begin(chat, table.player())
        assert [t.take for t in again.takes] == [1, 2] and tamsin in again.takes[1].lines[0].audience
        table.runner.keep(chat, table.player(), 2)


def test_a_take_is_not_kept_once_a_listener_changed_hands(cards):
    folder, _, storm = cards
    with Table(folder, [say("Only the players see the hidden door.")]) as table:
        tamsin = attach_tamsin(table.store, table.owner, storm)
        table.runner.sovereignty.record(storm.campaign_id, ControlChange(storm.campaign_id, tamsin.instance_id, BOB),
                                        table.owner)
        seated(table, storm)
        table.runner.begin(storm, table.player())
        table.runner.keep(storm, table.player(), 1)
        take = table.runner.generate(storm, table.player(), director=True)
        assert tamsin in take.lines[0].audience  # narration: the table, Bob's Tamsin included
        table.runner.sovereignty.record(storm.campaign_id, ControlChange(storm.campaign_id, tamsin.instance_id,
                                                                         DIRECTOR), table.owner)
        refused("cast_changed", table.runner.keep, storm, table.player(), take.take)  # she is an NPC now


def test_a_take_is_not_kept_once_its_speaker_is_a_player_character(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Mara's own words.")]) as table:
        take = _played(table, chat)
        _, mara = actors(table, chat)
        table.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, mara.instance_id, BOB),
                                        table.owner)
        refused("cast_changed", table.runner.keep, chat, table.player(), take.take)  # Bob speaks for Mara now


def test_a_run_claims_its_fence_as_its_running_take_is_published(cards, monkeypatch):
    folder, chat, _ = cards
    fence = RunFence()
    with Table(folder, [say("Claimed in step.")], fence=fence) as table:
        seated(table, chat)
        table.runner.post(chat, table.player(), '"Now?"')
        claim, seen = fence.claim, []

        def watched(key):
            held = []

            def try_lock():
                got = table.store.lock.acquire(blocking=False)
                if got:
                    table.store.lock.release()
                held.append(not got)

            probe = threading.Thread(target=try_lock)
            probe.start()
            probe.join()
            running = table.runner.scenes.turns(chat).last.record.running
            seen.append((held[0], running is not None))
            return claim(key)

        monkeypatch.setattr(fence, "claim", watched)
        assert table.runner.generate(chat, table.player()).state == "proposed"
        assert seen == [(True, True)]  # under the store's lock, with the running take already stored
