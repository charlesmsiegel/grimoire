"""One-time store migrations, run once per app startup. Each is idempotent."""

from __future__ import annotations

import logging
from pathlib import Path

from . import (
    alternates,
    atomic,
    cards,
    characters,
    entities,
    greetings,
    locks,
    overlay,
    scene_ids,
    scene_refs,
    worlds,
)
from .appearances import paths as appearances_paths
from .appearances import versions as appearances_versions
from .campaigns import paths as campaigns_paths
from .campaigns import read as campaigns_read
from .frontmatter import parse_frontmatter
from .paths import home, safe_id, slugify, uniquify
from .scenes import identity as scenes_identity
from .scenes import lifecycle as scenes_lifecycle
from .scenes import paths as scenes_paths

_log = logging.getLogger(__name__)


def migrate_scene_ids() -> None:
    """Rename legacy real-date scene files (<real-date>-<slug>.md) into the
    <number>--<date>--<slug> grammar: number by created order (continuing after
    any already-migrated scenes), date section from the scene's first
    time_history entry, then repoint every persisted reference. New-grammar
    files never match the legacy test, so re-running is a no-op."""
    for c in campaigns_read.list_campaigns():
        _migrate_campaign(c["id"])


def backfill_scene_identities() -> None:
    """Give every pre-feature scene an identity. Idempotent: a scene that has
    one is skipped, so re-running costs a read per scene.

    Assigning only at creation would be worse than nothing -- an old scene and a
    replacement that recycled its `sid` would both present the same absent
    value, so the publish fence would compare None with None, pass, and let the
    corruption it exists to prevent through while reading as solved.
    """
    for c in campaigns_read.list_campaigns():
        # Per campaign, not per pass. `main._lifespan` catches StoreBusy around
        # the whole startup step, so letting it out of this loop abandons every
        # campaign after the contended one -- they would wait for another
        # startup or a scene-specific lazy repair while the log named only one.
        # Contention on one campaign says nothing about the next.
        try:
            _backfill_campaign(c["id"])
        except locks.StoreBusy as exc:
            _log.warning("identity backfill skipped for %s -- %s; it will be "
                         "retried on the next start", c["id"], exc)
        except OSError as exc:
            # Enumeration itself can fail -- `glob()` raises if the directory
            # cannot be listed, on a permissions problem or a synced folder
            # mid-error -- and that happens BEFORE any per-scene handler. The
            # startup hook catches only StoreBusy, so an OSError escaping here
            # stops the app launching at all.
            _log.warning("identity backfill skipped for %s -- %s", c["id"], exc)


def _backfill_campaign(cid: str) -> None:
    """One campaign's pass, the whole thing under its lock like
    `_migrate_campaign` -- a second backend serving this campaign must not be
    read-modify-writing the same scene files underneath us."""
    with locks.campaign_lock(cid):
        seen: set[str] = set()
        for sid in _scene_ids(cid):
            # Check with the head-only read before calling `ensure_identity`,
            # which reads the whole file. This runs at every startup for the
            # life of the install, and after the first pass every scene already
            # has one -- so without this precheck the steady state is reading
            # every transcript in the library, in full, on every boot.
            # Per SCENE, not just per campaign. A transcript that is read-only,
            # or held open by a sync client, makes the write fail -- and
            # `_lifespan` catches only StoreBusy, so an OSError escaping here
            # stops the app booting at all. One stubborn file must cost that
            # file its identity until the lazy path repairs it, nothing more.
            try:
                token = scenes_identity.scene_identity(cid, sid)
                if token is None:
                    token = scenes_identity.ensure_identity(cid, sid)
                elif token in seen:
                    # Two scenes carrying the same token: the reverse lookup
                    # would answer with whichever file sorts first, so a
                    # notification for one would open the other. Re-mint the
                    # later one.
                    token = scenes_identity.ensure_identity(cid, sid, replace=True)
            except OSError as exc:
                _log.warning("identity backfill skipped for scene %s in %s -- %s",
                             sid, cid, exc)
                continue
            seen.add(token)


