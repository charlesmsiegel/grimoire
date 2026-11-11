"""114 in the app shell (108): the media adapter's ops served as ``media.<op>`` through ``AppApi``, the Art and
Sync pages in the page registry, and both pages end to end under Node in a stand-in DOM (``js/media.e2e.mjs``),
with the Sync page's keep-both view over a three-device conflict (``js/sync_conflicts.mjs``).

Synthetic libraries only; every picture is a synthetic byte string.
"""

from __future__ import annotations

import base64
import gc
import os
import shutil
import subprocess
import threading
import weakref
from pathlib import Path

import pytest
from textapi_fixtures import DESKTOP, Device, desktop_library, phone_library, say

from grimoire.app import AppServer
from grimoire.app.adapters.media import OPS, media_of
from grimoire.app.appapi import AppApi
from grimoire.app.pages import load_pages
from grimoire.app.server import STATIC
from grimoire.textapi import ApiRefused

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR synthetic"
MARA_CARD = "card:mara-vell/character/mara-vell"
TESTS = Path(__file__).with_name("js")
NODE = shutil.which("node")


def _media(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_every_media_op_is_served_and_the_art_and_sync_pages_need_only_those(tmp_path):
    desk, _chat = desktop_library(tmp_path / "desk")
    with Device(desk) as device:
        app = AppApi(device.api)
        served = set(app.call("library")["ops"])
        assert {f"media.{op}" for op in OPS} <= served
        pages = {page.id: page for page in load_pages(STATIC)}
        # Art is reached from a character, not from the rail: it is hidden there, and still opens by its route
        for page_id, route, slot in (("art", "art", "hidden"), ("sync", "sync", "media")):
            page = pages[page_id]
            assert (page.route, page.slot, page.owner, page.unavailable) == (route, slot, "114", None)
            assert set(page.requires) <= served
        # the picture picker other pages import is served from the Art page's folder, with its own style sheet
        assert (STATIC / "pages" / "art" / "picker.js").is_file() and (STATIC / "pages" / "art" / "picker.css").is_file()


def test_one_media_adapter_serves_each_open_library_so_a_pairing_made_here_is_listed(tmp_path):
    laptop = phone_library(tmp_path / "laptop")
    with Device(laptop) as device:
        first, second = AppApi(device.api), AppApi(device.api)
        assert media_of(device.api) is media_of(device.api)
        assert first.call("media.devices")["device_problem"] == "unnamed"
        assert first.call("media.identify", {"device": "laptop"})["device"] == "laptop"
        shown = first.call("media.pair", {"peer": DESKTOP})
        assert len(shown["key"]) == 64
        # another request through another AppApi over the same library sees the same adapter's pairing
        assert second.call("media.devices")["peers"] == [{"peer": DESKTOP, "address": None, "paired": True, "last": None}]


def test_the_media_adapter_goes_when_its_library_does(tmp_path):
    with Device(phone_library(tmp_path / "laptop")) as device:
        served = weakref.ref(media_of(device.api))
        assert served() is not None
    del device
    gc.collect()  # the library, then (its entry gone from the table) the adapter
    gc.collect()
    assert served() is None


def test_a_picture_placed_through_the_app_and_a_stale_edit_refused_with_nothing_written(tmp_path):
    desk, _chat = desktop_library(tmp_path / "desk")
    with Device(desk) as device:
        app = AppApi(device.api)
        placed = app.call("media.upload", {"scope": "card:mara-vell", "media": _media(PNG), "note": "Harbor",
                                           "subject": MARA_CARD, "slot": "portrait", "expected": None})
        (slot,) = [slot for slot in placed["art"]["slots"] if slot["slot"] == "portrait"]
        assert slot["state"] == "shown" and device.api.image(slot["image"]) == ("image/png", PNG)
        before = app.call("media.art", {"subject": MARA_CARD})
        with pytest.raises(ApiRefused) as caught:
            app.call("media.place", {"subject": MARA_CARD, "slot": "portrait", "mode": "set",
                                     "image": slot["image"], "expected": None})
        assert (caught.value.reason, caught.value.status) == ("changed", 409)
        assert app.call("media.art", {"subject": MARA_CARD}) == before


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_art_and_sync_pages_end_to_end(tmp_path):
    desk, _chat = desktop_library(tmp_path / "desk")
    with Device(desk) as device:
        app = AppServer(device.api)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            result = subprocess.run(
                [NODE, "--test", "--test-reporter=tap", str(TESTS / "media.e2e.mjs")], capture_output=True, text=True,
                timeout=180, check=False,
                env={**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token,
                     "GRIMOIRE_SCOPE": "card:mara-vell", "GRIMOIRE_SUBJECT": MARA_CARD})
        finally:
            app.close()
            thread.join(10)
        assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
        assert "# pass 1" in result.stdout
        # what the pages did is in the library: the picture placed, this device named
        art = AppApi(device.api).call("media.art", {"subject": MARA_CARD})
        (portrait,) = [slot for slot in art["slots"] if slot["slot"] == "portrait"]
        assert portrait["state"] == "shown" and device.api.image(portrait["image"]) == ("image/png", PNG)
        assert AppApi(device.api).call("media.devices")["device"] == "desk-e2e"


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_picture_picker_chooses_cancels_and_closes_with_its_page(tmp_path):
    desk, _chat = desktop_library(tmp_path / "desk")
    with Device(desk) as device:
        app = AppServer(device.api)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            result = subprocess.run(
                [NODE, "--test", "--test-reporter=tap", str(TESTS / "picker.e2e.mjs")], capture_output=True, text=True,
                timeout=180, check=False,
                env={**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token,
                     "GRIMOIRE_SCOPE": "card:mara-vell", "GRIMOIRE_SUBJECT": MARA_CARD})
        finally:
            app.close()
            thread.join(10)
        assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
        assert "# pass 1" in result.stdout
        # what the picker left in the library: the avatar's picture with the crop its zoom stands for
        art = AppApi(device.api).call("media.art", {"subject": MARA_CARD})
        (avatar,) = [slot for slot in art["slots"] if slot["slot"] == "avatar"]
        assert avatar["state"] == "shown" and avatar["crop"] == {"x": 2500, "y": 2500, "w": 5000, "h": 5000}


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_covers_entry_pictures_backdrops_and_post_pictures_through_the_pages(tmp_path):
    desk, chat = desktop_library(tmp_path / "desk")
    lore, world, persona = "card:mara-vell/lorebook/mara-vell-lore", "world:saltmarch", "card:anya-rook/character/anya-rook"
    with Device(desk, [say("The tide answers.")] * 8) as device:
        api = AppApi(device.api)
        api.call("sources.create_source", {"source": "world:harbor", "title": "Harbor"})
        (made,) = [item for item in api.call("sources.catalog", {"limit": 50})["sources"] if item["source"] == "world:harbor"]
        scenario = api.call("sources.create", {"source": "world:harbor", "kind": "scenario", "expected": made["etag"],
                                               "fields": {"title": "Night watch", "premise": "The lamps are out."}})["ref"]
        scenario = scenario.split("@")[0]
        prompts = list(device.prompts())
        app = AppServer(device.api)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            result = subprocess.run(
                [NODE, "--test", "--test-reporter=tap", str(TESTS / "covers.e2e.mjs")], capture_output=True, text=True,
                timeout=300, check=False,
                env={**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token,
                     "GRIMOIRE_LORE": lore, "GRIMOIRE_WORLD": world, "GRIMOIRE_SCENARIO": scenario,
                     "GRIMOIRE_CAMPAIGN": str(chat).split("#")[0].split(":", 1)[1], "GRIMOIRE_PERSONA": persona})
        finally:
            app.close()
            thread.join(10)
        assert result.returncode == 0, result.stdout[-8000:] + result.stderr[-3000:]
        assert "# pass 1" in result.stdout
        # what the pages left in the library: the lorebook's and the world's covers, the scenario's backdrop
        shown = {item["subject"]: item["state"] for item in api.call("media.shown", {"subjects": [lore, world]})["shown"]}
        assert shown == {lore: "shown", world: "shown"}
        backdrops = [slot for slot in api.call("media.art", {"subject": scenario})["slots"] if slot["slot"].startswith("backdrop.")]
        assert [slot["state"] for slot in backdrops] == ["shown"]
        # and no model was shown a picture: no prompt of the play the pages made names one
        asked = [prompt for prompt in device.prompts() if prompt not in prompts]
        assert not any("#image/" in prompt or "post." in prompt or "lamp.png" in prompt for prompt in asked)
        assert device.api.store.load().problems == ()


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_sync_page_shows_every_version_of_a_three_device_conflict_and_loses_none():
    result = subprocess.run([NODE, "--test", "--test-reporter=tap", str(TESTS / "sync_conflicts.mjs")],
                            capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
    assert "# pass 2" in result.stdout
