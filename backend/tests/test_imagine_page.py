"""073's proving scenario through the chat page under Node (``js/imagine.e2e.mjs``): how Mara Vell might take a
confession is asked, shown, copied and discarded, and the player's own line is posted instead. Afterwards the
library on disk has gained that one post's turn and nothing else, and no later prompt holds the confession or the
possible reply.

Synthetic library only; the model is a scripted fake, so nothing is billed. Skipped where Node is not installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from generation_fixtures import say
from textapi_fixtures import Device, desktop_library

from grimoire.app import AppServer
from grimoire.app.appapi import AppApi

TESTS = Path(__file__).with_name("js")
NODE = shutil.which("node")


def _files(folder: Path) -> dict[str, bytes]:
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_a_possible_reply_is_copied_and_the_real_scene_goes_on_unchanged(tmp_path):
    desk, chat = desktop_library(tmp_path / "desk")
    with Device(desk, [say("Then you owe me a ship."), say("Fair winds tonight.")]) as device:
        AppApi(device.api).call("continue", {"chat": str(chat)})  # this device takes its seat
        before = _files(desk)
        app = AppServer(device.api)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            result = subprocess.run(
                [NODE, "--test", "--test-reporter=tap", str(TESTS / "imagine.e2e.mjs")], capture_output=True, text=True,
                timeout=240, check=False,
                env={**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token, "GRIMOIRE_CHAT": str(chat)})
        finally:
            app.close()
            thread.join(10)
        assert result.returncode == 0, result.stdout[-8000:] + result.stderr[-3000:]
        assert "# pass 1" in result.stdout
        prompts = device.prompts()
        after = _files(desk)
        assert device.api.store.load().problems == ()
    assert len(prompts) == 2
    assert "I was the one who sank the Gull." in prompts[0]
    assert "sank the Gull" not in prompts[1] and "Then you owe me a ship." not in prompts[1]
    assert all(before[name] == after.get(name) for name in before if "/turns/" not in name and "authority" not in name), \
        "nothing already there was rewritten"
    written = [name for name in sorted(set(after) - set(before)) if not name.startswith("authority/")]
    # the player's post and its answer (the turn), and the take kept for it: nothing for the hypothetical
    assert written and all("/turns/" in name for name in written), written
