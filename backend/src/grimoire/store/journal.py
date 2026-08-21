"""Append-only per-campaign change journal at ``<campaign>/journal.json``: every
write-back that landed, and what it would take to put each one back (#31).

``changes.py`` is a *rolling* log -- one entry per record, replaced by the next
write-back -- so it answers "what did the last absorb do to this character" and
nothing else. The moment a second scene touched the same record, the first
scene's delta was gone, and with it any chance of reversing it. This module is
the history that log never kept.

Shape. The file is ``{"seq": n, "entries": [...]}`` rather than a bare list,
because ids have to outlive trimming: ``rolls.py`` can key positionally
(``r{len+1}``) precisely because it never drops an entry, and this one does.
``seq`` is a high-water mark that only ever rises, so an id is never reused even
after the oldest entries fall off the end.

Each entry:

    {"id": "j12", "ts": ..., "scene": "s3", "source": "absorb"|"manual"|"undo",
     "kind": "lore", "ref": {"kind": "lore", "id": "pact"},
     "field": "body", "label": "The Pact -- lore",
     "before": "...", "after": "...",
     "undo": {"target": {...}, "restore": ..., "expect": ...} | None,
     "why": "...",                    # why `undo` is None, when it is
     "undone": {"ts": ..., "by": "j13"} | absent}

``before``/``after`` are display text -- the same pair ``changes.py`` diffs, and
for the kinds that render a value (a plot status plus its latest beat, a
feeling) they are that rendering rather than anything a writer would accept.
The reversal never reads them: it lives entirely in ``undo``, which
``store/undo.py`` builds from the record itself and interprets on the way back.
An entry whose ``undo`` is None is audit-only, and ``why`` says so in the
reader's words rather than leaving them to guess.

``scene`` is "" for a manual edit made outside any scene. It is repointed on a
scene rename like every other store that persists a scene id
(``scene_refs.repoint``).

Retention is bounded **two ways**, and the second is the one that matters.
Unlike ``rolls.json`` -- a few hundred bytes an entry -- a journal row carries
the whole prior text of the record it moved, up to four times over
(``before``/``after`` for the panel, ``restore``/``expect`` for the reversal).
A row cap alone therefore bounds nothing: 500 rows of a 20 KB lore body is a
40 MB file, rewritten in full on every absorb save, in a folder the user may be
syncing between devices. So ``RETENTION`` caps the rows and ``MAX_BYTES`` caps
the serialized size, oldest dropped first until both hold.

Dropping the oldest is the honest failure: undo reaches back a bounded distance
and says so (a dropped entry is simply not found), rather than the store quietly
becoming the largest thing in ``~/.grimoire``.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import atomic, locks
from .campaigns import paths as campaigns_paths
from .paths import now_iso

#: How many entries are kept. Everything older is dropped on the next append.
RETENTION = 500

#: And how many bytes they may occupy once serialized. A row carries record
#: text, so this is the cap that actually binds -- see the module docstring.
MAX_BYTES = 2_000_000


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "journal.json"


def _high_water(entries: list[dict]) -> int:
    """The largest id in `entries`, as a number. Ids are ``j<n>``; anything else
    is a hand edit and contributes nothing rather than raising."""
    best = 0
    for entry in entries:
        jid = entry.get("id")
        if isinstance(jid, str) and jid.startswith("j") and jid[1:].isdigit():
            best = max(best, int(jid[1:]))
    return best


def _load(cid: str) -> dict:
    """The whole document, normalized. Tolerant of a garbled or hand-edited file
    for the reason `changes.read` is: this backs a display panel, and one bad
    byte must cost the history rather than the page.

    `seq` is taken as the HIGHER of what the file claims and what its entries
    show, so a truncated or hand-trimmed `seq` cannot hand out an id that is
    already in use -- the one invariant an undo target depends on.
    """
    empty = {"seq": 0, "entries": []}
    p = _path(cid)
    if not p.exists():
        return empty
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty
    if not isinstance(data, dict):
        return empty
    raw = data.get("entries")
    entries = [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
    seq = data.get("seq")
    seq = seq if isinstance(seq, int) and not isinstance(seq, bool) else 0
    return {"seq": max(seq, _high_water(entries)), "entries": entries}


def read(cid: str) -> list[dict]:
    """Every journalled change, oldest first."""
    return _load(cid)["entries"]


def get(cid: str, jid: str) -> dict | None:
    for entry in read(cid):
        if entry.get("id") == jid:
            return entry
    return None


def _write(cid: str, doc: dict) -> None:
    atomic.write_text(_path(cid), json.dumps(doc, indent=2) + "\n")


def append(cid: str, rows: list[dict]) -> list[dict]:
    """Append each row, stamped with a fresh id and timestamp. Returns what was
    written. No-op for an empty list.

    Takes the campaign lock: this is a read-modify-write of one whole file, so
    two unserialized callers lose one of the two appends -- and losing an append
    loses the only record of a write that already landed.
    """
    if not rows:
        return []
    with locks.campaign_lock(cid):
        doc = _load(cid)
        ts = now_iso()
        written: list[dict] = []
        for row in rows:
            doc["seq"] += 1
            # The allocated id and stamp go LAST, so a row carrying its own
            # `id` cannot take one already in use -- which would silently
            # re-point `get` and `mark_undone` at somebody else's entry. No
            # caller does that today; the ordering is what keeps it that way.
            entry = {**row, "id": f"j{doc['seq']}", "ts": ts}
            doc["entries"].append(entry)
            written.append(entry)
        doc["entries"] = _trim(doc["entries"], keep=len(written))
        _write(cid, doc)
        return written


def _trim(entries: list[dict], keep: int) -> list[dict]:
    """Drop the oldest entries until both caps hold, never below `keep`.

    `keep` is what this append just wrote. A single row can exceed `MAX_BYTES`
    on its own -- one absorb rewriting a very long lore body -- and trimming to
    satisfy the cap would then delete the entry for the write that just
    happened, leaving that change with no history at all. The cap is a bound on
    accumulation, not a licence to discard the present, so it yields.

    Sizes are measured once per entry rather than by re-serializing the list on
    each pop, which would be quadratic in exactly the case that reaches here.
    """
    # The floor applies to the row cap too, not only to the byte cap below. A
    # commit with more edits than `RETENTION` would otherwise have this trim
    # away rows of the write it is in the middle of recording -- the same thing
    # the byte cap is careful not to do, so the two must not disagree.
    room = max(RETENTION, keep)
    entries = entries[-room:] if len(entries) > room else entries
    sizes = [len(json.dumps(e, default=str)) for e in entries]
    total, first = sum(sizes), 0
    while total > MAX_BYTES and len(entries) - first > keep:
        total -= sizes[first]
        first += 1
    return entries[first:]


def mark_undone(cid: str, jid: str, by: str) -> bool:
    """Stamp an entry as reversed by entry `by`. False when it is unknown or was
    already stamped -- the caller decides what that means.

    Callers hold this lock already (`undo.undo` spans the whole check-write-mark
    so a second reversal cannot slip between the write and the stamp); the take
    here is reentrant and free, and it keeps this module's own read-modify-write
    correct for anyone who calls it alone.
    """
    with locks.campaign_lock(cid):
        doc = _load(cid)
        for entry in doc["entries"]:
            if entry.get("id") != jid:
                continue
            if entry.get("undone"):
                return False
            entry["undone"] = {"ts": now_iso(), "by": by}
            _write(cid, doc)
            return True
        return False


def _ref_pairs(mapping: dict[str, str]) -> dict[tuple[str, str], tuple[str, str]]:
    """`{"lore/pact": "items/pact"}` as `{("lore", "pact"): ("items", "pact")}`.

    An entry holds the kind and the id in separate fields, so the ledger's
    joined form has to be split before it can be matched against one. A half of
    a ref that is empty names nothing and is dropped rather than matched.
    """
    pairs = {}
    for old, new in mapping.items():
        if old == new:
            continue
        okind, _, oid = old.partition("/")
        nkind, _, nid = new.partition("/")
        if okind and oid and nkind and nid:
            pairs[(okind, oid)] = (nkind, nid)
    return pairs


def repoint_records(cid: str, mapping: dict[str, str]) -> None:
    """Follow reclassified records (#119) through both places an entry names one:
    the display `ref` and the `undo.target` of an entity write.

    `mapping` is in `<kind>/<id>` form; entries hold the pair unjoined, so it is
    split here rather than at the caller.

    The reversal half is why this is not cosmetic. `undo.read_value` resolves an
    entity target through `overlay.read_entity(cid, kind, id)`, so an entry left
    pointing at the old kind does not reverse anything -- it *refuses*, with the
    record sitting right there under its new kind. Reclassifying a record would
    otherwise quietly retire every undo offer standing against it.

    Only `w == "entity"` targets carry a kind at all; the sidecar writers
    (`state`, `dossier`, `group_state`, …) are keyed by actor id, and an actor
    is not a kind this can move.
    """
    pairs = _ref_pairs(mapping)
    if not pairs:
        return
    with locks.campaign_lock(cid):
        doc = _load(cid)
        hit = False
        for entry in doc["entries"]:
            undo = entry.get("undo")
            target = undo.get("target") if isinstance(undo, dict) else None
            if not isinstance(target, dict) or target.get("w") != "entity":
                target = None
            for named in (entry.get("ref"), target):
                if not isinstance(named, dict):
                    continue
                kind, rid = named.get("kind"), named.get("id")
                if not isinstance(kind, str) or not isinstance(rid, str):
                    continue
                moved = pairs.get((kind, rid))
                if moved is None:
                    continue
                named["kind"], named["id"] = moved
                hit = True
        if hit:
            _write(cid, doc)


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids in each entry's scene field."""
    with locks.campaign_lock(cid):
        doc = _load(cid)
        hit = False
        for entry in doc["entries"]:
            if entry.get("scene") in mapping:
                entry["scene"] = mapping[entry["scene"]]
                hit = True
        if hit:
            _write(cid, doc)
