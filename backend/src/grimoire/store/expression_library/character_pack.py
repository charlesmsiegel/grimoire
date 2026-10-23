"""Native actor exchange with explicit lineage and staged, new-record publication."""

import hashlib
import io
import json
import logging
import re
import secrets
import tempfile
import time
import zipfile
from contextlib import suppress
from pathlib import Path

from .. import atomic, characters, image_refs, image_store, locks, overlay, pcs
from .. import paths as store_paths
from ..overlay import _ledgers
from ..worlds import staging
from . import actor_pack_data as data
from . import contracts, packs, storage, transfer

log = logging.getLogger(__name__)


def _hold(target):
    return locks.campaign_lock(target.owner_id) if target.scope == "campaign" else locks.world_actor_lock(target.owner_id)


def export(target: contracts.ExpressionActorTarget, destination: Path) -> None:
    with _hold(target):
        manifest, files = data.capture(target)
    files["grimoire-character.json"] = storage.encoded(manifest)
    if len(files) > packs.MAX_MEMBERS or sum(map(len, files.values())) > packs.MAX_UPLOAD:
        raise contracts.ExpressionBudgetError("Native actor pack exceeds 512 members or 128 MiB")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, payload in sorted(files.items()):
            archive.writestr(name, payload)
    payload = buffer.getvalue()
    if len(payload) > packs.MAX_UPLOAD:
        raise contracts.ExpressionBudgetError("Native actor pack exceeds 128 MiB")
    atomic.write_bytes(destination, payload)


def _can_bind(target, manifest) -> bool:
    dependency = manifest.get("dependency")
    if not dependency or target.scope != "campaign" or target.actor_id != manifest["actor_id"]:
        return False
    view = overlay.view(target.owner_id)
    ref = f"{target.kind}/{target.actor_id}"
    if ref in view.off or ref in view.gone:
        return False
    try:
        return data.dependency_identity(view.wroot, target) == dependency
    except FileNotFoundError:
        return False


def _cache() -> Path:
    identity = hashlib.sha256(str(store_paths.home()).encode()).hexdigest()
    return Path(tempfile.gettempdir()) / "grimoire-native-actor-previews" / identity


def import_preview(upload, destination: contracts.ExpressionActorTarget) -> dict:
    data.owner_root(destination)
    raw = packs._bounded(upload, packs.MAX_UPLOAD)
    manifest, files = data.decode(raw)
    if manifest["kind"] != destination.kind:
        raise contracts.ExpressionInvalidError("Character and PC pack kinds must match")
    with staging.staging_tree() as tree:
        _stage(manifest, files, tree, ingest=False)
    token = secrets.token_hex(32)
    expires = time.time() + 1800
    record = {"home": str(store_paths.home()), "target": destination.__dict__, "expires": expires,
              "sha256": hashlib.sha256(raw).hexdigest()}
    directory = _cache()
    directory.mkdir(parents=True, exist_ok=True)
    # Cache serialization prevents an abandoned-preview sweep racing a writer.
    with locks.world_actor_lock("native-actor-preview-cache"):
        pending = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for path in pending[:-7]:
            path.with_suffix(".zip").unlink(missing_ok=True)
            path.unlink(missing_ok=True)
        atomic.write_bytes(directory / (token + ".zip"), raw)
        atomic.write_bytes(directory / (token + ".json"), storage.encoded(record))
    return {"token": token, "expires_at": expires, "kind": manifest["kind"],
            "versions": manifest["versions"], "has_dependency": bool(manifest.get("dependency")),
            "can_bind": _can_bind(destination, manifest)}


def _load(token: str, destination):
    if not isinstance(token, str) or len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
        raise contracts.ExpressionInvalidError("Invalid native pack preview")
    try:
        with locks.world_actor_lock("native-actor-preview-cache"):
            record = json.loads((_cache() / (token + ".json")).read_bytes())
            with (_cache() / (token + ".zip")).open("rb") as stream:
                raw = packs._bounded(stream, packs.MAX_UPLOAD)
        if (record["home"] != str(store_paths.home()) or record["target"] != destination.__dict__
                or record["expires"] < time.time() or record["sha256"] != hashlib.sha256(raw).hexdigest()):
            raise contracts.ExpressionInvalidError("Native pack preview expired or belongs to another target")
        return data.decode(raw)
    except (OSError, KeyError, ValueError) as exc:
        raise contracts.ExpressionInvalidError("Native pack preview unavailable; upload again") from exc


def _stage(manifest, files, tree: Path, *, ingest: bool):
    kind, aid = manifest["kind"], manifest["actor_id"]
    for name, payload in files.items():
        prefix, _, relative = name.partition("/")
        if prefix not in ("actor", "dependency"):
            continue
        path = tree / prefix / kind / aid / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_bytes(path, payload)
    root = tree / "actor"
    reader = characters.read_character if kind == "characters" else pcs.read_pc
    try:
        actor = reader(root, aid)
        if {v["id"] for v in actor["versions"]} != set(manifest["versions"]):
            raise contracts.ExpressionInvalidError("Native pack versions do not match its cards")
        for version in manifest["versions"]:
            (characters.read_card if kind == "characters" else pcs.read_persona)(root, aid, version)
        for side in ("actor", "dependency"):
            for directory in transfer.directories(tree / side):
                storage.layer(directory)
        _validate_images(files)
    except contracts.ExpressionInvalidError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, FileNotFoundError,
            characters.CharacterNotFound, characters.VersionNotFound, pcs.PCNotFound, pcs.PCVersionNotFound) as exc:
        raise contracts.ExpressionInvalidError("Unreadable native actor data") from exc
    mapping = _rehome(tree, files) if ingest else {}
    return root / kind / aid, mapping


