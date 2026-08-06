"""Campaign-over-world copy-on-write resolution.

A campaign materializes a record only when it diverges from its world (edit,
version lock, delete, campaign-local create); everything else reads through to
the world live. Rules:

- Flat records (every kind in INHERITED_KINDS that is not an actor -- currently
  locations, lore, items, groups, creatures, greetings -- plus plotmap.json):
  campaign file wins; else a tombstone means absent; else the world file.
- Actors (characters/pcs): whole-dir, keyed on character.md / pc.md existing in
  the campaign — a materialized actor is authoritative for meta + versions, so
  lock-purged versions stay purged. Sidecars (tagline.md, voice_anchor.md) and
  assets still overlay per file.
- sync.md holds base hashes for materialized records only. Tombstones live in
  <campaign>/deleted.json (a sorted JSON list of refs); a tombstoned id counts
  as taken for uniquify, so nothing ever resurrects under a reused id.

Which records inherit, and which do not, is the whole content of the rule, so
it is declared below as data (INHERITED_KINDS / INHERITED_FILES) rather than
only in prose: `tests/test_overlay_guard.py` reads those names to check that
nothing outside this module resolves an inheritable record off a raw campaign
root. Everything else under <campaign> is campaign-local by definition and is
read directly: campaign.md, sync.md, deleted.json, appearances.json,
calendar.json, changes.json, chronicle.json, timeline.md, sheet_baselines.json,
the climate default (store/campaign_climate.py owns that filename), scenes/,
sheets/, proposals/, and the per-actor sidecars filed inside an actor dir
(dossier.md, state.md, voice_drift.md). tagline.md and voice_anchor.md are the
exceptions among sidecars -- both are world-level identity, so both overlay per
file, via `tagline()` / `voice_anchor()` below.

Campaign-local, though, does not mean campaign-lifetime: those sidecars are
filed under a record id, and an id outlives the record it named. Deleting a
record frees its slug, so the next create hands the same id back -- which is
what `forget_world_record` (at the bottom) exists to get in front of.
"""

from __future__ import annotations

import json
import logging
import shutil
from contextlib import contextmanager
from pathlib import Path

from . import (assets, atomic, cards, characters, entities, failsoft, greetings,
               pcs, taglines, voice_anchors)
from .campaigns import paths as campaigns_paths, read as campaigns_read
from .worlds import paths as worlds_paths
from .paths import natural_key, safe_id

log = logging.getLogger(__name__)

#: Record kinds a campaign inherits from its world. A `<campaign>/<kind>/...`
#: read for one of these is only correct through this module (or after the
#: reader has materialized the record itself).
INHERITED_KINDS: tuple[str, ...] = entities.SYNCED_KINDS + ("characters", "pcs")

#: Campaign-root files that resolve through to the world the same way.
INHERITED_FILES: tuple[str, ...] = ("plotmap.json",)


def croot_of(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid)


def wroot_of(cid: str) -> Path:
    """The campaign's world root. May not exist — the world was deleted
    before the guard against that existed. Or, if the campaign records no
    world, `campaigns.world_root_of` yields a path nothing can occupy.
    Either way, nothing here raises for it — every resolver below just reads
    a path that doesn't hold the expected records as holding none."""
    return campaigns_read.world_root_of(cid)


# ---- tombstones ----

def _deleted_path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "deleted.json"


def deleted(cid: str) -> set[str]:
    """The campaign's tombstoned refs, empty when it has none.

    A corrupt file reads as empty too, because a campaign that cannot be opened
    at all is the worse failure -- but unlike the store's other fail-soft reads,
    this one degrades toward *more* content: "nothing was deleted" is how every
    record the user deleted campaign-side comes back, inherited from the world.
    That is the one direction of failure a user cannot spot by looking, so
    `failsoft` logs it (see that module for why only two reads do).
    """
    refs = failsoft.read_json(
        _deleted_path(cid), list,
        f"campaign {cid} reads as having no deletions, so records deleted here "
        "will reappear, inherited from the world")
    return set(refs) if refs else set()