def _scene_ids(cid: str) -> list[str]:
    """Scene ids by filename, without parsing anything.

    Deliberately not `list_scenes`: that reads each file's frontmatter as UTF-8
    and raises on bytes that are not, and this runs at STARTUP -- where
    `_lifespan` catches only StoreBusy, so one unreadable scene in the user's
    library would stop the app booting at all, every launch, with no way back
    except finding and deleting the file. Enumerating names cannot fail that
    way, and `ensure_identity` is tolerant of a file it cannot read.
    """
    d = scenes_paths._scenes_dir(cid)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.md") if safe_id(p.stem))


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
    d = campaigns_paths.campaign_root(cid) / "scenes"
    if not d.exists():
        return
    legacy, top, width = [], 0, scene_ids.MIN_WIDTH
    for p in d.glob("*.md"):
        if not safe_id(p.stem):
            continue   # never rename an id the store cannot address
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
    # Before a single transcript moves, exactly as `repad` does. `taken` is
    # built from `*.md`, so a destination can be an id an orphaned sidecar still
    # sits on — and clearing one can fail. Left to `scene_refs.repoint` at the
    # end, that failure lands with every legacy scene already renamed and every
    # other store still pointing at the old ids, on a startup migration.
    alternates.clear_destinations(cid, set(mapping.values()))
    for old, new in mapping.items():
        (d / f"{old}.md").rename(d / f"{new}.md")
    scene_refs.repoint(cid, mapping)
    scenes_lifecycle.repad(cid, width)  # widths must stay uniform if legacy count outgrew them


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
    for c in campaigns_read.list_campaigns():
        _bake_campaign(c["id"])
    atomic.write_text(marker, "")


def _bake_campaign(cid: str) -> None:
    croot = campaigns_paths.campaign_root(cid)
    wroot = campaigns_read.world_root_of(cid)
    _bake_characters(croot)  # materialized characters are self-contained
    _repair_character_baselines(cid, croot, wroot)
    gdir = croot / "greetings"
    if not gdir.exists():
        return
    for p in gdir.glob("*.md"):
        if not safe_id(p.stem):
            continue   # startup path: an unusable stem must not abort the lifespan
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
    manifest = campaigns_paths.read_manifest(cid)
    if ref not in manifest:
        return
    world_h = entities.entity_hash(wroot, "greetings", gid)
    mine_h = entities.entity_hash(croot, "greetings", gid)
    if world_h is not None and world_h == mine_h and manifest[ref] != mine_h:
        manifest[ref] = mine_h
        campaigns_paths.write_manifest(cid, manifest)


def _repair_character_baselines(cid: str, croot: Path, wroot: Path) -> None:
    """A materialized character is tracked one of two ways: a locked version
    (appearances.json, one base per locked ref) or -- if never version-locked
    -- the whole actor directory (campaigns manifest, one base per actor).
    Only one applies per actor; check which."""
    locked = appearances_paths.record(cid)
    manifest = campaigns_paths.read_manifest(cid)
    manifest_changed = False
    for meta in characters.list_characters(croot):
        actor_id = meta["id"]
        lock_ref = f"characters/{actor_id}"
        if lock_ref in locked:
            vid = locked[lock_ref]["version"]
            world_h = characters.card_hash(wroot, actor_id, vid)
            mine_h = characters.card_hash(croot, actor_id, vid)
            if world_h is not None and world_h == mine_h and locked[lock_ref]["base"] != mine_h:
                appearances_versions.set_base(cid, "characters", actor_id, mine_h)
        elif lock_ref in manifest:
            world_h = characters.dir_hash(wroot, actor_id)
            mine_h = characters.dir_hash(croot, actor_id)
            if world_h is not None and world_h == mine_h and manifest[lock_ref] != mine_h:
                manifest[lock_ref] = mine_h
                manifest_changed = True
    if manifest_changed:
        campaigns_paths.write_manifest(cid, manifest)
