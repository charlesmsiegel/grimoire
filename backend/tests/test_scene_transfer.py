"""084: copy a scene into another campaign, transcript only, with every actor's identity reviewed.

Over the synthetic card library (``scene_fixtures``): Mara Vell's card chat (Anya Rook as the player's persona) is
the origin, and the scenario chat's campaign, which has its own Anya, is the target. The target also holds an
alternate portrayal of Mara (another entity with Mara's name, linked as ``alternate_portrayal``), which a copy may
never treat as Mara. Every model call is a scripted fake; nothing is billed.
"""

from __future__ import annotations

import json
import shutil

import pytest

from generation_fixtures import say
from scene_fixtures import ANA, ANYA, LABEL_POLICY, MARA_VELL, TAMSIN, Table, Tagged, attach_tamsin, card_library
from store_helpers import OWNER

from chat_fixtures import Clock
from grimoire.archaeologystore import StImporter
from grimoire.chatstore import ChatLibrary
from grimoire.core import DIRECTOR
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.effects import Op
from grimoire.scenetransfer import LEAVE, NEW, SceneTransfer, TransferRefused
from grimoire.store import LinkSpec, RecordEnvelope, SourceManifest, StaleWrite
from grimoire.testing.fileops import snapshot
from grimoire.transcripts import Post

UNOPPOSED = json.dumps({"factors": [{"fact": "f1", "bearing": "favours"}], "stakes": []})
GREETING = "You came back. I didn't think you would."  # the card's own first message
KEPT = "Ledgers drown, and debts float to the top."
STRUCK = "A line nobody kept."
WHISPER = "The key is under the third crate."
NEWS = '"Any news from the quay?"'
NEWS_SAID = "Any news from the quay?"
LATER = "Then we count the crates again."
MET = "Anya and Mara have met."
NOW = 2**62
TALES = "catalog:harbor-tales"
ALTERNATE = SourceRef(TALES, "character", "mara-of-the-lamps")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return card_library(tmp_path_factory.mktemp("built"))


@pytest.fixture
def cards(built, tmp_path):
    folder, card_chat, storm = built
    copy = tmp_path / "cards"
    shutil.copytree(folder, copy)
    return copy, card_chat, storm


def refused(reason: str, call, *args, **kwargs) -> TransferRefused:
    with pytest.raises(TransferRefused) as error:
        call(*args, **kwargs)
    assert error.value.reason == reason, error.value
    return error.value


def transfer(at: Table, tag: str = "x") -> SceneTransfer:
    return SceneTransfer(at.store, at.owner, clock=Clock(), new_id=Tagged(tag), owner=OWNER)


def texts(plan) -> list[str]:
    return [post.text for page in plan.pages for post in page.posts]


def persona(at: Table, chat) -> InstanceRef:
    record, _ = ChatLibrary(at.store, at.owner, clock=Clock(), new_id=Tagged("r")).read_chat(chat)
    return record.persona.actor


def play_origin(folder, chat) -> None:
    """The card's greeting kept, Anya's action decided with an accepted effect and a kept reply, a whisper to Mara, and a
    turn whose only line was struck."""
    with Table(folder, [say(KEPT), say(STRUCK)], policy=LABEL_POLICY, labels=[say(UNOPPOSED)],
               adjudicate=True, effects=True) as at:
        runner, ana = at.runner, at.player()
        runner.seat(chat, ANA)
        runner.begin(chat, ana)
        runner.keep(chat, ana, 1)
        turn = runner.post(chat, ana, "*I wave to Mara*").turn_id
        runner.label(chat, ana)
        assert runner.confirm(chat, ana) is not None
        runner.generate(chat, ana)
        runner.keep(chat, ana, 1)
        scene = runner.open(chat)
        (mara,) = [actor for actor in scene.cast if actor != scene.persona]
        effect = runner.propose_effect(chat, ana, turn, MET, [Op("begin", "has_met", scene.persona, mara,
                                                                   scene.persona, turn)])
        runner.endorse_effect(chat, ana, turn, 1, effect.proposal.fingerprint)
        assert runner.approve_effect(chat, at.owner, turn, 1).state == "accepted"
        runner.post(chat, ana, f'I whisper to Mara Vell, "{WHISPER}"')
        runner.pass_turn(chat, ana)
        runner.post(chat, ana, NEWS)
        (line,) = runner.generate(chat, ana).lines
        runner.keep(chat, ana, 1)
        runner.request(chat, ana, "skip", [line.line_id])