def add_deleted(cid: str, ref: str) -> None:
    atomic.write_text(_deleted_path(cid), json.dumps(sorted(deleted(cid) | {ref}), indent=2) + "\n")


# ---- flat records (locations / lore; greetings + plotmap join in Task 2) ----

def _flat_ref(kind: str, eid: str) -> str:
    return f"{kind}/{eid}"


def _flat_path(root: Path, kind: str, eid: str) -> Path:
    return root / kind / f"{eid}.md"


@contextmanager
def _recorded_base(cid: str, ref: str, base: str, commit: Path):
    """Record `ref`'s sync base in sync.md, then make the copy it describes.
    `commit` is the copy's last write — the file whose existence *is* the
    materialization.

    Base first, copy second, because the two writes cannot be made one and a
    crash between them has to land on the harmless side (#247):

    - *base, no copy* — the record is still un-materialized, so it reads
      through to the world live and sync skips it (`mine_h is None`). The next
      materialization overwrites the base. Self-healing.
    - *copy, no base* — permanent silent divergence. Every ref the sync engine
      considers comes from the manifest, so that record never sees a world
      edit again, and nothing ever notices.

    The copy's own last write is therefore the commit point: for an actor that
    is character.md / pc.md, which is what `actor_root` keys on — version files
    landing without it leave the actor inherited, exactly as if the copy had
    not begun.

    An exception, unlike a crash, can unwind, so undo the base then. It restores
    what it displaced rather than dropping the ref: an earlier interrupted
    attempt may have left a base there.

    But undo only while `commit` is absent. An asynchronous exception —
    KeyboardInterrupt, a worker shutdown — can arrive after the commit write
    returned, and an unconditional undo would then strip the base off a copy
    that did land: the very state this ordering exists to prevent. A concurrent
    materialization of the same ref that finished while ours failed is the same
    case (Codex review).
    """
    manifest = campaigns_paths.read_manifest(cid)
    previous = manifest.get(ref)
    manifest[ref] = base
    campaigns_paths.write_manifest(cid, manifest)
    try:
        yield
    except BaseException:
        if not commit.exists():
            manifest = campaigns_paths.read_manifest(cid)
            if previous is None:
                manifest.pop(ref, None)
            else:
                manifest[ref] = previous
            try:
                campaigns_paths.write_manifest(cid, manifest)
            except Exception:  # noqa: BLE001 - the copy's failure is the one worth raising
                pass   # the copy's failure is the one worth raising
        raise


def _materialize_flat(cid: str, kind: str, eid: str) -> bool:
    """Copy an inherited flat record into the campaign and record its sync
    base. True if the campaign file exists afterwards. Assets are never
    copied — they overlay per file. Tombstoned records don't materialize."""
    croot = croot_of(cid)
    if _flat_path(croot, kind, eid).exists():
        return True
    wroot = wroot_of(cid)
    src = _flat_path(wroot, kind, eid)
    if not src.exists() or _flat_ref(kind, eid) in deleted(cid):
        return False
    dst = _flat_path(croot, kind, eid)
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = src.read_text(encoding="utf-8")
    # hashed from `text`, not re-read from the world: a world edit between the
    # two would otherwise record a base for content the campaign never got, and
    # sync would see world == base and skip the record forever (Codex review)
    with _recorded_base(cid, _flat_ref(kind, eid), entities.content_hash(text), dst):
        atomic.write_text(dst, text)
    return True


def _drop_manifest_ref(cid: str, ref: str) -> None:
    manifest = campaigns_paths.read_manifest(cid)
    if manifest.pop(ref, None) is not None:
        campaigns_paths.write_manifest(cid, manifest)


def materialize_entity(cid: str, kind: str, eid: str) -> None:
    if not _materialize_flat(cid, kind, eid):
        raise entities.EntityNotFound(f"{kind}/{eid}")


def list_entities(cid: str, kind: str) -> list[dict]:
    mine = entities.list_entities(croot_of(cid), kind)
    have = {e["id"] for e in mine}
    gone = deleted(cid)
    inherited = [e for e in entities.list_entities(wroot_of(cid), kind)
                 if e["id"] not in have and _flat_ref(kind, e["id"]) not in gone]
    return sorted(mine + inherited, key=lambda e: e["id"])


