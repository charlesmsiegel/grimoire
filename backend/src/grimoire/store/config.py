"""config.md read/write (frontmatter only)."""

from __future__ import annotations

from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import ensure_home, home

DEFAULT_MODEL = "anthropic/claude-opus-4.1"
DEFAULT_THEME = "occult"
DEFAULT_SCAN_DEPTH = "8"
_CONFIG_KEYS = ("openrouter_key", "model", "theme", "context_scan_depth", "system_prompt", "quote_color")


def _config_path():
    return home() / "config.md"


def read_config() -> dict[str, str]:
    ensure_home()
    path = _config_path()
    defaults = {"openrouter_key": "", "model": DEFAULT_MODEL, "theme": DEFAULT_THEME,
                "context_scan_depth": DEFAULT_SCAN_DEPTH, "system_prompt": "", "quote_color": "off"}
    if not path.exists():
        path.write_text(dump_frontmatter(defaults, ""), encoding="utf-8")
        return defaults
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return {k: meta.get(k, default) for k, default in defaults.items()}


def write_config(**fields: str) -> dict[str, str]:
    cfg = read_config()
    for key, value in fields.items():
        if key in _CONFIG_KEYS and value is not None:
            cfg[key] = value
    _config_path().write_text(dump_frontmatter(cfg, ""), encoding="utf-8")
    return cfg
