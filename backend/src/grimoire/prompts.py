"""Prompt rendering: every string sent to the LLM lives in <repo>/templates.

Modules gather data and call render(); the templates own the text, so prompts
are editable without touching code. templates/README.md documents each
template's variables. Jinja's auto-reload picks up template edits live.
"""

from __future__ import annotations

import functools
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"


@functools.lru_cache(maxsize=1)
def _env():
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)),
                       undefined=StrictUndefined)


def render(_template: str, **vars) -> str:
    return _env().get_template(_template).render(**vars)
