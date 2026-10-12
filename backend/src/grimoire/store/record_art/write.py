"""Record-art writes allocate placements against the effective visible slots.

Allocation is optimistic until the chosen world/campaign name stripes are held;
then occupancy is checked again. Publication holds ingest/GC exclusion through
the placement write, so a prepared but unplaced object cannot disappear between
validation and the reference that makes it live. Existing assets primitives own
sanitization, image identity, and sidecar preservation.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from .. import assets, image_descriptions, image_store, locks, overlay, revision
from . import targets

_GALLERY = re.compile(r"gallery_(\d+)\Z")


def _occupied(target: targets.Target) -> set[str]:
    d = assets.version_dir(target.root, target.record_id, target.version, target.kind)
    names = assets.names_in(d)[0]
    if target.view:
        names |= {i["name"] for i in overlay.list_images(target.scope_id, target.record_id,
                                                        target.version, target.kind, v=target.view)}
    return {name.casefold() for name in names}


def _candidate(target: targets.Target, *, gallery_only: bool = False) -> str:
    names = _occupied(target)
    if not gallery_only and assets.AVATAR not in names:
        return assets.AVATAR
    numbers = [int(match[1]) for name in names if (match := _GALLERY.fullmatch(name))]
    return f"gallery_{max(numbers, default=0) + 1}"


def _claim(target: targets.Target, obj: image_store.ImageObject, data: bytes, ext: str) -> dict:
    dirs = targets.directories(target)
    d = dirs[0]
    while True:
        name = _candidate(target)
        keyed = [(directory, slot) for directory in dirs for slot in {name, assets.AVATAR}]
        # A losing candidate's complete set is released before choosing again;
        # extending a held stripe set can reverse its order and deadlock.
        with assets.name_locks_held(*keyed):
            # Recovery may fill the chosen slot. Settle it before rechecking
            # occupancy, and keep avatar held so no new swap can start behind
            # this check before link_in runs its own writer-side recovery.
            for directory in dirs:
                assets.recover_promotion(directory, required=True)
            if _candidate(target) != name:
                continue
            with assets.sidecar_locks_held((d, assets.DESCRIPTIONS_FILE)), locks.image_ingest_gc_lock():
                current = image_store.read_fresh(obj.id)
                if current is None or not image_store.blob_intact(
                        image_store.blob_path(current.blob_sha256, current.ext)):
                    current = image_store.ingest(data, ext)
                # Decoding or reinstating an unplaced object can outlive its
                # destination. Recheck the real record/version while the name
                # stripes are held, before publishing any placement sidecar.
                targets.resolve(target.scope, target.scope_id, target.kind,
                                target.record_id, target.version if target.kind in targets.ACTOR_KINDS else None)
                image_descriptions.prepare_legacy(d, name, None)
                assets.link_in(d, name, current.id)
                return {"name": name, "ext": current.ext,
                        "v": current.blob_sha256, "image_id": current.id}


def append_world(wid: str, kind: str, rid: str, version: str | None,
                 data: bytes, ext: str) -> dict:
    target = targets.resolve("world", wid, kind, rid, version)
    obj = image_store.ingest(data, ext)
    return _claim(target, obj, data, ext)


def append_campaign(cid: str, kind: str, rid: str, version: str | None,
                    data: bytes, ext: str) -> dict:
    # Validate before decoding, but do not keep unrelated campaign writes
    # waiting on image sanitization. Re-resolve under the publication hold:
    # either the record/version or its inheritance can change during ingestion.
    with locks.campaign_lock(cid):
        targets.resolve("campaign", cid, kind, rid, version)
    obj = image_store.ingest(data, ext)
    with locks.campaign_lock(cid):
        target = targets.resolve("campaign", cid, kind, rid, version)
        return _claim(target, obj, data, ext)


class ArtConflictError(ValueError):
    """A captured placement/primary no longer describes the effective art."""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind


def _images(target: targets.Target) -> list[dict]:
    if target.view:
        return overlay.list_images(target.scope_id, target.record_id, target.version,
                                   target.kind, v=target.view)
    return assets.list_images(target.root, target.record_id, target.version, target.kind)


def _check_images(target: targets.Target, name: str, expected_v: str | None,
                  expected_primary_v: str | None, check_primary: bool) -> None:
    rows = {i["name"]: i for i in _images(target)}
    current = rows.get(name)
    if expected_v is not None and (current is None or current["v"] != expected_v):
        raise ArtConflictError("stale_image", "This image changed. Reload its art before trying again.")
    primary = rows.get(assets.AVATAR)
    if check_primary and (primary["v"] if primary else None) != expected_primary_v:
        raise ArtConflictError("stale_primary", "The primary image changed. Reload before choosing it again.")


def _focus(target: targets.Target, name: str, focus: int | None) -> None:
    if focus is None or not 0 <= focus <= 100:
        raise ValueError("focus must be between 0 and 100")
    if name != assets.AVATAR or not any(i["name"] == name for i in _images(target)):
        raise FileNotFoundError("image not found")
    assets.write_focus(target.root, target.record_id, target.version, focus, target.kind)


def _describe(target: targets.Target, name: str, description: str | None, guards: dict) -> None:
    if description is None:
        raise ValueError("description required")
    if target.view:
        overlay.set_description(target.scope_id, target.record_id, target.version,
                                name, description, target.kind, **guards)
    else:
        image_descriptions.set_description(target.root, target.record_id, target.version,
                                           name, description, target.kind, **guards)


def _perform(target: targets.Target, name: str, operation: str,
             description: str | None, focus: int | None, description_guards: dict) -> dict:
    root, rid, vid, kind = target.root, target.record_id, target.version, target.kind
    if operation == "focus":
        _focus(target, name, focus)
    elif operation == "delete":
        if target.view:
            overlay.delete_image(target.scope_id, rid, vid, name, kind)
        else:
            assets.delete_image(root, rid, vid, name, kind)
    elif operation == "promote":
        if target.view:
            overlay.promote_image(target.scope_id, rid, vid, name, kind)
        else:
            assets.promote_image(root, rid, vid, name, kind)
    elif operation == "description":
        _describe(target, name, description, description_guards)
    else:
        raise ValueError("unknown art operation")
    return {"ok": True}


def _guarded(target: targets.Target, name: str, operation: str, *,
             expected_v: str | None = None, expected_primary_v: str | None = None,
             check_primary: bool = False, description: str | None = None,
             focus: int | None = None, expected_description_rev: str | None = None,
             cleanup_only: bool = False) -> dict:
    if cleanup_only and (expected_v is None or expected_description_rev is None):
        raise image_descriptions.DescriptionGuardRequiredError(
            "Cleanup requires image and description preconditions.")
    if not assets.storable(name):
        raise FileNotFoundError("image not found")
    dirs = targets.directories(target)
    names = [(d, n) for d in dirs for n in {name, assets.AVATAR}]
    with assets.name_locks_held(*names), assets.sidecar_locks_held(
            *[(d, assets.DESCRIPTIONS_FILE) for d in dirs]):
        # Read-side recovery is best effort; a successful pre-swap comparison
        # is unsafe if the writer can finish that swap before deleting/cropping.
        for directory in dirs:
            assets.recover_promotion(directory, required=True)
        _check_images(target, name, expected_v, expected_primary_v, check_primary)
        return _perform(target, name, operation, description, focus,
                        {"expected_description_rev": expected_description_rev,
                         "cleanup_only": cleanup_only})


def guarded_world(wid: str, kind: str, rid: str, version: str | None,
                  name: str, operation: str, **guards) -> dict:
    return _guarded(targets.resolve("world", wid, kind, rid, version), name, operation, **guards)


def guarded_campaign(cid: str, kind: str, rid: str, version: str | None,
                     name: str, operation: str, **guards) -> dict:
    with locks.campaign_lock(cid):
        try:
            return _guarded(targets.resolve("campaign", cid, kind, rid, version), name, operation, **guards)
        except image_descriptions.DescriptionCleanupError:
            # The revision middleware stamps only successful responses. This
            # failed response still reports a confirmed shared write, so it
            # must invalidate campaign readers before releasing the hold.
            revision.bump(cid)
            raise


@dataclass(frozen=True)
class _PreparedSource:
    image_id: str
    data: bytes | None = None
    ext: str = ""


def _source_token(source: targets.Source) -> tuple[dict, Path]:
    row, d = targets.source_image(source)
    if row["v"] != source.expected_v:
        raise ArtConflictError("stale_image", "The selected source changed. Reload it before reusing it.")
    return row, d


def _prepare_source(source: targets.Source) -> _PreparedSource:
    # Legacy decoding happens before the destination claim. A replacement can
    # land during ingestion, so publication MUST revalidate the captured token.
    # Existing placements need no byte copy and never authorize arbitrary ids.
    with assets.name_locks_held(*[(d, name) for d in source.dirs for name in {source.name, assets.AVATAR}]):
        for directory in source.dirs:
            assets.recover_promotion(directory, required=True)
        row, d = _source_token(source)
        if row.get("image_id"):
            return _PreparedSource(row["image_id"])
        path = assets.path_in(d, source.name)
        if path is None:
            raise targets.ArtTargetNotFoundError("source image not found")
        data, ext = path.read_bytes(), path.suffix
        _source_token(source)
    obj = image_store.ingest(data, ext)
    return _PreparedSource(obj.id, data, ext)


def _arrived_source(prepared: _PreparedSource) -> image_store.ImageObject:
    obj = image_store.read_fresh(prepared.image_id)
    if obj is not None and image_store.blob_intact(image_store.blob_path(obj.blob_sha256, obj.ext)):
        return obj
    if prepared.data is not None:
        return image_store.ingest(prepared.data, prepared.ext)
    raise targets.ArtTargetNotFoundError("source image has not arrived or its stored bytes are damaged")


def _link_source(target: targets.Target, source_ref: dict, name: str,
                 prepared: _PreparedSource, intent: str) -> dict | None:
    with locks.image_ingest_gc_lock():
        obj = _arrived_source(prepared)
        # Restoring a GC-collected legacy object decodes again. Either record
        # can disappear during that work; the source can also change or become
        # hidden. Re-resolve the original typed reference to preserve its world
        # library visibility rules, then recheck its captured bytes token before
        # publishing anything. Caption state must come from that current source.
        target = targets.resolve(target.scope, target.scope_id, target.kind,
                                 target.record_id, target.version if target.kind in targets.ACTOR_KINDS else None)
        source = targets.resolve_source(target, source_ref)
        _, source_dir = _source_token(source)
        effective_dirs = source.dirs if source_dir != source.dirs[0] else (source_dir,)
        state = image_descriptions.description_state_in(source_dir, source.name,
                                                       effective_dirs=effective_dirs)["effective_description"]
        d = targets.directories(target)[0]
        primary = next((r for r in _images(target) if r["name"] == assets.AVATAR), None)
        same_slot = source.record and source.record.dto() == target.dto() and source.name == assets.AVATAR
        if intent == "primary" and primary and (primary.get("image_id") == obj.id or same_slot):
            return {"status": "ok", "no_op": True,
                    "created": {"name": assets.AVATAR, "ext": primary["ext"],
                                "v": primary["v"], "image_id": obj.id}}
        # Listing either record can rescue a stranded promotion into the slot
        # chosen before validation. Check after this final recovery-capable read,
        # then release the complete lock set and allocate again if it filled.
        # Reusing link_in onto that recovered slot would delete its legacy bytes.
        if _candidate(target, gallery_only=intent == "gallery") != name:
            return None
        # Caption metadata precedes the reference that makes this new slot
        # visible. Failure cannot claim a caption was carried when it was not;
        # legacy source bytes and all source metadata stay untouched.
        caption = state["text"] if state["scope"] == "legacy" and state["described"] else None
        image_descriptions.prepare_legacy(d, name, caption)
        assets.link_in(d, name, obj.id)
        created = {"name": name, "ext": obj.ext, "v": obj.blob_sha256, "image_id": obj.id}
    if intent == "primary" and name != assets.AVATAR:
        return _promote_linked(target, name, created)
    return {"status": "ok", "created": created}


def _promote_linked(target: targets.Target, name: str, created: dict) -> dict:
    try:
        _perform(target, name, "promote", None, None, {})
    except (OSError, ValueError, locks.StoreBusy) as exc:
        # The append already landed. Its returned slot permits promotion-only
        # recovery; treating this as a failed append would duplicate the art.
        return {"status": "partial", "created": created,
                "error": {"kind": "promotion_failed", "detail": str(exc)}}
    return {"status": "ok", "created": {**created, "name": assets.AVATAR}}


def _check_reuse_intent(intent: str, check_primary: bool) -> None:
    if intent not in ("gallery", "primary") or (intent == "primary" and not check_primary):
        raise ValueError("Primary reuse requires the displayed primary expectation.")


def _reuse(scope: str, sid: str, kind: str, rid: str, version: str | None,
           ref: dict, intent: str, expected_primary_v: str | None, check_primary: bool,
           *, prepared: _PreparedSource | None = None) -> dict:
    _check_reuse_intent(intent, check_primary)
    target = targets.resolve(scope, sid, kind, rid, version)
    if prepared is None:
        prepared = _prepare_source(targets.resolve_source(target, ref))
    while True:
        name = _candidate(target, gallery_only=intent == "gallery")
        source = targets.resolve_source(target, ref)
        dirs = tuple(dict.fromkeys((*targets.directories(target), *source.dirs)))
        keyed = [(d, n) for d in targets.directories(target) for n in {name, assets.AVATAR}]
        keyed.extend((d, n) for d in source.dirs for n in {source.name, assets.AVATAR})
        with assets.name_locks_held(*keyed), assets.sidecar_locks_held(
                *[(d, assets.DESCRIPTIONS_FILE) for d in dirs]):
            # Record/version and effective source visibility are checked again
            # under the same complete sorted sets as occupancy/publication.
            target = targets.resolve(scope, sid, kind, rid, version)
            source = targets.resolve_source(target, ref)
            for directory in dirs:
                assets.recover_promotion(directory, required=True)
            if _candidate(target, gallery_only=intent == "gallery") != name:
                continue
            _check_images(target, name, None, expected_primary_v, check_primary)
            linked = _link_source(target, ref, name, prepared, intent)
            if linked is not None:
                return linked


def reuse_world(wid: str, kind: str, rid: str, version: str | None,
                source: dict, intent: str, *, expected_primary_v: str | None = None,
                check_primary: bool = False) -> dict:
    return _reuse("world", wid, kind, rid, version, source, intent, expected_primary_v, check_primary)


def reuse_campaign(cid: str, kind: str, rid: str, version: str | None,
                   source: dict, intent: str, *, expected_primary_v: str | None = None,
                   check_primary: bool = False) -> dict:
    _check_reuse_intent(intent, check_primary)
    with locks.campaign_lock(cid):
        target = targets.resolve("campaign", cid, kind, rid, version)
        resolved_source = targets.resolve_source(target, source)
    # Legacy source decoding follows upload preparation's short-lock policy.
    # _reuse resolves both records and checks the captured source token again
    # under the final sorted name/sidecar and campaign publication holds.
    prepared = _prepare_source(resolved_source)
    with locks.campaign_lock(cid):
        return _reuse("campaign", cid, kind, rid, version, source, intent,
                      expected_primary_v, check_primary, prepared=prepared)
