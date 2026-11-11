"""The inbox feed (``inbox.list``): everything that waits on the person, gathered from the services that own it.

Every scenario runs through the app's own ops over synthetic libraries: an empty inbox, one item of each kind
(a fork, a keep-both conflict, a labelling to confirm, a change waiting on the player's key, a reply cut off), the
order they are listed in, the cap on the chats opened, a service that refuses, and that nothing the director
alone may read is in an item. Each item's action is then made as the item names it (the existing op with the
arguments given), and the item is gone: deciding in the inbox is the same write as deciding where it lives.

The page itself runs end to end under Node (``js/inbox.e2e.mjs``) over a library with four things waiting, and
what it decided is then read back from the library.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from store_helpers import OWNER
from test_campaign_page import cast_of, kept_turn, labelled_action
from textapi_fixtures import DESKTOP, Device, desktop_library, keep, pair_devices, phone_library, say, serving

from grimoire.app import AppServer
from grimoire.app.adapters import inbox
from grimoire.app.adapters.inbox import DEFAULT_CHATS, KINDS, conflict_actions
from grimoire.app.appapi import AppApi
from grimoire.app.pages import load_pages
from grimoire.app.server import STATIC
from grimoire.campaignapi import CampaignApi
from grimoire.mediaapi import MediaSyncApi
from grimoire.scene import SceneRefused, Take
from grimoire.textapi import ApiRefused

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR synthetic"
OTHER_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR another synthetic"
SECRET = "The harbourmaster forged the ledger"
NODE = shutil.which("node")
ITEM_KEYS = {"id", "kind", "label", "title", "where", "detail", "notes", "actions", "route", "order"}


@pytest.fixture
def desk(tmp_path):
    return desktop_library(tmp_path / "desk")


@pytest.fixture
def synced(tmp_path):
    """Paired desktop and phone, the phone holding the desktop's library after one sync: (desk, phone, chat)."""
    desk, card_chat = desktop_library(tmp_path / "desk")
    phone = phone_library(tmp_path / "phone")
    pair_devices(desk, phone)
    with Device(desk) as desktop, serving(desktop) as server, Device(phone, peers={DESKTOP: server.address}) as mobile:
        assert mobile("sync", peer=DESKTOP)["status"] == "synced"
    return desk, phone, str(card_chat)


def listed(app: AppApi, **args) -> dict:
    answer = app.call("inbox.list", args)
    assert [item["order"] for item in answer["items"]] == list(range(len(answer["items"])))
    assert answer["count"] == len(answer["items"])
    for item in answer["items"]:
        assert ITEM_KEYS <= set(item) and item["label"] == KINDS[item["kind"]]
        assert set(item["route"]) == {"route", "path", "query", "label"}
        for action in item["actions"]:
            assert set(action) == {"id", "label", "op", "args", "confirm", "done", "kind", "quick"}
            assert action["op"] in app.ops, "an item is decided by an op the app already serves"
    return answer


def kinds(answer: dict) -> list[str]:
    return [item["kind"] for item in answer["items"]]


def do(app: AppApi, item: dict, label: str) -> dict:
    """Decide ``item`` as its action says: the op it names, with exactly the arguments it gives."""
    (action,) = [action for action in item["actions"] if action["label"] == label]
    return app.call(action["op"], action["args"])


def test_the_op_is_served_and_the_page_needs_only_it():
    (page,) = [page for page in load_pages(STATIC) if page.id == "inbox"]
    assert (page.route, page.title, page.slot, page.entry) == ("inbox", "Needs you", "play", "page.js")
    assert page.requires == ("inbox.list",)
    ordered = [found.id for found in load_pages(STATIC) if found.slot in ("home", "play")]
    assert ordered.index("home") < ordered.index("inbox") < ordered.index("chats")


