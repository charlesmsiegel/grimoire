"""058: two player characters under independent control, posted as one turn and answered once.

Over the synthetic card library (``scene_fixtures``): Anya Rook is the chat's persona (``human:ana`` plays her),
Mara Vell is the director's NPC, and Tamsin Hale is attached and handed to ``human:bob`` by the owner. Every
model call is a scripted fake.
"""

from __future__ import annotations

import shutil

import pytest

from chat_fixtures import Clock
from scene_fixtures import ANA, BOB, POLICY, TAMSIN, Table, Tagged, attach_tamsin, card_library, say
from store_helpers import OWNER

from grimoire.app.adapters.play_story import said
from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.core import DIRECTOR
from grimoire.core.authority import Delegation, Revocation
from grimoire.core.identity import InstanceRef, ScopeRef
from grimoire.play.drafts import Draft, TurnDraft
from grimoire.play.together import MAX_TOGETHER, Contribution
from grimoire.scene import (
    COMBINED_TURN_SCHEMA, TURN_SCHEMA, SceneRefused, TurnRecord, heard_for, table_lines, turn_from_payload,
    turn_schema, turn_to_payload,
)
from grimoire.store import ScopeRecord
from grimoire.textapi import TextApi
from grimoire.textapi.errors import ApiRefused
from grimoire.textapi.views import turn_view

ANYA_ASKS = '"Is the lighthouse still dark?"'
TAMSIN_ASKS = '"And who keeps its lamp now?"'


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


def two_players(table: Table, chat):
    """Anya to Ana, Tamsin attached and handed to Bob by the owner, Mara the director's: (anya, mara, tamsin)."""
    tamsin = attach_tamsin(table.store, table.owner, chat)
    table.runner.seat(chat, ANA)
    table.runner.seat_player(chat, table.owner, tamsin, BOB)
    scene = table.runner.open(chat)
    others = [actor for actor in scene.cast if actor not in (scene.persona, tamsin)]
    return scene.persona, (others[0] if others else None), tamsin


def both(table: Table, anya, tamsin, first: str = ANYA_ASKS, second: str = TAMSIN_ASKS):
    return [Contribution(table.player(ANA), anya, first), Contribution(table.player(BOB), tamsin, second)]


# -- the proving scenario --------------------------------------------------------------------------

