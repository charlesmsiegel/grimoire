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
_SAFE_KEY = re.compile(r"[^A-Za-z0-9._-]+")


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
    # The key is sanitized rather than used raw: a location id containing `/`
    # would produce `ovr-foo/bar-0`, which fails `_valid_id`, cannot be passed
    # through the DELETE route as one path segment, and gets regenerated into
    # the same invalid form on every read.
    safe = _SAFE_KEY.sub("-", key) or "loc"
    candidate = f"ovr-{safe}-{n}"
    suffix = 2
    while candidate in taken:
        candidate = f"ovr-{safe}-{n}-{suffix}"
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
        was_collision = _valid_id(rid) and rid in collided
        if not _valid_id(rid) or rid in collided or rid in taken_ids:
            record["id"] = _generated_id(key, n, taken_ids)
            changed = True
        taken_ids.add(record["id"])

        # `tiebreak` is deliberately not the id: splitting reassigns ids, and a
        # fragment with a fresh id would take a fresh position in the ordering,
        # so clearing a range inside one span could flip which override wins in
        # a range the user never touched. Fragments of one span therefore
        # *share* a tiebreak on purpose, which is why this is not a blanket
        # uniqueness sweep.
        #
        # An id collision is the exception. Two cloned records carrying the
        # same explicit id can also carry the same explicit tiebreak; giving
        # them fresh ids alone leaves identical precedence tuples when source,
        # seq and set_at match too, and the winner falls back to array order —
        # the one thing the backstop exists to prevent.
        if (not isinstance(record.get("tiebreak"), str) or not record["tiebreak"]
                or was_collision):
            record["tiebreak"] = record["id"]
            changed = True

        # `suppress` is read as a container during resolution, so a truthy
        # non-list like `"suppress": 1` raises TypeError out of the weather GET
        # and out of prompt assembly — past the loader's malformed-file
        # tolerance. Normalized to known axes here, and dropped when empty.
        if "suppress" in record:
            raw = record["suppress"]
            clean = ([a for a in raw if a in AXES] if isinstance(raw, (list, tuple)) else [])
            if clean != raw:
                changed = True
            if clean:
                record["suppress"] = clean
            else:
                record.pop("suppress", None)

        # `note` is read with `.strip()` when resolution collects it for the
        # prompt, so a truthy non-string raises AttributeError there.
        if not isinstance(record.get("note"), str):
            if "note" in record:
                changed = True
            record["note"] = ""

        # `set_at` is compared inside the precedence tuple, so a truthy
        # non-string raises TypeError against a neighbouring string — out of
        # resolution and into prompt assembly, past the loader's tolerance.
        if not isinstance(record.get("set_at"), str):
            record["set_at"] = ""
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
        _write(cid, out, best_effort=True)
    return out


class OverrideWriteError(Exception):
    """A write that the caller must not report as success."""


def _write(cid: str, data: dict[str, list[dict]], *, best_effort: bool = False) -> None:
    """Persist the store.

    ``best_effort`` is for the load-repair pass only, where a read-only store
    should still resolve from what was parsed. Authoring writes raise instead:
    swallowing the error there makes PUT, clear, resume and delete all report
    success while `weather.json` is untouched, and the weather reverts on the
    next reload with nothing having said so.
    """
    try:
        path(cid).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        if best_effort:
            return
        raise OverrideWriteError(str(e)) from e


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
    if not end or blocks.at_block_start(minutes):
        return blocks.block_of(fixed, minutes)
    # A timed *end* inside a block rounds up to the next boundary. Returning the
    # containing block's start would make `09:00 to 10:00` resolve both
    # endpoints to the morning ordinal and match nothing — an override that
    # saves cleanly and never applies. Outward rounding means a start floors
    # and an exclusive end ceilings.
    o = blocks.next_ordinal(fixed, minutes)
    return (o // 5, o % 5)


def ordinal_of(point) -> int | None:
    """A stored `[fixed_day, block]` pair as a block ordinal."""
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        return None
    day, position = point
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (day, position)):
        return None
    return 5 * day + position


def _intersects(record: dict, lo: int, hi: int | None) -> bool:
    """Whether a span overlaps the half-open range [lo, hi) at all."""
    start = ordinal_of(record.get("from_fixed"))
    if start is None:
        return False
    end = ordinal_of(record.get("to_fixed")) if record.get("to_fixed") is not None else None
    if end is not None and end <= lo:
        return False
    return not (hi is not None and start >= hi)


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
    """manual beats extractor. Anything else sorts below both.

    Type-checked before the lookup: a hand-edited `"source": []` is valid JSON
    and unhashable, so using it as a dict key raises TypeError — out of
    resolution, through `current_weather`, and into prompt assembly, defeating
    the loader's malformed-file tolerance one layer further in.
    """
    source = record.get("source")
    if not isinstance(source, str):
        return 0
    return {"manual": 2, "extractor": 1}.get(source, 0)


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


