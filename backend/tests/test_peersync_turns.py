"""028's turn merge over the authenticated peer transport: a chat both paired devices played offline forks, the
owner chooses on one device, and the next transfer carries the choice, so both go on from the same line.

The same rules as a local sync (test_scene_turn_merge.py), between two processes' worth of state: the serving
device's library is changed only by the transfer. Synthetic card library, loopback only, scripted model calls.
"""

from __future__ import annotations

import contextlib
import shutil
import threading
from hashlib import sha256

import pytest

from scene_fixtures import ANA, Table, card_library, say
from store_helpers import OWNER

from grimoire.peersync import PeerServer, new_pairing_key, pair, transfer
from grimoire.scene import turn_id
from grimoire.sync import identify

DESKTOP, PHONE = "desktop", "phone"


def tag(device: str) -> str:
    return sha256(device.encode()).hexdigest()[:8]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    folder, chat, _ = card_library(tmp_path_factory.mktemp("built"))
    with Table(folder, [say("Dark, and the harbor pays for it.")]) as table:
        table.runner.seat(chat, ANA)
        table.runner.begin(chat, table.player())
        table.runner.keep(chat, table.player(), 1)
    return folder, chat


@contextlib.contextmanager
def serving(table: Table):
    server = PeerServer(table.store, table.authority)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.close()
        thread.join(10)


def play(table: Table, chat, line: str) -> None:
    table.runner.post(chat, table.player(), f'"{line}"')
    table.runner.generate(chat, table.player())
    table.runner.keep(chat, table.player(), 1)


def test_a_forked_chat_goes_on_from_the_owners_choice_on_both_paired_devices(built, tmp_path):
    folder, chat = built
    for name in (DESKTOP, PHONE):
        shutil.copytree(folder, tmp_path / name)
    key = new_pairing_key()
    with Table(tmp_path / DESKTOP, [say("Desk two."), say("Desk three.")]) as desk, \
            Table(tmp_path / PHONE, [say("Phone two."), say("Phone four.")]) as phone:
        for table, name, peer in ((desk, DESKTOP, PHONE), (phone, PHONE, DESKTOP)):
            identify(table.store, name)
            pair(table.store, table.authority, peer, key, granted_by=OWNER)
        with serving(phone) as server:
            def transferred():
                return transfer(desk.store, desk.authority, PHONE, server.address)
            assert transferred().status == "synced"
            play(desk, chat, "Asked on the desktop.")
            play(phone, chat, "Asked on the phone.")
            assert transferred().status == "synced"  # both turn files travel: no conflict, a fork
            ours, theirs = turn_id(chat, 2, tag(DESKTOP)), turn_id(chat, 2, tag(PHONE))
            for table in (desk, phone):
                scene = table.runner.open(chat)
                assert {b.head for b in scene.turns.fork.branches} == {ours, theirs}
            chosen = desk.runner.choose(chat, desk.owner, theirs)  # the owner, on the desktop
            assert [r.turn_id for r in chosen.records] == [turn_id(chat, 1), theirs]
            assert transferred().status == "synced"
            scene = phone.runner.open(chat)
            assert scene.turns.fork is None and [s.record.turn_id for s in scene.turns.set_aside] == [ours]
            play(phone, chat, "The phone goes on.")
            prompt = phone.harness.prompts()[-1]
            assert "Asked on the phone." in prompt and "Asked on the desktop." not in prompt
            assert transferred().status == "synced"
            play(desk, chat, "And the desktop after it.")
            prompt = desk.harness.prompts()[-1]
            assert "The phone goes on." in prompt and "Asked on the desktop." not in prompt
            assert transferred().status == "synced"
        ids = [[r.turn_id for r in table.runner.open(chat).records] for table in (desk, phone)]
        assert ids[0] == ids[1] == [turn_id(chat, 1), theirs, turn_id(chat, 3, tag(PHONE)),
                                    turn_id(chat, 4, tag(DESKTOP))]
