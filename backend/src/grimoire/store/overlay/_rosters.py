"""Actor listing/detail projections over the same file and sidecar authority rules."""

from __future__ import annotations

from .. import assets, characters, entities, pcs, voice_anchors
from . import _actors, _images, _record_paths, _sidecars, _views


def _patch_char_item(v: _views.View, item: dict) -> dict:
    """One listing row with its asset-derived fields and tagline re-derived
    from the union -- `list_images`, `read_focus` and `tagline`, answered from
    each root's memoized `characters.version_facts` instead of re-listing and
    re-reading both roots per row on every request.

    The merge is those three functions' rules, restated over the facts, and
    each branch is theirs: a detached record never consults the world (so its
    facts are never even computed), a per-image tombstone hides that world
    image, and the crop comes from the campaign whenever the campaign owns an
    avatar, a crop record (an avatar placement or a `focus.json`), or a
    tombstone over the world's avatar.
    `test_world_list_fast.py` holds the patched row to the one the three
    functions build, across each of those.
    """
    aid, vid = item["id"], item["default_version"]
    mine = characters.version_facts(v.croot, aid, vid, crop=True)
    detached = _record_paths.flat_ref("characters", aid) in v.off
    theirs = None if detached else characters.version_facts(v.wroot, aid, vid)
    # `list_images`' union, over names: the campaign's, then each world name
    # the campaign holds no file for and has not tombstoned. The avatar's
    # token comes from whichever side answered for the name -- a campaign row
    # whose avatar lives world-side would otherwise carry the campaign root's
    # token for a file it does not have, and `?v=` caches immutable.
    names = set(mine["names"])
    avatar_v = mine["avatar_v"]
    if theirs is not None:
        inherited = [n for n in theirs["names"]
                     if n not in names and _record_paths.asset_ref("characters", aid, vid, n) not in v.gone]
        if assets.AVATAR in inherited:
            avatar_v = theirs["avatar_v"]
        names.update(inherited)
    # `read_focus`: the campaign's crop whenever it owns an avatar, a crop
    # record (`focus_file`: an avatar placement, image-less override included,
    # or a `focus.json`), or a tombstone over the world's avatar -- or is
    # detached.
    focus = (mine["focus"] if (theirs is None or mine["avatar_file"] or mine["focus_file"]
                               or _record_paths.asset_ref("characters", aid, vid, assets.AVATAR) in v.gone)
             else theirs["focus"])
    return {**item,
            "has_avatar": assets.AVATAR in names,
            "avatar_v": avatar_v,
            "avatar_focus": focus,
            "gallery_count": assets.gallery_count(names),
            "localized_count": assets.localized_count(names),
            "tagline": (mine["tagline"] if mine["tagline"] or theirs is None
                        else theirs["tagline"])}


def list_characters(cid: str) -> list[dict]:
    # One view for the whole listing: every row below asks the same campaign
    # the same questions, and asking per row made this listing's cost grow
    # with the cast in campaign-file reads alone (see `View`).
    v = _views.view(cid)
    mine = characters.list_characters(v.croot)
    # dossier/state-only dirs have no character.md and are filtered by
    # characters.list_characters itself (it requires the meta file)
    have = {c["id"] for c in mine}
    world_rows = characters.list_characters(v.wroot)
    inherited = [c for c in world_rows
                 if c["id"] not in have and _record_paths.flat_ref("characters", c["id"]) not in v.gone]
    rows = [_patch_char_item(v, c) for c in mine + inherited]

    # `has_voice_anchor` is resolved from what the two scans ALREADY read, not
    # by asking the overlay again. Each scan does one anchor read per row, so
    # re-resolving in `_patch_char_item` cost three or four reads per returned
    # character on a listing that is already the expensive one -- and this store
    # may sit in a synced folder, where per-file latency is the whole cost.
    world = {c["id"]: c.get("has_voice_anchor", False) for c in world_rows}
    anchored = _anchor_presence(v, [row["id"] for row in rows],
                                lambda aid: world.get(aid, False))
    for row in rows:
        row["has_voice_anchor"] = anchored[row["id"]]
    return sorted(rows, key=lambda c: c["id"])


