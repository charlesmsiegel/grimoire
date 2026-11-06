"""035 M2: a chat's imported history is the history before its first played turn.

026's V0.2.1 scene chats and 036's SillyTavern chats carry their text as immutable ``transcripts/`` pages
that record who wrote each post, never who heard it. The runner decides that by one rule: the actors the
chat was imported with heard every shown post, and an actor attached later heard none of it. A narrator
post is the table's, never an NPC's; a ``system`` notice, a hidden post and an unchosen swipe are nobody's.
Pages that cannot all be read are heard by nobody, and ``open`` says so. Synthetic libraries; every model
call is a scripted fake.
"""

from __future__ import annotations

import json
import shutil

import pytest

from scene_fixtures import (
    ANA, ANYA, MARA_VELL, MARA_VOICE, STORM, V1_CHAT, Table, Tagged, attach_tamsin, card_library, say, v1_library,
)
from store_helpers import OWNER

from chat_fixtures import Clock
from grimoire.archaeologystore import StImporter
from grimoire.chatstore import ChatLibrary
from grimoire.core.identity import InstanceRef, TranscriptRef
from grimoire.scene import NARRATOR, SceneRefused, heard_for, imported_history, table_lines
from grimoire.transcripts import Post, Speaker, TranscriptPage, Variant

V1_IMPORTED = ("I knock on the door.", "The door opens.", "Someone waits inside.")
LAMPS = "The lamps are out tonight."
TIDE = "Then we wait for the tide."
UNCHOSEN = "A swipe nobody chose."
HIDDEN = "A note kept out of the chat."
NARRATED = "Fog rolls over the quay."


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return card_library(tmp_path_factory.mktemp("built"))


@pytest.fixture
def cards(built, tmp_path):
    folder, card_chat, storm = built
    copy = tmp_path / "cards"
    shutil.copytree(folder, copy)
    return copy, card_chat, storm


def problems(scene, code: str) -> list[str]:
    return [detail for found, detail in scene.problems if found == code]


def pages_of(folder, chat):
    return sorted(folder.glob(f"campaigns/{chat.campaign_id}/transcripts/{chat.chat_id}/*.json"))


def st_export() -> bytes:
    rows = [{"user_name": "Anya Rook", "character_name": "Mara Vell", "create_date": "2026-01-02@10h00m00s"},
            {"name": "Mara Vell", "is_user": False, "mes": LAMPS, "swipes": [UNCHOSEN, LAMPS], "swipe_id": 1,
             "original_avatar": "mara.png"},
            {"name": "Anya Rook", "is_user": True, "is_system": True, "mes": HIDDEN,
             "force_avatar": "User Avatars/anya.png"},
            {"name": "Narrator", "is_user": False, "mes": NARRATED, "extra": {"type": "narrator"}},
            {"name": "Anya Rook", "is_user": True, "mes": TIDE, "force_avatar": "User Avatars/anya.png"}]
    return ("\n".join(json.dumps(row) for row in rows) + "\n").encode("utf-8")


def import_st(table):
    importer = StImporter(table.store, table.owner, clock=Clock(), owner=OWNER)
    data = st_export()
    avatars = {speaker.avatar: speaker.id for speaker in importer.scan(data).speakers}
    plan = importer.plan(data, speakers={avatars["mara.png"]: MARA_VELL, avatars["User Avatars/anya.png"]: ANYA},
                         name="harbor.jsonl")
    plan.commit()
    return plan.chat


def add_v1_post(folder, speaker: dict, text: str) -> None:
    """A post appended to the V1 chat's page, as 026 writes them (V0.2.1's narrator has no label)."""
    (page,) = pages_of(folder, V1_CHAT)
    envelope = json.loads(page.read_text(encoding="utf-8"))
    posts = envelope["payload"]["posts"]
    posts.append({"id": f"p{len(posts) + 1:06d}", "speaker": speaker, "variants": [{"text": text}], "active": 0,
                  "audience": "unknown"})
    page.write_text(json.dumps(envelope), encoding="utf-8")


def test_a_migrated_v1_scene_chat_plays_on_from_its_imported_posts(tmp_path):
    folder = v1_library(tmp_path)
    assert len(pages_of(folder, V1_CHAT)) == 1
    with Table(folder, [say("Counted."), say("The lamps.")], voices=MARA_VOICE) as table:
        runner, ana = table.runner, table.player()
        runner.seat(V1_CHAT, ANA)
        scene = runner.open(V1_CHAT)
        assert not [code for code, _ in scene.problems if code.startswith("imported_history")]
        assert scene.imported is not None and scene.imported.posts == 3
        assert scene.records == ()  # imported text is not a played turn
        with pytest.raises(SceneRefused) as refused:
            runner.begin(V1_CHAT, ana)  # the scene already opened in V1: no second opening
        assert refused.value.reason == "already_begun"
        runner.post(V1_CHAT, ana, '"Where were we?"')
        runner.generate(V1_CHAT, ana)
        prompts = table.harness.prompts()
        assert len(prompts) == 2  # Mara and Seraphine, each in the V1 scene's cast
        for prompt in prompts:
            spots = [prompt.index(text) for text in V1_IMPORTED]
            assert spots == sorted(spots)  # in the order they were written
            assert spots[-1] < prompt.index("Where were we?")  # before the first played turn


