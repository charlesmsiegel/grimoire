"""Image blobs: the store's one media sniffer and the blob value it writes.

The file extension of a blob is decided here from magic bytes, never from a caller's claim,
so a blob's path says what its bytes are. Only stdlib and ``.errors`` are imported: the
store's runtime import set must not grow beyond its own modules.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .errors import InvalidBlob

MAX_BLOB_BYTES = 32 * 1024 * 1024
#: Blob extension -> media type. ``grimoire.images`` keeps an equal copy (it may not import the store).
MEDIA = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


def sniff(data: bytes) -> str | None:
    """The blob extension of ``data`` by magic bytes, or ``None`` for anything else."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


@dataclass(frozen=True)
class ImageBlob:
    """Exact image bytes, content-addressed: ``assets/blobs/<sha[:2]>/<sha256>.<ext>``."""

    data: bytes = field(repr=False)
    sha256: str = field(init=False)
    ext: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.data, (bytes, bytearray)):
            raise InvalidBlob("blob data must be bytes", "unsupported_media")
        data = bytes(self.data)
        if not data:
            raise InvalidBlob("blob data is empty", "empty")
        if len(data) > MAX_BLOB_BYTES:
            raise InvalidBlob(f"blob data is larger than {MAX_BLOB_BYTES} bytes", "too_large")
        ext = sniff(data)
        if ext is None:
            raise InvalidBlob("blob data is not a PNG, JPEG, GIF or WebP image", "unsupported_media")
        object.__setattr__(self, "data", data)
        object.__setattr__(self, "sha256", hashlib.sha256(data).hexdigest())
        object.__setattr__(self, "ext", ext)

    @property
    def media_type(self) -> str:
        return MEDIA[self.ext]

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def etag(self) -> str:
        return "sha256:" + self.sha256


@dataclass(frozen=True)
class BlobCopy:
    """Marks a lazily loaded blob copy (``Transaction.put_blob_exact``): its name and size, no bytes."""

    sha256: str
    ext: str
    size: int
