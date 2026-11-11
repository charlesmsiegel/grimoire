"""The shelf's adapter ops (docs/SHELF.md) over a synthetic library: tags and pins on entries, pins on chats and
saved filters, each refusal, and what the rest of the library sees of them (nothing: no revision, no chat edit).
"""

from __future__ import annotations

import json

import pytest
from textapi_fixtures import DESKTOP, Device, desktop_library, pair_devices, phone_library, serving

from grimoire.app.adapters.shelf import ADAPTER, WRITES
from grimoire.app.appapi import AppApi
from grimoire.core.identity import SourceRef
from grimoire.shelf import record_name
from grimoire.textapi import ApiRefused

MARA = "card:mara-vell/character/mara-vell"
LORE = "card:mara-vell/lorebook/mara-vell-lore"
GONE = "world:nowhere/character/nobody"


@pytest.fixture
def app(tmp_path):
    desk, chat = desktop_library(tmp_path / "desk")
    with Device(desk) as opened:
        yield AppApi(opened.api), desk, str(chat)


def _refused(api: AppApi, op: str, args: dict, reason: str, status: int) -> ApiRefused:
    with pytest.raises(ApiRefused) as refused:
        api.call(op, args)
    assert (refused.value.reason, refused.value.status) == (reason, status), str(refused.value)
    return refused.value


def _entry(api: AppApi, ref: str) -> dict | None:
    return next((item for item in api.call("shelf.read", {})["entries"] if item["ref"] == ref), None)


def test_the_shelf_ops_are_served_and_an_untouched_library_has_an_empty_shelf(app):
    api, desk, _chat = app
    assert {"shelf.read", *("shelf." + name for name in WRITES)} <= set(api.ops) and ADAPTER.name == "shelf"
    read = api.call("shelf.read", {})
    assert (read["entries"], read["chats"], read["filters"], read["tags"], read["problems"]) == ([], [], [], [], [])
    assert read["limits"] == {"tag_chars": 40, "tags": 32, "filter_name_chars": 60, "query_chars": 200, "filters": 200}
    assert not (desk / "config").exists()  # reading makes nothing


def test_tags_and_a_pin_are_about_the_entry_and_make_no_revision_of_it(app):
    api, desk, _chat = app
    before = api.call("sources.read", {"ref": MARA})
    tagged = api.call("shelf.set_tags", {"ref": MARA, "tags": ["  slow  burn ", "Crew"], "expected": None})
    assert tagged["tags"] == ["Crew", "slow burn"] and tagged["pinned"] is False
    pinned = api.call("shelf.pin_entry", {"ref": MARA, "pinned": True, "expected": tagged["etag"]})
    assert pinned["tags"] == ["Crew", "slow burn"] and pinned["pinned"] is True
    after = api.call("sources.read", {"ref": MARA})
    for key in ("revisions", "head", "etag", "manifest_etag", "payload"):
        assert after[key] == before[key], key
    read = api.call("shelf.read", {})
    assert read["entries"] == [pinned] and read["tags"] == [{"tag": "Crew", "entries": 1}, {"tag": "slow burn", "entries": 1}]
    # one small file, named by a digest of the exact ref, in the library's own config folder
    path = desk / "config" / "shelf" / "entries" / (record_name(SourceRef.parse(MARA)) + ".json")
    assert json.loads(path.read_text(encoding="utf-8"))["ref"] == MARA
    # the library itself reads as before: the shelf is no source, no campaign and no load problem
    assert api.api.store.load().problems == ()


def test_marks_outlive_a_new_revision_of_the_entry(app):
    api, _desk, _chat = app
    tagged = api.call("shelf.set_tags", {"ref": LORE, "tags": ["harbor"], "expected": None})
    read = api.call("sources.read", {"ref": LORE})
    api.call("sources.save", {"ref": read["entity"], "head": read["head"], "etag": read["etag"],
                              "expected": read["manifest_etag"], "fields": {"name": "Harbor lore"}})
    assert api.call("sources.read", {"ref": LORE})["revisions"] == ["r1", "r2"]
    assert _entry(api, LORE) == tagged  # same tags, same etag: the update did not touch them