def read_entity(cid: str, kind: str, eid: str) -> dict:
    try:
        return entities.read_entity(croot_of(cid), kind, eid)
    except entities.EntityNotFound:
        if _flat_ref(kind, eid) in deleted(cid):
            raise
        return entities.read_entity(wroot_of(cid), kind, eid)


def create_entity(cid: str, kind: str, name: str, body: str = "", keys: str = "",
                  owners: str = "", sd_prompt: str = "", fields: dict | None = None) -> str:
    wroot, gone = wroot_of(cid), deleted(cid)

    def taken(eid: str) -> bool:
        return _flat_path(wroot, kind, eid).exists() or _flat_ref(kind, eid) in gone

    return entities.create_entity(croot_of(cid), kind, name, body, keys, owners,
                                  sd_prompt=sd_prompt, taken=taken, fields=fields)


def update_entity(cid: str, kind: str, eid: str, *, name: str | None = None,
                  body: str | None = None, keys: str | None = None,
                  owners: str | None = None, fields: dict | None = None) -> None:
    croot = croot_of(cid)
    if not _flat_path(croot, kind, eid).exists():
        materialize_entity(cid, kind, eid)
    entities.update_entity(croot, kind, eid, name=name, body=body, keys=keys, owners=owners, fields=fields)


def delete_entity(cid: str, kind: str, eid: str) -> None:
    ref = _flat_ref(kind, eid)
    in_world = _flat_path(wroot_of(cid), kind, eid).exists() and ref not in deleted(cid)
    try:
        entities.delete_entity(croot_of(cid), kind, eid)
        _drop_manifest_ref(cid, ref)
    except entities.EntityNotFound:
        if not in_world:
            raise
    if in_world:
        add_deleted(cid, ref)   # keep the world's copy from showing through
    _drop_record_dir(croot_of(cid), kind, eid)


# ---- greetings + plot map ----

def list_greetings(cid: str) -> list[dict]:
    mine = greetings.list_greetings(croot_of(cid))
    have = {g["id"] for g in mine}
    gone = deleted(cid)
    inherited = [g for g in greetings.list_greetings(wroot_of(cid))
                 if g["id"] not in have and _flat_ref("greetings", g["id"]) not in gone]
    out = mine + inherited
    out.sort(key=lambda m: natural_key(m["name"]))
    return out


def read_greeting(cid: str, gid: str) -> dict:
    try:
        return greetings.read_greeting(croot_of(cid), gid)
    except greetings.GreetingNotFound:
        if _flat_ref("greetings", gid) in deleted(cid):
            raise
        return greetings.read_greeting(wroot_of(cid), gid)


def create_greeting(cid: str, name: str, character: str, version: str, body: str = "",
                    requires_tags=None, predecessor_join: str = "all",
                    present=None, pcless: bool = False) -> str:
    wroot, gone = wroot_of(cid), deleted(cid)

    def taken(gid: str) -> bool:
        return _flat_path(wroot, "greetings", gid).exists() or _flat_ref("greetings", gid) in gone

    # #137: bake {{char}} here, not inside greetings.create_greeting -- a thin
    # campaign's character commonly still lives only in the world, and
    # char_root (not croot_of) is the resolver that finds it there.
    body = cards.bake_char_token(body, greetings.char_name(char_root(cid, character), character, version))
    return greetings.create_greeting(croot_of(cid), name, character, version, body,
                                     requires_tags, predecessor_join, present=present,
                                     pcless=pcless, taken=taken)


def update_greeting(cid: str, gid: str, **kwargs) -> None:
    if not _materialize_flat(cid, "greetings", gid):
        raise greetings.GreetingNotFound(gid)
    if kwargs.get("body") is not None:
        meta = greetings.read_greeting(croot_of(cid), gid)["meta"]
        character = meta.get("character", "")
        name = greetings.char_name(char_root(cid, character), character, meta.get("version", ""))
        kwargs["body"] = cards.bake_char_token(kwargs["body"], name)
    greetings.update_greeting(croot_of(cid), gid, **kwargs)