def _anchor_presence(v: _views.View, ids: list[str], world_has) -> dict[str, bool]:
    """`has_voice_anchor` for each of `ids`, the rule `list_characters` and
    `character_sidecars` both report by. `world_has(aid)` answers for a row
    that falls through to the world, from whatever the caller already read.

    ONE directory scan settles which rows even have a campaign-side anchor
    file, and only those are read. `set_voice_anchor` writes that file
    WITHOUT copying the card down, so an inherited row can carry a campaign
    tombstone -- which is why "no campaign card, therefore no campaign
    anchor" would be wrong, and is the case this got wrong first.

    A DETACHED actor does not inherit, and this has to agree with
    `voice_anchor_record`, which suppresses the world fallback for exactly
    these refs. Without it a campaign whose character id has since been
    reused world-side would report has_voice_anchor: true off an unrelated
    row, while reading that character's effective anchor returned "".
    """
    cdir = v.croot / "characters"
    campaign_side = ({p.parent.name for p in cdir.glob("*/voice_anchor.md")}
                     if cdir.is_dir() else set())          # paths-ok: sibling of anchor_path
    out: dict[str, bool] = {}
    for aid in ids:
        if aid in campaign_side:
            rec = voice_anchors.read_record(v.croot, aid)
            out[aid] = bool(rec["text"])    # tombstone reads as none
        elif _record_paths.flat_ref("characters", aid) in v.off:
            out[aid] = False
        else:
            out[aid] = world_has(aid)
    return out


def _patch_pc_item(v: _views.View, item: dict) -> dict:
    """The PC counterpart of `_patch_char_item`: `pcs.list_pcs` computed these
    against one root, but a thin campaign's PC can hold its images world-side,
    so the derived fields have to come from the overlay union (#219).

    That does mean the store-level scan is always discarded here -- two asset
    scans per row for one answer. It is kept rather than parameterised away
    because `pcs.list_pcs` is also the world route's answer, where the values
    it computes are the ones served; `_patch_char_item` has carried the same
    cost since characters got theirs."""
    images = _images.list_images(v.cid, item["id"], item["default_version"], pcs.ASSET_BASE, v=v)
    names = [i["name"] for i in images]
    return {**item,
            "has_avatar": assets.AVATAR in names,
            # From the union, so the token is the one of whichever side
            # answered for the avatar: a thin campaign's PC wears the world's
            # file, and `?v=` is served immutable.
            "avatar_v": next((i["v"] for i in images if i["name"] == assets.AVATAR), None),
            "avatar_focus": _images.read_focus(v.cid, item["id"], item["default_version"],
                                       pcs.ASSET_BASE, v=v),
            "gallery_count": sum(1 for n in names if n.startswith("gallery_"))}


def list_pcs(cid: str) -> list[dict]:
    v = _views.view(cid)    # one for every row, as in `list_characters`
    mine = pcs.list_pcs(v.croot)
    have = {p["id"] for p in mine}
    inherited = [p for p in pcs.list_pcs(v.wroot)
                 if p["id"] not in have and _record_paths.flat_ref("pcs", p["id"]) not in v.gone]
    return sorted([_patch_pc_item(v, p) for p in mine + inherited], key=lambda p: p["id"])


# ---- rosters: the listings' union, cut to what an id-and-name caller reads ----
#
# `list_characters` is the full row, and most of its cost is in fields nobody
# but a grid reads: an image listing and a focus read per character off both
# roots, a card summary per version, a tagline. The turn's off-scene directory,
# cast-change detection, the suggestion rail, scene suggestions, the sheet
# tally and the to-do list asked it only who is here and what they are called.
# These answer exactly that, through the same union -- campaign rows, then the
# world's rows the campaign neither holds nor tombstoned -- so they list the
# same characters as the full listing, in the same order, and a test holds
# them to it. A caller that needs another field reads the full listing for it:
# widening a roster row piecemeal is how it would drift back into being one.

def _roster_union(v: _views.View, kind: str, mine: list, theirs: list, key=lambda r: r) -> list:
    """`mine` plus every row of `theirs` whose id `mine` lacks and the campaign
    has not tombstoned, sorted by id: the union `list_characters`, `list_pcs`
    and `list_entities` each build. `key` reads the id off a row, for rows
    that are dicts rather than bare ids."""
    have = {key(r) for r in mine}
    inherited = [r for r in theirs if key(r) not in have and _record_paths.flat_ref(kind, key(r)) not in v.gone]
    return sorted(mine + inherited, key=key)


