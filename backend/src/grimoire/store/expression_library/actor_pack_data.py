"""Bounded native actor snapshots, with original expression layers retained.

Ordinary art is captured as the visible slots. Expression layers stay separate
so importing with an explicitly matching dependency preserves future inheritance.
"""

import hashlib
import io
import json
import zipfile
from pathlib import Path

from .. import (
    assets,
    campaigns,
    characters,
    image_hash,
    image_refs,
    image_store,
    image_surfaces,
    overlay,
    pcs,
    worlds,
)
from .. import paths as store_paths
from ..frontmatter import parse_frontmatter
from . import contracts, packs, paths, storage, transfer


class BoundedFiles(dict):
    """Bound the snapshot while building it, before opening the output archive."""

    def __setitem__(self, key, value):
        total = sum(map(len, self.values())) - len(self.get(key, b"")) + len(value)
        if total > packs.MAX_UPLOAD or (key not in self and len(self) >= packs.MAX_MEMBERS):
            raise contracts.ExpressionBudgetError("Native actor pack exceeds 512 members or 128 MiB")
        super().__setitem__(key, value)

    def update(self, values):
        for name, value in values.items():
            self[name] = value


def _file_bytes(path: Path) -> bytes:
    with path.open("rb") as stream:
        return packs._bounded(stream, packs.images.MAX_BYTES)


def owner_root(target):
    if target.scope == "world":
        root = worlds.world_root(target.owner_id)
        if not (root / "world.md").is_file():
            raise FileNotFoundError("World not found")
    else:
        root = campaigns.campaign_root(target.owner_id)
        if not (root / "campaign.md").is_file():
            raise FileNotFoundError("Campaign not found")
    return root


def actor_snapshot(root, target):
    directory = root / target.kind / target.actor_id
    if image_surfaces.linked(store_paths.home(), directory):
        raise contracts.ExpressionUnavailableError("Linked actor directory")
    _preflight_cards(directory)
    snap = (characters.snapshot if target.kind == "characters" else pcs.snapshot)(root, target.actor_id)
    if snap is None:
        raise FileNotFoundError("Actor not found")
    return snap


def _preflight_cards(directory):
    total = count = 0
    for path in directory.iterdir() if directory.is_dir() else ():
        if path.suffix.lower() in (".md", ".json"):
            if image_surfaces.linked(directory, path):
                raise contracts.ExpressionUnavailableError("Linked actor card")
            size = path.stat().st_size
            total += size
            count += 1
            if size > packs.images.MAX_BYTES:
                raise contracts.ExpressionBudgetError("Actor card exceeds 20 MiB")
            if total > packs.MAX_UPLOAD or count > packs.MAX_MEMBERS:
                raise contracts.ExpressionBudgetError("Actor cards exceed native pack limits")


def dependency_identity(root: Path, target) -> dict:
    digest, pairs = actor_snapshot(root, target)
    world, _ = parse_frontmatter((root / "world.md").read_text(encoding="utf-8"))
    actor, _ = parse_frontmatter(pairs[0][1])
    identity = (root / target.kind / target.actor_id / pairs[0][0]).stat()
    # The store has no portable actor UUID. Native file identity deliberately
    # restricts binding to this original incarnation; an identical recreated
    # card or a cross-store copy must be explicitly imported detached.
    return {"world_id": root.name, "world_created": world.get("created"),
            "actor_id": target.actor_id, "actor_created": actor.get("created"), "cards_digest": digest,
            "native_identity": [identity.st_dev, identity.st_ino, identity.st_ctime_ns]}


def _active_layer(directory: Path) -> dict[str, bytes]:
    manifest, refs = storage.layer(directory)
    if not manifest["revision"]:
        return {}
    result = {"manifest.json": storage.encoded(manifest)}
    for outfit, label in refs:
        relative = f'revisions/{manifest["revision"]}/{outfit}/image-refs/{label}.json'
        path = directory / relative
        if image_surfaces.linked(directory, path):
            raise contracts.ExpressionUnavailableError("Linked expression placement")
        result[relative] = path.read_bytes()
    return result


