"""Prompt rendering: every string sent to the LLM lives in <repo>/templates.

Modules gather data and call render(); the templates own the text, so prompts
are editable without touching code. templates/README.md documents each
template's variables. Jinja's auto-reload picks up template edits live.
"""

from __future__ import annotations

import functools
import random
import threading

from jinja2 import Environment, FileSystemLoader, StrictUndefined, pass_context

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


#: The RNG a detached render draws from, per thread (see `render_detached`).
_detached = threading.local()
_detached_envs: dict[int, Environment] = {}


@pass_context
def _detached_random(context, seq):
    """Jinja's `random` filter, drawing from the detached render's own RNG."""
    try:
        return _detached.rng.choice(seq)
    except IndexError:
        return context.environment.undefined("No random item, sequence was empty.")


def _detached_lipsum(*_args, **_kwargs) -> str:
    return ""  # draws from the process RNG with no seam; a detached render just differs


def _detached_env() -> Environment:
    """An overlay of `_env()` whose randomness never touches the process RNG.

    Its own template cache, so a template compiled here (bound to these
    filters) is never handed to an ordinary render, and keyed to the current
    `_env()`, which tests rebuild when they point GRIMOIRE_TEMPLATES elsewhere.
    """
    base = _env()
    env = _detached_envs.get(id(base))
    if env is None:
        env = base.overlay(cache_size=400)
        env.filters = {**base.filters, "random": _detached_random}
        env.globals = {**base.globals, "lipsum": _detached_lipsum}
        _detached_envs.clear()
        _detached_envs[id(base)] = env
    return env


def render_detached(_template: str, rng: random.Random, **context) -> str:
    """`render`, but every draw a template makes comes from `rng`, never from
    the process-wide generator another thread may be drawing from. Used for a
    render whose output is only compared, never sent (#131)."""
    previous = getattr(_detached, "rng", None)
    _detached.rng = rng
    try:
        return _detached_env().get_template(_template).render(**context)
    finally:
        _detached.rng = previous
