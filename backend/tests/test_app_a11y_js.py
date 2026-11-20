"""The app's screens through a structural accessibility audit under Node (``js/a11y.e2e.mjs`` over
``js/a11y.mjs``): every route the shell registers, a chat and its campaign hub, each Import tab, a source page,
the command palette and a modal. It checks what the document tree alone decides (names, labels, references,
roles and states, landmarks, keyboard reachability of clickable things); a browser and a person with a real
screen reader check the rest (docs/ACCEPTANCE.md).

Skipped where Node is not installed, like the shell's other client-side tests.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from textapi_fixtures import Device, desktop_library, say

from grimoire.app import AppServer

JS = Path(__file__).with_name("js")
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_every_screen_passes_the_structural_accessibility_audit(tmp_path):
    folder, chat = desktop_library(tmp_path / "desk")
    with Device(folder, [say("Heard you.")]) as device:
        app = AppServer(device.api)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            result = subprocess.run([NODE, "--test", "--test-reporter=tap", str(JS / "a11y.e2e.mjs")],
                                    capture_output=True, text=True, timeout=300, check=False,
                                    env={**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token,
                                         "GRIMOIRE_CHAT": str(chat), "GRIMOIRE_CAMPAIGN": chat.campaign_id})
        finally:
            app.close()
            thread.join(10)
    assert result.returncode == 0, result.stdout[-12000:] + result.stderr[-3000:]
    assert "# pass 1" in result.stdout
