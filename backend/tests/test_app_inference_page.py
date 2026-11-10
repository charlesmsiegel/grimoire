"""The Models and costs page (112) run over a fake DOM in Node: skipped where Node is not installed."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
PAGE = HERE.parent / "src" / "grimoire" / "app" / "static" / "pages" / "providers" / "page.js"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_page_renders_usage_without_text_artifacts_and_saves_at_the_revision_read():
    done = subprocess.run([NODE, str(HERE / "pages" / "inference_dom.mjs"), PAGE.as_uri()], capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"
