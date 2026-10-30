"""Pictures: image objects, blobs, placements, legacy files, expressions and layering.

One picture object can be placed many times. The inventory keeps the two
apart: an object is listed once with its blob, and every placement (an avatar,
a gallery slot, an expression sprite, a campaign override) is its own row that
names the object. Object ids are V1's opaque ``px1-`` keys; the pixel
canonicalizer behind them is not reimplemented, so they are never "verified".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from . import _text
from ._campaigns import Campaign
from ._context import Context
from ._records import Library

BLOB_EXTS = frozenset({"png", "jpg", "gif", "webp"})
OUTFIT_META_KEYS = frozenset({"label", "clothing_aliases", "fallback_expression", "presentation"})
RECORD_ROUTE_KINDS = frozenset({"greetings", "locations", "lore", "items", "groups", "creatures"})


@dataclass
class Placements:
    objects: dict[str, dict] = field(default_factory=dict)
    blobs: dict[str, dict] = field(default_factory=dict)
    rows: list[dict] = field(default_factory=list)
    by_slot: dict[tuple, dict] = field(default_factory=dict)
    legacy: dict[tuple, list[dict]] = field(default_factory=dict)
    referenced: set[str] = field(default_factory=set)


def _owner(coords: dict) -> tuple:
    return (coords["scope"], coords["scope_id"], coords["base"], coords["record"], coords["version"])


# ---- objects and blobs ---------------------------------------------------------------

def read_store(ctx: Context, out: Placements) -> None:
    for entry in ctx.snap.files("blob"):
        c = entry.layout.coords
        sha, ext = c["sha"], c["ext"]
        if not (_text.SHA256.match(sha) and c["shard"] == sha[:2] and ext in BLOB_EXTS):
            ctx.finding("invalid_blob_name", entry.path)
            continue
        intact = entry.sha256 == sha
        if not intact:
            ctx.finding("blob_hash_mismatch", entry.path)
        out.blobs[f"{sha}.{ext}"] = {"path": entry.path, "sha256": sha, "ext": ext, "size": entry.size,
                                     "intact": intact, "objects": []}
    for entry in ctx.snap.files("image_object"):
        _object(ctx, out, entry)
    for key, blob in sorted(out.blobs.items()):
        if not blob["objects"]:
            ctx.finding("orphan_blob", blob["path"])


def _object(ctx: Context, out: Placements, entry) -> None:
    c = entry.layout.coords
    stem = c["object"]
    value, ok = ctx.read_json(entry)
    problems = []
    if not _text.IMAGE_ID.match(stem):
        problems.append("name")
    elif c["shard"] != stem[4:6]:
        ctx.finding("object_shard_mismatch", entry.path)
    if not ok or not isinstance(value, dict):
        problems.append("json")
        value = {}
    if type(value.get("format")) is not int or value.get("format") != 1:
        problems.append("format")
    if value.get("id") != stem:
        problems.append("id")
    if value.get("identity") not in ("pixels", "bytes"):
        problems.append("identity")
    blob = value.get("blob")
    if not isinstance(blob, dict):
        problems.append("blob")
        blob = {}
    sha, ext, size = blob.get("sha256"), blob.get("ext"), blob.get("size")
    if not (isinstance(sha, str) and _text.SHA256.match(sha)):
        problems.append("blob.sha256")
    if ext not in BLOB_EXTS:
        problems.append("blob.ext")
    if type(size) is not int or size < 0:
        problems.append("blob.size")
    if "animated" in blob and not isinstance(blob["animated"], bool):
        problems.append("blob.animated")
    row = {"id": stem, "path": entry.path, "valid": not problems}
    if problems:
        ctx.finding("invalid_object", entry.path, detail=",".join(problems))
        row["problems"] = problems
        out.objects[stem] = row
        return
    description = value.get("description")
    associations = value.get("associations")
    row.update({
        "identity": value["identity"], "blob_sha256": sha, "ext": ext, "size": size,
        "width": blob.get("width") if type(blob.get("width")) is int else None,
        "height": blob.get("height") if type(blob.get("height")) is int else None,
        "animated": blob.get("animated") is True,
        "has_description": isinstance(description, str) and bool(description),
        "description_sha256": _text.sha256(description.encode("utf-8")) if isinstance(description, str) else None,
        "subjects": sorted(_association(a) for a in associations if isinstance(a, dict))
        if isinstance(associations, list) else [],
        "sources": len(value["sources"]) if isinstance(value.get("sources"), list) else 0,
        "unknown_fields": sorted(k for k in value if k not in {
            "blob", "format", "id", "identity", "reason", "sources", "description",
            "description_conflicts", "associations", "reviews"}),
    })
    stored = out.blobs.get(f"{sha}.{ext}")
    if stored is None:
        row["blob"] = "unarrived"
        ctx.finding("unarrived_blob", entry.path, detail=f"{sha}.{ext}")
    else:
        stored["objects"].append(stem)
        row["blob"] = "present" if stored["intact"] else "hash_mismatch"
        if stored["size"] != size:
            ctx.finding("blob_size_mismatch", entry.path, stored["path"])
    out.objects[stem] = row


def _association(a: dict) -> str:
    return ":".join(str(a.get(k, "")) for k in ("relation", "kind", "scope", "id"))


def picture(out: Placements, image: str | None) -> str:
    """How a placement's object resolves: V1 needs both the sidecar and an intact blob."""
    if image is None:
        return "focus_only"
    obj = out.objects.get(image)
    if obj is None or not obj["valid"]:
        return "dangling_object"
    return "resolved" if obj.get("blob") == "present" else "unarrived_blob"