def capture(target) -> tuple[dict, dict[str, bytes]]:
    root = owner_root(target)
    source = overlay.actor_root(target.owner_id, target.kind, target.actor_id) if target.scope == "campaign" else root
    _, cards = actor_snapshot(source, target)
    versions = [Path(name).stem for name, _ in cards[1:]]
    targets = [contracts.ExpressionTarget(**target.__dict__, version_id=vid) for vid in versions]
    layers = {t.version_id: paths.layers(t) for t in targets}
    directories = [directory for value in layers.values() for _, directory in value]
    files = BoundedFiles()
    files.update({"actor/" + name: text.encode() for name, text in cards})
    inherited = next((d for value in layers.values() for role, d in value if role == "inherited"), None)
    dependency = dependency_identity(inherited.parents[4], target) if inherited else None
    with transfer.pinned_directories(directories):
        for vid, value in layers.items():
            for role, directory in value:
                prefix = "dependency" if role == "inherited" else "actor"
                files.update({f"{prefix}/assets/{vid}/expressions/{name}": data
                              for name, data in _active_layer(directory).items()})
        _ordinary_assets(target, source, versions, files)
        _objects(files)
    sources = [{"scope": target.scope, "owner_id": target.owner_id}]
    if inherited:
        sources.append({"scope": "world", "owner_id": inherited.parents[4].name})
    manifest = {"format": 1, "kind": target.kind, "actor_id": target.actor_id,
                "versions": versions, "dependency": dependency, "source_owners": sources}
    return manifest, files


def _ordinary_files(source, target, files):
    actor = source / target.kind / target.actor_id
    for path in actor.glob("assets/**/*"):
        if not path.is_file() or image_surfaces.linked(actor, path):
            continue
        relative = path.relative_to(actor)
        if "expressions" in relative.parts or "image-refs" in relative.parts or path.name.startswith("."):
            continue
        # Version-level images are replaced below by the effective named slots.
        if len(relative.parts) == 3 and path.suffix.lower() in packs.EXTENSIONS:
            continue
        files["actor/" + relative.as_posix()] = _file_bytes(path)


def _ordinary_assets(target, source, versions, files):
    _ordinary_files(source, target, files)
    for vid in versions:
        rows = (overlay.list_images(target.owner_id, target.actor_id, vid, target.kind)
                if target.scope == "campaign" else assets.list_images(source, target.actor_id, vid, target.kind))
        for row in rows:
            name = row["name"]
            root = (overlay.image_root(target.owner_id, target.actor_id, vid, name, target.kind)
                    if target.scope == "campaign" else source)
            directory = assets.version_dir(root, target.actor_id, vid, target.kind)
            if image_surfaces.linked(store_paths.home(), image_refs.ref_path(directory, name)):
                raise contracts.ExpressionUnavailableError("Linked ordinary image placement")
            ref = image_refs.read(directory, name)
            if ref and ref.image:
                files[f"actor/assets/{vid}/image-refs/{name}.json"] = image_refs.ref_path(directory, name).read_bytes()
            else:
                path = assets.image_path(root, target.actor_id, vid, name, target.kind)
                if path:
                    if image_surfaces.linked(store_paths.home(), path):
                        raise contracts.ExpressionUnavailableError("Linked ordinary image")
                    files[f"actor/assets/{vid}/{name}{path.suffix}"] = _file_bytes(path)
            if name == assets.AVATAR:
                # Image inheritance and occurrence crop inheritance are independent.
                # A legacy image also needs a focus-only placement in the archive.
                focus = (overlay.read_focus(target.owner_id, target.actor_id, vid, target.kind)
                         if target.scope == "campaign" else assets.read_focus(source, target.actor_id, vid, target.kind))
                placement = f"actor/assets/{vid}/image-refs/{name}.json"
                payload = json.loads(files.get(placement, b'{"format":1}'))
                payload.pop("focus", None)
                if focus is not None:
                    payload["focus"] = focus
                if payload.get("image") or focus is not None:
                    files[placement] = storage.encoded(payload)



