"""Stage immutable placements, then atomically publish a complete manifest.

Image writers acquire their own name/object locks before sidecar locks. We never
hold publication sidecars while staging images; final publication only compares
the revision and swaps metadata. Losing a race leaves an unreachable staged
revision, never a partially changed collection.
"""

from __future__ import annotations

import copy
import uuid
from contextlib import ExitStack, nullcontext

from .. import assets, atomic, locks
from . import contracts, paths, pins, read, storage
from . import mutations as operations


def apply_effective(target: contracts.ExpressionTarget, mutations: list[dict], *,
                    expected_effective_revision: str) -> dict:
    if not isinstance(mutations, list) or not 1 <= len(mutations) <= 1025:
        raise contracts.ExpressionInvalidError("Expected a bounded mutation batch")
    hold = locks.campaign_lock(target.owner_id) if target.scope == "campaign" else nullcontext()
    with hold, ExitStack() as leases:
        directory = paths.directory(target)
        layer_paths = paths.layers(target)
        publication = [(d, "manifest.json") for _, d in layer_paths]
        with assets.sidecar_locks_held(*publication):
            for _, path in layer_paths:
                leases.enter_context(pins.hold(path, storage.layer(path)[0]["revision"]))
            current = read.effective(target)
            if current["effective_revision"] != expected_effective_revision:
                raise contracts.ExpressionConflictError("Expression library changed; reload before saving")
            inherited = storage.merge([(source, path, *storage.layer(path)) for source, path in layer_paths[:-1]])
            manifest, refs = read.layer(directory)
            manifest = copy.deepcopy(manifest)
        slots: dict = {key: ref.image for key, ref in refs.items()}
        for mutation in mutations:
            if not isinstance(mutation, dict):
                raise contracts.ExpressionInvalidError("Invalid mutation")
            operations.apply(manifest, slots, mutation, inherited)
        revision = uuid.uuid4().hex
        manifest["previous_revision"] = manifest["revision"]
        manifest["revision"] = revision
        leases.enter_context(pins.hold(directory, revision))
        staging = directory / "revisions" / revision
        for (outfit, expression), image in slots.items():
            destination = staging / outfit
            if isinstance(image, tuple):
                data, ext = image
                assets.put_in(destination, expression, data, ext)
            else:
                assets.link_in(destination, expression, image)
        placements = {p.relative_to(staging).as_posix(): p.read_bytes()
                      for p in staging.glob("*/image-refs/*.json")}
        manifest["placement_count"] = len(placements)
        manifest["content_digest"] = read.checksum(manifest, placements)
        payload = read.encoded(manifest)
        if len(payload) > 1024 * 1024:
            raise contracts.ExpressionInvalidError("Expression metadata exceeds 1 MiB")
        directory.mkdir(parents=True, exist_ok=True)
        with assets.sidecar_locks_held(*publication):
            if paths.layers(target) != layer_paths or read.effective(target)["effective_revision"] != expected_effective_revision:
                raise contracts.ExpressionConflictError("Expression library changed during upload")
            atomic.write_bytes(directory / "manifest.json", payload)
        return read.effective(target)