def delete_greeting(cid: str, gid: str) -> None:
    ref = _flat_ref("greetings", gid)
    in_world = _flat_path(wroot_of(cid), "greetings", gid).exists() and ref not in deleted(cid)
    if in_world:
        materialize_plotmap(cid)   # edge cleanup must land campaign-side
    try:
        greetings.delete_greeting(croot_of(cid), gid)
        _drop_manifest_ref(cid, ref)
    except greetings.GreetingNotFound:
        if not in_world:
            raise
        greetings.remove_from_plotmap(croot_of(cid), gid)
    if in_world:
        add_deleted(cid, ref)


def read_plotmap(cid: str) -> dict:
    croot = croot_of(cid)
    if (croot / "plotmap.json").exists() or "plotmap" in deleted(cid):
        return greetings.read_plotmap(croot)
    return greetings.read_plotmap(wroot_of(cid))


def materialize_plotmap(cid: str) -> None:
    croot, wroot = croot_of(cid), wroot_of(cid)
    if (croot / "plotmap.json").exists() or "plotmap" in deleted(cid):
        return
    src = wroot / "plotmap.json"
    if not src.exists():
        return   # nothing to copy; set_edges will create a fresh campaign map
    text = src.read_text(encoding="utf-8")
    dst = croot / "plotmap.json"
    with _recorded_base(cid, "plotmap", greetings.plotmap_content_hash(text), dst):
        atomic.write_text(dst, text)


def set_edges(cid: str, gid: str, leads_to=None, excludes=None) -> None:
    materialize_plotmap(cid)
    greetings.set_edges(croot_of(cid), gid, leads_to, excludes)


# ---- actors (characters / pcs): whole-dir resolution keyed on the container meta ----

def _actor_meta(kind: str) -> str:
    return "character.md" if kind == "characters" else "pc.md"


def _actor_not_found(kind: str, aid: str) -> Exception:
    return characters.CharacterNotFound(aid) if kind == "characters" else pcs.PCNotFound(aid)


def actor_root(cid: str, kind: str, aid: str) -> Path:
    """Root whose <kind>/<aid> dir is authoritative for meta + version files.
    Tombstoned actors resolve to the campaign, where the caller's read raises
    its usual NotFound."""
    croot = croot_of(cid)
    if (croot / kind / aid / _actor_meta(kind)).exists():
        return croot
    if _flat_ref(kind, aid) in deleted(cid):
        return croot
    return wroot_of(cid)


def char_root(cid: str, aid: str) -> Path:
    return actor_root(cid, "characters", aid)


def pc_root(cid: str, aid: str) -> Path:
    return actor_root(cid, "pcs", aid)


def materialize_actor(cid: str, kind: str, aid: str) -> None:
    """Copy meta + every version file (never assets or sidecars) from the world
    and record the whole-actor sync base. No-op when already materialized."""
    croot, wroot = croot_of(cid), wroot_of(cid)
    if (croot / kind / aid / _actor_meta(kind)).exists():
        return
    # one read of the world actor: the base and the bytes it covers, so the two
    # cannot disagree even if the world moves mid-copy (Codex review)
    taken = (characters.snapshot if kind == "characters" else pcs.snapshot)(wroot, aid)
    if taken is None or _flat_ref(kind, aid) in deleted(cid):
        raise _actor_not_found(kind, aid)
    base, files = taken
    meta_name = _actor_meta(kind)
    dst = croot / kind / aid
    dst.mkdir(parents=True, exist_ok=True)
    # No meta here means no materialized actor, so any version file in the
    # campaign dir is residue from an interrupted copy. Drop it: overwriting
    # only what the world still has would resurrect a version purged in
    # between, and the base we are about to record excludes it (Codex review).
    ext = "json" if kind == "characters" else "md"
    for p in dst.glob(f"*.{ext}"):
        if p.name != meta_name:
            p.unlink()
    with _recorded_base(cid, _flat_ref(kind, aid), base, dst / meta_name):
        # the meta is the single commit point, so it goes last -- `snapshot`
        # puts it first, and for a PC it is an *.md sibling of the versions
        for name, text in files:
            if name != meta_name:
                atomic.write_text(dst / name, text)
        atomic.write_text(dst / meta_name, dict(files)[meta_name])


