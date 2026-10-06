"""Image-collection state for tests, in each format, built without `publish`.

`format1` is the only way a test builds format-1 state: the shape an older
grimoire left on disk, which the store must keep reading. Members are placed in
the world library through `world_images.put_image` under the name their bytes
hash to, and the manifest is hand-written, so these tests do not depend on what
`publish` and `put_member` write today.

`format2` hand-writes a format-2 manifest over ingested objects, for the same
reason in the other direction.
"""

from __future__ import annotations

import hashlib
import json

from grimoire.store import fetch, image_store, world_images
from grimoire.store import image_collections as collections


def _write(wid: str, cid: str, fmt: int, members: list[str]) -> None:
    path = collections.manifest_path(wid, cid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": fmt, "members": members}, indent=2) + "\n",
                    encoding="utf-8")


def member_name(data: bytes) -> str:
    return collections.MEMBER_PREFIX + hashlib.sha256(data).hexdigest()


def format1(wid: str, cid: str, *datas: bytes) -> list[str]:
    """A format-1 collection of `datas`, in order; returns the member names."""
    names = []
    for data in datas:
        name = member_name(data)
        world_images.put_image(wid, name, data, fetch.sniff_ext(data) or "png")
        names.append(name)
    _write(wid, cid, 1, names)
    return names


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
