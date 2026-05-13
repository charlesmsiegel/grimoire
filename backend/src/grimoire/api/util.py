"""Small helpers used by API routers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel


def to_payload(obj: Any) -> Any:
    """Best-effort conversion of a service return value into JSON-friendly data.

    Pydantic models are dumped via ``model_dump``. Dataclasses are converted to
    ``dict``. Iterables of either are converted element-wise. Plain dicts and
    primitives are returned unchanged.
    """
    if obj is None:
        return None
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
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
    """Translate well-known service-layer exceptions to HTTP errors."""
    name = type(exc).__name__.lower()
    detail = str(exc) or name
    if isinstance(exc, KeyError) or "notfound" in name or "unknown" in name:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    if isinstance(exc, ValueError) or "validation" in name or "invalid" in name:
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)


__all__ = ["map_lookup_errors", "to_payload"]