def ensure_actor_writable(cid: str, kind: str, aid: str) -> Path:
    """Materialize an inherited actor and return the campaign root writes target."""
    croot = croot_of(cid)
    if not (croot / kind / aid / _actor_meta(kind)).exists():
        materialize_actor(cid, kind, aid)
    return croot


def dematerialize_actor(cid: str, kind: str, aid: str) -> None:
    """Remove meta + version files so the actor reverts to inherited. Sidecars
    (tagline/dossier/state) and assets stay — they overlay per file. PCs carry
    no sidecar .md files, so all *.md go."""
    d = croot_of(cid) / kind / aid
    if not d.exists():
        return
    if kind == "characters":
        targets = list(d.glob("*.json")) + [d / "character.md"]
    else:
        targets = list(d.glob("*.md"))
    for p in targets:
        if p.exists():
            p.unlink()
    if not any(d.iterdir()):
        d.rmdir()


def _patch_char_item(cid: str, item: dict) -> dict:
    names = [i["name"] for i in list_images(cid, item["id"], item["default_version"])]
    return {**item,
            "has_avatar": assets.AVATAR in names,
            "avatar_focus": read_focus(cid, item["id"], item["default_version"]),
            "gallery_count": sum(1 for n in names if n.startswith("gallery_")),
            "localized_count": sum(1 for n in names if n.startswith("embed-")),
            "tagline": tagline(cid, item["id"])}


def list_characters(cid: str) -> list[dict]:
    mine = characters.list_characters(croot_of(cid))
    # dossier/state-only dirs have no character.md and are filtered by
    # characters.list_characters itself (it requires the meta file)
    have = {c["id"] for c in mine}
    gone = deleted(cid)
    inherited = [c for c in characters.list_characters(wroot_of(cid))
                 if c["id"] not in have and _flat_ref("characters", c["id"]) not in gone]
    return sorted([_patch_char_item(cid, c) for c in mine + inherited], key=lambda c: c["id"])


def list_pcs(cid: str) -> list[dict]:
    mine = pcs.list_pcs(croot_of(cid))
    have = {p["id"] for p in mine}
    gone = deleted(cid)
    inherited = [p for p in pcs.list_pcs(wroot_of(cid))
                 if p["id"] not in have and _flat_ref("pcs", p["id"]) not in gone]
    return sorted(mine + inherited, key=lambda p: p["id"])


def character_refs(cid: str) -> list[str]:
    return [c["id"] for c in list_characters(cid)]


def create_character(cid: str, name: str, version_name: str = "default",
                     card: dict | None = None) -> tuple[str, str]:
    wroot, gone = wroot_of(cid), deleted(cid)

    def taken(aid: str) -> bool:
        return (wroot / "characters" / aid / "character.md").exists() or _flat_ref("characters", aid) in gone

    return characters.create_character(croot_of(cid), name, version_name, card, taken=taken)


def create_pc(cid: str, name: str, tags: list[str], version_name: str = "default",
              persona: dict | None = None) -> tuple[str, str]:
    wroot, gone = wroot_of(cid), deleted(cid)

    def taken(pid: str) -> bool:
        return (wroot / "pcs" / pid / "pc.md").exists() or _flat_ref("pcs", pid) in gone

    return pcs.create_pc(croot_of(cid), name, tags, version_name, persona, taken=taken)


# ---- assets: per-file union, campaign wins ----

def _asset_ref(base: str, aid: str, vid: str, name: str) -> str:
    return f"assets/{base}/{aid}/{vid}/{name}"


def list_images(cid: str, aid: str, vid: str, base: str = "characters") -> list[dict]:
    mine = assets.list_images(croot_of(cid), aid, vid, base)
    have = {i["name"] for i in mine}
    gone = deleted(cid)
    inherited = [i for i in assets.list_images(wroot_of(cid), aid, vid, base)
                 if i["name"] not in have and _asset_ref(base, aid, vid, i["name"]) not in gone]
    return sorted(mine + inherited, key=lambda i: i["name"])