def _only_axes(axes) -> tuple[str, ...]:
    """Keep just the real axes, in canonical order.

    `_cut` deletes each named key from the record, so an unexpected name like
    `to_fixed` would strip that span's own bounds — turning a bounded override
    into an open-ended one. Filtering here means no caller, route or otherwise,
    can reach span metadata through an axis list.

    Only ``None`` means "all axes". An explicitly empty list is a selection of
    nothing and stays empty: treating it as all three would let a client with
    no axes selected clear every override, or resume every inherited axis,
    instead of doing nothing.
    """
    if axes is None:
        return AXES
    wanted = set(axes)
    return tuple(a for a in AXES if a in wanted)


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
         axes: tuple[str, ...], field: str, *, truncate_open_ended: bool = True) -> int:
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
        # A bounded span keeps whatever lies past the range; an open-ended one
        # does not. Clearing an open-ended span truncates the *named* axes
        # permanently rather than splitting them, because a fresh open-ended
        # fragment a block later would resume the storm immediately and run
        # forever — the opposite of "clear it".
        #
        # That truncation is per axis, not per record. The remainder therefore
        # runs on open-ended carrying the axes the caller did not name; when
        # every axis was named it sets nothing and is dropped, which is the
        # truncate-and-discard behaviour falling out rather than special-cased.
        # `truncate_open_ended` is the clear-a-concrete-override rule, and only
        # that. Applying it to a bounded *resume* would strip the axis from an
        # open-ended suppression for all time — resuming one block would resume
        # every later one — when the caller asked for a range.
        bounded_tail = hi is not None and (
            end > hi if end is not None else not truncate_open_ended)
        middle = _split_axes(record, axes, field)
        middle["from_fixed"] = [max(start, lo) // 5, max(start, lo) % 5]
        if bounded_tail:
            middle["to_fixed"] = [hi // 5, hi % 5]
        elif end is None and not bounded_tail:
            middle["to_fixed"] = None
        if _sets_anything(middle):
            if start < lo:                   # a fresh id: the head kept the original
                middle["id"] = _generated_id(key, len(out), taken)
                taken.add(middle["id"])
            out.append(middle)
        if bounded_tail:
            tail = dict(record)
            tail["from_fixed"] = [hi // 5, hi % 5]
            if end is None:
                tail["to_fixed"] = None
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
    axes = _only_axes(axes)
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
        # Intersection with [lo, hi), not coverage at lo. Clearing the next
        # three days while a campaign-wide storm begins tomorrow would
        # otherwise create no suppression and leave the later part of the
        # requested range stormy here.
        inherited = tuple(
            a for a in axes
            if any(_intersects(r, lo, hi) and r.get(a) for r in data.get(DEFAULT_KEY, [])))
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
    axes = _only_axes(axes)
    key = location_id or DEFAULT_KEY
    data = read(cid)
    lo = ordinal_of(resolve_endpoint(provider, frm, end=False))
    if lo is None:
        return 0
    hi = _upper(provider, lo, to, blocks)
    touched = _cut(cid, key, data, lo, hi, axes, field="suppress",
                   truncate_open_ended=False)
    if touched:
        _write(cid, data)
    return touched
def put_ordinals(cid: str, location_id: str, native: str, start: int, end: int | None,
                 axes: dict, note: str = "", source: str = "manual",
                 suppress: list[str] | None = None) -> dict:
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
    if suppress:
        record["suppress"] = [a for a in suppress if a in AXES]
    data.setdefault(key, []).append(record)
    _write(cid, data)
    return record


def replace(cid: str, key: str, span_id: str, *, from_ordinal: int, to_ordinal: int | None,
            native: str, axes: dict, note: str = "",
            suppress: list[str] | None = None) -> dict | None:
    """Rewrite one span in place. Returns the new record, or None if not found.

    One operation, not a delete followed by a create. Editing a span through
    that pair leaves a window where the original is gone and its replacement
    has not landed: if the second call fails — a moment that no longer parses
    under a swapped provider, a read-only store — the override the user was
    merely renaming has been destroyed.

    `seq` and `tiebreak` carry over, because this is the same instruction
    edited rather than a new one: bumping them would move the record in the
    precedence order as a side effect of changing its note.
    """
    data = read(cid)
    for n, record in enumerate(data.get(key, [])):
        if record.get("id") != span_id:
            continue
        updated = {
            "id": record["id"], "tiebreak": record.get("tiebreak", record["id"]),
            "seq": record.get("seq", 0), "source": record.get("source", "manual"),
            "from": native, "to": None,
            "from_fixed": [from_ordinal // 5, from_ordinal % 5],
            "to_fixed": None if to_ordinal is None else [to_ordinal // 5, to_ordinal % 5],
            "note": note, "set_at": now_iso(),
        }
        for axis in AXES:
            if axes.get(axis):
                updated[axis] = axes[axis]
        if suppress:
            updated["suppress"] = [a for a in suppress if a in AXES]
        data[key][n] = updated
        _write(cid, data)
        return updated
    return None
