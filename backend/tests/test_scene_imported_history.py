"""035: a chat with imported history says that the runner did not read it.

026's V0.2.1 scene chats and 036's SillyTavern chats carry their text as immutable ``transcripts/`` pages.
Reading them as the history before turn 1 (and deciding who heard them) is 035's milestone 2; until then the
runner reads none of them, so no NPC heard any imported line (fail closed), and ``open`` reports
``imported_history_unread`` instead of playing on as if the chat began here. Synthetic libraries; every
model call is a scripted fake.
"""

from __future__ import annotations

import json
import shutil

import pytest

from scene_fixtures import ANA, ANYA, MARA_VELL, MARA_VOICE, V1_CHAT, Table, card_library, say, v1_library
from store_helpers import OWNER

from chat_fixtures import Clock
from grimoire.archaeologystore import StImporter


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return card_library(tmp_path_factory.mktemp("built"))


@pytest.fixture
def cards(built, tmp_path):
    folder, card_chat, storm = built
    copy = tmp_path / "cards"
    shutil.copytree(folder, copy)
    return copy, card_chat, storm


def flags(scene) -> list[str]:
    return [detail for code, detail in scene.problems if code == "imported_history_unread"]


def test_a_migrated_v1_scene_chat_reports_its_unread_pages_and_npcs_heard_none_of_them(tmp_path):
    folder = v1_library(tmp_path)
    pages = sorted(folder.glob(f"campaigns/{V1_CHAT.campaign_id}/transcripts/{V1_CHAT.chat_id}/*.json"))
    assert len(pages) == 1
    imported = [post["variants"][post["active"]]["text"]
                for post in json.loads(pages[0].read_text(encoding="utf-8"))["payload"]["posts"]]
    assert imported
    with Table(folder, [say("Counted."), say("The lamps.")], voices=MARA_VOICE) as table:
        runner, ana = table.runner, table.player()
        runner.seat(V1_CHAT, ANA)
        assert flags(runner.open(V1_CHAT)) == ["1 page"]
        runner.post(V1_CHAT, ana, '"Where were we?"')
        runner.generate(V1_CHAT, ana)
        prompts = table.harness.prompts()
        assert len(prompts) == 2
        assert not [text for text in imported if any(text in prompt for prompt in prompts)]  # still fail closed
        assert flags(runner.open(V1_CHAT)) == ["1 page"]  # playing on does not make the history read


def test_a_sillytavern_chat_reports_its_unread_pages(cards):
    folder, card_chat, _ = cards
    rows = [{"user_name": "Anya Rook", "character_name": "Mara Vell", "create_date": "2026-01-02@10h00m00s"},
            {"name": "Mara Vell", "is_user": False, "mes": "The lamps are out tonight.", "original_avatar": "mara.png"},
            {"name": "Anya Rook", "is_user": True, "mes": "Then we wait for the tide.",
             "force_avatar": "User Avatars/anya.png"}]
    data = ("\n".join(json.dumps(row) for row in rows) + "\n").encode("utf-8")
    with Table(folder) as table:
        importer = StImporter(table.store, table.owner, clock=Clock(), owner=OWNER)
        scan = importer.scan(data)
        avatars = {speaker.avatar: speaker.id for speaker in scan.speakers}
        plan = importer.plan(data, speakers={avatars["mara.png"]: MARA_VELL, avatars["User Avatars/anya.png"]: ANYA},
                             name="harbor.jsonl")
        plan.commit()
        chat = plan.chat
        scene = table.runner.open(chat)
        assert flags(scene) == ["1 page"]
        assert scene.records == ()  # imported text is not a played turn
        assert flags(table.runner.open(card_chat)) == []  # a chat started here has no imported history
