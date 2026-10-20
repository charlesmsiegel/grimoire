"""Device/root/age-bound dry-run tokens and atomic single-use consumption."""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import uuid
from pathlib import Path

from .. import atomic, image_hash
from . import _contracts, _paths


def _issue_token(root: Path, report: dict, device: str, now: float) -> None:
    ids = [row["id"] for row in report["collectable"]]
    blobs = [row["blob"] for row in report["collectable_blobs"]]
    if not ids and not blobs and not report["outbox"]:
        return                      # waiting files alone earn a token: they drain
    token = secrets.token_hex(16)
    path = _paths.token_path(root, token)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps({
        "format": _contracts.FORMAT, "token": token, "ids": ids, "blobs": blobs,
        "root": str(root), "device": device, "scanned_at": now,
        "grace_hours": _contracts.GRACE_HOURS, "run_id": report["run_id"]}, indent=1) + "\n")
    report["token"] = token
    report["token_expires_at"] = now + _contracts.TOKEN_HOURS * 3600.0
    _prune_tokens(root, device, now)


def _read_token(root: Path, token: object, device: str, now: float
                ) -> tuple[dict | None, str]:
    """The token's record when it may be used here and now, else None and
    why. Read-only: a token another device issued is never consumed here."""
    try:
        path = _paths.token_path(root, token)          # type: ignore[arg-type]
    except ValueError:
        return None, "not a token"
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None, "unknown or already used"
    if not isinstance(rec, dict) or rec.get("format") != _contracts.FORMAT or rec.get("token") != token:
        return None, "unknown or already used"
    if rec.get("device") != device:
        return None, "issued on another device"
    if rec.get("root") != str(root):
        return None, "issued for another store"
    at, grace = _contracts._as_number(rec.get("scanned_at")), _contracts._as_number(rec.get("grace_hours"))
    if at is None or grace != _contracts.GRACE_HOURS:
        return None, "unknown or already used"
    if at > now:
        return None, "issued in the future (clock skew)"
    if now - at > _contracts.TOKEN_HOURS * 3600.0:
        return None, "expired"
    ids, blobs = rec.get("ids"), rec.get("blobs")
    if (not isinstance(ids, list) or not all(image_hash.is_image_id(i) for i in ids)
            or not isinstance(blobs, list)
            or not all(isinstance(b, str) and _paths._split_key(b) for b in blobs)):
        return None, "unknown or already used"
    return rec, ""


def _claim_token(root: Path, token: str, rec: dict) -> bool:
    """Consume the token, atomically: of two claims, one rename wins."""
    path = _paths.token_path(root, token)
    claimed = path.with_name(f".{token}.{uuid.uuid4().hex}.claimed")
    try:
        os.replace(path, claimed)
    except OSError:
        return False
    try:
        same = json.loads(claimed.read_text(encoding="utf-8")) == rec
    except (OSError, ValueError, RecursionError):
        same = False
    with contextlib.suppress(OSError):
        claimed.unlink()
    return same


def _prune_tokens(root: Path, device: str, now: float) -> None:
    """Drop this device's expired tokens. Fail-soft, and nobody else's."""
    try:
        entries = list(_paths._tokens_dir(root).iterdir())
    except OSError:
        return
    for p in entries:
        stem, dot, ext = p.name.rpartition(".")
        if not dot or ext != "json" or not _contracts._TOKEN.fullmatch(stem):
            continue
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            if (isinstance(rec, dict) and rec.get("device") == device
                    and _contracts._number(rec.get("scanned_at"))
                    and now - rec["scanned_at"] > _contracts.TOKEN_HOURS * 3600.0):
                p.unlink()
        except (OSError, ValueError, RecursionError):
            continue
