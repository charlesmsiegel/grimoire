"""The scene's reroll-steering log: every hint a regenerate carried, kept.

``<campaign>/scenes/<sid>.steering.json`` — the third per-scene sidecar
(`scenes.paths._steering_path`), and the durable half of the reroll hint.
`store/alternates.py` keeps the same string as a display label on the variant
it produced, lifecycle-bound to the trailing generation's set: the anchor
moves and it is gone. This log is the other consumer — the end-of-scene
absorb (`absorb.steering_snapshot`) — and its lifetime is the scene's: every
steering prompt marks a place where the written lore and the player's intent
disagreed hard enough to interrupt play, which stays true long after the
variant it steered is dropped, superseded, or promoted away.

Append-only in use (`record` never rewrites an entry), consecutive-deduped
(the error banner's Retry re-sends a reroll with the same guidance, and a
player hammering reroll with one instruction is one signal), and bounded both
ways: entries clip at ``MAX_STEERING_CHARS`` for
`alternates.MAX_GUIDANCE_CHARS`'s wire-input reason, and the list trims
oldest past ``STEERING_LIMIT`` — a structural backstop against a pathological
writer, not a packing policy, to be tuned against real prompts if it ever
bites.

``record`` is failsoft on ``OSError``: a steering row is an absorb hint, and
losing one must never fail the reroll that carries it. The concrete case is a
pre-cap store whose ``<sid>.md`` fits its directory entry and whose steering
sidecar name does not (ENAMETOOLONG — the tolerance
`scenes.lifecycle._unlink_sidecar` documents from the delete side). A garbled
file is replaced rather than raised on: whatever corrupted it already lost
its entries, and refusing to log new ones on top serves nobody.

Entries carry no transcript index or anchor on purpose: the absorb consumes
text and order, an index would renumber under cuts, and an anchor would drag
in the alternates' slot mathematics for a consumer that does not exist.
Readers treat entry keys as open, so a later field needs no migration.

The log is never cleared — not by a chronicle save, so a ``force`` re-absorb
is primed with the same notes the first absorb saw: a re-absorb redoes the
extraction, it does not forget the extraction's inputs. Only scene deletion
(`scenes.lifecycle.delete_scene`, and `drop_scene` here for the fan-out) and
the rename fan-out (`scene_refs.repoint` -> `repoint_scenes`) touch the file
from outside.
"""

from __future__ import annotations

import json

from . import atomic, locks
from .paths import now_iso
from .scenes import paths as scenes_paths

SCHEMA = 1

#: How much of one steering prompt is kept. Same bound, same reason as
#: `alternates.MAX_GUIDANCE_CHARS`: `guidance` is an unbounded string on the
#: wire, and nothing else stops one request from parking megabytes in a file
#: the absorb reads whole. Clipped, not rejected — prose keeps most of its
#: meaning cut short.
MAX_STEERING_CHARS = 500

#: How many entries one scene keeps, oldest dropped first. A backstop against
#: a runaway file and a runaway prompt block (`MAX_STEERING_CHARS` x this
#: bounds both), not a measured ceiling — an ordinary scene holds a handful.
STEERING_LIMIT = 100


def _read_raw(cid: str, sid: str) -> list[dict]:
    """The stored entries, or [] for absent/unreadable/malformed — the sidecar
    is a convenience beside the transcript and must never make a scene
    unopenable, or fail the absorb that reads it."""
    p = scenes_paths._steering_path(cid, sid)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return []
    return [e for e in data["entries"]
            if isinstance(e, dict)
            and isinstance(e.get("text"), str) and e["text"]]


def record(cid: str, sid: str, text: str) -> None:
    """Log one reroll's guidance. No-op on empty text and on a repeat of the
    newest entry; failsoft on OSError (module docstring)."""
    text = (text or "").strip()[:MAX_STEERING_CHARS]
    if not text:
        return
    try:
        with locks.campaign_lock(cid):
            entries = _read_raw(cid, sid)
            if entries and entries[-1]["text"] == text:
                return
            entries.append({"text": text, "created": now_iso()})
            atomic.write_text(
                scenes_paths._steering_path(cid, sid),
                json.dumps({"v": SCHEMA, "entries": entries[-STEERING_LIMIT:]},
                           indent=2) + "\n")
    except OSError:
        pass


def texts(cid: str, sid: str) -> list[str]:
    """Entry texts, oldest first. [] for a scene with no log."""
    return [e["text"] for e in _read_raw(cid, sid)]