def character_roster(cid: str, *, v: _views.View | None = None) -> list[dict]:
    """`{id, name, default_version}` per character `list_characters` lists.

    The name is the owning root's, as in the full listing: a campaign copy
    renamed campaign-side lists under the campaign's name."""
    v = _views._view(cid, v)
    return _roster_union(v, "characters", characters.roster(v.croot),
                        characters.roster(v.wroot), key=lambda r: r["id"])


def pc_roster(cid: str, *, v: _views.View | None = None) -> list[dict]:
    """`{id, name, default_version}` per PC `list_pcs` lists -- the PC
    counterpart of `character_roster`, and like it, no image is scanned."""
    v = _views._view(cid, v)
    return _roster_union(v, "pcs", pcs.roster(v.croot), pcs.roster(v.wroot),
                         key=lambda r: r["id"])


def character_ids(cid: str, *, v: _views.View | None = None) -> list[str]:
    """The ids of `character_roster`, reading no character file at all."""
    v = _views._view(cid, v)
    return _roster_union(v, "characters", characters.listed_ids(v.croot),
                        characters.listed_ids(v.wroot))


def cast_ids(cid: str, kind: str, *, v: _views.View | None = None) -> list[str]:
    """The ids `list_characters`, `list_pcs` or `list_entities` would list for
    `kind`, off stats and directory listings alone -- for a caller that counts
    a cast (`sheets.coverage`) and never reads a row of it."""
    v = _views._view(cid, v)
    if kind == "characters":
        return character_ids(cid, v=v)
    if kind == "pcs":
        return _roster_union(v, "pcs", pcs.listed_ids(v.croot), pcs.listed_ids(v.wroot))
    # `list_entities`' union: the flat rule has no whole-record detachment to
    # honour here, because a detached flat record is one the campaign holds.
    return _roster_union(v, kind, entities.entity_ids(v.croot, kind),
                        entities.entity_ids(v.wroot, kind))


def character_sidecars(cid: str, *, v: _views.View | None = None) -> list[dict]:
    """`{id, name, tagline, has_voice_anchor}` per character, with exactly the
    values `list_characters` reports for those two fields.

    For the to-do list's tagline and anchor gaps, which read nothing else off
    the row. Both fields keep the full listing's precedence, which is subtler
    than "campaign, else world", and that is why this lives here rather than
    at the call site:

    - `tagline` is `tagline()`: the campaign's line, else nothing for a
      detached id (the world's id-mate is a stranger), else the world's.
    - `has_voice_anchor` is `_anchor_presence`, the very rule the full listing
      applies. Its world fallback reads the world's anchor only for a row the
      full listing would have had a WORLD row for: a campaign copy whose world
      original lost every card is not listed world-side, so the full listing
      never read that anchor, and neither does this.
    """
    v = _views._view(cid, v)
    theirs = characters.roster(v.wroot)
    rows = _roster_union(v, "characters", characters.roster(v.croot), theirs,
                         key=lambda r: r["id"])
    world_listed = {r["id"] for r in theirs}
    anchored = _anchor_presence(
        v, [r["id"] for r in rows],
        lambda aid: aid in world_listed and bool(voice_anchors.read(v.wroot, aid)))
    return [{"id": r["id"], "name": r["name"], "tagline": _sidecars.tagline(cid, r["id"], v=v),
             "has_voice_anchor": anchored[r["id"]]} for r in rows]


def character_refs(cid: str, *, v: _views.View | None = None) -> list[str]:
    """The campaign's character ids: `character_ids`, under the name the turn's
    off-scene directory has always called it by. It used to build the full
    listing and keep only the ids, on every generated turn."""
    return character_ids(cid, v=v)


# ---- payload patching: asset-derived fields come from the union ----

