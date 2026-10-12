"""A scene-owned authorial paragraph, never a transcript edit or identity store.

Focus travels in the scene's frontmatter through ordinary file lifecycle
operations. Reads never repair legacy records; writes require a real record
and hold the same whole-read/modify/write lock as transcript mutations.
"""

from __future__ import annotations

from pathlib import Path

from .. import atomic, frontmatter
from .. import paths as store_paths
from . import locking, paths


def normalize(text: str) -> str:
    """Collapse every frontmatter line boundary, preserving interior prose."""
    return "".join(" " if char in frontmatter.LINE_BREAKS else char
                   for char in text).strip()


def _path(cid: str, sid: str) -> Path:
    if not store_paths.safe_id(sid):
        raise paths.SceneNotFound(sid)
    paths._require_campaign(cid)
    return paths._scene_path(cid, sid)


def _field(text: str) -> dict[str, str]:
    text = normalize(text)
    return {"text": text, "status": "ok" if text else "empty"}


def read(cid: str, sid: str) -> dict[str, str]:
    """Unreadable is not empty: callers can warn without leaking the record."""
    path = _path(cid, sid)
    try:
        meta, _body = frontmatter.read_record(path, "scene")
    except FileNotFoundError:
        raise paths.SceneNotFound(sid) from None
    except (OSError, UnicodeError):
        return {"text": "", "status": "unreadable"}
    return _field(meta.get("focus", ""))


@locking._serialized
def set(cid: str, sid: str, text: str) -> dict[str, str]:  # noqa: A001 -- public focus API; no builtin set is used here
    """Change only focus; a refused read/publication leaves the scene intact."""
    path = _path(cid, sid)
    try:
        meta, body = frontmatter.read_record(path, "scene")
    except FileNotFoundError:
        raise paths.SceneNotFound(sid) from None
    field = _field(text)
    if field["text"]:
        meta["focus"] = field["text"]
    else:
        meta.pop("focus", None)
    # In particular, do not mint identity or stamp updated: the transcript
    # and its captured responses have not changed. HTTP activity is separate.
    atomic.write_text(path, frontmatter.dump_frontmatter(meta, body))
    return field
