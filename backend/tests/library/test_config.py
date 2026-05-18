"""Tests for ``LibraryConfig`` YAML loading and service wiring (§2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.library import LibraryConfig, LibraryService
from grimoire.state_store import StateStore
from grimoire.types.composition import Composition, WorldRef


def test_default_config_matches_legacy_behavior() -> None:
    cfg = LibraryConfig()
    assert cfg.root is None
    assert cfg.watch is True
    assert cfg.scan_on_startup is True
    assert cfg.indexing.embed_on_index is True
    assert cfg.indexing.embedding_provider is None
    assert cfg.version_pinning.default == "pinned"
    assert cfg.version_pinning.snapshot_on_bind is True
    assert cfg.promotion.confirm_required is False
    assert cfg.default_track_latest is False


def test_from_yaml_full_block(tmp_path: Path) -> None:
    target = tmp_path / "library.yaml"
    target.write_text(
        """
library:
  root: /tmp/custom-lib
  watch: false
  scan_on_startup: false
  indexing:
    embed_on_index: false
    embedding_provider: bge-small
  version_pinning:
    default: track_latest
    snapshot_on_bind: false
  promotion:
    confirm_required: true
""",
        encoding="utf-8",
    )
    cfg = LibraryConfig.from_yaml(target)
    assert cfg.root == Path("/tmp/custom-lib")
    assert cfg.watch is False
    assert cfg.scan_on_startup is False
    assert cfg.indexing.embed_on_index is False
    assert cfg.indexing.embedding_provider == "bge-small"
    assert cfg.version_pinning.default == "track_latest"
    assert cfg.version_pinning.snapshot_on_bind is False
    assert cfg.promotion.confirm_required is True
    assert cfg.default_track_latest is True


def test_from_yaml_missing_file_returns_defaults(tmp_path: Path) -> None:
    cfg = LibraryConfig.from_yaml(tmp_path / "missing.yaml")
    assert cfg == LibraryConfig()


def test_from_yaml_rejects_unknown_pinning_default(tmp_path: Path) -> None:
    target = tmp_path / "library.yaml"
    target.write_text(
        "library:\n  version_pinning:\n    default: hybrid\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"version_pinning\.default"):
        LibraryConfig.from_yaml(target)


async def test_set_composition_honors_snapshot_on_bind_false(store: StateStore) -> None:
    """When the config disables snapshot-on-bind, a pinned WorldRef should not seed snapshots."""

    # Seed a world + one entity so a snapshot could exist if the policy fired.
    await store.write_library_file(
        library_id="worlds/wod/world",
        frontmatter={"id": "wod", "name": "WoD", "version": 1},
        body="",
        source="test",
    )
    await store.write_library_file(
        library_id="worlds/wod/characters/vivienne",
        frontmatter={"id": "vivienne", "name": "vivienne"},
        body="A vampire.",
        source="test",
    )
    await store.upsert_campaign(
        campaign_id="c1",
        name="Camp",
        description="",
        mechanics_module=None,
        style_guide_id=None,
        image_preset_id=None,
        inline_style_guide=None,
        content_boundaries=None,
        greeting_id=None,
        config=None,
    )

    cfg = LibraryConfig(
        version_pinning=LibraryConfig().version_pinning.__class__(
            default="pinned", snapshot_on_bind=False
        )
    )
    lib = LibraryService(store, config=cfg)
    await lib.set_composition(
        "c1",
        Composition(
            worlds=[
                WorldRef(world_id="wod", priority=0, include=None, track_latest=False),
            ],
            mechanics=None,
            style_guide_id=None,
            image_preset_id=None,
        ),
    )

    rows = await store.db.fetchall(
        "SELECT * FROM library_snapshots WHERE campaign_id = ?",
        ("c1",),
    )
    assert rows == []  # snapshot_on_bind=False suppressed seeding


async def test_set_composition_default_writes_snapshots(store: StateStore) -> None:
    await store.write_library_file(
        library_id="worlds/wod/world",
        frontmatter={"id": "wod", "name": "WoD", "version": 1},
        body="",
        source="test",
    )
    await store.write_library_file(
        library_id="worlds/wod/characters/vivienne",
        frontmatter={"id": "vivienne", "name": "vivienne"},
        body="A vampire.",
        source="test",
    )
    await store.upsert_campaign(
        campaign_id="c2",
        name="Camp",
        description="",
        mechanics_module=None,
        style_guide_id=None,
        image_preset_id=None,
        inline_style_guide=None,
        content_boundaries=None,
        greeting_id=None,
        config=None,
    )

    lib = LibraryService(store)  # default config: snapshot_on_bind=True
    await lib.set_composition(
        "c2",
        Composition(
            worlds=[
                WorldRef(world_id="wod", priority=0, include=None, track_latest=False),
            ],
            mechanics=None,
            style_guide_id=None,
            image_preset_id=None,
        ),
    )
    rows = await store.db.fetchall(
        "SELECT library_id FROM library_snapshots WHERE campaign_id = ?",
        ("c2",),
    )
    assert {r["library_id"] for r in rows} >= {"worlds/wod/characters/vivienne"}
