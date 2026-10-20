"""todo: sources responsibilities."""

from __future__ import annotations

import json
from urllib.parse import quote

from ... import store
from ...store.continuity import effective as continuity_effective
from ._router import router as router


def _slugish(name: str) -> str:
    """`name` as it would most obviously slugify, for deciding whether an id is
    worth showing beside it. Not `paths.safe_id` -- this is a display question
    ("does the id tell the reader anything the name did not"), not an
    addressing one, and being approximate here costs nothing."""
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")


def _character_gaps(cid: str) -> tuple[list[dict], list[dict]]:
    """(no tagline, no voice anchor) across the campaign's EFFECTIVE roster.

    Through the overlay, and that is the whole point of this function rather
    than an implementation note. A campaign is copy-on-write over its world,
    so reading the world root directly gets three things wrong at once, and
    this got all three:

    - a character the campaign DELETED is still in the world, so it was
      reported as missing a tagline in a campaign it is not in;
    - `tagline.md` and `voice_anchor.md` are sidecars that overlay per file, so
      a tagline written campaign-side did not count and the character was
      reported as lacking one;
    - a materialized actor is authoritative for its own meta, so a campaign
      copy's tagline was invisible behind the world's.

    `overlay.character_sidecars` resolves both fields exactly as
    `list_characters` does -- `tagline` per file and `has_voice_anchor` off one
    directory scan rather than a read per row -- without the image listing,
    focus read and card summaries the full row adds, none of which this reads.
    It runs for the badge on every navigation, so that difference is paid per
    page, not per visit to this one.
    """
    try:
        rows = store.overlay.character_sidecars(cid)
    except (store.CampaignNotFound, OSError):
        return [], []
    no_tagline: list[dict] = []
    no_anchor: list[dict] = []
    for row in rows:
        name = str(row.get("name") or row["id"])
        # The slug, when it is not just the name lowercased. It is what
        # addresses the character in a ref and what names the file, so it is
        # the one fact that turns a list of names into something you can act
        # on -- and a roster with two Maras is exactly when it matters.
        slug = row["id"] if row["id"] != _slugish(name) else ""
        if not str(row.get("tagline") or "").strip():
            no_tagline.append({"id": row["id"], "label": name, "detail": slug})
        if not row.get("has_voice_anchor"):
            no_anchor.append({"id": row["id"], "label": name, "detail": slug})
    return no_tagline, no_anchor


def _ledger_href(cid: str, section: str = "", *segments: str) -> str:
    """A Ledger address, in the grammar `frontend/src/ledgerPaths.ts`'s
    `ledgerHref` parses (§12.1): ``/campaigns/<cid>/ledger[/<section>][/<id>...]``
    with the campaign id and every segment percent-encoded whole, so a
    model-written id holding a ``/`` or ``:`` stays one segment."""
    parts = [f"/campaigns/{quote(cid, safe='')}/ledger"]
    if section:
        parts.append(section)
    parts.extend(quote(segment, safe="") for segment in segments)
    return "/".join(parts)


def _owed(cid: str) -> list[dict]:
    """The live canonical commitments with a stated deadline, in effective
    order: a merged-away source is never a second row, and its due is never
    inherited by the canonical. A due counts whether or not it parses --
    Todo does no calendar work (no provider, no aging), because the global page
    runs every builder for every campaign and a provider is plugin code. Link
    deadlines are pressure's, not this set's. Raises what
    `effective.commitments` raises."""
    return [c for c in continuity_effective.commitments(cid) if c.get("due")]


def _world_char_gaps(worlds: list[dict]) -> tuple[list[dict], list[dict]]:
    """(untagged, anchorless) across `worlds`, by stat rather than by reading.

    `taglines.untagged_ids` and `voice_anchors.anchorless_ids` carry the rules;
    their docstrings carry why a stat is sound for each and where it would stop
    being. What is here is the reason it has to be a stat at all: this runs for
    every character of every world on every `/api/shell` read, and opening the
    two sidecars instead costs orders of magnitude more on a cold cache -- one
    small file per record spread across the whole store is the worst shape a
    filesystem can be handed, and the first read after a restart would stall
    the page this list is drawn on.

    The character's NAME is not resolved here, only its id and world. A name is
    a frontmatter read per row, which is the cost this avoids; the expansion
    pays for it, for the one chore a reader opened. That split is the same one
    `ITEMS` exists for.
    """
    untagged: list[dict] = []
    anchorless: list[dict] = []
    for w in worlds:
        try:
            root = store.worlds.paths.world_root(w["id"])
            ids = store.characters.character_refs(root)
        except (OSError, store.worlds.paths.WorldNotFound):
            continue
        where = {"wid": w["id"], "world": w["name"]}
        untagged.extend({**where, "id": c}
                        for c in store.taglines.untagged_ids(root, ids))
        anchorless.extend({**where, "id": c}
                          for c in store.voice_anchors.anchorless_ids(root, ids))
    return untagged, anchorless


