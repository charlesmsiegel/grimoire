"""Device identity, per-pair archive and pending-marker documents (``.sync/``), parsed strictly.

A document that is not exactly valid is *ignored and reported*, never partially trusted. The
archive and pending marker carry a ``check`` digest of their own content, so a hand edit or a
truncation is detected even when the JSON still parses.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from grimoire.core.identity import validate_segment
from grimoire.security import InvalidResourcePath, ResourcePath
from grimoire.store.jsonfmt import JsonError, is_etag, parse_strict, render
from grimoire.store.layout import classify

from .model import SYNCED_KINDS

DEVICE_FORMAT = "grimoire.sync.device/1"
ARCHIVE_FORMAT = "grimoire.sync.archive/1"
PENDING_FORMAT = "grimoire.sync.pending/1"
DEVICE_FILE = "device.json"


def archive_name(peer: str) -> str:
    return f"peers/{peer}/archive.json"


def pending_name(peer: str) -> str:
    return f"peers/{peer}/pending.json"


def check_device_id(device: object) -> str:
    """A device ID is one lowercase identity segment that is also a portable path segment."""
    try:
        validate_segment(device, "device")
        ResourcePath((device,))  # type: ignore[arg-type]
    except (ValueError, InvalidResourcePath) as error:
        raise ValueError(f"not a device id: {device!r} ({error})") from None
    return device  # type: ignore[return-value]


def render_device(device: str) -> bytes:
    return render({"format": DEVICE_FORMAT, "device": check_device_id(device)})


def parse_device(data: bytes | None) -> str | None:
    """The device ID, or ``None`` when absent or damaged."""
    if data is None:
        return None
    try:
        document = parse_strict(data)
    except JsonError:
        return None
    if set(document) != {"format", "device"} or document.get("format") != DEVICE_FORMAT:
        return None
    try:
        return check_device_id(document["device"])
    except ValueError:
        return None


def _digest(devices: tuple[str, str], entries: Mapping[str, object]) -> str:
    return "sha256:" + hashlib.sha256(render({"devices": list(devices), "entries": dict(entries)})).hexdigest()


def syncable(path: object) -> str | None:
    """The record kind sync owns at ``path``, or ``None``: the one definition of the synced set.

    The extension point for later families (018 images, 021 accepted events and wrap-ups, 035 scenes,
    ledgers): add the kind to
    ``model.SYNCED_KINDS`` and its decision overrides. Derived reverse indexes (107's navigation
    index) are never synchronized.
    """
    if type(path) is not str:
        return None
    found = classify(tuple(path.split("/")))
    return found[0] if found is not None and found[0] in SYNCED_KINDS else None


_syncable = syncable


@dataclass(frozen=True)
class ArchiveView:
    """``missing`` (no file), ``ignored`` (a file that is not a valid archive of this pair) or ``valid``."""

    state: str
    entries: Mapping[str, tuple[str, str]] = field(default_factory=dict)  # path -> (agreed|deleted, etag)
    reason: str | None = None
    data: bytes | None = None


@dataclass(frozen=True)
class PendingView:
    state: str
    entries: Mapping[str, Mapping[str, object]] = field(default_factory=dict)  # path -> {from,to}|{uncertain}
    reason: str | None = None
    data: bytes | None = None


def _ignored(cls, reason: str, data: bytes):  # type: ignore[no-untyped-def]
    return cls("ignored", MappingProxyType({}), reason, data)


def _envelope(data: bytes, expected_format: str, devices: tuple[str, str]) -> tuple[Mapping[str, object] | None, str]:
    try:
        document = parse_strict(data)
    except JsonError as error:
        return None, f"not strict JSON: {error}"
    if set(document) != {"format", "devices", "entries", "check"}:
        return None, "unexpected fields"
    if document["format"] != expected_format:
        return None, f"format {document['format']!r} is not {expected_format!r}"
    if not isinstance(document["devices"], tuple) or document["devices"] != tuple(devices):
        return None, "it belongs to another pair of devices"
    entries = document["entries"]
    if not isinstance(entries, Mapping):
        return None, "entries must be an object"
    if document["check"] != _digest(devices, entries):
        return None, "its check digest does not match its content"
    return entries, ""


def parse_archive(data: bytes | None, devices: tuple[str, str]) -> ArchiveView:
    if data is None:
        return ArchiveView("missing")
    entries, reason = _envelope(data, ARCHIVE_FORMAT, devices)
    if entries is None:
        return _ignored(ArchiveView, reason, data)
    parsed: dict[str, tuple[str, str]] = {}
    for path, entry in entries.items():
        if not _syncable(path) or not isinstance(entry, Mapping) or len(entry) != 1:
            return _ignored(ArchiveView, f"bad entry for {path!r}", data)
        (kind, value), = entry.items()
        if kind not in ("agreed", "deleted") or not is_etag(value):
            return _ignored(ArchiveView, f"bad entry for {path!r}", data)
        parsed[path] = (kind, value)
    return ArchiveView("valid", MappingProxyType(parsed), None, data)


def parse_pending(data: bytes | None, devices: tuple[str, str]) -> PendingView:
    if data is None:
        return PendingView("missing")
    entries, reason = _envelope(data, PENDING_FORMAT, devices)
    if entries is None:
        return _ignored(PendingView, reason, data)
    parsed: dict[str, Mapping[str, object]] = {}
    for path, entry in entries.items():
        valid = isinstance(entry, Mapping) and _syncable(path) and (
            dict(entry) == {"uncertain": True}
            or (set(entry) == {"from", "to"} and is_etag(entry["to"])
                and (entry["from"] is None or is_etag(entry["from"]))))
        if not valid:
            return _ignored(PendingView, f"bad entry for {path!r}", data)
        parsed[path] = MappingProxyType(dict(entry))
    return PendingView("valid", MappingProxyType(parsed), None, data)


def render_archive(devices: tuple[str, str], entries: Mapping[str, tuple[str, str]]) -> bytes:
    body = {path: {kind: value} for path, (kind, value) in sorted(entries.items())}
    return render({"format": ARCHIVE_FORMAT, "devices": list(devices), "entries": body,
                   "check": _digest(devices, body)})


def render_pending(devices: tuple[str, str], entries: Mapping[str, Mapping[str, object]]) -> bytes:
    body = {path: dict(entry) for path, entry in sorted(entries.items())}
    return render({"format": PENDING_FORMAT, "devices": list(devices), "entries": body,
                   "check": _digest(devices, body)})
