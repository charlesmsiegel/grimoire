"""Revision leases keep reference roots alive through readers and staged writes.

Leases are created under publication sidecar locks and removed on normal exit.
A crashed operation may leave a conservative lease; cleanup never guesses that
an exporter on another process has finished merely because time has passed.
"""

import json
import uuid
from contextlib import contextmanager, suppress
from pathlib import Path

from .. import assets, atomic


@contextmanager
def hold(directory: Path, revision: str | None):
    if revision is None:
        yield
        return
    path = directory / "pins" / (uuid.uuid4().hex + ".json")
    with assets.sidecar_locks_held((directory, "manifest.json")):
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_text(path, json.dumps({"revision": revision}))
    try:
        yield
    finally:
        with suppress(OSError):
            path.unlink(missing_ok=True)