def test_a_library_with_nothing_waiting_lists_nothing_and_writes_nothing(desk):
    folder, _chat = desk
    with Device(folder) as device:
        app = AppApi(device.api)
        assert "inbox.list" in app.call("library")["ops"]
        before = sorted((path.relative_to(folder).as_posix(), path.stat().st_mtime_ns)
                        for path in folder.rglob("*") if path.is_file())
        answer = listed(app)
        assert answer == {"items": [], "count": 0, "complete": True, "problems": [], "scope": "library",
                          "inspected": {"chats": 2, "limit": DEFAULT_CHATS, "more_chats": False, "more_items": False}}
        after = sorted((path.relative_to(folder).as_posix(), path.stat().st_mtime_ns)
                       for path in folder.rglob("*") if path.is_file())
        assert after == before, "the inbox is a view: reading it writes no file"


def test_a_labelling_and_a_change_wait_on_the_player_and_are_decided_by_the_hubs_own_ops(desk):
    folder, chat = desk
    with Device(folder, [say("Mara pockets it."), say("Mara shrugs.")]) as device:
        app = AppApi(device.api)
        first = kept_turn(device, chat)
        device("post", chat=str(chat), text="*I press another coin into Mara's hand*")
        device("generate", chat=str(chat))
        turn = keep(device, str(chat))["turns"][-1]["turn"]
        names = cast_of(app.call("campaign.hub", {"chat": str(chat)}))
        anya, mara = names["Anya Rook"], names["Mara Vell"]
        # a change no character of the player's acts in is the director's alone: the table never sees it
        app.call("campaign.propose_effect", {"chat": str(chat), "audience": "director", "turn": first, "summary": SECRET,
                                             "operations": [{"kind": "begin", "predicate": "knows", "subject": mara,
                                                             "object": anya}]})
        backed = {"kind": "begin", "predicate": "knows", "subject": mara, "object": anya, "agent": anya, "backed_by": turn}
        proposed = app.call("campaign.propose_effect", {"chat": str(chat), "audience": "director", "turn": turn,
                                                        "summary": "Anya pays Mara a coin.", "operations": [backed]})
    labelled_action(folder, chat)
    with Device(folder, [say("Mara pockets the coins.")]) as device:
        app = AppApi(device.api)
        answer = listed(app)
        assert kinds(answer) == ["action", "approval"], "an action to confirm comes before a change to approve"
        action, approval = answer["items"]

        assert action["id"] == f"label:{chat}:{action['action']['turn']}"
        assert action["title"] == "I press five coins into Mara's hand"
        assert "Labelled" in action["detail"] and "Get into the cargo hold." in action["detail"]
        assert action["where"]["chat"] == str(chat) and "turn 3" in action["where"]["text"]
        assert action["route"] == {"route": "campaign", "path": [chat.campaign_id], "query": {"chat": str(chat)},
                                   "label": "Open where it happened"}
        (confirm,) = action["actions"]
        assert (confirm["op"], confirm["args"]) == ("campaign.confirm", {"chat": str(chat), "audience": "table"})
        # a labelling with nothing more to read may be confirmed from the chat's tray; a change never is,
        # because its operations are read in full first
        assert confirm["quick"] is not bool(action["action"]["label"]["factors"] or action["action"]["label"]["stakes"])

        effect = proposed["done"]["effect"]
        assert approval["id"] == f"effect:{chat}:{turn}:1" and approval["title"] == "Anya pays Mara a coin."
        (endorse,) = approval["actions"]
        assert endorse["op"] == "campaign.endorse_effect" and endorse["quick"] is False
        assert endorse["args"] == {"chat": str(chat), "audience": "table", "turn": turn, "number": 1,
                                   "fingerprint": effect["fingerprint"]}
        assert endorse["confirm"]["message"] == "Give your key to effect 1, exactly as shown: Anya pays Mara a coin."
        assert approval["effect"]["effect"]["operations"] == effect["operations"], "shown in full before the key is given"

        # nothing the director alone reads is in the feed: not the other effect, world truth, goals or intentions
        text = json.dumps(answer)
        assert SECRET not in text
        director = app.call("campaign.hub", {"chat": str(chat), "audience": "director"})
        assert SECRET in json.dumps(director), "the director's hub does hold it"
        for key in ('"truth"', '"goals"', '"intentions"'):
            assert key not in text

        # the tray's view of one chat: the same items, and no other chat opened
        only = listed(app, chat=str(chat))
        assert (only["scope"], only["inspected"]["chats"]) == ("chat", 1)
        assert [item["id"] for item in only["items"]] == [action["id"], approval["id"]]

        # deciding is the hub's own write: a stale fingerprint is refused there exactly as it is on the hub
        with pytest.raises(ApiRefused) as caught:
            app.call(endorse["op"], {**endorse["args"], "fingerprint": "0" * 64})
        assert caught.value.reason == "fingerprint_mismatch"
        assert kinds(listed(app)) == ["action", "approval"]
        do(app, approval, "Endorse as shown")
        assert kinds(listed(app)) == ["action"], "a key given is no longer waited on (the director's is not the table's)"
        do(app, action, "Confirm the labelling")
        assert listed(app)["items"] == []


