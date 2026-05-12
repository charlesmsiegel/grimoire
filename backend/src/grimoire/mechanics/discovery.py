"""Filesystem discovery for mechanics modules.

Walks ``data/mechanics/`` and parses each subdirectory's ``manifest.yaml``.
Discovery is read-only — validation and instantiation happen in the loader.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.files.yaml_io import YamlError, load_yaml

MANIFEST_FILENAME = "manifest.yaml"
MODULE_ENTRY_FILENAME = "mechanics.py"


@dataclass(frozen=True)
class DiscoveryError:
    """Recorded against a directory whose manifest couldn't be parsed."""

    module_dir: Path
    message: str


@dataclass(frozen=True)
class DiscoveredModule:
    """A directory that *looks* like a mechanics module (has a manifest.yaml).

    ``raw_manifest`` is whatever YAML produced; the loader is responsible
    for schema validation and the ``id == directory name`` check.
    """

    module_dir: Path
    manifest_path: Path
    entry_path: Path
    raw_manifest: dict[str, Any]
    source_root: Path


def discover(
    roots: list[Path],
) -> tuple[list[DiscoveredModule], list[DiscoveryError]]:
    """Walk every root and yield discovered modules and parse errors.

    A directory becomes a candidate if it contains a ``manifest.yaml``.
    Each root is scanned one level deep; nested module trees are not
    supported. Roots that don't exist are silently skipped.

    Duplicate ids across roots are dropped with an error — the first
    occurrence wins.
    """
    discovered: list[DiscoveredModule] = []
    errors: list[DiscoveryError] = []
    seen_ids: dict[str, Path] = {}

    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            manifest_path = entry / MANIFEST_FILENAME
            if not manifest_path.is_file():
                continue
            entry_path = entry / MODULE_ENTRY_FILENAME
            try:
                raw = load_yaml(manifest_path)
            except YamlError as exc:
                errors.append(DiscoveryError(module_dir=entry, message=str(exc)))
                continue
            if not isinstance(raw, dict):
                errors.append(
                    DiscoveryError(
                        module_dir=entry,
                        message="manifest.yaml must contain a top-level mapping",
                    )
                )
                continue
            module_id = raw.get("id")
            if isinstance(module_id, str) and module_id in seen_ids:
                errors.append(
                    DiscoveryError(
                        module_dir=entry,
                        message=(
                            f"duplicate mechanics module id '{module_id}'; "
                            f"already discovered at {seen_ids[module_id]}"
                        ),
                    )
                )
                continue
            if isinstance(module_id, str):
                seen_ids[module_id] = entry
            discovered.append(
                DiscoveredModule(
                    module_dir=entry,
                    manifest_path=manifest_path,
                    entry_path=entry_path,
                    raw_manifest=raw,
                    source_root=root,
                )
            )
    return discovered, errors


__all__ = [
    "MANIFEST_FILENAME",
    "MODULE_ENTRY_FILENAME",
    "DiscoveredModule",
    "DiscoveryError",
    "discover",
]
