"""Filesystem discovery for plugins.

Walks one or more plugin roots and parses each subdirectory's
`manifest.yaml`. Discovery is intentionally read-only — neither validation
nor instantiation happens here. The loader takes over from the
`DiscoveredPlugin` records returned by `discover`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.files.yaml_io import YamlError, load_yaml

MANIFEST_FILENAME = "manifest.yaml"
PLUGIN_ENTRY_FILENAME = "plugin.py"


@dataclass(frozen=True)
class DiscoveryError:
    """Recorded against a directory whose manifest couldn't be parsed."""

    plugin_dir: Path
    message: str


@dataclass(frozen=True)
class DiscoveredPlugin:
    """A directory that *looks* like a plugin (has a manifest.yaml).

    `raw_manifest` is whatever YAML produced; the loader is responsible for
    schema validation and the `id == directory name` check.
    """

    plugin_dir: Path
    manifest_path: Path
    entry_path: Path
    raw_manifest: dict[str, Any]
    source_root: Path
    bundled: bool


def discover(
    roots: list[Path],
    bundled_roots: list[Path] | None = None,
) -> tuple[list[DiscoveredPlugin], list[DiscoveryError]]:
    """Walk every root and yield discovered plugins and parse errors.

    A directory becomes a candidate if it contains a `manifest.yaml`. Each
    root is scanned only one level deep; nested plugin trees are not
    supported (spec 15 §What is a plugin). Roots that don't exist are
    silently skipped.

    Later roots are not allowed to shadow earlier ones — if the same plugin
    id appears twice, the later one is dropped with an error. Bundled roots
    are scanned *before* user roots so a user copy in `data/plugins/` wins.
    """
    discovered: list[DiscoveredPlugin] = []
    errors: list[DiscoveryError] = []
    seen_ids: dict[str, Path] = {}

    ordered: list[tuple[Path, bool]] = []
    for root in bundled_roots or ():
        ordered.append((root, True))
    for root in roots:
        ordered.append((root, False))

    for root, bundled in ordered:
        if not root.exists() or not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            manifest_path = entry / MANIFEST_FILENAME
            if not manifest_path.is_file():
                continue
            entry_path = entry / PLUGIN_ENTRY_FILENAME
            try:
                raw = load_yaml(manifest_path)
            except YamlError as exc:
                errors.append(DiscoveryError(plugin_dir=entry, message=str(exc)))
                continue
            if not isinstance(raw, dict):
                errors.append(
                    DiscoveryError(
                        plugin_dir=entry,
                        message="manifest.yaml must contain a top-level mapping",
                    )
                )
                continue
            plugin_id = raw.get("id")
            if isinstance(plugin_id, str) and plugin_id in seen_ids:
                errors.append(
                    DiscoveryError(
                        plugin_dir=entry,
                        message=(
                            f"duplicate plugin id '{plugin_id}'; "
                            f"already discovered at {seen_ids[plugin_id]}"
                        ),
                    )
                )
                continue
            if isinstance(plugin_id, str):
                seen_ids[plugin_id] = entry
            discovered.append(
                DiscoveredPlugin(
                    plugin_dir=entry,
                    manifest_path=manifest_path,
                    entry_path=entry_path,
                    raw_manifest=raw,
                    source_root=root,
                    bundled=bundled,
                )
            )
    return discovered, errors


__all__ = [
    "MANIFEST_FILENAME",
    "PLUGIN_ENTRY_FILENAME",
    "DiscoveredPlugin",
    "DiscoveryError",
    "discover",
]