def image_root(cid: str, aid: str, vid: str, name: str, base: str = "characters") -> Path:
    croot = croot_of(cid)
    if assets.image_path(croot, aid, vid, name, base) is not None:
        return croot
    gone = deleted(cid)
    # A per-asset tombstone or a whole-record tombstone (the record was deleted
    # campaign-side; only its <base>/<aid> ref is written) both hide the image:
    # return croot so the serve route 404s instead of falling through to the world.
    if _asset_ref(base, aid, vid, name) in gone or _flat_ref(base, aid) in gone:
        return croot
    return wroot_of(cid)


def read_focus(cid: str, aid: str, vid: str, base: str = "characters") -> int | None:
    croot = croot_of(cid)
    focus_file = croot / base / aid / "assets" / vid / assets.FOCUS_FILE
    if (assets.image_path(croot, aid, vid, assets.AVATAR, base) is not None
            or focus_file.exists()
            or _asset_ref(base, aid, vid, assets.AVATAR) in deleted(cid)):
        return assets.read_focus(croot, aid, vid, base)
    return assets.read_focus(wroot_of(cid), aid, vid, base)


def delete_image(cid: str, aid: str, vid: str, name: str, base: str = "characters") -> None:
    assets.delete_image(croot_of(cid), aid, vid, name, base)   # no-op when absent
    if assets.image_path(wroot_of(cid), aid, vid, name, base) is not None:
        add_deleted(cid, _asset_ref(base, aid, vid, name))


def promote_image(cid: str, aid: str, vid: str, name: str, base: str = "characters") -> None:
    """Copy-up the named image and the current avatar, then swap campaign-side."""
    croot, wroot = croot_of(cid), wroot_of(cid)
    for n in (name, assets.AVATAR):
        if (assets.image_path(croot, aid, vid, n, base) is None
                and _asset_ref(base, aid, vid, n) not in deleted(cid)):
            src = assets.image_path(wroot, aid, vid, n, base)
            if src is not None:
                assets.put_image(croot, aid, vid, n, src.read_bytes(),
                                 src.suffix.lstrip("."), base)
    assets.promote_image(croot, aid, vid, name, base)
    # When there was no avatar to swap into the promoted slot, the swap leaves
    # no campaign file at `name`, so the inherited image there would still show
    # through the overlay next to the new avatar. Tombstone it so promotion
    # moves the image out of the gallery instead of duplicating it.
    if (assets.image_path(croot, aid, vid, name, base) is None
            and assets.image_path(wroot, aid, vid, name, base) is not None):
        add_deleted(cid, _asset_ref(base, aid, vid, name))


# ---- payload patching: asset-derived fields come from the union ----

def read_character(cid: str, char_id: str) -> dict:
    detail = characters.read_character(char_root(cid, char_id), char_id)
    for v in detail["versions"]:
        v["images"] = [i["name"] for i in list_images(cid, char_id, v["id"])]
        v["avatar_focus"] = read_focus(cid, char_id, v["id"])
    return detail


def tagline(cid: str, char_id: str) -> str:
    return taglines.read(croot_of(cid), char_id) or taglines.read(wroot_of(cid), char_id)


