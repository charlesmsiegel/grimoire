"""What the store phase rewrote: one record per message, per scene.

A `rewrite_stored` rule changes text as it lands, which is the one phase that
changes the transcript -- so what the model or the player actually wrote is
kept here, for Restore original to write back. One file per scene, keyed by
the scene's **identity** rather than its `sid`: a `sid` moves on rename and is
handed to the next scene after a delete, and the identity is never reused, so
a record follows a rename without being moved and is never inherited by a
stranger. The scene DELETE route drops the file (this package may not be
imported from `store.scenes`, which it imports).

Each record is keyed by the message's `response_id` (a model reply) or
`post_id` (a player post, or a legacy reply segment), and holds the text
before the rewrite (`original`), the ids of the rules that changed it
(`rules`), the text the rewrite stored (`stored`) and when (`at`). Only the
latest rewrite of a message is kept. `stored` is what lets a reader tell a
record from the text that has since replaced it under the same key -- a reroll
or a swipe puts a different variant under one `response_id`. Two variants can
store the same text, though, so a model reply's record also names the variant
it was made for (`variant`), and a swipe back to another one does not match.

Nothing here is written unless a rule fired: a store with no `rewrite_stored`
rule never gets a `rewrites/` directory.
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path

from .. import atomic, locks
from ..campaigns import paths as campaigns_paths
from ..paths import now_iso
from ..scenes import identity as scenes_identity

_IDENTITY = re.compile(r"\A[0-9a-f]{32}\Z")


def path(cid: str, identity: str) -> Path:
    """The scene's record file. `identity` becomes a file name, so anything
    that is not an identity token is refused rather than resolved."""
    if not _IDENTITY.match(identity or ""):
        raise ValueError(f"not a scene identity: {identity!r}")
    return campaigns_paths.campaign_root(cid) / "rewrites" / f"{identity}.json"


def _read(p: Path) -> dict[str, dict]:
    """The well-formed records in `p`; a missing, garbled or hand-mangled file
    reads as what survives of it, down to nothing."""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if isinstance(v, dict) and isinstance(v.get("original"), str)}


def _write(p: Path, records: dict[str, dict]) -> None:
    if not records:
        with contextlib.suppress(FileNotFoundError):
            p.unlink()
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, json.dumps(records, ensure_ascii=False, indent=1))


def read_all(cid: str, sid: str) -> dict[str, dict]:
    """Every record of the scene, `{key: {original, rules, stored, at}}`.
    Never raises: a scene that cannot be read has no records to show."""
    try:
        ident = scenes_identity.scene_identity(cid, sid)
        return _read(path(cid, ident)) if ident else {}
    except (OSError, ValueError, campaigns_paths.CampaignNotFound):
        return {}


def record(cid: str, sid: str, key: str, *, original: str, rules: list[str],
           stored: str, variant: str = "") -> None:
    """Record (or replace) the rewrite of the message `key`. Called inside the
    hold that wrote `stored` to the transcript; the acquisition is reentrant.
    `variant` is the response variant a model reply's rewrite belongs to;
    "" (a player post, a legacy segment) stores none."""
    with locks.campaign_lock(cid):
        p = path(cid, scenes_identity.ensure_identity(cid, sid))
        records = _read(p)
        records[key] = {"original": original, "rules": list(rules), "stored": stored,
                        "at": now_iso(), **({"variant": variant} if variant else {})}
        _write(p, records)


def forget(cid: str, sid: str, key: str) -> None:
    """Drop the record of `key`, if there is one. Writes nothing when there is
    none -- in particular, never creates the file."""
    with locks.campaign_lock(cid):
        ident = scenes_identity.scene_identity(cid, sid)
        if not ident:
            return
        p = path(cid, ident)
        records = _read(p)
        if key in records:
            del records[key]
            _write(p, records)


def drop(cid: str, identity: str) -> None:
    """Remove the scene's whole record file. One that is not there is already
    dropped; anything else that stops the removal raises."""
    with locks.campaign_lock(cid), contextlib.suppress(FileNotFoundError):
        path(cid, identity).unlink()
