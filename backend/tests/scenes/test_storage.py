from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from grimoire.scenes.storage import (
    next_ordinal,
    parse_body,
    read_sidecar,
    render_body,
    scene_basename,
    scene_files_transaction,
    scene_paths,
    slugify,
    write_sidecar,
)
from grimoire.scenes.types import AuthorKind, Post, Scene


def _make_post(order: int, kind: AuthorKind, body: str, **kwargs) -> Post:
    return Post(
        id=f"p{order}",
        scene_id="s1",
        order_in_scene=order,
        author_kind=kind,
        body=body,
        is_player=kwargs.pop("is_player", False),
        created_at=datetime(2024, 10, 31, 22, order, 0),
        turn_id=f"t{order}",
        **kwargs,
    )


def test_slugify_handles_unicode_and_punctuation() -> None:
    assert slugify("Elysium Opening") == "elysium-opening"
    assert slugify("Café — Round 2!") == "cafe-round-2"
    assert slugify("---") == "scene"


def test_scene_basename_zero_pads_ordinal() -> None:
    assert scene_basename(7, "opening") == "0007-opening"


def test_next_ordinal_returns_one_for_empty(tmp_path: Path) -> None:
    assert next_ordinal(tmp_path, "campaign-a") == 1


def test_next_ordinal_skips_gaps(tmp_path: Path) -> None:
    directory = tmp_path / "campaigns" / "campaign-a" / "scenes"
    directory.mkdir(parents=True)
    (directory / "0001-foo.yaml").write_text("")
    (directory / "0005-bar.yaml").write_text("")
    assert next_ordinal(tmp_path, "campaign-a") == 6


def test_sidecar_roundtrip(tmp_path: Path) -> None:
    scene = Scene(
        id="campaign-a:0001-elysium-opening",
        campaign_id="campaign-a",
        ordinal=1,
        slug="elysium-opening",
        title="Elysium Opening",
        location_ref="elysium",
        in_game_start=datetime(2024, 10, 31, 22, 0, 0),
        present_pc_refs=["alistair"],
        present_character_refs=["alistair", "prince-of-london"],
        tags=["intro"],
    )
    md_path, yaml_path = scene_paths(tmp_path, scene)
    write_sidecar(yaml_path, scene)
    loaded = read_sidecar(yaml_path)
    assert loaded.id == scene.id
    assert loaded.present_pc_refs == ["alistair"]
    assert loaded.in_game_start == datetime(2024, 10, 31, 22, 0, 0)
    assert loaded.tags == ["intro"]
    assert md_path.parent.exists()


def test_render_and_parse_body_roundtrip() -> None:
    posts = [
        _make_post(1, AuthorKind.NARRATOR, "The tower is candle-lit."),
        _make_post(
            2,
            AuthorKind.PC,
            "I incline my head.",
            author_pc_ref="alistair",
            is_player=True,
        ),
        _make_post(
            3,
            AuthorKind.NPC,
            "The Prince smiles thinly.",
            author_npc_ref="prince-of-london",
        ),
    ]
    body = render_body(posts)
    assert "## Post 1 — narrator" in body
    assert "## Post 2 — pc:alistair" in body
    assert "## Post 3 — npc:prince-of-london" in body

    parsed = parse_body(body, "s1")
    assert [(o, k.value, pc, npc) for (o, k, pc, npc, _) in parsed] == [
        (1, "narrator", None, None),
        (2, "pc", "alistair", None),
        (3, "npc", None, "prince-of-london"),
    ]
    assert parsed[0][4] == "The tower is candle-lit."


def test_parse_body_ignores_non_post_headings() -> None:
    text = "# Title\n\n## Some other heading\n\nbody\n\n## Post 1 — narrator\n\nhello\n"
    parsed = parse_body(text, "s1")
    assert len(parsed) == 1
    assert parsed[0][0] == 1
    assert parsed[0][4] == "hello"


def test_render_body_trailing_newline() -> None:
    posts = [_make_post(1, AuthorKind.NARRATOR, "Just one post.")]
    body = render_body(posts)
    assert body.endswith("\n")


def test_scene_files_transaction_restores_both_files_on_failure(tmp_path: Path) -> None:
    md = tmp_path / "0001-scene.md"
    yml = tmp_path / "0001-scene.yaml"
    md.write_text("old md", encoding="utf-8")
    yml.write_text("old yaml", encoding="utf-8")
    with pytest.raises(RuntimeError, match="boom"), scene_files_transaction(md, yml):
        md.write_text("new md", encoding="utf-8")
        yml.write_text("new yaml", encoding="utf-8")
        raise RuntimeError("boom")
    assert md.read_text(encoding="utf-8") == "old md"
    assert yml.read_text(encoding="utf-8") == "old yaml"


def test_scene_files_transaction_unlinks_files_created_inside(tmp_path: Path) -> None:
    md = tmp_path / "0001-scene.md"
    yml = tmp_path / "0001-scene.yaml"
    with pytest.raises(RuntimeError), scene_files_transaction(md, yml):
        md.write_text("new md", encoding="utf-8")
        raise RuntimeError("boom")
    assert not md.exists()
    assert not yml.exists()


def test_scene_files_transaction_keeps_changes_on_success(tmp_path: Path) -> None:
    md = tmp_path / "0001-scene.md"
    yml = tmp_path / "0001-scene.yaml"
    md.write_text("old md", encoding="utf-8")
    with scene_files_transaction(md, yml):
        md.write_text("new md", encoding="utf-8")
        yml.write_text("new yaml", encoding="utf-8")
    assert md.read_text(encoding="utf-8") == "new md"
    assert yml.read_text(encoding="utf-8") == "new yaml"
