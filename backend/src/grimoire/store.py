"""Filesystem-as-database for grimoire: markdown files under ~/.grimoire/."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
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


ROLE_TO_LABEL = {"user": "You", "assistant": "Grimoire"}
LABEL_TO_ROLE = {"You": "user", "Grimoire": "assistant"}
_MARKER = re.compile(r"^\*\*(You|Grimoire):\*\*[ ]?", re.MULTILINE)


class ConversationNotFound(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "chat"


def _conv_path(cid: str) -> Path:
    return home() / "conversations" / f"{cid}.md"


def create_conversation(title: str) -> str:
    _ensure_home()
    now = _now_iso()
    cid = f"{now[:10]}-{_slugify(title)}"
    path = _conv_path(cid)
    n = 2
    while path.exists():
        cid = f"{now[:10]}-{_slugify(title)}-{n}"
        path = _conv_path(cid)
        n += 1
    meta = {"title": title, "model": read_config()["model"], "created": now, "updated": now}
    path.write_text(dump_frontmatter(meta, ""), encoding="utf-8")
    return cid


def list_conversations() -> list[dict[str, str]]:
    _ensure_home()
    out = []
    for path in (home() / "conversations").glob("*.md"):
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        out.append({
            "id": path.stem,
            "title": meta.get("title", path.stem),
            "model": meta.get("model", ""),
            "created": meta.get("created", ""),
            "updated": meta.get("updated", ""),
        })
    out.sort(key=lambda m: m["updated"], reverse=True)
    return out


def _parse_messages(body: str) -> list[dict[str, str]]:
    matches = list(_MARKER.finditer(body))
    messages = []
    for i, m in enumerate(matches):
        label = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        messages.append({"role": LABEL_TO_ROLE[label], "content": body[start:end].strip()})
    return messages


def read_conversation(cid: str) -> dict:
    path = _conv_path(cid)
    if not path.exists():
        raise ConversationNotFound(cid)
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    return {"meta": {"id": cid, **meta}, "messages": _parse_messages(body)}


def rename_conversation(cid: str, title: str) -> str:
    """Update the title and rename the file to match. Returns the new id.

    The creation date prefix is preserved and `updated` is left unchanged, so
    only the slug changes and the list order is stable.
    """
    path = _conv_path(cid)
    if not path.exists():
        raise ConversationNotFound(cid)
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    meta["title"] = title

    prefix = meta.get("created", _now_iso())[:10]
    new_cid = f"{prefix}-{_slugify(title)}"
    new_path = _conv_path(new_cid)
    n = 2
    while new_path != path and new_path.exists():
        new_cid = f"{prefix}-{_slugify(title)}-{n}"
        new_path = _conv_path(new_cid)
        n += 1

    path.write_text(dump_frontmatter(meta, body), encoding="utf-8")
    if new_path != path:
        path.rename(new_path)
    return new_cid


def delete_conversation(cid: str) -> None:
    path = _conv_path(cid)
    if not path.exists():
        raise ConversationNotFound(cid)
    path.unlink()


def append_message(cid: str, role: str, content: str) -> None:
    path = _conv_path(cid)
    if not path.exists():
        raise ConversationNotFound(cid)
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    label = ROLE_TO_LABEL[role]
    block = f"**{label}:** {content.strip()}\n"
    body = (body.rstrip() + "\n\n" + block) if body.strip() else block
    meta["updated"] = _now_iso()
    path.write_text(dump_frontmatter(meta, body), encoding="utf-8")
