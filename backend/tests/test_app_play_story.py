"""The play adapter's story ops: the "Last time" recap, the Story timeline and bookmarks (``play.story``,
``play.bookmark_add``, ``play.bookmark_rename``, ``play.bookmark_remove``).

Synthetic cards; every model call is a scripted fake, and the story ops must make none.
"""

from __future__ import annotations

import json
import shutil

import pytest
from card_fixtures import encode, land
from scene_fixtures import ANYA, ANYA_CARD, MARA_VELL, VOICED, say, secure
from test_app_play import Device, actor_named, keep_newest, refused
from textapi_fixtures import DESKTOP, desktop_library, pair_devices, phone_library, serving

from grimoire.app.adapters.play_story import RECAP_LINES, recap_of, said
from grimoire.scene.bookmarks import MAX_BOOKMARK_LABEL
from grimoire.store import LibraryStore

GREETING = ("Mara Vell", "You came back. I didn't think you would.")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    folder = tmp_path_factory.mktemp("built") / "cards"
    with LibraryStore.create(folder) as store:
        for document in (VOICED, ANYA_CARD):
            land(store, encode(document))
    secure(folder)
    return folder


@pytest.fixture
def folder(built, tmp_path):
    copy = tmp_path / "cards"
    shutil.copytree(built, copy)
    return copy


def start(device: Device) -> dict:
    return device("play.start", kind="character", ref=str(MARA_VELL), persona=str(ANYA))


def play(device: Device, chat: str, text: str) -> dict:
    device("play.post", chat=chat, text=f'"{text}"', mode="speech")
    return keep_newest(device, device("generate", chat=chat))


def quoted(story: dict) -> list[tuple[str, str]]:
    return [(line["speaker"], line["text"]) for line in story["recap"]["lines"]]


# -- the recap, as a pure function of the player-facing view --------------------------------------------------

def view(number: int, *, post=None, kept=None, private=False, withheld=False, offered=()) -> dict:
    return {"turn": f"c.{number:06d}", "number": number,
            "post": None if post is None else {"speaker": "Anya", "actor": "pc", "text": post, "private": private,
                                               "withheld": withheld},
            "kept": None if kept is None else {"lines": [{"speaker": speaker, "text": text} for speaker, text in kept]},
            "offered": [{"lines": [{"speaker": "Mara", "text": text}]} for text in offered]}


def test_the_recap_quotes_only_what_was_said_in_the_open_and_kept():
    turns = [view(1, kept=[("Mara", "Opening.")]),
             view(2, post="A whisper.", private=True, kept=[("Mara", "Two."), (None, "Rain falls.")]),
             view(3, post="A hidden note.", withheld=True),  # passed, and only its sender knows it
             view(4, post="Passed aloud."),
             view(5, post="Open question.", offered=["Not kept yet."])]
    lines = said(turns, "pc")
    assert [(line["speaker"], line["text"], line["you"]) for line in lines] == [
        ("Mara", "Opening.", False), ("Mara", "Two.", False), ("Narrator", "Rain falls.", False),
        ("Anya", "Passed aloud.", True), ("Anya", "Open question.", True)]
    recap, last_time = recap_of(turns, "pc", None)
    assert last_time is None and recap["basis"] == "latest"
    # before the newest post: turn 5's own post and the take on offer are not "last time"
    assert [line["text"] for line in recap["lines"]] == ["Opening.", "Two.", "Rain falls.", "Passed aloud."]
    recap, last_time = recap_of(turns, "pc", "c.000002")
    assert last_time == {"turn": "c.000002", "number": 2} and recap["basis"] == "sitting"
    assert [line["text"] for line in recap["lines"]] == ["Opening.", "Two.", "Rain falls."]
    assert all(set(line) == {"turn", "number", "speaker", "text", "you"} for line in recap["lines"])


def test_the_recap_is_short_and_absent_when_nothing_was_said_before():
    many = [view(n, post=f"Post {n}.", kept=[("Mara", f"Line {n}.")]) for n in range(1, 8)]
    recap, _ = recap_of(many, "pc", "c.000006")
    assert len(recap["lines"]) == RECAP_LINES and recap["lines"][-1]["text"] == "Line 6."
    assert recap_of([], "pc", None) == (None, None)
    assert recap_of([view(1, post="First words.")], "pc", None) == (None, None)  # nothing before the newest post
    recap, last_time = recap_of(many, "pc", "c.000099")  # a turn this chat does not have: no sitting is claimed
    assert recap["basis"] == "latest" and last_time is None


# -- the story of a played chat ------------------------------------------------------------------------------

