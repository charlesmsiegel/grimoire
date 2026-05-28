"""Tests for the bundled export adapters.

The adapters share a snapshot helper that walks ``data/campaigns/<id>/``;
each test sets up a small on-disk campaign fixture in a temporary
directory and runs the adapter against it.
"""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from grimoire.plugins.discovery import discover
from grimoire.plugins.loader import load_plugin
from grimoire.scenes.storage import write_body, write_sidecar
from grimoire.scenes.types import AuthorKind, Post, Scene
from grimoire.types.export import ExportOptions, ExportSelection

from .conftest import BUNDLED_PLUGINS_ROOT, assert_protocol_attrs

PROTOCOL_ATTRS = (
    "id",
    "name",
    "extensions",
    "mime_type",
    "export",
    "default_options",
    "option_schema",
)


def _make_post(scene_id: str, order: int, body: str, *, pc_ref: str | None = None) -> Post:
    kind = AuthorKind.PC if pc_ref else AuthorKind.NARRATOR
    return Post(
        id=f"{scene_id}:p{order}",
        scene_id=scene_id,
        order_in_scene=order,
        author_kind=kind,
        body=body,
        is_player=bool(pc_ref),
        created_at=datetime.now(UTC),
        turn_id=f"turn-{order}",
        author_pc_ref=pc_ref,
    )


def _build_campaign(root: Path, campaign_id: str = "cmp-1") -> str:
    """Create a tiny on-disk campaign rooted at ``root``.

    Returns ``campaign_id`` for convenience. Layout matches what the
    Scene Manager + Library writers produce.
    """
    campaign_dir = root / "campaigns" / campaign_id
    campaign_dir.mkdir(parents=True)
    (campaign_dir / "campaign.yaml").write_text(
        yaml.safe_dump({"id": campaign_id, "title": "The Long Night"}),
        encoding="utf-8",
    )

    scenes_dir = campaign_dir / "scenes"
    scenes_dir.mkdir()
    scene = Scene(
        id="sc-001",
        campaign_id=campaign_id,
        ordinal=1,
        slug="arrival",
        title="Arrival",
        location_ref="loc:harbor",
        present_pc_refs=["julian"],
        closed=True,
        running_summary="julian steps off the boat.",
        final_summary="julian arrives in the harbor.",
    )
    posts = [
        _make_post("sc-001", 1, "The fog rolls across the docks.", pc_ref=None),
        _make_post("sc-001", 2, "julian tugs his cloak tighter.", pc_ref="julian"),
        _make_post(
            "sc-001",
            3,
            "(OOC: skip dice) The lantern-keeper waves him in.",
            pc_ref=None,
        ),
    ]
    write_body(scenes_dir / "0001-arrival.md", posts)
    write_sidecar(scenes_dir / "0001-arrival.yaml", scene)

    # Closed but empty scene exercises the include_drafts filter.
    draft = Scene(
        id="sc-002",
        campaign_id=campaign_id,
        ordinal=2,
        slug="empty",
        title="Placeholder",
        closed=False,
    )
    write_sidecar(scenes_dir / "0002-empty.yaml", draft)
    (scenes_dir / "0002-empty.md").write_text("", encoding="utf-8")

    emergent_chars = campaign_dir / "emergent" / "characters"
    emergent_chars.mkdir(parents=True)
    (emergent_chars / "julian.md").write_text(
        "---\nname: julian\ndescription: A traveler from the south.\n---\n\n"
        "Quiet, observant, prone to long silences.\n",
        encoding="utf-8",
    )

    emergent_locs = campaign_dir / "emergent" / "locations"
    emergent_locs.mkdir(parents=True)
    (emergent_locs / "harbor.md").write_text(
        "---\nname: The Harbor\n---\n\nFoggy docks at the city's edge.\n",
        encoding="utf-8",
    )

    images_dir = campaign_dir / "images"
    images_dir.mkdir()
    (images_dir / "img-001.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (images_dir / "img-001.yaml").write_text(
        yaml.safe_dump({"id": "img-001", "prompt": "foggy harbor at dawn"}),
        encoding="utf-8",
    )

    return campaign_id


