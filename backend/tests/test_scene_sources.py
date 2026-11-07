"""035: a chat whose bound source disappears reports the exact broken ref and keeps its story.

The sprint's world-less rule: if a source card disappears, report its exact broken ref, preserve the accepted
story, and never bind another similarly named card. Over the synthetic card library (``scene_fixtures``);
every model call is a scripted fake.
"""

from __future__ import annotations

import shutil

import pytest

from scene_fixtures import ANA, MARA_LORE, MARA_VELL, STORM, Table, card_library, say

from grimoire.scene import SceneRefused, turn_to_payload


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return card_library(tmp_path_factory.mktemp("built"))


@pytest.fixture
def cards(built, tmp_path):
    folder, card_chat, storm = built
    copy = tmp_path / "cards"
    shutil.copytree(folder, copy)
    return copy, card_chat, storm


def _story(table: Table, chat) -> list[dict]:
    return [turn_to_payload(record) for record in table.runner.open(chat).records]


def _chat_files(folder, chat) -> dict[str, bytes]:
    root = folder / "campaigns" / chat.campaign_id
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*.json")) if "turns" not in p.parts}


def test_a_deleted_launch_card_and_its_lorebook_are_reported_by_ref_and_the_story_stays(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Dark, and the harbor pays for it.")]) as table:
        runner, ana = table.runner, table.player()
        runner.seat(chat, ANA)
        runner.begin(chat, ana)
        runner.keep(chat, ana, 1)
        runner.post(chat, ana, '"Is the lighthouse still dark?"')
        runner.generate(chat, ana)
        runner.keep(chat, ana, 1)
        assert not [code for code, _ in runner.open(chat).problems if code == "broken_source"]
        before = _story(table, chat)
    shutil.rmtree(folder / "sources" / "card" / "mara-vell")  # deleted while the app was closed
    chat_files = _chat_files(folder, chat)

    with Table(folder, [say("Still here.")]) as table:
        runner, ana = table.runner, table.player()
        scene = runner.open(chat)
        broken = [detail for code, detail in scene.problems if code == "broken_source"]
        # the card and the lorebook bound with it, exactly, at the revisions the chat pins (pin by default)
        assert broken == [str(MARA_VELL.at("r1")), str(MARA_LORE.at("r1"))]
        assert _story(table, chat) == before  # the story is kept as it was
        # it still opens and plays: the post is saved, and with nobody left to speak, generation says so
        runner.post(chat, ana, '"Are you still there?"')
        with pytest.raises(SceneRefused) as error:
            runner.generate(chat, ana)
        assert error.value.reason == "nothing_to_generate"
        assert table.harness.prompts() == []
        assert len(runner.open(chat).records) == 3
    assert _chat_files(folder, chat) == chat_files  # nothing rebinds the chat to another card


def test_a_deleted_scenario_is_reported_by_ref_and_its_chat_keeps_its_story(cards):
    folder, _, storm = cards
    with Table(folder, [say("Lightning shows a ship with no lamps.")]) as table:
        runner, ana = table.runner, table.player()
        runner.seat(storm, ANA)
        runner.begin(storm, ana)
        runner.keep(storm, ana, 1)
        runner.post(storm, ana, "*I shake the rain from my coat*")
        runner.generate(storm, ana, director=True)
        runner.keep(storm, ana, 1)
        before = _story(table, storm)
    shutil.rmtree(folder / "sources" / "card" / "storm-over-port-vell")

    with Table(folder, [say("Unreachable.")]) as table:
        runner, ana = table.runner, table.player()
        scene = runner.open(storm)
        assert ("broken_source", str(STORM.at("r1"))) in scene.problems  # pinned by default
        assert _story(table, storm) == before
        runner.post(storm, ana, '"Whose ship is that?"')
        with pytest.raises(SceneRefused) as error:  # no scenario text to direct from, and no guess at one
            runner.generate(storm, ana, director=True)
        assert error.value.reason == "no_scenario"
        assert table.harness.prompts() == []
