"""The campaign-local override store: `campaigns/<cid>/weather.json`.

A JSON sidecar keyed by location id, with `_default` applying campaign-wide.
Campaign-local by construction — never a world file — so `sync.py`'s hash
diffing of entity blobs is undisturbed.

Each record is a *span*, not a point, because GM fiat is usually "it snows for
three days" and per-block rows for that would be absurd. Spans are half-open on
**block ordinals**: `from <= t < to`. Matching on raw minutes would split
blocks, so a date-only override starting at midnight would apply at 01:00 but
not at 23:00 the previous evening even though both share one `night` ordinal —
the same block returning two skies depending which minute was asked about,
which is the exact thing giving the post-midnight hours to the preceding date
exists to prevent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .. import calendars, campaigns
from ..paths import now_iso
from . import blocks

AXES = ("condition", "temperature", "wind")
DEFAULT_KEY = "_default"

# Ids appear as a path segment in the DELETE route. `/` is unaddressable there
# even percent-encoded, and `.`/`..` satisfy the character class but are
# normalized away by URL parsers before the request is sent — both leave an
# accepted span that nothing can remove.
_ID = re.compile(r"[A-Za-z0-9._-]+")


def _valid_id(value) -> bool:
    return isinstance(value, str) and bool(_ID.fullmatch(value)) and bool(value.strip("."))


def path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "weather.json"


def _generated_id(key: str, n: int, taken: set[str]) -> str:
    """A fresh id, unique per storage key.

    Uniqueness per key is the whole requirement. Deriving ids from content was
    tried twice and collided both times — a subset omitting the location key
    gave one storm at two places the same id, and a subset omitting `set_at`
    collided for hand-authored records differing only in when they were
    written, which precedence explicitly treats as a tiebreak.
    """
    candidate = f"ovr-{key}-{n}"
    suffix = 2
    while candidate in taken:
        candidate = f"ovr-{key}-{n}-{suffix}"
        suffix += 1
    return candidate


def _fingerprint(record: dict) -> str:
    """Every field, for detecting records nothing can tell apart."""
    return json.dumps({k: v for k, v in sorted(record.items()) if k != "id"},
                      sort_keys=True, default=str)


def _repair_key(key: str, records: list, taken_ids: set[str]) -> tuple[list[dict], bool]:
    """Normalize one storage key's records. Returns (records, changed)."""
    changed = False
    out: list[dict] = []
    seen_fingerprints: set[str] = set()

    # Explicit ids are checked for uniqueness here: two hand-authored records
    # under one key can otherwise share an id while differing in bounds, giving
    # them one DELETE address and making them indistinguishable to the id
    # tiebreak. A collision re-derives both.
    explicit: dict[str, int] = {}
    for record in records:
        if isinstance(record, dict) and _valid_id(record.get("id")):
            explicit[record["id"]] = explicit.get(record["id"], 0) + 1
    collided = {i for i, n in explicit.items() if n > 1}

    for n, record in enumerate(records):
        if not isinstance(record, dict):
            changed = True
            continue  # a hand-edited scalar in the array; drop it rather than raise

        fingerprint = _fingerprint(record)
        if fingerprint in seen_fingerprints:
            # Identical in every field: genuinely indistinguishable, so keeping
            # the pair serves nobody.
            changed = True
            continue
        seen_fingerprints.add(fingerprint)

        record = dict(record)
        rid = record.get("id")
        if not _valid_id(rid) or rid in collided or rid in taken_ids:
            record["id"] = _generated_id(key, n, taken_ids)
            changed = True
        taken_ids.add(record["id"])

        # `tiebreak` is deliberately not the id: splitting reassigns ids, and a
        # fragment with a fresh id would take a fresh position in the ordering,
        # so clearing a range inside one span could flip which override wins in
        # a range the user never touched.
        if not isinstance(record.get("tiebreak"), str) or not record["tiebreak"]:
            record["tiebreak"] = record["id"]
            changed = True

        if not isinstance(record.get("seq"), int) or isinstance(record.get("seq"), bool):
            # Every legacy and hand-authored record reads as 0, which needs no
            # migration pass: such records keep their old ordering among
            # themselves and lose to anything written afterwards.
            record["seq"] = 0
            changed = True

        out.append(record)
    return out, changed


