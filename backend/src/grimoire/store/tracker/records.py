"""The scene tracker's records: one snapshot file per tracked post, and an index.

A *record* is a key's snapshot file together with its entry in `index.json`
(`docs/superpowers/specs/2026-10-03-scene-state-tracker-design.md`, "Storage").
Every function takes the scene's **identity**, never its `sid`: a `sid` moves on
rename and is handed to the next scene made after a delete, so records keyed on
it would follow a rename only by fan-out and would be inherited by a stranger.
The identity is minted once and never reused, so neither can happen. Resolving
`sid -> identity` is the caller's job (`tracker.walk`), which is also what keeps
this module's imports down to the store primitives -- it must never import
`store.scenes`, because `scenes.lifecycle.delete_scene` imports it to drop a
deleted scene's directory.

Snapshot values live only in the per-key files, so an update costs one small
file however long the scene gets. Everything that changes often and in bulk --
status, the change list, the two staleness flags -- lives in the index, so an
edit that flags fifty later posts rewrites one file rather than fifty.

Write order is **snapshot first, then index**, each file written whole
(`atomic`) but the pair not atomically. For an update run, a crash between the
two leaves a snapshot the index does not yet call `ok` (it is still `pending`
from `mark_pending`, and reads as interrupted), which a Retry overwrites; the
other order could leave an index saying `ok` about a snapshot that never
landed. A hand edit has no `pending` step, so a crash between its two writes
leaves the new snapshot under the entry's previous `ok` (its change list and
flags one write behind) -- stale bookkeeping about a value that did land, not
a lost one.

A missing or garbled index is **rebuilt** from the snapshot files present
rather than taking the play view down over a half-synced file. That is a
recovery, not a lossless one: the index's own fields -- status, change list,
staleness flags, the counters -- are in no snapshot file, so every rebuilt
entry reads `ok`, unflagged and with no changes, including one whose last run
had failed and left an older snapshot behind.
"""

from __future__ import annotations

import contextlib
import json
import shutil
from pathlib import Path

from .. import atomic, locks
from ..paths import now_iso
from . import paths

VERSION = 1
FLAGS = ("upstream_changed", "text_changed")
SEQ = "flag_seq"
"""The entry field counting flag raises (`set_flags`), so an update can tell
at save time whether a flag went up after it read its inputs."""
GEN = "mark_gen"
"""The entry field counting the writes that make an update run obsolete: every
`mark_pending` (a newer run for the same key) and every hand edit. A run
carries the generation its mark returned, and is skipped -- or its result
discarded -- once the entry's has moved on: a newer run will settle the record,
or a person has written to it and a result built before that would replace the
edit without a trace."""
INTERNAL = (SEQ, GEN)
"""Bookkeeping fields no reader is shown."""


def _index_path(cid: str, identity: str) -> Path:
    return paths.scene_dir(cid, identity) / "index.json"


def _snapshot_path(cid: str, identity: str, key: str) -> Path:
    # A key becomes a file name, so it is checked here rather than trusted: the
    # walk only ever produces valid ones, and anything else is a caller bug.
    if not paths.valid_key(key):
        raise ValueError(f"not a tracker key: {key!r}")
    return paths.scene_dir(cid, identity) / f"{key}.json"


def _clear_flags() -> dict:
    return dict.fromkeys(FLAGS, False)


def _rebuilt(cid: str, identity: str) -> dict[str, dict]:
    """The index as the snapshot files on disk imply it.

    Only stems that are keys: `index.json` and the scene's field layer
    (`fields.json`) share the directory and must never become phantom records,
    and neither must an `atomic` temp caught mid-write."""
    d = paths.scene_dir(cid, identity)
    try:
        files = sorted(d.glob("*.json"))
    except OSError:
        return {}
    return {p.stem: {"status": "ok", "changed": [], "flags": _clear_flags()}
            for p in files if paths.valid_key(p.stem)}


def _well_formed(entries: dict) -> bool:
    return all(
        paths.valid_key(k) and isinstance(v, dict) for k, v in entries.items())


