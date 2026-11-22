"""Sprint 115's Prep page through 108's shell contract: its manifest, the ops it needs, the links to it, and the page
itself end to end under Node (``js/prep.e2e.mjs``) against a real app server over a synthetic library. The one
model call it makes goes to ``ScriptedTransport``; never a real provider.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from prep_app_fixtures import DIRECTOR, configured, draft, ids_of, reply, settings, stocked
from scene_fixtures import ANYA, MARA_LORE, MARA_VELL
from textapi_fixtures import Device, desktop_library, say

from grimoire.app import AppServer
from grimoire.app.adapters.chronicle import FEATURES
from grimoire.app.appapi import AppApi
from grimoire.app.pages import load_pages
from grimoire.catalogs import Catalog, publish_catalog
from grimoire.core.identity import SourceRef

STATIC = Path(__file__).resolve().parents[1] / "src" / "grimoire" / "app" / "static"
NODE = shutil.which("node")
HARBOR = SourceRef(MARA_VELL.source, "catalog", "harbor", "r1")


def pinned(ref):
    return ref if ref.pinned else ref.at("r1")


def test_the_page_registers_hidden_on_its_own_route_and_every_op_it_needs_is_served(tmp_path):
    pages = {page.id: page for page in load_pages(STATIC)}
    page = pages["prep"]
    assert (page.route, page.slot, page.owner) == ("prep", "hidden", "115")
    assert set(page.requires) == {"campaign.chats", "prep.catalogs", "prep.rulesets", "prep.ruleset_check",
                                  "prep.preparation", "prep.prepare_plan", "prep.prepare", "prep.prepare_review"}
    folder, _chat = desktop_library(tmp_path / "desk")
    with Device(folder) as device:
        assert page.to_json(AppApi(device.api).ops)["missing"] == []
    # the Chronicle's coverage names the Prep page for all three slices, and the hub links to it
    rows = {item["slice"]: item for item in FEATURES}
    assert {rows[number]["route"] for number in ("056", "062", "064")} == {"prep"}
    assert all(rows[number]["status"] == "screen" for number in ("056", "062", "064"))
    hub = (STATIC / "pages" / "campaign" / "hub.js").read_text(encoding="utf-8")
    assert 'ctx.registered("prep") ? ui.buttonLink("Prep", "prep", [campaign]' in hub


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_prep_page_end_to_end(tmp_path):
    folder, chat = desktop_library(tmp_path / "desk")
    with Device(folder, [say("Mara pockets it.")]) as device:
        cast = stocked(device, chat)
        store = device.table.store
        publish_catalog(store, device.table.owner, Catalog(HARBOR, (pinned(MARA_VELL), pinned(MARA_LORE)), (),
                                                           (ANYA.unpinned(),)),
                        expected=store.read_source(HARBOR.source).etag, name="The harbor")
        transport = configured(device, settings(tmp_path / "model.json", cast["Mara Vell"]), [])
        evidence = AppApi(device.api).call("prep.preparation", {"chat": str(chat), **DIRECTOR})["evidence"]["evidence"]
        transport._steps.append(reply(draft(cast["Mara Vell"], cast["harbor"], ids_of(evidence))))
        app = AppServer(device.api)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            env = {**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token,
                   "GRIMOIRE_CHAT": str(chat), "GRIMOIRE_CAMPAIGN": chat.campaign_id,
                   "GRIMOIRE_CATALOG": str(HARBOR), "GRIMOIRE_CHARACTER": str(pinned(MARA_VELL))}
            result = subprocess.run([NODE, "--test", "--test-reporter=tap",
                                     str(Path(__file__).with_name("js") / "prep.e2e.mjs")],
                                    capture_output=True, text=True, timeout=180, check=False, env=env)
        finally:
            app.close()
            thread.join(10)
        assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
        assert "# pass 1" in result.stdout
        # the page made exactly the one approved call, and nothing more
        assert len(transport.sends) == 1
