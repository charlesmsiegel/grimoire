"""Scope-owned standing prompts, with live world inheritance and additive text.

A default is an optional whole text file, not an entity to materialize or merge
through the campaign overlay. Resolve the campaign's own setting and only its
safely validated bound world's setting afresh; an absent world never means the
campaign directory. Empty and unreadable are different so editing cannot turn a
failed read into a blank save. Atomic publication is the save; the subsequent
activity stamp is only a ranking hint and may not make that save fail.

This does not coordinate devices or compare simultaneous saves. Each scope is
last-successful-publication; campaign writes use the existing campaign lock,
while worlds retain their existing whole-record atomic-write convention.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from . import atomic, locks
from .campaigns import paths as campaign_paths
from .campaigns import read as campaign_read
from .worlds import lifecycle as world_lifecycle
from .worlds import paths as world_paths

_FILENAME = "default_prompt.md"


def _world_path(wid: str) -> Path:
    root = world_paths.world_root(wid)
    if not world_paths.world_exists(wid):
        raise world_paths.WorldNotFound(wid)
    return root / _FILENAME


def _campaign_path(cid: str) -> Path:
    root = campaign_paths.campaign_root(cid)
    if not campaign_paths.campaign_exists(cid):
        raise campaign_paths.CampaignNotFound(cid)
    return root / _FILENAME


def _field(text: str) -> dict[str, str]:
    # Match universal-newline reads before text-mode publication. Otherwise a
    # CRLF input is returned differently by PUT/GET, and Windows can expand
    # its existing CR as well as its LF. Do not trim nonblank authored prose.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return {"text": text if text.strip() else "",
            "status": "ok" if text.strip() else "empty"}


def _read(path: Path) -> dict[str, str]:
    try:
        return _field(atomic.read_text(path))
    except FileNotFoundError:
        return _field("")
    except (OSError, UnicodeError):
        # Status only: exception messages can carry the author's text, and a
        # missing optional section is not permission to log their prose.
        return {"text": "", "status": "unreadable"}


def read_world(wid: str) -> dict[str, str]:
    """Read an existing world's optional field without creating a sidecar."""
    return _read(_world_path(wid))


def read_campaign(cid: str) -> dict[str, str]:
    """Read only an existing campaign's own field, never its inherited text."""
    return _read(_campaign_path(cid))


def _world(cid: str) -> dict:
    try:
        wid = campaign_read.read_campaign(cid)["meta"].get("world", "")
    except (OSError, UnicodeError):
        # A damaged binding cannot identify a world, but need not hide a
        # readable campaign setting or fail an already-published field save.
        return {"id": None, "text": "", "status": "unreadable"}
    try:
        world_paths.world_root(wid)
    except world_paths.WorldNotFound:
        return {"id": None, "text": "", "status": "unavailable"}
    try:
        field = read_world(wid)
    except world_paths.WorldNotFound:
        # External sync can remove the world after its binding was validated.
        # A missing optional preview must not fail an already-published save.
        return {"id": wid, "text": "", "status": "unavailable"}
    except (OSError, UnicodeError):
        return {"id": wid, "text": "", "status": "unreadable"}
    return {"id": wid, **field}


def resolve(cid: str) -> dict:
    """The campaign field plus its live world field, with independent status."""
    return {**read_campaign(cid), "world": _world(cid)}


def set_world(wid: str, text: str) -> dict[str, str]:
    """Publish one field; failure of its secondary activity hint is harmless."""
    field = _field(text)
    atomic.write_text(_world_path(wid), field["text"])
    # Publication has already succeeded; a lost ranking hint is not a failed save.
    with contextlib.suppress(Exception):
        world_lifecycle.touch(wid)
    return field


def set_campaign(cid: str, text: str) -> dict:
    """Publish the own field under the shared campaign lock, then resolve it."""
    # Reject an unsafe id before asking the lock registry to name a namespace;
    # existence is checked inside the hold covering the actual publication.
    campaign_paths.campaign_root(cid)
    with locks.campaign_lock(cid):
        field = _field(text)
        atomic.write_text(_campaign_path(cid), field["text"])
        # As above, suppress only the secondary hint, never the field write.
        with contextlib.suppress(Exception):
            campaign_read.touch_quietly(cid)
        return {**field, "world": _world(cid)}
