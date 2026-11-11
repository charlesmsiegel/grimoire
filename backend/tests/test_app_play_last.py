"""111: ``play.options`` says who the player last played with a card (``last``), derived from the chats.

Synthetic cards through 037's importer; no model runs. Nothing is stored for this: every answer is read from the
chats the library holds at that moment, so a chat that arrived by sync counts like any other.
"""

from __future__ import annotations

import shutil

import pytest
from card_fixtures import SCENARIO, V3, card, encode, land
from chat_fixtures import Clock
from scene_fixtures import ANYA, ANYA_CARD, MARA_LORE, MARA_VELL, STORM, TAMSIN, TAMSIN_CARD, VOICED, secure
from test_app_play import Device
from textapi_fixtures import DESKTOP, desktop_library, pair_devices, phone_library, serving

from grimoire.app.adapters.play_last import last_played
from grimoire.app.appapi import AppApi
from grimoire.core.identity import SourceRef
from grimoire.store import LibraryStore

#: A different card that happens to share Mara's name: never the same character.
NAMESAKE = SourceRef("card:mara-vell-other", "character", "mara-vell")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    folder = tmp_path_factory.mktemp("built") / "cards"
    with LibraryStore.create(folder) as store:
        for document in (VOICED, ANYA_CARD, TAMSIN_CARD, SCENARIO):
            land(store, encode(document))
        land(store, encode(card(V3, description="Another harbor's Mara Vell.", character_book=None)),
             target=NAMESAKE.source)
    secure(folder)
    return folder


@pytest.fixture
def folder(built, tmp_path):
    copy = tmp_path / "cards"
    shutil.copytree(built, copy)
    return copy


def ticking(folder, **kwargs) -> Device:
    """A device whose chats are stamped one second apart, so which chat is newest is never a tie."""
    device = Device(folder, **kwargs)
    device.api.chats._clock = Clock()
    return device


def last(device: Device, ref=MARA_VELL, kind: str = "character", **args):
    return device("play.options", kind=kind, ref=str(ref), **args)["last"]


def test_a_card_never_played_and_a_premise_have_no_last(folder):
    with ticking(folder) as device:
        assert last(device) is None
        assert last(device, kind="bundle") is None
        assert last(device, STORM, "scenario") is None
        assert device("play.options", kind="premise", premise="A locked lighthouse.")["last"] is None


def test_one_chat_says_who_was_played_and_when(folder):
    with ticking(folder) as device:
        chat = device("play.start", kind="character", ref=str(MARA_VELL), persona=str(ANYA))["chat"]
        options = device("play.options", kind="character", ref=str(MARA_VELL))
        (entry,) = options["related"]
        assert options["last"] == {"chat": chat, "persona": {"ref": str(ANYA), "name": "Anya Rook"},
                                   "lorebooks": [], "when": entry["last_activity"]}
        # a pinned ref asks about the same card, and so does the same card with a lorebook
        assert last(device, MARA_VELL.at("r1")) == options["last"]
        assert last(device, kind="bundle") == options["last"]
        # Anya was the persona there, not who the chat was with: her own card has no history
        assert last(device, ANYA) is None


def test_the_most_recent_chat_wins(folder):
    with ticking(folder) as device:
        device("play.start", kind="character", ref=str(MARA_VELL), persona=str(ANYA))
        assert last(device)["persona"]["ref"] == str(ANYA)
        newer = device("play.start", kind="character", ref=str(MARA_VELL), persona=str(TAMSIN))["chat"]
        found = last(device)
        assert (found["chat"], found["persona"]) == (newer, {"ref": str(TAMSIN), "name": "Tamsin Hale"})


def test_a_persona_since_deleted_is_not_offered_and_an_older_chat_does_not_stand_in(folder):
    with ticking(folder) as device:
        device("play.start", kind="character", ref=str(MARA_VELL), persona=str(TAMSIN))
        device("play.start", kind="character", ref=str(MARA_VELL), persona=str(ANYA))
        assert last(device)["persona"]["ref"] == str(ANYA)
    shutil.rmtree(folder / "sources" / "card" / "anya-rook")
    with ticking(folder) as device:
        options = device("play.options", kind="character", ref=str(MARA_VELL))
        assert len(options["related"]) == 2 and options["last"] is None


def test_same_named_cards_do_not_share_a_history(folder):
    with ticking(folder) as device:
        names = {item["ref"]: item["name"] for item in device("play.sources")["characters"]}
        assert names[str(NAMESAKE)] == names[str(MARA_VELL)] == "Mara Vell"
        device("play.start", kind="character", ref=str(MARA_VELL), persona=str(ANYA))
        assert last(device, NAMESAKE) is None
        device("play.start", kind="character", ref=str(NAMESAKE), persona=str(TAMSIN))
        assert last(device, NAMESAKE)["persona"]["ref"] == str(TAMSIN)
        assert last(device)["persona"]["ref"] == str(ANYA)


