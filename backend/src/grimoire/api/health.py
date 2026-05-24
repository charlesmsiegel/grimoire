from fastapi import APIRouter, Request
from pydantic import BaseModel

from grimoire import __version__
from grimoire.config import settings

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    version: str
    data_root: str
    # Set when mechanics/plugins rescan failed during lifespan startup.
    # Without these, the empty service still wired into the container
    # made endpoints look like the user hadn't installed any modules.
    mechanics_rescan_error: str | None = None
    plugins_rescan_error: str | None = None


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    container = getattr(request.app.state, "container", None)
    mechanics_err = container.mechanics_rescan_error if container is not None else None
    plugins_err = container.plugins_rescan_error if container is not None else None
    return HealthResponse(
        status="degraded" if (mechanics_err or plugins_err) else "ok",
        version=__version__,
        data_root=str(settings.data_root),
        mechanics_rescan_error=mechanics_err,
        plugins_rescan_error=plugins_err,
    )
