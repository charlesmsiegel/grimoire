"""Small helpers used by API routers."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel


def to_payload(obj: Any) -> Any:
    """Best-effort conversion of a service return value into JSON-friendly data.

    Pydantic models are dumped via ``model_dump``. Dataclasses are converted to
    ``dict``. Iterables of either are converted element-wise. Plain dicts and
    primitives are returned unchanged. ``datetime``/``date`` are serialised to
    ISO 8601 so a nested datetime inside e.g. a service-returned dataclass
    doesn't fall through to the bare ``return obj`` branch and produce a
    response shape that varies with Pydantic's downstream encoder.
    """
    if obj is None:
        return None
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, list | tuple):
        return [to_payload(item) for item in obj]
    if isinstance(obj, dict):
        return {key: to_payload(value) for key, value in obj.items()}
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(mode="json")
        except Exception:
            pass
    if hasattr(obj, "__dataclass_fields__"):
        return {field: to_payload(getattr(obj, field)) for field in obj.__dataclass_fields__}
    return obj


def map_lookup_errors(exc: Exception) -> HTTPException:
    """Translate well-known service-layer exceptions to HTTP errors.

    HTTPException is passed through unchanged so service-layer code that
    explicitly raises e.g. ``HTTPException(403)`` keeps its status — without
    this branch the generic Exception path would reclassify it as 500.
    """
    if isinstance(exc, HTTPException):
        return exc
    name = type(exc).__name__.lower()
    detail = str(exc) or name

    # Walk the cause chain first: the orchestrator wraps gateway errors in
    # OrchestratorError "from exc.cause", and without this the name-based
    # match below forces 409 even when the real cause is "no LLM provider
    # configured" (a 503 service-not-ready signal).
    cause: BaseException | None = exc.__cause__ or exc.__context__
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        cname = type(cause).__name__.lower()
        if "routenotfound" in cname or "providernotfound" in cname:
            return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)
        cause = cause.__cause__ or cause.__context__

    # RouteNotFoundError / ProviderNotFoundError are gateway "no provider
    # configured" signals — must be checked before the generic "notfound"
    # → 404 branch below, since their class names also contain "notfound".
    if "routenotfound" in name or "providernotfound" in name:
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)
    if isinstance(exc, KeyError) or "notfound" in name or "unknown" in name:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    if isinstance(exc, ValueError) or "validation" in name or "invalid" in name:
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    # Orchestrator/state-machine preconditions ("no active scene", "campaign
    # already ended", etc.) are operator-actionable, not server bugs — return
    # 409 so the UI can show the message instead of a generic 500.
    if "orchestrator" in name or "state" in name or "conflict" in name:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    # NoBackendAvailableError ("install an imagegen plugin first") is a
    # service-not-ready signal, not a server bug — return 503 so the UI can
    # render a "configure a backend" prompt instead of an opaque 500.
    if "nobackendavailable" in name:
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)


__all__ = ["map_lookup_errors", "to_payload"]