# ---- placements, legacy files and sidecars --------------------------------------------

def _slot(coords: dict) -> str:
    if coords["area"] == "expression":
        return "expression"
    if coords["base"] in ("cover", "library"):
        return coords["base"]
    name = coords["name"]
    if name == "avatar":
        return "avatar"
    return "embed" if name.startswith("embed-") else "gallery"


def read_placements(ctx: Context, out: Placements) -> None:
    for entry in ctx.snap.files("placement"):
        c = dict(entry.layout.coords)
        row = {"path": entry.path, "legacy_id": None, "owner": {k: c[k] for k in (
            "scope", "scope_id", "base", "record", "version")}, "area": c["area"], "name": c["name"],
            "slot": _slot(c), "image": None, "focus": None}
        if c["area"] == "expression":
            row.update(revision=c["revision"], outfit=c["outfit"])
        value, ok = ctx.read_json(entry)
        status = "invalid"
        if not _text.valid_image_name(c["name"]) and c["area"] == "record":
            status = "not_addressable"
        if ok and isinstance(value, dict) and type(value.get("format")) is int and value["format"] == 1:
            image = value.get("image")
            image_ok = "image" not in value or (isinstance(image, str) and _text.IMAGE_ID.match(image))
            focus = value.get("focus")
            focus = focus if type(focus) is int and 0 <= focus <= 100 else None
            if image_ok and (image is not None or focus is not None):
                row["image"], row["focus"] = image, focus
                if image is not None:
                    out.referenced.add(image)
                if status != "not_addressable":
                    status = picture(out, image)
        row["status"] = status
        if status == "invalid":
            ctx.finding("invalid_placement", entry.path)
            ctx.unresolve("placement", entry.path, "unparseable placement")
        elif status == "dangling_object":
            ctx.finding("dangling_object", entry.path, detail=row["image"])
        out.rows.append(row)
        key = _owner(c) + (c["area"], c.get("revision"), c.get("outfit"), c["name"])
        out.by_slot[key] = row
    for entry in ctx.snap.files("promote_journal"):
        value, ok = ctx.read_json(entry)
        out.referenced |= set(_ids_in(value)) if ok else set()
    for entry in ctx.snap.files("legacy_image"):
        c = entry.layout.coords
        out.legacy.setdefault(_owner(c) + (c["name"],), []).append(
            {"path": entry.path, "sha256": entry.sha256, "size": entry.size, "ext": c["ext"]})
    for entry in ctx.snap.files("asset_sidecar"):
        ctx.read_json(entry)  # presence; their captions and crops travel with the source


def _ids_in(value) -> list[str]:
    if isinstance(value, str):
        return [value] if _text.IMAGE_ID.match(value) else []
    if isinstance(value, dict):
        return [i for v in value.values() for i in _ids_in(v)]
    if isinstance(value, list):
        return [i for v in value for i in _ids_in(v)]
    return []


