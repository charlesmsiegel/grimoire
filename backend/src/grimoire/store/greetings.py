"""World greeting objects + the plot map.

A greeting is a markdown file under <world>/greetings/<gid>.md that references a
character + version and carries scalar gating attributes. The directed plot-map
edges (leads_to / excludes) are nested data, so they live in <world>/plotmap.json
keyed by greeting id.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import characters
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import slugify, uniquify


class GreetingNotFound(Exception):
    pass


def _safe(part: str) -> bool:
    return part not in ("", ".", "..") and "/" not in part and "\\" not in part


def _greetings_dir(root: Path) -> Path:
    return root / "greetings"


def _greeting_path(root: Path, gid: str) -> Path:
    return _greetings_dir(root) / f"{gid}.md"


def _plotmap_path(root: Path) -> Path:
    return root / "plotmap.json"


def _tags_list(s: str) -> list[str]:
    return [t for t in s.split(",") if t]


def _meta_dict(gid: str, meta: dict) -> dict:
    return {
        "id": gid,
        "name": meta.get("name", gid),
        "character": meta.get("character", ""),
        "version": meta.get("version", ""),
        "requires_tags": _tags_list(meta.get("requires_tags", "")),
        "predecessor_join": meta.get("predecessor_join", "all"),
    }


def create_greeting(root: Path, name: str, character: str, version: str, body: str = "",
                    requires_tags: list[str] | None = None, predecessor_join: str = "all") -> str:
    _greetings_dir(root).mkdir(parents=True, exist_ok=True)
    gid = uniquify(slugify(name), lambda c: _greeting_path(root, c).exists())
    meta = {"name": name, "character": character, "version": version,
            "requires_tags": ",".join(requires_tags or []), "predecessor_join": predecessor_join}
    _greeting_path(root, gid).write_text(dump_frontmatter(meta, body), encoding="utf-8")
    return gid


def read_greeting(root: Path, gid: str) -> dict:
    p = _greeting_path(root, gid)
    if not _safe(gid) or not p.exists():
        raise GreetingNotFound(gid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"meta": _meta_dict(gid, meta), "body": body}


def list_greetings(root: Path) -> list[dict]:
    d = _greetings_dir(root)
    if not d.exists():
        return []
    return [read_greeting(root, p.stem)["meta"] for p in sorted(d.glob("*.md"))]


def update_greeting(root: Path, gid: str, *, name: str | None = None, body: str | None = None,
                    requires_tags: list[str] | None = None, predecessor_join: str | None = None) -> None:
    p = _greeting_path(root, gid)
    if not _safe(gid) or not p.exists():
        raise GreetingNotFound(gid)
    meta, cur_body = parse_frontmatter(p.read_text(encoding="utf-8"))
    if name is not None:
        meta["name"] = name
    if requires_tags is not None:
        meta["requires_tags"] = ",".join(requires_tags)
    if predecessor_join is not None:
        meta["predecessor_join"] = predecessor_join
    new_body = cur_body if body is None else body
    p.write_text(dump_frontmatter(meta, new_body), encoding="utf-8")


def read_plotmap(root: Path) -> dict:
    p = _plotmap_path(root)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _write_plotmap(root: Path, data: dict) -> None:
    _plotmap_path(root).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def edges_of(plotmap: dict, gid: str) -> dict:
    e = plotmap.get(gid) or {}
    return {"leads_to": e.get("leads_to", []), "excludes": e.get("excludes", [])}


def set_edges(root: Path, gid: str, leads_to: list[str] | None = None,
              excludes: list[str] | None = None) -> None:
    data = read_plotmap(root)
    cur = edges_of(data, gid)
    if leads_to is not None:
        cur["leads_to"] = list(leads_to)
    if excludes is not None:
        cur["excludes"] = list(excludes)
    data[gid] = cur
    _write_plotmap(root, data)


def delete_greeting(root: Path, gid: str) -> None:
    p = _greeting_path(root, gid)
    if not _safe(gid) or not p.exists():
        raise GreetingNotFound(gid)
    p.unlink()
    data = read_plotmap(root)
    changed = data.pop(gid, None) is not None
    for e in data.values():
        for key in ("leads_to", "excludes"):
            if gid in e.get(key, []):
                e[key] = [x for x in e[key] if x != gid]
                changed = True
    if changed:
        _write_plotmap(root, data)


def import_from_character(root: Path, char_id: str, vid: str) -> list[str]:
    data = characters.read_card(root, char_id, vid).get("data", {})
    cname = data.get("name", char_id)
    items: list[tuple[str, str]] = []
    first = data.get("first_mes", "")
    if isinstance(first, str) and first.strip():
        items.append((cname, first))
    for i, alt in enumerate(data.get("alternate_greetings", []) or [], start=1):
        if isinstance(alt, str) and alt.strip():
            items.append((f"{cname} (alt {i})", alt))
    return [create_greeting(root, name, char_id, vid, body) for name, body in items]


def availability(world_root: Path, plotmap: dict, played, player_tags) -> list[dict]:
    """Pure: which greetings are startable given the played set + player tags."""
    played = set(played)
    player_tags = set(player_tags)
    items = list_greetings(world_root)
    preds: dict[str, set] = {g["id"]: set() for g in items}
    for src, e in plotmap.items():
        for tgt in e.get("leads_to", []):
            if tgt in preds:
                preds[tgt].add(src)
    out: list[dict] = []
    for g in items:
        gid = g["id"]
        reasons: list[str] = []
        p = preds[gid]
        if p:
            if g["predecessor_join"] == "any":
                if not (p & played):
                    reasons.append("predecessors not played (any)")
            elif not (p <= played):
                reasons.append("predecessors not played (all)")
        excluded = ({x for x in played if gid in edges_of(plotmap, x)["excludes"]}
                    or set(edges_of(plotmap, gid)["excludes"]) & played)
        if excluded:
            reasons.append("excluded by a played greeting")
        if not (set(g["requires_tags"]) <= player_tags):
            reasons.append("missing required tags")
        out.append({"id": gid, "name": g["name"], "available": not reasons, "reasons": reasons})
    return out