def seed_alternate(at: Table, campaign_id: str) -> InstanceRef:
    """Another portrayal of Mara, named like her, with an actor in the target campaign."""
    alternate = InstanceRef(campaign_id, "character", "mara-of-the-lamps-1")
    txn = at.store.transaction()
    txn.put(SourceManifest(TALES, OWNER, "Harbor tales", links=(
        LinkSpec(str(ALTERNATE.at("r1")), "alternate_portrayal", str(MARA_VELL)),)), None)
    txn.put(RecordEnvelope(ALTERNATE.at("r1"), {"bio": "a lamplighter"}, name="Mara Vell"), None)
    txn.put(RecordEnvelope(alternate, {}, derived_from=str(ALTERNATE), base=str(ALTERNATE.at("r1"))), None)
    txn.commit(at.owner)
    return alternate


# -- the proof ------------------------------------------------------------------------------------------

def test_a_copied_scene_gets_a_new_chat_keeps_the_portrayal_distinct_and_leaves_the_origin_unchanged(cards):
    folder, chat, storm = cards
    play_origin(folder, chat)
    with Table(folder, effects=True) as at:
        alternate = seed_alternate(at, storm.campaign_id)
        anya, target_anya = persona(at, chat), persona(at, storm)
        scan = transfer(at).scan(chat, storm.scope)
        mara = next(member for member in scan.cast if member.role == "character")
        assert (scan.member(anya).candidates, scan.member(anya).namesakes) == ((target_anya,), ())
        assert (mara.name, mara.candidates, mara.namesakes) == ("Mara Vell", (), (alternate,))  # a name is no match
        assert scan.history_problems == () and scan.history["turns"] == 4 and scan.provider_calls == 0

        refused("unreviewed_actor", transfer(at).plan, chat, storm.scope, cast={anya: target_anya})
        refused("different_individual", transfer(at).plan, chat, storm.scope,
                cast={anya: target_anya, mara.actor: alternate})
        before = snapshot(folder / "campaigns" / chat.campaign_id)
        plan = transfer(at).plan(chat, storm.scope, cast={anya: target_anya, mara.actor: NEW}, label="Copied")
        new_mara = plan.mapping[mara.actor]
        assert plan.chat.campaign_id == storm.campaign_id and plan.chat not in (chat, storm)  # a new chat
        assert new_mara not in (alternate, mara.actor) and plan.mapping[anya] == target_anya
        assert texts(plan) == [GREETING, "*I wave to Mara*", KEPT, NEWS_SAID]  # no whisper, no struck line
        assert [item.reason for item in plan.omitted] == ["private"]
        (effect,) = [item for item in plan.left if item.kind == "effect"]
        assert effect.detail is not None and any(item.kind == "ruling" for item in plan.left)
        speakers = {post.speaker.actor for page in plan.pages for post in page.posts}
        assert speakers == {target_anya, new_mara}
        plan.commit()
        assert snapshot(folder / "campaigns" / chat.campaign_id) == before  # the origin is untouched
        assert at.effects.continuity(storm.campaign_id, as_of=NOW, requested_by=DIRECTOR).events == ()
        assert len(at.effects.continuity(chat.campaign_id, as_of=NOW, requested_by=DIRECTOR).events) == 1

    with Table(folder, [say(LATER)]) as at:  # the target plays on from the copied history
        runner, ana = at.runner, at.player()
        runner.seat(plan.chat, ANA)
        scene = runner.open(plan.chat)
        assert scene.persona == target_anya and new_mara in scene.npcs and alternate not in scene.cast
        runner.post(plan.chat, ana, '"Do you remember the lamps?"')
        runner.generate(plan.chat, ana)
        (prompt,) = at.harness.prompts()
        assert KEPT in prompt and GREETING in prompt and "I wave to Mara" in prompt
        assert WHISPER not in prompt and STRUCK not in prompt and MET not in prompt