def test_a_paused_chat_says_why_its_items_cannot_be_decided(desk):
    folder, chat = desk
    labelled_action(folder, chat)
    with Device(folder) as device:
        app = AppApi(device.api)
        device("pause", chat=str(chat))
        (item,) = listed(app)["items"]
        assert item["kind"] == "action" and item["actions"] == []
        assert item["notes"] == ["Confirming the labelling is not available now: play is paused."]


def _upload(media: MediaSyncApi, subject: str, data: bytes) -> None:
    art = media.call("media.art", {"subject": subject})
    own = next((entry for entry in art["own"] if entry["slot"] == "avatar"), None)
    media.call("media.upload", {"scope": "campaign:q-c-1", "media": base64.b64encode(data).decode("ascii"),
                                "subject": subject, "slot": "avatar", "expected": own and own["etag"]})


def test_a_fork_and_a_conflict_are_listed_first_and_decided_by_the_sync_ops(synced):
    desk, phone, chat = synced
    with Device(desk, [say("Desk answer.")]) as desktop:
        (mara,) = [member["actor"] for member in desktop("open", chat=chat)["cast"] if member["name"] == "Mara Vell"]
        _upload(MediaSyncApi(desktop.api), mara, PNG)
        desktop("continue", chat=chat)
        desktop("post", chat=chat, text="From the desk.")
        desktop("generate", chat=chat)
        keep(desktop, chat)
        with serving(desktop) as server, Device(phone, [say("Phone answer.")], peers={DESKTOP: server.address}) as mobile:
            app = AppApi(mobile.api)
            _upload(MediaSyncApi(mobile.api), mara, OTHER_PNG)
            mobile("continue", chat=chat)
            mobile("post", chat=chat, text="From the phone.")
            mobile("generate", chat=chat)
            keep(mobile, chat)
            assert app.call("media.sync", {"peer": DESKTOP})["conflicts"]

            answer = listed(app)
            assert kinds(answer) == ["fork", "conflict"]
            fork, conflict = answer["items"]

            # the fork is exactly what media.forks lists, one action for each branch
            (theirs,) = app.call("media.forks", {})["forks"]
            assert fork["fork"] == theirs and fork["id"] == f"fork:{chat}"
            assert fork["where"]["devices"] == [DESKTOP, "this device"], "a branch is named by its device, not its tag"
            assert sorted(action["label"] for action in fork["actions"]) == ["Go on with desktop's", "Go on with this device's"]
            assert {action["args"]["turn"] for action in fork["actions"]} == \
                {branch["head"] for branch in theirs["fork"]["branches"]}
            assert all(action["op"] == "media.choose" and action["confirm"]["label"] == "Go on with it"
                       for action in fork["actions"])
            assert fork["route"]["route"] == "chat" and fork["route"]["path"] == [chat]

            # the conflict is exactly what media.conflicts lists, kept by etag
            (theirs,) = app.call("media.conflicts", {})["conflicts"]
            assert conflict["conflict"] == theirs and conflict["id"] == "conflict:" + theirs["path"]
            assert conflict["title"] == "Mara Vell's avatar picture changed on more than one device"
            assert conflict["where"]["devices"] == [DESKTOP]
            assert conflict["route"] == {"route": "art", "path": [], "query": {"subject": mara},
                                         "label": "Open where it happened"}
            assert [action["label"] for action in conflict["actions"]] == ["Keep this device's", "Keep desktop's"]
            mine, other = conflict["actions"]
            etags = {"mine": theirs["mine"]["etag"], "theirs": [theirs["theirs"][0]["etag"]]}
            assert mine["args"] == {"peer": DESKTOP, "path": theirs["path"], "keep": "mine", **etags}
            assert other["args"] == {"peer": DESKTOP, "path": theirs["path"], "keep": etags["theirs"][0], **etags}
            assert "set aside here and on desktop" in mine["confirm"]["message"]
            assert not any(action["quick"] for action in conflict["actions"]), "the versions are read first"

            # a chat's own view lists its fork and leaves the library's sync conflicts out
            assert kinds(listed(app, chat=chat)) == ["fork"]

            # a stale etag is refused by the same op, and nothing is decided
            with pytest.raises(ApiRefused):
                app.call(mine["op"], {**mine["args"], "mine": "0" * 64})
            assert kinds(listed(app)) == ["fork", "conflict"]

            resolved = do(app, conflict, "Keep desktop's")
            assert resolved["status"] == "resolved" and resolved["not_kept"][0]["holder"] == "this device"
            assert kinds(listed(app)) == ["fork"]
            chosen = do(app, fork, "Go on with this device's")
            assert "forked" not in chosen["waiting"] and chosen["set_aside"]
            assert listed(app)["items"] == []


