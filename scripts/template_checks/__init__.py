"""The checks behind `scripts/verify_templates.py`, one module per family.

Importing this package is the harness's preamble, and it has to run before
anything imports `grimoire`: the app is put on the path from this checkout's
`backend/src`, and `GRIMOIRE_HOME` is pointed at a throwaway directory, so the
store the fixture builds can never land in a real library. Every module here
imports the package first (as `template_checks.<module>`), so no import order
can reach the store before this has run.

`harness` owns the rendering environment and the tally; each check module
exposes its checks, in their running order, as `CHECKS`. The entry script runs
the storeless families, then builds the fixture store (`fixture.build`) and
runs the scene checks over it, then the reach check over everything rendered.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend" / "src"))
os.environ["GRIMOIRE_HOME"] = tempfile.mkdtemp(prefix="grimoire-verify-")
