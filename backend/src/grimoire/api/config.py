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

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from grimoire import config as config_module
from grimoire.files import load_yaml, write_yaml

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config")


_DEFAULT_BACKUP = {
    "schedule": "off",
    "retention_days": 30,
    "location": "data/backups",
}

_DEFAULT_LLM_DEFAULTS = {
    "heavy": "deepseek.deepseek-v4-pro",
    "light": "deepseek.deepseek-v4-flash",
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


class LLMDefaultsPayload(BaseModel):
    """App-wide default Heavy and Light routes used as the seed for
    new campaigns' ``model_tiers`` block.
    """

    heavy: str = "deepseek.deepseek-v4-pro"
    light: str = "deepseek.deepseek-v4-flash"


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
            await asyncio.to_thread(lambda: Path(candidate).expanduser())
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"invalid library_path: {exc}") from exc
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


@router.get("/llm-defaults")
async def get_llm_defaults() -> Any:
    raw = _read_app_yaml()
    block = raw.get("llm_defaults") if isinstance(raw.get("llm_defaults"), dict) else {}
    return {
        "heavy": str(block.get("heavy") or _DEFAULT_LLM_DEFAULTS["heavy"]),
        "light": str(block.get("light") or _DEFAULT_LLM_DEFAULTS["light"]),
    }


@router.put("/llm-defaults")
async def set_llm_defaults(payload: LLMDefaultsPayload) -> Any:
    from grimoire.llm_gateway.routing import Route

    # Validate up-front so a half-written app.yaml isn't possible.
    for value in (payload.heavy, payload.light):
        try:
            Route.parse(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    data = _read_app_yaml()
    data["llm_defaults"] = {"heavy": payload.heavy, "light": payload.light}
    write_yaml(_app_yaml_path(), data)
    return data["llm_defaults"]


def _yaml_path(name: str) -> Path:
    return config_module.settings.data_root / "config" / name


def _read_yaml_safe(name: str) -> dict[str, Any]:
    path = _yaml_path(name)
    if not path.exists():
        return {}
    try:
        raw = load_yaml(path)
    except Exception:
        logger.warning("%s read failed; treating as empty", name, exc_info=True)
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_yaml_safe(name: str, data: dict[str, Any]) -> None:
    path = _yaml_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_yaml(path, data)


class LibrarySettingsPatch(BaseModel):
    embed_on_index: bool | None = None


@router.get("/state-store/library")
async def get_library_settings() -> Any:
    ss = _read_yaml_safe("state_store.yaml")
    ss_lib = ss.get("library") if isinstance(ss.get("library"), dict) else {}
    return {
        "embed_on_index": bool(ss_lib.get("embed_on_index", False)),
    }


@router.patch("/state-store/library")
async def patch_library_settings(payload: LibrarySettingsPatch) -> Any:
    if payload.embed_on_index is not None:
        ss = _read_yaml_safe("state_store.yaml")
        ss_lib = ss.get("library") if isinstance(ss.get("library"), dict) else {}
        ss_lib["embed_on_index"] = payload.embed_on_index
        ss["library"] = ss_lib
        try:
            _write_yaml_safe("state_store.yaml", ss)
        except Exception as exc:
            logger.exception("state_store.yaml write failed")
            raise HTTPException(status_code=500, detail=f"failed to persist: {exc}") from exc
    return await get_library_settings()


class EmbeddingDefaultsPatch(BaseModel):
    route: str | None = None


@router.get("/embedding-defaults")
async def get_embedding_defaults() -> Any:
    raw = _read_yaml_safe("state_store.yaml")
    lib = raw.get("library") if isinstance(raw.get("library"), dict) else {}
    return {"route": lib.get("embedding_provider") or None}


@router.patch("/embedding-defaults")
async def patch_embedding_defaults(payload: EmbeddingDefaultsPatch) -> Any:
    if "route" in payload.model_fields_set:
        raw = _read_yaml_safe("state_store.yaml")
        lib = raw.get("library") if isinstance(raw.get("library"), dict) else {}
        if payload.route is not None:
            lib["embedding_provider"] = payload.route
        else:
            lib.pop("embedding_provider", None)
        raw["library"] = lib
        try:
            _write_yaml_safe("state_store.yaml", raw)
            app = _read_app_yaml()
            app_embed = (
                app.get("embedding_defaults")
                if isinstance(app.get("embedding_defaults"), dict)
                else {}
            )
            if payload.route is not None:
                app_embed["route"] = payload.route
            else:
                app_embed.pop("route", None)
            app["embedding_defaults"] = app_embed
            write_yaml(_app_yaml_path(), app)
        except Exception as exc:
            logger.exception("embedding defaults write failed")
            raise HTTPException(status_code=500, detail=f"failed to persist: {exc}") from exc
    return await get_embedding_defaults()


class ImagegenDefaultsPatch(BaseModel):
    backend: str | None = None


@router.get("/imagegen-defaults")
async def get_imagegen_defaults() -> Any:
    raw = _read_yaml_safe("imagegen.yaml")
    return {"backend": raw.get("default_backend") or None}


@router.patch("/imagegen-defaults")
async def patch_imagegen_defaults(payload: ImagegenDefaultsPatch) -> Any:
    if "backend" in payload.model_fields_set:
        try:
            ig_raw = _read_yaml_safe("imagegen.yaml")
            if payload.backend is not None:
                ig_raw["default_backend"] = payload.backend
            else:
                ig_raw.pop("default_backend", None)
            _write_yaml_safe("imagegen.yaml", ig_raw)
        except Exception as exc:
            logger.exception("imagegen defaults write failed")
            raise HTTPException(status_code=500, detail=f"failed to persist: {exc}") from exc
    return await get_imagegen_defaults()


class BrowseFilesResponse(BaseModel):
    parent: str
    entries: list[dict[str, Any]]


def _list_directory(base: Path, glob: str) -> list[dict[str, Any]]:
    """List picker entries under ``base`` (blocking; run via to_thread)."""
    entries: list[dict[str, Any]] = []
    for child in sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            entries.append({"name": child.name, "path": str(child), "is_dir": True})
        elif child.match(glob):
            entries.append({"name": child.name, "path": str(child), "is_dir": False})
    return entries


@router.get("/browse-files")
async def browse_files(
    directory: str | None = None,
    glob: str = "*.gguf",
) -> BrowseFilesResponse:
    """List files and directories for a server-side file picker."""
    base = await asyncio.to_thread(lambda: Path(directory).resolve() if directory else Path.home())

    if not await asyncio.to_thread(base.is_dir):
        raise HTTPException(status_code=400, detail=f"Not a directory: {base}")

    try:
        entries = await asyncio.to_thread(_list_directory, base, glob)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"Permission denied: {base}") from exc

    return BrowseFilesResponse(parent=str(base.parent), entries=entries)


@router.get("/gguf-introspect")
async def gguf_introspect(path: str) -> Any:
    """Read metadata from a GGUF file without loading the model."""
    from grimoire.gguf import GGUFError, introspect

    resolved = await asyncio.to_thread(lambda: Path(path).resolve())
    if not await asyncio.to_thread(resolved.is_file):
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")
    try:
        return await asyncio.to_thread(introspect, resolved)
    except GGUFError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


__all__ = ["router"]
