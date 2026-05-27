"""Narrator response mode: campaign default + per-scene override.

Three values are supported:

- ``"all_at_once"`` — the narrator emits one combined response covering
  every present character in a single post.
- ``"per_character"`` — the narrator emits a single LLM response using
  XML character tags, parsed into separate per-character posts.
- ``"per_character_multi_call"`` — the orchestrator makes one LLM call
  per character in a speaker loop, with player interject control.

The setting lives on the campaign (in the ``campaigns.config`` JSON column
under the ``narrator`` namespace) and may be overridden per-scene (in the
scene YAML sidecar's ``narrator_response_mode`` field). Both surfaces are
read here by :func:`effective_response_mode` so callers don't have to
remember the precedence rules.
"""

from __future__ import annotations

import json
from typing import Any

ALL_AT_ONCE = "all_at_once"
PER_CHARACTER = "per_character"
PER_CHARACTER_MULTI_CALL = "per_character_multi_call"
DEFAULT_RESPONSE_MODE = ALL_AT_ONCE
RESPONSE_MODES: tuple[str, ...] = (ALL_AT_ONCE, PER_CHARACTER, PER_CHARACTER_MULTI_CALL)


def normalize_response_mode(value: Any) -> str | None:
    """Return ``value`` if it's a recognised mode, else ``None``.

    Used at both write and read time so a hand-edited YAML / DB row with a
    typo doesn't poison the resolver — it just falls back to the campaign
    default (or the global default).
    """
    if isinstance(value, str) and value in RESPONSE_MODES:
        return value
    return None


def campaign_response_mode(campaign_row: dict | None) -> str:
    """Resolve the campaign-level default from a ``campaigns`` row.

    Reads ``row["config"]`` (a JSON string), looks up the ``narrator``
    namespace, and pulls ``response_mode``. Falls back to
    :data:`DEFAULT_RESPONSE_MODE` whenever the value is missing or invalid.
    """
    if not campaign_row:
        return DEFAULT_RESPONSE_MODE
    raw = campaign_row.get("config")
    if not raw:
        return DEFAULT_RESPONSE_MODE
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return DEFAULT_RESPONSE_MODE
    if not isinstance(data, dict):
        return DEFAULT_RESPONSE_MODE
    narrator = data.get("narrator")
    if not isinstance(narrator, dict):
        return DEFAULT_RESPONSE_MODE
    mode = normalize_response_mode(narrator.get("response_mode"))
    return mode or DEFAULT_RESPONSE_MODE


def effective_response_mode(
    *,
    scene_override: str | None,
    campaign_row: dict | None,
) -> str:
    """Resolve the mode that should govern an active scene.

    Scene-level override wins when set; otherwise the campaign default
    wins; otherwise :data:`DEFAULT_RESPONSE_MODE`.
    """
    override = normalize_response_mode(scene_override)
    if override is not None:
        return override
    return campaign_response_mode(campaign_row)