def read(cid: str) -> dict[str, list[dict]]:
    """Every span, repaired. Never raises; a broken file reads as empty."""
    try:
        raw = json.loads(path(cid).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}

    out: dict[str, list[dict]] = {}
    dirty = False
    # Shared across keys, not per key: DELETE addresses a span by id alone, so
    # two hand-authored records under different locations sharing one id would
    # make that route ambiguous. Generated ids embed the key and cannot collide
    # anyway; this only ever re-derives an explicit duplicate.
    taken_ids: set[str] = set()
    for key, records in raw.items():
        if not isinstance(key, str) or not isinstance(records, list):
            dirty = True
            continue
        repaired, changed = _repair_key(key, records, taken_ids)
        dirty = dirty or changed
        if repaired:
            out[key] = repaired
    if dirty:
        # Derived exactly once: ids and tiebreaks become stored facts rather
        # than recomputations, so a loader refactor cannot quietly move a
        # record's DELETE address or reverse a precedence winner.
        _write(cid, out)
    return out


def _write(cid: str, data: dict[str, list[dict]]) -> None:
    try:
        path(cid).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass  # read-only store: resolution still works from what we parsed


def next_seq(data: dict[str, list[dict]], key: str) -> int:
    """One past the current maximum for this storage key."""
    return max((r.get("seq", 0) for r in data.get(key, [])), default=0) + 1


def resolve_endpoint(provider, native: str | None, *, end: bool) -> tuple[int, int] | None:
    """A native moment as `(fixed_day, block_position)`, or None for open-ended.

    A date-only endpoint resolves in *blocks*, not minutes: `from: D` is D's
    first owned block and `to: D` is one past D's last, so `from: D, to: D`
    covers exactly the five blocks D owns. Stating it in minutes does not work
    — midnight-to-midnight plus outward rounding expands backwards into D-1's
    night and forwards into D+1, so a one-day span silently covers parts of
    three dates.
    """
    if native is None:
        return None
    fixed = calendars.fixed_of(provider, native)
    minutes = calendars.minutes_of(native)
    if minutes is None:
        # Date-only. Outward rounding: the whole of D, and `to` is exclusive.
        return (fixed, 0) if not end else (fixed + 1, 0)
    return blocks.block_of(fixed, minutes)


def ordinal_of(point) -> int | None:
    """A stored `[fixed_day, block]` pair as a block ordinal."""
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        return None
    day, position = point
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (day, position)):
        return None
    return 5 * day + position


def covers(record: dict, ordinal: int) -> bool:
    """Half-open on block ordinals: from <= t < to, with `to: null` open-ended.

    Compared on the resolved fixed coordinates, never on the stored strings.
    `PUT /campaigns/{cid}/calendar` can swap the primary provider after
    overrides exist, and native dates are not lexicographically ordered under
    every provider anyway — the shipped Hebrew calendar formats with a month
    *name*, so `5784-nisan-01` and `5784-tishrei-01` sort alphabetically into
    the wrong order. String comparison would silently match the wrong spans
    rather than fail loudly.
    """
    start = ordinal_of(record.get("from_fixed"))
    if start is None or ordinal < start:
        return False
    if record.get("to_fixed") is None:
        return True  # open-ended: runs until stopped
    end = ordinal_of(record.get("to_fixed"))
    return end is not None and ordinal < end


def _rank(record: dict) -> int:
    """manual beats extractor. Anything else sorts below both."""
    return {"manual": 2, "extractor": 1}.get(record.get("source"), 0)


