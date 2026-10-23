"""Actor-aware expression reads; physical layers are independently portable."""

import hashlib
from contextlib import ExitStack, contextmanager

from .. import assets
from ..tracker import fields
from . import contracts, paths, pins, storage
from .storage import checksum as checksum
from .storage import encoded as encoded
from .storage import layer as layer


def local(target: contracts.ExpressionTarget) -> dict | None:
    manifest, _ = storage.layer(paths.directory(target))
    return manifest if manifest["revision"] else None


def effective(target: contracts.ExpressionTarget) -> dict:
    layers = [(source, directory, *storage.layer(directory)) for source, directory in paths.layers(target)]
    return {"format": 1, "revision": layers[-1][2]["revision"],
            "effective_revision": hashlib.sha256(storage.encoded([target.__dict__, paths.incarnation(target),
                [(source, str(directory), manifest.get("content_digest"), manifest["revision"])
                 for source, directory, manifest, _ in layers]])).hexdigest(),
            **storage.merge(layers), "standard_labels": list(fields.VISIBLE_MOODS)}


@contextmanager
def pinned(target: contracts.ExpressionTarget):
    layers = paths.layers(target)
    with ExitStack() as leases:
        with assets.sidecar_locks_held(*[(directory, "manifest.json") for _, directory in layers]):
            for _, directory in layers:
                leases.enter_context(pins.hold(directory, storage.layer(directory)[0]["revision"]))
            collection = effective(target)
        yield collection
