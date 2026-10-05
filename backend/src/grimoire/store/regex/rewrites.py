"""What the store phase rewrote: one record per message, per scene.

A `rewrite_stored` rule changes text as it lands, which is the one phase that
changes the transcript -- so what the model or the player actually wrote is
kept here, for Restore original to write back. One file per scene, keyed by
the scene's **identity** rather than its `sid`: a `sid` moves on rename and is
handed to the next scene after a delete, and the identity is never reused, so
a record follows a rename without being moved and is never inherited by a
stranger. The scene DELETE route drops the file (this package may not be
imported from `store.scenes`, which it imports).

Each record is keyed by the message it describes (`key_for`): a model reply's
`response_id`, joined to its `response_part` as `<response_id>#<part>` for
every part after the first -- a reply resumed after a roll is two messages
under one `response_id`, and each part's rewrite is its own -- else the
`post_id` of a player post or a legacy reply segment. Records written before
parts had keys of their own sit under the bare `response_id` whichever part
they were made for; `candidates` still offers that key for every part, and the
stored text decides which part it describes. Each record holds the text
before the rewrite (`original`), the ids of the rules that changed it
(`rules`), the text the rewrite stored (`stored`) and when (`at`). Only the
latest rewrite of a message is kept. `stored` is what lets a reader tell a
record from the text that has since replaced it under the same key -- a reroll
or a swipe puts a different variant under one `response_id`. Two variants can
store the same text, though, so a model reply's record also names the variant
it was made for (`variant`), and a swipe back to another one does not match.

A record leaves with its message. Every seam that takes messages off a
transcript -- a cut, a deleted response, a post taken back, a reroll or swipe
that replaces a run, a replay that lets its originals go -- calls `prune`,
which keeps only the records some message still answers to (`candidates`), so
the original prose of a post that is gone is neither kept on disk nor handed
out. Posts a running replay holds to put back count as there. A seam that
forgets to prune leaks nothing for long: the next prune anywhere in the scene
catches up.

Nothing here is written unless a rule fired: a store with no `rewrite_stored`
rule never gets a `rewrites/` directory.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path

from .. import atomic, locks
from ..campaigns import paths as campaigns_paths
from ..paths import now_iso
from ..scenes import identity as scenes_identity
from ..scenes import paths as scenes_paths
from ..scenes import read as scenes_read

log = logging.getLogger(__name__)

_IDENTITY = re.compile(r"\A[0-9a-f]{32}\Z")


def key_for(message: dict) -> str:
    """The key a rewrite of `message` is recorded under now, "" for a message
    with no id to key one by."""
    rid = message.get("response_id") or ""
    part = message.get("response_part") or ""
    if rid:
        return f"{rid}#{part}" if part else rid
    return message.get("post_id") or ""


def candidates(message: dict) -> list[str]:
    """Every key a record of `message` may sit under, the current one first:
    its part's key, the bare `response_id` (the first part's, and where a
    later part's record was kept before parts had keys of their own), and the
    `post_id` a legacy reply was recorded under before the response migration
    gave it a `response_id` too."""
    keys = [key_for(message), message.get("response_id") or "", message.get("post_id") or ""]
    return [k for k in dict.fromkeys(keys) if k]


def response_of(key: str) -> str:
    """The `response_id` (or `post_id`) a record key belongs to."""
    return key.split("#", 1)[0]


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
    return {k: _shaped(v) for k, v in data.items()
            if isinstance(v, dict) and isinstance(v.get("original"), str)}


def _shaped(rec: dict) -> dict:
    """A record whose `original` survived, with every field a reader
    dereferences put back in shape: `rules` a list of rule-id strings, `stored`
    and `at` strings, `variant` kept only as a non-empty string. A hand edit or
    a sync conflict can leave any of them as anything, and the inspector maps
    over `rules`. Unknown fields are dropped."""
    rules = rec.get("rules")
    out = {"original": rec["original"],
           "rules": [r for r in rules if isinstance(r, str)] if isinstance(rules, list) else [],
           "stored": rec["stored"] if isinstance(rec.get("stored"), str) else "",
           "at": rec["at"] if isinstance(rec.get("at"), str) else ""}
    if isinstance(rec.get("variant"), str) and rec["variant"]:
        out["variant"] = rec["variant"]
    return out


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


def carry(cid: str, sid: str, rid: str, before: str, after: str, *, skip: str = "") -> None:
    """Move every record of response `rid` made for variant `before` onto
    `after`, except the one under `skip`. A continuation part is a new variant
    of the same response whose earlier parts are unchanged, so their records
    still describe them -- left naming `before`, they would stop matching and
    Restore of an earlier part would be refused. Writes nothing when no
    record moves."""
    if not after or after == before:
        return
    with locks.campaign_lock(cid):
        ident = scenes_identity.scene_identity(cid, sid)
        if not ident:
            return
        p = path(cid, ident)
        records = _read(p)
        moved = False
        for key, rec in records.items():
            if key != skip and response_of(key) == rid and rec.get("variant") == before:
                rec["variant"] = after
                moved = True
        if moved:
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


def prune(cid: str, sid: str, *, held: Iterable[dict] = ()) -> None:
    """Drop every record no message of the scene answers to any more.

    `held` are messages kept outside the transcript that can come back to it
    with their ids -- the posts a running replay cut and holds to put back --
    so their records stay. Called from inside the hold that removed the
    messages; the acquisition is reentrant. Writes nothing when nothing goes,
    and never creates the file.

    Never raises: it tidies up beside a removal that has already landed, and a
    record file that cannot be pruned now is pruned by the next seam that
    asks. Logged without the text, which is private prose."""
    try:
        with locks.campaign_lock(cid):
            ident = scenes_identity.scene_identity(cid, sid)
            if not ident:
                return
            p = path(cid, ident)
            records = _read(p)
            if not records:
                return
            live = {k for m in [*scenes_read.read_scene(cid, sid)["messages"], *held]
                    for k in candidates(m)}
            kept = {k: v for k, v in records.items() if k in live}
            if len(kept) != len(records):
                _write(p, kept)
    except (OSError, ValueError, scenes_paths.SceneNotFound,
            campaigns_paths.CampaignNotFound):
        log.warning("could not prune the stored rewrites of %s/%s", cid, sid, exc_info=True)


def drop(cid: str, identity: str) -> None:
    """Remove the scene's whole record file. One that is not there is already
    dropped; anything else that stops the removal raises."""
    with locks.campaign_lock(cid), contextlib.suppress(FileNotFoundError):
        path(cid, identity).unlink()
