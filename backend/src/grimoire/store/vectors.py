"""Unit vectors for text, cached on disk by content hash.

The derived half of semantic recall (`context/semantic.py`): that module
decides *what* to embed and what to do with the scores, this one owns the
storage and the arithmetic.

Three decisions worth stating, because all three are invisible at the call
site:

**Everything is normalized on the way in, and never again.** A stored vector
has length 1, so a cosine similarity is a plain dot product — no arithmetic on
the read path at all.

**Stored as little-endian float32, not JSON.** `struct.unpack` parses a whole
vector in one C call where `json.loads` builds it a token at a time, and a
fixed-width record makes "is this file intact" a length check rather than a
per-component type check. float32 is not a precision compromise either:
round-tripping a unit vector through it moves a cosine by ~1e-9, three orders
of magnitude *less* than rounding decimal JSON to six places. The byte order
is explicit so a store synced between machines cannot read a vector back
byte-swapped.

Both decisions are about the read path, and the read path is the one that
runs: `load` covers every candidate on *every* turn, warm or not. Measured
over 500 vectors of dimension 1536 — a mid-sized world, fully cached — JSON
plus a re-derived norm on read cost 440ms per turn. This costs 58ms, on top
of ~39ms to score them, which is what a pure-python dot product costs and
where the remaining time honestly goes (numpy is not available: base
dependencies have to stay Android-installable).

**Every record carries a CRC32 of its own bytes.** A fixed-width form makes a
*truncated* file detectable by length, but a file corrupted in place unpacks
perfectly cleanly into nonsense, and nonsense ranks: a stray 1e20 component
scores 1e20 against a unit query and outranks every genuine hit.

Range-checking the score is not sufficient, and the reasoning that said it was
is worth recording because it was wrong. The claim was that both operands are
unit vectors, so a valid score lies in [-1, 1] and a corruption landing inside
that range "can only depress a score, never inflate one". It cannot: a vector
is only unit while it is intact, and a corrupted one projects up to its own
norm. Cached `[0.9, 0.9]` has norm 1.27, scores 0.9 against the query `[1, 0]`
— comfortably in range — and can outrank a genuine 0.8. Verifying the norm
instead would work, but it costs a second pass over every component of every
candidate on every turn, which is the read-path cost this file exists to
avoid. A CRC32 costs microseconds per record, catches corruption that happens
to preserve the norm as well as corruption that does not, and is checked once
at load rather than once per score.

`semantic.recall` still range-checks, as a backstop that costs one comparison
— but the integrity guarantee lives here, where it can be exact.

**The cache is derived data**, under `home()/.cache/` beside the thumbnail
cache: safe to delete, never scanned by the store, excluded from backups.
Deleting it costs one re-embed per entry and can never cost content. Entries
are keyed by ``sha256(space + text)``, where `space` identifies the embedding
space the vector lives in -- endpoint *and* model, not the model alone. A
model name is not a space: two endpoints can both serve "embedding" or
"text-embedding-3-small" and mean different weights, and vectors from
different spaces are not comparable even when their dimensionality matches, so
sharing a key between them produces silently wrong rankings rather than any
detectable error. Editing an entry's body simply maps it to a new key and the
old vector goes unreferenced — the same
never-invalidate, never-collide arrangement `thumbs.py` uses, and it inherits
the same limit: nothing prunes the strays. That is a bounded leak (one file
per distinct version of an entry that was ever activated), and pruning it
needs a reachability pass the recall path has no reason to run.

Every failure here is a miss, not an exception. The cache sits in a folder the
user may sync, truncate, or delete under the app; a bad file must cost a
re-embed and never a failed scene.
"""

from __future__ import annotations

import hashlib
import math
import struct
import zlib
from pathlib import Path

from . import atomic
from .paths import home

#: Bytes per component. Little-endian float32, spelled out rather than left to
#: the platform: a store shared between machines must read back the vector it
#: was given.
_COMPONENT = "<f"
_WIDTH = struct.calcsize(_COMPONENT)

#: File extension. Not `.json` — the contents are not text, and the distinct
#: suffix means a cache written by an older format is simply never read rather
#: than parsed as garbage.
SUFFIX = ".vec"

#: Largest record `load` will read. A vector is at most a few thousand
#: float32s; anything past this is a corrupt or mis-synced file, and reading it
#: to find that out is the problem — `read_bytes` allocates the whole file
#: before any length or checksum check can reject it, so a multi-gigabyte
#: record failed the context build with a MemoryError instead of degrading to
#: the cache miss every other bad file degrades to.
MAX_RECORD = 1 << 20

