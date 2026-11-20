"""081's story threads page through 108's shell contract: it registers on a hidden route reached from the
campaign hub, needs the story ops, and runs end to end under Node (``js/story.e2e.mjs``) where Node is installed,
over a played chat whose ledger records an early promise and canon's ruling on how it ended.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from generation_fixtures import say
from test_app_play import Device
from test_app_play_story import built, folder  # noqa: F401 -- the played card library fixtures
from test_app_story import PROMISE, SOLD, ledger, played

from grimoire.app import AppServer
from grimoire.app.pages import load_pages
from grimoire.campaignapi.api import UNAVAILABLE

STATIC = Path(__file__).resolve().parents[1] / "src" / "grimoire" / "app" / "static"
NODE = shutil.which("node")


def test_the_page_registers_on_a_hidden_route_and_needs_the_story_ops():
    (page,) = [page for page in load_pages(STATIC) if page.id == "story"]
    assert (page.route, page.slot, page.owner, page.entry) == ("story", "hidden", "081", "page.js")
    assert set(page.requires) == {"story.threads", "story.thread"}
    assert page.unavailable is None
    # the hub no longer says the story graph waits on this slice
    assert "081" not in {item["blocker"] for item in UNAVAILABLE}


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_story_page_end_to_end(folder):  # noqa: F811
    with Device(folder, [say("I will, before the thaw."), say("Gone, I'm afraid.")]) as device:
        chat, turns, mara, anya = played(device)
        ledger(device, chat, turns, mara, anya)
        app = AppServer(device.api)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            env = {**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token,
                   "GRIMOIRE_CHAT": str(chat), "GRIMOIRE_CAMPAIGN": chat.campaign_id, "GRIMOIRE_PROMISE": PROMISE,
                   "GRIMOIRE_SOLD": SOLD}
            result = subprocess.run([NODE, "--test", "--test-reporter=tap", str(Path(__file__).with_name("js") / "story.e2e.mjs")],
                                    capture_output=True, text=True, timeout=180, check=False, env=env)
        finally:
            app.close()
            thread.join(10)
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
    assert "# pass 1" in result.stdout