def test_a_pinned_chat_is_noted_beside_the_chat_and_its_record_is_untouched(app):
    api, _desk, chat = app
    before = {item["chat"]: item["etag"] for item in api.call("sources.chats", {})["chats"]}
    bound = lambda: next(item["sources"] for item in api.call("chats", {"limit": 10})["chats"] if item["chat"] == chat)  # noqa: E731
    sources = bound()
    pinned = api.call("shelf.pin_chat", {"chat": chat, "pinned": True, "expected": None})
    assert pinned["chat"] == chat and pinned["pinned"] is True
    assert {item["chat"]: item["etag"] for item in api.call("sources.chats", {})["chats"]} == before
    assert bound() == sources and sources  # what the chat binds, at which revisions
    assert api.call("shelf.read", {})["chats"] == [pinned]
    unpinned = api.call("shelf.pin_chat", {"chat": chat, "pinned": False, "expected": pinned["etag"]})
    assert unpinned == {"chat": chat, "pinned": False, "etag": None} and api.call("shelf.read", {})["chats"] == []


def test_every_refusal_says_why_and_writes_nothing(app):
    api, desk, chat = app
    _refused(api, "shelf.set_tags", {"ref": MARA, "tags": ["a,b"], "expected": None}, "invalid_tag", 400)
    _refused(api, "shelf.set_tags", {"ref": MARA, "tags": ["x" * 41], "expected": None}, "invalid_tag", 400)
    _refused(api, "shelf.set_tags", {"ref": MARA, "tags": ["bell\x07"], "expected": None}, "invalid_tag", 400)
    _refused(api, "shelf.set_tags", {"ref": MARA, "tags": [f"t{n}" for n in range(33)], "expected": None}, "too_many_tags", 400)
    _refused(api, "shelf.set_tags", {"ref": MARA, "tags": "crew", "expected": None}, "invalid_field", 400)
    _refused(api, "shelf.set_tags", {"ref": MARA, "tags": ["crew"]}, "invalid_field", 400)  # never looked: no write
    _refused(api, "shelf.set_tags", {"ref": "Mara", "tags": ["crew"], "expected": None}, "invalid_field", 400)
    _refused(api, "shelf.set_tags", {"ref": MARA + "@r1", "tags": ["crew"], "expected": None}, "invalid_ref", 400)
    _refused(api, "shelf.edit_tags", {"ref": MARA, "expected": None}, "invalid_field", 400)
    _refused(api, "shelf.edit_tags", {"ref": MARA, "add": ["x"], "remove": ["X"], "expected": None}, "invalid_tag", 400)
    _refused(api, "shelf.pin_entry", {"ref": MARA, "pinned": "yes", "expected": None}, "invalid_field", 400)
    _refused(api, "shelf.pin_entry", {"ref": MARA, "pinned": True, "expected": "abc"}, "invalid_field", 400)
    # a name is never looked up: an entry and a chat are their exact refs, and must be in the library
    _refused(api, "shelf.pin_entry", {"ref": GONE, "pinned": True, "expected": None}, "no_entry", 404)
    _refused(api, "shelf.set_tags", {"ref": "card:mara-vell/character/mara", "tags": ["crew"], "expected": None}, "no_entry", 404)
    _refused(api, "shelf.pin_chat", {"chat": "campaign:nowhere#chat/none", "pinned": True, "expected": None}, "no_chat", 404)
    _refused(api, "shelf.pin_chat", {"chat": "the card chat", "pinned": True, "expected": None}, "invalid_field", 400)
    _refused(api, "shelf.save_filter", {"name": " "}, "invalid_name", 400)
    _refused(api, "shelf.save_filter", {"name": "Crew", "role": "Characters!"}, "invalid_filter", 400)
    _refused(api, "shelf.save_filter", {"name": "Crew", "source": "world:harbor", "shelf": "cards"}, "invalid_filter", 400)
    _refused(api, "shelf.rename_filter", {"id": "f-none", "name": "Crew", "expected": None}, "no_filter", 404)
    _refused(api, "shelf.delete_filter", {"id": "f-none", "expected": None}, "no_filter", 404)
    assert not (desk / "config").exists() and chat


