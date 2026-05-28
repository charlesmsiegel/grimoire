from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from grimoire.export import EpubAdapter, ExportService, ExportServiceConfig, UnknownAdapterError
from grimoire.types.export import ExportOptions, ExportSelection

from .conftest import make_post, make_scene, make_sources


def _service(tmp_path: Path) -> ExportService:
    scene = make_scene(post_count=1)
    sources = make_sources(
        scenes=[scene],
        posts={scene.id: [make_post(scene.id, 1, "Lights flicker.")]},
    )
    config = ExportServiceConfig(output_directory=tmp_path / "exports")
    return ExportService(
        sources=sources,
        adapters=[EpubAdapter(sources)],
        config=config,
    )


async def test_service_lists_and_resolves_adapters(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    ids = [a.id for a in svc.list_adapters()]
    assert "epub" in ids
    assert svc.get_adapter("epub").id == "epub"
    with pytest.raises(UnknownAdapterError):
        svc.get_adapter("pdf")


async def test_service_export_writes_into_default_directory(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    selection = ExportSelection(include_appendices=[])
    options = ExportOptions(title="My Campaign", style_preset="novel")
    result = await svc.export("campaign-a", "epub", selection, options)

    assert result.file_path is not None
    out = Path(result.file_path)
    assert out.exists()
    assert out.suffix == ".epub"
    assert out.parent.parent == tmp_path / "exports"
    with zipfile.ZipFile(out) as zf:
        assert "mimetype" in zf.namelist()

    history = await svc.history("campaign-a")
    assert len(history) == 1
    assert history[0].adapter_id == "epub"
    assert history[0].options.title == "My Campaign"


async def test_service_preview_reports_counts_without_writing(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    selection = ExportSelection(include_appendices=[])
    options = ExportOptions(title="T")
    preview = await svc.preview("campaign-a", "epub", selection, options)
    assert preview.scene_count == 1
    assert preview.word_count > 0
    assert preview.estimated_size_bytes >= 4096
    # Preview should not have created the output directory.
    assert not (tmp_path / "exports").exists()


async def test_service_export_uses_explicit_output_path(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    custom = tmp_path / "elsewhere" / "book.epub"
    selection = ExportSelection(include_appendices=[])
    options = ExportOptions(title="X")
    result = await svc.export("campaign-a", "epub", selection, options, output_path=custom)
    assert custom.exists()
    assert result.file_path == str(custom)