def test_a_bundle_remembers_its_lorebooks_while_they_can_still_be_bound(folder):
    with ticking(folder) as device:
        storm_lore = next(item for item in device("play.sources")["lorebooks"] if item["ref"] != str(MARA_LORE))
        device("play.start", kind="bundle", ref=str(MARA_VELL), persona=str(ANYA))
        found = last(device, kind="bundle")
        assert found["lorebooks"] == [{"ref": str(MARA_LORE), "name": found["lorebooks"][0]["name"]}]
        assert last(device) == found  # the character alone is the same card
        device("play.start", kind="bundle", ref=str(MARA_VELL), lorebook=storm_lore["ref"], persona=str(TAMSIN))
        found = last(device, kind="bundle")
        assert found["persona"]["ref"] == str(TAMSIN)
        assert found["lorebooks"] == [{"ref": storm_lore["ref"], "name": storm_lore["name"]}]
    shutil.rmtree(folder / "sources" / "card" / "storm-over-port-vell")
    with ticking(folder) as device:  # the lorebook is gone: who was played is still known, the lorebook is not offered
        found = last(device, kind="bundle")
        assert found["persona"]["ref"] == str(TAMSIN) and found["lorebooks"] == []


def test_a_chat_the_card_was_only_added_to_is_not_a_chat_with_that_card(folder):
    with ticking(folder) as device:
        chat = device("play.start", kind="scenario", ref=str(STORM), persona=str(ANYA))["chat"]
        device("play.add_card", chat=chat, ref=str(MARA_VELL))
        options = device("play.options", kind="character", ref=str(MARA_VELL))
        assert [entry["chat"] for entry in options["related"]] == [chat] and options["last"] is None
        assert last(device, STORM, "scenario")["persona"]["ref"] == str(ANYA)


def test_a_chat_that_arrived_by_sync_counts(tmp_path):
    desk, card_chat = desktop_library(tmp_path / "desk")  # Mara with her lorebook, played as Anya, on the desktop
    phone = phone_library(tmp_path / "phone")
    pair_devices(desk, phone)
    with Device(desk) as desktop, serving(desktop) as server, Device(phone, peers={DESKTOP: server.address}) as mobile:
        assert mobile("play.sources")["characters"] == []  # nothing here yet: the phone's library is empty
        assert mobile("sync", peer=DESKTOP)["status"] == "synced"
        found = last(mobile, kind="bundle")
        assert found["chat"] == str(card_chat) and found["persona"] == {"ref": str(ANYA), "name": "Anya Rook"}
        assert [item["ref"] for item in found["lorebooks"]] == [str(MARA_LORE)]
        assert found == last(desktop, kind="bundle")


# -- the lookup itself, on entries a list could hold ---------------------------------------------------------

def entry(chat: str, launch: str | None, persona: str | None, *, problem: str | None = None, when: str = "t") -> dict:
    sources = [] if persona is None else [{"role": "persona", "ref": persona, "name": "Anya Rook"}]
    return {"chat": chat, "label": None, "last_activity": when, "scope": None, "problem": problem, "sources": sources,
            "launch": None if launch is None else {"kind": "character", "ref": launch, "name": "Mara Vell"}}


def test_broken_and_unreadable_entries_are_passed_over_without_a_crash(folder):
    mara, anya = str(MARA_VELL.at("r1")), str(ANYA)
    with Device(folder) as device:
        store = device.table.store
        good = entry("c#chat/3", mara, anya, when="2026-10-31T10:00:03Z")
        assert last_played(store, MARA_VELL, [good])["persona"] == {"ref": anya, "name": "Anya Rook"}
        broken = [entry("c#chat/1", mara, anya, problem="orphan_chat"), entry("c#chat/2", None, None, problem="undecodable")]
        assert last_played(store, MARA_VELL, broken) is None  # a broken chat is never the evidence, even alone
        assert last_played(store, MARA_VELL, [*broken, good])["chat"] == "c#chat/3"
        # no persona bound says nothing about who was played: the next chat is asked
        assert last_played(store, MARA_VELL, [entry("c#chat/4", mara, None), good])["chat"] == "c#chat/3"
        for odd in ({"chat": "x"}, {"chat": "x", "launch": {"ref": "not a ref"}, "sources": [], "problem": None},
                    {"chat": "x", "launch": {"ref": mara}, "sources": "no", "problem": None},
                    entry("c#chat/5", mara, "not a ref"), entry("c#chat/6", mara, str(MARA_LORE))):
            assert last_played(store, MARA_VELL, [odd]) is None
        nameless = entry("c#chat/7", mara, anya)
        nameless["sources"][0]["name"] = None  # the library cannot name it now: it could not be shown or bound
        assert last_played(store, MARA_VELL, [nameless, good]) is None


def test_asking_writes_nothing(folder):
    def files():
        return {path.relative_to(folder): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}

    with ticking(folder) as device:
        device("play.start", kind="bundle", ref=str(MARA_VELL), persona=str(ANYA))
        before = files()
        assert last(device, kind="bundle") is not None
        assert files() == before


def test_the_op_is_served_by_the_apps_dispatch(folder):
    with Device(folder) as device:
        assert isinstance(device.app, AppApi) and "last" in device("play.options", kind="character", ref=str(MARA_VELL))
