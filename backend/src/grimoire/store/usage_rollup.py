"""All-time money, cheap enough to ask for on every navigation.

``usage.lifetime_since`` backs the all-time cost view and says so in its own
docstring: "the one read here whose cost grows with the library's age, and it
is deliberate -- it backs the all-time view and nothing on the play path."
``routes/shell.py`` is the play path, on every navigation, and it shipped its
Costs row with no figure at all rather than either paying that scan or quietly
substituting a 30-day window -- which would give the same unlabelled number a
different meaning, the drift the three-money-columns rule exists to prevent.

This module is the maintained aggregate that sentence was waiting for. It does
not change what a figure means: what it reports is the same all-time rollup
``usage`` would compute, arrived at by not re-reading bytes it has already
read.

**The aggregate is derived, never authoritative.** ``<home>/usage/rollup.json``
can be deleted at any moment and the next read rebuilds it from the ledger. No
caller may ever treat it as a record of anything -- the JSONL files are the
ledger, and this is a bookmark in them.

How it stays cheap
------------------

The ledger is append-only by construction (``usage.record`` writes a single
``O_APPEND`` line and nothing here ever rewrites a row), so a reader that
remembers **how many bytes of each month file it has already folded in** can
fold in only what arrived since. The steady state is one ``stat`` per month
file plus a read of the handful of lines a turn appended -- which is what makes
this legal on a surface that runs on every navigation, where
``lifetime_since`` is not.

Four things force a full rebuild, and each is a case where the bookmark is
provably meaningless rather than merely old:

- **The rate table changed.** ``modelled_usd`` is arithmetic done against the
  user's own per-token table, so a stored figure is only true of the table it
  was computed under. The fingerprint is part of the file, and a mismatch
  discards the whole aggregate rather than leaving one column priced two ways.
- **A month file shrank.** The ledger only grows, so a file shorter than the
  bookmark into it is a file somebody rewrote by hand. Everything before the
  bookmark is then unverified.
- **A month file this aggregate had read is gone.** Its rows are still in the
  totals and can no longer be checked against anything.
- **The format version moved.** Same as having no file.

Concurrency
-----------

Unlocked, in both directions, and safe for the reason the ledger's own writes
are: two readers may scan and write concurrently, and each writes a *complete*
snapshot through ``atomic.write_text``, so the loser of the race is replaced by
a snapshot that is equally true. Nothing is lost because nothing here is a
source -- the worst outcome is that one of them did its scan for nothing. It
takes no campaign lock and needs none: the file is home-scoped, most ledger
rows belong to no campaign at all, and stalling a navigation behind a
minutes-long absorb to update a cache would cost the thing this module exists
to buy.

**Never raises.** Every entry point fail-softs to a direct scan, and a scan
that itself fails reports zeros with ``partial`` set -- because a cost surface
that cannot say how complete it is has to say *that*, which is the same
sentence ``unpriced_calls`` carries one level down.

One note on the reach into ``usage``'s privates (``_ZERO``, ``_add``,
``_is_call``, ``_rounded``, ``_MONTH``). It is deliberate and it is the point:
this module must fold a row into a bucket **exactly** as every other rollup in
the app does, down to the cache-pair rule and the three-column split, or the
figure on the rail would be a second opinion about what a call cost. A public
wrapper per helper would move the names without moving that dependency, and a
private copy of ``_add`` is the drift itself. ``routes/shell.py`` already
reaches for ``scenes._model_blocks`` on the same reasoning.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import atomic, pricing, usage

#: Bumped when the stored shape changes. A file from an older version is
#: discarded rather than migrated: it is a cache, and rebuilding it costs one
#: scan that the very next read would otherwise have had to do anyway.
VERSION = 2

#: What a caller gets for a campaign the ledger has never mentioned, and what a
#: failed scan degrades to. `partial` is the field that keeps it honest -- see
#: `_empty`.
_FIELDS = ("calls", "cost_usd", "estimated_usd", "modelled_usd",
           "unpriced_calls", "unmetered_calls", "subscription_calls",
           "modelled_calls", "priced_calls", "total_tokens")


def rollup_path() -> Path:
    return usage.ledger_dir() / "rollup.json"


def campaign_totals(cid: str) -> dict:
    """All-time money for one campaign, priced at the user's current rates.

    The three columns, never summed, plus the counts that say how complete they
    are. ``partial`` is True only when the aggregate could not be brought up to
    date at all -- that is the one case a caller must not render as a
    measurement. A campaign the ledger has simply never mentioned is a real
    zero and says so, because "nothing was spent here" is an answer and
    "nobody could look" is not.

    **There is deliberately no ``forget``.** A campaign id is a slug and a slug
    is reusable, so a replacement campaign of the same name inherits the dead
    one's rows -- but it inherits them from the *ledger*, which is append-only
    and keeps them by design, and every other all-time surface
    (``usage.campaign_scenes``, the Costs page) reports the same thing. A cache
    that answered differently from the scan it stands in for would put two
    figures on the screen with nothing to say which was right, which is the
    whole failure this module is built to avoid.
    """
    data = _totals()
    bucket = data["campaigns"].get(cid)
    if bucket is not None:
        return bucket
    return _empty() if data["partial"] else _zero()


def library_totals() -> dict:
    """All-time money across everything the ledger holds, campaign or not."""
    return _totals()["all"]


# --- the aggregate ----------------------------------------------------------


def _zero() -> dict:
    """Nothing was spent here, and that is a measurement."""
    return {**dict.fromkeys(_FIELDS, 0), "partial": False}


def _empty() -> dict:
    """Zeros, marked as an answer nobody could compute.

    `partial` rather than `None` for the whole payload: the shape a caller
    renders should not change with whether the scan worked, or every consumer
    grows a second branch. What changes is the one field that says whether to
    believe it -- and it is the difference between `$0.00` and "not counted".
    """
    return {**_zero(), "partial": True}


def _fresh() -> dict:
    return {"version": VERSION, "rates": _rates_fingerprint(),
            "months": {}, "all": dict(usage._ZERO), "campaigns": {}}


def _rates_fingerprint() -> str:
    """A stable digest of the user's per-token table.

    Sorted keys so two reads of one unchanged file agree, and a digest rather
    than the table itself so the aggregate does not grow a second copy of a
    file that has its own home. A table that will not read is `{}`, which is
    the same value `Rates.current` prices against -- so the fingerprint follows
    the pricing rather than second-guessing it.
    """
    try:
        table = pricing.read_pricing()
    except (OSError, ValueError):
        table = {}
    blob = json.dumps(table, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _load() -> dict | None:
    """The stored aggregate if it is usable for the table in force now."""
    try:
        raw = rollup_path().read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return None
    if data.get("rates") != _rates_fingerprint():
        return None
    months, all_, camps = data.get("months"), data.get("all"), data.get("campaigns")
    if not isinstance(months, dict) or not isinstance(all_, dict) \
            or not isinstance(camps, dict):
        return None
    return data


def _write(data: dict) -> None:
    """Best-effort. A cache that cannot be written costs the next reader one
    scan, which is precisely what it would have cost with no cache at all."""
    try:
        path = rollup_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_text(path, json.dumps(data, allow_nan=False) + "\n")
    except (OSError, TypeError, ValueError):
        return


def _month_files() -> dict[str, Path]:
    """Every month file in the ledger, keyed by its ``YYYY-MM`` stem.

    Globbed rather than derived from a window, unlike `usage._window_files`:
    the window here is all of time, and the directory is the only thing that
    knows how far back that goes. Names that are not a month are ignored --
    `rollup.json` is in this directory and must not be mistaken for a ledger.
    """
    out: dict[str, Path] = {}
    try:
        entries = list(usage.ledger_dir().iterdir())
    except OSError:
        return out
    for path in entries:
        if path.suffix == ".jsonl" and usage._MONTH.match(path.stem):
            out[path.stem] = path
    return out


def _totals() -> dict:
    """The aggregate, brought up to date with whatever the ledger has grown.

    Returns the stored shape (`all`, `campaigns`, both rounded and marked
    complete) or, when nothing could be read at all, a payload whose buckets
    are all `partial`.
    """
    try:
        return _refresh()
    except (OSError, ValueError, TypeError):
        # A scan that failed outright. Reporting zeros as fact here is the one
        # thing a cost surface may not do, so every bucket comes back `partial`
        # and the callers render it as "not counted" rather than as $0.00.
        return {"all": _empty(), "campaigns": {}, "partial": True}


def _refresh() -> dict:
    data = _load()
    files = _month_files()
    if data is None:
        data = _fresh()
    else:
        # A month whose file is gone takes the aggregate with it: its rows are
        # inside these totals and there is nothing left to check them against.
        # Same for one that shrank -- the ledger only ever grows, so a file
        # shorter than the bookmark into it was rewritten by hand.
        stale = any(stem not in files for stem in data["months"])
        if not stale:
            for stem, read in data["months"].items():
                try:
                    if files[stem].stat().st_size < _int(read):
                        stale = True
                        break
                except OSError:
                    stale = True
                    break
        if stale:
            data = _fresh()

    rates = usage.Rates.current()
    months = data["months"]
    changed = False
    for stem in sorted(files):
        read = _int(months.get(stem))
        consumed = _fold(files[stem], read, data, rates)
        if consumed != read:
            months[stem] = consumed
            changed = True
    if changed or _load() is None:
        _write(data)
    return _report(data)


def _fold(path: Path, start: int, data: dict, rates: usage.Rates) -> int:
    """Fold every complete line after byte ``start`` into ``data``.

    Returns the offset actually consumed, which is the start of the trailing
    partial line when there is one. A torn write -- `atomic.append_line`
    documents how one can be produced -- must not be counted now and skipped
    forever, so the bookmark stops in front of it and the next read tries
    again once the rest of the line has landed.

    Opened in binary and split on ``\\n`` for the same reason the offset is in
    bytes: a text-mode file object's ``tell()`` is an opaque cookie, not a
    count of anything, and the whole scheme rests on the number meaning what
    ``st_size`` means.
    """
    try:
        with open(path, "rb") as f:
            f.seek(start)
            blob = f.read()
    except OSError:
        return start
    if not blob:
        return start
    # `keepends` so the consumed byte count is exact, and the final element is
    # identifiable as unterminated by what it does NOT end with.
    lines = blob.splitlines(keepends=True)
    if lines and not lines[-1].endswith(b"\n"):
        lines.pop()
    consumed = start
    for raw in lines:
        consumed += len(raw)
        row = _row(raw)
        if row is None or not usage._is_call(row):
            continue
        usage._add(data["all"], row, rates)
        cid = row.get("campaign")
        if isinstance(cid, str) and cid:
            bucket = data["campaigns"].setdefault(cid, dict(usage._ZERO))
            usage._add(bucket, row, rates)
    return consumed


def _row(raw: bytes) -> dict | None:
    """One ledger line as a row, or None to skip it.

    Deliberately *not* `usage._row`, which also filters on a date window: this
    aggregate's window is all of time, and a row whose `ts` is unreadable is
    still a call that was made and paid for. Dropping it here would make the
    all-time total disagree with the all-time view over exactly the rows a
    hand edit damaged.
    """
    if not raw.strip():
        return None
    try:
        row = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return row if isinstance(row, dict) else None


def _report(data: dict) -> dict:
    """The stored buckets as callers see them: rounded, and complete."""
    return {
        "all": _public(data["all"]),
        "campaigns": {cid: _public(b) for cid, b in data["campaigns"].items()},
        "partial": False,
    }


def _public(bucket: dict) -> dict:
    rounded = usage._rounded(bucket)
    return {**{f: rounded.get(f, 0) for f in _FIELDS}, "partial": False}


def _int(value: object) -> int:
    """A byte offset off a file a human can edit. Anything that is not a
    non-negative int is 0, which re-reads the month from the top -- correct,
    and cheaper than deciding the whole aggregate is unusable."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)
