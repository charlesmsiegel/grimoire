"""Top-level imagegen YAML config (§7)."""

from __future__ import annotations

from pathlib import Path

from grimoire.imagegen.config import ImageGenConfig


def test_from_yaml_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    cfg = ImageGenConfig.from_yaml(tmp_path / "nope.yaml")
    assert cfg.default_backend is None
    assert cfg.queue_max_concurrent_per_backend == 1
    assert cfg.caching_enabled is True
    assert cfg.thumbnails_size == (256, 256)
    assert cfg.thumbnails_format == "JPEG"
    assert cfg.thumbnails_quality == 85
    assert cfg.storage_image_format == "PNG"


def test_from_yaml_parses_top_level_keys(tmp_path: Path) -> None:
    path = tmp_path / "imagegen.yaml"
    path.write_text(
        "default_backend: a1111\n"
        "queue:\n"
        "  max_concurrent_per_backend: 2\n"
        "  persist_pending: true\n"
        "caching:\n"
        "  enabled: false\n"
        "thumbnails:\n"
        "  size: [128, 128]\n"
        "  format: PNG\n"
        "  quality: 95\n"
        "storage:\n"
        "  image_format: WEBP\n",
        encoding="utf-8",
    )
    cfg = ImageGenConfig.from_yaml(path)
    assert cfg.default_backend == "a1111"
    assert cfg.queue_max_concurrent_per_backend == 2
    assert cfg.queue_persist_pending is True
    assert cfg.caching_enabled is False
    assert cfg.thumbnails_size == (128, 128)
    assert cfg.thumbnails_format == "PNG"
    assert cfg.thumbnails_quality == 95
    assert cfg.storage_image_format == "WEBP"


def test_from_yaml_ignores_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "imagegen.yaml"
    path.write_text("default_backend: x\nzzz: 'unknown'\n", encoding="utf-8")
    cfg = ImageGenConfig.from_yaml(path)
    assert cfg.default_backend == "x"