# -- what a copy refuses ----------------------------------------------------------------------------------

def test_every_actor_needs_a_decision_and_a_reuse_must_be_the_same_individual(cards):
    folder, chat, storm = cards
    with Table(folder) as at:
        anya, target_anya = persona(at, chat), persona(at, storm)
        scan = transfer(at).scan(chat, storm.scope)
        mara = next(member.actor for member in scan.cast if member.role == "character")
        refused("same_campaign", transfer(at).scan, chat, chat.scope)
        refused("unknown_actor", transfer(at).plan, chat, storm.scope,
                cast={anya: target_anya, mara: NEW, InstanceRef(chat.campaign_id, "character", "nobody"): NEW})
        refused("actor_mismatch", transfer(at).plan, chat, storm.scope, cast={anya: NEW, mara: target_anya})
        refused("foreign_actor", transfer(at).plan, chat, storm.scope, cast={anya: anya, mara: NEW})
        refused("invalid_field", transfer(at).plan, chat, storm.scope, cast={anya: "keep", mara: NEW})
        assert transfer(at).plan(chat, storm.scope, cast={anya: LEAVE, mara: LEAVE}).pages == ()


def test_two_personas_cannot_both_be_carried_into_one_chat(cards):
    folder, chat, storm = cards
    with Table(folder) as at:
        chats = ChatLibrary(at.store, at.owner, clock=Clock(), new_id=Tagged("p"), owner=OWNER)
        anya = persona(at, chat)
        _, etag = chats.read_chat(chat)
        chats.set_persona(chat, TAMSIN, expected=etag).commit()
        scan = transfer(at).scan(chat, storm.scope)
        tamsin = persona(at, chat)
        mara = next(member.actor for member in scan.cast if member.role == "character")
        assert not scan.member(anya).active and scan.member(tamsin).active
        refused("second_persona", transfer(at).plan, chat, storm.scope, cast={anya: NEW, tamsin: NEW, mara: NEW})
        plan = transfer(at).plan(chat, storm.scope, cast={anya: LEAVE, tamsin: NEW, mara: NEW})
        assert plan.mapping[anya] is None and plan.mapping[tamsin] is not None


def test_an_origin_that_changes_after_the_plan_is_not_copied(cards):
    folder, chat, storm = cards
    with Table(folder) as at:
        anya, target_anya = persona(at, chat), persona(at, storm)
        mara = next(member.actor for member in transfer(at).scan(chat, storm.scope).cast if member.role == "character")
        plan = transfer(at).plan(chat, storm.scope, cast={anya: target_anya, mara: NEW})
        at.runner.seat(chat, ANA)
        at.runner.begin(chat, at.player())  # the card's greeting, proposed: the turn written after the plan
        with pytest.raises(StaleWrite):
            plan.commit()
        at.runner.keep(chat, at.player(), 1)
        assert texts(transfer(at).plan(chat, storm.scope, cast={anya: target_anya, mara: NEW})) == [GREETING]


def test_a_history_that_cannot_all_be_read_is_not_copied(cards):
    folder, chat, storm = cards
    page = folder / "campaigns" / chat.campaign_id / "transcripts" / chat.chat_id / "0000000001.json"
    page.parent.mkdir(parents=True)
    page.write_text("{not json", encoding="utf-8")
    with Table(folder) as at:
        anya, target_anya = persona(at, chat), persona(at, storm)
        scan = transfer(at).scan(chat, storm.scope)
        assert scan.history_problems and scan.history_problems[0][0] == "imported_history_unreadable"
        mara = next(member.actor for member in scan.cast if member.role == "character")
        refused("history_incomplete", transfer(at).plan, chat, storm.scope, cast={anya: target_anya, mara: NEW})


