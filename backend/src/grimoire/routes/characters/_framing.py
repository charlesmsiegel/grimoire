"""characters: framing responsibilities."""

from __future__ import annotations

import json

from ._router import router as router


def _sse(payload: dict) -> str:
    """One SSE data frame. Local, like `runs.sse` and `streaming._sse`: this
    module is not allowed to import either (`test_import_guard`), and the four
    streaming routes here were each spelling the frame out inline."""
    return f"data: {json.dumps(payload)}\n\n"
