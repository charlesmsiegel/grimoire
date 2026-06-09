"""Read-only serving of allowlisted data-root files (issue #582).

The frontend addresses generated images by their data-root-relative
``file_path`` (the ``images.file_path`` column / ``ImageMetadata.file_path``)
as ``GET /api/files/{path}`` — gallery tiles, thumbnails, and inline scene
images all build URLs that way. This router serves exactly the allowlisted
subtrees and nothing else: requests are resolved strictly inside the data
root (path-traversal guarded) and must land on an image file under
``campaigns/<id>/images/``. ``campaigns.sqlite``, YAML sidecars, library
content, and arbitrary data-root files are never exposed.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from grimoire import config as _config

router = APIRouter(prefix="/files", tags=["files"])

# Suffixes servable from the allowlisted subtree. The image directory also
# holds per-image YAML metadata sidecars, which stay private.
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _resolve_allowlisted(path: str) -> Path:
    """Map a data-root-relative request path to a servable file.

    Raises 404 for anything that escapes the data root, falls outside the
    ``campaigns/<id>/images/`` subtree, isn't an image file, or doesn't
    exist. Disallowed paths get the same 404 as missing ones so the route
    can't be used to probe what exists elsewhere in the data root.
    """
    not_found = HTTPException(status_code=404, detail=f"no such file {path!r}")
    data_root = _config.settings.data_root.resolve()
    try:
        candidate = (data_root / path).resolve()
        rel = candidate.relative_to(data_root)
    except (OSError, ValueError) as exc:
        raise not_found from exc
    parts = rel.parts
    if len(parts) < 4 or parts[0] != "campaigns" or parts[2] != "images":
        raise not_found
    if candidate.suffix.lower() not in _IMAGE_SUFFIXES:
        raise not_found
    if not candidate.is_file():
        raise not_found
    return candidate


@router.get("/{path:path}")
async def serve_file(path: str) -> FileResponse:
    return FileResponse(_resolve_allowlisted(path))


__all__ = ["router"]
