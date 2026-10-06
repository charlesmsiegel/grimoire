"""Image-collection state for tests, in each format, built without `publish`.

`format1` is the only way a test builds format-1 state: the shape an older
grimoire left on disk, which the store must keep reading. Members are placed in
the world library through `world_images.put_image` under the name their bytes
hash to, and the manifest is hand-written, so these tests do not depend on what
`publish` and `put_member` write today.

`format2` hand-writes a format-2 manifest over ingested objects, for the same
reason in the other direction.

`format1_journal` is the harvest journal an older grimoire left mid-flight:
format 1, its members library names.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from grimoire.store import fetch, image_store, world_images
from grimoire.store import image_collection_imports as imports
from grimoire.store import image_collections as collections


def _write(wid: str, cid: str, fmt: int, members: list[str]) -> None:
    path = collections.manifest_path(wid, cid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": fmt, "members": members}, indent=2) + "\n",
                    encoding="utf-8")


def member_name(data: bytes) -> str:
    return collections.MEMBER_PREFIX + hashlib.sha256(data).hexdigest()


def place(wid: str, *datas: bytes) -> list[str]:
    """Format-1 members of `datas` in the world library, under the names their
    bytes hash to, with no manifest naming them (a harvest still sampling)."""
    names = []
    for data in datas:
        name = member_name(data)
        world_images.put_image(wid, name, data, fetch.sniff_ext(data) or "png")
        names.append(name)
    return names


def format1(wid: str, cid: str, *datas: bytes) -> list[str]:
    """A format-1 collection of `datas`, in order; returns the member names."""
    names = place(wid, *datas)
    _write(wid, cid, 1, names)
    return names


def format1_journal(wid: str, source_url: str, cid: str, names: list[str], *,
                    accepted: bool = False) -> Path:
    """A format-1 harvest journal of `names` for `source_url`, publishing to
    `cid`; returns its path. The members are whatever the test placed."""
    job_id = hashlib.sha256(source_url.encode()).hexdigest()
    path = imports.job_path(wid, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "format": 1, "job_id": job_id, "source_url": source_url, "collection_id": cid,
        "members": list(names), "accepted": accepted,
        "totals": {"requests": len(names), "valid": len(names), "added": len(names),
                   "duplicates": 0, "failed": 0},
        "stop": "accepted" if accepted else "limit"}, indent=2) + "\n", encoding="utf-8")
    return path


def format2(wid: str, cid: str, *datas: bytes) -> list[str]:
    """A format-2 collection of `datas`, in order; returns the member ids."""
    ids = [image_store.ingest(data, fetch.sniff_ext(data) or "png").id for data in datas]
    _write(wid, cid, 2, ids)
    return ids


def write_manifest(wid: str, cid: str, raw: object) -> None:
    """Any manifest at all, valid or not."""
    path = collections.manifest_path(wid, cid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw), encoding="utf-8")
