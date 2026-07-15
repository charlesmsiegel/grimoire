"""One-time store migrations, run once per app startup. Each is idempotent."""

from __future__ import annotations

from pathlib import Path

from . import campaigns, cards, characters, greetings, overlay, scene_ids, scene_refs, scenes, worlds
from .frontmatter import parse_frontmatter
from .paths import slugify, uniquify


def migrate_scene_ids() -> None:
    """Rename legacy real-date scene files (<real-date>-<slug>.md) into the
    <number>--<date>--<slug> grammar: number by created order (continuing after
    any already-migrated scenes), date section from the scene's first
    time_history entry, then repoint every persisted reference. New-grammar
    files never match the legacy test, so re-running is a no-op."""
    for c in campaigns.list_campaigns():
        _migrate_campaign(c["id"])


def _migrate_campaign(cid: str) -> None:
    d = campaigns.campaign_root(cid) / "scenes"
    if not d.exists():
        return
    legacy, top, width = [], 0, scene_ids.MIN_WIDTH
    for p in d.glob("*.md"):
        parsed = scene_ids.parse_sid(p.stem)
        if parsed:
            top = max(top, parsed["number"])
            width = max(width, parsed["width"])
        else:
            meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
            legacy.append((meta.get("created", ""), p.stem, meta))
    if not legacy:
        return
    legacy.sort(key=lambda t: (t[0], t[1]))  # created, then stem — never compare the meta dicts
    width = max(width, len(str(top + len(legacy))))
    taken = {p.stem for p in d.glob("*.md")}
    mapping: dict[str, str] = {}
    for number, (_, stem, meta) in enumerate(legacy, start=top + 1):
        history = [x for x in meta.get("time_history", "").split(",") if x]
        date_slug = scene_ids.date_slug_of(history[0]) if history else None
        base = scene_ids.format_sid(number, width, date_slug, slugify(meta.get("title", stem)))
        new_sid = uniquify(base, lambda cand: cand in taken)
        taken.add(new_sid)
        mapping[stem] = new_sid
    for old, new in mapping.items():
        (d / f"{old}.md").rename(d / f"{new}.md")
    scene_refs.repoint(cid, mapping)
    scenes.repad(cid, width)  # widths must stay uniform if legacy count outgrew them


def bake_char_macros() -> None:
    """One-time bake of {{char}} into every already-saved card and greeting
    (#137): content saved before {{char}} was resolved at write time still
    carries the raw macro, and scene-time substitution no longer resolves it
    (removed as ambiguous once more than one NPC is present). Idempotent --
    already-baked content has no {{char}} left, so a re-run touches nothing."""
    for w in worlds.list_worlds():
        wroot = worlds.world_root(w["id"])
        _bake_characters(wroot)
        for g in greetings.list_greetings(wroot):
            _bake_greeting(wroot, wroot, g)  # world greetings are self-contained
    for c in campaigns.list_campaigns():
        croot = campaigns.campaign_root(c["id"])
        _bake_characters(croot)  # materialized characters are self-contained
        gdir = croot / "greetings"
        if not gdir.exists():
            continue
        for p in gdir.glob("*.md"):
            g = greetings.read_greeting(croot, p.stem)["meta"]
            # a materialized greeting's character may still be world-only, so
            # its name resolves through overlay.char_root, not the bare croot
            name_root = overlay.char_root(c["id"], g["character"])
            _bake_greeting(croot, name_root, g)


def _bake_characters(root: Path) -> None:
    for meta in characters.list_characters(root):
        cid = meta["id"]
        for v in meta["versions"]:
            vid = v["id"]
            card = characters.read_card(root, cid, vid)
            if cards.bake_char_name(card):
                characters.update_version(root, cid, vid, card)


def _bake_greeting(root: Path, name_root: Path, g: dict) -> None:
    """Bake {{char}} in the greeting `g` (meta dict) stored under `root`,
    resolving its character's name from `name_root`."""
    full = greetings.read_greeting(root, g["id"])
    name = greetings.char_name(name_root, g["character"], g["version"])
    baked = cards.bake_char_token(full["body"], name)
    if baked != full["body"]:
        greetings.update_greeting(root, g["id"], body=baked)
