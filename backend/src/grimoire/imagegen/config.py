"""Top-level ImageGen YAML config loader (spec 12 §Configuration).

Only the knobs that actually flow through to the service are surfaced here.
Plugin-specific knobs (e.g. ``diffusers.active_model``) live in the plugin's
``manifest.yaml`` ``config_schema`` and are not duplicated here.

The public shape is flat (``config.caching_enabled``); the YAML is nested
(``caching.enabled``). ``from_yaml`` does the pure key remap, then pydantic
owns all coercion, defaults, and validation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from grimoire.files import load_yaml


class ImageGenConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    default_backend: str | None = None
    queue_max_concurrent_per_backend: int = 1
    queue_persist_pending: bool = False
    caching_enabled: bool = True
    caching_cache_dir: str | None = None
    # Upper bound on the in-memory (request -> result) LRU. Each entry can
    # hold image bytes (inline results), so an unbounded cache OOMs a
    # long-running server. Set to 0 to disable bounding (not recommended).
    caching_max_entries: int = 256
    thumbnails_size: tuple[int, int] = (256, 256)
    thumbnails_format: str = "JPEG"
    thumbnails_quality: int = 85
    storage_image_format: str = "PNG"

    @field_validator("thumbnails_format", "storage_image_format")
    @classmethod
    def _normalize_format(cls, value: str) -> str:
        return value.upper()

    @classmethod
    def from_yaml(cls, path: Path) -> ImageGenConfig:
        """Load top-level config; return defaults if the file is missing."""
        if not path.exists():
            return cls()
        raw = load_yaml(path) or {}
        if not isinstance(raw, dict):
            return cls()
        return cls.model_validate(_flatten(raw))


def _flatten(raw: dict[str, Any]) -> dict[str, Any]:
    """Remap the nested YAML sections onto the flat field names.

    Only keys actually present are emitted, so anything omitted falls back to
    the field default. Empty strings collapse to "omitted" (matching the old
    ``... or None`` handling for the optional string knobs).
    """
    queue = raw.get("queue") or {}
    caching = raw.get("caching") or {}
    thumbs = raw.get("thumbnails") or {}
    storage = raw.get("storage") or {}
    candidates = {
        "default_backend": raw.get("default_backend"),
        "queue_max_concurrent_per_backend": queue.get("max_concurrent_per_backend"),
        "queue_persist_pending": queue.get("persist_pending"),
        "caching_enabled": caching.get("enabled"),
        "caching_cache_dir": caching.get("cache_dir"),
        "caching_max_entries": caching.get("max_entries"),
        "thumbnails_size": thumbs.get("size"),
        "thumbnails_format": thumbs.get("format"),
        "thumbnails_quality": thumbs.get("quality"),
        "storage_image_format": storage.get("image_format"),
    }
    return {key: value for key, value in candidates.items() if value not in (None, "")}
