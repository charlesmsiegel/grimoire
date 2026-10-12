"""Prompt rendering: every string sent to the LLM lives in <repo>/templates.

Modules gather data and call render(); the templates own the text, so prompts
are editable without touching code. templates/README.md documents each
template's variables. Jinja's auto-reload picks up template edits live.
"""

from __future__ import annotations

import functools

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from . import schemas

# Public resolver names remain available to rendering callers and tests.
from .template_paths import (  # noqa: F401 -- public compatibility re-exports
    DEFAULT_TEMPLATES_DIR,
    templates_dir,
)


@functools.lru_cache(maxsize=1)
def _env():
    env = Environment(loader=FileSystemLoader(str(templates_dir())),
                      undefined=StrictUndefined)
    # The one spelling of a `generate` schema (01f-C3): a template renders it
    # `{{ schema | schema_json }}`, and `inference.generate` checks the prompt
    # holds exactly this text. Not `tojson`, which HTML-escapes; that stays
    # `decide/system.j2`'s, whose bytes do not move.
    env.filters["schema_json"] = schemas.render
    return env


def render(_template: str, **vars) -> str:
    return _env().get_template(_template).render(**vars)
