"""config.md read/write (frontmatter only)."""

from __future__ import annotations

from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import ensure_home, home
from . import atomic

DEFAULT_MODEL = "anthropic/claude-opus-4.1"
DEFAULT_THEME = "codex"
DEFAULT_SCAN_DEPTH = "8"
DEFAULT_RECAP_DEPTH = "5"
DEFAULT_USER_LABEL = "You"
DEFAULT_ASSISTANT_LABEL = "Grimoire"
DEFAULT_CLAUDE_MODEL = "opus"
# The global scope of the response-preset cascade. These MUST be listed here:
# read_config() narrows its return to _CONFIG_KEYS, so a key omitted from this
# tuple is silently dropped and the global scope resolves as if unset — no
# error, just the wrong budget.
_LENGTH_KEYS = ("response_preset", "length_reply_words", "length_blocks",
                "length_paragraphs", "length_speakers", "length_blocks_per_speaker")

_CONFIG_KEYS = ("theme", "context_scan_depth", "system_prompt",
                "quote_color", "recap_depth", "user_label", "assistant_label",
                "default_style_id", "active_connection_id") + _LENGTH_KEYS


def _config_path():
    return home() / "config.md"


def read_config() -> dict[str, str]:
    ensure_home()
    path = _config_path()
    defaults = {"theme": DEFAULT_THEME,
                "context_scan_depth": DEFAULT_SCAN_DEPTH, "system_prompt": "", "quote_color": "off",
                "recap_depth": DEFAULT_RECAP_DEPTH,
                "user_label": DEFAULT_USER_LABEL, "assistant_label": DEFAULT_ASSISTANT_LABEL,
                "default_style_id": "", "active_connection_id": "",
                **{k: "" for k in _LENGTH_KEYS}}
    if not path.exists():
        atomic.write_text(path, dump_frontmatter(defaults, ""))
        return defaults
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return {k: meta.get(k, default) for k, default in defaults.items()}


def write_config(**fields: str) -> dict[str, str]:
    # Merge onto the file's RAW frontmatter (not read_config()'s narrowed
    # reconstruction) so any key not in _CONFIG_KEYS — including the legacy
    # openrouter_key/model/provider/claude_model fields on a pre-migration
    # install — survives every write untouched. This is what makes the
    # design spec's "legacy fields stay physically present for recovery if
    # llm_connections/ is ever deleted" claim actually true: migration's own
    # first write (ensure_migrated's config.write_config(active_connection_id=...))
    # would otherwise silently erase them immediately.
    ensure_home()
    path = _config_path()
    raw, _ = parse_frontmatter(path.read_text(encoding="utf-8")) if path.exists() else ({}, "")
    for key, value in fields.items():
        if key in _CONFIG_KEYS and value is not None:
            raw[key] = value
    atomic.write_text(path, dump_frontmatter(raw, ""))
    return read_config()
