"""Immutable historical prompts, addressed by response ID and content digest.

The response ledger holds small references; reading or updating a round never
loads every earlier prompt. Content-addressed files also keep a new roll's
continuation context from overwriting a snapshot that recovery may still need.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import atomic, locks
from .campaigns import paths as campaign_paths

_ID = re.compile(r"[0-9a-f]{32}")
_REFERENCE = re.compile(r"[0-9a-f]{32}/(?:primary|resume)-([0-9a-f]{64})\.json")


def _path(cid: str, reference: str) -> Path:
    if not isinstance(reference, str) or _REFERENCE.fullmatch(reference) is None:
        raise ValueError("invalid response snapshot reference")
    return campaign_paths.campaign_root(cid) / "response-prompts" / reference


def write(cid: str, rid: str, kind: str, snapshot: dict) -> str:
    """Publish once; retries of identical evidence return the same reference."""
    if not isinstance(rid, str) or _ID.fullmatch(rid) is None:
        raise ValueError("invalid response ID")
    if kind not in ("primary", "resume"):
        raise ValueError("invalid response snapshot kind")
    if not isinstance(snapshot, dict):
        raise ValueError("invalid response snapshot")
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    reference = f"{rid}/{kind}-{digest}.json"
    with locks.campaign_lock(cid):
        target = _path(cid, reference)
        if target.exists():
            # Existing immutable evidence may have been externally edited.
            # Replacing it silently would hide corruption in historical context.
            read(cid, reference)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic.write_bytes(target, payload)
    return reference


def read(cid: str, reference: str) -> dict:
    """Fail closed if stored bytes no longer match their historical identity."""
    target = _path(cid, reference)
    payload = target.read_bytes()
    expected = reference.rsplit("-", 1)[1][:-5]
    if hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError("response snapshot integrity check failed")
    snapshot = json.loads(payload)
    if not isinstance(snapshot, dict):
        raise ValueError("invalid response snapshot")
    return snapshot


def remove(cid: str, reference: str) -> None:
    """Remove one known snapshot when its scene is deleted; safe to retry."""
    target = _path(cid, reference)
    with locks.campaign_lock(cid):
        target.unlink(missing_ok=True)
