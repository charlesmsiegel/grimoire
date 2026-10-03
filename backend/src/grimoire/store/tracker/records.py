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

Write order is **snapshot first, then index**. A crash between the two leaves a
snapshot the index does not yet call `ok` (it is still `pending` from
`mark_pending`), which the next run simply overwrites; the other order could
leave an index saying `ok` about a snapshot that never landed. That ordering is
also why the index is **rebuildable**: it holds no value that is not either in a
snapshot file or regenerable, so a missing or garbled index is rebuilt from the
snapshot files present, each counted `ok` and unflagged, rather than taking the
play view down over a half-synced file.
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


def mark_pending(cid: str, identity: str, key: str) -> None:
    """A run for `key` has started. Its flags and change list stay as they were
    until the run lands, so a disclosure can keep showing the last result."""
    with locks.campaign_lock(cid):
        _snapshot_path(cid, identity, key)          # validates the key
        entries = read_index(cid, identity)
        entry = entries.get(key) or {"changed": [], "flags": _clear_flags()}
        entry["status"] = "pending"
        entry.pop("error", None)
        entries[key] = entry
        _write_index(cid, identity, entries)


def save(cid: str, identity: str, key: str, snapshot: dict, *, changed: list[list],
         fields_digest: str, model: str, keep_flags: bool = False) -> None:
    """Store `key`'s snapshot and mark it `ok` -- with fresh flags, or with the
    entry's current ones when `keep_flags`.

    `keep_flags` is for a writer whose inputs were read EARLIER than this save:
    an update run (which clears the flags itself, in the hold that reads its
    inputs -- `clear_flags`) and a hand edit. A flag still raised at save time
    was raised by a write the snapshot never saw, so clearing it here would
    make a stale record read as fresh.

    The snapshot is stored as given; what a valid one looks like is the update
    pipeline's business, not the store's."""
    with locks.campaign_lock(cid):
        _write_json(_snapshot_path(cid, identity, key),
                    {"version": VERSION, "snapshot": snapshot,
                     "fields_digest": fields_digest, "model": model, "at": now_iso()})
        entries = read_index(cid, identity)
        flags = _clear_flags()
        if keep_flags:
            flags.update((entries.get(key) or {}).get("flags") or {})
        entries[key] = {"status": "ok", "changed": changed, "flags": flags}
        _write_index(cid, identity, entries)


def clear_flags(cid: str, identity: str, key: str) -> None:
    """Lower both flags on `key`'s entry, if it has one: an update has just read
    the transcript and prior state those flags said had moved, so the result it
    is about to compute answers them."""
    with locks.campaign_lock(cid):
        entries = read_index(cid, identity)
        entry = entries.get(key)
        if entry is None or not any((entry.get("flags") or {}).values()):
            return
        entry["flags"] = _clear_flags()
        _write_index(cid, identity, entries)


def mark_failed(cid: str, identity: str, key: str, error: str) -> None:
    """The run for `key` failed. An earlier snapshot file, if any, is kept: the
    walk skips a non-`ok` key, so it is not read, and a retry overwrites it."""
    with locks.campaign_lock(cid):
        _snapshot_path(cid, identity, key)
        entries = read_index(cid, identity)
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
