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
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import home, natural_key, slugify, uniquify

_STYLE_CLEAR = "none"


class PresetNotFound(Exception):
    pass


class BuiltInPresetImmutable(Exception):
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
        row = _meta_dict(p.stem, meta, built_in)
        row["validity"] = validity(row)
        out.append(row)
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
    row = _meta_dict(pid, meta, built_in)
    return {"meta": row, "validity": validity(row)}


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


# Scope names, narrowest first — the order every field is walked in.
_SCOPES = ("turn", "scene", "campaign", "global")


def _override_key(field: str, scope: str) -> str:
    """The loose-override frontmatter key for a field at a given scope.

    style_id is spelled `default_style_id` globally and keeps that spelling:
    renaming it would break every existing install's global style for no
    functional gain.
    """
    if field == "style_id":
        return "default_style_id" if scope == "global" else "style_id"
    return f"length_{field}"


def _supplied_by_preset(meta: dict) -> dict:
    """What the preset named by this scope supplies. A scope naming a missing
    or invalid preset supplies nothing and the walk continues."""
    pid = (meta.get("response_preset") or "").strip()
    if not pid:
        return {}
    try:
        record = read_preset(pid)
    except (PresetNotFound, OSError, UnicodeDecodeError):
        # A damaged or externally-edited file is an invalid record, not a crash:
        # resolution must degrade to "supplies nothing" and keep walking, or a
        # single corrupt preset takes the whole scene down. list_presets already
        # treats these as broken-file cases.
        return {}
    return supplies(record["meta"]) or {}


def _override(meta: dict, field: str, scope: str):
    raw = (meta.get(_override_key(field, scope)) or "").strip()
    if not raw:
        return None
    if field == "style_id":
        return "" if raw == _STYLE_CLEAR else raw
    return lengths.coerce(raw)


def resolve(*, turn: dict | None = None, scene_meta: dict | None = None,
            campaign_meta: dict | None = None, config: dict | None = None) -> dict:
    """The per-field cascade over turn -> scene -> campaign -> global.

    EACH FIELD RESOLVES INDEPENDENTLY. For one field, walk the scopes and take
    the first value found; within a scope, a loose override beats that scope's
    own preset. There is no single "base preset" — that formulation is what made
    a narrower length-only preset wipe a broader style.

    Always returns a COMPLETE dict: style_id plus every knob. The Jinja env runs
    with StrictUndefined, so a missing key is a hard render failure mid-scene.
    """
    from . import styles  # lazy: keeps the store package's import order simple

    scoped = {"turn": turn or {}, "scene": scene_meta or {},
              "campaign": campaign_meta or {}, "global": config or {}}
    presets = {name: _supplied_by_preset(meta) for name, meta in scoped.items()}

    out: dict = {}
    provenance: dict = {}
    for field in ("style_id",) + lengths.KNOBS:
        for scope in _SCOPES:
            for source, value in (("override", _override(scoped[scope], field, scope)),
                                  ("preset", presets[scope].get(field))):
                if value is None:
                    continue
                # An id naming a style that doesn't exist is no opinion, not a
                # clear — the walk continues. "" IS a clear and stops the walk.
                if field == "style_id" and value and not styles.exists(value):
                    continue
                out[field] = value
                provenance[field] = {"scope": scope, "source": source}
                break
            if field in out:
                break
        if field not in out:
            out[field] = "" if field == "style_id" else lengths.PRESETS[lengths.DEFAULT][field]
            provenance[field] = {"scope": "default", "source": "default"}

    out["provenance"] = provenance
    return out


def _length_fields(length_preset: str, knobs: dict | None) -> dict:
    """Frontmatter for the length half, enforcing the tagged union on write.

    Exactly one form is stored. Switching form must ERASE the other, or a record
    ends up carrying both on disk — which read_preset resolves by silently
    preferring length_preset, exactly the ambiguity the union exists to remove.
    """
    if length_preset and knobs:
        raise ValueError("a preset carries either length_preset or explicit knobs, not both")
    out = {"length_preset": length_preset or ""}
    for knob in lengths.KNOBS:
        out[knob] = str((knobs or {}).get(knob, "")) if (knobs or {}).get(knob) else ""
    return out


def create_preset(name: str, description: str = "", style_id: str = "",
                  length_preset: str = "", knobs: dict | None = None) -> str:
    _custom_dir().mkdir(parents=True, exist_ok=True)

    def exists(c: str) -> bool:
        return (_custom_dir() / f"{c}.md").exists() or (_builtin_dir() / f"{c}.md").exists()

    pid = uniquify(slugify(name), exists)
    meta = {"name": name, "description": description, "style_id": style_id,
            **_length_fields(length_preset, knobs)}
    (_custom_dir() / f"{pid}.md").write_text(dump_frontmatter(meta, ""), encoding="utf-8")
    return pid


def update_preset(pid: str, *, name: str | None = None, description: str | None = None,
                  style_id: str | None = None, length_preset: str | None = None,
                  knobs: dict | None = None) -> None:
    if is_built_in(pid):
        raise BuiltInPresetImmutable(pid)
    p = _custom_dir() / f"{pid}.md"
    if not _safe(pid) or not p.exists():
        raise PresetNotFound(pid)
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    if name is not None:
        meta["name"] = name
    if description is not None:
        meta["description"] = description
    if style_id is not None:
        meta["style_id"] = style_id
    if length_preset is not None or knobs is not None:
        meta.update(_length_fields(
            length_preset if length_preset is not None else meta.get("length_preset", ""),
            knobs))
    p.write_text(dump_frontmatter(meta, ""), encoding="utf-8")


def delete_preset(pid: str) -> None:
    if is_built_in(pid):
        raise BuiltInPresetImmutable(pid)
    p = _custom_dir() / f"{pid}.md"
    if not _safe(pid) or not p.exists():
        raise PresetNotFound(pid)
    p.unlink()


def validity(meta: dict) -> dict:
    """What this record's fields were understood to mean — {"valid", "issues"}.

    Reporting only; resolution's fail-open behaviour is unchanged. Without this
    a preset can look selected while supplying nothing, which is
    indistinguishable from ordinary inheritance.
    """
    issues: list[str] = []
    named = (meta.get("length_preset") or "").strip()
    if named and lengths.get(named) is None:
        return {"valid": False,
                "issues": [f"unknown length preset '{named}' — this preset supplies nothing"]}
    if not named:
        for knob in lengths.KNOBS:
            raw = (meta.get(knob) or "").strip()
            if raw and lengths.coerce(raw) is None:
                issues.append(f"{knob}: '{raw}' is not a positive whole number — ignored")
    return {"valid": True, "issues": issues}


def duplicate_preset(pid: str) -> str:
    src = read_preset(pid)["meta"]
    knobs = {k: lengths.coerce(src.get(k, "")) for k in lengths.KNOBS}
    knobs = {k: v for k, v in knobs.items() if v is not None}
    return create_preset(f"{src['name']} (copy)", src.get("description", ""),
                         src.get("style_id", ""), src.get("length_preset", ""),
                         knobs or None)