def _precedence(record: dict, key: str) -> tuple:
    """Sort key, greatest wins. Never array order.

    Specificity first — a span keyed by this location beats one keyed by
    `_default`. Then recency by `seq`, a monotonic per-key integer rather than
    `set_at`: `now_iso()` formats only to whole seconds, so two spans written
    in the same second tie and fall through to the backstop, which can hand the
    argument to the *earlier* instruction — and a GM adjusting an override
    twice in quick succession is exactly when recency matters most.

    `tiebreak` is the backstop so the result never depends on file ordering.
    """
    return (_rank(record),
            0 if key == DEFAULT_KEY else 1,
            record.get("seq", 0),
            record.get("set_at") or "",
            record.get("tiebreak") or "")


def winner(data: dict[str, list[dict]], location_id: str | None, ordinal: int, axis: str):
    """The record deciding `axis` at this moment, or None.

    Applied per axis rather than per record, so a manual `condition` and a
    procedural wind coexist — which is what narration usually gives us, since
    "it was raining" says nothing about wind.
    """
    best = None
    best_key = None
    for key in (DEFAULT_KEY, location_id):
        if not key:
            continue
        for record in data.get(key, []):
            if not covers(record, ordinal):
                continue
            if axis in (record.get("suppress") or []):
                # Forces this axis back to procedural — used to shadow an
                # inherited _default override at one location. A field of its
                # own rather than a reserved value inside an axis, so it cannot
                # be confused with an authored condition of the same name.
                candidate = ("suppress", record)
            elif record.get(axis):
                candidate = ("set", record)
            else:
                continue
            if best is None or _precedence(record, key) > _precedence(best[1], best_key):
                best, best_key = candidate, key
    return best


def stack(data: dict[str, list[dict]], location_id: str | None, ordinal: int) -> list[dict]:
    """Every span covering this moment, strongest first — for the HUD."""
    rows = []
    for key in (DEFAULT_KEY, location_id):
        if not key:
            continue
        for record in data.get(key, []):
            if covers(record, ordinal):
                rows.append((_precedence(record, key), {**record, "location": key}))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [r[1] for r in rows]


def put(cid: str, provider, location_id: str, frm: str, to: str | None,
        axes: dict, note: str = "", source: str = "manual",
        suppress: list[str] | None = None) -> dict:
    """Write one span. Returns the stored record.

    Both native strings and resolved coordinates are stored: the natives are
    for display and re-editing, the coordinates are authoritative and survive
    a primary-provider change.
    """
    key = location_id or DEFAULT_KEY
    data = read(cid)
    record = {
        "id": _generated_id(key, len(data.get(key, [])),
                            {r["id"] for rs in data.values() for r in rs}),
        "from": frm, "to": to,
        "from_fixed": list(resolve_endpoint(provider, frm, end=False)),
        "to_fixed": (list(resolve_endpoint(provider, to, end=True))
                     if to is not None else None),
        "note": note, "source": source,
        "seq": next_seq(data, key), "set_at": now_iso(),
    }
    record["tiebreak"] = record["id"]
    for axis in AXES:
        if axes.get(axis):
            record[axis] = axes[axis]
    if suppress:
        record["suppress"] = [a for a in suppress if a in AXES]
    data.setdefault(key, []).append(record)
    _write(cid, data)
    return record


def delete(cid: str, span_id: str, storage_key: str | None = None) -> bool:
    """Retract a span outright, as if it had never been set.

    ``storage_key`` narrows the search. Ids are unique across the file, so it
    is not needed for correctness, but the caller knows which key it read the
    span from and a mismatch is a bug worth reporting as a 404 rather than
    silently deleting a span somewhere else.
    """
    data = read(cid)
    found = False
    for key, records in list(data.items()):
        if storage_key is not None and key != storage_key:
            continue
        kept = [r for r in records if r.get("id") != span_id]
        if len(kept) != len(records):
            found = True
        if kept:
            data[key] = kept
        else:
            del data[key]
    if found:
        _write(cid, data)
    return found