def set_voice_anchor(cid: str, char_id: str, text: str) -> None:
    """Write the campaign's own anchor for `char_id`; blank opts the character
    out of voice checks in this campaign.

    Campaign-side by definition: this IS the per-file divergence, the same shape
    as any other campaign edit to an inherited record, and it lives here rather
    than at the call site so nothing outside this module hands `voice_anchors` a
    raw campaign root.

    Blank is the one place the anchor overlay does NOT behave like tagline's.
    Deleting the campaign copy would let the world's anchor show through again,
    so a user who cleared the field would still be judged -- against the very
    text they just erased -- while the editor told them clearing skips the
    checks. Blankness is this feature's opt-out switch, so it has to survive
    resolution: a blank save writes a TOMBSTONE.

    The one exception is a blank that erased nothing -- no world anchor, no
    campaign anchor, no tombstone already. There is no decision in that save to
    record, and a tombstone would turn it into a standing promise to ignore an
    anchor the world might add later. Anything else -- an inherited anchor, the
    campaign's own, or an opt-out already made -- is a decision, and once made
    only the user typing an anchor back in may end it. Not the world removing
    its anchor, and not a later save that changed nothing.

    The nonce `voice_anchors.write` preserves is read from the CAMPAIGN copy, so
    a campaign that overrides the world's anchor mints its own identity. That is
    correct rather than incidental: an override is a different standard, and
    findings judged against the world's should stop applying here.
    """
    croot = croot_of(cid)
    inherited = voice_anchors.read(wroot_of(cid), char_id)
    mine = voice_anchors.read_record(croot, char_id)
    if not text.strip():
        # Tombstone unless this blank erased nothing at all. Each of the three
        # is an opt-out the resolver would otherwise undo:
        #   inherited        -- the world's anchor would show through again
        #   mine["text"]     -- clearing an override IS the opt-out; deleting it
        #                       re-exposes the world's, or lets a world anchor
        #                       created later start judging a character whose
        #                       owner just erased one
        #   mine["disabled"] -- an opt-out already made, which must outlive the
        #                       world anchor being removed and restored
        if inherited or mine["text"] or mine["disabled"]:
            voice_anchors.disable(croot, char_id)
            return
        voice_anchors.write(croot, char_id, text)   # nothing to erase: just delete
        return
    if not mine["text"] and not mine["disabled"] and text.split() == inherited.split():
        # Still inheriting, and the submitted text IS the inherited text: the
        # editor shows the resolved anchor, so re-saving an untouched form
        # lands here. Materializing a copy would mint a new nonce -- silently
        # suppressing every committed flag fingerprinted against the identical
        # world anchor -- and detach the campaign from later world edits, all
        # for a save that changed nothing. Only a real divergence diverges.
        #
        # `.split()`, not `.strip()`, so "the same anchor" means here exactly
        # what it means to `voice_drift.anchor_fingerprint` -- which normalizes
        # whitespace THROUGHOUT because rewrapping a line is presentation, not a
        # new standard. With `.strip()` the two disagreed, and the disagreement
        # was the bug: rewrapping an inherited anchor materialized a campaign
        # copy whose nonce suppressed every flag judged against text the
        # fingerprint still considered identical. The rewrap itself is not
        # persisted, which is the same outcome as re-saving an untouched form
        # and for the same reason: by the only definition of anchor identity
        # this feature has, nothing changed.
        #
        # Deliberately not extended to a standing TOMBSTONE: re-entering the
        # world's words there is a decision to be judged again, and it gets a
        # fresh identity like any other opt-back-in.
        return
    voice_anchors.write(croot, char_id, text)


def voice_anchor_record(cid: str, char_id: str) -> dict:
    """The winning anchor's {"text", "id"} — same per-file overlay as
    `voice_anchor`, but carrying the nonce a fingerprint needs. Resolved as one
    record so text and identity always come from the SAME file: reading the
    text campaign-side and the nonce world-side would fingerprint an anchor that
    exists nowhere."""
    mine = voice_anchors.read_record(croot_of(cid), char_id)
    if mine["disabled"]:
        return mine   # explicit campaign opt-out: the world's must not show through
    return mine if mine["text"] else voice_anchors.read_record(wroot_of(cid), char_id)


def voice_anchor(cid: str, char_id: str) -> str:
    """The character's voice anchor as this campaign sees it.

    Same per-file overlay as tagline, with one difference tagline has no need
    for: a campaign can hold a TOMBSTONE saying it wants no anchor here, and
    that beats the world's rather than falling back to it (see
    `set_voice_anchor`). The anchor is world-level (a voice is a library
    property), but a campaign that has materialized its own copy of the
    character is entitled to its own reference text -- or to none."""
    return voice_anchor_record(cid, char_id)["text"]


# ---- world-side deletes: nothing outlives the record it was filed beside ----

def _record_dir(root: Path, kind: str, rid: str) -> Path:
    """`<root>/<kind>/<rid>/` — the directory a record's *neighbours* live in.

    Flat records are a file (`<kind>/<rid>.md`) with a sibling directory; actors
    are the directory. Either way this holds everything filed under the record's
    id rather than inside it: an actor's campaign-local sidecars (state.md,
    dossier.md, voice_drift.md), a group's state.md, and any kind's `assets/`.
    """
    return root / kind / rid


