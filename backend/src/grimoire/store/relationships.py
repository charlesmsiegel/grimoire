"""Per-campaign relationships: directed feelings (asymmetric) + symmetric bonds among
cast actors. Actor tokens are "<kind>:<id>". Stored at <campaign>/relationships.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import campaigns, characters, pcs


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


def actor_name(croot, token: str) -> str:
    kind, _, aid = token.partition(":")
    try:
        if kind == "pcs":
            return pcs.read_pc(croot, aid)["meta"]["name"]
        return characters.read_character(croot, aid)["meta"].get("name", aid)
    except (characters.CharacterNotFound, pcs.PCNotFound):
        return aid


def _render_feeling(f: dict) -> str:
    note = f" ({f['note']})" if f.get("note") else ""
    return f"trust {f['trust']}, affection {f['affection']}, tension {f['tension']}{note}"


def render_present(cid: str, tokens: list[str], name_of) -> list[str]:
    data = read(cid)
    lines: list[str] = []
    for a in tokens:
        for b in tokens:
            if a == b:
                continue
            f = data["feelings"].get(feeling_key(a, b))
            if f:
                lines.append(f"{name_of(a)} → {name_of(b)}: {_render_feeling(f)}")
    seen: set[str] = set()
    for a in tokens:
        for b in tokens:
            if a >= b:
                continue
            key = bond_key(a, b)
            if key in seen:
                continue
            bd = data["bonds"].get(key)
            if bd:
                seen.add(key)
                lines.append(f"{name_of(a)} & {name_of(b)}: {bd['type']}")
    return lines
