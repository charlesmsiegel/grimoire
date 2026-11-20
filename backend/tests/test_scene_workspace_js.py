"""065's first useful proof under Node: one thread reached from Chats, Characters, Scenarios, Campaigns and
Worlds without losing its composer draft, and a derived portrayal's chat reached only through the explicit
descendant filter, never its namesake's (``js/workspace.e2e.mjs``), against a real app server over a synthetic
library. The navigation cache behind those pages is tested in ``test_workspace.py``.

The e2e run is skipped where Node is not installed: the Python suite is the gate, and that is the client side.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
import textapi_fixtures as tf
from card_fixtures import SCENARIO, encode, land
from portrayal_fixtures import BASE, MODERN_MARA, NAMESAKE, seed_elderfen
from textapi_fixtures import Device

from grimoire.app import AppServer
from grimoire.chats import Launch
from grimoire.core.identity import ChatRef, SourceRef

TESTS = Path(__file__).with_name("js")
NODE = shutil.which("node")
CAMPAIGN = "the-drowned-ledger"
WORLD = "world:saltmarch"
CHARACTER = SourceRef(WORLD, "character", "mara.default")
STORM = SourceRef("card:storm-over-port-vell", "scenario", "storm-over-port-vell")


def _library(root: Path) -> Path:
    """044's desktop library (its imported V1 campaign in a world, and the card chat) plus 037's scenario card,
    landed before the library is secured."""
    root.mkdir(parents=True, exist_ok=True)
    env = tf.Env(root)
    tf.write(env.v1, f"{tf.SERAPHINE_DIR}/default.json", tf.SERAPHINE_CARD)
    tf.write(env.v1, f"{tf.CAMPAIGN}/characters/seraphine/default.json", tf.SERAPHINE_CARD)
    appearances = tf.json.loads((env.v1 / tf.CAMPAIGN / "appearances.json").read_text(encoding="utf-8"))
    appearances["characters/seraphine"]["base"] = tf.sha(tf.SERAPHINE_CARD)
    tf.write(env.v1, f"{tf.CAMPAIGN}/appearances.json", tf.json.dumps(appearances, indent=2, sort_keys=True) + "\n")
    env.rescan()
    env.plan().apply()
    for document in (tf.VOICED, tf.ANYA_CARD, SCENARIO):
        land(env.store, encode(document))
    env.store.close()
    tf.secure(root / "v2")
    return root / "v2"


def _campaign_chat(device: Device) -> ChatRef:
    listed = device("chats.workspace", filter="campaign", campaign=CAMPAIGN)["chats"]
    assert len(listed) == 1
    return ChatRef.parse(listed[0]["chat"])


def _written(device: Device, chat: ChatRef) -> str:
    """The chat record's etag: a post, a turn or any other edit of the chat changes it."""
    return device.api.chats.read_chat(chat)[1]


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_one_thread_from_every_library_view_keeps_its_draft(tmp_path):
    with Device(_library(tmp_path / "desk")) as device:
        chat = _campaign_chat(device)
        _record, etag = device.api.chats.read_chat(chat)
        device.api.chats.attach_source(chat, STORM, "scenario", expected=etag).commit()
        # 022's portrayals of one catalog Mara: the base, a Modern portrayal derived from it, an unrelated namesake
        seed_elderfen(device.table.store, device.api.owner_handle)
        portrayed = {name: device.api.chats.start_chat(Launch("character", ref)).commit().chat
                     for name, ref in (("BASE_CHAT", BASE), ("DERIVED_CHAT", MODERN_MARA), ("NAMESAKE_CHAT", NAMESAKE))}
        before = _written(device, chat)
        app = AppServer(device.api)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            result = subprocess.run(
                [NODE, "--test", "--test-reporter=tap", str(TESTS / "workspace.e2e.mjs")], capture_output=True, text=True,
                timeout=240, check=False,
                env={**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token,
                     "GRIMOIRE_CHAT": str(chat), "GRIMOIRE_CHARACTER": str(CHARACTER), "GRIMOIRE_SCENARIO": str(STORM),
                     "GRIMOIRE_CAMPAIGN": CAMPAIGN, "GRIMOIRE_WORLD": WORLD, "GRIMOIRE_BASE": str(BASE.unpinned()),
                     **{"GRIMOIRE_" + name: str(ref) for name, ref in portrayed.items()}})
        finally:
            app.close()
            thread.join(10)
        assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
        assert "# pass 1" in result.stdout
        assert _written(device, chat) == before, "browsing wrote nothing to the chat: the draft was never posted"
