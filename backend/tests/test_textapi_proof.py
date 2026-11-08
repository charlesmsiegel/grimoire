"""044's proving scenario through the text API: add a post on a phone, sync to desktop, and resume without
duplicate turns or missing art.

Two libraries in one process: the desktop holds 026's imported V1 Drowned Ledger campaign and 037's
world-less Mara Vell card chat (with its lorebook); the phone starts empty. They are paired, and the
desktop serves its library on loopback. Every model call is a scripted fake.
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from scene_fixtures import MARA, SERAPHINE
from textapi_fixtures import (
    DESKTOP,
    PHONE,
    V1_CHAT,
    Device,
    desktop_library,
    pair_devices,
    phone_library,
    say,
    serving,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR synthetic phone picture"
MARA_CARD = "card:mara-vell/character/mara-vell"


@pytest.fixture
def libraries(tmp_path):
    desk, card_chat = desktop_library(tmp_path / "desk")
    phone = phone_library(tmp_path / "phone")
    pair_devices(desk, phone)
    return desk, phone, card_chat


def _texts(scene: dict) -> list[str]:
    """Every kept line and post of a scene, in order, as the player reads it."""
    out = []
    for turn in scene["turns"]:
        if turn["post"] is not None:
            out.append(turn["post"]["text"])
        if turn["kept"] is not None:
            out += [line["text"] for line in turn["kept"]["lines"]]
    return out


def _actor(scene: dict, name: str) -> dict:
    (found,) = [member for member in scene["cast"] if member["name"] == name]
    return found


def test_a_post_and_a_picture_from_the_phone_resume_on_the_desktop(libraries):
    desk, phone, card_chat = libraries
    chat = str(card_chat)
    with Device(desk, [say("Lamps out at midnight, as promised.")]) as desktop, serving(desktop) as server:
        with Device(phone, [say("Dark, and the harbor pays for it.")], peers={DESKTOP: server.address}) as mobile:
            assert mobile("chats")["chats"] == []
            first = mobile("sync", peer=DESKTOP)
            assert first["status"] == "synced", first

            # Recent Chats lists both chats; a selected character finds each chat once.
            recent = mobile("chats")
            assert {entry["chat"] for entry in recent["chats"]} == {chat, str(V1_CHAT)}
            assert {"role": "character", "ref": MARA_CARD, "name": "Mara Vell"} in recent["sources"]
            by_card = mobile("chats", filter="character", source=MARA_CARD)["chats"]
            assert [entry["chat"] for entry in by_card] == [chat]
            assert by_card[0]["scope"]["world"] is None
            assert {(item["role"], item["ref"]) for item in by_card[0]["sources"]} >= {
                ("character", MARA_CARD), ("lorebook", "card:mara-vell/lorebook/mara-vell-lore")}

            # Play one turn on the phone and give Mara a new avatar there.
            scene = mobile("continue", chat=chat)
            opening = _texts(scene)
            scene = mobile("post", chat=chat, text='Is the lighthouse lit?')
            scene = mobile("generate", chat=chat)
            assert scene["state"]["keepable"] == [1]
            scene = mobile("keep", chat=chat, take=1)
            phone_turns = [turn["turn"] for turn in scene["turns"]]
            assert _texts(scene) == opening + ['Is the lighthouse lit?', "Dark, and the harbor pays for it."]
            mara = _actor(scene, "Mara Vell")
            before = {slot["slot"]: slot for slot in mara["art"]["slots"]}
            assert before["avatar"]["state"] == "unavailable"
            scene = mobile("add_art", chat=chat, actor=mara["actor"], slot="avatar",
                           media=base64.b64encode(PNG).decode("ascii"))
            avatar = {slot["slot"]: slot for slot in _actor(scene, "Mara Vell")["art"]["slots"]}["avatar"]
            assert avatar["state"] == "shown" and avatar["media_type"] == "image/png"
            assert mobile.api.image(avatar["image"]) == ("image/png", PNG)

            report = mobile("sync", peer=DESKTOP)
            assert report["status"] == "synced", report
            assert report["conflicts"] == []

        # The desktop resumes: the phone's turn once, its picture whole, the card's own art still unresolved.
        scene = desktop("open", chat=chat)
        assert [turn["turn"] for turn in scene["turns"]] == phone_turns
        assert all(turn.endswith(f"-{_tag(PHONE)}") for turn in phone_turns)  # made on the phone (028's tag)
        assert _texts(scene).count("Dark, and the harbor pays for it.") == 1
        art = {slot["slot"]: slot for slot in _actor(scene, "Mara Vell")["art"]["slots"]}
        assert art["avatar"]["state"] == "shown"
        assert desktop.api.image(art["avatar"]["image"]) == ("image/png", PNG)
        assert art["background"]["state"] == "unavailable"
        assert scene["state"]["waiting"] == [] and scene["state"]["conflicts"] == []

        # Who plays the persona is this device's own record: the desktop seats its player before it posts.
        assert scene["state"]["seated"] is False
        scene = desktop("continue", chat=chat)
        assert scene["state"]["seated"] is True and scene["unfinished"] is None
        assert [turn["turn"] for turn in scene["turns"]] == phone_turns
        scene = desktop("post", chat=chat, text='Then we sail tonight.')
        scene = desktop("generate", chat=chat)
        scene = desktop("keep", chat=chat, take=1)
        assert _texts(scene)[-2:] == ['Then we sail tonight.', "Lamps out at midnight, as promised."]
        (prompt,) = desktop.prompts()
        assert prompt.count("Dark, and the harbor pays for it.") == 1
        desktop_turns = [turn["turn"] for turn in scene["turns"]]
        assert desktop_turns[:len(phone_turns)] == phone_turns and len(desktop_turns) == len(phone_turns) + 1

        # The phone picks the desktop's turn up: one history, the same turn ids on both.
        with Device(phone, peers={DESKTOP: server.address}) as mobile:
            assert mobile("sync", peer=DESKTOP)["status"] == "synced"
            again = mobile("open", chat=chat)
            assert [turn["turn"] for turn in again["turns"]] == desktop_turns
            assert _texts(again) == _texts(scene)
            assert mobile("sync", peer=DESKTOP)["copied"] == 0


def test_the_imported_v1_chat_is_found_by_its_world_character_and_played_on_the_phone(libraries):
    desk, phone, _card_chat = libraries
    chat = str(V1_CHAT)
    steps = [say("The ledger is wet, but legible."), say("Then read it aloud.")]
    with Device(desk) as desktop, serving(desktop) as server:
        with Device(phone, steps, peers={DESKTOP: server.address}) as mobile:
            assert mobile("sync", peer=DESKTOP)["status"] == "synced"
            listed = mobile("chats", filter="character", source=_world_ref(mobile, "Mara"))["chats"]
            assert [entry["chat"] for entry in listed] == [chat]
            assert listed[0]["scope"]["world"] is not None

            scene = mobile("open", chat=chat)
            assert [line["text"] for line in scene["history"]] == [
                "I knock on the door.", "The door opens.", "Someone waits inside."]
            mara = _actor(scene, "Mara")
            assert mara["actor"] == str(MARA)
            assert {slot["slot"]: slot["state"] for slot in mara["art"]["slots"]}["avatar"] == "shown"

            assert scene["state"]["seated"] is False
            scene = mobile("continue", chat=chat)
            assert scene["state"]["seated"] is True and scene["persona"]["name"] == "Winifred"
            mobile("post", chat=chat, text='What does the ledger say?')
            scene = mobile("generate", chat=chat, speakers=[str(MARA), str(SERAPHINE)])
            scene = mobile("keep", chat=chat, take=1)
            assert _texts(scene)[-3:] == ['What does the ledger say?', "The ledger is wet, but legible.",
                                          "Then read it aloud."]
            turns = [turn["turn"] for turn in scene["turns"]]
            assert mobile("sync", peer=DESKTOP)["status"] == "synced"

        resumed = desktop("open", chat=chat)
        assert [turn["turn"] for turn in resumed["turns"]] == turns
        assert resumed["history"] == scene["history"]


def _world_ref(device: Device, name: str) -> str:
    (ref,) = [item["ref"] for item in device("chats")["sources"]
              if item["name"] == name and item["ref"].startswith("world:")]
    return ref


def _tag(device: str) -> str:
    """028's device tag of a device id, as new turn ids carry it."""
    return hashlib.sha256(device.encode("utf-8")).hexdigest()[:8]