def test_two_pcs_speak_the_cast_answers_once_and_each_post_keeps_its_author(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Dark, and nobody's.")]) as table:
        anya, mara, tamsin = two_players(table, chat)
        turn = table.runner.post_together(chat, both(table, anya, tamsin))
        assert [(post.actor, post.submitted_by, post.kind) for post in turn.posts] == [
            (anya, ANA, "speech"), (tamsin, BOB, "speech")]
        assert table.runner.open(chat).pending == turn and len(table.runner.open(chat).records) == 1
        take = table.runner.generate(chat, table.player(ANA))
        (line,) = take.lines  # one answer, by the NPC: Tamsin is a player character now, never voiced
        assert line.actor == mara and [call.actor_id for call in take.calls] == [mara.instance_id]
        (prompt,) = table.harness.prompts()
        assert prompt.index("Anya Rook: Is the lighthouse still dark?") < prompt.index(
            "Tamsin Hale: And who keeps its lamp now?")
        table.runner.keep(chat, table.player(BOB), 1)
        scene = table.runner.open(chat)
        assert scene.players == (anya, tamsin) or set(scene.players) == {anya, tamsin}
        assert scene.npcs == (mara,) and scene.controller(mara) == DIRECTOR  # no NPC promoted by the turn
        assert [(said.speaker, said.text) for said in table_lines(scene.records, scene.names)] == [
            ("Anya Rook", "Is the lighthouse still dark?"), ("Tamsin Hale", "And who keeps its lamp now?"),
            ("Mara Vell", "Dark, and nobody's.")]
    with Table(folder) as table:  # read back from disk, under the combined turn's own schema
        (record,) = table.runner.open(chat).records
        assert [post.submitted_by for post in record.posts] == [ANA, BOB]
        assert table.store.read(record.ref).record.schema == COMBINED_TURN_SCHEMA


def test_the_narrator_answers_two_player_characters_once(cards):
    folder, _, storm = cards
    with Table(folder, [say("Thunder rolls over the quay.")]) as table:
        anya, _, tamsin = two_players(table, storm)
        table.runner.post_together(storm, both(table, anya, tamsin, "*I shake the rain from my coat*",
                                               '"We should get inside."'))
        take = table.runner.generate(storm, table.player(BOB), director=True)
        (line,) = take.lines
        assert line.actor is None and set(line.audience) == {anya, tamsin}
        assert len(table.harness.prompts()) == 1  # one narration for both posts, nothing between them


# -- independent control ---------------------------------------------------------------------------

def test_nobody_posts_for_a_character_they_do_not_control_and_nothing_is_half_posted(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        anya, mara, tamsin = two_players(table, chat)
        ana, bob = table.player(ANA), table.player(BOB)
        refused("not_your_character", table.runner.post_together, chat,
                [Contribution(ana, anya, ANYA_ASKS), Contribution(ana, tamsin, TAMSIN_ASKS)])
        refused("not_your_character", table.runner.post_together, chat,
                [Contribution(bob, anya, ANYA_ASKS), Contribution(bob, tamsin, TAMSIN_ASKS)])
        refused("not_a_player_character", table.runner.post_together, chat,  # an NPC is never posted for
                [Contribution(ana, anya, ANYA_ASKS), Contribution(table.owner, mara, '"I am a player now."')])
        refused("invalid_field", table.runner.post_together, chat,
                [Contribution(ana, anya, ANYA_ASKS), Contribution(ana, anya, '"Twice."')])
        refused("no_content", table.runner.post_together, chat,
                [Contribution(ana, anya, ANYA_ASKS), Contribution(bob, tamsin, "   ")])
        stranger = InstanceRef(chat.campaign_id, "character", "nobody")
        refused("not_present", table.runner.post_together, chat,
                [Contribution(ana, anya, ANYA_ASKS), Contribution(bob, stranger, TAMSIN_ASKS)])
        refused("invalid_field", table.runner.post_together, chat, [])
        refused("invalid_field", table.runner.post_together, chat,
                [Contribution(ana, anya, ANYA_ASKS)] * (MAX_TOGETHER + 1))
        refused("not_authenticated", table.runner.post_together, chat,
                [Contribution(ana, anya, ANYA_ASKS),
                 Contribution(table.authority.handle_for(BOB, trusted=False), tamsin, TAMSIN_ASKS)])
        assert table.runner.open(chat).records == ()  # every refusal before any write
        assert table.runner.open(chat).controller(mara) == DIRECTOR


def test_an_unseated_character_is_not_a_player_character_until_the_owner_hands_it_over(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        tamsin = attach_tamsin(table.store, table.owner, chat)
        table.runner.seat(chat, ANA)
        anya = table.runner.open(chat).persona
        refused("not_at_table", table.runner.post_together, chat,  # Bob plays nobody here yet
                [Contribution(table.player(ANA), anya, ANYA_ASKS), Contribution(table.player(BOB), tamsin, TAMSIN_ASKS)])
        refused("not_a_player_character", table.runner.post_together, chat,
                [Contribution(table.player(ANA), anya, ANYA_ASKS), Contribution(table.owner, tamsin, TAMSIN_ASKS)])
        refused("not_owner", table.runner.seat_player, chat, table.player(ANA), tamsin, BOB)
        refused("persona", table.runner.seat_player, chat, table.owner, anya, BOB)
        refused("invalid_field", table.runner.seat_player, chat, table.owner, tamsin, "app:x")
        controls = table.runner.seat_player(chat, table.owner, tamsin, BOB)
        assert {(c.actor_id, c.controller_id) for c in controls} >= {(tamsin.instance_id, BOB)}
        history = table.runner.sovereignty.load(chat.campaign_id).control_history
        assert (history[-1].actor_id, history[-1].controller_id, history[-1].recorded_by) == (
            tamsin.instance_id, BOB, OWNER)
        refused("seated_elsewhere", table.runner.seat_player, chat, table.owner, tamsin, ANA)
        table.runner.seat_player(chat, table.owner, tamsin, DIRECTOR)  # handed back: an NPC again
        scene = table.runner.open(chat)
        assert scene.controller(tamsin) == DIRECTOR and tamsin in scene.npcs


def test_a_delegation_never_revives_when_its_player_gets_the_character_back(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        _, _, tamsin = two_players(table, chat)
        grant = Delegation(chat.campaign_id, "d-bob", tamsin.instance_id, BOB,
                           frozenset({"voluntary_action", "voluntary_speech"}), "campaign", "incidental_dialogue")
        table.runner.sovereignty.record(chat.campaign_id, grant, table.player(BOB))
        table.runner.seat_player(chat, table.owner, tamsin, DIRECTOR)
        refused("delegations_on_record", table.runner.seat_player, chat, table.owner, tamsin, BOB)
        table.runner.seat_player(chat, table.owner, tamsin, ANA)  # Bob's delegation never applies to Ana's hold
        table.runner.seat_player(chat, table.owner, tamsin, DIRECTOR)
        table.runner.sovereignty.record(chat.campaign_id, Revocation(chat.campaign_id, "d-bob", BOB), table.player(BOB))
        table.runner.seat_player(chat, table.owner, tamsin, BOB)
        assert table.runner.open(chat).controller(tamsin) == BOB


def test_a_character_whose_post_waits_on_an_answer_keeps_its_player(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Dark.")]) as table:
        anya, mara, tamsin = two_players(table, chat)
        table.runner.post_together(chat, both(table, anya, tamsin))
        refused("turn_open", table.runner.seat_player, chat, table.owner, tamsin, DIRECTOR)
        take = table.runner.generate(chat, table.player(BOB))
        assert [call.actor_id for call in take.calls] == [mara.instance_id]  # never Tamsin answering Bob's words
        table.runner.keep(chat, table.player(BOB), 1)
        table.runner.seat_player(chat, table.owner, tamsin, DIRECTOR)


# -- one turn, all or nothing ----------------------------------------------------------------------

def test_a_post_waiting_in_another_chat_of_the_campaign_keeps_its_player_too(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        anya, _, tamsin = two_players(table, chat)
        table.runner.post_together(chat, both(table, anya, tamsin))
        chats = ChatLibrary(table.store, table.owner, clock=Clock(), new_id=Tagged("dawn"), owner=OWNER)
        dawn = chats.start_chat(Launch("premise", premise="The quay at dawn"), attach=[(TAMSIN, "character")],
                                scope=ScopeRef(chat.campaign_id), reuse_actors={("character", TAMSIN.entity_key): tamsin}).commit()
        # control is the campaign's: handing Tamsin back through the other chat would leave Bob's post to the model
        refused("turn_open", table.runner.seat_player, dawn.chat, table.owner, tamsin, DIRECTOR)
        assert table.runner.open(chat).controller(tamsin) == BOB


def test_a_post_on_a_branch_a_fork_choice_set_aside_holds_nothing(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Dark.")]) as table:
        anya, _, tamsin = two_players(table, chat)
        table.runner.post_together(chat, both(table, anya, tamsin))
        table.runner.generate(chat, table.player(ANA))
        table.runner.keep(chat, table.player(ANA), 1)
        table.runner.post_together(chat, both(table, anya, tamsin, '"Then we light it."', '"With what?"'))
        # another device's turn 1 arrives, as a sync would bring it, and the owner goes on from it: this device's
        # turn 1 is set aside, and the open turn 2 after it, unmarked itself, is on no chat's line
        first = table.runner.open(chat).turns.turns[0].record
        theirs = TurnRecord(chat, 1, "post", first.post, device="0badcafe", also=first.also, passed_by=BOB)
        txn = table.store.transaction()
        txn.put(ScopeRecord(theirs.ref, turn_schema(theirs), turn_to_payload(theirs), owner=OWNER), None)
        txn.commit(table.owner)
        refused("turn_fork", table.runner.seat_player, chat, table.owner, tamsin, DIRECTOR)  # nothing until chosen
        table.runner.choose(chat, table.owner, theirs.turn_id)
        table.runner.seat_player(chat, table.owner, tamsin, DIRECTOR)
        assert table.runner.open(chat).controller(tamsin) == DIRECTOR


def test_a_retried_submission_returns_its_turn_and_nothing_else_is_posted_while_it_is_open(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        anya, _, tamsin = two_players(table, chat)
        turn = table.runner.post_together(chat, both(table, anya, tamsin))
        assert table.runner.post_together(chat, both(table, anya, tamsin)) == turn
        refused("turn_open", table.runner.post_together, chat, both(table, anya, tamsin, '"Something else."'))
        refused("turn_open", table.runner.post, chat, table.player(ANA), ANYA_ASKS)  # one post is not the turn
        assert len(table.runner.open(chat).records) == 1


def test_each_post_is_heard_by_its_own_audience(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        anya, mara, tamsin = two_players(table, chat)
        turn = table.runner.post_together(chat, [
            Contribution(table.player(ANA), anya, ANYA_ASKS),
            Contribution(table.player(BOB), tamsin, 'I whisper to Mara Vell: "The lamp is out"', "action")])
        whisper = turn.posts[1]
        assert whisper.channel == "whisper" and set(whisper.audience) == {tamsin, mara}
        scene = table.runner.open(chat)
        assert [said.text for said in heard_for(scene.records, anya, scene.names)] == ["Is the lighthouse still dark?"]
        assert [said.text for said in heard_for(scene.records, mara, scene.names)] == [
            "Is the lighthouse still dark?", "The lamp is out"]
        assert [said.text for said in table_lines(scene.records, scene.names)] == ["Is the lighthouse still dark?"]


def test_a_combined_turn_is_not_adjudicated_or_given_an_effect_in_this_slice(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Dark.")], effects=True) as table:
        anya, _, tamsin = two_players(table, chat)
        turn = table.runner.post_together(chat, both(table, anya, tamsin, "*I light a match*", "*I open the door*"))
        table.runner.generate(chat, table.player(ANA))
        table.runner.keep(chat, table.player(ANA), 1)
        refused("combined_turn", table.runner.propose_effect, chat, table.owner, turn.turn_id, "The door opens.")


def test_the_last_time_recap_quotes_every_open_post_of_a_combined_turn(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        anya, mara, tamsin = two_players(table, chat)
        table.runner.post_together(chat, [
            Contribution(table.player(ANA), anya, ANYA_ASKS),
            Contribution(table.player(BOB), tamsin, 'I whisper to Mara Vell: "The lamp is out"', "action")])
        table.runner.pass_turn(chat, table.player(ANA))
        scene = table.runner.open(chat)
        view = turn_view(scene.turns.turns[-1], scene.names)
    assert [(line["speaker"], line["text"], line["you"]) for line in said([view], str(anya))] == [
        ("Anya Rook", "Is the lighthouse still dark?", True)]  # the whisper is the table's to keep out
    public = {**view, "posts": [view["posts"][0], {**view["posts"][1], "private": False}]}
    assert [line["speaker"] for line in said([public], str(anya))] == ["Anya Rook", "Tamsin Hale"]


# -- the turn file ---------------------------------------------------------------------------------

def test_a_combined_turn_reads_only_under_its_own_schema(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        anya, _, tamsin = two_players(table, chat)
        turn = table.runner.post_together(chat, both(table, anya, tamsin))
    payload = turn_to_payload(turn)
    assert turn_from_payload(turn.ref, COMBINED_TURN_SCHEMA, payload) == turn
    for schema, data in ((TURN_SCHEMA, payload), (COMBINED_TURN_SCHEMA, {k: v for k, v in payload.items() if k != "also"})):
        with pytest.raises(SceneRefused) as error:
            turn_from_payload(turn.ref, schema, data)
        assert error.value.reason == "invalid_field"
    single = TurnRecord(turn.chat, turn.number, "post", turn.post, device=turn.device)
    assert "also" not in turn_to_payload(single) and turn_from_payload(turn.ref, TURN_SCHEMA, turn_to_payload(single))
    with pytest.raises(SceneRefused):  # one post per character, and further posts only after a first
        TurnRecord(turn.chat, turn.number, "post", turn.post, device=turn.device, also=(turn.post,))
    with pytest.raises(SceneRefused):
        TurnRecord(turn.chat, turn.number, "prompt", None, device=turn.device, also=turn.also)


# -- drafts ----------------------------------------------------------------------------------------

def test_drafts_are_corrected_in_place_reordered_and_each_is_its_own_players(cards):
    _, chat, _ = cards
    anya, tamsin = InstanceRef(chat.campaign_id, "character", "a"), InstanceRef(chat.campaign_id, "character", "t")
    draft = TurnDraft(chat).put(Draft(anya, ANA, '"First."')).put(Draft(tamsin, BOB, '"Second."'))
    draft = draft.put(Draft(anya, ANA, '"First, corrected."'))
    assert [(d.actor, d.text) for d in draft.drafts] == [(anya, '"First, corrected."'), (tamsin, '"Second."')]
    assert [d.actor for d in draft.move(tamsin, BOB, 0).drafts] == [tamsin, anya]
    assert [d.actor for d in draft.put(Draft(tamsin, BOB, '"Me first."'), at=0).drafts] == [tamsin, anya]
    assert draft.drop(anya, ANA).drafts == (Draft(tamsin, BOB, '"Second."'),)
    for reason, call in (("not_your_draft", lambda: draft.put(Draft(anya, BOB, '"Mine now."'))),
                         ("not_your_draft", lambda: draft.move(anya, BOB, 1)),
                         ("not_your_draft", lambda: draft.drop(tamsin, ANA)),
                         ("no_draft", lambda: draft.drop(InstanceRef(chat.campaign_id, "character", "x"), ANA)),
                         ("invalid_field", lambda: draft.move(anya, ANA, 2)),
                         ("invalid_field", lambda: Draft(anya, ANA, '"x"', "director")),
                         ("invalid_field", lambda: Draft(anya, "app:x", '"x"')),
                         ("no_content", lambda: Draft(anya, ANA, " "))):
        refused(reason, call)
    many = TurnDraft(chat, tuple(Draft(InstanceRef(chat.campaign_id, "character", f"c{n}"), ANA, '"x"')
                                 for n in range(MAX_TOGETHER)))
    refused("too_many_drafts", many.put, Draft(InstanceRef(chat.campaign_id, "character", "more"), ANA, '"x"'))


# -- the text API's local table --------------------------------------------------------------------

class Device:
    """The owner's text API over ``table``, started with Bob as a second player at the same device."""

    def __init__(self, table: Table) -> None:
        self.api = TextApi(table.store, table.authority, owner=OWNER, local_players=[BOB],
                           runtime=table.harness.runtime, policy=POLICY)

    def __call__(self, op: str, **args):
        return self.api.call(op, args)

    def refused(self, reason: str, op: str, **args) -> None:
        with pytest.raises(ApiRefused) as error:
            self(op, **args)
        assert error.value.reason == reason, error.value


def opened(device: Device, chat: str) -> dict:
    """The chat continued, with the card's greeting kept if one is open, so play can go on."""
    scene = device("continue", chat=chat)
    if scene["state"]["open_turn"] is not None:
        scene = device("keep", chat=chat, take=1, turn=scene["state"]["open_turn"])
    return scene


def test_only_players_the_app_names_at_start_join_the_local_table(cards):
    folder, _, _ = cards
    with Table(folder) as table:
        for players in (["app:x"], [OWNER], ["not a principal"]):
            with pytest.raises(ValueError):
                TextApi(table.store, table.authority, owner=OWNER, local_players=players)


def test_two_players_at_one_device_draft_and_submit_one_turn_through_the_text_api(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Dark, and nobody's.")]) as table:
        tamsin = str(attach_tamsin(table.store, table.owner, chat))
        device, ref = Device(table), str(chat)
        anya = opened(device, ref)["persona"]["actor"]
        before = len(table.harness.prompts())
        device.refused("not_at_this_table", "seat", chat=ref, actor=tamsin, player="human:mallory")
        seated = device("seat", chat=ref, actor=tamsin, player=BOB)
        assert {(item["actor"], item["role"], item["controller"]) for item in seated["cast"]} >= {
            (tamsin, "player", BOB), (anya, "player", OWNER)}
        device("draft", chat=ref, actor=anya, text='"Is it dark?"')
        device("draft", chat=ref, actor=tamsin, by=BOB, text=TAMSIN_ASKS)
        device("draft", chat=ref, actor=anya, text=ANYA_ASKS)  # corrected in place
        drafted = device("draft", chat=ref, actor=tamsin, by=BOB, at=0)  # moved first
        assert [(d["actor"], d["by"], d["text"]) for d in drafted["drafts"]] == [
            (tamsin, BOB, TAMSIN_ASKS), (anya, OWNER, ANYA_ASKS)]
        device.refused("not_at_this_table", "draft", chat=ref, actor=anya, by="human:mallory", text='"Hello."')
        device.refused("not_your_draft", "drop_draft", chat=ref, actor=anya, by=BOB)
        played = device("submit", chat=ref)
        assert played["drafts"] == []
        turn = played["turns"][-1]
        assert [(post["actor"], post["by"], post["speaker"]) for post in turn["posts"]] == [
            (tamsin, BOB, "Tamsin Hale"), (anya, OWNER, "Anya Rook")]
        assert turn["post"] == turn["posts"][0]
        device.refused("no_drafts", "submit", chat=ref)
        for n in range(70):  # a draft refused for a chat that does not exist holds nothing against the cap
            device.refused("no_chat", "draft", chat=str(chat).replace(chat.chat_id, f"nochat{n}"), actor=anya,
                           text='"Hi."')
        assert device("draft", chat=ref, actor=anya, text='"Hi."')["drafts"]
        answered = device("generate", chat=ref)
        assert len(answered["turns"][-1]["offered"]) == 1 and len(table.harness.prompts()) == before + 1


def test_one_player_drafts_for_two_player_characters_and_each_post_has_its_own_speaker(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        tamsin = str(attach_tamsin(table.store, table.owner, chat))
        device, ref = Device(table), str(chat)
        anya = opened(device, ref)["persona"]["actor"]
        device("seat", chat=ref, actor=tamsin, player=OWNER)
        device("draft", chat=ref, actor=anya, text=ANYA_ASKS)
        device("draft", chat=ref, actor=tamsin, text="*I check the lamp room door*")
        turn = device("submit", chat=ref)["turns"][-1]
        assert [(post["speaker"], post["by"], post["kind"]) for post in turn["posts"]] == [
            ("Anya Rook", OWNER, "speech"), ("Tamsin Hale", OWNER, "action")]
        cast = {item["actor"]: item for item in device("open", chat=ref)["cast"]}
        assert cast[tamsin]["controller"] == OWNER and cast[anya]["controller"] == OWNER


def test_a_refused_submission_keeps_the_drafts_to_correct(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        tamsin = str(attach_tamsin(table.store, table.owner, chat))
        device, ref = Device(table), str(chat)
        anya = opened(device, ref)["persona"]["actor"]
        turns = len(device("open", chat=ref)["turns"])
        device("draft", chat=ref, actor=anya, text=ANYA_ASKS)
        device("draft", chat=ref, actor=tamsin, by=BOB, text=TAMSIN_ASKS)  # not handed to Bob yet
        device.refused("not_at_table", "submit", chat=ref)
        kept = device("open", chat=ref)
        assert len(kept["drafts"]) == 2 and len(kept["turns"]) == turns
        device("seat", chat=ref, actor=tamsin, player=BOB)
        played = device("submit", chat=ref)
        assert [post["by"] for post in played["turns"][-1]["posts"]] == [OWNER, BOB]