def _upper(provider, lo: int, to: str | None, blocks: int | None) -> int | None:
    """The exclusive upper bound: a block count, a native endpoint, or open.

    A count wins when given. The duration control offers "this block" and "the
    rest of today" as block counts, and turning those into native strings
    client-side would mean reimplementing the calendar's month lengths — the
    same reason the season tables come from the server.
    """
    if blocks is not None:
        return lo + max(1, int(blocks))
    if to is None:
        return None
    return ordinal_of(resolve_endpoint(provider, to, end=True))


def _split_axes(record: dict, axes: tuple[str, ...], field: str) -> dict:
    """A copy of ``record`` with ``axes`` removed from ``field``."""
    out = dict(record)
    if field == "suppress":
        out["suppress"] = [a for a in (record.get("suppress") or []) if a not in axes]
    else:
        for axis in axes:
            out.pop(axis, None)
    return out


def _sets_anything(record: dict) -> bool:
    return any(record.get(a) for a in AXES) or bool(record.get("suppress"))


def _cut(cid: str, key: str, data: dict, lo: int, hi: int | None,
         axes: tuple[str, ...], field: str) -> int:
    """Remove ``axes`` from every span under ``key`` intersecting [lo, hi).

    Splits a span whose remainder still applies outside the range. The earlier
    fragment keeps the original id and each later one gets a fresh id: copying
    the id onto both leaves two records sharing a DELETE address until some
    later load canonicalizes them, and regenerating both invalidates an id the
    client may have just been handed. Every fragment keeps the original's
    `tiebreak` and `seq`, so a clear in the middle of a span cannot change
    which override wins on either side of it.
    """
    out: list[dict] = []
    touched = 0
    taken = {r["id"] for rs in data.values() for r in rs}
    for record in data.get(key, []):
        start = ordinal_of(record.get("from_fixed"))
        end = ordinal_of(record.get("to_fixed")) if record.get("to_fixed") is not None else None
        outside = (start is None or (end is not None and end <= lo)
                   or (hi is not None and start >= hi))
        relevant = (any(record.get(a) for a in axes) if field != "suppress"
                    else bool(set(axes) & set(record.get("suppress") or [])))
        if outside or not relevant:
            out.append(record)
            continue
        touched += 1
        if start < lo:                       # head: untouched, keeps the id
            head = dict(record)
            head["to_fixed"] = [lo // 5, lo % 5]
            out.append(head)
        if end is None and hi is not None:
            # An open-ended span cut by a *bounded* range is truncated and
            # everything after is discarded, never split. Splitting would leave
            # a fresh open-ended fragment starting a block later, so the storm
            # would resume immediately and run forever — the opposite of "clear
            # it". An instruction that runs until stopped ends when stopped; a
            # user who wants a gap sets a new override after the clear.
            #
            # An open-ended *range* has no "after" to discard, so it falls
            # through and edits the span in place instead. Discarding there
            # would drop the record whole and take its other axes with it.
            continue
        middle = _split_axes(record, axes, field)
        middle["from_fixed"] = [max(start, lo) // 5, max(start, lo) % 5]
        if hi is not None and (end is None or end > hi):
            middle["to_fixed"] = [hi // 5, hi % 5]
        if _sets_anything(middle):
            if start < lo:                   # a fresh id: the head kept the original
                middle["id"] = _generated_id(key, len(out), taken)
                taken.add(middle["id"])
            out.append(middle)
        if hi is not None and (end is None or end > hi):
            tail = dict(record)
            tail["from_fixed"] = [hi // 5, hi % 5]
            tail["id"] = _generated_id(key, len(out), taken)
            taken.add(tail["id"])
            out.append(tail)
    if touched:
        if out:
            data[key] = out
        else:
            data.pop(key, None)
    return touched


def clear(cid: str, provider, location_id: str, frm: str, to: str | None,
          axes: tuple[str, ...] | list[str] | None = None, blocks: int | None = None) -> int:
    """Return axes to procedural over a range. One atomic write.

    Only spans stored under ``location_id`` are mutated. A `_default` span is
    inherited by every location in the campaign, so truncating it to clear the
    docks would clear everywhere else too, and skipping it would leave the
    docks overridden and the button ineffective. Neither is acceptable, so an
    inherited span is **suppressed rather than edited**: a location-scoped
    record naming the axis in its `suppress` list resolves as "no override
    here" and outranks `_default` by the ordinary specificity rule. Clearing a
    campaign-wide override for everyone is then a separate, explicit act —
    issuing the clear against `_default` itself.

    Clearing does not retract history. A storm set on day 10 and cleared on
    day 15 did storm for five days, and re-reading day 12 still shows it;
    `delete` is the more emphatic action for taking that back.
    """
    axes = tuple(axes) if axes else AXES
    key = location_id or DEFAULT_KEY
    data = read(cid)
    lo = ordinal_of(resolve_endpoint(provider, frm, end=False))
    if lo is None:
        return 0
    hi = _upper(provider, lo, to, blocks)

    touched = _cut(cid, key, data, lo, hi, axes, field="axes")

    if key != DEFAULT_KEY:
        # Anything still inherited over this range needs suppressing, or the
        # clear looks like it did nothing at the one place it was aimed.
        inherited = tuple(
            a for a in axes
            if any(covers(r, lo) and r.get(a) for r in data.get(DEFAULT_KEY, [])))
        if inherited:
            record = {
                "id": _generated_id(key, len(data.get(key, [])),
                                    {r["id"] for rs in data.values() for r in rs}),
                "from": frm, "to": to,
                "from_fixed": [lo // 5, lo % 5],
                "to_fixed": None if hi is None else [hi // 5, hi % 5],
                "suppress": list(inherited), "note": "", "source": "manual",
                "seq": next_seq(data, key), "set_at": now_iso(),
            }
            record["tiebreak"] = record["id"]
            data.setdefault(key, []).append(record)
            touched += 1

    if touched:
        _write(cid, data)
    return touched


def resume(cid: str, provider, location_id: str, frm: str, to: str | None,
           axes: tuple[str, ...] | list[str] | None = None, blocks: int | None = None) -> int:
    """Undo suppression, restoring an inherited override over a range.

    Axis-aware rather than a whole-record delete: one suppression routinely
    names several axes, since clearing all three at once produces exactly that,
    and dropping the record would restore inheritance for every axis it names —
    so resuming wind would silently re-enable an inherited condition override
    the user meant to keep suppressed.

    Without this the clear is a one-way door. A suppressed axis renders as
    generated, so *Clear override* does not appear for it, and setting a
    concrete value writes another local exception rather than restoring the
    campaign-wide one.
    """
    axes = tuple(axes) if axes else AXES
    key = location_id or DEFAULT_KEY
    data = read(cid)
    lo = ordinal_of(resolve_endpoint(provider, frm, end=False))
    if lo is None:
        return 0
    hi = _upper(provider, lo, to, blocks)
    touched = _cut(cid, key, data, lo, hi, axes, field="suppress")
    if touched:
        _write(cid, data)
    return touched
def put_ordinals(cid: str, location_id: str, native: str, start: int, end: int | None,
                 axes: dict, note: str = "", source: str = "manual") -> dict:
    """Write a span whose bounds are already block ordinals.

    The extractor works this way: narration gives a moment and sometimes a
    duration in blocks, never a pair of native endpoints, and round-tripping
    ordinals back through native strings just to re-parse them would lose the
    block alignment this store exists to keep.
    """
    key = location_id or DEFAULT_KEY
    data = read(cid)
    record = {
        "id": _generated_id(key, len(data.get(key, [])),
                            {r["id"] for rs in data.values() for r in rs}),
        "from": native, "to": None,
        "from_fixed": [start // 5, start % 5],
        "to_fixed": None if end is None else [end // 5, end % 5],
        "note": note, "source": source,
        "seq": next_seq(data, key), "set_at": now_iso(),
    }
    record["tiebreak"] = record["id"]
    for axis in AXES:
        if axes.get(axis):
            record[axis] = axes[axis]
    data.setdefault(key, []).append(record)
    _write(cid, data)
    return record