def test_a_conflict_with_a_device_this_one_cannot_dial_is_listed_with_no_way_to_keep(synced):
    desk, phone, chat = synced
    with Device(desk) as desktop:
        (mara,) = [member["actor"] for member in desktop("open", chat=chat)["cast"] if member["name"] == "Mara Vell"]
        _upload(MediaSyncApi(desktop.api), mara, PNG)
        with serving(desktop) as server, Device(phone, peers={DESKTOP: server.address}) as mobile:
            _upload(MediaSyncApi(mobile.api), mara, OTHER_PNG)
            assert mobile("sync", peer=DESKTOP)["conflicts"]
    with Device(phone) as mobile:  # no address for the desktop now
        (item,) = listed(AppApi(mobile.api))["items"]
        assert item["kind"] == "conflict" and item["actions"] == []
        assert item["notes"] == ["Both devices must be reachable: give desktop's address under Pair a device, on "
                                 "Devices and sync, to resolve it."]


# -- which versions of a conflict may be kept (the rules the Devices and sync page had) --------------------

MINE, PHONE_TAG, TABLET_TAG = "1" * 64, "2" * 64, "3" * 64
PATH = "campaigns/mara-vell/art/portrait.json"


def _version(tag: str, **more) -> dict:
    return {"etag": tag, "deleted": False, "preview": {"placement": {"mode": "set", "image": None}}, **more}


THREE = {"path": PATH, "kind": "placement", "campaign": "mara-vell", "mine": _version(MINE),
         "theirs": [_version(PHONE_TAG, peers=["phone"]), _version(TABLET_TAG, peers=["tablet"])],
         "peers": [{"peer": "phone", "versions": [PHONE_TAG], "deleted": False},
                   {"peer": "tablet", "versions": [TABLET_TAG], "deleted": False},
                   {"peer": "watch", "versions": [], "deleted": True}]}
ALL = frozenset({"phone", "tablet", "watch"})


def labels(actions: list[dict]) -> list[str]:
    return [action["label"] for action in actions]