def test_a_stale_write_is_refused_changed_and_the_newer_marks_stay(app):
    api, _desk, chat = app
    seen = api.call("shelf.set_tags", {"ref": MARA, "tags": ["crew"], "expected": None})
    newer = api.call("shelf.pin_entry", {"ref": MARA, "pinned": True, "expected": seen["etag"]})
    _refused(api, "shelf.set_tags", {"ref": MARA, "tags": ["noir"], "expected": seen["etag"]}, "changed", 409)
    _refused(api, "shelf.edit_tags", {"ref": MARA, "add": ["noir"], "expected": None}, "changed", 409)
    _refused(api, "shelf.pin_entry", {"ref": MARA, "pinned": False, "expected": seen["etag"]}, "changed", 409)
    assert _entry(api, MARA) == newer
    pinned = api.call("shelf.pin_chat", {"chat": chat, "pinned": True, "expected": None})
    _refused(api, "shelf.pin_chat", {"chat": chat, "pinned": True, "expected": None}, "changed", 409)
    assert api.call("shelf.read", {})["chats"] == [pinned]


def test_a_bulk_tag_adds_and_removes_and_leaves_each_entrys_other_tags(app):
    api, _desk, _chat = app
    api.call("shelf.set_tags", {"ref": MARA, "tags": ["crew", "noir"], "expected": None})
    for ref in (MARA, LORE):  # what the page's Tag... does, one entry at a time, each at the etag it read
        api.call("shelf.edit_tags", {"ref": ref, "add": ["Harbor"], "remove": ["noir"],
                                     "expected": (_entry(api, ref) or {"etag": None})["etag"]})
    assert _entry(api, MARA)["tags"] == ["crew", "Harbor"] and _entry(api, LORE)["tags"] == ["Harbor"]
    assert api.call("shelf.read", {})["tags"] == [{"tag": "crew", "entries": 1}, {"tag": "Harbor", "entries": 2}]


def test_one_tag_reads_in_one_spelling_however_each_entry_wrote_it(app):
    api, _desk, _chat = app
    api.call("shelf.set_tags", {"ref": MARA, "tags": ["Noir"], "expected": None})
    api.call("shelf.set_tags", {"ref": LORE, "tags": ["noir"], "expected": None})
    saved = api.call("shelf.save_filter", {"name": "Dark", "tags": ["NOIR"]})
    read = api.call("shelf.read", {})
    assert [item["tags"] for item in read["entries"]] == [["Noir"], ["Noir"]]
    assert read["tags"] == [{"tag": "Noir", "entries": 2}] and read["filters"][0]["tags"] == ["Noir"]
    assert saved["tags"] == ["NOIR"]  # as it was written; reading shows it in the library's spelling


def test_marks_about_something_no_longer_here_are_kept_and_can_be_taken_away(app):
    api, desk, _chat = app
    folder = desk / "config" / "shelf" / "entries"
    folder.mkdir(parents=True)
    record = {"format": "grimoire.shelf.entry/1", "ref": GONE, "tags": ["crew"], "pinned": True}
    (folder / (record_name(SourceRef.parse(GONE)) + ".json")).write_text(json.dumps(record), encoding="utf-8")
    held = _entry(api, GONE)
    assert held["tags"] == ["crew"] and held["pinned"] is True  # listed as it is; a page leaves it out of its tiles
    assert GONE not in {entry["ref"] for entry in api.call("sources.catalog", {"limit": 1000})["entries"]}
    _refused(api, "shelf.edit_tags", {"ref": GONE, "add": ["more"], "expected": held["etag"]}, "no_entry", 404)
    unpinned = api.call("shelf.pin_entry", {"ref": GONE, "pinned": False, "expected": held["etag"]})
    assert unpinned["tags"] == ["crew"] and unpinned["pinned"] is False
    assert api.call("shelf.set_tags", {"ref": GONE, "tags": [], "expected": unpinned["etag"]})["etag"] is None
    assert list(folder.iterdir()) == []