def test_a_v1_chat_hears_its_history_after_a_restart(tmp_path):
    folder = v1_library(tmp_path)
    with Table(folder, voices=MARA_VOICE) as table:
        table.runner.seat(V1_CHAT, ANA)
        table.runner.post(V1_CHAT, table.player(), '"Where were we?"')
    with Table(folder, [say("Counted."), say("The lamps.")], voices=MARA_VOICE) as table:
        table.runner.generate(V1_CHAT, table.player())
        assert all(all(text in prompt for text in V1_IMPORTED) for prompt in table.harness.prompts())


def test_a_sillytavern_chat_is_heard_by_its_imported_cast_and_not_by_a_card_attached_later(cards):
    folder, card_chat, _ = cards
    with Table(folder, [say("Wait, then."), say("Who is she?"), say("I see the quay.")]) as table:
        runner, ana = table.runner, table.player()
        chat = import_st(table)
        runner.seat(chat, ANA)
        scene = runner.open(chat)
        assert scene.imported is not None and scene.imported.posts == 4
        assert runner.open(card_chat).imported is None  # a chat started here has no imported history
        runner.generate(chat, ana)
        (mara,) = table.harness.prompts()
        assert mara.index(LAMPS) < mara.index(TIDE)
        assert UNCHOSEN not in mara and HIDDEN not in mara  # an unchosen swipe and a hidden post: nobody's
        assert NARRATED not in mara  # narration is the table's, never an NPC's
        runner.keep(chat, ana, 1)
        tamsin = attach_tamsin(table.store, table.owner, chat)
        runner.seat(chat, ANA)
        runner.post(chat, ana, '"Someone new is on the quay."')
        runner.generate(chat, ana, speakers=[tamsin])
        late = table.harness.prompts()[-1]
        assert "Someone new is on the quay." in late
        assert not [text for text in (LAMPS, TIDE, UNCHOSEN, HIDDEN, NARRATED) if text in late]
        assert "Wait, then." not in late  # nor what was said before she arrived


def test_v1_narration_is_the_tables_and_never_an_npcs(tmp_path):
    folder = v1_library(tmp_path)
    secret = "Unseen by Mara, Seraphine pockets the key."
    add_v1_post(folder, {"role": "assistant"}, secret)  # V0.2.1's **Grimoire:** narrator
    with Table(folder, [say("Counted."), say("The lamps.")], voices=MARA_VOICE) as table:
        runner, ana = table.runner, table.player()
        runner.seat(V1_CHAT, ANA)
        scene = runner.open(V1_CHAT)
        assert scene.imported.posts == 4
        assert [line.speaker for line in scene.imported.table if line.text == secret] == [NARRATOR]
        assert secret not in [line.text for line in scene.imported.heard]
        runner.post(V1_CHAT, ana, '"Where were we?"')
        runner.generate(V1_CHAT, ana)
        assert all(secret not in prompt and V1_IMPORTED[1] in prompt for prompt in table.harness.prompts())


def test_the_director_sees_the_imported_table_view(cards):
    folder, _, _ = cards
    with Table(folder, [say("Rain hammers the quay.")]) as table:
        runner, ana = table.runner, table.player()
        chat = import_st(table)
        chats = ChatLibrary(table.store, table.owner, clock=Clock(), new_id=Tagged("s"), owner=OWNER)
        _, etag = chats.read_chat(chat)
        chats.attach_source(chat, STORM, "scenario", expected=etag).commit()
        runner.seat(chat, ANA)
        runner.generate(chat, ana, speakers=[], director=True)
        (prompt,) = table.harness.prompts()
        assert prompt.index(LAMPS) < prompt.index(NARRATED) < prompt.index(TIDE)
        assert UNCHOSEN not in prompt and HIDDEN not in prompt


def test_a_sillytavern_import_missing_its_last_page_is_heard_by_nobody(cards):
    folder, _, _ = cards
    with Table(folder) as table:
        chat = import_st(table)
    (page,) = pages_of(folder, chat)
    envelope = json.loads(page.read_text(encoding="utf-8"))
    envelope["payload"]["origin"]["pages"] = 2  # its import wrote two pages; the second is gone
    page.write_text(json.dumps(envelope), encoding="utf-8")
    with Table(folder) as table:
        scene = table.runner.open(chat)
        assert scene.imported is None
        assert any("missing_pages" in detail for detail in problems(scene, "imported_history_unreadable"))


