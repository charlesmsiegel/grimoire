"""Per-campaign relationships: directed feelings (asymmetric) + symmetric bonds among
cast actors. Actor tokens are "<kind>:<id>". Stored at <campaign>/relationships.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import campaigns, characters, overlay, pcs


def _path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "relationships.json"


def read(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {"feelings": {}, "bonds": {}}
    data = json.loads(p.read_text(encoding="utf-8"))
    data.setdefault("feelings", {})
    data.setdefault("bonds", {})
    return data


def _write(cid: str, data: dict) -> None:
    _path(cid).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def render_present(cid: str, tokens: list[str], name_of) -> list[str]:
    from .. import prompts
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