@pytest.fixture
def campaign_fixture(tmp_path: Path) -> tuple[Path, str]:
    data_root = tmp_path / "data"
    data_root.mkdir()
    campaign_id = _build_campaign(data_root)
    return data_root, campaign_id


@pytest.mark.parametrize(
    "plugin_id",
    [
        "export-markdown",
        "export-single-markdown",
        "export-json",
        "export-transcript",
        "export-html",
    ],
)
def test_manifest_discovers_and_loads(plugin_id: str) -> None:
    discovered, errors = discover([], bundled_roots=[BUNDLED_PLUGINS_ROOT])
    assert not errors, errors
    target = next(d for d in discovered if d.raw_manifest["id"] == plugin_id)
    result = load_plugin(target, config={})
    assert result.ok, result.errors
    instance = result.instances[0].instance
    assert_protocol_attrs(instance, PROTOCOL_ATTRS)
    assert isinstance(instance.option_schema(), dict)


@pytest.mark.asyncio
async def test_markdown_bundle_zips_scenes_and_cast(
    export_markdown_module, campaign_fixture, tmp_path: Path
) -> None:
    data_root, campaign_id = campaign_fixture
    adapter = export_markdown_module.MarkdownBundleAdapter(config={"data_root": str(data_root)})
    out = tmp_path / "out.zip"
    result = await adapter.export(
        campaign_id,
        ExportSelection(),
        ExportOptions(title="Bundle Probe"),
        out,
    )

    assert result.scene_count == 1
    assert result.size_bytes == out.stat().st_size
    assert result.format == "markdown"
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "README.md" in names
        assert any(n.startswith("scenes/0001-arrival") for n in names)
        assert "characters/julian.md" in names
        # Image binary was bundled
        assert "images/img-001.png" in names
        scene_text = zf.read("scenes/0001-arrival.md").decode("utf-8")
        assert "Arrival" in scene_text
        assert "fog rolls across the docks" in scene_text


@pytest.mark.asyncio
async def test_markdown_bundle_respects_scene_selection(
    export_markdown_module, campaign_fixture, tmp_path: Path
) -> None:
    data_root, campaign_id = campaign_fixture
    adapter = export_markdown_module.MarkdownBundleAdapter(config={"data_root": str(data_root)})
    out = tmp_path / "out.zip"
    result = await adapter.export(
        campaign_id,
        ExportSelection(scene_ids=[]),
        ExportOptions(title="Empty"),
        out,
    )
    assert result.scene_count == 0
    with zipfile.ZipFile(out) as zf:
        scene_files = [n for n in zf.namelist() if n.startswith("scenes/")]
        assert scene_files == []


@pytest.mark.asyncio
async def test_single_markdown_lists_scenes_and_filters_ooc(
    export_single_markdown_module, campaign_fixture, tmp_path: Path
) -> None:
    data_root, campaign_id = campaign_fixture
    adapter = export_single_markdown_module.SingleMarkdownAdapter(
        config={"data_root": str(data_root)}
    )
    out = tmp_path / "campaign.md"
    result = await adapter.export(
        campaign_id,
        ExportSelection(filters={"strip_ooc": True}),
        ExportOptions(title="My Campaign", author="The Storyteller"),
        out,
    )
    text = out.read_text(encoding="utf-8")
    assert result.scene_count == 1
    assert result.size_bytes > 0
    assert "# My Campaign" in text
    assert "by The Storyteller" in text
    assert "Arrival" in text
    # OOC parenthetical should be stripped by filter
    assert "OOC" not in text
    assert "lantern-keeper" in text


