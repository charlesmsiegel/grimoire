"""App-level configuration routes.

The Frontend's `AppSettings` Library + Backup tabs talk to this surface.
Spec 14 §App settings tabs and §16 of the remaining-design spec call for
``GET /api/config/app`` and ``PATCH /api/config/app`` to read and partially
update the on-disk ``data_root/config/app.yaml`` file.

The endpoint exposes (and persists) two keys today:

  - ``library_path``: filesystem path scanned by the library service.
  - ``backup``: ``{schedule, retention_days, location}``.

The shape is forwards-compatible: extra keys round-trip unchanged so future
tabs can add to the same file without touching this router.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from grimoire import config as config_module
from grimoire.files import load_yaml, write_yaml

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config")


_DEFAULT_BACKUP = {
    "schedule": "off",
    "retention_days": 30,
    "location": "data/backups",
}


def _app_yaml_path() -> Path:
    return config_module.settings.data_root / "config" / "app.yaml"


def _default_library_path() -> str:
    # `data_root / "library"` is where the library service actually scans; the
    # Frontend can display this as the default until the user picks a path.
    return str(config_module.settings.data_root / "library")


def _read_app_yaml() -> dict[str, Any]:
    path = _app_yaml_path()
    if not path.exists():
        return {}
    try:
        raw = load_yaml(path)
    except Exception:
        logger.warning("app.yaml read failed; treating as empty", exc_info=True)
        return {}
    return raw if isinstance(raw, dict) else {}


def _shape_response(data: dict[str, Any]) -> dict[str, Any]:
    """Return the dict in the canonical response shape."""
    library_path = data.get("library_path")
    if not isinstance(library_path, str) or not library_path:
        library_path = _default_library_path()
    backup_raw = data.get("backup") if isinstance(data.get("backup"), dict) else {}
    backup = {**_DEFAULT_BACKUP, **backup_raw}
    # Coerce retention_days to int for stable typing on the wire.
    try:
        backup["retention_days"] = int(backup.get("retention_days", 30))
    except (TypeError, ValueError):
        backup["retention_days"] = 30
    return {"library_path": library_path, "backup": backup}


class BackupPayload(BaseModel):
    schedule: str | None = None
    retention_days: int | None = None
    location: str | None = None


class AppConfigPatch(BaseModel):
    library_path: str | None = None
    backup: BackupPayload | None = None


@router.get("/app")
async def get_app_config() -> Any:
    return _shape_response(_read_app_yaml())


@router.patch("/app")
async def patch_app_config(payload: AppConfigPatch) -> Any:
    data = _read_app_yaml()

    if payload.library_path is not None:
        candidate = payload.library_path.strip()
        if not candidate:
            raise HTTPException(status_code=422, detail="library_path must be non-empty")
        # Light-touch validation: don't require the directory to exist yet, but
        # reject relative paths with embedded NULs / clearly invalid input.
        if "\x00" in candidate:
            raise HTTPException(status_code=422, detail="library_path contains a null byte")
        try:
            Path(candidate).expanduser()
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid library_path: {exc}"
            ) from exc
        data["library_path"] = candidate

    if payload.backup is not None:
        current = data.get("backup") if isinstance(data.get("backup"), dict) else {}
        merged = {**_DEFAULT_BACKUP, **current}
        patch = payload.backup.model_dump(exclude_unset=True)
        if "retention_days" in patch and patch["retention_days"] is not None:
            try:
                patch["retention_days"] = int(patch["retention_days"])
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=422, detail=f"invalid retention_days: {exc}"
                ) from exc
            if patch["retention_days"] < 0:
                raise HTTPException(status_code=422, detail="retention_days must be >= 0")
        if "schedule" in patch and patch["schedule"] is not None:
            allowed = {"off", "hourly", "daily", "weekly"}
            if patch["schedule"] not in allowed:
                raise HTTPException(
                    status_code=422,
                    detail=f"schedule must be one of {sorted(allowed)}",
                )
        merged.update({k: v for k, v in patch.items() if v is not None})
        data["backup"] = merged

    try:
        write_yaml(_app_yaml_path(), data)
    except Exception as exc:
        logger.exception("app.yaml write failed")
        raise HTTPException(status_code=500, detail=f"failed to persist app.yaml: {exc}") from exc

    return _shape_response(data)


__all__ = ["router"]