def test_with_several_other_versions_this_devices_is_not_offered_and_a_deletion_waits():
    actions, notes = conflict_actions(THREE, ALL)
    assert labels(actions) == ["Keep phone's", "Keep tablet's"]
    assert all(action["args"]["mine"] == MINE and action["args"]["theirs"] == [PHONE_TAG, TABLET_TAG]
               for action in actions)
    assert [action["args"]["keep"] for action in actions] == [PHONE_TAG, TABLET_TAG]
    assert any("can be kept only on phone" in note for note in notes)
    assert any(note.startswith("watch deleted it. Resolve the other devices' versions first") for note in notes)


def test_with_one_other_version_left_this_devices_can_be_kept_and_the_deletion_still_waits():
    left = {**THREE, "mine": _version(PHONE_TAG), "theirs": [THREE["theirs"][1]], "peers": THREE["peers"][1:]}
    actions, notes = conflict_actions(left, ALL)
    assert labels(actions) == ["Keep this device's (with tablet)", "Keep tablet's"]
    assert [action["args"]["peer"] for action in actions] == ["tablet", "tablet"]
    assert [note for note in notes if note.startswith("watch deleted it")]


def test_a_deletion_alone_is_accepted_or_this_devices_version_kept_over_it():
    gone = {**THREE, "theirs": [], "peers": [THREE["peers"][2]]}
    keep_mine, accept = conflict_actions(gone, ALL)[0]
    assert (keep_mine["label"], keep_mine["args"]["keep"]) == ("Keep this device's", "mine")
    assert (accept["label"], accept["args"]["keep"], accept["kind"]) == ("Accept watch's deletion", "theirs", "danger")
    assert accept["args"]["theirs"] == [] and accept["done"] == "Kept watch's deletion"
    deleted_here = {**THREE, "mine": {"etag": None, "deleted": True, "preview": None},
                    "theirs": [THREE["theirs"][0]], "peers": [THREE["peers"][0]]}
    assert labels(conflict_actions(deleted_here, ALL)[0]) == ["Keep this device's deletion", "Keep phone's"]


def test_a_conflict_whose_devices_cannot_be_read_or_dialed_offers_nothing_and_says_why():
    assert conflict_actions({**THREE, "peers": []}, ALL) == \
        ([], ["Which device this is with cannot be read here, so it cannot be resolved yet."])
    actions, notes = conflict_actions(THREE, frozenset({"tablet"}))
    assert labels(actions) == ["Keep tablet's"]
    assert sum("Both devices must be reachable" in note for note in notes) == 2  # the phone and the watch


# -- a reply that did not arrive, the cap, and a service that refuses ---------------------------------------

def test_a_reply_cut_off_is_listed_with_the_chats_own_ways_on(desk):
    folder, chat = desk
    with Device(folder) as device:
        app = AppApi(device.api)
        device.api.runner.seat(chat, OWNER)
        device("post", chat=str(chat), text="Anyone there?")
        scene = device.api.runner.open(chat)
        last = scene.turns.last
        crashed = dataclasses.replace(last.record, takes=(Take(1, "running", "generated", scene.cast, by=OWNER),))
        device.api.runner.scenes.write(crashed, last)
        assert "interrupted" in device("open", chat=str(chat))["state"]["waiting"]

        (item,) = listed(app)["items"]
        assert (item["kind"], item["title"]) == ("reply", "A reply stopped before it finished")
        assert item["id"] == f"reply:{chat}:{crashed.turn_id}" and item["route"]["route"] == "chat"
        assert [(action["label"], action["op"], action["args"]) for action in item["actions"]] == [
            ("Ask again", "generate", {"chat": str(chat)}),
            ("Pass the turn", "pass", {"chat": str(chat), "turn": crashed.turn_id})]
        do(app, item, "Pass the turn")
        assert listed(app)["items"] == []
    with Device(folder, runtime=False) as device:  # no model: asking again is not offered
        device("post", chat=str(chat), text="Still there?")
        scene = device.api.runner.open(chat)
        last = scene.turns.last
        device.api.runner.scenes.write(
            dataclasses.replace(last.record, takes=(Take(1, "running", "generated", scene.cast, by=OWNER),)), last)
        (item,) = listed(AppApi(device.api))["items"]
        assert [action["label"] for action in item["actions"]] == ["Pass the turn"]


