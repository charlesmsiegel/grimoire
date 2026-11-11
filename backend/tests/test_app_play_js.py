"""111's pages under Node: Start Chat, play, corrections and the chat lists in 108's shell, in a stand-in DOM
(``js/fakedom.mjs``) against a real app server over a synthetic card library with a scripted model.

Skipped where Node is not installed: the Python suite is the gate, and this is the pages' client side.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from card_fixtures import SCENARIO, encode, land
from scene_fixtures import ANYA, ANYA_CARD, MARA_VELL, TAMSIN, TAMSIN_CARD, VOICED, say, secure
from textapi_fixtures import Device

from grimoire.app import AppServer
from grimoire.app.appapi import AppApi
from grimoire.scene import SceneRefused
from grimoire.store import LibraryStore

TESTS = Path(__file__).with_name("js")
NODE = shutil.which("node")


def _library(tmp_path):
    folder = tmp_path / "cards"
    with LibraryStore.create(folder) as store:
        for document in (VOICED, ANYA_CARD, TAMSIN_CARD, SCENARIO):
            land(store, encode(document))
    secure(folder)
    return folder


def _run(device, **env):
    """Serve ``device`` and run the Node test with ``env``; the Node run's output."""
    app = AppServer(device.api)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = app.address
        result = subprocess.run(
            [NODE, "--test", str(TESTS / "play.e2e.mjs")], capture_output=True, text=True, timeout=240,
            check=False, env={**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token, **env})
    finally:
        app.close()
        thread.join(10)
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
    assert "# pass 1" in result.stdout
    return result


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_start_chat_and_play_end_to_end(tmp_path):
    folder = _library(tmp_path)
    replies = ["Safe as the tide.", "Gone with the tide.", "Out with the tide.", "Back with the tide."]
    with Device(folder, [say(text) for text in replies]) as device:
        _run(device, GRIMOIRE_CHARACTER=str(MARA_VELL), GRIMOIRE_PERSONA=str(ANYA), GRIMOIRE_NEWCOMER=str(TAMSIN))


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_a_chat_with_no_model_says_so_and_still_takes_a_post(tmp_path):
    folder = _library(tmp_path)
    with Device(folder, runtime=False) as device:
        play = AppApi(device.api)
        chat = play.call("play.start", {"kind": "character", "ref": str(MARA_VELL), "persona": str(ANYA)})["chat"]
        _run(device, GRIMOIRE_NOMODEL=chat)
        scene = play.call("open", {"chat": chat})
        assert [bool(turn["post"]) for turn in scene["turns"]][-1] is True and scene["state"]["open_turn"] is None


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_a_gone_card_is_detached_in_the_chat_page(tmp_path):
    folder = _library(tmp_path)
    with Device(folder) as device:
        play = AppApi(device.api)
        chat = play.call("play.start", {"kind": "character", "ref": str(MARA_VELL), "persona": str(ANYA)})["chat"]
        play.call("play.add_card", {"chat": chat, "ref": str(TAMSIN)})
    shutil.rmtree(folder / "sources" / "card" / "tamsin-hale")
    with Device(folder) as device:
        _run(device, GRIMOIRE_BROKEN=chat)
        assert AppApi(device.api).call("play.repairs", {"chat": chat})["repairs"] == []


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_a_start_cut_off_is_finished_in_the_chat_page(tmp_path):
    folder = _library(tmp_path)
    with Device(folder) as device:
        runner = device.api.runner
        begin = runner.begin
        cut = []

        def once(*args, **kwargs):  # the first opening is lost to a race; continue's goes through
            if not cut:
                cut.append(True)
                raise SceneRefused("turn_changed", "a turn was opened meanwhile")
            return begin(*args, **kwargs)

        runner.begin = once
        _run(device, GRIMOIRE_UNFINISHED="1", GRIMOIRE_CHARACTER=str(MARA_VELL), GRIMOIRE_PERSONA=str(ANYA))
        assert cut == [True]