def _world_describe_counts(worlds: list[dict]) -> list[dict]:
    """Per world, how many stored images carry no description.

    `undescribed_count`, not `len(undescribed(...))`: the list resolves an
    extension and a cache-busting token per image and those are a stat apiece,
    which on a whole-library sweep is most of the walk. The two are held to the
    same answer by a test, because a cheap count that can drift from the list
    behind it is the stale number this whole module is arranged to not have.
    """
    out = []
    for w in worlds:
        try:
            root = store.worlds.paths.world_root(w["id"])
            n = (sum(store.image_descriptions.undescribed_count(root, base)
                     for base in _DESCRIBE_BASES)
                 # The world's own library hangs off no record, so no base walk
                 # can reach it. Inside the same `try`: a leak here 500s the
                 # whole chore sweep, which is what its `except` is for.
                 + store.world_images.undescribed_count(w["id"]))
        except (OSError, store.worlds.paths.WorldNotFound):
            continue
        if n:
            out.append({"wid": w["id"], "world": w["name"], "n": n})
    return out


#: The RECORD bases the describe queue walks -- `routes/characters.list_undescribed_images`'
#: list, and it must stay that list. The queue also offers the world's own image
#: library, which hangs off no record and so appears in none of these; every
#: reader of this roster adds `world_images` alongside it rather than expecting
#: this tuple to cover the whole backlog. Greetings are deliberately absent there
#: (their sidecar is `image_subjects`, a different question), so a chore that
#: counted them would offer a backlog the queue cannot empty.
_DESCRIBE_BASES = ("characters", store.pcs.ASSET_BASE, *store.entities.ENTITY_KINDS)


def _world_avatar_gaps(worlds: list[dict]) -> list[dict]:
    """Missing default portraits, without opening card bodies or all versions.

    `version_facts` supplies the same avatar presence as the world cast grid;
    `overlay.avatar_v` supplies the campaign half. Neither path treats a
    gallery image or another version's portrait as this version's avatar.
    """
    out: list[dict] = []
    for world in worlds:
        try:
            root = store.worlds.paths.world_root(world["id"])
            rows = store.characters.roster(root)
            missing = [r for r in rows if store.characters.version_facts(
                root, r["id"], r["default_version"])["avatar_v"] is None]
        except (OSError, store.worlds.paths.WorldNotFound):
            continue
        out.extend({"wid": world["id"], "world": world["name"], **r} for r in missing)
    return out


def _world_cover_gaps(worlds: list[dict]) -> list[dict]:
    out = []
    for world in worlds:
        try:
            if store.covers.world_cover_path(world["id"]) is None:
                out.append(world)
        except (OSError, store.worlds.paths.WorldNotFound):
            continue
    return out


def _world_subject_gaps(worlds: list[dict]) -> list[dict]:
    """The tagging queue's unanswered images, including a campaign's world.

    An explicit empty assignment means "no subjects", not unfinished work.
    `image_subjects.untagged` owns that distinction; testing whether a subject
    list is truthy would put reviewed images back on the list forever.
    """
    out: list[dict] = []
    for world in worlds:
        try:
            root = store.worlds.paths.world_root(world["id"])
            waiting = store.image_subjects.untagged(root)
        except (OSError, store.worlds.paths.WorldNotFound):
            continue
        out.extend({"wid": world["id"], "world": world["name"], **image} for image in waiting)
    return out


def _review_sidecars(cid: str) -> list[tuple[str, list]]:
    """`(sid, edits)` for every readable `<sid>.review.json`, in name order.

    A glob plus one small read per sidecar, and there is normally none. A
    sidecar that cannot be read is skipped, as the shell's `_pending` skips
    it."""
    d = store.scenes.paths._scenes_dir(cid)   # paths-ok: the resolver itself
    if not d.exists():
        return []
    out = []
    for path in sorted(d.glob("*.review.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            edits = rec.get("review", {}).get("edits", [])
        except (OSError, ValueError, AttributeError):
            continue
        out.append((path.name[: -len(".review.json")], edits))
    return out


def _items_avatars(cid: str) -> list[dict]:
    """Default portraits over the effective roster, with one overlay view.

    Reusing `avatar_v` keeps inherited avatars, image tombstones and detached
    characters consistent with the cast grid without building full card rows.
    """
    try:
        v = store.overlay.view(cid)
        rows = store.overlay.character_roster(cid, v=v)
        return [{"id": r["id"], "label": r["name"], "detail": r["default_version"],
                 "fix": f"/campaigns/{cid}/world/characters/{r['id']}"}
                for r in rows if store.overlay.avatar_v(cid, r["id"], r["default_version"], v=v) is None]
    except (OSError, store.CampaignNotFound):
        return []


def _items_cover(cid: str) -> list[dict]:
    try:
        if store.covers.cover_path(cid) is not None:
            return []
        name = store.campaigns.read_campaign(cid)["meta"].get("name") or cid
    except (OSError, store.CampaignNotFound):
        return []
    return [{"id": cid, "label": name, "detail": "no cover",
             "fix": f"/campaigns/{cid}"}]