def test_only_so_many_chats_are_opened_and_the_answer_says_when_it_stopped_early(desk):
    folder, chat = desk
    labelled_action(folder, chat)
    with Device(folder) as device:
        app = AppApi(device.api)
        recent = [entry["chat"] for entry in device("chats", limit=2)["chats"]]
        assert len(recent) == 2
        one = listed(app, limit=1)
        assert (one["complete"], one["inspected"]) == (False, {"chats": 1, "limit": 1, "more_chats": True,
                                                              "more_items": False})
        # the chat played last is the one opened
        assert kinds(one) == (["action"] if recent[0] == str(chat) else [])
        both = listed(app, limit=2)
        assert (both["complete"], both["inspected"]["chats"], both["inspected"]["more_chats"]) == (True, 2, False)
        assert kinds(both) == ["action"]
        for bad in (0, 101, True, "2", 1.5):
            with pytest.raises(ApiRefused) as caught:
                app.call("inbox.list", {"limit": bad})
            assert caught.value.reason == "invalid_field"
        with pytest.raises(ApiRefused) as caught:
            app.call("inbox.list", {"chat": "not a chat"})
        assert caught.value.reason == "invalid_field"
        with pytest.raises(ApiRefused) as caught:
            app.call("inbox.list", {"chat": f"campaign:{chat.campaign_id}#chat/no-such-chat"})
        assert (caught.value.reason, caught.value.status) == ("no_chat", 404)


def test_a_list_cut_at_the_most_items_says_it_is_not_complete(desk, monkeypatch):
    folder, chat = desk
    labelled_action(folder, chat)
    with Device(folder) as device:
        monkeypatch.setattr(inbox, "MAX_ITEMS", 0)
        answer = listed(AppApi(device.api))
        assert (answer["items"], answer["complete"], answer["inspected"]["more_items"]) == ([], False, True)


def test_a_service_that_refuses_is_named_and_the_rest_is_still_listed(desk, monkeypatch):
    folder, chat = desk
    labelled_action(folder, chat)
    with Device(folder) as device:
        app = AppApi(device.api)
        assert kinds(listed(app)) == ["action"]
        real = MediaSyncApi.call

        def no_conflicts(self, op, args=None):  # type: ignore[no-untyped-def]
            if op == "sync.conflicts":
                raise ApiRefused("device_identity", "this device's sync records cannot be read")
            return real(self, op, args)

        monkeypatch.setattr(MediaSyncApi, "call", no_conflicts)
        answer = listed(app)
        assert kinds(answer) == ["action"] and answer["complete"] is False
        assert answer["problems"] == [{"source": "sync", "code": "device_identity"}]
        monkeypatch.setattr(MediaSyncApi, "call", real)

        real_hub = CampaignApi.call

        def no_hub(self, op, args=None):  # type: ignore[no-untyped-def]
            raise ApiRefused("knowledge_unreadable", "the campaign cannot be read")

        monkeypatch.setattr(CampaignApi, "call", no_hub)
        answer = listed(app)
        assert answer["items"] == [] and answer["complete"] is False
        # only the chat whose turns show something waiting is asked for its hub, and it is named by its ref
        assert answer["problems"] == [{"source": "campaign", "chat": str(chat), "code": "knowledge_unreadable"}]
        monkeypatch.setattr(CampaignApi, "call", real_hub)

        # a chat that cannot be opened is named, and the other chat's items are still listed
        other = next(entry["chat"] for entry in device("chats", limit=2)["chats"] if entry["chat"] != str(chat))
        opened = device.api.runner.open

        def unreadable(ref):  # type: ignore[no-untyped-def]
            if str(ref) == other:
                raise SceneRefused("no_chat", "it cannot be read")
            return opened(ref)

        monkeypatch.setattr(device.api.runner, "open", unreadable)
        answer = listed(app)
        assert kinds(answer) == ["action"] and answer["complete"] is False
        assert answer["problems"] == [{"source": "chat", "chat": other, "code": "no_chat"}]