def read_character(cid: str, char_id: str, *, v: _views.View | None = None) -> dict:
    # One view across every version below and the pass-through after them:
    # each version asks the same campaign the same questions five times over.
    v = _views._view(cid, v)
    detail = characters.read_character(_actors.char_root(cid, char_id, v=v), char_id)
    croot = v.croot
    for ver in detail["versions"]:
        descriptions, images = _images.read_descriptions_listed(cid, char_id, ver["id"], v=v)
        ver["images"] = [i["name"] for i in images]
        ver["image_v"] = {i["name"]: i["v"] for i in images}
        ver["image_ids"] = {i["name"]: i["image_id"] for i in images if i.get("image_id")}
        # Which of those names the campaign holds no file for -- the Art tab
        # shelves the campaign's own pictures apart from the world's. Names
        # only: the token and caption of an inherited picture are the union's.
        mine = {i["name"] for i in assets.list_images(croot, char_id, ver["id"])}
        ver["inherited"] = [n for n in ver["images"] if n not in mine]
        ver["avatar_focus"] = _images.read_focus(cid, char_id, ver["id"], v=v)
        # Re-derived per image, not per folder: `read_descriptions` refuses to
        # caption a campaign-side picture with the world's sentence about a
        # different one. See its docstring.
        ver["image_descriptions"] = descriptions
        # The world's copies this version's own files hide -- shown beside
        # them, never instead of them. See `shadowed_images`.
        ver["world_shadowed"] = _images.shadowed_images(cid, char_id, ver["id"], v=v)
    # The world's versions the campaign no longer holds, passed through beside
    # the ones it does: a pick purges every other version from the campaign,
    # and the union above cannot reach a version id the campaign lacks.
    detail["base_versions"] = _images.base_versions(cid, char_id, v=v)
    return detail


def character_name_and_versions(cid: str, char_id: str, *,
                                v: _views.View | None = None) -> tuple[str, list[str]]:
    """`read_character`'s `meta.name` and version ids, reading no card and no
    art: both come off whichever root `char_root` says answers for the actor,
    exactly as the full read takes them. For the campaign describe queue,
    which asks it once per record behind an image."""
    v = _views._view(cid, v)
    return characters.name_and_versions(_actors.char_root(cid, char_id, v=v), char_id)


def read_pc(cid: str, pid: str, *, v: _views.View | None = None) -> dict:
    """`pcs.read_pc` with its image fields re-derived from the union.

    The persona files resolve whole-directory through `pc_root`, but assets
    overlay per file, so a materialized PC can still be showing an avatar that
    only the world has. Reading the detail off one root would hide it -- the
    same reason `read_character` exists rather than callers using
    `characters.read_character` directly (#219)."""
    v = _views._view(cid, v)
    detail = pcs.read_pc(_actors.pc_root(cid, pid, v=v), pid)
    for ver in detail["versions"]:
        descriptions, images = _images.read_descriptions_listed(cid, pid, ver["id"], pcs.ASSET_BASE, v=v)
        ver["images"] = [i["name"] for i in images]
        ver["avatar_focus"] = _images.read_focus(cid, pid, ver["id"], pcs.ASSET_BASE, v=v)
        ver["image_descriptions"] = descriptions
    return detail


def pc_revisions(cid: str, pid: str, vid: str) -> list[dict]:
    """A campaign PC version's revision history (#67): the CAMPAIGN copy's.

    History belongs to the copy that was edited, so a PC this campaign still
    inherits has none here -- not the world's, which is the world page's to
    show. The version is checked through `pc_root` first, so an id the campaign
    cannot see at all is a 404 rather than an empty list."""
    pcs.require_version(_actors.pc_root(cid, pid), pid, vid)
    croot = _record_paths.croot_of(cid)
    if not pcs.pc_exists(croot, pid):
        return []
    return pcs.list_revisions(croot, pid, vid)


def pc_revision(cid: str, pid: str, vid: str, rid: str) -> dict:
    """One entry of `pc_revisions`. Raises `pcs.PCRevisionNotFoundError` for an
    inherited PC, which has no campaign history to hold it."""
    pcs.require_version(_actors.pc_root(cid, pid), pid, vid)
    croot = _record_paths.croot_of(cid)
    if not pcs.pc_exists(croot, pid):
        raise pcs.PCRevisionNotFoundError(rid)
    return pcs.read_revision(croot, pid, vid, rid)
