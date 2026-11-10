"""108's shell modules that need no browser (routes, drafts), run under Node's own test runner.

Skipped where Node is not installed: these are the shell's client-side contract, and the Python suite is the gate.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

TESTS = Path(__file__).with_name("js")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
@pytest.mark.parametrize("script", sorted(path.name for path in TESTS.glob("*.test.mjs")))
def test_shell_module(script):
    result = subprocess.run(["node", "--test", str(TESTS / script)], capture_output=True, text=True, timeout=120,
                            check=False)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
