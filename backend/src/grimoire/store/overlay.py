"""Campaign-over-world copy-on-write resolution.

A campaign materializes a record only when it diverges from its world (edit,
version lock, delete, campaign-local create); everything else reads through to
the world live. Rules:

- Flat records (locations/lore/greetings, plotmap.json): campaign file wins;
  else a tombstone means absent; else the world file.
- Actors (characters/pcs): whole-dir, keyed on character.md / pc.md existing in
  the campaign — a materialized actor is authoritative for meta + versions, so
  lock-purged versions stay purged. Sidecars (tagline.md) and assets still
  overlay per file.
- sync.md holds base hashes for materialized records only. Tombstones live in
  <campaign>/deleted.json (a sorted JSON list of refs); a tombstoned id counts
  as taken for uniquify, so nothing ever resurrects under a reused id.

Which records inherit, and which do not, is the whole content of the rule, so
it is declared below as data (INHERITED_KINDS / INHERITED_FILES) rather than
only in prose: `tests/test_overlay_guard.py` reads those names to check that
nothing outside this module resolves an inheritable record off a raw campaign
root. Everything else under <campaign> is campaign-local by definition and is
read directly: campaign.md, sync.md, deleted.json, appearances.json,
climate.json, calendar.json, changes.json, chronicle.json, timeline.md,
sheet_baselines.json, scenes/, sheets/, proposals/, and the per-actor sidecars
filed inside an actor dir (dossier.md, state.md). tagline.md is the exception
among sidecars -- it overlays per file, via `tagline()` below.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from . import assets, atomic, campaigns, cards, characters, entities, greetings, groupstate, pcs, taglines, worlds
from .paths import natural_key

#: Record kinds a campaign inherits from its world. A `<campaign>/<kind>/...`
#: read for one of these is only correct through this module (or after the
#: reader has materialized the record itself).
INHERITED_KINDS: tuple[str, ...] = entities.SYNCED_KINDS + ("characters", "pcs")

#: Campaign-root files that resolve through to the world the same way.
INHERITED_FILES: tuple[str, ...] = ("plotmap.json",)


def croot_of(cid: str) -> Path:
    return campaigns.campaign_root(cid)


def wroot_of(cid: str) -> Path:
    """The campaign's world root. May not exist — the world was deleted
    before the guard against that existed. Or, if the campaign's `world` meta
    is empty, this resolves to `worlds.world_root("")`: the worlds parent dir
    itself, which does exist but holds no `<kind>/<id>.md` files directly.
    Either way, nothing here raises for it — every resolver below just reads
    a path that doesn't hold the expected records as holding none."""
    return worlds.world_root(campaigns.read_campaign(cid)["meta"].get("world", ""))


# ---- tombstones ----

def _deleted_path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "deleted.json"


def deleted(cid: str) -> set[str]:
    p = _deleted_path(cid)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    return set(data) if isinstance(data, list) else set()


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
    manifest = campaigns.read_manifest(cid)
    previous = manifest.get(ref)
    manifest[ref] = base
    campaigns.write_manifest(cid, manifest)
    try:
        yield
    except BaseException:
        if not commit.exists():
            manifest = campaigns.read_manifest(cid)
            if previous is None:
                manifest.pop(ref, None)
            else:
                manifest[ref] = previous
            try:
                campaigns.write_manifest(cid, manifest)
            except Exception:
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
    manifest = campaigns.read_manifest(cid)
    if manifest.pop(ref, None) is not None:
        campaigns.write_manifest(cid, manifest)


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
    if kind == "groups":
        _delete_group_state(cid, eid)


def _delete_group_state(cid: str, gid: str) -> None:
    """Campaign-local state.md is never inherited from the world (state is
    campaign-local by definition), so it must die with the group record —
    otherwise a same-slug recreate silently reattaches the dead group's
    Secrets to scene context."""
    p = groupstate.state_path(croot_of(cid), gid)
    p.unlink(missing_ok=True)
    if p.parent.exists() and not any(p.parent.iterdir()):
        p.parent.rmdir()


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
