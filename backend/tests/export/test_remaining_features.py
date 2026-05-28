"""Coverage for the remaining-design pass: §5/§6/§8/§9/§10/§11/§15."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from grimoire.export import (
    build_snapshot,
)
from grimoire.export.config import EpubAdapterConfig, ExportFiltersConfig
from grimoire.export.epub import EpubAdapter
from grimoire.scenes.types import AuthorKind
from grimoire.types.export import ExportOptions, ExportSelection

from .conftest import (
    make_commitment,
    make_fact,
    make_library_location,
    make_post,
    make_scene,
    make_sources,
)

# §5 — Per-arc selection -------------------------------------------------- #


async def test_arc_tag_selection_narrows_scenes() -> None:
    s1 = make_scene(ordinal=1, slug="opening", tags=["arc:saint-werewolf"], post_count=1)
    s2 = make_scene(ordinal=2, slug="other", tags=["arc:unrelated"], post_count=1)
    s3 = make_scene(ordinal=3, slug="untagged", tags=[], post_count=1)

    sources = make_sources(
        scenes=[s1, s2, s3],
        posts={
            s1.id: [make_post(s1.id, 1, "Saint scene.")],
            s2.id: [make_post(s2.id, 1, "Other scene.")],
            s3.id: [make_post(s3.id, 1, "Untagged scene.")],
        },
    )
    selection = ExportSelection(arcs=["saint-werewolf"])
    snapshot = await build_snapshot("campaign-a", selection, ExportOptions(), sources)
    slugs = [part.scene.slug for part in snapshot.scenes]
    assert slugs == ["opening"]


# §9 — POV consolidation -------------------------------------------------- #


async def test_pov_consolidation_by_kind_merges_adjacent_posts() -> None:
    scene = make_scene(post_count=3)
    sources = make_sources(
        scenes=[scene],
        posts={
            scene.id: [
                make_post(scene.id, 1, "Narrator A.", kind=AuthorKind.NARRATOR),
                make_post(scene.id, 2, "Narrator B.", kind=AuthorKind.NARRATOR),
                make_post(scene.id, 3, "Alistair speaks.", kind=AuthorKind.PC, pc_ref="alistair"),
            ]
        },
    )
    selection = ExportSelection(filters={"pov_consolidation": "by_kind"})
    snapshot = await build_snapshot("campaign-a", selection, ExportOptions(), sources)
    posts = snapshot.scenes[0].posts
    assert len(posts) == 2
    assert "Narrator A." in posts[0].body and "Narrator B." in posts[0].body
    assert posts[1].author_kind == "pc"


# §15 — Audience policy --------------------------------------------------- #


async def test_share_audience_excludes_continuity_appendix() -> None:
    scene = make_scene(post_count=1)
    sources = make_sources(
        scenes=[scene],
        posts={scene.id: [make_post(scene.id, 1, "Body.")]},
        facts=[make_fact()],
        commitments=[make_commitment()],
    )
    selection = ExportSelection(
        include_appendices=["continuity"],
        audience="share",
    )
    snapshot = await build_snapshot("campaign-a", selection, ExportOptions(), sources)
    assert snapshot.facts == []
    assert snapshot.commitments == []


async def test_personal_audience_keeps_continuity_appendix() -> None:
    scene = make_scene(post_count=1)
    sources = make_sources(
        scenes=[scene],
        posts={scene.id: [make_post(scene.id, 1, "Body.")]},
        facts=[make_fact()],
        commitments=[make_commitment()],
    )
    selection = ExportSelection(
        include_appendices=["continuity"],
        audience="personal",
    )
    snapshot = await build_snapshot("campaign-a", selection, ExportOptions(), sources)
    assert snapshot.facts
    assert snapshot.commitments


# §4 — Config-block defaults --------------------------------------------- #


async def test_filter_defaults_honoured_when_selection_silent() -> None:
    scene = make_scene(post_count=1)
    sources = make_sources(
        scenes=[scene],
        posts={scene.id: [make_post(scene.id, 1, "Body. [roll Dex 5]")]},
    )
    defaults = ExportFiltersConfig(strip_mechanics_default=True)
    selection = ExportSelection()
    snapshot = await build_snapshot(
        "campaign-a", selection, ExportOptions(), sources, filter_defaults=defaults
    )
    body = snapshot.scenes[0].posts[0].body
    assert "[roll" not in body


# §10 — EPUB mechanical footnotes ---------------------------------------- #


async def test_epub_emits_real_footnotes_for_mech_chips() -> None:
    scene = make_scene(post_count=1)
    sources = make_sources(
        scenes=[scene],
        posts={
            scene.id: [
                make_post(scene.id, 1, "He drew his sword [roll Dex 5] and lunged."),
            ]
        },
    )
    adapter = EpubAdapter(sources)
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "book.epub"
        await adapter.export(
            "campaign-a",
            ExportSelection(),
            ExportOptions(title="Test", extra={"include_mechanics_footnotes": True}),
            out,
        )
        # Crack the EPUB zip and inspect the chapter.
        import zipfile

        with zipfile.ZipFile(out) as zf:
            chapter = next(
                zf.read(name).decode("utf-8")
                for name in zf.namelist()
                if name.startswith("OEBPS/chapters/")
            )
    assert 'epub:type="noteref"' in chapter
    assert 'epub:type="footnote"' in chapter
    assert "Dex 5" in chapter  # body of the extracted note


# §8 — Source-attribution labels ----------------------------------------- #


async def test_epub_attribution_renders_source_label() -> None:
    from grimoire.types.composition import WorldMeta

    scene = make_scene(post_count=1)
    sources = make_sources(
        scenes=[scene],
        posts={scene.id: [make_post(scene.id, 1, "Body.")]},
        world=WorldMeta(id="wod-london", name="WoD London", version=7),
        locations=[make_library_location()],
    )
    adapter = EpubAdapter(sources)
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "book.epub"
        await adapter.export(
            "campaign-a",
            ExportSelection(include_appendices=["world", "locations"]),
            ExportOptions(title="T", extra={"show_source_attribution": True}),
            out,
        )
        import zipfile

        with zipfile.ZipFile(out) as zf:
            world_app = next(
                zf.read(name).decode("utf-8")
                for name in zf.namelist()
                if name.endswith("appendices/world.xhtml")
            )
    assert "wod-london v7" in world_app
    assert "source:" in world_app


# §6 — Cover auto-gen ---------------------------------------------------- #


class _StubCoverGen:
    """Returns a tiny PNG so ``_attach_image`` accepts it."""

    PNG = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01"
        b"\xd1\x8a\xd6\xbf\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    async def generate_cover(self, campaign_id: str, prompt: str) -> bytes | None:
        return self.PNG


async def test_epub_auto_cover_pulls_from_generator() -> None:
    scene = make_scene(post_count=1)
    sources = make_sources(
        scenes=[scene],
        posts={scene.id: [make_post(scene.id, 1, "Body.")]},
    )
    sources.cover_generator = _StubCoverGen()
    adapter = EpubAdapter(sources)
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "book.epub"
        await adapter.export(
            "campaign-a",
            ExportSelection(),
            ExportOptions(title="T", extra={"generate_cover": True}),
            out,
        )
        import zipfile

        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
    assert any(name.startswith("OEBPS/images/cover") for name in names)
    assert 'properties="cover-image"' in opf


# §11 — Capabilities are declared on every shipped adapter --------------- #


def test_epub_adapter_declares_capabilities() -> None:
    assert hasattr(EpubAdapter, "capabilities")
    caps = EpubAdapter.capabilities
    assert caps.supports_appendices is True
    assert "novel" in caps.supported_style_presets


# §1 — EPUB adapter constructor honours config-block knobs --------------- #


def test_epub_default_options_picks_up_config_knobs() -> None:
    cfg = EpubAdapterConfig(
        default_style="manuscript",
        include_appendices_by_default=["cast"],
        validate_with_epubcheck=True,
    )
    sources = make_sources()
    adapter = EpubAdapter(sources, config=cfg)
    opts = adapter.default_options()
    assert opts.style_preset == "manuscript"
    assert opts.extra["include_appendices"] == ["cast"]
    assert opts.extra["validate"] is True
