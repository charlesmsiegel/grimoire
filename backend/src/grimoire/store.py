"""Filesystem-as-database for grimoire: markdown files under ~/.grimoire/."""

from __future__ import annotations

import os
from pathlib import Path


def _needs_quotes(value: str) -> bool:
    if value == "":
        return True
    if value != value.strip():
        return True
    return any(c in value for c in ":#'\"")


def _quote(value: str) -> str:
    if not _needs_quotes(value):
        return value
    return "'" + value.replace("'", "''") + "'"


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split `---`-fenced frontmatter from the body. String scalars only."""
    if not text.startswith("---\n"):
        return {}, text
    rest = text[4:]
    end = rest.find("\n---")
    if end == -1:
        return {}, text
    block = rest[:end]
    after = rest[end + 4:]
    # Consume the newline that closes the `---` line, then one optional blank
    # separator line, so a canonical `---\n...\n---\n\nbody` yields just `body`.
    if after.startswith("\n"):
        after = after[1:]
    if after.startswith("\n"):
        after = after[1:]
    meta: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        meta[key.strip()] = _unquote(value)
    return meta, after


def dump_frontmatter(meta: dict[str, str], body: str) -> str:
    lines = ["---"]
    for key, value in meta.items():
        lines.append(f"{key}: {_quote('' if value is None else str(value))}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + "\n" + body


DEFAULT_MODEL = "anthropic/claude-opus-4.1"
DEFAULT_THEME = "occult"
_CONFIG_KEYS = ("openrouter_key", "model", "theme")


def home() -> Path:
    return Path(os.environ.get("GRIMOIRE_HOME") or (Path.home() / ".grimoire"))


def _ensure_home() -> Path:
    base = home()
    (base / "conversations").mkdir(parents=True, exist_ok=True)
    return base


def _config_path() -> Path:
    return home() / "config.md"


def read_config() -> dict[str, str]:
    _ensure_home()
    path = _config_path()
    if not path.exists():
        defaults = {"openrouter_key": "", "model": DEFAULT_MODEL, "theme": DEFAULT_THEME}
        path.write_text(dump_frontmatter(defaults, ""), encoding="utf-8")
        return defaults
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return {
        "openrouter_key": meta.get("openrouter_key", ""),
        "model": meta.get("model", DEFAULT_MODEL),
        "theme": meta.get("theme", DEFAULT_THEME),
    }


def write_config(**fields: str) -> dict[str, str]:
    cfg = read_config()
    for key, value in fields.items():
        if key in _CONFIG_KEYS and value is not None:
            cfg[key] = value
    _config_path().write_text(dump_frontmatter(cfg, ""), encoding="utf-8")
    return cfg