def test_saved_filters_are_made_renamed_and_deleted(app):
    api, _desk, _chat = app
    crew = api.call("shelf.save_filter", {"name": " Harbor  crew ", "role": "character", "source": "card:mara-vell",
                                          "tags": ["crew", "Noir"], "query": "mara", "sort": "name"})
    assert crew["id"].startswith("f-") and crew["etag"].startswith("sha256:")
    assert {key: crew[key] for key in ("name", "role", "shelf", "source", "tags", "query", "sort")} == {
        "name": "Harbor crew", "role": "character", "shelf": "", "source": "card:mara-vell", "tags": ["crew", "Noir"],
        "query": "mara", "sort": "name"}
    pinned = api.call("shelf.save_filter", {"name": "Pinned lore", "role": "lore", "shelf": "pinned"})
    assert [item["name"] for item in api.call("shelf.read", {})["filters"]] == ["Harbor crew", "Pinned lore"]
    _refused(api, "shelf.save_filter", {"name": "harbor CREW"}, "filter_name_taken", 409)
    _refused(api, "shelf.rename_filter", {"id": pinned["id"], "name": "Harbor crew", "expected": pinned["etag"]},
             "filter_name_taken", 409)
    renamed = api.call("shelf.rename_filter", {"id": crew["id"], "name": "Quay", "expected": crew["etag"]})
    assert renamed["name"] == "Quay" and renamed["tags"] == ["crew", "Noir"] and renamed["id"] == crew["id"]
    _refused(api, "shelf.rename_filter", {"id": crew["id"], "name": "Again", "expected": crew["etag"]}, "changed", 409)
    _refused(api, "shelf.delete_filter", {"id": crew["id"], "expected": crew["etag"]}, "changed", 409)
    assert api.call("shelf.delete_filter", {"id": crew["id"], "expected": renamed["etag"]}) == {"id": crew["id"], "deleted": True}
    assert [item["name"] for item in api.call("shelf.read", {})["filters"]] == ["Pinned lore"]


def test_a_shelf_file_that_cannot_be_read_is_named_and_the_app_still_opens(tmp_path):
    """What a keep-both sync conflict, another sync tool or a hand edit can leave: the library opens, the shelf
    reads what it can and says which file it could not, and that file is never written over."""
    desk, chat = desktop_library(tmp_path / "desk")
    with Device(desk) as opened:
        api = AppApi(opened.api)
        api.call("shelf.pin_entry", {"ref": LORE, "pinned": True, "expected": None})
        api.call("shelf.set_tags", {"ref": MARA, "tags": ["crew"], "expected": None})
    path = desk / "config" / "shelf" / "entries" / (record_name(SourceRef.parse(MARA)) + ".json")
    torn = path.read_bytes()[:-9] + b"<<<<<<< theirs\n"
    path.write_bytes(torn)
    (path.parent / (path.stem + ".sync-conflict.json")).write_bytes(b"not json")
    with Device(desk) as opened:  # the app opens
        api = AppApi(opened.api)
        assert api.api.store.load().problems == ()
        assert str(chat) in {item["chat"] for item in api.call("chats", {"limit": 10})["chats"]}
        read = api.call("shelf.read", {})
        assert [item["ref"] for item in read["entries"]] == [LORE]
        assert sorted((problem["path"].rsplit("/", 1)[1], problem["reason"]) for problem in read["problems"]) == sorted(
            [(path.name, "malformed"), (path.stem + ".sync-conflict.json", "malformed")])
        assert all(problem["detail"] for problem in read["problems"])
        error = _refused(api, "shelf.pin_entry", {"ref": MARA, "pinned": True, "expected": None}, "unreadable_record", 409)
        assert "config/shelf/entries/" + path.name in str(error)
        assert path.read_bytes() == torn
        api.call("shelf.pin_chat", {"chat": str(chat), "pinned": True, "expected": None})  # the rest still works


def test_device_sync_leaves_the_shelf_on_its_own_device_and_says_it_skipped_it(tmp_path):
    """The contract today (docs/SHELF.md): the shelf lives in the library folder but is not one of the record
    families device sync carries, so a sync neither sends it nor disturbs it."""
    desk, _chat = desktop_library(tmp_path / "desk")
    phone = phone_library(tmp_path / "phone")
    pair_devices(desk, phone)
    with Device(desk) as desktop:
        api = AppApi(desktop.api)
        tagged = api.call("shelf.set_tags", {"ref": MARA, "tags": ["crew"], "expected": None})
        assert "config" in desktop.api.store.scan().skipped and not desktop.api.store.scan().blocked
        with serving(desktop) as server, Device(phone, peers={DESKTOP: server.address}) as mobile:
            assert mobile("sync", peer=DESKTOP)["status"] == "synced"
            carried = AppApi(mobile.api)
            assert MARA in {entry["ref"] for entry in carried.call("sources.catalog", {"limit": 1000})["entries"]}
            assert carried.call("shelf.read", {})["entries"] == []
        assert api.call("shelf.read", {})["entries"] == [tagged]
