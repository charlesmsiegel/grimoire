"""Prompt rendering: every string sent to the LLM lives in <repo>/templates.

Modules gather data and call render(); the templates own the text, so prompts
are editable without touching code. templates/README.md documents each
template's variables. Jinja's auto-reload picks up template edits live.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

DEFAULT_TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"  # paths-ok: DEFAULT_TEMPLATES_DIR only; GRIMOIRE_TEMPLATES overrides it on Android


def templates_dir() -> Path:
    """Resolve the template directory.

    ``GRIMOIRE_TEMPLATES`` overrides the repo-relative default for builds where
    the source tree isn't laid out as a checkout (the Android APK extracts
    templates to app storage).
    """
    env = os.environ.get("GRIMOIRE_TEMPLATES")
    return Path(env) if env else DEFAULT_TEMPLATES_DIR


@functools.lru_cache(maxsize=1)
def _env():
    return Environment(loader=FileSystemLoader(str(templates_dir())),
                       undefined=StrictUndefined)


def render(_template: str, **vars) -> str:
    return _env().get_template(_template).render(**vars)
