"""World greeting objects + the plot map.

A greeting is a markdown file under <world>/greetings/<gid>.md that references a
character + version and carries scalar gating attributes. The directed plot-map
edges (leads_to / excludes) are nested data, so they live in <world>/plotmap.json
keyed by greeting id.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import atomic, cards, characters, statcache
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import natural_key, safe_id, slugify, uniquify


class GreetingNotFound(Exception):
    pass


def _greetings_dir(root: Path) -> Path:
    return root / "greetings"


def _greeting_path(root: Path, gid: str) -> Path:
    return _greetings_dir(root) / f"{gid}.md"


def _plotmap_path(root: Path) -> Path:
    return root / "plotmap.json"


def _tags_list(s: str) -> list[str]:
    return [t for t in s.split(",") if t]


def _meta_dict(gid: str, meta: dict) -> dict:
    character = meta.get("character", "")
    # `present` is the full cast at the opener (comma-joined ids, like requires_tags).
    # When absent it falls back to just the primary character so old greetings work.
    present = _tags_list(meta.get("present", "")) or ([character] if character else [])
    return {
        "id": gid,
        "name": meta.get("name", gid),
        "character": character,
        "version": meta.get("version", ""),
        "present": present,
        "requires_tags": _tags_list(meta.get("requires_tags", "")),
        "predecessor_join": meta.get("predecessor_join", "all"),
        "pcless": meta.get("pcless") == "true",
    }


def present_in(body: str, source: str, roster: dict[str, str]) -> list[str]:
    """Who is present at this greeting: the source character plus any character in
    `roster` (display-name -> id) whose name appears as a whole word in `body`.

    `{{char}}` is the source; `{{user}}` is the player, not a character. Result is
    ordered source-first, then the rest by first appearance, so it is stable.
    """
    found: dict[str, int] = {}
    for name, cid in roster.items():
        if cid == source:
            continue
        m = re.search(rf"\b{re.escape(name)}\b", body, re.IGNORECASE)
        if m:
            found[cid] = m.start()
    return [source] + sorted(found, key=found.__getitem__)


def char_name(root: Path, character: str, version: str = "") -> str:
    """The associated character version's display name, or "" if there isn't
    one -- baking has nothing to bake {{char}} to (e.g. a character-less
    greeting). Card names are version-specific (self-reference means "this
    card"), so the version's own card name wins; the container's name is a
    fallback for a missing/invalid version."""
    if not character:
        return ""
    if version:
        try:
            name = characters.read_card(root, character, version)["data"].get("name", "")
            if name:
                return name
        except (characters.CharacterNotFound, characters.VersionNotFound):
            pass
    try:
        return characters.read_character(root, character)["meta"].get("name", "")
    except characters.CharacterNotFound:
        return ""


def create_greeting(root: Path, name: str, character: str, version: str, body: str = "",
                    requires_tags: list[str] | None = None, predecessor_join: str = "all",
                    present: list[str] | None = None, pcless: bool = False, taken=None) -> str:
    _greetings_dir(root).mkdir(parents=True, exist_ok=True)

    def exists(c: str) -> bool:
        # `taken` widens the id namespace (overlay: world files + tombstones)
        return _greeting_path(root, c).exists() or (taken is not None and taken(c))

    gid = uniquify(slugify(name), exists)
    meta = {"name": name, "character": character, "version": version,
            "present": ",".join(present or []),
            "requires_tags": ",".join(requires_tags or []), "predecessor_join": predecessor_join,
            "pcless": "true" if pcless else ""}
    # #137: {{char}} is the greeting's own associated character, baked at write
    # time -- scene-time substitution is ambiguous once more than one NPC is
    # present, so it's never resolved there.
    body = cards.bake_char_token(body, char_name(root, character, version))
    atomic.write_text(_greeting_path(root, gid), dump_frontmatter(meta, body))
    return gid


def read_greeting(root: Path, gid: str) -> dict:
    p = _greeting_path(root, gid)
    if not safe_id(gid) or not p.exists():
        raise GreetingNotFound(gid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"meta": _meta_dict(gid, meta), "body": body}


def list_greetings(root: Path) -> list[dict]:
    d = _greetings_dir(root)
    if not d.exists():
        return []
    metas = [read_greeting(root, p.stem)["meta"] for p in d.glob("*.md") if safe_id(p.stem)]
    metas.sort(key=lambda m: natural_key(m["name"]))  # A2 before A10
    return metas


def greeting_count(root: Path) -> int:
    d = _greetings_dir(root)
    return sum(1 for p in d.glob("*.md") if safe_id(p.stem)) if d.exists() else 0


def update_greeting(root: Path, gid: str, *, name: str | None = None, body: str | None = None,
                    requires_tags: list[str] | None = None, predecessor_join: str | None = None,
                    present: list[str] | None = None, pcless: bool | None = None) -> None:
    p = _greeting_path(root, gid)
    if not safe_id(gid) or not p.exists():
        raise GreetingNotFound(gid)
    meta, cur_body = parse_frontmatter(p.read_text(encoding="utf-8"))
    if name is not None:
        meta["name"] = name
    if requires_tags is not None:
        meta["requires_tags"] = ",".join(requires_tags)
    if present is not None:
        meta["present"] = ",".join(present)
    if predecessor_join is not None:
        meta["predecessor_join"] = predecessor_join
    if pcless is not None:
        meta["pcless"] = "true" if pcless else ""
    if body is not None:
        body = cards.bake_char_token(
            body, char_name(root, meta.get("character", ""), meta.get("version", "")))
    new_body = cur_body if body is None else body
    atomic.write_text(p, dump_frontmatter(meta, new_body))


def read_plotmap(root: Path) -> dict:
    p = _plotmap_path(root)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def plotmap_content_hash(text: str) -> str:
    """`plotmap_hash` of a map you are holding rather than one on disk — see
    `entities.content_hash` for why a copier needs this (#247)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def plotmap_hash(root: Path) -> str | None:
    p = _plotmap_path(root)
    sig = statcache.signature(p)
    if sig is None:
        return None
    return statcache.memo("plotmap_hash", sig, lambda: plotmap_content_hash(p.read_text(encoding="utf-8")))


def _write_plotmap(root: Path, data: dict) -> None:
    atomic.write_text(_plotmap_path(root), json.dumps(data, indent=2, sort_keys=True) + "\n")


def edges_of(plotmap: dict, gid: str) -> dict:
    e = plotmap.get(gid) or {}
    return {"leads_to": e.get("leads_to", []), "excludes": e.get("excludes", [])}


def predecessors_of(plotmap: dict, gid: str) -> list[str]:
    """Greetings whose `leads_to` includes `gid` — i.e. what unlocks it. Sorted."""
    return sorted(src for src, e in plotmap.items() if gid in (e.get("leads_to") or []))


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


def remove_from_plotmap(root: Path, gid: str) -> None:
    """Drop gid's own edges and every reference to it from other greetings' edges."""
    data = read_plotmap(root)
    changed = data.pop(gid, None) is not None
    for e in data.values():
        for key in ("leads_to", "excludes"):
            if gid in e.get(key, []):
                e[key] = [x for x in e[key] if x != gid]
                changed = True
    if changed:
        _write_plotmap(root, data)


def delete_greeting(root: Path, gid: str) -> None:
    p = _greeting_path(root, gid)
    if not safe_id(gid) or not p.exists():
        raise GreetingNotFound(gid)
    p.unlink()
    remove_from_plotmap(root, gid)


def import_from_character(root: Path, char_id: str, vid: str) -> list[str]:
    data = characters.read_card(root, char_id, vid).get("data", {})
    cname = data.get("name", char_id)
    roster = {c["name"]: c["id"] for c in characters.list_characters(root)}
    items: list[tuple[str, str]] = []
    first = data.get("first_mes", "")
    if isinstance(first, str) and first.strip():
        items.append((cname, first))
    for i, alt in enumerate(data.get("alternate_greetings", []) or [], start=1):
        if isinstance(alt, str) and alt.strip():
            items.append((f"{cname} (alt {i})", alt))
    return [create_greeting(root, name, char_id, vid, body, present=present_in(body, char_id, roster))
            for name, body in items]


def availability(items: list[dict], plotmap: dict, played, player_tags,
                 skipped=frozenset()) -> list[dict]:
    """Pure: which greetings are startable given the played set + player tags.
    Skipped greetings are dropped from the output and pruned from predecessor
    lists — the plot routes around a greeting marked won't-do."""
    played = set(played)
    skipped = set(skipped)
    player_tags = set(player_tags)
    items = [g for g in items if g["id"] not in skipped]
    preds: dict[str, set] = {g["id"]: set() for g in items}
    for src, e in plotmap.items():
        if src in skipped:
            continue
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
        out.append({"id": gid, "name": g["name"], "available": not reasons,
                    "reasons": reasons, "pcless": g["pcless"]})
    return out