# -- imported pages -----------------------------------------------------------------------------------

LAMPS = "The lamps are out tonight."
UNCHOSEN = "A swipe nobody chose."
HIDDEN = "A note kept out of the chat."
NARRATED = "Fog rolls over the quay."
TIDE = "Then we wait for the tide."


def st_export() -> bytes:
    rows = [{"user_name": "Anya Rook", "character_name": "Mara Vell", "create_date": "2026-01-02@10h00m00s"},
            {"name": "Mara Vell", "is_user": False, "mes": LAMPS, "swipes": [UNCHOSEN, LAMPS], "swipe_id": 1,
             "original_avatar": "mara.png"},
            {"name": "Anya Rook", "is_user": True, "is_system": True, "mes": HIDDEN,
             "force_avatar": "User Avatars/anya.png"},
            {"name": "Narrator", "is_user": False, "mes": NARRATED, "extra": {"type": "narrator"}},
            {"name": "Anya Rook", "is_user": True, "mes": TIDE, "force_avatar": "User Avatars/anya.png"}]
    return ("\n".join(json.dumps(row) for row in rows) + "\n").encode("utf-8")


def import_st(at: Table):
    importer = StImporter(at.store, at.owner, clock=Clock(), owner=OWNER)
    data = st_export()
    avatars = {speaker.avatar: speaker.id for speaker in importer.scan(data).speakers}
    plan = importer.plan(data, speakers={avatars["mara.png"]: MARA_VELL, avatars["User Avatars/anya.png"]: ANYA})
    plan.commit()
    return plan.chat


def test_imported_pages_travel_whole_with_their_speakers_mapped(cards):
    folder, _chat, storm = cards
    with Table(folder) as at:
        imported = import_st(at)
        anya, target_anya = persona(at, imported), persona(at, storm)
        mara = next(member.actor for member in transfer(at).scan(imported, storm.scope).cast
                    if member.role == "character")
        plan = transfer(at).plan(imported, storm.scope, cast={anya: target_anya, mara: NEW})
        posts: list[Post] = [post for page in plan.pages for post in page.posts]
        assert [post.text for post in posts] == [LAMPS, HIDDEN, NARRATED, TIDE]
        assert [variant.text for variant in posts[0].unchosen] == [UNCHOSEN]  # every variant, still unchosen
        assert posts[1].hidden and posts[2].speaker.role == "narrator"
        assert [post.speaker.actor for post in posts] == [plan.mapping[mara], target_anya, None, target_anya]
        assert posts[0].source["copied_from"]["chat"] == str(imported) and "source" in posts[0].source
        assert plan.pages[0].origin["from"] == str(imported) and plan.pages[0].origin["pages"] == 1
        plan.commit()
    with Table(folder) as at:
        scene = at.runner.open(plan.chat)
        assert scene.imported is not None
        assert [line.text for line in scene.imported.heard] == [LAMPS, TIDE]  # the hidden note is still nobody's


def test_a_late_joiner_carried_over_gets_no_post_it_never_heard(cards):
    folder, _chat, storm = cards
    with Table(folder) as at:
        imported = import_st(at)
        tamsin = attach_tamsin(at.store, at.owner, imported)
        anya, target_anya = persona(at, imported), persona(at, storm)
        mara = next(member.actor for member in transfer(at).scan(imported, storm.scope).cast
                    if member.role == "character" and member.actor != tamsin)
        plan = transfer(at).plan(imported, storm.scope, cast={anya: target_anya, mara: NEW, tamsin: NEW})
        assert texts(plan) == [HIDDEN, NARRATED]  # heard by no actor: the table's only, and still so
        assert {item.reason for item in plan.omitted} == {"unheard"}
        assert all(item.missed == (tamsin,) for item in plan.omitted)
        left_out = transfer(at).plan(imported, storm.scope, cast={anya: target_anya, mara: NEW, tamsin: LEAVE})
        assert texts(left_out) == [LAMPS, HIDDEN, NARRATED, TIDE] and left_out.omitted == ()
