"""What the legacy settings layout could not carry over, and the record of it.

The planner (`legacy_plan`) computes notes -- what no preset can carry -- and
retirement persists them, with each connection's legacy model fields, in the
retirement record: `<home>/inference-retired.json` (slice I, ruling 16). It
sits at the store root, not under `.cache/`, so it is in every backup and
every synced copy, and a note shown on `/models` is shown on every device
until the user dismisses it there.

A note's id is a digest of what it is ABOUT -- its scope, subject, provider,
effort and kind -- and never of its wording (N14): a later build that rewords
a note must not bring back one the user dismissed.

The record::

    {"fields": {conn_id: {model_field: value, ...}, ...},
     "notes": [{id, scope, subject, provider_id, effort, kind, text,
                dismissed}, ...]}

- `fields` holds what the strip took off each connection
  (`llm_connections.strip_model_fields`), written BEFORE the strip. The first
  values recorded for a connection win (N20): they are what the C migration
  translated. Only the planner's lookup reads them (`legacy_plan.lookup`), as
  the fallback for a connection whose file is there and holds no legacy
  field -- so a campaign that arrives unmarked after the strip still
  resolves. `MODEL_FIELDS` names no key or URL, so the record never holds a
  credential.
- `notes`: merged by id; merging never un-dismisses a note.

Reading: `read()` is fail-soft (display). `read(strict=True)` is for every
writer here and for every lookup that feeds a write: a file that is there but
holds no record -- empty, not JSON, the wrong shape -- raises
`RecordUnreadableError` (a `frontmatter.RecordUnreadableError`, so an
`OSError`), and so does a read the OS refuses. Nothing is ever written over a
record that could not be read.

**Locks.** Every write is through `store.atomic`, under `_lock`, a
process-local lock that is innermost: nothing that holds it takes another
lock. Its callers hold `config_lock` (`llm_connections.LOCK`) around it --
retirement's writes, the strip and the dismiss route all do -- which is what
makes a read-modify-write of this file whole across processes. Order:
`llm_connections.LOCK` -> `_lock`, never the reverse (N4).

A store-level leaf, as `inference_keys` is and for the same reason: the
planner (`inference.legacy_plan`), retirement and the settings view import it,
and so does `llm_connections` -- the strip records into it, and a delete
forgets a connection in it -- which `store/inference/` cannot be imported
from without a cycle (its `__init__` imports `resolve`, which imports
`llm_connections`). It imports nothing but `atomic`, `frontmatter` and
`paths`, and nothing of the planner's (N10).
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import NamedTuple

from . import atomic, frontmatter, paths

#: The record's file name, at the store root.
FILENAME = "inference-retired.json"

#: What a note can say was lost:
#: - `route_preset`: a route-level preset that sets no reasoning effort, over a
#:   GLM provider whose legacy effort rode under it (ruling 5);
#: - `unrepresentable`: a legacy effort no preset can carry;
#: - `fact_not_carried`: a connection's model field its model's facts do not
#:   state, found before the strip (N9).
KINDS: tuple[str, ...] = ("route_preset", "unrepresentable", "fact_not_carried")

#: A note's scope on `config.md`; a campaign's is `campaign_scope(cid)`. Held
#: here, in the leaf, so the planner and the settings view spell them alike.
GLOBAL_SCOPE = "global"


def campaign_scope(cid: str) -> str:
    """The scope of a note on campaign `cid`."""
    return f"campaign:{cid}"


def scope_campaign(scope: str) -> str:
    """The campaign id a campaign scope names; "" for any other scope."""
    return scope.removeprefix("campaign:") if scope.startswith("campaign:") else ""


#: A note's fields, as the record stores them (plus `dismissed`).
_NOTE_FIELDS: tuple[str, ...] = ("id", "scope", "subject", "provider_id", "effort", "kind",
                                 "text")

#: The one lock over the record's read-modify-writes in this process. Innermost.
_lock = threading.Lock()


class Note(NamedTuple):
    """One thing not carried over. `subject` names what it is about at that
    scope (a route key, a selection slot, a model field); `effort` is the
    legacy reasoning effort involved, "" when none is."""

    id: str
    scope: str
    subject: str
    provider_id: str
    effort: str
    kind: str
    text: str


class RecordUnreadableError(frontmatter.RecordUnreadableError):
    """The retirement record is there and cannot be read (empty, not JSON,
    the wrong shape, or held by another program). Raised by a strict read,
    so nothing that persists from it -- the strip, a dismiss, the migration's
    or retirement's lookup -- writes on a guess. The settings write that
    reaches it answers 409 `retirement_unreadable`."""


def note_id(scope: str, subject: str, provider_id: str, effort: str, kind: str) -> str:
    """The stable id of the note with these fields: the same on every device
    and in every build, whatever the note's text says."""
    canonical = json.dumps([scope, subject, provider_id, effort, kind], ensure_ascii=False,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def path() -> Path:
    """Where the record lives: the store root."""
    return paths.home() / FILENAME


def _empty() -> dict:
    return {"fields": {}, "notes": []}


def _note_row(raw: object) -> dict | None:
    """A stored note, checked: every field a string, `dismissed` a bool."""
    if not isinstance(raw, dict):
        return None
    row = {k: raw.get(k) for k in _NOTE_FIELDS}
    if not all(isinstance(v, str) for v in row.values()) or not row["id"]:
        return None
    dismissed = raw.get("dismissed", False)
    if not isinstance(dismissed, bool):
        return None
    return {**row, "dismissed": dismissed}


def _fields_entry(raw: object) -> dict[str, str] | None:
    if not isinstance(raw, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
        return None
    return dict(raw)


def _refused(why: str, strict: bool) -> dict:
    """What a read makes of a record it cannot use: `RecordUnreadableError`
    (strict), else an empty record."""
    if strict:
        raise RecordUnreadableError(f"the retirement record {why}; it is left as it is")
    return _empty()


def _parsed(text: str, *, strict: bool) -> dict:
    """The record in `text`, checked; malformed parts dropped (soft) or
    `RecordUnreadableError` (strict)."""
    if not text.strip():
        return _refused("is empty", strict)
    try:
        data = json.loads(text)
    except ValueError:
        return _refused("is not JSON", strict)
    if not isinstance(data, dict):
        return _refused("is not an object", strict)
    fields_raw = data.get("fields", {})
    notes_raw = data.get("notes", [])
    if not isinstance(fields_raw, dict) or not isinstance(notes_raw, list):
        return _refused("does not have the record's shape", strict)
    fields = {conn_id: _fields_entry(entry) for conn_id, entry in fields_raw.items()}
    notes = [_note_row(raw) for raw in notes_raw]
    if strict and (None in fields.values() or None in notes):
        return _refused("holds a malformed entry", strict)
    return {"fields": {k: v for k, v in fields.items() if v is not None},
            "notes": [row for row in notes if row is not None]}


def read(*, strict: bool = False) -> dict:
    """The record: `{"fields": {...}, "notes": [...]}`, empty when there is
    none. Fail-soft for display; `strict` for a writer and for every lookup
    that feeds a write (see the module docstring)."""
    try:
        text = path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty()
    except (OSError, UnicodeDecodeError) as exc:
        if strict:
            raise RecordUnreadableError(f"the retirement record could not be read: {exc}") \
                from exc
        return _empty()
    return _parsed(text, strict=strict)


def _write(doc: dict) -> None:
    fields = {k: dict(sorted(v.items())) for k, v in sorted(doc["fields"].items())}
    atomic.write_text(path(), json.dumps({"fields": fields, "notes": doc["notes"]},
                                         indent=2, ensure_ascii=False) + "\n")


def record_fields(conn_id: str, values: Mapping[str, str]) -> None:
    """Record `values` (a connection's non-empty legacy model fields) for
    `conn_id`, keeping any value already recorded for it and adding only the
    fields not there yet: the first values -- the ones C translated -- win
    (N20). Strict; writes nothing when nothing is new."""
    with _lock:
        doc = read(strict=True)
        held = doc["fields"].get(conn_id, {})
        merged = {**{k: str(v) for k, v in values.items()}, **held}
        if merged == held:
            return
        doc["fields"][conn_id] = merged
        _write(doc)


def forget_fields(conn_id: str) -> bool:
    """Drop what the record holds for connection `conn_id` -- called in the
    hold that deletes that connection (`llm_connections.delete_connection`),
    so a provider later created under the same slug is never answered with
    the dead one's fields (N20). Returns whether it wrote. Strict: a record
    that cannot be read raises, and the delete with it."""
    with _lock:
        doc = read(strict=True)
        if conn_id not in doc["fields"]:
            return False
        del doc["fields"][conn_id]
        _write(doc)
        return True


def record_notes(notes: Iterable[Note]) -> None:
    """Merge `notes` into the record by id: a new one is added, a known one
    keeps its `dismissed` -- merging never un-dismisses -- and takes the
    newest wording. Strict; writes nothing when nothing changed."""
    incoming = list(notes)
    if not incoming:
        return
    with _lock:
        doc = read(strict=True)
        by_id = {row["id"]: row for row in doc["notes"]}
        changed = False
        for note in incoming:
            row = {**note._asdict(), "dismissed": False}
            held = by_id.get(note.id)
            if held is None:
                doc["notes"].append(row)
                by_id[note.id] = row
                changed = True
            elif {k: held[k] for k in _NOTE_FIELDS} != note._asdict():
                held.update(note._asdict())
                changed = True
        if changed:
            _write(doc)


def dismiss(note: Note | str) -> bool:
    """Mark a note dismissed; returns whether the record now holds it so.

    A note id the record does not hold is unknown here (False); a `Note` it
    does not hold -- one the planner computed before retirement recorded it
    -- is recorded, already dismissed (N14). Strict."""
    nid = note if isinstance(note, str) else note.id
    with _lock:
        doc = read(strict=True)
        for row in doc["notes"]:
            if row["id"] == nid:
                if not row["dismissed"]:
                    row["dismissed"] = True
                    _write(doc)
                return True
        if isinstance(note, str):
            return False
        doc["notes"].append({**note._asdict(), "dismissed": True})
        _write(doc)
        return True