# -- the page, end to end ---------------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_inbox_page_end_to_end(synced):
    """The phone's library with a fork, a picture in conflict, a labelling to confirm and a change to endorse,
    served with the desktop reachable; then what the page decided, read back from the library."""
    desk, phone, chat = synced
    with Device(desk, [say("Mara pockets it."), say("Mara shrugs.")]) as desktop:
        app = AppApi(desktop.api)
        chat_ref = desktop.api.chats.navigation().recent(5)
        (card,) = [ref for ref in chat_ref if str(ref) == chat]
        kept_turn(desktop, card)
        desktop("post", chat=chat, text="*I press another coin into Mara's hand*")
        desktop("generate", chat=chat)
        turn = keep(desktop, chat)["turns"][-1]["turn"]
        names = cast_of(app.call("campaign.hub", {"chat": chat}))
        anya, mara = names["Anya Rook"], names["Mara Vell"]
        backed = {"kind": "begin", "predicate": "knows", "subject": mara, "object": anya, "agent": anya, "backed_by": turn}
        app.call("campaign.propose_effect", {"chat": chat, "audience": "director", "turn": turn,
                                             "summary": "Anya pays Mara a coin.", "operations": [backed]})
        with serving(desktop) as server, Device(phone, peers={DESKTOP: server.address}) as mobile:
            assert mobile("sync", peer=DESKTOP)["status"] == "synced"
    with Device(desk, [say("Desk answer.")]) as desktop:
        (other,) = [entry["chat"] for entry in desktop("chats", limit=5)["chats"] if entry["chat"] != chat]
        _upload(MediaSyncApi(desktop.api), mara, PNG)
        desktop("continue", chat=other)
        desktop("post", chat=other, text="From the desk.")
        desktop("generate", chat=other)
        keep(desktop, other)
        with serving(desktop) as server, Device(phone, [say("Phone answer.")], peers={DESKTOP: server.address}) as mobile:
            _upload(MediaSyncApi(mobile.api), mara, OTHER_PNG)
            mobile("continue", chat=other)
            mobile("post", chat=other, text="From the phone.")
            mobile("generate", chat=other)
            keep(mobile, other)
            assert mobile("sync", peer=DESKTOP)["conflicts"]
    labelled_action(phone, card)
    with Device(desk) as desktop, serving(desktop) as server, Device(phone, peers={DESKTOP: server.address}) as mobile:
        api = AppApi(mobile.api)
        assert kinds(listed(api)) == ["fork", "conflict", "action", "approval"]
        app = AppServer(mobile.api)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            env = {**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token,
                   "GRIMOIRE_CHAT": chat, "GRIMOIRE_PEER": DESKTOP}
            result = subprocess.run([NODE, "--test", "--test-reporter=tap", str(Path(__file__).with_name("js") / "inbox.e2e.mjs")],
                                    capture_output=True, text=True, timeout=240, check=False, env=env)
        finally:
            app.close()
            thread.join(10)
        assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
        assert "# pass 1" in result.stdout
        # what the page decided is in the library, written by the ops that own each record
        assert listed(api)["items"] == []
        assert api.call("media.conflicts", {})["conflicts"] == [] and api.call("media.forks", {})["forks"] == []
        art = api.call("media.art", {"subject": mara})
        (avatar,) = [slot for slot in art["slots"] if slot["slot"] == "avatar"]
        assert mobile.api.image(avatar["image"]) == ("image/png", PNG), "the desktop's picture was the one kept"
        hub = api.call("campaign.hub", {"chat": chat})
        (effect,) = [item for record in hub["turns"] for item in record["effects"]]
        assert effect["endorsers"][0]["given"] is True and effect["approved_by"] is None, "the player's key only"
        assert hub["open_action"]["framed"] is not None or hub["open_action"]["outcome"] is not None