def _objects(files):
    ids = set()
    for name, data in list(files.items()):
        if "/image-refs/" in name:
            ref = json.loads(data)
            if ref.get("image"):
                ids.add(ref["image"])
    for image_id in sorted(ids):
        if image_surfaces.linked(store_paths.home(), image_store.object_path(image_id)):
            raise contracts.ExpressionUnavailableError("Linked image object")
        obj = image_store.read(image_id)
        if obj is None:
            raise contracts.ExpressionUnavailableError("Referenced image is unavailable")
        blob = image_store.blob_path(obj.blob_sha256, obj.ext)
        if image_surfaces.linked(store_paths.home(), blob) or not image_store.blob_intact(blob):
            raise contracts.ExpressionUnavailableError("Referenced image has not synchronized")
        files[f"objects/{image_id}.json"] = storage.encoded({"blob": obj.blob_sha256, "ext": obj.ext,
                                                           "description": obj.raw.get("description")})
        files[f"blobs/{obj.blob_sha256}.{obj.ext}"] = _file_bytes(blob)


def decode(data: bytes) -> tuple[dict, dict[str, bytes]]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = BoundedFiles()
            for info in packs._checked_members(archive):
                with archive.open(info) as stream:
                    name = "/".join(packs.ziputil.member_parts(info.filename, err=contracts.ExpressionInvalidError))
                    files[name] = packs._bounded(stream, packs.images.MAX_BYTES)
            manifest = json.loads(files.pop("grimoire-character.json"))
        _validate(manifest, files)
        return manifest, files
    except contracts.ExpressionInvalidError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, OSError, zipfile.BadZipFile) as exc:
        raise contracts.ExpressionInvalidError("Invalid native character pack") from exc


def _validate(manifest: dict, files: dict) -> None:
    if type(manifest.get("format")) is not int or manifest["format"] != 1 or manifest.get("kind") not in ("characters", "pcs"):
        raise contracts.ExpressionInvalidError("Unsupported native actor format")
    contracts.ExpressionActorTarget("world", "pack", manifest["kind"], manifest["actor_id"])
    _validate_sources(manifest)
    versions = manifest["versions"]
    if not isinstance(versions, list) or not versions or len(set(versions)) != len(versions):
        raise contracts.ExpressionInvalidError("Invalid actor versions")
    for version in versions:
        contracts.ExpressionTarget("world", "pack", manifest["kind"], manifest["actor_id"], version)
    objects = {}
    for name, data in files.items():
        parts = _member_path(name)
        if parts[0] not in ("actor", "dependency", "objects", "blobs"):
            raise contracts.ExpressionInvalidError("Unexpected native pack member")
        if parts[0] == "objects":
            image_id = Path(name).stem
            if len(parts) != 2 or not image_hash.is_image_id(image_id) or name != f"objects/{image_id}.json":
                raise contracts.ExpressionInvalidError("Invalid image object name")
            meta = json.loads(data)
            if meta["ext"] not in ("png", "jpg", "webp", "gif"):
                raise contracts.ExpressionInvalidError("Invalid image blob extension")
            blob = files[f'blobs/{meta["blob"]}.{meta["ext"]}']
            if hashlib.sha256(blob).hexdigest() != meta["blob"]:
                raise contracts.ExpressionInvalidError("Image blob checksum mismatch")
            objects[image_id] = meta
    _contained(files, objects)


def _validate_sources(manifest):
    sources = manifest["source_owners"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 2:
        raise contracts.ExpressionInvalidError("Invalid native actor source owners")
    for source in sources:
        if not isinstance(source, dict) or set(source) != {"scope", "owner_id"}:
            raise contracts.ExpressionInvalidError("Invalid native actor source owner")
        contracts.ExpressionActorTarget(**source, kind=manifest["kind"], actor_id=manifest["actor_id"])


def _member_path(name: str) -> list[str]:
    parts = name.split("/")
    exact = {"assets", "expressions", "image-refs", "manifest.json", "revisions", "pins", "character.md", "pc.md"}
    for part in parts:
        folded = part.casefold()
        if (folded in exact and part != folded) or folded == image_refs.JOURNAL:
            raise contracts.ExpressionInvalidError("Noncanonical native asset path")
    if "image-refs" in parts and (parts[-2] != "image-refs" or not parts[-1].endswith(".json")
                                  or not image_refs._valid_name(Path(name).stem)):
        raise contracts.ExpressionInvalidError("Invalid native placement path")
    return parts


def _contained(files, objects):
    for name, data in files.items():
        if "/image-refs/" in name:
            ref = json.loads(data)
            if ref.get("image") and ref["image"] not in objects:
                raise contracts.ExpressionInvalidError("Uncontained image reference")