def legacy_rows(ctx: Context, out: Placements) -> list[dict]:
    rows = []
    for key in sorted(out.legacy, key=repr):
        files = sorted(out.legacy[key], key=lambda f: f["path"])
        placement = out.by_slot.get(key[:5] + ("record", None, None, key[5]))
        shadowed = placement is not None and placement["status"] == "resolved"
        if len(files) > 1:
            ctx.finding("legacy_image_ambiguous", *(f["path"] for f in files))
        for f in files:
            rows.append({**f, "name": key[5], "shadowed_by_placement": shadowed,
                         "ambiguous": len(files) > 1})
    return rows


def slot_exists(out: Placements, owner: tuple, name: str) -> bool:
    row = out.by_slot.get(owner + ("record", None, None, name))
    if row is not None and row["status"] == "resolved":
        return True
    return bool(out.legacy.get(owner + (name,)))


# ---- expressions ------------------------------------------------------------------------

def _enc(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def _validate_manifest(m) -> str | None:
    if not isinstance(m, dict):
        return "not an object"
    if type(m.get("format")) is not int or m["format"] != 1:
        return "format"
    if not (isinstance(m.get("revision"), str) and _text.HEX32.match(m["revision"])):
        return "revision"
    if type(m.get("placement_count")) is not int or m["placement_count"] < 0:
        return "placement_count"
    outfits = m.get("outfits")
    if not isinstance(outfits, dict):
        return "outfits"
    for outfit, meta in outfits.items():
        if not _text.SLUG.match(outfit) or not isinstance(meta, dict) or set(meta) - OUTFIT_META_KEYS:
            return f"outfit {outfit!r}"
    if m.get("default_outfit") is not None and not (
            isinstance(m["default_outfit"], str) and _text.SLUG.match(m["default_outfit"])):
        return "default_outfit"
    hidden = m.get("hidden_outfits")
    if not isinstance(hidden, list) or not all(isinstance(x, str) and _text.SLUG.match(x) for x in hidden):
        return "hidden_outfits"
    hidden_x = m.get("hidden_expressions")
    if not isinstance(hidden_x, dict) or not all(
            _text.SLUG.match(k) and isinstance(v, list) and all(isinstance(x, str) and _text.SLUG.match(x)
                                                                 for x in v)
            for k, v in hidden_x.items()):
        return "hidden_expressions"
    if not isinstance(m.get("content_digest"), str):
        return "content_digest"
    return None


def expression_layers(ctx: Context, out: Placements) -> dict[tuple, dict]:
    layers: dict[tuple, dict] = {}
    expression_rows = [r for r in out.rows if r["area"] == "expression"]
    for entry in ctx.snap.files("expression_manifest"):
        owner = _owner(entry.layout.coords)
        layer = {"path": entry.path, "owner": dict(zip(("scope", "scope_id", "base", "record", "version"),
                                                         owner)), "status": "available", "reason": None,
                 "revision": None, "default_outfit": None, "outfits": [], "hidden_outfits": [],
                 "hidden_expressions": {}, "sprites": []}
        layers[owner] = layer
        value, ok = ctx.read_json(entry)
        problem = "unreadable" if not ok else _validate_manifest(value)
        mine = [r for r in expression_rows if tuple(r["owner"].values()) == owner]
        if problem is None:
            revision = value["revision"]
            live = [r for r in mine if r["revision"] == revision]
            placements = {}
            for r in live:
                if not _text.SLUG.match(r["outfit"]) or not _text.SLUG.match(r["name"]):
                    problem = f"placement name {r['outfit']}/{r['name']}"
                elif r["outfit"] not in value["outfits"]:
                    problem = f"undeclared outfit {r['outfit']}"
                elif r["image"] is None or r["status"] == "invalid":
                    problem = f"unreadable placement {r['outfit']}/{r['name']}"
                elif (data := ctx.read(ctx.snap.entries[r["path"]])) is None:
                    problem = f"unreadable placement {r['outfit']}/{r['name']}"
                else:
                    placements[f"{r['outfit']}/image-refs/{r['name']}.json"] = data
            if problem is None:
                header = {k: v for k, v in value.items() if k not in ("content_digest", "placement_count")}
                digest = hashlib.sha256(_enc(header))
                for name, data in sorted(placements.items()):
                    digest.update(_enc([name, len(data)]))
                    digest.update(data)
                if len(placements) != value["placement_count"] or digest.hexdigest() != value["content_digest"]:
                    problem = "not completely synchronized (count or digest mismatch)"
            layer.update(revision=revision, default_outfit=value.get("default_outfit"),
                         outfits=sorted(value["outfits"]), hidden_outfits=sorted(value["hidden_outfits"]),
                         hidden_expressions={k: sorted(v) for k, v in sorted(value["hidden_expressions"].items())})
            for r in mine:
                r["live"] = r["revision"] == revision
                if not r["live"]:
                    ctx.finding("stale_expression_revision", r["path"])
            layer["sprites"] = sorted(f"{r['outfit']}/{r['name']}" for r in live)
        if problem is not None:
            layer["status"], layer["reason"] = "unavailable", problem
            ctx.finding("expression_layer_unavailable", entry.path, detail=problem)
            ctx.unresolve("expression_layer", entry.path, problem)
    for r in expression_rows:
        if tuple(r["owner"].values()) not in layers:
            r["live"] = False
            ctx.finding("stale_expression_revision", r["path"], detail="no manifest")
    return layers


# ---- collections ------------------------------------------------------------------------

def collection_rows(ctx: Context, out: Placements) -> list[dict]:
    rows = []
    for entry in ctx.snap.files("collection"):
        c = entry.layout.coords
        value, ok = ctx.read_json(entry)
        row = {"path": entry.path, "world": c["scope_id"], "id": c["collection"], "valid": False,
               "format": None, "members": []}
        rows.append(row)
        if (not ok or not isinstance(value, dict) or set(value) != {"format", "members"}
                or value["format"] not in (1, 2) or type(value["format"]) is not int
                or not isinstance(value["members"], list) or not _text.HEX32.match(c["collection"])):
            ctx.finding("invalid_collection", entry.path)
            ctx.unresolve("collection", entry.path, "invalid manifest")
            continue
        row.update(valid=True, format=value["format"])
        owner = ("world", c["scope_id"], "library", None, None)
        for member in value["members"]:
            if not isinstance(member, str):
                row["members"].append({"member": None, "status": "invalid"})
                continue
            if value["format"] == 2:
                status = picture(out, member) if _text.IMAGE_ID.match(member) else "invalid"
                out.referenced.add(member)
            else:
                status = "resolved" if slot_exists(out, owner, member) else "missing"
            if status not in ("resolved",):
                ctx.finding("unresolved_image_ref", entry.path, detail=member[:80])
            row["members"].append({"member": member, "status": status})
    return rows


# ---- campaign layering -----------------------------------------------------------------

def layering_rows(ctx: Context, lib: Library, out: Placements, campaigns: dict[str, Campaign]) -> list[dict]:
    rows = []
    for cid in sorted(campaigns):
        campaign = campaigns[cid]
        if not campaign.inherits:
            continue
        for p in out.rows:
            o = p["owner"]
            if o["scope"] != "world" or o["scope_id"] != campaign.world or p["area"] != "record":
                continue
            if o["base"] == "cover":
                continue
            row = {"campaign": cid, "world_placement": p["path"], "name": p["name"], "slot": p["slot"]}
            if o["base"] == "library":
                row["state"] = "hidden" if f"assets/library/{p['name']}" in campaign.deleted else "inherited"
                rows.append(row)
                continue
            record_ref = f"{o['base']}/{o['record']}"
            asset_ref = f"assets/{o['base']}/{o['record']}/{o['version']}/{p['name']}"
            mine = out.by_slot.get(("campaign", cid, o["base"], o["record"], o["version"], "record",
                                    None, None, p["name"]))
            if record_ref in campaign.detached or record_ref in campaign.deleted or asset_ref in campaign.deleted:
                row["state"] = "hidden"
            elif mine is not None and mine["status"] != "invalid":
                row["state"] = "focus_override" if mine["status"] == "focus_only" else "overridden"
                row["campaign_placement"] = mine["path"]
            else:
                row["state"] = "inherited"
            rows.append(row)
    return rows


def expression_layering(layers: dict[tuple, dict], campaigns: dict[str, Campaign], lib: Library) -> None:
    """Mark campaign expression layers that sit over an inherited world layer."""
    for owner, layer in layers.items():
        scope, cid, base, actor, version = owner
        if scope != "campaign":
            continue
        campaign = campaigns.get(cid)
        world_layer = None
        if campaign is not None and campaign.inherits and f"{base}/{actor}" not in campaign.detached:
            world_layer = layers.get(("world", campaign.world, base, actor, version))
        if world_layer is None:
            layer["over"] = None
            continue
        inherited = set(world_layer["sprites"]) if world_layer["status"] == "available" else set()
        layer["over"] = world_layer["path"]
        layer["overrides"] = sorted(set(layer["sprites"]) & inherited)
        layer["hides"] = sorted(
            [f"{o}/*" for o in layer["hidden_outfits"]]
            + [f"{o}/{x}" for o, xs in layer["hidden_expressions"].items() for x in xs])


# ---- Markdown image references ---------------------------------------------------------

def _route(url: str) -> tuple | None:
    """(scope, scope_id, base, record, version, name-or-collection) for a V1 local image route."""
    segments = _text.url_path_segments(url)
    if segments[:1] == ["api"]:
        segments = segments[1:]
    if len(segments) < 4 or segments[0] not in ("worlds", "campaigns"):
        return None
    scope = "world" if segments[0] == "worlds" else "campaign"
    sid, rest = segments[1], segments[2:]
    if len(rest) == 2 and rest[0] == "images":
        return (scope, sid, "library", None, None, rest[1])
    if len(rest) == 6 and rest[0] in ("characters", "pcs") and rest[2] == "versions" and rest[4] == "images":
        return (scope, sid, rest[0], rest[1], rest[3], rest[5])
    if len(rest) == 4 and rest[0] in RECORD_ROUTE_KINDS and rest[2] == "images":
        return (scope, sid, rest[0], rest[1], "default", rest[3])
    if rest[0] == "image-collections" and len(rest) in (3, 4):
        return (scope, sid, "collection", rest[1], None, "/".join(rest[2:]))
    return None


def _visible(out: Placements, owner: tuple, name: str, campaign: Campaign | None) -> bool:
    """Whether a slot shows from where it is read (through the overlay for a campaign)."""
    if campaign is None:
        return slot_exists(out, owner, name)
    _, _, base, record, version = owner
    mine = ("campaign", campaign.id, base, record, version)
    if slot_exists(out, mine, name):
        return True
    if not campaign.inherits:
        return False
    if base == "library":
        return f"assets/library/{name}" not in campaign.deleted and slot_exists(
            out, ("world", campaign.world, base, None, None), name)
    ref = f"{base}/{record}"
    if ref in campaign.detached or ref in campaign.deleted or \
            f"assets/{base}/{record}/{version}/{name}" in campaign.deleted:
        return False
    return slot_exists(out, ("world", campaign.world, base, record, version), name)


def reference_rows(ctx: Context, lib: Library, out: Placements, campaigns: dict[str, Campaign]) -> list[dict]:
    sources: list[tuple[str, str, Campaign | None]] = []
    for key in sorted(lib.records):
        record = lib.records[key]
        if record.kind == "greetings":
            sources.append((record.path, record.body, None))
    for key in sorted(lib.actors):
        for vid, version in sorted(lib.actors[key].versions.items()):
            sources += [(version.path, text, None) for text in version.texts]
    for cid in sorted(campaigns):
        for sid, texts in sorted(campaigns[cid].scene_texts.items()):
            path = f"{campaigns[cid].path}/scenes/{sid}.md"
            sources += [(path, text, campaigns[cid]) for text in texts]
    rows = []
    for path, text, campaign in sources:
        for url in _text.image_urls(text):
            if _text.is_external(url):
                ctx.finding("external_reference", path)
                rows.append({"source": path, "kind": "external", "url": _text.redact_url(url),
                             "status": "not_fetched"})
                continue
            route = _route(url)
            if route is None:
                ctx.finding("unresolved_image_ref", path, detail="unrecognized route")
                rows.append({"source": path, "kind": "unrecognized", "status": "unresolved"})
                continue
            scope, sid, base, record, version, name = route
            if base == "collection":
                status = "listed"
            else:
                names = _text.decoded_names(name)
                status = "resolved" if any(
                    _visible(out, (scope, sid, base, record, version), n, campaign) for n in names
                ) else "unresolved"
                if status == "unresolved":
                    ctx.finding("unresolved_image_ref", path, detail="/".join(
                        str(x) for x in (base, record, version, name) if x is not None)[:120])
            rows.append({"source": path, "kind": "local", "status": status,
                         "slot": {"scope": scope, "scope_id": sid, "base": base, "record": record,
                                  "version": version, "name": name},
                         "resolved_through": "campaign_overlay" if campaign is not None else "url_scope"})
    rows.sort(key=lambda r: (r["source"], json.dumps(r, sort_keys=True)))
    return rows
