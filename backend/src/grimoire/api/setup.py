"""First-run setup status.

The startup wizard surfaces on the client whenever this endpoint reports
``completed=false``. Completion is persisted as a single sentinel file at
``{data_root}/.setup-complete`` so the answer is machine-local and
survives across browsers (the existing localStorage-based prefs do not).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from grimoire import config as _config

router = APIRouter()

_SENTINEL = ".setup-complete"


def _sentinel_path() -> Path:
    return _config.settings.data_root / _SENTINEL


class SetupStatus(BaseModel):
    completed: bool
    data_root: str


class SetupCompleteRequest(BaseModel):
    completed: bool = True


@router.get("/setup/status", response_model=SetupStatus)
def get_status() -> SetupStatus:
    p = _sentinel_path()
    return SetupStatus(completed=p.is_file(), data_root=str(_config.settings.data_root))


@router.post("/setup/status", response_model=SetupStatus)
def set_status(body: SetupCompleteRequest) -> SetupStatus:
    p = _sentinel_path()
    if body.completed:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch(exist_ok=True)
    else:
        if p.exists():
            p.unlink()
    return SetupStatus(completed=p.is_file(), data_root=str(_config.settings.data_root))