def _validate_images(files):
    sprite_ids = {json.loads(raw).get("image") for name, raw in files.items()
                  if "/expressions/" in name and "/image-refs/" in name}
    sprite_blobs = {json.loads(raw)["blob"] for name, raw in files.items()
                    if name.startswith("objects/") and Path(name).stem in sprite_ids}
    pixels = 0
    for name, raw in files.items():
        if name.startswith("blobs/") and Path(name).stem in sprite_blobs:
            _, count = packs.images.validate(raw)
            pixels += count
            if pixels > packs.MAX_PIXELS:
                raise contracts.ExpressionBudgetError("Native pack exceeds 128 megapixels")
        elif name.startswith("blobs/"):
            image_store.identity_of(image_store.prepare(raw, Path(name).suffix[1:]))
        if "/image-refs/" in name and Path(name).name.startswith("."):
            raise contracts.ExpressionInvalidError("Transient image journal in actor pack")


def _rehome(tree, files):
    id_map = {}
    for name, raw in files.items():
        if name.startswith("objects/"):
            obj = json.loads(raw)
            id_map[Path(name).stem] = image_store.ingest(files[f'blobs/{obj["blob"]}.{obj["ext"]}'], obj["ext"]).id
    for side in ("actor", "dependency"):
        root = tree / side
        directories = transfer.directories(root)
        transfer.rehome(root, id_map, set(id_map.values()))
        for owner, ref in image_refs.walk(root):
            if any(owner.is_relative_to(directory) for directory in directories):
                continue
            if ref.image is not None:
                image_refs.write(owner, ref.name, id_map[ref.image], focus=ref.focus)
    return id_map


def import_commit(token: str, destination: contracts.ExpressionActorTarget, disposition: str):
    if disposition not in ("bind", "detached"):
        raise contracts.ExpressionInvalidError("Choose bind or detached explicitly")
    manifest, files = _load(token, destination)
    with _hold(destination), staging.staging_tree() as tree:
        root = data.owner_root(destination)
        final = root / destination.kind / destination.actor_id
        if final.exists():
            raise contracts.ExpressionConflictError("Destination already has local files; choose a new actor ID")
        if disposition == "bind" and not _can_bind(destination, manifest):
            raise contracts.ExpressionConflictError("World dependency does not match; choose detached import")
        actor, mapping = _stage(manifest, files, tree, ingest=True)
        if disposition == "detached":
            _materialize(tree, actor, manifest)
        _repoint_cards(actor, manifest, destination)
        _publish(destination, actor, final, disposition)
    _descriptions(files, mapping, destination)
    with locks.world_actor_lock("native-actor-preview-cache"), suppress(OSError):
        (_cache() / (token + ".json")).unlink(missing_ok=True)
        (_cache() / (token + ".zip")).unlink(missing_ok=True)
    return destination


def _repoint_cards(actor: Path, manifest: dict, destination):
    """Copied embedded art follows this actor's serving address across stores.

    Campaign cards may still name the inherited world's art. Both original
    owners are recorded in the pack; unrelated actors and remote URLs stay as
    authored. Only card/metadata files are rewritten, never sprite manifests
    whose exact bytes participate in revision checksums.
    """
    new = (f"/api/{destination.scope}s/{destination.owner_id}/{destination.kind}/"
           f"{destination.actor_id}/versions/").encode()
    patterns = [re.compile(rb"(?<![A-Za-z0-9:/])" + re.escape(
        (f'/api/{source["scope"]}s/{source["owner_id"]}/{manifest["kind"]}/'
         f'{manifest["actor_id"]}/versions/').encode())) for source in manifest["source_owners"]]
    for path in actor.iterdir():
        if path.is_file() and path.suffix.lower() in (".md", ".json"):
            original = path.read_bytes()
            payload = original
            for pattern in patterns:
                payload = pattern.sub(lambda _: new, payload)
            if payload != original:
                atomic.write_bytes(path, payload)


def _descriptions(files, mapping, destination):
    for original, local in mapping.items():
        description = json.loads(files[f"objects/{original}.json"]).get("description")
        if not isinstance(description, str) or len(description) > image_store.MAX_DESCRIPTION:
            continue
        try:
            image_store.merge_projection(local, {"description": description}, f"{destination.scope}:{destination.owner_id}")
        except Exception:
            # The actor is already published; metadata failure must never report
            # the whole import as failed and invite a duplicate retry.
            log.warning("Could not retain imported image description", exc_info=True)


def _materialize(tree, actor, manifest):
    for version in manifest["versions"]:
        local = actor / "assets" / version / "expressions"
        inherited = tree / "dependency" / manifest["kind"] / manifest["actor_id"] / "assets" / version / "expressions"
        transfer.materialize([inherited, local], local)


def _publish(destination, actor: Path, final: Path, disposition: str):
    final.parent.mkdir(parents=True, exist_ok=True)
    # Mark detached before publication: a crash may leave a conservative marker
    # for an absent record, but never a published actor attached to a stranger.
    ledger = _ledgers._detached_path(destination.owner_id) if destination.scope == "campaign" else None
    before = ledger.read_bytes() if ledger and ledger.exists() else None
    if ledger and disposition == "detached":
        overlay.add_detached(destination.owner_id, f"{destination.kind}/{destination.actor_id}")
    try:
        actor.rename(final)
    except BaseException:
        if ledger and disposition == "detached" and not final.exists():
            atomic.write_bytes(ledger, before) if before is not None else ledger.unlink(missing_ok=True)
        raise
