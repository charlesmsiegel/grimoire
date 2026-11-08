"""Synthetic phone and desktop libraries for 044's text API. No real library content, no paid calls.

The desktop library holds two chats, built through the real importers: 026's imported V1 Drowned Ledger
campaign (with its imported scene history) and 037's world-less Mara Vell card chat with its lorebook and
Anya Rook as the persona (its card art is ``unresolved``: the card's pictures were not given with it). The
phone starts as an empty library. Both are identified, paired and synced over 028's loopback transport.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import threading
from pathlib import Path

from card_fixtures import encode, land
from chat_fixtures import Clock
from import_fixtures import Env
from scene_fixtures import (
    ANYA,
    ANYA_CARD,
    MARA_LORE,
    MARA_VELL,
    MARA_VOICE,
    POLICY,
    SERAPHINE_CARD,
    SERAPHINE_DIR,
    V1_CHAT,
    VOICED,
    Table,
    Tagged,
    say,
    secure,
)
from store_helpers import OWNER, owner_handle
from v1_fixture import CAMPAIGN, sha, write

from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.peersync import PeerServer, new_pairing_key, pair
from grimoire.store import LibraryStore
from grimoire.sync import identify
from grimoire.textapi import TextApi

DESKTOP, PHONE = "desktop", "phone"
__all__ = ["DESKTOP", "PHONE", "V1_CHAT", "Device", "copy_library", "desktop_library", "keep", "pair_devices",
           "phone_library", "say", "serving"]


def keep(device, chat: str, take: int = 1, **args) -> dict:
    """Keep ``take`` of the open turn, naming that turn as a client that read it does."""
    turn = device("open", chat=chat)["state"]["open_turn"]
    return device("keep", chat=chat, take=take, turn=turn, **args)


def desktop_library(root: Path):
    """The V1 import and the card chat in one library, secured: (folder, card chat)."""
    root.mkdir(parents=True, exist_ok=True)
    env = Env(root)
    write(env.v1, f"{SERAPHINE_DIR}/default.json", SERAPHINE_CARD)
    write(env.v1, f"{CAMPAIGN}/characters/seraphine/default.json", SERAPHINE_CARD)
    appearances = json.loads((env.v1 / CAMPAIGN / "appearances.json").read_text(encoding="utf-8"))
    appearances["characters/seraphine"]["base"] = sha(SERAPHINE_CARD)
    write(env.v1, f"{CAMPAIGN}/appearances.json", json.dumps(appearances, indent=2, sort_keys=True) + "\n")
    env.rescan()
    env.plan().apply()
    store = env.store
    for document in (VOICED, ANYA_CARD):
        land(store, encode(document))
    chats = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Tagged("c"), owner=OWNER)
    card_chat = chats.start_chat(Launch("character", MARA_VELL), attach=[(MARA_LORE, "lorebook")]).commit().chat
    _, etag = chats.read_chat(card_chat)
    chats.set_persona(card_chat, ANYA, expected=etag).commit()
    store.close()
    secure(root / "v2")
    return root / "v2", card_chat


def phone_library(root: Path) -> Path:
    """An empty, secured library."""
    LibraryStore.create(root).close()
    secure(root)
    return root


class Device:
    """One device's open library, its scripted model and the text API over it (single user: the owner plays)."""

    def __init__(self, folder: Path, steps=(), *, peers=None, runtime: bool = True) -> None:
        self.table = Table(folder, list(steps), voices=MARA_VOICE)
        self.api = TextApi(self.table.store, self.table.authority, owner=OWNER,
                           runtime=self.table.harness.runtime if runtime else None, policy=POLICY,
                           voices=MARA_VOICE, peers=peers, new_id=Tagged(folder.name), transfer_timeout=60.0)

    def __call__(self, op: str, **args):
        return self.api.call(op, args)

    def prompts(self) -> list[str]:
        return self.table.harness.prompts()

    def close(self) -> None:
        self.table.close()

    def __enter__(self) -> Device:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def pair_devices(desk: Path, phone: Path) -> None:
    """Identify both libraries and pair them with one fresh key."""
    key = new_pairing_key()
    for folder, name, peer in ((desk, DESKTOP, PHONE), (phone, PHONE, DESKTOP)):
        with Table(folder) as table:
            identify(table.store, name)
            pair(table.store, table.authority, peer, key, granted_by=OWNER)


@contextlib.contextmanager
def serving(device: Device):
    """The device's library served to its paired devices on loopback, in a thread of this process."""
    server = PeerServer(device.table.store, device.table.authority)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.close()
        thread.join(10)


def copy_library(source: Path, destination: Path) -> Path:
    shutil.copytree(source, destination)
    return destination
