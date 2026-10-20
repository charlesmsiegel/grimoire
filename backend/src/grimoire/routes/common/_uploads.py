"""common: uploads responsibilities."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from fastapi import HTTPException, Request


# ---- uploaded archives ----
@contextlib.asynccontextmanager
async def _spooled_upload(request: Request, cap: int, too_large: str):
    """Stream the request body to a temp file, yield its path, then remove it.

    Both zip-import routes (module packs, world bundles) need the same three
    things and get them here rather than each writing their own: the
    declared-length pre-check that refuses an oversized upload before a byte is
    read, the running count that refuses one whose Content-Length lied, and the
    unlink on every exit path. A world bundle runs to a gigabyte, so the body
    is never held in memory.
    """
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > cap:
        raise HTTPException(status_code=413, detail=too_large)
    fd, tmp_name = tempfile.mkstemp(suffix=".zip")
    try:
        total = 0
        # atomic-ok: a system temp file for the uploaded archive, not a store
        # record; read by the importer and unlinked in the finally below
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                total += len(chunk)
                if total > cap:
                    raise HTTPException(status_code=413, detail=too_large)
                f.write(chunk)
        yield Path(tmp_name)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