def read_index(cid: str, identity: str) -> dict[str, dict]:
    """Every key's entry: `{key: {"status", "changed", "flags", "error"?}}`.

    Never raises over the file's contents: a missing, unreadable or garbled
    index is rebuilt from the snapshot files (see the module docstring). Not
    written back here -- a read takes no lock -- but the next mutator persists
    whatever this returned."""
    try:
        raw = json.loads(_index_path(cid, identity).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _rebuilt(cid, identity)
    entries = raw.get("entries") if isinstance(raw, dict) else None
    if not isinstance(entries, dict) or not _well_formed(entries):
        return _rebuilt(cid, identity)
    return entries


def stored_keys(cid: str, identity: str) -> set[str]:
    """Every key with an index entry or a snapshot file -- what a prune has to
    consider, since a crash between the two writes leaves a file the index may
    not mention."""
    return set(read_index(cid, identity)) | set(_rebuilt(cid, identity))


def read_snapshot(cid: str, identity: str, key: str) -> dict | None:
    """The snapshot file's body (`version`, `snapshot`, `fields_digest`, `model`,
    `at`), or `None` when there is none or it cannot be read as one."""
    try:
        body = json.loads(_snapshot_path(cid, identity, key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(body, dict) or not isinstance(body.get("snapshot"), dict):
        return None
    return body


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def _write_index(cid: str, identity: str, entries: dict[str, dict]) -> None:
    _write_json(_index_path(cid, identity), {"version": VERSION, "entries": entries})


def generation(entry: dict | None) -> int:
    """The entry's mark generation (`GEN`); absent reads as 0."""
    gen = (entry or {}).get(GEN, 0)
    return gen if isinstance(gen, int) else 0


def mark_pending(cid: str, identity: str, key: str) -> int:
    """A run for `key` has been scheduled; returns the generation it owns.
    Its flags and change list stay as they were until the run lands, so a
    disclosure can keep showing the last result. The generation moves, so any
    run marked before this one is now obsolete (`GEN`)."""
    with locks.campaign_lock(cid):
        _snapshot_path(cid, identity, key)          # validates the key
        entries = read_index(cid, identity)
        entry = entries.get(key) or {"changed": [], "flags": _clear_flags()}
        entry["status"] = "pending"
        entry.pop("error", None)
        entry[GEN] = generation(entry) + 1
        entries[key] = entry
        _write_index(cid, identity, entries)
        return entry[GEN]


def flag_seq(entry: dict | None) -> int:
    """How many times a flag has been raised on this entry -- what an update
    reads beside its inputs and hands back to `save` as `seen_seq`."""
    seq = (entry or {}).get(SEQ, 0)
    return seq if isinstance(seq, int) else 0


def save(cid: str, identity: str, key: str, snapshot: dict, *, changed: list[list],
         fields_digest: str, model: str, keep_flags: bool = False,
         seen_seq: int | None = None, raise_upstream: bool = False,
         bump_gen: bool = False) -> None:
    """Store `key`'s snapshot and mark it `ok`. What happens to its flags:

    - by default they are cleared (a writer that read everything just now);
    - `keep_flags`: kept as they are (a hand edit -- one value typed in does
      not make the rest of the record agree with an edited post);
    - `seen_seq`: cleared only if no flag was raised since the writer read its
      inputs (`flag_seq` then), else kept. An update run reads its inputs a
      provider call before this save; a flag raised in between is a write the
      snapshot never saw, and clearing it would make a stale record read fresh.

    The flags are never cleared anywhere else -- not when a run is marked, not
    when it fails -- so a run that produced nothing leaves them as it found
    them. The sequence is carried over, never reset.

    `raise_upstream` then raises `upstream_changed` in the same write: an
    update built on a base record that was itself stale is stale too, however
    fresh its own reading of its post. `bump_gen` moves the mark generation
    (a hand edit -- see `GEN`); otherwise it is carried over.

    The snapshot is stored as given; what a valid one looks like is the update
    pipeline's business, not the store's."""
    with locks.campaign_lock(cid):
        _write_json(_snapshot_path(cid, identity, key),
                    {"version": VERSION, "snapshot": snapshot,
                     "fields_digest": fields_digest, "model": model, "at": now_iso()})
        entries = read_index(cid, identity)
        prev = entries.get(key) or {}
        seq = flag_seq(prev)
        flags = _clear_flags()
        if keep_flags or (seen_seq is not None and seen_seq != seq):
            flags.update(prev.get("flags") or {})
        if raise_upstream:
            flags["upstream_changed"] = True
        entries[key] = {"status": "ok", "changed": changed, "flags": flags}
        if seq:
            entries[key][SEQ] = seq     # absent reads as 0; only a raise starts it
        gen = generation(prev) + (1 if bump_gen else 0)
        if gen:
            entries[key][GEN] = gen
        _write_index(cid, identity, entries)


def mark_failed(cid: str, identity: str, key: str, error: str,
                gen: int | None = None) -> None:
    """The run for `key` failed. An earlier snapshot file, if any, is kept: the
    walk skips a non-`ok` key, so it is not read, and a retry overwrites it.

    `gen`, when given, is the generation the failing run owned: once the entry
    has moved past it, the record belongs to a newer run or a hand edit, and a
    stale run's failure is not news about it -- nothing is written."""
    with locks.campaign_lock(cid):
        _snapshot_path(cid, identity, key)
        entries = read_index(cid, identity)
        if gen is not None and generation(entries.get(key)) != gen:
            return
        entry = entries.get(key) or {"changed": [], "flags": _clear_flags()}
        entry.update(status="failed", error=error)
        entries[key] = entry
        _write_index(cid, identity, entries)


def set_flags(cid: str, identity: str, keys: list[str], flag: str) -> None:
    """Raise `flag` on every key in `keys` that has an entry. A key with none
    has no record to be stale, so it is skipped rather than conjured."""
    if flag not in FLAGS:
        raise ValueError(f"unknown tracker flag: {flag!r}")
    with locks.campaign_lock(cid):
        entries = read_index(cid, identity)
        hit = [k for k in keys if k in entries]
        if not hit:
            return
        for k in hit:
            entries[k].setdefault("flags", _clear_flags())[flag] = True
            entries[k][SEQ] = flag_seq(entries[k]) + 1
        _write_index(cid, identity, entries)


def discard(cid: str, identity: str, keys: list[str]) -> None:
    """Remove each key's snapshot file and index entry. Index first: an entry
    left pointing at a deleted file would read as `ok` with no snapshot, while a
    file left without an entry is only an orphan the next prune collects."""
    with locks.campaign_lock(cid):
        entries = read_index(cid, identity)
        if any(k in entries for k in keys):
            _write_index(cid, identity, {k: v for k, v in entries.items() if k not in keys})
        for k in keys:
            _snapshot_path(cid, identity, k).unlink(missing_ok=True)


def drop(cid: str, identity: str) -> None:
    """Remove the scene's whole tracker directory (records and its field layer).
    A directory that is not there is already dropped; anything else that stops
    the removal raises, so a delete that half-worked is reported."""
    with locks.campaign_lock(cid), contextlib.suppress(FileNotFoundError):
        shutil.rmtree(paths.scene_dir(cid, identity))
