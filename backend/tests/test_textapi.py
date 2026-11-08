"""044's text API: refusals, validation, offline play on two devices, forks and sync conflicts.

Synthetic libraries from ``textapi_fixtures``; loopback only; every model call is a scripted fake.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket

import pytest
from scene_fixtures import Table
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

from grimoire.sync import identify
from grimoire.textapi import ApiRefused, TextApi

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR synthetic"
OTHER_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR another synthetic"


@pytest.fixture
def synced(tmp_path):
    """Paired desktop and phone, the phone holding the desktop's library after one sync: (desk, phone, chat)."""
    desk, card_chat = desktop_library(tmp_path / "desk")
    phone = phone_library(tmp_path / "phone")
    pair_devices(desk, phone)
    with Device(desk) as desktop, serving(desktop) as server, Device(phone, peers={DESKTOP: server.address}) as mobile:
        assert mobile("sync", peer=DESKTOP)["status"] == "synced"
    return desk, phone, str(card_chat)


def _refused(device: Device, op: str, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        device(op, **args)
    return caught.value


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _play(device: Device, chat: str, text: str) -> dict:
    device("continue", chat=chat)
    device("post", chat=chat, text=text)
    device("generate", chat=chat)
    return device("keep", chat=chat, take=1)


def _mara(scene: dict) -> str:
    (actor,) = [member["actor"] for member in scene["cast"] if member["name"] == "Mara Vell"]
    return actor


def _media(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# -- requests the API refuses ---------------------------------------------------------------------------

def test_malformed_requests_are_refused_with_their_status_and_write_nothing(synced):
    _desk, phone, chat = synced
    with Device(phone) as mobile:
        before = json.dumps(mobile("open", chat=chat), sort_keys=True)
        cases = [
            ("nope", {}, "unknown_op", 404),
            ("open", {}, "invalid_field", 400),
            ("open", {"chat": 7}, "invalid_field", 400),
            ("open", {"chat": "not a chat"}, "invalid_field", 400),
            ("open", {"chat": "campaign:nowhere#chat/none"}, "no_chat", 404),
            ("chats", {"filter": "everything"}, "invalid_field", 400),
            ("chats", {"limit": 0}, "invalid_field", 400),
            ("chats", {"limit": True}, "invalid_field", 400),
            ("chats", {"filter": "character"}, "invalid_field", 400),
            ("post", {"chat": chat, "text": "x" * 20001}, "invalid_field", 400),
            ("keep", {"chat": chat, "take": 0}, "invalid_field", 400),
            ("keep", {"chat": chat, "take": True}, "invalid_field", 400),
            ("generate", {"chat": chat, "speakers": "mara"}, "invalid_field", 400),
            ("add_art", {"chat": chat, "actor": "campaign:x/character/y", "media": "!!"}, "invalid_field", 400),
            ("add_art", {"chat": chat, "actor": "campaign:x/character/y", "media": ""}, "empty", 400),
            ("add_art", {"chat": chat, "actor": "campaign:x/character/y", "media": _media(PNG)}, "no_actor", 404),
            ("sync", {"peer": "laptop"}, "unknown_peer", 404),
            ("resolve", {"peer": "laptop", "path": "x", "keep": "mine"}, "unknown_peer", 404),
            ("post", {"chat": chat, "text": ""}, "no_content", 400),
        ]
        for op, args, reason, status in cases:
            error = _refused(mobile, op, **args)
            assert (error.reason, error.status) == (reason, status), (op, args, error)
            assert error.to_json()["error"] == reason
        with pytest.raises(ApiRefused) as caught:
            mobile.api.call("open", ["not", "an", "object"])  # type: ignore[arg-type]
        assert caught.value.reason == "invalid_field"
        mara = _mara(mobile("open", chat=chat))
        assert _refused(mobile, "add_art", chat=chat, actor=mara, media=_media(PNG), expected="garbage").status == 400
        assert _refused(mobile, "add_art", chat=chat, actor=_mara(mobile("open", chat=chat)), slot="expr.default.smile",
                        media=_media(PNG)).reason == "invalid_field"
        for slot in ("", "a/b", "x" * 300):  # a slot PlacementRef refuses is a field error, not a failure
            assert _refused(mobile, "add_art", chat=chat, actor=mara, slot=slot, media=_media(PNG)).status == 400
        assert json.dumps(mobile("open", chat=chat), sort_keys=True) == before


def test_without_a_model_the_chat_can_be_read_and_posted_to_but_not_answered(synced):
    _desk, phone, chat = synced
    with Device(phone, runtime=False) as mobile:
        assert mobile("library")["generation"] is False
        mobile("continue", chat=chat)
        mobile("post", chat=chat, text="Anyone there?")
        error = _refused(mobile, "generate", chat=chat)
        assert error.status == 409


def test_only_the_owner_plays_in_this_slice(synced):
    _desk, phone, _chat = synced
    with Table(phone) as table, pytest.raises(ValueError):
        TextApi(table.store, table.authority, owner="human:owner", player="human:ana")


# -- sync failures ----------------------------------------------------------------------------------------

def test_an_unreachable_desktop_leaves_the_phone_playable(synced):
    _desk, phone, chat = synced
    with Device(phone, [say("Still here.")], peers={DESKTOP: ("127.0.0.1", _free_port())}) as mobile:
        report = mobile("sync", peer=DESKTOP)
        assert report["status"] == "incomplete" and report["reason"].startswith("unreachable")
        assert mobile("library")["peers"][0]["last"] == report
        scene = _play(mobile, chat, "Hello?")
        assert scene["turns"][-1]["kept"]["lines"][0]["text"] == "Still here."


def test_a_library_that_is_not_paired_with_the_peer_is_refused(tmp_path):
    desk, _chat = desktop_library(tmp_path / "desk")
    phone = phone_library(tmp_path / "phone")
    for folder, name in ((desk, DESKTOP), (phone, PHONE)):
        with Table(folder) as table:
            identify(table.store, name)
    with Device(desk) as desktop, serving(desktop) as server, Device(phone, peers={DESKTOP: server.address}) as mobile:
        error = _refused(mobile, "sync", peer=DESKTOP)
        assert (error.reason, error.status) == ("not_paired", 409)


def test_a_take_proposed_on_the_phone_is_kept_on_the_desktop(synced):
    desk, phone, chat = synced
    with Device(desk) as desktop, serving(desktop) as server:
        with Device(phone, [say("Proposed on the phone.")], peers={DESKTOP: server.address}) as mobile:
            mobile("continue", chat=chat)
            mobile("post", chat=chat, text="Your move.")
            scene = mobile("generate", chat=chat)
            assert scene["state"]["keepable"] == [1]
            assert mobile("sync", peer=DESKTOP)["status"] == "synced"
        desktop("continue", chat=chat)
        scene = desktop("open", chat=chat)
        assert scene["state"]["keepable"] == [1]
        scene = desktop("keep", chat=chat, take=1)
        assert scene["turns"][-1]["kept"]["lines"][0]["text"] == "Proposed on the phone."
        assert scene["state"]["open_turn"] is None


# -- both devices played offline --------------------------------------------------------------------------

def test_both_devices_playing_offline_fork_until_the_owner_chooses(synced):
    desk, phone, chat = synced
    with Device(desk, [say("Desk reply.")]) as desktop:
        _play(desktop, chat, "From the desk.")
        with serving(desktop) as server:
            with Device(phone, [say("Phone reply.")], peers={DESKTOP: server.address}) as mobile:
                _play(mobile, chat, "From the phone.")
                assert mobile("sync", peer=DESKTOP)["status"] == "synced"
                scene = mobile("open", chat=chat)
                assert "forked" in scene["state"]["waiting"]
                fork = scene["fork"]
                assert len(fork["branches"]) == 2
                assert _refused(mobile, "post", chat=chat, text="Hm.").reason == "turn_fork"
                (phone_head,) = [branch["head"] for branch in fork["branches"] if branch["device"] == tag(PHONE)]
                scene = mobile("choose", chat=chat, turn=phone_head)
                assert scene["fork"] is None or scene["fork"]["state"] != "open"
                assert "forked" not in scene["state"]["waiting"]
                assert all("Desk reply." != line["text"] for turn in scene["turns"] if turn["kept"]
                           for line in turn["kept"]["lines"])
                assert scene["set_aside"] and all(set(turn) == {"turn", "number", "device"}
                                                  for turn in scene["set_aside"])
                chosen = [turn["turn"] for turn in scene["turns"]]
                assert mobile("sync", peer=DESKTOP)["status"] == "synced"
        scene = desktop("open", chat=chat)
        assert "forked" not in scene["state"]["waiting"]
        assert [turn["turn"] for turn in scene["turns"]] == chosen


def tag(device: str) -> str:
    """028's device tag of a device id, as turn and branch records carry it."""
    return hashlib.sha256(device.encode("utf-8")).hexdigest()[:8]


def test_two_devices_keeping_one_turn_differently_conflict_until_one_is_resolved(synced):
    desk, phone, chat = synced
    with Device(desk, [say("Shared question asked.")]) as desktop, serving(desktop) as server:
        with Device(phone, peers={DESKTOP: server.address}) as mobile:
            desktop("continue", chat=chat)
            desktop("post", chat=chat, text="One question.")
            desktop("generate", chat=chat)
            assert mobile("sync", peer=DESKTOP)["status"] == "synced"
    # offline, each device passes or keeps the same open turn its own way
    with Device(desk) as desktop:
        desktop("keep", chat=chat, take=1)
        with serving(desktop) as server, Device(phone, peers={DESKTOP: server.address}) as mobile:
            mobile("continue", chat=chat)
            mobile("pass", chat=chat)
            report = mobile("sync", peer=DESKTOP)
            assert report["conflicts"], report
            scene = mobile("open", chat=chat)
            assert "conflicted" in scene["state"]["waiting"]
            (conflict,) = [item for item in scene["state"]["conflicts"] if "/turns/" in item["path"]]
            assert _refused(mobile, "post", chat=chat, text="Hm.").reason == "turn_conflict"
            result = mobile("resolve", peer=DESKTOP, path=conflict["path"], keep="theirs")
            assert result["status"] == "resolved", result
            scene = mobile("open", chat=chat)
            assert "conflicted" not in scene["state"]["waiting"]
            assert scene["turns"][-1]["kept"]["lines"][0]["text"] == "Shared question asked."
            assert mobile("sync", peer=DESKTOP)["conflicts"] == []
        scene = desktop("open", chat=chat)
        assert "conflicted" not in scene["state"]["waiting"]


def test_a_picture_added_to_one_slot_on_both_devices_conflicts_and_both_pictures_stay_readable(synced):
    desk, phone, chat = synced
    with Device(desk) as desktop:
        mara = _mara(desktop("open", chat=chat))
        desk_art = desktop("add_art", chat=chat, actor=mara, media=_media(PNG))
        with serving(desktop) as server, Device(phone, peers={DESKTOP: server.address}) as mobile:
            phone_art = mobile("add_art", chat=chat, actor=mara, media=_media(OTHER_PNG))
            report = mobile("sync", peer=DESKTOP)
            assert report["conflicts"], report
            for device, scene, data in ((desktop, desk_art, PNG), (mobile, phone_art, OTHER_PNG)):
                (avatar,) = [slot for member in scene["cast"] if member["actor"] == mara
                             for slot in member["art"]["slots"] if slot["slot"] == "avatar"]
                assert mobile.api.image(avatar["image"]) == ("image/png", data)
            scene = mobile("open", chat=chat)
            (conflict,) = scene["state"]["conflicts"]
            assert len(conflict["theirs"]) == 1 and conflict["mine"] not in conflict["theirs"]
            assert scene["state"]["waiting"] == []  # a picture in conflict holds the slot, not play
            # a third version would leave the conflict with nothing to resolve: the slot waits for resolve
            error = _refused(mobile, "add_art", chat=chat, actor=mara, media=_media(PNG + b"3"))
            assert (error.reason, error.status) == ("art_conflict", 409)
            assert _refused(mobile, "resolve", peer=DESKTOP, path="nowhere", keep="mine").status == 400
            assert _refused(mobile, "resolve", peer=DESKTOP, path=conflict["path"], keep="neither").status == 400
            assert mobile("resolve", peer=DESKTOP, path=conflict["path"], keep="mine")["status"] == "resolved"
            assert mobile("sync", peer=DESKTOP)["conflicts"] == []
        scene = desktop("open", chat=chat)
        (avatar,) = [slot for member in scene["cast"] if member["actor"] == mara
                     for slot in member["art"]["slots"] if slot["slot"] == "avatar"]
        assert desktop.api.image(avatar["image"]) == ("image/png", OTHER_PNG)


def test_a_second_picture_on_a_slot_needs_the_slot_as_it_was_read(synced):
    _desk, phone, chat = synced
    with Device(phone) as mobile:
        mara = _mara(mobile("open", chat=chat))
        scene = mobile("add_art", chat=chat, actor=mara, media=_media(PNG))
        (avatar,) = [slot for member in scene["cast"] if member["actor"] == mara
                     for slot in member["art"]["slots"] if slot["slot"] == "avatar"]
        error = _refused(mobile, "add_art", chat=chat, actor=mara, media=_media(OTHER_PNG))
        assert (error.reason, error.status) == ("changed", 409)
        scene = mobile("add_art", chat=chat, actor=mara, media=_media(OTHER_PNG), expected=avatar["etag"])
        (avatar,) = [slot for member in scene["cast"] if member["actor"] == mara
                     for slot in member["art"]["slots"] if slot["slot"] == "avatar"]
        assert mobile.api.image(avatar["image"]) == ("image/png", OTHER_PNG)


def test_open_never_shows_what_the_player_may_not_read(synced):
    _desk, phone, chat = synced
    with Device(phone, [say("Plain line.")]) as mobile:
        scene = _play(mobile, chat, "Hi.")
        text = json.dumps(scene)
        for hidden in ("intention", "brief", "unscreened_names", "secret"):
            assert hidden not in text
        assert all(set(turn) >= {"turn", "post", "kept"} for turn in scene["turns"])


def test_the_v1_chat_reads_on_the_phone_with_its_imported_history(synced):
    _desk, phone, _chat = synced
    with Device(phone) as mobile:
        scene = mobile("open", chat=str(V1_CHAT))
        assert scene["scope"]["world"] is not None
        assert len(scene["history"]) == 3


def test_a_take_with_no_line_shown_is_not_offered_to_keep(synced):
    _desk, phone, chat = synced
    with Device(phone, []) as mobile:  # the scripted model has no reply: the call fails and the take halts
        mobile("continue", chat=chat)
        mobile("post", chat=chat, text="Anyone?")
        try:
            scene = mobile("generate", chat=chat)
        except ApiRefused:
            scene = mobile("open", chat=chat)
        (take,) = scene["turns"][-1]["offered"]
        assert take["lines"] == [] and take["calls"]
        assert scene["state"]["keepable"] == []


def test_a_character_filter_reads_every_chat_however_many_the_library_holds(synced, monkeypatch):
    import grimoire.chats.navigation as navigation

    _desk, phone, chat = synced
    with Device(phone) as mobile:
        monkeypatch.setattr(navigation, "MAX_RECENT", 1)  # fewer than the library's chats: recent() would refuse
        listed = mobile("chats", filter="character", source="card:mara-vell/character/mara-vell")["chats"]
        assert chat in [entry["chat"] for entry in listed]


def test_a_staged_take_shows_only_the_lines_a_player_character_heard():
    from grimoire.core.identity import InstanceRef
    from grimoire.scene.model import SceneLine, Take
    from grimoire.textapi.views import heard

    anya, mara, tamsin = (InstanceRef("w", "character", name) for name in ("anya", "mara", "tamsin"))
    lines = (SceneLine("l1", mara, "Mara", "Psst, the vault code.", (tamsin,)),
             SceneLine("l2", mara, "Mara", "Good evening.", (anya, tamsin)))
    staged = Take(1, "kept", "generated", (anya, mara, tamsin), lines, on_stage=(anya, mara, tamsin), players=(anya,))
    assert [line.line_id for line in heard(staged)] == ["l2"]  # Tamsin is no player: the table never heard l1
    unknown = Take(1, "kept", "generated", (anya, mara, tamsin), lines, on_stage=(anya, mara, tamsin))
    assert heard(unknown) == ()  # a staged take that recorded no players: fail closed
    unstaged = Take(1, "kept", "generated", (anya, mara, tamsin), lines)
    assert len(heard(unstaged)) == 2
