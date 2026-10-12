"""Uniform art metadata, retaining assets and overlay as the visibility owners."""

import re
from urllib.parse import quote

from .. import assets, image_descriptions, image_store, overlay
from . import targets

OPERATIONS = ("append", "reuse", "promote", "focus", "description", "remove")
_NUMERIC_GALLERY = re.compile(r"gallery_(\d+)\Z")


def image_order(row: dict) -> tuple:
    name = row["name"]
    numeric = _NUMERIC_GALLERY.fullmatch(name)
    # Legacy named slots are peers too: map2 must precede map10. A type tag
    # keeps numeric and text tokens comparable; spelling breaks equal ties.
    natural = tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                    for part in re.split(r"(\d+)", name))
    return (0, 0, natural, name) if name == assets.AVATAR else (
        (1, int(numeric[1]), natural, name) if numeric else (2, 0, natural, name))


def placement(scope: str, scope_id: str, kind: str, rid: str, vid: str,
              name: str, token: str) -> dict:
    target = {"scope": {"kind": scope, "id": scope_id}, "kind": kind, "record_id": rid}
    if kind in targets.ACTOR_KINDS:
        target["version"] = vid
    return {"surface": "record", "target": target, "name": name, "expected_v": token}


def _row(target: targets.Target, image: dict, root, descriptions: dict,
         provenance: str, *, world_original: bool = False) -> dict:
    scope = "world" if world_original else target.scope
    # world_root_of already resolves/validates the id; a real original can
    # exist only beneath that world's root, whose final segment is its id.
    sid = target.view.wroot.name if world_original and target.view else target.scope_id
    prefix = f"/api/{scope}s/{quote(sid, safe='')}/{target.kind}/{quote(target.record_id, safe='')}"
    if target.kind in targets.ACTOR_KINDS:
        prefix += f"/versions/{quote(target.version, safe='')}"
    d = assets.version_dir(root, target.record_id, target.version, target.kind)
    other = (assets.version_dir(target.root, target.record_id, target.version, target.kind)
             if target.view and root != target.root and not world_original else None)
    try:
        snapshot = image_descriptions.description_state_in(d, image["name"], also_clear=other)
    except OSError:
        # Art remains viewable when caption metadata is unavailable, but the
        # editor cannot authorize a shared Save by treating that state as empty.
        snapshot = {"description_available": False, "description_rev": None}
    else:
        binding = snapshot.pop("image_binding")
        image = {**image, "v": binding["v"], "image_id": binding["image_id"]}
        effective = snapshot["effective_description"]
        descriptions = {image["name"]: effective["text"]} if effective["described"] else {}
        snapshot["description_available"] = True
        if effective["scope"]:
            snapshot["description_scope"] = effective["scope"]
    url = f"{prefix}/images/{quote(image['name'], safe='')}"
    token = quote(image["v"], safe="")
    out = {**image, **snapshot, "url": f"{url}?v={token}",
           "description": descriptions.get(image["name"], ""),
           "described": image["name"] in descriptions, "provenance": provenance,
           "placement": placement(scope, sid, target.kind, target.record_id,
                                  target.version, image["name"], image["v"])}
    if image["name"] == assets.AVATAR:
        out["focus"] = (overlay.read_focus(target.scope_id, target.record_id,
                                           target.version, target.kind, v=target.view)
                        if target.view and not world_original else
                        assets.read_focus(root, target.record_id, target.version, target.kind))
    obj = image_store.read(image.get("image_id", "")) if image.get("image_id") else None
    if obj:
        out.update({"width": obj.width, "height": obj.height, "animated": obj.animated})
    return out


def read_target(target: targets.Target) -> dict:
    rid, vid, kind = target.record_id, target.version, target.kind
    originals = []
    originals_error = None
    if target.view:
        ov = target.view
        images = overlay.list_images(target.scope_id, rid, vid, kind, v=ov)
        descriptions = overlay.read_descriptions(target.scope_id, rid, vid, kind, v=ov)
        rows = []
        for image in images:
            root = overlay.image_root(target.scope_id, rid, vid, image["name"], kind, v=ov)
            rows.append(_row(target, image, root, descriptions,
                             "campaign" if root == ov.croot else "inherited"))
        # Originals are supplemental, never the source of an effective row.
        # A sync/read failure here must not erase art the campaign can display.
        if f"{kind}/{rid}" not in ov.off:
            try:
                shadowed = overlay.shadowed_images(target.scope_id, rid, vid, base=kind, v=ov)
                if shadowed:
                    world_images = {i["name"]: i for i in assets.list_images(ov.wroot, rid, vid, kind)}
                    world_desc = {i["name"]: i["description"] for i in shadowed if "description" in i}
                    for image in shadowed:
                        current = world_images.get(image["name"])
                        if current is None:
                            # World writers do not take this campaign's lock.
                            # A removed/promoted original between these reads
                            # must not erase the effective or surviving shelf.
                            originals_error = "World originals could not be loaded."
                            continue
                        originals.append(_row(target, current, ov.wroot, world_desc,
                                              "world-original", world_original=True))
            except (OSError, UnicodeDecodeError):
                originals_error = "World originals could not be loaded."
    else:
        images = assets.list_images(target.root, rid, vid, kind)
        descriptions = image_descriptions.read_all(target.root, rid, vid, kind)
        rows = [_row(target, i, target.root, descriptions, "world") for i in images]
    operations = [*OPERATIONS]
    if target.scope == "world":
        operations.append("draft_description")
    result = {"target": target.dto(), "images": sorted(rows, key=image_order),
              "world_originals": sorted(originals, key=image_order), "operations": operations}
    if originals_error:
        result["world_originals_error"] = originals_error
    return result


def read_world(wid: str, kind: str, rid: str, version: str | None) -> dict:
    return read_target(targets.resolve("world", wid, kind, rid, version))


def read_campaign(cid: str, kind: str, rid: str, version: str | None) -> dict:
    return read_target(targets.resolve("campaign", cid, kind, rid, version))
