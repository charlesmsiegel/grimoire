from fastapi import APIRouter
from pydantic import BaseModel

from grimoire import __version__
from grimoire.config import settings

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    version: str
    data_root: str


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        data_root=str(settings.data_root),
    )
