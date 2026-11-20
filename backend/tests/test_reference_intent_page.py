"""070's proving scenario through the chat page under Node (``js/reference.e2e.mjs``): a doorway circled on an
imported sketch, offered as the draft action "I hide here (Reference: doorway)", posted with the marked sketch as
the post's picture. Afterwards the library on disk has gained the turn and that one picture, and nothing else: no
map, actor, scene or campaign record was written for the reference, and no model was asked anything.

Synthetic library only; every picture is a synthetic byte string. Skipped where Node is not installed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from textapi_fixtures import Device, desktop_library

from grimoire.app import AppServer
from grimoire.app.appapi import AppApi

TESTS = Path(__file__).with_name("js")
NODE = shutil.which("node")


def _files(folder: Path) -> dict[str, bytes]:
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_a_circled_doorway_is_carried_with_the_post_as_uncommitted_intent(tmp_path):
    desk, chat = desktop_library(tmp_path / "desk")
    with Device(desk, runtime=False) as device:  # no model: nothing could be sent one
        AppApi(device.api).call("continue", {"chat": str(chat)})  # this device takes its seat, as "Take your seat" would
        before = _files(desk)
        app = AppServer(device.api)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            result = subprocess.run(
                [NODE, "--test", "--test-reporter=tap", str(TESTS / "reference.e2e.mjs")], capture_output=True, text=True,
                timeout=240, check=False,
                env={**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token, "GRIMOIRE_CHAT": str(chat)})
        finally:
            app.close()
            thread.join(10)
        assert result.returncode == 0, result.stdout[-8000:] + result.stderr[-3000:]
        assert "# pass 1" in result.stdout
        after = _files(desk)
        assert device.api.store.load().problems == ()
    assert all(before[name] == after.get(name) for name in before), "nothing already there was rewritten"
    added = sorted(set(after) - set(before))
    ledger = [name for name in added if name.startswith("authority/security/")]
    written = [name for name in added if name not in ledger]
    # one post turn, one picture (its blob and its record) and the post's own picture slot: no map, actor,
    # scene, campaign or chat record, and no second picture (the imported sketch itself is kept nowhere)
    kinds = sorted(name.split("/")[0] if name.startswith("assets/") else name.split("/")[2] for name in written)
    assert kinds == ["assets", "images", "scope-art", "turns"], written
    (turn,) = [Path(name).stem for name in written if "/turns/" in name]
    assert [Path(name).name for name in written if "/scope-art/" in name] == [f"post.{turn}.json"]
    # the security ledger only records the owner's writes of exactly those files
    decided = {entry["decision"]["resource"] for name in ledger
               for entry in json.loads(after[name])["entries"] if entry.get("kind") == "decision"}
    assert decided <= set(written) and decided, decided
