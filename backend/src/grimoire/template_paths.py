"""Template location shared by rendering and cache fingerprints.

Keep this resolver below schemas/tokens so token measures can use compiled
without closing compiled -> prompts -> schemas -> tokens -> compiled.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"  # paths-ok: resolver default only; GRIMOIRE_TEMPLATES supplies packaged hosts


def templates_dir() -> Path:
    """Resolve the extraction override or desktop default template directory."""
    env = os.environ.get('GRIMOIRE_TEMPLATES')
    return Path(env) if env else DEFAULT_TEMPLATES_DIR
