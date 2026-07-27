"""Prose style guides: named prompt-fragment presets selectable at three
nested levels (global default -> campaign default -> scene override).

Built-in genre presets ship as markdown+frontmatter files under
templates/styles/ (resolved via prompts.templates_dir(), the same
GRIMOIRE_TEMPLATES-aware path the Android build already relies on).
User-authored styles live in <GRIMOIRE_HOME>/styles/ and are the only ones
that can be created, edited, or deleted — mirrors the built-in/user-content
split in store/calendars/plugins.py.
"""

from __future__ import annotations

from pathlib import Path

from .. import prompts
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import home, natural_key, slugify, uniquify


class StyleNotFound(Exception):
    pass


class BuiltInStyleImmutable(Exception):
    pass


def _safe(sid: str) -> bool:
    return sid not in ("", ".", "..") and "/" not in sid and "\\" not in sid


def _builtin_dir() -> Path:
    return prompts.templates_dir() / "styles"


def _custom_dir() -> Path:
    return home() / "styles"


def _builtin_path(sid: str) -> Path:
    return _builtin_dir() / f"{sid}.md"


def _custom_path(sid: str) -> Path:
    return _custom_dir() / f"{sid}.md"


def _tags_list(s: str) -> list[str]:
    return [t for t in s.split(",") if t]


def _meta_dict(sid: str, meta: dict, built_in: bool) -> dict:
    return {"id": sid, "name": meta.get("name", sid), "description": meta.get("description", ""),
            "tags": _tags_list(meta.get("tags", "")), "built_in": built_in}


def _find_path(sid: str) -> tuple[Path, bool] | None:
    if not _safe(sid):
        return None
    p = _custom_path(sid)
    if p.exists():
        return p, False
    p = _builtin_path(sid)
    if p.exists():
        return p, True
    return None


def _list_dir(directory: Path, built_in: bool) -> list[dict]:
    out: list[dict] = []
    if not directory.exists():
        return out
    for p in sorted(directory.glob("*.md")):
        try:
            meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue  # a broken file is skipped, not fatal — same as calendar plugins
        out.append(_meta_dict(p.stem, meta, built_in))
    return out


def list_styles() -> list[dict]:
    """Every style guide (built-in + user-authored), for a UI picker."""
    items = _list_dir(_builtin_dir(), built_in=True) + _list_dir(_custom_dir(), built_in=False)
    items.sort(key=lambda m: natural_key(m["name"]))
    return items


def is_built_in(sid: str) -> bool:
    found = _find_path(sid)
    return found is not None and found[1]


def exists(sid: str) -> bool:
    """Whether `sid` names a readable style. Resolution treats an id that
    doesn't as 'no opinion' and keeps walking outward, matching the
    long-standing skip-and-fall-back behaviour of style resolution."""
    return _find_path(sid) is not None


def read_style(sid: str) -> dict:
    found = _find_path(sid)
    if found is None:
        raise StyleNotFound(sid)
    p, built_in = found
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"meta": _meta_dict(sid, meta, built_in), "body": body}


def create_style(name: str, description: str = "", tags: list[str] | None = None, body: str = "") -> str:
    _custom_dir().mkdir(parents=True, exist_ok=True)

    def exists(c: str) -> bool:
        return _custom_path(c).exists() or _builtin_path(c).exists()

    sid = uniquify(slugify(name), exists)
    meta = {"name": name, "description": description, "tags": ",".join(tags or [])}
    _custom_path(sid).write_text(dump_frontmatter(meta, body), encoding="utf-8")
    return sid


def update_style(sid: str, *, name: str | None = None, description: str | None = None,
                 tags: list[str] | None = None, body: str | None = None) -> None:
    if is_built_in(sid):
        raise BuiltInStyleImmutable(sid)
    p = _custom_path(sid)
    if not _safe(sid) or not p.exists():
        raise StyleNotFound(sid)
    meta, cur_body = parse_frontmatter(p.read_text(encoding="utf-8"))
    if name is not None:
        meta["name"] = name
    if description is not None:
        meta["description"] = description
    if tags is not None:
        meta["tags"] = ",".join(tags)
    new_body = cur_body if body is None else body
    p.write_text(dump_frontmatter(meta, new_body), encoding="utf-8")


def delete_style(sid: str) -> None:
    if is_built_in(sid):
        raise BuiltInStyleImmutable(sid)
    p = _custom_path(sid)
    if not _safe(sid) or not p.exists():
        raise StyleNotFound(sid)
    p.unlink()


def duplicate_style(sid: str) -> str:
    src = read_style(sid)
    return create_style(f"{src['meta']['name']} (copy)", src["meta"]["description"],
                        src["meta"]["tags"], src["body"])