def test_the_story_recaps_the_last_sitting_without_a_model_call(folder):
    with Device(folder, [say("Safe as the tide."), say("Before dawn."), say("Unkept words.")]) as device:
        scene = start(device)
        chat = scene["chat"]
        story = device("play.story", chat=chat)
        assert story == {"chat": chat, "recap": {"basis": "latest", "lines": story["recap"]["lines"]},
                         "last_time": None, "scenes": [], "bookmarks": [], "problems": []}
        assert quoted(story) == [GREETING]

        scene = play(device, chat, "Is the cargo safe?")
        second = scene["turns"][-1]["turn"]
        scene = play(device, chat, "When do we sail?")
        calls = len(device.prompts())

        story = device("play.story", chat=chat, since=second)
        assert story["last_time"] == {"turn": second, "number": 2} and story["recap"]["basis"] == "sitting"
        assert quoted(story) == [GREETING, ("Anya Rook", "Is the cargo safe?"), ("Mara Vell", "Safe as the tide.")]
        assert [line["you"] for line in story["recap"]["lines"]] == [False, True, False]

        latest = device("play.story", chat=chat)
        assert latest["last_time"] is None and latest["recap"]["basis"] == "latest"
        assert quoted(latest) == quoted(story)  # before the newest post
        assert device("play.story", chat=chat, since="nope.000001")["last_time"] is None
        assert len(device.prompts()) == calls  # reading the story asked no model anything


def test_the_recap_never_quotes_an_unkept_take_or_a_struck_line(folder):
    with Device(folder, [say("Safe as the tide."), say("Unkept words.")]) as device:
        scene = start(device)
        chat = scene["chat"]
        scene = play(device, chat, "Is the cargo safe?")
        kept_turn = scene["turns"][-1]
        device("play.post", chat=chat, text='"And the crew?"', mode="speech")
        scene = device("generate", chat=chat)  # proposed, not kept
        open_turn = scene["state"]["open_turn"]
        story = device("play.story", chat=chat, since=open_turn)
        assert ("Mara Vell", "Unkept words.") not in quoted(story)
        assert quoted(story)[-1] == ("Anya Rook", "And the crew?")

        device("play.strike", chat=chat, line=kept_turn["kept"]["lines"][0]["line"])
        story = device("play.story", chat=chat, since=kept_turn["turn"])
        assert quoted(story) == [GREETING, ("Anya Rook", "Is the cargo safe?")]


# -- bookmarks -------------------------------------------------------------------------------------------------

def test_a_bookmark_is_added_renamed_and_removed_and_leaves_every_turn_file_alone(folder):
    with Device(folder, [say("Safe as the tide.")]) as device:
        scene = start(device)
        chat = scene["chat"]
        scene = play(device, chat, "Is the cargo safe?")
        first, second = (turn["turn"] for turn in scene["turns"])
        turns = sorted((folder / "campaigns").glob("*/turns/*.json"))
        before = {path: path.read_bytes() for path in turns}

        story = device("play.bookmark_add", chat=chat, turn=second, label="  The cargo  ")
        device("play.bookmark_add", chat=chat, turn=first)
        story = device("play.story", chat=chat)
        assert [(item["number"], item["label"], item["missing"]) for item in story["bookmarks"]] == [
            (1, None, False), (2, "The cargo", False)]
        (plain, cargo) = story["bookmarks"]

        story = device("play.bookmark_rename", chat=chat, bookmark=cargo["id"], label="Cargo", expected=cargo["etag"])
        renamed = story["bookmarks"][1]
        assert renamed["label"] == "Cargo" and renamed["etag"] != cargo["etag"]
        error = refused(device, "play.bookmark_rename", chat=chat, bookmark=cargo["id"], label="Late", expected=cargo["etag"])
        assert (error.reason, error.status) == ("changed", 409)
        error = refused(device, "play.bookmark_remove", chat=chat, bookmark=cargo["id"], expected=cargo["etag"])
        assert error.reason == "changed"
        assert device("play.story", chat=chat)["bookmarks"][1]["label"] == "Cargo"  # nothing half-done

        story = device("play.bookmark_remove", chat=chat, bookmark=plain["id"], expected=plain["etag"])
        assert [item["id"] for item in story["bookmarks"]] == [cargo["id"]]
        assert refused(device, "play.bookmark_remove", chat=chat, bookmark=plain["id"],
                       expected=plain["etag"]).reason == "unknown_bookmark"
        assert {path: path.read_bytes() for path in turns} == before  # the transcript is untouched
        assert sorted((folder / "campaigns").glob("*/turns/*.json")) == turns

    with Device(folder) as device:  # a restart reads the same bookmarks from their files
        assert [item["label"] for item in device("play.story", chat=chat)["bookmarks"]] == ["Cargo"]


