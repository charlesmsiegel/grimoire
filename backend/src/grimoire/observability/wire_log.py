"""Full request/response wire logging to the terminal.

When enabled (the default), every outbound LLM completion, embedding, and
image-generation request — and its response — is printed in full to a
dedicated ``grimoire.wire`` logger that writes to stdout. It exists so a
developer can see exactly what the app sends to, and receives from, model
and image providers.

Disable by setting ``GRIMOIRE_WIRE_LOG=0`` (also accepts ``false``/``no``/``off``).

The logger owns its own stdout handler with ``propagate=False`` so output
reaches the terminal regardless of how the host configures the root logger
(uvicorn, pytest, ``scripts/run.sh``) and is never duplicated through the
root handler.

Payloads are JSON-encoded. Binary fields (image bytes, init images) are
replaced with a ``<bytes: N>`` placeholder so the terminal isn't flooded
with base64, and raw embedding vectors are summarized (count + dimensions)
by callers rather than dumped float-by-float.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger("grimoire.wire")

_configured = False
_DISABLED = {"0", "false", "no", "off", ""}


def enabled() -> bool:
    """True unless ``GRIMOIRE_WIRE_LOG`` is set to a falsey value."""
    return os.environ.get("GRIMOIRE_WIRE_LOG", "1").strip().lower() not in _DISABLED


class _StdoutHandler(logging.Handler):
    """Write each record to the *current* ``sys.stdout``.

    Resolving the stream at emit time (rather than binding it once at
    construction like ``logging.StreamHandler``) keeps output flowing to
    the real terminal in production while staying compatible with pytest's
    per-test stdout capture, which swaps and closes the stream between
    tests.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stream = sys.stdout
            stream.write(self.format(record) + "\n")
            stream.flush()
        except Exception:
            self.handleError(record)


def _ensure_configured() -> None:
    global _configured
    if _configured:
        return
    if not logger.handlers:
        handler = _StdoutHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s [wire] %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    _configured = True


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, bytes | bytearray):
        return f"<bytes: {len(obj)}>"
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_sanitize(v) for v in obj]
    return obj


def _jsonable(payload: Any) -> Any:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump()
    return _sanitize(payload)


def _dump(payload: Any) -> str:
    try:
        return json.dumps(
            _jsonable(payload),
            ensure_ascii=False,
            indent=2,
            default=str,
            sort_keys=True,
        )
    except Exception:  # never let logging break a real call
        return repr(payload)


def _meta(meta: dict[str, Any]) -> str:
    return " ".join(f"{k}={v}" for k, v in meta.items() if v is not None)


def log_request(channel: str, *, payload: Any, **meta: Any) -> None:
    """Print a provider request payload in full. ``channel`` is e.g. ``"llm.complete"``."""
    if not enabled():
        return
    try:
        _ensure_configured()
        logger.info(">> %s request | %s\n%s", channel, _meta(meta), _dump(payload))
    except Exception:
        pass


def log_response(channel: str, *, payload: Any, **meta: Any) -> None:
    """Print a provider response payload in full."""
    if not enabled():
        return
    try:
        _ensure_configured()
        logger.info("<< %s response | %s\n%s", channel, _meta(meta), _dump(payload))
    except Exception:
        pass


def log_error(channel: str, *, error: Any, **meta: Any) -> None:
    """Print a failed provider call so the failure is visible in the terminal."""
    if not enabled():
        return
    try:
        _ensure_configured()
        logger.info("!! %s error | %s\n%s", channel, _meta(meta), error)
    except Exception:
        pass


__all__ = ["enabled", "log_error", "log_request", "log_response"]
