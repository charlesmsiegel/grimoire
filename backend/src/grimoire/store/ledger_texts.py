"""The text seam under the five continuity ledger readers (roadmap 04 section 3.3).

`plot.json`, `commitments.json`, `events.json`, `continuity.json` and
`continuity_candidates.json` are each read by one function in their own module,
and every question the continuity view answers -- the effective records, the
live canon, links, the pending findings -- reaches its file only through that
one read. Each read asks `text(cid, name, path)` here for the file's text, and
parses it with its module's `*_from_text` twin, so there is exactly one parser
per file.

`text` reads the path, unless the caller has opened `feeding(cid, files)` for
that campaign: then it answers from the bytes it was handed and opens nothing.
That is how the overview's `continuity_summary` runs today's code over the
exact bytes its key was computed from (spec 04 section 3.3: computes receive
bytes, never a path), and why the live path and the byte-fed path cannot
disagree -- they are the same code, fed differently.

A leaf: stdlib only, so every reader can import it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

#: The five files, by the name each reader asks with.
NAMES = ("plot", "commitments", "events", "continuity", "candidates")

_fed: ContextVar[tuple[str, Mapping[str, bytes | None]] | None] = ContextVar(
    "grimoire_ledger_texts", default=None)


def decode(data: bytes) -> str:
    """`data` as `Path.read_text(encoding="utf-8")` would have returned it:
    a strict UTF-8 decode (raising `UnicodeDecodeError` where that read
    raises), then universal newlines."""
    return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


@contextmanager
def feeding(cid: str, files: Mapping[str, bytes | None]) -> Iterator[None]:
    """Answer the five readers for `cid` from `files` (name -> bytes, or None
    for an absent file) while this is open. Every name in `NAMES` must be
    present: a reader asking for one that was not handed is a bug, and raises
    rather than quietly reading the disk."""
    missing = [n for n in NAMES if n not in files]
    if missing:
        raise ValueError(f"ledger texts not handed: {missing}")
    token = _fed.set((cid, dict(files)))
    try:
        yield
    finally:
        _fed.reset(token)


def text(cid: str, name: str, path: Path) -> str | None:
    """The text of `cid`'s ledger file `name`, or None when it is absent.

    Fed: the bytes `feeding` was handed, decoded as `read_text` would decode
    them. Otherwise: `path`, read with universal newlines. Raises what that
    read raises (`OSError`, `UnicodeDecodeError`) in either case, at the same
    point, so a reader's own handling of a failed read is unchanged."""
    fed = _fed.get()
    if fed is not None and fed[0] == cid:
        data = fed[1][name]
        return None if data is None else decode(data)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")
