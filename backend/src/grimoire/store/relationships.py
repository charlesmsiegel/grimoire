"""Per-campaign relationships: directed feelings (asymmetric) + symmetric bonds among
cast actors. Actor tokens are "<kind>:<id>". Stored at <campaign>/relationships.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import prompts
from . import atomic, characters, overlay, pcs
from .campaigns import paths as campaigns_paths


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "relationships.json"


def read(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {"feelings": {}, "bonds": {}}
    data = json.loads(p.read_text(encoding="utf-8"))
    data.setdefault("feelings", {})
    data.setdefault("bonds", {})
    return data


def _write(cid: str, data: dict) -> None:
    atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")


def feeling_key(a: str, b: str) -> str:
    return f"{a}->{b}"


def bond_key(a: str, b: str) -> str:
    lo, hi = sorted((a, b))
    return f"{lo}|{hi}"


def get_feeling(cid: str, a: str, b: str) -> dict | None:
    return read(cid)["feelings"].get(feeling_key(a, b))


def get_bond(cid: str, a: str, b: str) -> dict | None:
    return read(cid)["bonds"].get(bond_key(a, b))


def set_feeling(cid: str, a: str, b: str, trust: int, affection: int, tension: int, note: str) -> None:
    data = read(cid)
    data["feelings"][feeling_key(a, b)] = {"trust": trust, "affection": affection,
                                           "tension": tension, "note": note}
    _write(cid, data)


def set_bond(cid: str, a: str, b: str, type: str, since_scene: str = "") -> None:
    data = read(cid)
    key = bond_key(a, b)
    existing = data["bonds"].get(key, {})
    data["bonds"][key] = {"type": type, "since_scene": since_scene or existing.get("since_scene", "")}
    _write(cid, data)


def restore_feeling(cid: str, a: str, b: str, record: dict | None) -> None:
    """Put one directed feeling back to a recorded state, or remove it when there
    was none. `set_feeling` has no inverse of its own -- there is no way to spell
    "there was nothing here" in trust/affection/tension -- so `store/undo.py`
    snapshots the record and hands it back (#31).

    One key, never a whole-file restore: other pairs may have moved since.
    """
    data = read(cid)
    key = feeling_key(a, b)
    if record is None:
        if data["feelings"].pop(key, None) is None:
            return
    else:
        data["feelings"][key] = record
    _write(cid, data)


def restore_bond(cid: str, a: str, b: str, record: dict | None) -> None:
    """`restore_feeling` for bonds. `set_bond` cannot express removal either, and
    it preserves `since_scene` across a type change, so putting a bond back means
    putting the whole record back."""
    data = read(cid)
    key = bond_key(a, b)
    if record is None:
        if data["bonds"].pop(key, None) is None:
            return
    else:
        data["bonds"][key] = record
    _write(cid, data)


def actor_name(cid: str, token: str) -> str:
    """Overlay-aware: a thin campaign's cast is mostly inherited (never
    materialized campaign-side), so the name must resolve across the union,
    not just the campaign's own copy."""
    kind, _, aid = token.partition(":")
    try:
        if kind == "pcs":
            return pcs.read_pc(overlay.pc_root(cid, aid), aid)["meta"].get("name", aid)
        return characters.read_character(overlay.char_root(cid, aid), aid)["meta"].get("name", aid)
    except (characters.CharacterNotFound, pcs.PCNotFound):
        return aid


def _render_feeling(f: dict) -> str:
    # the staged-edit diff format (absorb.materialize); the PROMPT line format
    # lives in templates/snippets/feeling_line.j2
    note = f" ({f['note']})" if f.get("note") else ""
    return f"trust {f['trust']}, affection {f['affection']}, tension {f['tension']}{note}"


def render_standing(kind: str, record) -> str:
    """A STORED feeling or bond as the one-line standing the review shows, and
    "" when there is none (#63).

    `_render_feeling` renders a feeling from its four fields and a bond's
    standing is simply its type, so this is a thin dispatch -- but it is the one
    the relationship timeline needs, because that ledger describes records
    rather than staged edits. Its rows are built from what
    `relationships.json` holds either side of a write, never from the `after`
    string an edit travelled with: those agree for anything `materialize`
    staged, and a client-supplied PUT body can carry an `after` that disagrees
    with the payload the write actually used -- which would leave an
    append-only row permanently claiming a standing this store does not hold.

    Tolerant of a record missing an axis, which `_render_feeling` is not: this
    reads records off disk, where a hand edit can leave one out, and a row with
    no text is a smaller loss than a KeyError out of a write that has landed.
    `record` is untyped for the same reason it is `isinstance`-checked: two of
    the three callers hand it a value that reached them as `object` (an
    `undo.snapshot` reading, an `undo.read_value` one), and narrowing at each of
    them would be three copies of the check below.
    """
    if not isinstance(record, dict):
        return ""
    if kind == "bond":
        return record["type"] if isinstance(record.get("type"), str) else ""
    if any(not isinstance(record.get(axis), int) or isinstance(record.get(axis), bool)
           for axis in ("trust", "affection", "tension")):
        return ""
    return _render_feeling(record)


def render_present(cid: str, tokens: list[str], name_of) -> list[str]:
    data = read(cid)
    lines: list[str] = []
    for a in tokens:
        for b in tokens:
            if a == b:
                continue
            f = data["feelings"].get(feeling_key(a, b))
            if f:
                lines.append(prompts.render("snippets/feeling_line.j2",
                                            a=name_of(a), b=name_of(b), f=f))
    for a in tokens:
        for b in tokens:
            if a >= b:  # each unordered pair once (tokens are unique)
                continue
            bd = data["bonds"].get(bond_key(a, b))
            if bd:
                lines.append(prompts.render("snippets/bond_line.j2",
                                            a=name_of(a), b=name_of(b), bond=bd))
    return lines
