"""Smoke tests for the TestApp harness."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.testing import TestApp, TestAppFixture


@pytest.mark.asyncio
async def test_test_app_lifecycle(tmp_path: Path) -> None:
    async with TestApp(tmp_path) as app:
        assert app.state_store is not None
        assert app.mechanics is not None
        assert app.scene_manager is not None
        assert app.continuity is not None
        # MockLLMGateway is present and usable.
        app.llm.queue_response("primary", "She nods.")
        assert app.llm.remaining("primary") == 1


@pytest.mark.asyncio
async def test_test_app_with_fixtures_copies_files(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "library" / "settings").mkdir(parents=True)
    (src / "library" / "settings" / "marker.yaml").write_text("id: marker\n", encoding="utf-8")

    fixture = TestAppFixture(name="probe", files_root=src)
    async with TestApp.with_fixtures(fixture, root=tmp_path / "data") as app:
        marker = app.data_root / "library" / "settings" / "marker.yaml"
        assert marker.is_file()


@pytest.mark.asyncio
async def test_test_app_fixture_setup_hook_runs(tmp_path: Path) -> None:
    flag: dict[str, bool] = {"ran": False}

    async def setup(app: TestApp) -> None:
        flag["ran"] = True

    fixture = TestAppFixture(name="probe", setup=setup)
    async with TestApp.with_fixtures(fixture, root=tmp_path) as _:
        pass
    assert flag["ran"] is True
