"""How long a campaign has owed what it owes: overdue and stale (#103).

Two questions about the same ledger, and they are not the same question:

- **overdue** — a commitment's deadline is behind the campaign's present. Only
  a commitment can be overdue, and only one whose `due` names a day this
  calendar can parse: `due` is free text in the campaign's own reckoning
  (`commitments`' module docstring), so "before the harvest moon" is a real
  deadline that no arithmetic can place. It ages by staleness alone, and saying
  nothing about it is the honest answer.
- **stale** — nothing has moved this thread or commitment for longer than the
  campaign's threshold. Every record can go stale, because every record carries
  the scene that last moved it, and a scene carries a date.

Read-time and pure: nothing here writes, and no record stores `overdue_since`.
The clock can move backwards (a correction), a scene's date can be edited, and
a beat can be dropped by a cascade — stored derived state would then say a
thing the files no longer support, and nothing would recompute it. #103's
option C proposes stamping these on advance for "warn once" semantics; that
belongs to #106, which needs a *notice* to have been shown, not a classification
to have been made.

The arithmetic is exact integer math on the fixed-day axis
(`calendars.fixed_of`), so it holds for Harptos and Hebrew as much as for
Gregorian — there is no `datetime.date` anywhere in this module, deliberately.

**`prepare` then `annotate`**: the context (provider, threshold, "now", the
scene-date index) is read once and handed to each list, because both callers
age two lists at a time — the ledger route ages threads and commitments in one
response, `clock.digest` ages both against the moment an advance would land on.
Resolving all of that twice would read the chronicle twice and, worse, could
answer the two lists against two different presents.
"""

from __future__ import annotations

from . import calendars, chronicle, fieldtext
from .campaigns import paths as campaigns_paths
from .scenes import read as scenes_read

#: The three answers. `ok` is not "nothing to say" — it is "this is inside the
#: campaign's own patience", which is a fact the ledger renders as an unmarked
#: row.
OK, STALE, OVERDUE = "ok", "stale", "overdue"

#: Overdue outranks stale on a record that is both, and the row still carries
#: both numbers. A deadline that has passed is what the reader has to act on;
#: "and nobody has mentioned it in six weeks" is the same news, twice.


def _blank_ctx() -> dict:
    return {"provider": None, "now": "", "now_fixed": None,
            "stale_after": calendars.STALE_AFTER_DAYS, "dates": {}, "_scenes": {}}


def prepare(cid: str, now: str) -> dict:
    """Everything the classifier needs, read once.

    `{"provider", "now", "now_fixed", "stale_after", "dates", "_scenes"}` —
    where `dates` is the chronicle's per-scene in-world date and `_scenes` is
    the lazy cache behind the per-scene fallback below.

    `now` is handed in rather than read from `clock`: this module must not
    import it (`clock` reads the chronicle, which reads scenes, and `clock`
    imports this one to age its digest — the other direction closes a cycle),
    and, more usefully, the caller decides *which* present it is asking about.
    `clock.digest` asks about the moment an advance would land on, which is the
    only way a preview can say what a skip is about to make overdue.

    Every failure degrades to "cannot tell": a calendar that will not load, a
    `now` this calendar cannot parse, a garbled chronicle. `annotate` then
    returns `ok` rows with empty numbers, which is what an unaged ledger looked
    like before this module existed.
    """
    ctx = _blank_ctx()
    croot = campaigns_paths.campaign_root(cid)
    ctx["cid"] = cid
    ctx["now"] = now or ""
    ctx["stale_after"] = calendars.stale_after_days(croot)
    provider = calendars.primary_provider(croot)
    if provider is None or not now:
        return ctx
    ctx["provider"] = provider
    try:
        ctx["now_fixed"] = calendars.fixed_of(provider, now)
    except calendars.CalendarError:
        return ctx
    try:
        records = chronicle.read_chronicle(cid)
    except Exception:  # noqa: BLE001 — garbled chronicle.json: no dates, no crash
        records = {}
    if isinstance(records, dict):
        ctx["dates"] = {sid: fieldtext.text(r.get("date"))
                        for sid, r in records.items() if isinstance(r, dict)}
    return ctx


