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
(`scenes.lifecycle.delete_scene` unlinks the path itself, with the other two
sidecars), the rename fan-out (`scene_refs.repoint` -> `repoint_scenes`) and
the repad/migration orphan sweep (`clear_destinations`) touch the file from
outside.
"""

from __future__ import annotations

import contextlib
import json

from . import atomic, locks, revision
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
            # Stamps for itself (`store/revision.py`'s census): this write is
            # reachable on a reroll the route then REFUSES — the
            # TurnSizesDesynced 400 — and the activity middleware only sees
            # success. After the write, not before: a token minted ahead of it
            # is readable while the write is still landing. Inside the try on
            # purpose — the failsoft above must cover the stamp too, and a
            # bump lost to the same dead disk costs one stale token, which
            # `revision.current`'s damage semantics already price.
            revision.bump(cid)
    except OSError:
        pass


def texts(cid: str, sid: str) -> list[str]:
    """Entry texts, oldest first. [] for a scene with no log."""
    return [e["text"] for e in _read_raw(cid, sid)]


def clear_destinations(cid: str, sids) -> None:
    """Drop the logs sitting on ids that are about to change hands.

    `alternates.clear_destinations`' argument, for the third sidecar that
    shares its hazard: `repad` and the legacy migration rename every scene and
    deliberately do not skip a taken id, so a destination orphan is a scene
    about to inherit another scene's corrections — fed straight to its absorb.
    Cleared before a single transcript moves, where a failure costs only the
    request; `repoint_scenes`' own clear runs after and cannot afford to raise.
    """
    with locks.campaign_lock(cid):
        for sid in sids:
            scenes_paths._steering_path(cid, sid).unlink(missing_ok=True)


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids: carry each log to its scene's new id.

    The shape is `pending_reviews.repoint_scenes`', and for its reasons: read
    every source before writing any target so a swapped mapping cannot land
    one log on top of another; publish before clearing so a crash leaves the
    entries readable at one path or the other; and never raise, because the
    caller has *already renamed the transcript* by the time this runs and the
    rest of `scene_refs.repoint` still owes a dozen stores their new id. A log
    that could not be carried is left where it is — an orphan `_sid_taken`
    already declines to hand out — rather than deleted.

    Bytes, verbatim: unlike a pending review, a steering entry stores no scene
    id to follow, and moving an undecodable file unchanged keeps `texts`'
    judgement ("unreadable reads as no log") where it belongs.
    """
    with locks.campaign_lock(cid):
        moving, stranded = {}, set()
        for old in mapping:
            try:
                moving[old] = scenes_paths._steering_path(cid, old).read_bytes()
            except FileNotFoundError:
                continue
            except OSError:
                stranded.add(old)
        published = set()
        # Destinations that are themselves sources go last: publishing over one
        # would destroy the last durable copy of what it still owes elsewhere.
        for old in sorted(moving, key=lambda o: mapping[o] in moving):
            try:
                atomic.write_bytes(
                    scenes_paths._steering_path(cid, mapping[old]), moving[old])
            except OSError:
                stranded.add(old)
                continue
            published.add(mapping[old])
        for sid in (*mapping, *mapping.values()):
            if sid in published or sid in stranded:
                continue
            # A source whose bytes are already published, or a destination
            # whose orphan will not go. Either costs one stale file that
            # `_sid_taken` keeps out of circulation; raising would abort the
            # fan-out and strand every other store on an id whose scene is gone.
            with contextlib.suppress(OSError):
                scenes_paths._steering_path(cid, sid).unlink(missing_ok=True)
