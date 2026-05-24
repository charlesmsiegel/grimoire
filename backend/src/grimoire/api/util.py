"""Small helpers used by API routers."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import HTTPException
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

    Uses the ``http_status`` class attribute on domain exceptions. Walks
    the cause chain so wrapped exceptions (e.g. OrchestratorError wrapping
    a RouteNotFoundError) resolve to the inner exception's status.
    """
    if isinstance(exc, HTTPException):
        return exc

    detail = str(exc) or type(exc).__name__

    # Walk the cause chain: the orchestrator wraps gateway errors in
    # OrchestratorError "from exc.cause"; the inner exception's status
    # is more specific than the wrapper's.
    cause: BaseException | None = exc.__cause__ or exc.__context__
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        status_code = getattr(cause, "http_status", None)
        if status_code is not None and status_code != 500:
            return HTTPException(status_code=status_code, detail=detail)
        cause = cause.__cause__ or cause.__context__

    status_code = getattr(exc, "http_status", 500)
    return HTTPException(status_code=status_code, detail=detail)


__all__ = ["map_lookup_errors", "to_payload"]
