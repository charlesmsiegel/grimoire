"""073: the app's ``play.imagine``, a hypothetical reply from one NPC, saved nowhere.

The proving scenario over the app's own dispatch: the player asks how Mara Vell might take a confession, gets one
possible reply, and plays the real scene on unchanged. Synthetic cards; every model call is a scripted fake.
"""

from __future__ import annotations

import json
import shutil

import pytest

from card_fixtures import encode, land
from scene_fixtures import ANYA, ANYA_CARD, MARA_VELL, TAMSIN, TAMSIN_CARD, VOICED, say, secure
from test_app_play import Device, _tree, actor_named, kept_lines, keep_newest, refused

from grimoire.store import LibraryStore
from grimoire.testing.providers import Status

CONFESSION = '"I was the one who sank the Gull."'
MIGHT = "Then you owe me a ship."
REAL = "Fair winds tonight."


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    folder = tmp_path_factory.mktemp("built") / "cards"
    with LibraryStore.create(folder) as store:
        for document in (VOICED, ANYA_CARD, TAMSIN_CARD):
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


def test_a_hypothetical_reply_is_marked_not_canonical_and_the_scene_plays_on_unchanged(folder):
    with Device(folder, [say(MIGHT), say(REAL)]) as device:
        scene = start(device)
        chat, mara = scene["chat"], actor_named(scene, "Mara Vell")
        before_tree, before_scene = _tree(folder), json.dumps(device("open", chat=chat), sort_keys=True)
        found = device("play.imagine", chat=chat, speaker=mara, text=CONFESSION)
        assert (found["canonical"], found["saved"], found["chat"]) == (False, False, chat)
        assert found["speaker"] == {"actor": mara, "name": "Mara Vell"}
        assert found["line"]["text"] == "I was the one who sank the Gull." and found["line"]["kind"] == "speech"
        assert found["line"]["mode"] == "auto"
        assert found["replies"] == [{"speaker": "Mara Vell", "actor": mara, "text": MIGHT}]
        assert found["outcome"] == "complete" and found["calls"] == []
        assert found["usage"]["attempts"] == 1
        # nothing was written, and the chat reads exactly as it did
        assert _tree(folder) == before_tree
        assert json.dumps(device("open", chat=chat), sort_keys=True) == before_scene

        # copied, perhaps, but never inserted: the real turn is the player's own, and nobody heard the confession
        device("play.post", chat=chat, text='"Any news from the harbor?"', mode="speech")
        scene = keep_newest(device, device("generate", chat=chat, speakers=[mara]))
        prompt = device.prompts()[-1]
        assert "sank the Gull" not in prompt and MIGHT not in prompt
        assert kept_lines(scene)[-1] == ("Mara Vell", REAL)
        assert all(MIGHT not in text and "sank the Gull" not in text for _, text in kept_lines(scene))


def test_the_line_is_read_in_the_mode_the_composer_would_post_it(folder):
    with Device(folder, [say(MIGHT), say(MIGHT)]) as device:
        scene = start(device)
        chat, mara = scene["chat"], actor_named(scene, "Mara Vell")
        line = "I sank the Gull, Mara."
        assert device("play.imagine", chat=chat, speaker=mara, text=line)["line"]["kind"] == "action"
        spoken = device("play.imagine", chat=chat, speaker=mara, text=line, mode="speech")
        assert (spoken["line"]["kind"], spoken["line"]["mode"]) == ("speech", "speech")


def test_a_failed_reply_reports_why_and_what_was_sent(folder):
    with Device(folder, [Status(500)]) as device:
        scene = start(device)
        before = _tree(folder)
        found = device("play.imagine", chat=scene["chat"], speaker=actor_named(scene, "Mara Vell"), text=CONFESSION)
        assert found["replies"] == [] and found["calls"][0]["status"] == "failed"
        assert found["usage"]["attempts"] >= 1
        assert _tree(folder) == before


def test_malformed_and_refused_hypotheticals_are_refused_and_write_nothing(folder):
    with Device(folder, [say("Safe as the tide.")]) as device:
        scene = start(device)
        chat, mara = scene["chat"], actor_named(scene, "Mara Vell")
        persona = scene["persona"]["actor"]
        before = _tree(folder)
        cases = [
            ({"chat": chat, "speaker": mara}, "invalid_field"),
            ({"chat": chat, "speaker": mara, "text": 7}, "invalid_field"),
            ({"chat": chat, "speaker": mara, "text": "   "}, "no_content"),
            ({"chat": chat, "speaker": mara, "text": "x" * 20001}, "invalid_field"),
            ({"chat": chat, "speaker": mara, "text": "hi", "mode": "director"}, "invalid_field"),
            ({"chat": chat, "speaker": "not a ref", "text": "hi"}, "invalid_field"),
            ({"chat": chat, "speaker": persona, "text": "hi"}, "not_an_npc"),
            ({"chat": chat, "speaker": mara, "text": "Grimoire, skip"}, "table_request"),
        ]
        for args, reason in cases:
            assert refused(device, "play.imagine", **args).reason == reason, args
        assert _tree(folder) == before and device.prompts() == []

        device("play.post", chat=chat, text='"Is the cargo safe?"', mode="speech")  # a turn waiting for its answer
        assert refused(device, "play.imagine", chat=chat, speaker=mara, text=CONFESSION).reason == "turn_open"
        assert device.prompts() == []


def test_another_npc_must_be_present_to_be_asked(folder):
    with Device(folder, [say("Papers, please.")]) as device:
        scene = start(device)
        chat = scene["chat"]
        scene = device("play.add_card", chat=chat, ref=str(TAMSIN))
        found = device("play.imagine", chat=chat, speaker=actor_named(scene, "Tamsin Hale"), text=CONFESSION)
        assert found["replies"][0]["speaker"] == "Tamsin Hale"
        assert "I was the one who sank the Gull." in device.prompts()[-1]


def test_no_model_means_no_hypothetical(folder):
    with Device(folder, runtime=False) as device:
        scene = start(device)
        error = refused(device, "play.imagine", chat=scene["chat"], speaker=actor_named(scene, "Mara Vell"),
                        text=CONFESSION)
        assert error.reason == "no_runtime"
        assert "play.imagine" in device("library")["ops"]
