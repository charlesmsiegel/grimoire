"""GRIMOIRE_TEMPLATES / GRIMOIRE_DIST relocate repo-relative resources.

The Android build extracts templates and the built frontend to app storage and
points these env vars at the extracted copies (docs/android-architecture.md §6).
"""

import pytest
from fastapi.testclient import TestClient

from grimoire import prompts
from grimoire.main import create_app, dist_dir


@pytest.fixture
def fresh_jinja():
    """The jinja Environment is cached per-process; isolate it on both sides."""
    prompts._env.cache_clear()
    yield
    prompts._env.cache_clear()


def test_templates_dir_defaults_to_repo(monkeypatch):
    monkeypatch.delenv("GRIMOIRE_TEMPLATES", raising=False)
    assert prompts.templates_dir() == prompts.DEFAULT_TEMPLATES_DIR


def test_templates_env_override_renders(monkeypatch, tmp_path, fresh_jinja):
    (tmp_path / "hello").write_text("hi {{ name }}", encoding="utf-8")
    monkeypatch.setenv("GRIMOIRE_TEMPLATES", str(tmp_path))
    assert prompts.templates_dir() == tmp_path
    assert prompts.render("hello", name="grimoire") == "hi grimoire"


def test_dist_env_override_serves_spa(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "store"))
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>grimoire</title>", encoding="utf-8")
    monkeypatch.setenv("GRIMOIRE_DIST", str(dist))
    assert dist_dir() == dist

    client = TestClient(create_app())
    res = client.get("/")
    assert res.status_code == 200
    assert "grimoire" in res.text


def test_dist_env_override_missing_dir_skips_mount(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "store"))
    monkeypatch.setenv("GRIMOIRE_DIST", str(tmp_path / "nowhere"))
    client = TestClient(create_app())
    # the API is still up even with no frontend bundle present
    assert client.get("/api/config").status_code == 200