def test_malformed_bookmark_requests_are_refused_before_anything_is_written(folder):
    with Device(folder) as device:
        scene = start(device)
        chat, turn = scene["chat"], scene["turns"][0]["turn"]
        added = device("play.bookmark_add", chat=chat, turn=turn, label="Kept")["bookmarks"][0]
        for op, args in [
            ("play.story", {"chat": "nonsense"}),
            ("play.story", {"chat": chat, "since": 4}),
            ("play.bookmark_add", {"chat": chat}),
            ("play.bookmark_add", {"chat": chat, "turn": ""}),
            ("play.bookmark_add", {"chat": chat, "turn": turn, "label": 9}),
            ("play.bookmark_add", {"chat": chat, "turn": turn, "label": "x" * (MAX_BOOKMARK_LABEL + 1)}),
            ("play.bookmark_rename", {"chat": chat, "bookmark": added["id"], "label": "x"}),
            ("play.bookmark_rename", {"chat": chat, "bookmark": added["id"], "label": "x", "expected": "stale"}),
            ("play.bookmark_remove", {"chat": chat, "bookmark": 3, "expected": added["etag"]}),
        ]:
            error = refused(device, op, **args)
            assert (error.reason, error.status) == ("invalid_field", 400), (op, args)
        assert refused(device, "play.bookmark_add", chat=chat, turn="other.000001").reason == "unknown_turn"
        assert refused(device, "play.bookmark_rename", chat=chat, bookmark="x.bm-none", label="x",
                       expected=added["etag"]).reason == "unknown_bookmark"
        assert [item["label"] for item in device("play.story", chat=chat)["bookmarks"]] == ["Kept"]
        ops = device("library")["ops"]
        assert {"play.story", "play.bookmark_add", "play.bookmark_rename", "play.bookmark_remove"} <= set(ops)


def test_a_bookmark_whose_turn_is_gone_is_listed_as_missing_and_a_damaged_file_is_reported(folder):
    with Device(folder) as device:
        scene = start(device)
        chat, turn = scene["chat"], scene["turns"][0]["turn"]
        device("play.bookmark_add", chat=chat, turn=turn, label="Here")
        device("play.bookmark_add", chat=chat, turn=turn, label="Gone")
        (gone,) = [item for item in device("play.story", chat=chat)["bookmarks"] if item["label"] == "Gone"]
        folder_of = next((folder / "campaigns").glob("*/bookmarks"))
        path = folder_of / f"{gone['id']}.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["payload"]["turn"] = turn.rsplit(".", 1)[0] + ".000077"  # a turn this chat does not have
        path.write_text(json.dumps(document), encoding="utf-8")
        (folder_of / f"{gone['id']}x.json").write_text("{", encoding="utf-8")

        story = device("play.story", chat=chat)
        assert [(item["label"], item["missing"], item["number"]) for item in story["bookmarks"]] == [
            ("Here", False, 1), ("Gone", True, None)]
        assert [problem["path"].rsplit("/", 1)[-1] for problem in story["problems"]] == [f"{gone['id']}x.json"]
        # still the person's to rename or remove
        missing = story["bookmarks"][1]
        story = device("play.bookmark_remove", chat=chat, bookmark=missing["id"], expected=missing["etag"])
        assert [item["label"] for item in story["bookmarks"]] == ["Here"]


# -- two devices -----------------------------------------------------------------------------------------------

def test_bookmarks_added_on_two_devices_both_survive_a_sync_and_a_double_rename_conflicts(tmp_path):
    desk, card_chat = desktop_library(tmp_path / "desk")
    phone = phone_library(tmp_path / "phone")
    pair_devices(desk, phone)
    chat = str(card_chat)
    with Device(desk) as desktop, serving(desktop) as server, Device(phone, peers={DESKTOP: server.address}) as mobile:
        scene = desktop("continue", chat=chat)
        turn = scene["turns"][0]["turn"]
        assert mobile("sync", peer=DESKTOP)["status"] == "synced"
        mobile("continue", chat=chat)

        desktop("play.bookmark_add", chat=chat, turn=turn, label="From the desk")
        mobile("play.bookmark_add", chat=chat, turn=turn, label="From the phone")  # offline: neither saw the other
        report = mobile("sync", peer=DESKTOP)
        assert report["status"] == "synced" and report["conflicts"] == []
        for device in (desktop, mobile):
            story = device("play.story", chat=chat)
            assert sorted(item["label"] for item in story["bookmarks"]) == ["From the desk", "From the phone"]
            assert story["problems"] == []
        ids = [item["id"] for item in desktop("play.story", chat=chat)["bookmarks"]]
        assert len(set(ids)) == 2  # two files, one per device

        # the same bookmark renamed on both: sync keeps both versions and says so, losing neither silently
        (shared,) = [item for item in desktop("play.story", chat=chat)["bookmarks"] if item["label"] == "From the desk"]
        desktop("play.bookmark_rename", chat=chat, bookmark=shared["id"], label="Desk name", expected=shared["etag"])
        mobile("play.bookmark_rename", chat=chat, bookmark=shared["id"], label="Phone name", expected=shared["etag"])
        report = mobile("sync", peer=DESKTOP)
        assert [conflict["path"].rsplit("/", 1)[-1] for conflict in report["conflicts"]] == [f"{shared['id']}.json"]
        labels = {name: {item["id"]: item["label"] for item in device("play.story", chat=chat)["bookmarks"]}[shared["id"]]
                  for name, device in (("desk", desktop), ("phone", mobile))}
        assert labels == {"desk": "Desk name", "phone": "Phone name"}
        waiting = mobile("open", chat=chat)["state"]["conflicts"]
        assert [item["path"].rsplit("/", 1)[-1] for item in waiting] == [f"{shared['id']}.json"]
