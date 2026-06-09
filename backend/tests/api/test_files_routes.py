"""Tests for GET /api/files/{path} (issue #582).

The route serves campaign-generated images by their data-root-relative
``file_path`` and refuses everything else: traversal out of the data root,
paths outside the ``campaigns/<id>/images/`` allowlist, and non-image
files (YAML sidecars, campaigns.sqlite). The data root is the per-test
``tmp_path`` wired up by the autouse ``_isolate_api_data_root`` fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from grimoire.api.files import _resolve_allowlisted

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"not-a-real-png-but-close-enough"
JPG_BYTES = b"\xff\xd8\xff\xe0" + b"thumb"


@pytest.fixture()
def image_tree(tmp_path: Path) -> Path:
    """Seed a campaign images subtree plus files that must stay private."""
    images = tmp_path / "campaigns" / "camp-1" / "images"
    (images / "thumbnails").mkdir(parents=True)
    (images / "img-1.png").write_bytes(PNG_BYTES)
    (images / "img-1.yaml").write_text("prompt: secret sidecar\n")
    (images / "thumbnails" / "img-1.jpg").write_bytes(JPG_BYTES)
    (tmp_path / "campaigns.sqlite").write_bytes(b"sqlite-ish")
    (tmp_path / "secret.png").write_bytes(b"outside the allowlist")
    library = tmp_path / "library" / "worlds" / "w1" / "characters"
    library.mkdir(parents=True)
    (library / "alice.md").write_text("---\nid: alice\n---\nbody\n")
    return tmp_path


def test_serves_campaign_image(client: TestClient, image_tree: Path) -> None:
    resp = client.get("/api/files/campaigns/camp-1/images/img-1.png")
    assert resp.status_code == 200
    assert resp.content == PNG_BYTES
    assert resp.headers["content-type"] == "image/png"


def test_serves_thumbnail(client: TestClient, image_tree: Path) -> None:
    resp = client.get("/api/files/campaigns/camp-1/images/thumbnails/img-1.jpg")
    assert resp.status_code == 200
    assert resp.content == JPG_BYTES
    assert resp.headers["content-type"] == "image/jpeg"


def test_missing_image_404s(client: TestClient, image_tree: Path) -> None:
    resp = client.get("/api/files/campaigns/camp-1/images/nope.png")
    assert resp.status_code == 404


def test_rejects_traversal_over_http(client: TestClient, image_tree: Path) -> None:
    # Percent-encoded dot segments so no client-side normalization can
    # collapse them before they reach the route. tmp_path/secret.png
    # exists — a 404 here is the guard, not a missing file.
    resp = client.get("/api/files/campaigns/camp-1/images/%2e%2e/%2e%2e/%2e%2e/secret.png")
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        # Climb out of the images subtree (target exists at the data root).
        "campaigns/camp-1/images/../../../secret.png",
        # Climb out of the data root entirely.
        "campaigns/camp-1/images/../../../../etc/passwd",
        # Absolute path.
        "/etc/passwd",
        # Not allowlisted: the SQLite DB, library content, campaign YAML.
        "campaigns.sqlite",
        "library/worlds/w1/characters/alice.md",
        "campaigns/camp-1/campaign.yaml",
        "secret.png",
        # Inside the allowlisted subtree but not an image: the metadata sidecar.
        "campaigns/camp-1/images/img-1.yaml",
        # The images directory itself.
        "campaigns/camp-1/images",
    ],
)
def test_resolver_refuses_disallowed_paths(image_tree: Path, path: str) -> None:
    with pytest.raises(HTTPException) as excinfo:
        _resolve_allowlisted(path)
    assert excinfo.value.status_code == 404


def test_resolver_accepts_allowlisted_image(image_tree: Path) -> None:
    resolved = _resolve_allowlisted("campaigns/camp-1/images/img-1.png")
    assert resolved == image_tree / "campaigns" / "camp-1" / "images" / "img-1.png"
