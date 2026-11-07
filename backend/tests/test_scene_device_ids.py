"""035: turn ids carry 028's per-device tag, so two paired devices playing one chat offline never mint one turn id.

A turn is ``<chat_id>.<n:06d>`` on a library with no device identity (as before) and
``<chat_id>.<n:06d>-<device tag>`` on one with an identity. Turns made before ``identify`` keep their ids and
are still read; play goes on from them with tagged turns. Two devices' turn n are two files. Once both are in
one library (sync carries both since 028's turn merge, or a hand copy), the chat is forked: it still opens, in
one order on every device, and every write is refused ``turn_fork`` until the owner chooses a branch
(test_scene_turn_merge.py). Over the synthetic card library; every model call is a scripted fake.
"""

from __future__ import annotations

import shutil
from hashlib import sha256

import pytest

from scene_fixtures import ANA, Table, card_library, say

from grimoire.scene import Post, SceneRefused, TurnRecord, turn_id
from grimoire.sync import identify


def tag(device: str) -> str:
    """028's documented tag: the first eight hex digits of the SHA-256 of the device ID."""
    return sha256(device.encode()).hexdigest()[:8]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return card_library(tmp_path_factory.mktemp("built"))


@pytest.fixture
def cards(built, tmp_path):
    folder, card_chat, storm = built
    copy = tmp_path / "cards"
    shutil.copytree(folder, copy)
    return copy, card_chat, storm


def refused(reason: str, call, *args, **kwargs) -> None:
    with pytest.raises(SceneRefused) as error:
        call(*args, **kwargs)
    assert error.value.reason == reason, error.value


def turn_files(folder, chat) -> dict[str, bytes]:
    root = folder / "campaigns" / chat.campaign_id / "turns"
    return {p.name: p.read_bytes() for p in sorted(root.glob("*.json"))} if root.is_dir() else {}


def opened(table: Table, chat) -> None:
    runner, ana = table.runner, table.player()
    runner.seat(chat, ANA)
    runner.begin(chat, ana)
    runner.keep(chat, ana, 1)


def test_an_identified_library_tags_every_new_turn_with_its_device(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Dark, and the harbor pays for it.")]) as table:
        identify(table.store, "desktop")
        opened(table, chat)
        post = table.runner.post(chat, table.player(), '"Is the lighthouse still dark?"')
        take = table.runner.generate(chat, table.player())
        expected = tag("desktop")
        assert [r.turn_id for r in table.runner.open(chat).records] == [
            f"{chat.chat_id}.000001-{expected}", f"{chat.chat_id}.000002-{expected}"]
        assert post.device == expected and take.lines[0].line_id == f"{post.turn_id}.1.1"
        assert sorted(turn_files(folder, chat)) == [f"{chat.chat_id}.000001-{expected}.json",
                                                   f"{chat.chat_id}.000002-{expected}.json"]


def test_turns_played_before_identify_are_still_read_and_play_goes_on_tagged(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Dark, and the harbor pays for it."), say("Mind the tide.")]) as table:
        runner, ana = table.runner, table.player()
        opened(table, chat)
        runner.post(chat, ana, '"Is the lighthouse still dark?"')
        runner.generate(chat, ana)
        runner.keep(chat, ana, 1)
        assert [r.turn_id for r in runner.open(chat).records] == [f"{chat.chat_id}.000001", f"{chat.chat_id}.000002"]
        identify(table.store, "desktop")
        kept_line = runner.open(chat).records[1].kept.lines[0].line_id
        runner.strike(chat, ana, kept_line)  # a rewrite of an old turn keeps its untagged id
        runner.post(chat, ana, '"And the debts?"')
        runner.generate(chat, ana)
        scene = runner.open(chat)
        assert scene.turns.complete
        assert [r.turn_id for r in scene.records] == [
            f"{chat.chat_id}.000001", f"{chat.chat_id}.000002", f"{chat.chat_id}.000003-{tag('desktop')}"]
        assert not scene.records[1].kept.lines[0].shown


def test_a_turn_planned_before_identify_is_refused_once_the_library_is_identified(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Dark, and the harbor pays for it.")]) as table:
        opened(table, chat)
        scene = table.runner.open(chat)
        persona = scene.persona
        planned = TurnRecord(chat, 2, "post", Post(ANA, persona, "speech", "Planned.", (persona,), "speech"))
        identify(table.store, "desktop")
        before = turn_files(folder, chat)
        refused("turn_changed", table.runner.scenes.write, planned, None, after=scene.turns.last)
        # and a library never holds two turns of one number, whatever tags they carry
        twice = TurnRecord(chat, 1, "prompt", device=tag("desktop"))
        refused("turn_changed", table.runner.scenes.write, twice, None)
        assert turn_files(folder, chat) == before


def test_two_offline_devices_playing_turn_n_keep_both_turns_and_the_chat_waits_for_a_choice(cards, tmp_path):
    folder, chat, _ = cards
    with Table(folder, [say("Dark, and the harbor pays for it.")]) as table:
        opened(table, chat)
    phone_folder = tmp_path / "phone"
    shutil.copytree(folder, phone_folder)
    played = {}
    for device, where in (("desktop", folder), ("phone", phone_folder)):
        with Table(where, [say(f"Answered on the {device}.")]) as table:
            identify(table.store, device)
            played[device] = table.runner.post(chat, table.player(), f'"Asked on the {device}."')
    assert played["desktop"].number == played["phone"].number == 2
    assert played["desktop"].turn_id != played["phone"].turn_id  # untagged, both would be chat.000002
    # both devices' turn 2 in one library (a hand copy here, as sync would bring it): two files, side by side
    name = f"{played['phone'].turn_id}.json"
    turns = folder / "campaigns" / chat.campaign_id / "turns"
    (turns / name).write_bytes((phone_folder / "campaigns" / chat.campaign_id / "turns" / name).read_bytes())
    before = turn_files(folder, chat)
    with Table(folder, [say("Unreachable.")]) as table:
        runner, ana = table.runner, table.player()
        scene = runner.open(chat)
        assert [p for p in scene.problems if p[0] == "turns"] == [("turns", f"{chat}:turn_fork")]
        first, second = sorted((tag("desktop"), tag("phone")))
        assert [r.turn_id for r in scene.records] == [  # one order on every device
            f"{chat.chat_id}.000001", turn_id(chat, 2, first), turn_id(chat, 2, second)]
        for call, args in ((runner.post, ('"Hello?"',)), (runner.generate, ()), (runner.pass_turn, ()),
                           (runner.request, ("pause",))):
            refused("turn_fork", call, chat, ana, *args)
        assert table.harness.prompts() == []
    assert turn_files(folder, chat) == before  # neither turn was dropped or overwritten
    (turns / f"{chat.chat_id}.000003.json").write_bytes(b"{not json")  # a fork and an unreadable file
    with Table(folder) as table:
        refused("turns_unreadable", table.runner.post, chat, table.player(), '"Hello?"')


def test_a_damaged_device_identity_refuses_new_turns_rather_than_untagged_ids(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Dark, and the harbor pays for it.")]) as table:
        opened(table, chat)
        identify(table.store, "desktop")
        (folder / ".sync" / "device.json").write_bytes(b"{not json")
        before = turn_files(folder, chat)
        refused("device_identity", table.runner.post, chat, table.player(), '"Hello?"')
        refused("device_identity", table.runner.generate, chat, table.player())
        assert table.runner.open(chat).turns.complete  # reading needs no identity
        assert turn_files(folder, chat) == before