@pytest.mark.asyncio
async def test_json_export_structure(export_json_module, campaign_fixture, tmp_path: Path) -> None:
    data_root, campaign_id = campaign_fixture
    adapter = export_json_module.JsonExportAdapter(config={"data_root": str(data_root)})
    out = tmp_path / "dump.json"
    result = await adapter.export(
        campaign_id,
        ExportSelection(),
        ExportOptions(title="Snapshot"),
        out,
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert result.format == "json"
    assert payload["campaign"]["id"] == campaign_id
    assert payload["campaign"]["title"] == "The Long Night"
    assert len(payload["scenes"]) == 1
    assert payload["scenes"][0]["title"] == "Arrival"
    assert payload["scenes"][0]["posts"][0]["author_kind"] == "narrator"
    assert any(c["id"] == "julian" for c in payload["characters"])
    assert payload["images"][0]["id"] == "img-001"


@pytest.mark.asyncio
async def test_json_export_can_be_compact(
    export_json_module, campaign_fixture, tmp_path: Path
) -> None:
    data_root, campaign_id = campaign_fixture
    adapter = export_json_module.JsonExportAdapter(
        config={"data_root": str(data_root), "pretty_print": False}
    )
    out = tmp_path / "dump.json"
    await adapter.export(
        campaign_id,
        ExportSelection(),
        ExportOptions(title="Snapshot"),
        out,
    )
    text = out.read_text(encoding="utf-8")
    # Pretty-printed output starts with "{\n  "; compact output starts with `{"`.
    assert not text.startswith("{\n")


@pytest.mark.asyncio
async def test_transcript_drops_mechanics_and_labels_speakers(
    export_transcript_module, campaign_fixture, tmp_path: Path
) -> None:
    data_root, campaign_id = campaign_fixture
    adapter = export_transcript_module.TranscriptAdapter(config={"data_root": str(data_root)})
    out = tmp_path / "transcript.txt"
    result = await adapter.export(
        campaign_id,
        ExportSelection(),
        ExportOptions(title="The Long Night"),
        out,
    )
    text = out.read_text(encoding="utf-8")
    assert result.format == "transcript"
    # Transcript always strips OOC by default
    assert "OOC" not in text
    # PC speaker is labeled by ref
    assert "julian:" in text.lower()
    # Title appears upper-cased
    assert "THE LONG NIGHT" in text


@pytest.mark.asyncio
async def test_html_export_emits_anchors_and_assets(
    export_html_module, campaign_fixture, tmp_path: Path
) -> None:
    data_root, campaign_id = campaign_fixture
    adapter = export_html_module.HtmlExportAdapter(config={"data_root": str(data_root)})
    out = tmp_path / "campaign.html"
    result = await adapter.export(
        campaign_id,
        ExportSelection(),
        ExportOptions(title="The Long Night"),
        out,
    )
    text = out.read_text(encoding="utf-8")
    assert result.format == "html"
    assert "<!DOCTYPE html>" in text
    assert "Scene 1: Arrival" in text
    # Anchor for the only scene
    assert 'id="scene-0001"' in text
    # Image copied into the asset directory
    asset_dir = out.parent / f"{out.stem}_assets"
    assert (asset_dir / "img-001.png").is_file()


@pytest.mark.asyncio
async def test_html_export_writes_external_stylesheet_when_not_embedded(
    export_html_module, campaign_fixture, tmp_path: Path
) -> None:
    data_root, campaign_id = campaign_fixture
    adapter = export_html_module.HtmlExportAdapter(
        config={"data_root": str(data_root), "embed_styles": False}
    )
    out = tmp_path / "campaign.html"
    await adapter.export(
        campaign_id,
        ExportSelection(),
        ExportOptions(title="Probe"),
        out,
    )
    css = out.parent / "campaign.css"
    assert css.is_file() and css.read_text(encoding="utf-8")
    text = out.read_text(encoding="utf-8")
    assert "campaign.css" in text


@pytest.mark.asyncio
async def test_empty_campaign_still_produces_output(export_json_module, tmp_path: Path) -> None:
    """The conformance fixture uses an empty campaign id — we must not crash."""
    data_root = tmp_path / "data"
    data_root.mkdir()
    adapter = export_json_module.JsonExportAdapter(config={"data_root": str(data_root)})
    out = tmp_path / "out.json"
    result = await adapter.export(
        "probe",
        ExportSelection(),
        ExportOptions(title="Empty"),
        out,
    )
    assert result.size_bytes > 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["scenes"] == []
    assert payload["campaign"]["id"] == "probe"


@pytest.mark.asyncio
async def test_html_export_neutralizes_custom_css_breakout(
    export_html_module, campaign_fixture, tmp_path: Path
) -> None:
    data_root, campaign_id = campaign_fixture
    adapter = export_html_module.HtmlExportAdapter(config={"data_root": str(data_root)})
    out = tmp_path / "campaign.html"
    hostile = "</style><script>alert(1)</script><style>"
    await adapter.export(
        campaign_id,
        ExportSelection(),
        ExportOptions(title="Probe", extra={"custom_css": hostile}),
        out,
    )
    text = out.read_text(encoding="utf-8")
    # The injected </style> must be defused so the <script> stays inside
    # the style block (where browsers treat it as inert CSS text).
    style_open = text.index("<style>")
    style_close = text.index("</style>", style_open)
    style_body = text[style_open + len("<style>") : style_close]
    assert "</style>" not in style_body
    # The hostile script must not appear *outside* the style block.
    assert "<script>" not in text[style_close:]


@pytest.mark.asyncio
async def test_anonymize_pcs_rewrites_speaker_labels(
    export_html_module,
    export_single_markdown_module,
    export_transcript_module,
    campaign_fixture,
    tmp_path: Path,
) -> None:
    data_root, campaign_id = campaign_fixture
    filters = {"anonymize_pcs": {"julian": "Traveler"}}

    html_adapter = export_html_module.HtmlExportAdapter(config={"data_root": str(data_root)})
    html_out = tmp_path / "out.html"
    await html_adapter.export(
        campaign_id,
        ExportSelection(filters=filters),
        ExportOptions(title="Anon"),
        html_out,
    )
    html_text = html_out.read_text(encoding="utf-8")
    assert ">julian:" not in html_text.lower()
    assert "Traveler:" in html_text or ">Traveler<" in html_text

    md_adapter = export_single_markdown_module.SingleMarkdownAdapter(
        config={"data_root": str(data_root)}
    )
    md_out = tmp_path / "out.md"
    await md_adapter.export(
        campaign_id,
        ExportSelection(filters=filters),
        ExportOptions(title="Anon"),
        md_out,
    )
    md_text = md_out.read_text(encoding="utf-8")
    assert "**julian**" not in md_text
    assert "**Traveler**" in md_text

    txt_adapter = export_transcript_module.TranscriptAdapter(config={"data_root": str(data_root)})
    txt_out = tmp_path / "out.txt"
    await txt_adapter.export(
        campaign_id,
        ExportSelection(filters=filters),
        ExportOptions(title="Anon"),
        txt_out,
    )
    txt_text = txt_out.read_text(encoding="utf-8")
    assert "julian:" not in txt_text.lower()
    assert "Traveler:" in txt_text


@pytest.mark.asyncio
async def test_markdown_bundle_image_count_matches_written_files(
    export_markdown_module, campaign_fixture, tmp_path: Path
) -> None:
    data_root, campaign_id = campaign_fixture
    adapter = export_markdown_module.MarkdownBundleAdapter(config={"data_root": str(data_root)})
    out = tmp_path / "out.zip"
    result = await adapter.export(
        campaign_id,
        ExportSelection(),
        # extra={include_image_binaries: False} → image is *not* written
        ExportOptions(title="Probe", extra={"include_image_binaries": False}),
        out,
    )
    with zipfile.ZipFile(out) as zf:
        png_files = [n for n in zf.namelist() if n.startswith("images/") and n.endswith(".png")]
    assert png_files == []
    # image_count must reflect what's actually in the archive, not the source list.
    assert result.image_count == 0
