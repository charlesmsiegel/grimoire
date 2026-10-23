"""Actor-scoped collection copies use existing campaign serialization."""

import json
import re
import shutil
import time
from contextlib import nullcontext

from .. import assets, image_surfaces, locks
from .. import paths as store_paths
from ..image_gc import _contracts as gc_contracts
from . import contracts, paths, storage, transfer


def materialize_effective(target: contracts.ExpressionTarget) -> None:
    hold = locks.campaign_lock(target.owner_id) if target.scope == "campaign" else nullcontext()
    with hold:
        transfer.materialize([directory for _, directory in paths.layers(target)], paths.directory(target))


def copy_effective(source: contracts.ExpressionTarget, destination: contracts.ExpressionTarget) -> None:
    campaigns = sorted({t.owner_id for t in (source, destination) if t.scope == "campaign"})
    with locks.hold_all(campaigns):
        transfer.materialize([directory for _, directory in paths.layers(source)], paths.directory(destination))


def cleanup_revisions(target: contracts.ExpressionTarget) -> int:
    hold = locks.campaign_lock(target.owner_id) if target.scope == "campaign" else nullcontext()
    with hold:
        directory = paths.directory(target)
        if image_surfaces.linked(store_paths.home(), directory):
            raise contracts.ExpressionUnavailableError("Linked expression directory")
        with assets.sidecar_locks_held((directory, "manifest.json")):
            manifest, _ = storage.layer(directory)
            retained = {manifest["revision"], manifest.get("previous_revision")}
            for pin in (directory / "pins").glob("*.json"):
                try:
                    retained.add(json.loads(pin.read_text(encoding="utf-8"))["revision"])
                except FileNotFoundError:
                    continue
                except (ValueError, KeyError, TypeError) as exc:
                    raise contracts.ExpressionUnavailableError("Unreadable expression lease") from exc
            return _remove_old(directory, retained)


def _remove_old(directory, retained) -> int:
    if image_surfaces.linked(directory, directory / "revisions"):
        raise contracts.ExpressionUnavailableError("Linked expression revisions")
    root = (directory / "revisions").resolve()
    removed = 0
    before = time.time() - gc_contracts.GRACE_HOURS * 3600
    for revision in root.iterdir() if root.exists() else ():
        if revision.name in retained or not re.fullmatch(r"[0-9a-f]{32}", revision.name):
            continue
        if image_surfaces.linked(root, revision) or not revision.resolve().is_relative_to(root):
            raise contracts.ExpressionUnavailableError("Unsafe expression revision")
        if not revision.is_dir() or revision.stat().st_mtime >= before:
            continue
        shutil.rmtree(revision)
        removed += 1
    return removed