def _scene_date(ctx: dict, sid: str) -> str:
    """When the scene that last moved a record happened, in native notation.

    The scene's own `time_history` first and the chronicle second, in that
    order and not the other way round: `scenes.set_datetime` normalizes what it
    stores through the campaign's provider, while a chronicle `date` is whatever
    the absorb extracted and may never have been through a calendar at all. The
    chronicle still answers for the scenes the reader never dated by hand, which
    is most of them.

    Cached per scene id on the context, because a campaign's threads and
    commitments cluster on the same handful of recent scenes and each miss is a
    frontmatter read.
    """
    if not sid:
        return ""
    cached = ctx["_scenes"].get(sid)
    if cached is not None:
        return cached
    found = ""
    try:
        history = scenes_read.get_time_history(ctx["cid"], sid)
        found = history[-1] if history else ""
    except Exception:  # noqa: BLE001 — a deleted or unreadable scene: fall through
        found = ""
    found = found or ctx["dates"].get(sid, "")
    ctx["_scenes"][sid] = found
    return found


def _days_since(ctx: dict, sid: str) -> int | None:
    """Days from the record's last movement to `now`, or None when unknowable.

    Signed, and only the positive side classifies: a scene dated *after* the
    campaign's present is an ordinary thing (a flashforward, a clock corrected
    backwards), and reporting -12 days as staleness would put a thread nobody
    has touched at the top of the ledger for being touched too recently.
    """
    when = _scene_date(ctx, sid)
    if ctx["provider"] is None or ctx["now_fixed"] is None or not when:
        return None
    try:
        return ctx["now_fixed"] - calendars.fixed_of(ctx["provider"], when)
    except calendars.CalendarError:
        return None


def _due_delta(ctx: dict, due: str) -> int | None:
    """Days from `now` to the deadline (negative once it is behind), or None.

    None means "this deadline is not a date": free text, or a date in a notation
    the campaign's current calendar cannot read. Both are ordinary — `due` is
    written in the fiction's own words — so neither is an error and neither
    makes the record overdue.
    """
    if ctx["provider"] is None or ctx["now_fixed"] is None or not due:
        return None
    try:
        return calendars.fixed_of(ctx["provider"], due) - ctx["now_fixed"]
    except calendars.CalendarError:
        return None


def age(ctx: dict, row: dict) -> dict:
    """One record's aging block: `{state, days_since, days_over, due_in}`.

    `days_over` and `due_in` are the two sides of the same number and never both
    set: past the deadline the row reports how far past, before it how far
    ahead. The second is not this issue's job — it is what #106 warns from — but
    it falls out of the same subtraction, and computing it here keeps the one
    place that knows how to read a `due` from being written twice.

    A row with no `due` key (a plot thread) simply has no deadline to miss.
    """
    days_since = _days_since(ctx, fieldtext.text(row.get("last_scene")))
    delta = _due_delta(ctx, fieldtext.text(row.get("due")))
    days_over = -delta if delta is not None and delta < 0 else None
    due_in = delta if delta is not None and delta >= 0 else None
    if days_over is not None:
        state = OVERDUE
    elif days_since is not None and days_since >= ctx["stale_after"]:
        state = STALE
    else:
        state = OK
    return {"state": state, "days_since": days_since,
            "days_over": days_over, "due_in": due_in}


def annotate(ctx: dict, rows: list[dict]) -> list[dict]:
    """`rows` with an `aging` block added to each. Order is the caller's.

    Deliberately not a re-sort. The ledger orders by `last_scene` and the digest
    by nothing at all; putting the overdue rows on top is a rendering decision,
    and one the panel can make from the field this adds. Sorting here would also
    make an aged list and an unaged one differ in more than one way, which is a
    poor thing to ask a reader to hold.
    """
    return [{**row, "aging": age(ctx, row)} for row in rows if isinstance(row, dict)]


def summary(rows: list[dict]) -> dict:
    """`{overdue, stale}` counts over annotated rows — the digest's headline.

    A skip that makes four things overdue should be able to say so in one line;
    counting in the panel would mean every consumer of these rows implementing
    the same two filters.
    """
    states = [r.get("aging", {}).get("state") for r in rows if isinstance(r, dict)]
    return {"overdue": states.count(OVERDUE), "stale": states.count(STALE)}
