from __future__ import annotations

from datetime import datetime

from grimoire.export import build_snapshot
from grimoire.scenes.types import AuthorKind
from grimoire.types.export import ExportOptions, ExportSelection

from .conftest import (
    make_character,
    make_commitment,
    make_fact,
    make_post,
    make_scene,
    make_sources,
)


async def test_snapshot_filters_by_scene_id_and_date_range() -> None:
    s1 = make_scene(
        ordinal=1, slug="opening", in_game_start=datetime(2024, 10, 31, 22), post_count=1
    )
    s2 = make_scene(
        ordinal=2,
        slug="rising-action",
        title="Rising Action",
        in_game_start=datetime(2024, 11, 1, 10),
        post_count=1,
    )
    s3 = make_scene(
        ordinal=3,
        slug="climax",
        title="Climax",
        in_game_start=datetime(2024, 11, 2, 23),
        post_count=1,
    )

    sources = make_sources(
        scenes=[s1, s2, s3],
        posts={
            s1.id: [make_post(s1.id, 1, "A small overture.")],
            s2.id: [make_post(s2.id, 1, "A walk in daylight.")],
            s3.id: [make_post(s3.id, 1, "Curtain.")],
        },
    )

    selection = ExportSelection(
        branch_id="main",
        scene_ids=[s2.id, s3.id],
        include_appendices=[],
    )
    snap = await build_snapshot("campaign-a", selection, ExportOptions(title="x"), sources)
    titles = [p.scene.title for p in snap.scenes]
    assert titles == [s2.title, s3.title]


async def test_snapshot_collects_appendix_data() -> None:
    scene = make_scene(post_count=1)
    sources = make_sources(
        scenes=[scene],
        posts={scene.id: [make_post(scene.id, 1, "Body.")]},
        characters=[make_character()],
        facts=[make_fact()],
        commitments=[make_commitment()],
    )
    selection = ExportSelection(branch_id="main", include_appendices=["cast", "continuity"])
    snap = await build_snapshot("campaign-a", selection, ExportOptions(title="t"), sources)
    assert len(snap.characters) == 1
    assert len(snap.facts) == 1
    assert len(snap.commitments) == 1


async def test_snapshot_applies_filters_to_post_bodies() -> None:
    scene = make_scene(post_count=2)
    sources = make_sources(
        scenes=[scene],
        posts={
            scene.id: [
                make_post(
                    scene.id,
                    1,
                    "Alistair speaks. (OOC: brb)\n\nHe coughs.",
                    kind=AuthorKind.NARRATOR,
                ),
                make_post(
                    scene.id,
                    2,
                    "Roll: perception 5\nHe sees nothing.",
                    kind=AuthorKind.NARRATOR,
                ),
            ]
        },
    )
    selection = ExportSelection(
        branch_id="main",
        include_appendices=[],
        filters={"strip_ooc": True, "strip_mechanics": True},
    )
    snap = await build_snapshot("campaign-a", selection, ExportOptions(title="t"), sources)
    bodies = "\n".join(p.body for p in snap.scenes[0].posts)
    assert "OOC" not in bodies
    assert "Roll:" not in bodies
    assert "He coughs." in bodies
    assert "He sees nothing." in bodies


async def test_snapshot_skips_scenes_with_skip_tags() -> None:
    s1 = make_scene(ordinal=1, slug="open", tags=["draft"], post_count=1)
    s2 = make_scene(ordinal=2, slug="next", post_count=1)
    sources = make_sources(
        scenes=[s1, s2],
        posts={
            s1.id: [make_post(s1.id, 1, "First.")],
            s2.id: [make_post(s2.id, 1, "Second.")],
        },
    )
    selection = ExportSelection(
        branch_id="main",
        include_appendices=[],
        filters={"skip_tags": ["draft"]},
    )
    snap = await build_snapshot("campaign-a", selection, ExportOptions(title="t"), sources)
    assert [p.scene.id for p in snap.scenes] == [s2.id]


async def test_snapshot_anonymize_pcs_swaps_pc_names_in_characters_and_text() -> None:
    scene = make_scene(post_count=1)
    pc = make_character(name="Alistair")
    sources = make_sources(
        scenes=[scene],
        posts={scene.id: [make_post(scene.id, 1, "Alistair pauses.")]},
        characters=[pc],
        pcs={"alistair": "Alistair"},
    )
    selection = ExportSelection(
        branch_id="main",
        include_appendices=["cast"],
        filters={"anonymize_pcs": "The Adept"},
    )
    snap = await build_snapshot("campaign-a", selection, ExportOptions(title="t"), sources)
    assert snap.characters[0].name == "The Adept"
    assert "The Adept" in snap.scenes[0].posts[0].body
    assert "Alistair" not in snap.scenes[0].posts[0].body
