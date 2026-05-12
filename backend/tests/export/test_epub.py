from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from grimoire.export import EmptyExportError, EpubAdapter
from grimoire.scenes.types import AuthorKind
from grimoire.types.composition import SettingMeta
from grimoire.types.export import ExportOptions, ExportSelection

from .conftest import (
    make_character,
    make_commitment,
    make_fact,
    make_image,
    make_library_location,
    make_post,
    make_scene,
    make_sources,
)

# A minimal 1x1 transparent PNG so the EPUB has something to embed.
PNG_1x1 = bytes.fromhex(
    "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489"
    "0000000A49444154789C63000100000500010D0A2DB40000000049454E44AE426082"
)


def _selection(*, scene_ids=None, include_appendices=None, **kw) -> ExportSelection:
    return ExportSelection(
        branch_id="main",
        scene_ids=scene_ids,
        include_appendices=list(include_appendices or []),
        **kw,
    )


def _options(**kw) -> ExportOptions:
    base: dict = {"title": "The Tower Year One", "author": "julian", "style_preset": "novel"}
    base.update(kw)
    return ExportOptions(**base)


async def test_epub_export_writes_valid_zip_with_required_files(tmp_path: Path) -> None:
    scene = make_scene(post_count=2)
    sources = make_sources(
        scenes=[scene],
        posts={
            scene.id: [
                make_post(scene.id, 1, "The tower is candle-lit.", kind=AuthorKind.NARRATOR),
                make_post(
                    scene.id,
                    2,
                    "I incline my head.",
                    kind=AuthorKind.PC,
                    pc_ref="alistair",
                    is_player=True,
                ),
            ]
        },
    )
    adapter = EpubAdapter(sources)
    out = tmp_path / "campaign.epub"
    result = await adapter.export(
        "campaign-a",
        _selection(include_appendices=["cast"]),
        _options(),
        out,
    )

    assert out.exists()
    assert result.scene_count == 1
    assert result.word_count > 0
    assert result.format == "epub"

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        # mimetype must be first entry, stored uncompressed.
        first_info = zf.infolist()[0]
        assert first_info.filename == "mimetype"
        assert first_info.compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype") == b"application/epub+zip"

        assert "META-INF/container.xml" in names
        assert "OEBPS/content.opf" in names
        assert "OEBPS/nav.xhtml" in names
        assert "OEBPS/styles/main.css" in names

        opf = zf.read("OEBPS/content.opf").decode("utf-8")
        assert "<dc:title>The Tower Year One</dc:title>" in opf
        assert "<dc:creator>julian</dc:creator>" in opf
        assert 'properties="nav"' in opf

        nav = zf.read("OEBPS/nav.xhtml").decode("utf-8")
        assert "Elysium Opening" in nav

        chapter_files = [n for n in names if n.startswith("OEBPS/chapters/")]
        assert chapter_files, "expected at least one chapter file"
        chapter_html = zf.read(chapter_files[0]).decode("utf-8")
        assert "candle-lit" in chapter_html
        assert "incline my head" in chapter_html


async def test_epub_raises_on_empty_selection(tmp_path: Path) -> None:
    sources = make_sources()
    adapter = EpubAdapter(sources)
    with pytest.raises(EmptyExportError):
        await adapter.export(
            "campaign-a",
            _selection(),
            _options(),
            tmp_path / "x.epub",
        )


async def test_epub_includes_appendices(tmp_path: Path) -> None:
    scene = make_scene(post_count=1)
    setting = SettingMeta(id="wod-london", name="WoD London", description="Foggy.")
    sources = make_sources(
        scenes=[scene],
        posts={scene.id: [make_post(scene.id, 1, "Opening line.")]},
        characters=[make_character(name="Alistair", description="Edwardian gent.")],
        setting=setting,
        locations=[make_library_location("Elysium")],
        facts=[make_fact("The cup is poisoned.")],
        commitments=[make_commitment("Speak with Edwin by dawn.")],
    )
    adapter = EpubAdapter(sources)
    out = tmp_path / "b.epub"
    result = await adapter.export(
        "campaign-a",
        _selection(include_appendices=["cast", "setting", "continuity", "calendar"]),
        _options(),
        out,
    )
    assert result.scene_count == 1
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        for slug in ("cast", "setting", "continuity", "calendar"):
            assert f"OEBPS/appendices/{slug}.xhtml" in names, slug
        cast = zf.read("OEBPS/appendices/cast.xhtml").decode("utf-8")
        assert "Alistair" in cast
        assert "Edwardian gent." in cast
        continuity = zf.read("OEBPS/appendices/continuity.xhtml").decode("utf-8")
        assert "cup is poisoned" in continuity
        assert "Speak with Edwin" in continuity


async def test_epub_embeds_inline_images(tmp_path: Path) -> None:
    scene = make_scene(post_count=1)
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "elysium.png"
    image_path.write_bytes(PNG_1x1)
    image = make_image(image_id="img-elysium", file_path=image_path, scene_id=scene.id)
    sources = make_sources(
        scenes=[scene],
        posts={scene.id: [make_post(scene.id, 1, "Candlelight flickers.")]},
        images=[image],
    )
    adapter = EpubAdapter(sources)
    out = tmp_path / "c.epub"
    await adapter.export(
        "campaign-a",
        _selection(include_appendices=[]),
        _options(),
        out,
    )
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "OEBPS/images/img-elysium.png" in names
        chapter = next(n for n in names if n.startswith("OEBPS/chapters/"))
        assert "img-elysium.png" in zf.read(chapter).decode("utf-8")


async def test_epub_cover_image_added_to_manifest(tmp_path: Path) -> None:
    scene = make_scene(post_count=1)
    sources = make_sources(
        scenes=[scene],
        posts={scene.id: [make_post(scene.id, 1, "x.")]},
    )
    adapter = EpubAdapter(sources)
    out = tmp_path / "d.epub"
    await adapter.export(
        "campaign-a",
        _selection(include_appendices=[]),
        _options(cover_image=PNG_1x1, extra={"include_appendices": []}),
        out,
    )
    with zipfile.ZipFile(out) as zf:
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
        assert 'properties="cover-image"' in opf
        assert 'name="cover"' in opf
        assert "OEBPS/cover.xhtml" in zf.namelist()


async def test_epub_unknown_style_preset_falls_back_with_warning(tmp_path: Path) -> None:
    scene = make_scene(post_count=1)
    sources = make_sources(
        scenes=[scene],
        posts={scene.id: [make_post(scene.id, 1, "x")]},
    )
    adapter = EpubAdapter(sources)
    out = tmp_path / "e.epub"
    result = await adapter.export(
        "campaign-a",
        _selection(),
        _options(style_preset="gothic-pulp"),
        out,
    )
    assert any("unknown style preset" in w for w in result.warnings)


async def test_epub_option_schema_lists_style_presets() -> None:
    adapter = EpubAdapter(make_sources())
    schema = adapter.option_schema()
    presets = schema["properties"]["style_preset"]["enum"]
    assert "novel" in presets
    assert "manuscript" in presets


async def test_epub_validate_without_binary_records_warning(tmp_path: Path) -> None:
    scene = make_scene(post_count=1)
    sources = make_sources(
        scenes=[scene],
        posts={scene.id: [make_post(scene.id, 1, "x.")]},
    )
    adapter = EpubAdapter(sources, epubcheck_path="/nonexistent/epubcheck")
    out = tmp_path / "g.epub"
    result = await adapter.export(
        "campaign-a",
        _selection(),
        _options(extra={"validate": True}),
        out,
    )
    assert any("not found" in w for w in result.warnings)
