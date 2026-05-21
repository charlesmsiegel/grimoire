"""Library file writes: file + library_index stay synchronized; deltas log writes."""

from __future__ import annotations

import pytest

from grimoire.files import read_markdown
from grimoire.state_store import StateStore
from grimoire.state_store import store as _store_module
from grimoire.state_store.paths import library_path


async def test_write_library_file_creates_file_and_index_row(store: StateStore) -> None:
    result = await store.write_library_file(
        library_id="worlds/wod-london/characters/winifred",
        frontmatter={"name": "winifred", "tags": ["vampire", "elder"]},
        body="winifred is a Toreador elder. She is patient and watchful.",
        source="user",
    )

    assert result.path.exists()
    assert result.version == 1
    doc = read_markdown(result.path)
    assert doc.frontmatter["name"] == "winifred"

    row = await store.get_library_entity("worlds/wod-london/characters/winifred")
    assert row is not None
    assert row["name"] == "winifred"
    assert row["kind"] == "character"
    assert row["world_id"] == "wod-london"
    assert "vampire" in row["tags"]
    assert row["version"] == 1

    # Re-writing identical content keeps the same version.
    again = await store.write_library_file(
        library_id="worlds/wod-london/characters/winifred",
        frontmatter={"name": "winifred", "tags": ["vampire", "elder"]},
        body="winifred is a Toreador elder. She is patient and watchful.",
        source="user",
    )
    assert again.version == 1


async def test_write_library_file_bumps_version_on_real_change(
    store: StateStore,
) -> None:
    await store.write_library_file(
        library_id="worlds/wod-london/characters/winifred",
        frontmatter={"name": "winifred"},
        body="v1",
        source="user",
    )
    second = await store.write_library_file(
        library_id="worlds/wod-london/characters/winifred",
        frontmatter={"name": "winifred"},
        body="v2",
        source="user",
    )
    assert second.version == 2

    row = await store.get_library_entity("worlds/wod-london/characters/winifred")
    assert row["body"] == "v2"
    assert row["version"] == 2


async def test_write_library_emits_reversible_delta(store: StateStore) -> None:
    await store.write_library_file(
        library_id="worlds/wod-london/characters/winifred",
        frontmatter={"name": "winifred"},
        body="prose v1",
        source="user",
    )

    log = await store.get_delta_log()
    assert len(log) == 1
    assert log[0].kind == "library_file_write"
    assert log[0].target_scope == "library"
    assert log[0].before is None

    # A second write captures the previous content as `before`.
    result2 = await store.write_library_file(
        library_id="worlds/wod-london/characters/winifred",
        frontmatter={"name": "winifred", "tags": ["seer"]},
        body="prose v2",
        source="user",
    )
    log = await store.get_delta_log()
    assert len(log) == 2
    assert log[1].before is not None
    assert log[1].before["body"] == "prose v1"

    # Reverse the second delta — file content rolls back to v1.
    await store.reverse_delta(log[1].id)
    doc = read_markdown(result2.path)
    assert doc.body == "prose v1"
    row = await store.get_library_entity("worlds/wod-london/characters/winifred")
    assert row["body"] == "prose v1"


async def test_delete_library_file_is_reversible(store: StateStore) -> None:
    result = await store.write_library_file(
        library_id="worlds/wod-london/characters/winifred",
        frontmatter={"name": "winifred"},
        body="prose",
        source="user",
    )
    assert result.path.exists()

    await store.delete_library_file(
        library_id="worlds/wod-london/characters/winifred",
        source="user",
    )
    assert not result.path.exists()
    assert await store.get_library_entity("worlds/wod-london/characters/winifred") is None

    log = await store.get_delta_log()
    delete_delta = next(d for d in log if d.kind == "library_file_delete")
    await store.reverse_delta(delete_delta.id)

    assert result.path.exists()
    row = await store.get_library_entity("worlds/wod-london/characters/winifred")
    assert row is not None
    assert row["body"] == "prose"


async def test_write_library_file_restores_disk_when_sql_rolls_back_for_new_file(
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library_id = "worlds/wod-london/characters/winifred"
    target = library_path(store.data_root, library_id)
    assert not target.exists()

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated SQL failure")

    monkeypatch.setattr(_store_module, "insert_delta", _boom)

    with pytest.raises(RuntimeError, match="simulated SQL failure"):
        await store.write_library_file(
            library_id=library_id,
            frontmatter={"name": "winifred"},
            body="prose",
            source="user",
        )

    assert not target.exists()
    assert await store.get_library_entity(library_id) is None


async def test_write_library_file_restores_disk_when_sql_rolls_back_for_overwrite(
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library_id = "worlds/wod-london/characters/winifred"
    await store.write_library_file(
        library_id=library_id,
        frontmatter={"name": "winifred"},
        body="original",
        source="user",
    )
    target = library_path(store.data_root, library_id)
    original_bytes = target.read_bytes()

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated SQL failure")

    monkeypatch.setattr(_store_module, "insert_delta", _boom)

    with pytest.raises(RuntimeError, match="simulated SQL failure"):
        await store.write_library_file(
            library_id=library_id,
            frontmatter={"name": "winifred"},
            body="modified — must not survive",
            source="user",
        )

    assert target.read_bytes() == original_bytes
    row = await store.get_library_entity(library_id)
    assert row is not None
    assert row["body"] == "original"


async def test_delete_library_file_restores_disk_when_sql_rolls_back(
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library_id = "worlds/wod-london/characters/winifred"
    await store.write_library_file(
        library_id=library_id,
        frontmatter={"name": "winifred"},
        body="prose",
        source="user",
    )
    target = library_path(store.data_root, library_id)
    original_bytes = target.read_bytes()

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated SQL failure")

    monkeypatch.setattr(_store_module, "insert_delta", _boom)

    with pytest.raises(RuntimeError, match="simulated SQL failure"):
        await store.delete_library_file(library_id=library_id, source="user")

    assert target.exists()
    assert target.read_bytes() == original_bytes
    row = await store.get_library_entity(library_id)
    assert row is not None
    assert row["body"] == "prose"


async def test_image_preset_is_yaml_only(store: StateStore) -> None:
    result = await store.write_library_file(
        library_id="image-presets/oil-painting",
        frontmatter={
            "name": "Oil painting",
            "prompt_suffix": "in the style of an oil painting",
        },
        body="",
        source="user",
    )
    assert result.path.suffix == ".yaml"
    # No frontmatter fences — the YAML file IS the frontmatter.
    text = result.path.read_text(encoding="utf-8")
    assert "name: Oil painting" in text