def _drop_record_dir(root: Path, kind: str, rid: str) -> None:
    """Remove `_record_dir`, if there is one.

    The id is the only thing tying those files to their record, and ids are
    handed out by slug (`entities.create_entity` / `characters.create_character`
    uniquify against what exists *now*), so a record deleted and recreated under
    the same name gets the same id back. Anything left behind is then adopted by
    a new, unrelated record — for a group's state.md that means a dead group's
    Secrets in a live scene's context (#225).

    A failure to remove is logged rather than raised: the record itself is
    already gone by the time this runs, and answering a delete that succeeded
    with a 500 helps nobody. `rmtree` stops at its first error, so what survives
    is a *part* of the directory rather than all of it — still strictly less
    than the pre-#225 leftovers, but the warning says which path to look at
    because "some of the dead record's sidecars" is not a state to leave a user
    guessing about.
    """
    if kind not in INHERITED_KINDS or not safe_id(rid):
        return
    d = _record_dir(root, kind, rid)
    if not d.is_dir():
        return
    try:
        shutil.rmtree(d)
    except OSError as exc:
        log.warning("could not remove %s (%s) -- a record recreated under the id "
                    "%r will inherit what is still in it", d, exc, rid)


def dependent_campaigns(wroot: Path) -> list[str]:
    """Ids of the campaigns that inherit from `wroot`.

    `list_campaigns`, not `campaigns.read.world_refs`: this is the opposite of
    `worlds.delete_world`'s in-use check, which must over-include because
    missing a campaign there destroys a world still in use. Missing one here
    leaves that campaign exactly as it was before this function existed, so the
    listing that agrees with the resolvers is the right one to sweep.
    """
    return [c["id"] for c in campaigns_read.list_campaigns()
            if worlds_paths.references_world(c["world"], wroot)]


def forget_world_record(wroot: Path, kind: str, rid: str) -> None:
    """Sweep what a world-side delete of `<kind>/<rid>` leaves reachable only
    by its id. Call it *after* the delete, from every world route that removes
    an inheritable record.

    Two places keep such leftovers:

    - the world's own `_record_dir` — `entities.delete_entity` unlinks the
      `.md` and nothing else, so the record's images survive it;
    - each dependent campaign's `_record_dir`, holding state the campaign filed
      against a record it only ever *inherited*. That state is campaign-local by
      definition, so no sync ever removes it, and a world-side delete leaves it
      unreachable but intact — until the next same-name create hands the id
      back and it re-attaches to a record it knows nothing about (#225).

    A campaign that materialized its own copy is left alone: its record did not
    go anywhere, so its state is still the state of something it has. That is
    also why the sweep cannot simply run for every campaign — deleting a
    world record has never removed a campaign's copy of it (`sync.incoming`
    skips world-side deletions), and this is not the change that starts.

    What this does NOT reach is campaign-local state keyed by the record id from
    *outside* the record's directory: `sheets/`, `relationships.json`,
    `appearances.json`, and the `deleted.json` tombstone that now hides a
    recreated record rather than adopting it. Each is a separate store with its
    own semantics, and sweeping them is the dependents design #52 carries.

    The enumeration is best-effort for the same reason the removal is: a store
    holding one campaign nobody can read must not make a world record
    undeletable, and the delete has already happened by the time we get here.
    """
    _drop_record_dir(wroot, kind, rid)
    try:
        cids = dependent_campaigns(wroot)
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("could not enumerate the campaigns of %s (%s) -- state they "
                    "filed against %s/%s stays, and a record recreated under that "
                    "id will inherit it", wroot, exc, kind, rid)
        return
    for cid in cids:
        croot = croot_of(cid)
        if kind in ("characters", "pcs"):
            mine = (croot / kind / rid / _actor_meta(kind)).exists()
        else:
            mine = _flat_path(croot, kind, rid).exists()
        if not mine:
            _drop_record_dir(croot, kind, rid)