def test_a_problem_in_the_campaigns_transcripts_folder_does_not_give_a_new_chat_history(cards):
    folder, card_chat, _ = cards
    stray = folder / "campaigns" / card_chat.campaign_id / "Transcripts" / "stray.txt"  # a look-alike folder
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("not a page", encoding="utf-8")
    with Table(folder) as table:
        scene = table.runner.open(card_chat)
        assert problems(scene, "transcripts") and not problems(scene, "imported_history_unreadable")
        table.runner.seat(card_chat, ANA)
        table.runner.begin(card_chat, table.player())  # a chat started here still opens


def test_damaged_imported_pages_are_heard_by_nobody_and_reported(tmp_path):
    folder = v1_library(tmp_path)
    (page,) = pages_of(folder, V1_CHAT)
    page.write_text("{not json", encoding="utf-8")
    with Table(folder, [say("Counted."), say("The lamps.")], voices=MARA_VOICE) as table:
        runner, ana = table.runner, table.player()
        runner.seat(V1_CHAT, ANA)
        scene = runner.open(V1_CHAT)
        assert scene.imported is None
        assert problems(scene, "imported_history_unreadable")
        with pytest.raises(SceneRefused) as refused:
            runner.begin(V1_CHAT, ana)  # the chat still has history, even unread: no second opening
        assert refused.value.reason == "already_begun"
        runner.post(V1_CHAT, ana, '"Where were we?"')
        runner.generate(V1_CHAT, ana)
        assert not [text for text in V1_IMPORTED if any(text in p for p in table.harness.prompts())]


def test_a_gap_in_the_pages_is_heard_by_nobody(tmp_path):
    folder = v1_library(tmp_path)
    (page,) = pages_of(folder, V1_CHAT)
    moved = page.with_name("0000000002.json")  # page 2 of the same chat, with page 1 gone
    moved.write_text(page.read_text(encoding="utf-8").replace("/0000000001", "/0000000002"), encoding="utf-8")
    page.unlink()
    with Table(folder, voices=MARA_VOICE) as table:
        scene = table.runner.open(V1_CHAT)
        assert scene.imported is None
        assert any("page_gap" in detail for detail in problems(scene, "imported_history_unreadable"))


# -- the rule itself (pure) -------------------------------------------------------------------------

MARA = InstanceRef("c", "character", "mara")
LATE = InstanceRef("c", "character", "late")
PC = InstanceRef("c", "character", "pc")


def page(*posts: Post, seq: int = 1) -> TranscriptPage:
    return TranscriptPage(TranscriptRef("c", "chat", seq), posts)


def post(n: int, role: str, text: str, *, actor=None, label=None, hidden=False, active=0) -> Post:
    return Post(f"p{n:06d}", Speaker(role, actor, label), (Variant(text),), active, hidden=hidden)


def test_who_hears_which_imported_post():
    pages = [page(post(1, "user", "Hello.", actor=PC, label="Pc"), post(2, "assistant", "Hm.", label="Mara"),
                  post(3, "narrator", "Rain."), post(4, "system", "Rolled a 6."),
                  post(5, "character", "No.", actor=MARA, hidden=True), post(6, "character", "Unshown.", active=None)),
             page(post(7, "character", "Fine.", actor=MARA, label="M"), seq=2)]
    history = imported_history(reversed(pages), (MARA, PC), {MARA: "Mara Vell"})
    assert history.posts == 7
    assert [(line.speaker, line.text) for line in history.heard] == [("Pc", "Hello."), ("Mara", "Hm."),
                                                                      ("Mara Vell", "Fine.")]
    assert [line.text for line in history.table] == ["Hello.", "Hm.", "Rain.", "Fine."]
    assert history.table[2].speaker == NARRATOR
    assert [line.text for line in heard_for((), MARA, {}, before=history)] == ["Hello.", "Hm.", "Fine."]
    assert heard_for((), LATE, {}, before=history) == ()  # not imported with the chat
    assert [line.text for line in table_lines((), {}, before=history)] == ["Hello.", "Hm.", "Rain.", "Fine."]
    assert [line.text for line in heard_for((), MARA, {}, before=history, limit=1)] == ["Fine."]  # one window
    tail = imported_history(pages, (MARA,), {}, limit=2)
    assert [line.text for line in tail.heard] == ["Hm.", "Fine."] and [line.text for line in tail.table] == [
        "Rain.", "Fine."]
    assert tail.posts == 7


def test_unnamed_and_unlabelled_imported_speakers():
    pages = [page(post(1, "user", "I knock."), post(2, "assistant", "The door opens."),
                  post(3, "character", "Who?", label="???"), post(4, "character", "Me.", label="🐉"))]
    history = imported_history(pages, (MARA,), {}, player="Winifred")
    assert [(line.speaker, line.text) for line in history.heard] == [("Winifred", "I knock."),
                                                                      ("Character", "Who?"), ("Character", "Me.")]
    assert [(line.speaker, line.text) for line in history.table][1] == (NARRATOR, "The door opens.")
    assert imported_history(pages, (MARA,), {}).heard[0].speaker == "Player"
