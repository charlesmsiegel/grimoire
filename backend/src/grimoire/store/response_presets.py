"""Response presets: saveable records pairing a prose style with a length budget.

Built-ins ship under templates/response_presets/ (resolved via
prompts.templates_dir(), so the Android build's GRIMOIRE_TEMPLATES indirection
works unchanged); user-authored ones live in <GRIMOIRE_HOME>/response_presets/
and are the only editable kind. Mirrors the split in store/styles.py.

The governing rule, which every function here serves: A PRESET SUPPLIES EXACTLY
THE FIELDS IT SPECIFIES. A field it does not specify is not defaulted — the
preset has no opinion and resolution walks past it to the next scope. Defaulting
an unspecified field is what makes a length choice silently clobber a style.
"""

from __future__ import annotations

from pathlib import Path

from .. import prompts
from . import lengths
from .frontmatter import parse_frontmatter
from .paths import home, natural_key

_STYLE_CLEAR = "none"


class PresetNotFound(Exception):
    pass


def _safe(pid: str) -> bool:
    return pid not in ("", ".", "..") and "/" not in pid and "\\" not in pid


def _builtin_dir() -> Path:
    return prompts.templates_dir() / "response_presets"


def _custom_dir() -> Path:
    return home() / "response_presets"


def _find_path(pid: str) -> tuple[Path, bool] | None:
    if not _safe(pid):
        return None
    p = _custom_dir() / f"{pid}.md"
    if p.exists():
        return p, False
    p = _builtin_dir() / f"{pid}.md"
    if p.exists():
        return p, True
    return None


def _meta_dict(pid: str, meta: dict, built_in: bool) -> dict:
    return {"id": pid, "name": meta.get("name", pid),
            "description": meta.get("description", ""),
            "style_id": meta.get("style_id", ""),
            "length_preset": meta.get("length_preset", ""),
            **{k: meta.get(k, "") for k in lengths.KNOBS},
            "built_in": built_in}


def _list_dir(directory: Path, built_in: bool) -> list[dict]:
    out: list[dict] = []
    if not directory.exists():
        return out
    for p in sorted(directory.glob("*.md")):
        try:
            meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue  # a broken file is skipped, not fatal — as in styles.py
        out.append(_meta_dict(p.stem, meta, built_in))
    return out


def list_presets() -> list[dict]:
    """Every response preset (built-in + user-authored), for a UI picker."""
    items = _list_dir(_builtin_dir(), built_in=True) + _list_dir(_custom_dir(), built_in=False)
    items.sort(key=lambda m: natural_key(m["name"]))
    return items


def is_built_in(pid: str) -> bool:
    found = _find_path(pid)
    return found is not None and found[1]


def read_preset(pid: str) -> dict:
    found = _find_path(pid)
    if found is None:
        raise PresetNotFound(pid)
    p, built_in = found
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"meta": _meta_dict(pid, meta, built_in)}


def supplies(meta: dict) -> dict | None:
    """The fields this record specifies, or None if the record is invalid.

    Keys are drawn from lengths.KNOBS plus "style_id". A key's ABSENCE means
    "no opinion", which is materially different from a falsy value: a supplied
    style_id of "" is an explicit clear (the `none` sentinel).
    """
    named = (meta.get("length_preset") or "").strip()
    out: dict = {}

    if named:
        knobs = lengths.get(named)
        if knobs is None:
            return None  # invalid record: supplies nothing, not even its style
        out.update(knobs)  # named form ignores explicit keys unconditionally
    else:
        for knob in lengths.KNOBS:
            value = lengths.coerce(meta.get(knob, ""))
            if value is not None:
                out[knob] = value

    style = (meta.get("style_id") or "").strip()
    if style == _STYLE_CLEAR:
        out["style_id"] = ""
    elif style:
        out["style_id"] = style
    return out
