"""Explicit campaign opinions over global play defaults (#73).

Absence inherits; zero and an empty prompt are opinions. Validate the entire
partial update before touching its one record, and stamp only a durable change.
Reuses the config parsers, frontmatter line-boundary rules and atomic publisher;
new play settings extend this allowlist rather than introducing another overlay.
"""

from __future__ import annotations

import json
import re

from . import atomic, config, frontmatter, locks, paths, revision
from .campaigns import paths as campaigns_paths
from .campaigns import read as campaigns_read

COUNT_DEFAULTS = {
    "context_scan_depth": config.DEFAULT_SCAN_DEPTH,
    "context_budget": config.DEFAULT_CONTEXT_BUDGET,
    "recap_depth": config.DEFAULT_RECAP_DEPTH,
    "archive_depth": config.DEFAULT_ARCHIVE_DEPTH,
    "rolling_summary_every": config.DEFAULT_ROLLING_SUMMARY_EVERY,
    "scene_break_every": config.DEFAULT_SCENE_BREAK_EVERY,
}
KEYS = (*COUNT_DEFAULTS, "system_prompt", "user_label", "assistant_label")
_DECIMAL = re.compile(r"[0-9]+\Z")
_MAX_COUNT = "2147483647"
_PROMPT_KEY = "system_prompt_json"


def _valid(key: str, value: object) -> bool:
    if not isinstance(value, str):
        return False
    # JSON permits escaped surrogates, but neither UTF-8 records nor HTTP
    # responses can carry them. Refuse before any part of a write is published.
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if key in COUNT_DEFAULTS:
        # Bound before int(): requests must not trigger Python's huge-integer
        # conversion limit, and every accepted count fits Android's UI too.
        return (len(value) <= len(_MAX_COUNT) and _DECIMAL.fullmatch(value) is not None
                and int(value) <= int(_MAX_COUNT))
    if key == "system_prompt":
        return True
    return key in KEYS and bool(value.strip()) and not frontmatter.breaks_line(value)


def _refusal(key: str) -> str:
    """What a refused value must look like, said where the field is named.

    The page clears a numeric field to retype it, so an empty count is the
    common refusal; "invalid campaign setting" alone reads as a bug.
    """
    if key in COUNT_DEFAULTS:
        return f"{key} must be a whole number, zero or more"
    if key == "system_prompt":
        return "system_prompt must be text"
    return f"{key} must be a single non-blank line"


def _record(cid: str) -> tuple[dict[str, str], str]:
    try:
        return frontmatter.read_record(campaigns_paths.campaign_meta_path(cid), "campaign.md")
    except FileNotFoundError:
        raise campaigns_paths.CampaignNotFound(cid) from None


def _overrides(meta: dict[str, str]) -> dict[str, str]:
    own = {key: meta[key] for key in KEYS if key != "system_prompt"
           and key in meta and _valid(key, meta[key])}
    raw = meta.get(_PROMPT_KEY, "")
    # Only a JSON string scalar is a prompt. Do not send arbitrary containers
    # through the decoder: a deeply nested manual value can exhaust its stack.
    if not raw.lstrip().startswith('"'):
        return own
    try:
        prompt = json.loads(raw)
    except (ValueError, TypeError):
        pass
    else:
        if _valid("system_prompt", prompt):
            own["system_prompt"] = prompt
    return own


def effective_config(cid: str) -> dict[str, str]:
    """Full config for internal consumers; a missing campaign id reads global."""
    cfg = config.read_config()
    if cid:
        cfg.update(_overrides(_record(cid)[0]))
    return cfg


def count(cid: str, key: str) -> int:
    """An effective count, preserving the global parser's legacy tolerance."""
    default = COUNT_DEFAULTS[key]
    cfg = effective_config(cid)
    if key in {"rolling_summary_every", "scene_break_every"}:
        return config._every(key, default, cfg)
    return config._whole(cfg.get(key, default), default)


def view(cid: str) -> dict:
    """The public allowlisted view, with no credentials or unrelated config."""
    own = _overrides(_record(cid)[0])
    cfg = config.read_config()
    inherited = {key: cfg[key] for key in KEYS}
    return {"overrides": own, "inherited": inherited, "effective": {**inherited, **own}}


def write(cid: str, fields: dict) -> None:
    """Apply one validated partial opinion under the campaign's write lock."""
    if not isinstance(fields, dict):
        raise ValueError("settings must be an object")
    for key, value in fields.items():
        if key not in KEYS:
            # Keys come from JSON too: an escaped surrogate must not poison
            # the UTF-8 error response while we refuse an unknown field.
            raise ValueError(f"invalid campaign setting: {key!a}")
        if value is not None and not _valid(key, value):
            raise ValueError(_refusal(key))
    with locks.campaign_lock(cid):
        meta, body = _record(cid)
        updated = dict(meta)
        for key, value in fields.items():
            stored_key = _PROMPT_KEY if key == "system_prompt" else key
            if value is None:
                updated.pop(stored_key, None)
            else:
                updated[stored_key] = json.dumps(value, ensure_ascii=True) if key == "system_prompt" else value
        if updated == meta:
            return
        updated["updated"] = paths.now_iso()
        atomic.write_text(campaigns_paths.campaign_meta_path(cid),
                          frontmatter.dump_frontmatter(updated, body))
        campaigns_read.touch_quietly(cid)
        revision.bump(cid)
