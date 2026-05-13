"""Tests for the prompt-template HTTP API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from grimoire.templates import registry as template_registry


@pytest.fixture(autouse=True)
def _reset_template_registry() -> None:
    """Strip any user-supplied search paths and clear variant overrides.

    The registry is a module-level singleton, so writes from one test would
    leak into the next without this — every test starts with bundled defaults
    only, no pinned variants.
    """
    # Keep only the bundled package path (last entry); drop user paths added
    # by previous lifespans.
    paths = template_registry.search_paths
    bundled = paths[-1] if paths else None
    template_registry._search_paths.clear()
    if bundled is not None:
        template_registry._search_paths.append(bundled)
    template_registry._variants.clear()
    template_registry._env = None


def test_list_templates_returns_bundled_defaults(client: TestClient) -> None:
    response = client.get("/api/templates")
    assert response.status_code == 200
    payload = response.json()
    assert "templates" in payload
    names = {entry["name"] for entry in payload["templates"]}
    # A couple of bundled templates we know exist
    assert "extractor_user" in names
    assert "imagegen_positive" in names
    for entry in payload["templates"]:
        assert "default" in entry["variants"]
        assert entry["active"] == "default"
        assert entry["editable"] == []


def test_write_then_read_user_variant(client: TestClient) -> None:
    body = "User variant: {{ subject }}"
    write = client.put(
        "/api/templates/extractor_user/terse",
        json={"body": body},
    )
    assert write.status_code == 200

    read = client.get("/api/templates/extractor_user/terse")
    assert read.status_code == 200
    payload = read.json()
    assert payload["body"] == body
    assert payload["editable"] is True

    listing = client.get("/api/templates").json()
    extractor = next(e for e in listing["templates"] if e["name"] == "extractor_user")
    assert "terse" in extractor["variants"]
    assert "terse" in extractor["editable"]


def test_set_active_variant_and_revert(client: TestClient) -> None:
    client.put(
        "/api/templates/extractor_user/snappy",
        json={"body": "snappy: {{ subject }}"},
    )
    activate = client.post(
        "/api/templates/extractor_user/active",
        json={"variant": "snappy"},
    )
    assert activate.status_code == 200
    assert activate.json()["active"] == "snappy"

    revert = client.post(
        "/api/templates/extractor_user/active",
        json={"variant": None},
    )
    assert revert.status_code == 200
    assert revert.json()["active"] == "default"


def test_delete_user_variant(client: TestClient) -> None:
    client.put(
        "/api/templates/extractor_user/scratch",
        json={"body": "scratch"},
    )
    delete = client.delete("/api/templates/extractor_user/scratch")
    assert delete.status_code == 200

    read = client.get("/api/templates/extractor_user/scratch")
    assert read.status_code == 404


def test_rejects_bad_names(client: TestClient) -> None:
    bad = client.put(
        "/api/templates/..%2Fevil/oops",
        json={"body": "no"},
    )
    assert bad.status_code in (400, 404)


def test_rejects_oversized_body(client: TestClient) -> None:
    huge = "x" * 200_001
    response = client.put(
        "/api/templates/extractor_user/huge",
        json={"body": huge},
    )
    assert response.status_code == 413
