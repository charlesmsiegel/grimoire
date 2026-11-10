"""114's media and sync adapter: the gallery, picture editing, devices, pairing, sync, forks and keep-both
conflicts, over 044's synthetic phone and desktop libraries.

Loopback only; every model call is a scripted fake; every picture is a synthetic byte string.
"""

from __future__ import annotations

import base64
import socket

import pytest
from textapi_fixtures import DESKTOP, PHONE, Device, desktop_library, keep, pair_devices, phone_library, say, serving

from grimoire.core.identity import ImageRef, SourceRef, TurnRef
from grimoire.images import Placement
from grimoire.mediaapi import MediaSyncApi
from grimoire.peersync import read_pairing
from grimoire.store.layout import turn_parts
from grimoire.sync.keeps import kept_slots
from grimoire.textapi import ApiRefused

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR synthetic"
OTHER_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR another synthetic"
THIRD_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR a third synthetic"
MARA_CARD = "card:mara-vell/character/mara-vell"


def _media(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class Both:
    """A device and the media adapter over its text API."""

    def __init__(self, device: Device) -> None:
        self.device = device
        self.media = MediaSyncApi(device.api)

    def __call__(self, op: str, **args):
        if op.startswith(("media.", "sync.")):
            return self.media.call(op, args)
        return self.device(op, **args)


def _refused(both: Both, op: str, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        both(op, **args)
    return caught.value


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def synced(tmp_path):
    """Paired desktop and phone, the phone holding the desktop's library after one sync: (desk, phone, chat)."""
    desk, card_chat = desktop_library(tmp_path / "desk")
    phone = phone_library(tmp_path / "phone")
    pair_devices(desk, phone)
    with Device(desk) as desktop, serving(desktop) as server, Device(phone, peers={DESKTOP: server.address}) as mobile:
        assert mobile("sync", peer=DESKTOP)["status"] == "synced"
    return desk, phone, str(card_chat)


def _mara(both: Both, chat: str) -> str:
    (actor,) = [member["actor"] for member in both("open", chat=chat)["cast"] if member["name"] == "Mara Vell"]
    return actor


def _slot(art: dict, name: str) -> dict:
    (slot,) = [slot for slot in art["slots"] if slot["slot"] == name]
    return slot


def _own(art: dict, name: str) -> dict | None:
    return next((entry for entry in art["own"] if entry["slot"] == name), None)


# -- the proving scenario -------------------------------------------------------------------------------

def test_art_on_a_character_and_its_chat_syncs_and_a_conflict_reports_the_version_not_kept(synced):
    desk, phone, chat = synced
    with Device(desk) as desktop_device:
        desktop = Both(desktop_device)
        mara = _mara(desktop, chat)
        # the character's own picture, in its card source, and the chat actor's own avatar over it
        card = desktop("media.upload", scope="card:mara-vell", media=_media(PNG), note="Mara at the harbor",
                       subject=MARA_CARD, slot="portrait", expected=None)
        assert _slot(card["art"], "portrait")["state"] == "shown"
        assert card["image"]["provenance"]["note"] == "Mara at the harbor"
        avatar = desktop("media.upload", scope="campaign:q-c-1", media=_media(PNG), subject=mara, slot="avatar",
                         expected=None)["art"]
        assert _slot(avatar, "avatar")["layer"] == "campaign"
        with serving(desktop_device) as server, Device(phone, peers={DESKTOP: server.address}) as mobile_device:
            mobile = Both(mobile_device)
            assert mobile("sync.run", peer=DESKTOP)["status"] == "synced"
            for subject, slot in ((MARA_CARD, "portrait"), (mara, "avatar")):
                shown = _slot(mobile("media.art", subject=subject), slot)
                assert mobile_device.api.image(shown["image"]) == ("image/png", PNG)
    # offline, each device puts its own picture in the chat actor's avatar
    with Device(desk) as desktop_device:
        desktop = Both(desktop_device)
        art = desktop("media.art", subject=mara)
        desktop("media.upload", scope="campaign:q-c-1", media=_media(OTHER_PNG), subject=mara, slot="avatar",
                expected=_own(art, "avatar")["etag"])
        with serving(desktop_device) as server, Device(phone, peers={DESKTOP: server.address}) as mobile_device:
            mobile = Both(mobile_device)
            art = mobile("media.art", subject=mara)
            mobile("media.upload", scope="campaign:q-c-1", media=_media(THIRD_PNG), subject=mara, slot="avatar",
                   expected=_own(art, "avatar")["etag"])
            report = mobile("sync.run", peer=DESKTOP)
            assert report["conflicts"], report
            (conflict,) = mobile("sync.conflicts")["conflicts"]
            assert (conflict["kind"], conflict["subject"], conflict["slot"]) == ("placement", mara, "avatar")
            # both candidates are shown, each with the picture it names, both readable here
            (theirs,) = conflict["theirs"]
            shown = {}
            for candidate in (conflict["mine"], theirs):
                image = candidate["preview"]["placement"]["image"]
                shown[candidate["etag"]] = mobile_device.api.image(image)[1]
            assert sorted(shown.values()) == sorted([OTHER_PNG, THIRD_PNG])
            assert conflict["their_deletion"] is False
            # a kept version whose bytes are lost here is never taken for the other device's deletion
            store = mobile_device.api.store
            (slot,) = kept_slots(store, conflict["path"], theirs["etag"])[0]
            kept_file = store.root / slot
            saved = kept_file.read_bytes()
            kept_file.unlink()
            lost = mobile("sync.conflicts")
            assert lost["unknown"] == ["q-c-1"]
            assert not any(item["their_deletion"] for item in lost["conflicts"])
            kept_file.write_bytes(saved)
            assert mobile("sync.conflicts")["unknown"] == []
            # the slot is held until the conflict is resolved: a third version would leave nothing to resolve
            art = mobile("media.art", subject=mara)
            assert art["conflicts"] == ["avatar"]
            error = _refused(mobile, "media.place", subject=mara, slot="avatar", mode="hide",
                             expected=_own(art, "avatar")["etag"])
            assert (error.reason, error.status) == ("art_conflict", 409)
            result = mobile("sync.resolve", peer=DESKTOP, path=conflict["path"], keep="theirs",
                            mine=conflict["mine"]["etag"], theirs=[theirs["etag"]])
            assert result["status"] == "resolved", result
            # the version not kept is reported, with the picture it named
            assert result["kept"] == theirs["etag"]
            (lost,) = result["not_kept"]
            assert (lost["etag"], lost["holder"]) == (conflict["mine"]["etag"], "this device")
            assert mobile_device.api.image(lost["preview"]["placement"]["image"])[1] == THIRD_PNG
            assert mobile("sync.conflicts")["conflicts"] == []
            assert mobile("sync.run", peer=DESKTOP)["conflicts"] == []
            assert mobile_device.api.image(_slot(mobile("media.art", subject=mara), "avatar")["image"])[1] == OTHER_PNG
        assert desktop_device.api.image(_slot(desktop("media.art", subject=mara), "avatar")["image"])[1] == OTHER_PNG


def test_a_chat_forked_offline_lists_its_branches_and_the_choice_reports_the_branch_set_aside(synced):
    desk, phone, chat = synced
    with Device(desk, [say("Desk answer.")]) as desktop_device:
        desktop = Both(desktop_device)
        desktop("continue", chat=chat)
        desktop("post", chat=chat, text="From the desk.")
        desktop("generate", chat=chat)
        keep(desktop_device, chat)
        with serving(desktop_device) as server, \
                Device(phone, [say("Phone answer.")], peers={DESKTOP: server.address}) as mobile_device:
            mobile = Both(mobile_device)
            mobile("continue", chat=chat)
            mobile("post", chat=chat, text="From the phone.")
            mobile("generate", chat=chat)
            keep(mobile_device, chat)
            assert mobile("sync.run", peer=DESKTOP)["status"] == "synced"
            forks = mobile("sync.forks")
            assert forks["more"] is False
            (fork,) = forks["forks"]
            assert fork["chat"] == chat and len(fork["fork"]["branches"]) == 2
            chosen, other = fork["fork"]["branches"]
            result = mobile("sync.choose", chat=chat, turn=chosen["head"])
            assert "forked" not in result["waiting"]
            assert {turn["turn"] for turn in result["set_aside"]} == set(other["turns"])
            # a set-aside turn was heard by no one: a preview of it shows its id, number and device only
            for turn in result["set_aside"]:
                path = "/".join(turn_parts(TurnRef("q-c-1", turn["turn"])))
                preview = mobile.media._candidate(path, (mobile_device.api.store.root / path).read_bytes())["preview"]
                assert preview.get("set_aside") is True, preview
                assert set(preview["turn"]) == {"turn", "number", "device"}
            assert mobile("sync.forks")["forks"] == []


def test_a_turn_in_conflict_is_previewed_as_the_table_reads_it(synced):
    desk, phone, chat = synced
    with Device(desk, [say("Shared question asked.")]) as desktop_device, serving(desktop_device) as server:
        with Device(phone, peers={DESKTOP: server.address}) as mobile_device:
            desktop_device("continue", chat=chat)
            desktop_device("post", chat=chat, text="One question.")
            desktop_device("generate", chat=chat)
            assert mobile_device("sync", peer=DESKTOP)["status"] == "synced"
    with Device(desk) as desktop_device:
        keep(desktop_device, chat)
        with serving(desktop_device) as server, Device(phone, peers={DESKTOP: server.address}) as mobile_device:
            mobile = Both(mobile_device)
            mobile("continue", chat=chat)
            mobile("pass", chat=chat, turn=mobile("open", chat=chat)["state"]["open_turn"])
            assert mobile("sync.run", peer=DESKTOP)["conflicts"]
            (conflict,) = [item for item in mobile("sync.conflicts")["conflicts"] if item["kind"] == "turn"]
            mine, (theirs,) = conflict["mine"]["preview"], conflict["theirs"]
            assert mine["chat"] == chat and mine["turn"]["passed"] is True and mine["turn"]["kept"] is None
            assert theirs["preview"]["turn"]["kept"]["lines"][0]["text"] == "Shared question asked."
            # the table view only: no prompts, intentions or briefs in a preview
            assert set(theirs["preview"]["turn"]) == {"turn", "number", "origin", "device", "post", "kept", "offered",
                                                      "open", "passed", "paused", "set_aside"}
            result = mobile("sync.resolve", peer=DESKTOP, path=conflict["path"], keep="mine",
                            mine=conflict["mine"]["etag"], theirs=[theirs["etag"]])
            assert result["status"] == "resolved", result
            (lost,) = result["not_kept"]
            assert lost["preview"]["turn"]["kept"]["lines"][0]["text"] == "Shared question asked."


# -- the gallery and picture editing ---------------------------------------------------------------------

def test_the_gallery_lists_a_scopes_pictures_with_provenance_and_where_they_are_used(synced):
    _desk, phone, chat = synced
    with Device(phone) as device:
        mobile = Both(device)
        mara = _mara(mobile, chat)
        scopes = mobile("media.scopes")
        assert "campaign:q-c-1" in {item["scope"] for item in scopes["campaigns"]}
        assert {"scope": "card:mara-vell", "title": "Mara Vell", "state": "admitted"} in scopes["sources"]
        uploaded = mobile("media.upload", scope="campaign:q-c-1", media=_media(PNG), note="A lantern sketch")
        image = uploaded["image"]["image"]
        assert "art" not in uploaded
        gallery = mobile("media.gallery", scope="campaign:q-c-1")
        (entry,) = gallery["images"]
        assert (entry["image"], entry["state"], entry["used_by"]) == (image, "readable", [])
        assert (entry["media_type"], entry["size"]) == ("image/png", len(PNG))
        assert entry["provenance"]["note"] == "A lantern sketch"
        assert mara in {item["subject"] for item in gallery["subjects"]}
        # an existing picture reused in another slot
        art = mobile("media.place", subject=mara, slot="portrait", mode="set", image=image, expected=None)
        assert _slot(art, "portrait")["image"] == image
        (entry,) = mobile("media.gallery", scope="campaign:q-c-1")["images"]
        assert entry["used_by"] == [{"subject": mara, "slot": "portrait", "mode": "set"}]


def test_a_refused_placement_leaves_no_uploaded_picture_behind(synced):
    _desk, phone, chat = synced
    with Device(phone) as device:
        mobile = Both(device)
        mara = _mara(mobile, chat)
        mobile("media.upload", scope="campaign:q-c-1", media=_media(PNG), subject=mara, slot="avatar", expected=None)
        # the slot now holds a placement: an upload that read it as empty is refused, and writes nothing
        error = _refused(mobile, "media.upload", scope="campaign:q-c-1", media=_media(OTHER_PNG), subject=mara,
                         slot="avatar", expected=None)
        assert (error.reason, error.status) == ("changed", 409)
        assert len(mobile("media.gallery", scope="campaign:q-c-1")["images"]) == 1
        # a picture of another campaign's scope cannot be placed on this one's actor (038 W7)
        error = _refused(mobile, "media.upload", scope="card:anya-rook", media=_media(OTHER_PNG), subject=mara,
                         slot="portrait", expected=None)
        assert error.reason == "wrong_scope"
        assert mobile("media.gallery", scope="card:anya-rook")["images"] == []


def test_hide_crop_and_inherit_edit_an_actors_slot_without_ever_substituting_a_picture(synced):
    _desk, phone, chat = synced
    with Device(phone) as device:
        mobile = Both(device)
        mara = _mara(mobile, chat)
        art = mobile("media.art", subject=mara)
        assert art["inherits"]["follows"] == "pinned"  # 046: the chat keeps the card's art as it was started
        # the card's own picture was never given with the card: unresolved, not replaced
        avatar = _slot(art, "avatar")
        assert (avatar["state"], avatar["reason"], avatar["origin"]) == ("unavailable", "unresolved", "inherited")
        # a picture added to the card later shows on the card, not on the pinned chat actor
        card = mobile("media.upload", scope="card:mara-vell", media=_media(PNG), subject=MARA_CARD, slot="portrait",
                      expected=None)
        image = _slot(card["art"], "portrait")["image"]
        assert "portrait" not in {slot["slot"] for slot in mobile("media.art", subject=mara)["slots"]}
        # hide the inherited avatar: nothing shows, and nothing stands in for it
        art = mobile("media.place", subject=mara, slot="avatar", mode="hide", expected=None)
        assert _slot(art, "avatar")["state"] == "hidden"
        # inherit drops the chat's own say: the card's unresolved avatar shows as unresolved again
        art = mobile("media.place", subject=mara, slot="avatar", mode="inherit", expected=_own(art, "avatar")["etag"])
        assert (_slot(art, "avatar")["reason"], _slot(art, "avatar")["origin"]) == ("unresolved", "inherited")
        # the card picture, reused by the chat actor (its derivation source, W7), then re-cropped as its own
        art = mobile("media.place", subject=mara, slot="portrait", mode="set", image=image,
                     crop={"x": 0, "y": 0, "w": 5000, "h": 5000}, focus={"x": 2500, "y": 2500}, expected=None)
        portrait = _slot(art, "portrait")
        assert (portrait["image"], portrait["layer"], portrait["crop"]["w"], portrait["focus"]) == \
            (image, "campaign", 5000, {"x": 2500, "y": 2500})
        assert _slot(mobile("media.art", subject=MARA_CARD), "portrait")["crop"] is None


def test_a_standalone_definition_may_only_set_and_every_edit_needs_the_slot_as_read(synced):
    _desk, phone, chat = synced
    with Device(phone) as device:
        mobile = Both(device)
        assert mobile("media.art", subject=MARA_CARD)["modes"] == ["set"]
        card = mobile("media.upload", scope="card:mara-vell", media=_media(PNG), subject=MARA_CARD, slot="gallery1",
                      expected=None)
        etag = _own(card["art"], "gallery1")["etag"]
        error = _refused(mobile, "media.place", subject=MARA_CARD, slot="gallery1", mode="hide", expected=etag)
        assert error.reason == "mode_not_allowed"
        image = _slot(card["art"], "gallery1")["image"]
        assert _refused(mobile, "media.place", subject=MARA_CARD, slot="gallery1", mode="set",
                        image=image).reason == "invalid_field"  # no expected: nobody read the slot
        stale = _refused(mobile, "media.place", subject=MARA_CARD, slot="gallery1", mode="set", image=image,
                         expected="sha256:" + "0" * 64)
        assert (stale.reason, stale.status) == ("changed", 409)
        mara = _mara(mobile, chat)
        assert _refused(mobile, "media.place", subject=mara, slot="expr.Bad", mode="hide",
                        expected=None).reason == "invalid_field"


@pytest.mark.parametrize("op, args, reason", [
    ("media.nothing", {}, "unknown_op"),
    ("media.gallery", {"scope": "not a scope!"}, "invalid_field"),
    ("media.gallery", {"scope": "card:nowhere"}, "missing_source"),
    ("media.art", {"subject": "card:mara-vell/character/mara-vell@r1"}, "invalid_field"),
    ("media.art", {"subject": 3}, "invalid_field"),
    ("media.upload", {"scope": "campaign:q-c-1", "media": "not base64!"}, "invalid_field"),
    ("media.upload", {"scope": "campaign:q-c-1", "media": ""}, "empty"),
    ("media.upload", {"scope": "campaign:q-c-1", "media": _media(b"not a picture")}, "unsupported_media"),
    ("media.upload", {"scope": "campaign:q-c-1", "media": _media(PNG), "note": "x" * 2001}, "invalid_field"),
    ("media.place", {"subject": MARA_CARD, "slot": "avatar", "mode": "unresolved", "expected": None}, "invalid_field"),
    ("media.place", {"subject": MARA_CARD, "slot": "avatar", "mode": "set", "image": "card:mara-vell#image/x",
                     "crop": {"x": 0.5, "y": 0, "w": 1, "h": 1}, "expected": None}, "invalid_field"),
    ("media.place", {"subject": MARA_CARD, "slot": "avatar", "mode": "hide", "focus": {"x": 1, "y": 1},
                     "expected": None}, "invalid_field"),
    ("sync.pair", {"peer": "laptop", "key": "zz"}, "invalid_field"),
    ("sync.pair", {"peer": "laptop", "address": "192.0.2.1:9"}, "invalid_field"),
    ("sync.run", {"peer": "nowhere"}, "unknown_peer"),
    ("sync.resolve", {"peer": DESKTOP, "path": "sources/x.json", "keep": "mine"}, "invalid_field"),
    ("sync.forks", {"limit": 0}, "invalid_field"),
])
def test_malformed_requests_are_refused_and_write_nothing(synced, op, args, reason):
    _desk, phone, _chat = synced
    with Device(phone, peers={DESKTOP: ("127.0.0.1", 9)}) as device:
        mobile = Both(device)
        before = mobile("media.gallery", scope="campaign:q-c-1")
        with pytest.raises(ApiRefused) as caught:
            mobile.media.call(op, args)
        assert caught.value.reason == reason
        assert mobile("media.gallery", scope="campaign:q-c-1") == before


# -- devices, pairing and offline peers -------------------------------------------------------------------

def test_a_new_library_is_named_and_paired_from_the_app_and_then_syncs(tmp_path):
    desk, card_chat = desktop_library(tmp_path / "desk")
    laptop = phone_library(tmp_path / "laptop")
    with Device(desk) as desk_device, Device(laptop) as laptop_device:
        desktop, other = Both(desk_device), Both(laptop_device)
        status = other("sync.devices")
        assert (status["device"], status["device_problem"]) == (None, "unnamed")
        assert status["transport"]["encrypted"] is False and status["unavailable"]
        assert _refused(other, "sync.pair", peer=DESKTOP).reason == "device_identity"
        assert other("sync.identify", device="laptop")["device"] == "laptop"
        assert _refused(other, "sync.identify", device="other").reason == "device_identity"
        desktop("sync.identify", device=DESKTOP)
        shown = desktop("sync.pair", peer="laptop")
        assert len(shown["key"]) == 64 and shown["device"] == DESKTOP
        # pairing a paired device again would end its key: it must be asked for
        assert _refused(desktop, "sync.pair", peer="laptop").reason == "already_paired"
        # the desktop never dials the laptop, which dials it: still listed, with no address
        assert desktop("sync.devices")["peers"] == [{"peer": "laptop", "address": None, "paired": True, "last": None}]
        with serving(desk_device) as server:
            host, port = server.address
            paired = other("sync.pair", peer=DESKTOP, key=shown["key"], address=f"{host}:{port}")
            assert paired["key"] is None and read_pairing(laptop_device.api.store, DESKTOP) is not None
            assert other("sync.devices")["peers"] == [{"peer": DESKTOP, "address": f"{host}:{port}", "paired": True,
                                                       "last": None}]
            assert other("sync.run", peer=DESKTOP)["status"] == "synced"
            assert other("chats")["chats"] and str(card_chat) in {item["chat"] for item in other("chats")["chats"]}
            assert other("sync.devices")["peers"][0]["last"]["status"] == "synced"
        # after the app restarts, the device it synced with is still listed
        assert [peer["peer"] for peer in Both(desk_device)("sync.devices")["peers"]] == ["laptop"]


def test_an_unreachable_peer_leaves_every_local_picture_and_chat_in_place(synced):
    _desk, phone, chat = synced
    with Device(phone, peers={DESKTOP: ("127.0.0.1", _free_port())}) as device:
        mobile = Both(device)
        mara = _mara(mobile, chat)
        mobile("media.upload", scope="campaign:q-c-1", media=_media(PNG), subject=mara, slot="avatar", expected=None)
        before = (mobile("media.gallery", scope="campaign:q-c-1"), mobile("chats"))
        report = mobile("sync.run", peer=DESKTOP)
        assert (report["status"], report["code"]) == ("incomplete", "unreachable")
        assert (mobile("media.gallery", scope="campaign:q-c-1"), mobile("chats")) == before
        assert mobile("sync.devices")["peers"][0]["last"]["code"] == "unreachable"
        # play goes on offline
        mobile("continue", chat=chat)
        assert mobile("post", chat=chat, text="Still here.")["turns"]


def test_the_phone_and_desktop_need_one_owner_for_one_table(synced):
    """Pairing is granted by the owner the app plays as: a request to pair is never anonymous."""
    _desk, phone, _chat = synced
    with Device(phone) as device:
        mobile = Both(device)
        assert mobile("sync.devices")["owner"] == device.api.owner
        assert PHONE == mobile("sync.devices")["device"]


def test_keeping_mine_over_a_peers_deletion_reports_the_deletion_not_kept(synced, monkeypatch):
    _desk, phone, _chat = synced
    with Device(phone) as device:
        media = MediaSyncApi(device.api)
        path = "campaigns/q-c-1/placements/x.json"
        mine = {"etag": "sha256:" + "1" * 64, "deleted": False, "size": 3, "preview": None}
        monkeypatch.setattr(media, "_campaign_conflicts",
                            lambda campaign: ([{"path": path, "campaign": campaign, "kind": "placement",
                                                "mine": mine, "theirs": [], "their_deletion": True}], True))
        monkeypatch.setattr(device.api, "call", lambda op, args: {"status": "resolved", "path": path})
        result = media.call("sync.resolve", {"peer": DESKTOP, "path": path, "keep": "mine", "mine": mine["etag"],
                                             "theirs": []})
        assert result["kept"] == mine["etag"]
        assert result["not_kept"] == [{"etag": None, "deleted": True, "unreadable": False, "preview": None,
                                       "holder": "another device"}]


def test_placing_another_picture_over_an_imported_one_says_it_was_placed_here(synced):
    _desk, phone, _chat = synced
    with Device(phone) as device:
        mobile = Both(device)
        first = mobile("media.upload", scope="card:mara-vell", media=_media(PNG))["image"]["image"]
        second = mobile("media.upload", scope="card:mara-vell", media=_media(OTHER_PNG))["image"]["image"]
        batch = device.api.images.change()
        batch.place(SourceRef.parse(MARA_CARD), "gallery2",
                    Placement("set", "gallery", ImageRef.parse(first),
                              provenance={"origin": "an importer", "note": "from the card", "legacy_id": "v1:arch:assets/x.json"}), None)
        batch.commit()
        art = mobile("media.art", subject=MARA_CARD)
        art = mobile("media.place", subject=MARA_CARD, slot="gallery2", mode="set", image=first,
                     crop={"x": 0, "y": 0, "w": 5000, "h": 5000}, expected=_own(art, "gallery2")["etag"])
        assert _own(art, "gallery2")["provenance"]["note"] == "from the card"  # a re-crop keeps its record
        art = mobile("media.place", subject=MARA_CARD, slot="gallery2", mode="set", image=second,
                     expected=_own(art, "gallery2")["etag"])
        provenance = _own(art, "gallery2")["provenance"]
        assert (provenance["origin"], provenance["note"], provenance["legacy_id"]) == \
            ("placed in the app", None, "v1:arch:assets/x.json")
