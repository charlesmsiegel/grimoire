"""Read-only inventory of a frozen Grimoire 0.2.1 library (V1 → V2 migration, step one).

`scan` walks an explicitly chosen V1 source without writing to it, following
links or calling any model, and returns a deterministic manifest: what the
library holds, the V2 identities it could map to, its pictures and their
placements, and everything that is unsupported, ambiguous or unresolved. It is
a plan for review. Nothing is imported; the importer is a later sprint.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core.identity import validate_segment
from ..library import check_migration_paths, resolve_library
from . import _campaigns, _identity, _images, _records, _report
from ._context import Context
from ._fs import NotADirectory, Options, Snapshot, SourceChanged
from ._report import SCHEMA, render_json, render_summary

EXIT_READY, EXIT_USAGE, EXIT_ISOLATION, EXIT_BLOCKED, EXIT_CHANGED = 0, 2, 3, 4, 5


class NotAV1Library(ValueError):
    """The source has neither ``worlds/`` nor ``campaigns/``: not a 0.2.1 library."""


@dataclass(frozen=True)
class ScanResult:
    manifest: dict

    @property
    def status(self) -> str:
        return self.manifest["status"]

    @property
    def exit_code(self) -> int:
        return EXIT_READY if self.status == "ready_for_review" else EXIT_BLOCKED


def migration_roots(source, destination=None, *, environ=None, home=None) -> tuple[Path, Path]:
    """The checked (source, destination) pair; the destination defaults to the V2 library."""
    if destination is None:
        destination = resolve_library(environ=environ, home=home).path
    return check_migration_paths(source, destination, environ=environ, home=home)


def _given_is_link(source, home: Path | None) -> bool:
    """Whether the final component the user named was a link (its target was scanned)."""
    raw = os.fspath(source)
    if raw == "~" or raw[:2] in ("~/", "~" + os.sep):
        raw = os.path.join(home if home is not None else Path.home(), raw[2:])
    return os.path.islink(raw.rstrip("/" + os.sep) or raw)


def scan(source, *, archive_id: str, destination=None, environ=None, home: Path | None = None,
         _options: Options | None = None, _roots: tuple[Path, Path] | None = None) -> ScanResult:
    """Inventory the V1 library at `source`. Writes nothing; raises on unusable input.

    `_roots` is the pair a caller already checked with `migration_roots` for
    these same arguments, so its report guard and the scan agree on them."""
    validate_segment(archive_id, "archive_id")
    src, dst = _roots or migration_roots(source, destination, environ=environ, home=home)
    try:
        snap = Snapshot.take(str(src), _options)
    except NotADirectory:
        raise NotAV1Library(f"{src} is not a directory") from None
    try:
        if not any(snap.is_dir(name) or name in snap.entries and snap.entries[name].disposition == "refused"
                   for name in ("worlds", "campaigns")):
            raise NotAV1Library(f"{src} has no worlds/ or campaigns/ directory")
        manifest = _interpret(Context(snap, archive_id))
        snap.verify_unchanged()
    finally:
        snap.close()
    manifest = {**_report.header(archive_id, manifest.pop("_markers"), src,
                                 _given_is_link(source, home), dst), **manifest}
    return ScanResult(manifest)


def _interpret(ctx: Context) -> dict:
    lib = _records.Library()
    markers = _records.read_config(ctx, lib)
    worlds = _records.read_worlds(ctx, lib)
    _records.read_flat_records(ctx, lib)
    _records.read_actors(ctx, lib)
    campaigns = _campaigns.read_campaigns(ctx, lib)
    scenes = _campaigns.scene_rows(ctx, lib, campaigns)
    _campaigns.count_unsupported(ctx)
    views = {cid: (c.world if c.inherits else None, frozenset(c.deleted | c.detached))
             for cid, c in campaigns.items()}
    records = _records.record_rows(ctx, lib, views)
    campaign_rows = _campaigns.campaign_rows(ctx, lib, campaigns)

    pictures = _images.Placements()
    _images.read_store(ctx, pictures)
    _images.read_placements(ctx, pictures)
    layers = _images.expression_layers(ctx, pictures)
    _images.expression_layering(layers, campaigns, lib)
    images = {
        "objects": sorted(pictures.object_rows, key=lambda r: r["path"]),
        "blobs": [pictures.blobs[k] for k in sorted(pictures.blobs)],
        "legacy_images": _images.legacy_rows(ctx, pictures),
        "expression_layers": [layers[k] for k in sorted(layers, key=repr)],
        "collections": _images.collection_rows(ctx, pictures),
        "campaign_layering": _images.layering_rows(ctx, lib, pictures, campaigns),
        "references": _images.reference_rows(ctx, lib, pictures, campaigns),
    }

    legacy = _identity.LegacyIds(ctx)
    identity = _identity.identity_section(ctx, lib, campaigns, legacy)
    for row in pictures.rows:
        row["legacy_id"] = legacy(row["path"])
    images["placements"] = sorted(pictures.rows, key=lambda r: r["path"])
    images["referenced_objects"] = [{"id": i, "known": i in pictures.objects}
                                    for i in sorted(pictures.referenced)]
    entries = _report.entry_rows(ctx, legacy)
    _report.path_findings(ctx)
    manifest = {
        "_markers": markers,
        "source_fingerprint": _report.fingerprint(entries),
        "entries": entries,
        "worlds": worlds,
        "records": records,
        "actors": _records.actor_rows(lib),
        "campaigns": campaign_rows,
        "scenes": scenes,
        "images": images,
        "identity_candidates": identity,
    }
    return _report.finalize(manifest, ctx)


__all__ = [
    "EXIT_BLOCKED", "EXIT_CHANGED", "EXIT_ISOLATION", "EXIT_READY", "EXIT_USAGE", "NotAV1Library",
    "Options", "SCHEMA", "ScanResult", "SourceChanged", "migration_roots", "render_json",
    "render_summary", "scan",
]
