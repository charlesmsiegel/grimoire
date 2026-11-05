"""Read-only inventory of a frozen Grimoire 0.2.1 library (V1 → V2 migration, step one).

`scan` walks an explicitly chosen V1 source without writing to it, following
links or calling any model, and returns a deterministic manifest: what the
library holds, the V2 identities it could map to, its pictures and their
placements, and everything that is unsupported, ambiguous or unresolved. It is
a plan for review. Nothing is imported here: `open_verified` hands the reviewed
manifest's source to the importer (`grimoire.import_v1`, sprint 026) only after
proving the source still holds exactly what was reviewed.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..core.identity import validate_segment
from ..library import check_migration_paths, resolve_library
from . import _campaigns, _identity, _images, _records, _report
from ._context import Context
from ._fs import NotADirectory, Options, Snapshot, SourceChanged, TooLarge
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


#: Header fields that describe where and how a scan ran, not what the source holds.
_RUN_FIELDS = ("roots", "unicode_version")


class ManifestMismatch(ValueError):
    """The reviewed manifest does not describe the source as it is now; ``reason`` says how.

    ``not_an_inventory`` (not a ``grimoire.v1-inventory/1`` manifest), ``blocked`` (the scan says
    the source must be fixed and rescanned), ``destination_mismatch`` (reviewed for another V2
    destination), ``source_changed`` (an entry or the fingerprint differs) and ``manifest_changed``
    (the source is the same but the manifest no longer says what a scan of it says: edited, or
    made by another scanner version). Every one means: rescan and review again.
    """

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


class VerifiedSource:
    """A V1 source proven to hold exactly what a reviewed manifest describes, open for reading.

    ``read(path)`` returns the bytes of one hashed file, re-verified against the walk's digest.
    ``close()`` walks again and raises ``SourceChanged`` if anything moved meanwhile; use it as a
    context manager. Nothing here writes, follows a link or calls a model.
    """

    def __init__(self, snap: Snapshot, manifest: Mapping) -> None:
        self._snap = snap
        self._open = True
        self.manifest = manifest

    @property
    def archive_id(self) -> str:
        return self.manifest["archive_id"]

    def read(self, path: str) -> bytes:
        """The verified bytes of ``path``; ``KeyError`` if it was not walked, ``TooLarge`` past the cap."""
        if not self._open:
            raise ValueError("the verified source is closed")
        return self._snap.read(path)

    def close(self, *, verify: bool = True) -> None:
        """Close the source; with ``verify`` (the default) first prove nothing changed since it opened."""
        if not self._open:
            return
        self._open = False
        try:
            if verify:
                self._snap.verify_unchanged()
        finally:
            self._snap.close()

    def __enter__(self) -> VerifiedSource:
        return self

    def __exit__(self, kind, value, traceback) -> None:  # type: ignore[no-untyped-def]
        self.close(verify=kind is None)


def open_verified(source, manifest: Mapping, *, destination, environ=None, home: Path | None = None,
                  _options: Options | None = None) -> VerifiedSource:
    """Open ``source`` for an import of ``manifest`` into ``destination``, or refuse.

    The importer's obligations (MIGRATION_INVENTORY): a ``blocked`` manifest is refused; the
    destination must pass ``check_migration_paths`` and be the one the manifest was reviewed for;
    every entry's size and sha256 and the ``source_fingerprint`` are re-verified against the
    source. The manifest is then re-derived from the verified bytes by the same deterministic
    interpreter and must match it field for field (where and when the scan ran aside), so an
    importer may trust every row it reads. This is a check of the reviewed plan, never a second
    plan: nothing derived here is returned. Raises ``ManifestMismatch``, ``NotAV1Library`` or
    ``LibraryPathError``.
    """
    if not isinstance(manifest, Mapping) or manifest.get("schema") != SCHEMA:
        raise ManifestMismatch(f"not a {SCHEMA} manifest", "not_an_inventory")
    archive_id = manifest.get("archive_id")
    try:
        validate_segment(archive_id, "archive_id")
    except ValueError as error:
        raise ManifestMismatch(str(error), "not_an_inventory") from None
    if manifest.get("status") == "blocked":
        raise ManifestMismatch("the manifest is blocked: fix the source and rescan", "blocked")
    src, dst = migration_roots(source, destination, environ=environ, home=home)
    roots = manifest.get("roots")
    if not isinstance(roots, Mapping) or roots.get("destination") != str(dst):
        raise ManifestMismatch(f"the manifest was reviewed for another destination, not {dst}",
                               "destination_mismatch")
    try:
        snap = Snapshot.take(str(src), _options)
    except NotADirectory:
        raise NotAV1Library(f"{src} is not a directory") from None
    try:
        rows = [{"path": e.path, "disposition": e.disposition, "size": e.size, "sha256": e.sha256}
                for e in (snap.entries[path] for path in sorted(snap.entries))]
        listed = manifest.get("entries")
        reviewed = ([{key: row.get(key) for key in ("path", "disposition", "size", "sha256")}
                     for row in listed] if isinstance(listed, list)
                    and all(isinstance(row, Mapping) for row in listed) else None)
        if (reviewed != rows or _report.fingerprint(rows) != manifest.get("source_fingerprint")):
            raise ManifestMismatch("the source no longer holds what the manifest reviewed", "source_changed")
        body = _interpret(Context(snap, archive_id))
        markers = body.pop("_markers")
        derived = {**_report.header(archive_id, markers, src, False, dst), **body}
        expected = render_json({key: value for key, value in derived.items() if key not in _RUN_FIELDS})
        try:  # compared as rendered text, so 40.0 or true never passes for 40 or 1
            given = render_json({key: value for key, value in manifest.items() if key not in _RUN_FIELDS})
        except (TypeError, ValueError):
            given = None
        if given != expected:
            raise ManifestMismatch("the manifest does not say what a scan of this source says",
                                   "manifest_changed")
    except BaseException:
        snap.close()
        raise
    return VerifiedSource(snap, manifest)


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
    "EXIT_BLOCKED", "EXIT_CHANGED", "EXIT_ISOLATION", "EXIT_READY", "EXIT_USAGE", "ManifestMismatch",
    "NotAV1Library", "Options", "SCHEMA", "ScanResult", "SourceChanged", "TooLarge", "VerifiedSource",
    "migration_roots", "open_verified", "render_json", "render_summary", "scan",
]
