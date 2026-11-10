"""108's shell under Node: its pure modules (routes, drafts) with Node's own test runner, and the shell end to
end in a stand-in DOM (``js/fakedom.mjs``) against a real app server over a synthetic library.

Skipped where Node is not installed: the Python suite is the gate, and these are the shell's client side.
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

TESTS = Path(__file__).with_name("js")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node is not installed")


def _node(script: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([NODE, "--test", "--test-reporter=tap", str(script)], capture_output=True, text=True, timeout=180, check=False,
                          env={**os.environ, **(env or {})})


@needs_node
@pytest.mark.parametrize("script", sorted(path.name for path in TESTS.glob("*.test.mjs")))
def test_shell_module(script):
    result = _node(TESTS / script)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]


@needs_node
def test_the_shell_end_to_end(tmp_path):
    desk, chat = desktop_library(tmp_path / "desk")
    with Device(desk, [say("Heard you.")]) as device:
        app = AppServer(device.api)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            result = _node(TESTS / "shell.e2e.mjs", {"GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token,
                                                         "GRIMOIRE_CHAT": str(chat)})
        finally:
            app.close()
            thread.join(10)
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
    assert "# pass 1" in result.stdout
