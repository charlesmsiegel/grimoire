"""One-time store migrations, run once per app startup. Each is idempotent."""

from __future__ import annotations

from pathlib import Path

from . import appearances, atomic, campaigns, cards, characters, entities, greetings, locks, overlay, scene_ids, scene_refs, scenes, worlds
from .frontmatter import parse_frontmatter
from .paths import home, slugify, uniquify


def migrate_scene_ids() -> None:
    """Rename legacy real-date scene files (<real-date>-<slug>.md) into the
    <number>--<date>--<slug> grammar: number by created order (continuing after
    any already-migrated scenes), date section from the scene's first
    time_history entry, then repoint every persisted reference. New-grammar
    files never match the legacy test, so re-running is a no-op."""
    for c in campaigns.list_campaigns():
        _migrate_campaign(c["id"])


def _migrate_campaign(cid: str) -> None:
    # The WHOLE migration under the campaign lock, not just the repad() at the
    # end (#234). Everything above it is destructive -- it reads every legacy
    # transcript, renames them all, and repoints every reference -- and once
    # the lock is cross-process a second backend can be serving from this
    # campaign while we do it: it reads a transcript, we rename the file, its
    # atomic write recreates the old path, and the campaign now has two
    # divergent copies of one scene. Two simultaneous startups race the rename
    # itself and one gets FileNotFoundError. repad() is reentrant, so nesting
    # under this costs nothing.
    with locks.campaign_lock(cid):
        _migrate_campaign_locked(cid)


def _migrate_campaign_locked(cid: str) -> None:
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
    already-baked content has no {{char}} left -- but a marker file skips the
    full-store scan on every later startup regardless, since that scan reads
    and parses every card/greeting and isn't free for a large store.

    Baking a materialized campaign copy identically to its world source (the
    common case: the campaign never actually diverged from the world for this
    ref) changes both hashes the same way, which would otherwise surface as a
    spurious sync conflict -- the campaign looks "changed" relative to its
    recorded base, even though it's still exactly in step with the world. Each
    baked campaign ref is checked against its world counterpart afterward and,
    if they now match, the recorded sync base is advanced to match too -- a
    genuine pre-existing divergence (campaign != world before baking) is left
    alone, since baking each side separately doesn't resolve that."""
    marker = home() / ".char_macros_baked"
    if marker.exists():
        return
    for w in worlds.list_worlds():
        wroot = worlds.world_root(w["id"])
        _bake_characters(wroot)
        for g in greetings.list_greetings(wroot):
            _bake_greeting(wroot, wroot, g)  # world greetings are self-contained
    for c in campaigns.list_campaigns():
        _bake_campaign(c["id"])
    atomic.write_text(marker, "")


def _bake_campaign(cid: str) -> None:
    croot = campaigns.campaign_root(cid)
    wroot = campaigns.world_root_of(cid)
    _bake_characters(croot)  # materialized characters are self-contained
    _repair_character_baselines(cid, croot, wroot)
    gdir = croot / "greetings"
    if not gdir.exists():
        return
    for p in gdir.glob("*.md"):
        g = greetings.read_greeting(croot, p.stem)["meta"]
        # a materialized greeting's character may still be world-only, so its
        # name resolves through overlay.char_root, not the bare croot
        name_root = overlay.char_root(cid, g["character"])
        _bake_greeting(croot, name_root, g)
        _repair_greeting_baseline(cid, croot, wroot, g["id"])


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


def _repair_greeting_baseline(cid: str, croot: Path, wroot: Path, gid: str) -> None:
    ref = f"greetings/{gid}"
    manifest = campaigns.read_manifest(cid)
    if ref not in manifest:
        return
    world_h = entities.entity_hash(wroot, "greetings", gid)
    mine_h = entities.entity_hash(croot, "greetings", gid)
    if world_h is not None and world_h == mine_h and manifest[ref] != mine_h:
        manifest[ref] = mine_h
        campaigns.write_manifest(cid, manifest)


def _repair_character_baselines(cid: str, croot: Path, wroot: Path) -> None:
    """A materialized character is tracked one of two ways: a locked version
    (appearances.json, one base per locked ref) or -- if never version-locked
    -- the whole actor directory (campaigns manifest, one base per actor).
    Only one applies per actor; check which."""
    locked = appearances.record(cid)
    manifest = campaigns.read_manifest(cid)
    manifest_changed = False
    for meta in characters.list_characters(croot):
        actor_id = meta["id"]
        lock_ref = f"characters/{actor_id}"
        if lock_ref in locked:
            vid = locked[lock_ref]["version"]
            world_h = characters.card_hash(wroot, actor_id, vid)
            mine_h = characters.card_hash(croot, actor_id, vid)
            if world_h is not None and world_h == mine_h and locked[lock_ref]["base"] != mine_h:
                appearances.set_base(cid, "characters", actor_id, mine_h)
        elif lock_ref in manifest:
            world_h = characters.dir_hash(wroot, actor_id)
            mine_h = characters.dir_hash(croot, actor_id)
            if world_h is not None and world_h == mine_h and manifest[lock_ref] != mine_h:
                manifest[lock_ref] = mine_h
                manifest_changed = True
    if manifest_changed:
        campaigns.write_manifest(cid, manifest)