#: Record layout: a little-endian CRC32 of the payload, then the payload. A
#: record written before the checksum existed fails it and reads as a miss,
#: which costs one re-embed and needs no migration — the whole point of
#: keeping this directory derived.
_CRC = "<I"
_CRC_WIDTH = struct.calcsize(_CRC)


def _cache_dir() -> Path:
    return home() / ".cache" / "embeddings"


def _path(space: str, text: str) -> Path:
    # The space is hashed WITH the text rather than becoming a directory:
    # it carries a URL and a model id, both full of slashes, and slugifying
    # them would let two spaces share a directory. A hex digest also cannot
    # name anything outside the cache dir.
    key = hashlib.sha256(f"{space}\0{text}".encode("utf-8")).hexdigest()
    return _cache_dir() / f"{key}{SUFFIX}"


def unit(vector: list[float]) -> list[float] | None:
    """`vector` scaled to length 1, or None if it has no usable direction.

    None covers the empty vector, the zero vector, and any vector carrying a
    non-finite component — that last one matters most: nan propagates through
    the dot product, and `nan >= threshold` is False, so a broken vector would
    read as "not relevant" and never be noticed.
    """
    if not vector or any(not math.isfinite(c) for c in vector):
        return None
    norm = math.sqrt(sum(c * c for c in vector))
    if not norm or not math.isfinite(norm):
        return None
    return [c / norm for c in vector]


def dot(a: list[float], b: list[float]) -> float:
    """Dot product — the cosine similarity of two vectors from `unit`.

    Callers must pass vectors of equal length, and must treat a non-finite
    result as no score; `semantic.py` does both rather than letting a
    dimension change or a corrupted cache file quietly produce a ranking.
    """
    return sum(x * y for x, y in zip(a, b))


def load(space: str, texts: list[str]) -> dict[str, list[float]]:
    """Cached unit vectors for `texts`, keyed by text. Misses are absent.

    Runs on every turn over every candidate, so it does the least it can: read
    the bytes, check the CRC, unpack, hand them back. A file that is not a
    whole number of components is a truncated write; one whose checksum does
    not match was corrupted in place. Both read as a miss, which costs a
    re-embed rather than a wrong ranking.
    """
    out: dict[str, list[float]] = {}
    for text in texts:
        if text in out:
            continue
        path = _path(space, text)
        try:
            # Size first: `read_bytes` would allocate the whole file before
            # either check below could reject it. See MAX_RECORD.
            if path.stat().st_size > MAX_RECORD:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        payload = raw[_CRC_WIDTH:]
        if not payload or len(payload) % _WIDTH:
            continue
        if struct.unpack_from(_CRC, raw)[0] != zlib.crc32(payload):
            continue
        out[text] = list(struct.unpack(f"<{len(payload) // _WIDTH}f", payload))
    return out


def save(space: str, text: str, vector: list[float]) -> None:
    """Cache `vector` for (`space`, `text`), normalized.

    A vector with no direction is dropped rather than stored: the caller must
    see a miss and re-embed, not a cached vector that scores 0 against
    everything forever. A write that fails (read-only store, full disk) is
    dropped too — it costs one re-embed next turn, and raising would cost the
    scene the recall was meant to improve.
    """
    normalized = unit(vector)
    if normalized is None:
        return
    payload = struct.pack(f"<{len(normalized)}f", *normalized)
    payload = struct.pack(_CRC, zlib.crc32(payload)) + payload
    try:
        _cache_dir().mkdir(parents=True, exist_ok=True)
        atomic.write_bytes(_path(space, text), payload)
    except OSError:
        return


def forget(space: str, text: str) -> None:
    """Drop the cached vector for (`space`, `text`), if there is one.

    The cache has no other invalidation -- a changed entry maps to a new key
    and the old vector is simply never looked up again. This exists for the
    one case that key cannot express: a vector that is still *addressed*
    correctly but is no longer *usable*, because the endpoint began answering
    the same model id with a different dimensionality. Without eviction such a
    vector is a permanent cache hit that can never be scored, so its entry
    drops out of recall for good; forgetting it makes the next turn a miss,
    which re-embeds it and heals.
    """
    try:
        _path(space, text).unlink(missing_ok=True)
    except OSError:  # read-only store: the entry stays unscorable, nothing worse
        return
