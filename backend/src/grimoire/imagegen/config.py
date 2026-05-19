"""Top-level ImageGen YAML config loader (spec 12 §Configuration).

Only the knobs that actually flow through to the service are surfaced here.
Plugin-specific knobs (e.g. ``diffusers.active_model``) live in the plugin's
``manifest.yaml`` ``config_schema`` and are not duplicated here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.files import load_yaml


@dataclass(frozen=True, slots=True)
class ImageGenConfig:
    default_backend: str | None = None
    queue_max_concurrent_per_backend: int = 1
    queue_persist_pending: bool = False
    caching_enabled: bool = True
    caching_cache_dir: str | None = None
    thumbnails_size: tuple[int, int] = (256, 256)
    thumbnails_format: str = "JPEG"
    thumbnails_quality: int = 85
    storage_image_format: str = "PNG"

    @classmethod
    def from_yaml(cls, path: Path) -> ImageGenConfig:
        """Load top-level config; return defaults if the file is missing."""
        if not path.exists():
            return cls()
        raw = load_yaml(path) or {}
        if not isinstance(raw, dict):
            return cls()
        return cls._from_mapping(raw)

    @classmethod
    def _from_mapping(cls, raw: dict[str, Any]) -> ImageGenConfig:
        queue = raw.get("queue") or {}
        caching = raw.get("caching") or {}
        thumbs = raw.get("thumbnails") or {}
        storage = raw.get("storage") or {}
        size_raw = thumbs.get("size") or (256, 256)
        if isinstance(size_raw, list | tuple) and len(size_raw) == 2:
            size = (int(size_raw[0]), int(size_raw[1]))
        else:
            size = (256, 256)
        return cls(
            default_backend=raw.get("default_backend") or None,
            queue_max_concurrent_per_backend=int(queue.get("max_concurrent_per_backend") or 1),
            queue_persist_pending=bool(queue.get("persist_pending", False)),
            caching_enabled=bool(caching.get("enabled", True)),
            caching_cache_dir=caching.get("cache_dir") or None,
            thumbnails_size=size,
            thumbnails_format=str(thumbs.get("format") or "JPEG").upper(),
            thumbnails_quality=int(thumbs.get("quality") or 85),
            storage_image_format=str(storage.get("image_format") or "PNG").upper(),
        )
