"""Bounded ledger projections for summaries, scenes, campaigns and months."""

from __future__ import annotations

from datetime import date, timedelta

from . import _clock, _folding, _ledger, _rows


def summary(days: int = 30, campaign: str = "") -> dict:
    """Roll the last ``days`` calendar days (UTC) up into totals and breakdowns.

    ``days`` counts back **inclusive of today**, so ``days=1`` is today alone
    and the default 30 is today plus the 29 days before it. It is clamped to
    ``[1, MAX_DAYS]`` rather than rejected: this backs a dashboard control, and a
    silly number there should draw a chart, not a 422.

    ``campaign`` scopes every bucket to one campaign; the calls that belong to no
    campaign (taglines, connection tests) drop out entirely, which is what makes
    the per-campaign endpoint's total a campaign's own spend rather than the
    library's.

    ``session`` is the intersection of this process's lifetime and the window,
    not the lifetime alone, and the difference is real rather than theoretical:
    a backend left running for a month and asked for ``days=1`` reports a
    "session" that is really just today. Stated rather than fixed, because the
    fix is worse than the limit -- widening the scan to the process's whole
    lifetime would make a cheap query's cost depend on server uptime, for a
    number whose entire purpose is "what have I spent since I opened this". At
    the default 30 days it takes a month of continuous uptime to differ at all.
    """
    days = max(1, min(int(days), _clock.MAX_DAYS))
    until = _clock._today()
    since = (date.fromisoformat(until) - timedelta(days=days - 1)).isoformat()

    totals, session, today = dict(_rows._ZERO), dict(_rows._ZERO), dict(_rows._ZERO)
    by_day: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    by_task: dict[str, dict] = {}
    by_campaign: dict[str, dict] = {}
    rates = _folding.Rates.current()

    for row in _ledger._read_rows(since, until, campaign):
        if not _ledger._is_call(row):
            continue
        _folding._add(totals, row, rates)
        ts = row["ts"]
        if ts >= _clock._SESSION_START:
            _folding._add(session, row, rates)
        if ts[:10] == until:
            _folding._add(today, row, rates)
        _folding._add(by_day.setdefault(ts[:10], dict(_rows._ZERO)), row, rates)
        _folding._add(by_model.setdefault(_rows._label(row.get("model")), dict(_rows._ZERO)), row, rates)
        _folding._add(by_task.setdefault(_rows._label(row.get("task")), dict(_rows._ZERO)), row, rates)
        if isinstance(row.get("campaign"), str) and row["campaign"]:
            _folding._add(by_campaign.setdefault(row["campaign"], dict(_rows._ZERO)), row, rates)

    return {
        "days": days, "since": since, "until": until, "campaign": campaign,
        "generated_at": _clock._now(), "session_started": _clock._SESSION_START,
        "totals": _rows._rounded(totals), "session": _rows._rounded(session),
        "today": _rows._rounded(today),
        # Chronological, unlike the other three: a day breakdown is a time
        # series and a chart reads it left to right.
        "by_day": [{"key": day, **_rows._rounded(by_day[day])} for day in sorted(by_day)],
        "by_model": _rows._ranked(by_model), "by_task": _rows._ranked(by_task),
        "by_campaign": _rows._ranked(by_campaign),
    }


# ---- one scene's turns ----
#: How many rows `scene_usage` lists, newest first. The cap is on the RESPONSE,
#: not on the scan: the totals beside the list are summed over every row in the
#: window, so a scene played for a week still reports its true cost and only its
#: list is short. `truncated` says when that happened, because a list that
#: silently stops is a list somebody reads as complete.
SCENE_TURNS = 200

#: The tasks that re-answer a post somebody has already had an answer to. Not
#: "everything after the first call": a roll's `continuation` is the second half
#: of ONE answer, and counting it as a reroll would tell a player they had
#: rerolled a turn they never touched. Named here rather than in the view
#: because it is a fact about the task labels this module writes.
#:
#: `replay` IS one. A retcon replays the turns after a post -- the post itself
#: stands, so the replayed generation answers the same text a previous one
#: already answered, and the earlier take was cut. That is what a reroll is, and
#: leaving it out hid exactly the discarded generation this count exists to
#: show.
REROLL_TASKS = ("retry", "regenerate", "replay")


def _turn(row: dict, rates: _folding.Rates | None = None) -> dict:
    """One ledger row as the per-turn view sees it.

    Projected, never passed through. A row is a line in a file a human can edit,
    and handing the frontend whatever it holds is how a dict where a model name
    belongs takes a panel down — the judgement `_label` already makes for bucket
    keys. Counts come back through `_int` so the numbers a view sums are
    numbers, and `cost_usd` stays **None rather than 0.0** when the provider
    priced nothing, so a turn nobody costed reads as unpriced instead of free.
    """
    prompt = _rows._int(row.get("prompt_tokens"))
    completion = _rows._int(row.get("completion_tokens"))
    return {
        "ts": _rows._label(row.get("ts")), "task": _rows._label(row.get("task")),
        "model": _rows._label(row.get("model")), "status": _rows._label(row.get("status")),
        "error": row.get("error") if isinstance(row.get("error"), str) else "",
        # `_int` floors a hand-edited negative to 0, and a call that happened
        # was attempted at least once -- the field is only written when it is
        # more than one (`record`), so absent means exactly one too.
        "attempts": _rows._int(row.get("attempts")) or 1,
        "prompt_tokens": prompt, "completion_tokens": completion,
        # Same rule as `_add`: the cache pair is a slice OF the prompt (#148),
        # so it sits beside the total and never inside it.
        "total_tokens": prompt + completion,
        "cache_read_tokens": _rows._int(row.get("cache_read_tokens")),
        "cache_write_tokens": _rows._int(row.get("cache_write_tokens")),
        "cost_usd": _rows._float(row.get("cost_usd")),
        "cost_basis": _rows._label(row.get("cost_basis")) if row.get("cost_basis") else "",
        # What the user's own rates (#158) say this would have cost, for a turn
        # the provider priced at nothing. Beside `cost_usd` and never instead of
        # it: a turn with a real price keeps it, and a turn with neither stays
        # `null` in both columns so the view can still say "unpriced".
        "modelled_usd": None if _rows._float(row.get("cost_usd")) is not None
                        else (rates.estimate(row) if rates is not None else None),
        "post": _rows._post(row),
        "duration_ms": _rows._int(row.get("duration_ms")),
        # Labels for the view (spec 9.1), never figures: `billing` says a
        # subscription served the turn, and `tokens_estimated` that its counts
        # (and so any modelled figure) rest on a local count.
        "billing": _rows._text(row.get("billing")),
        "tokens_estimated": row.get("tokens_estimated") is True,
    }


#: How far `_scene_now` will follow a rename trail before giving up. A scene is
#: renamed once or twice in its life (the first date set, an edited title), so
#: any real chain is short -- and this is a file a human can edit, where
#: ``a -> b -> a`` is one typo away from looping forever inside a report.
_MAX_RENAMES = 64


def _rename_trail(campaign: str, since: str,
                  until: str) -> dict[str, list[tuple[str, str]]]:
    """Every hop this campaign's scenes made in the window, as
    ``old -> [(when, new), ...]`` oldest first.

    A LIST per id, not one hop, and that is the whole point of the shape. An id
    a scene is renamed off is free for the next scene to take (`paths.uniquify`
    checks only what exists now), and that scene can be renamed too -- so ``a``
    can genuinely hop to ``b`` and, later and as a different scene, to ``c``.
    Keeping only the last of those sent every row ever filed under ``a`` to
    ``c``, including the ones written before ``b`` existed.

    Sorted by stamp so `_scene_now` can take the FIRST hop at or after a row's
    own stamp, which is the one that was in force when the row was written.

    A pass of its own, and a cheap one: a rename row is a handful per scene's
    life, and the text test in `_read_rows` rejects every other line -- this
    campaign's calls included, which do not say "rename" or hold a backslash
    unless a hand edit or an odd scene id put one there -- without parsing
    it. The alternative, one pass that buffered the campaign's rows until the
    trail was complete, would hold every parsed row of the campaign's whole
    history at once for the all-time view, which is exactly what `_read_rows`
    reads a line at a time to avoid.
    """
    trail: dict[str, list[tuple[str, str]]] = {}
    for row in _ledger._read_rows(since, until, campaign, _rows.KIND_RENAME):
        was, now, ts = row.get("was"), row.get("scene"), row.get("ts")
        if not (isinstance(was, str) and isinstance(now, str) and isinstance(ts, str)):
            continue
        if was and now and was != now:
            trail.setdefault(was, []).append((ts, now))
    for hops in trail.values():
        hops.sort()
    return trail


def _scene_now(scene: object, ts: str, trail: dict[str, list[tuple[str, str]]]) -> str:
    """Which scene a row filed under `scene` at `ts` belongs to today.

    The one resolver both cost views ask, replacing the alias-and-cutoff pair
    this module used to keep for the per-scene read. That pair could express
    "``a`` stopped being this scene at T" but not "``a`` WAS this scene only
    between T1 and T2", which is exactly what a recycled id produces -- so the
    question is now asked per row, where it has a single right answer.

    The hop taken is the first at or after the stamp the walk currently holds:
    a rename only carries the rows written before it, and a row stamped after
    every hop off an id belongs to whatever holds that id now, which is the id
    itself. That stamp starts as the row's own and moves forward with each hop
    -- see the loop for why comparing every hop against the row's original
    stamp follows renames from another scene's tenancy of the same id.

    Bounded by `_MAX_RENAMES`: this file is hand-editable and ``a -> b -> a`` is
    one typo away from a loop inside a report.
    """
    if not isinstance(scene, str) or not scene:
        return NO_SCENE
    cursor, at, seen = scene, ts, 0
    while seen < _MAX_RENAMES:
        hop = next(((when, nxt) for when, nxt in trail.get(cursor, ()) if at <= when), None)
        if hop is None:
            return cursor
        # The clock ADVANCES to the hop just taken, and this is the subtle half.
        # An id can be renamed away from and later handed to a different scene,
        # so `b`'s own hops include ones that happened BEFORE this row's scene
        # ever became `b`. Comparing them against the row's original stamp
        # follows a rename belonging to somebody else's tenancy of that id --
        # `b -> c` at T1 then `a -> b` at T2 sent a row written before T1 all
        # the way to `c`, when it should stop at `b`.
        at, cursor = hop
        seen += 1
    return cursor


def scene_usage(campaign: str, scene: str, *, since: str = "",
                limit: int = SCENE_TURNS) -> dict:
    """What one scene's calls cost, per turn and in total (#153).

    The ledger is home-scoped and has no index, so "this scene" is a filter over
    a scanned window rather than a lookup. ``since`` is what keeps that scan
    honest *and* cheap: hand it the scene's own ``created`` date and the window
    is exactly the scene's lifetime — no arbitrary 30 days that would report
    $0.00 for a scene played last spring, and no year-long scan for one started
    this morning. See `_scan_since` for what an absent or impossible one does.

    ``turns`` is newest first and capped at ``limit``; ``totals`` and ``by_task``
    are summed over the whole window regardless, so the numbers do not change
    when the list is cut.

    ``clamped`` says the window starts later than ``since`` asked for, which
    `_scan_since` does for a scene older than `MAX_DAYS`. Everything in this
    payload is then a floor -- including ``by_post``, whose older posts have no
    bucket at all rather than an empty one, so a view that did not know would
    render them as posts that cost nothing. A campaign or scene id that never appears answers with
    zeroes — a scene that has generated nothing has spent nothing, which is an
    answer, and the route above is what distinguishes it from a typo.
    """
    until = _clock._today()
    start = _clock._scan_since(since, until)
    # Whether the scan starts LATER than the scene did. `_scan_since` floors a
    # long-lived scene's window at `MAX_DAYS`, so a scene played across more
    # than a year is scanned from part-way through its own life -- and the
    # per-post breakdown then simply has no bucket for its older posts, which
    # in a transcript is indistinguishable from a post that cost nothing. The
    # flag is what lets the view say "this breakdown does not reach that far"
    # instead of implying it is complete.
    clamped = bool(_clock._valid_day(since)) and start > _clock._valid_day(since)
    trail = _rename_trail(campaign, start, until)
    rates = _folding.Rates.current()
    totals = dict(_rows._ZERO)
    by_task: dict[str, dict] = {}
    by_post: dict[int, dict] = {}
    rerolls: dict[int, int] = {}
    turns: list[dict] = []
    for row in _ledger._read_rows(start, until, campaign):
        if not _ledger._is_call(row):
            continue
        # Asked per row rather than resolved to a set of ids up front: an id can
        # belong to this scene for a stretch and to another scene before and
        # after it, which no per-id answer can express. See `_scene_now`.
        if _scene_now(row.get("scene"), row["ts"], trail) != scene:
            continue
        _folding._add(totals, row, rates)
        _folding._add(by_task.setdefault(_rows._label(row.get("task")), dict(_rows._ZERO)), row, rates)
        post = _rows._post(row)
        if post is not None:
            bucket = by_post.setdefault(post, dict(_rows._ZERO))
            _folding._add(bucket, row, rates)
            # Counted here rather than derived from `calls` in the view: a post
            # answered once and then continued past a roll has two calls and no
            # rerolls, and `calls - 1` would report one.
            if row.get("task") in REROLL_TASKS:
                rerolls[post] = rerolls.get(post, 0) + 1
        turns.append(_turn(row, rates))
    # Sorted, not reversed. Rows are appended in completion order and two
    # concurrent calls interleave, so file order is *nearly* chronological and
    # reversing it would put a slow turn that finished late above a fast one
    # that started after it. A stable sort on the stamp says what the reader
    # means by "newest".
    turns.sort(key=lambda turn: turn["ts"], reverse=True)
    limit = max(0, int(limit))
    return {"campaign": campaign, "scene": scene, "since": start, "until": until,
            "clamped": clamped,
            "generated_at": _clock._now(), "totals": _rows._rounded(totals),
            "by_task": _rows._ranked(by_task),
            # Keyed by transcript index, ascending, so a view can walk it beside
            # the messages it is rendering. Every reroll of a post lands in that
            # post's bucket, which is the whole question this answers: what did
            # getting THIS reply, over however many attempts, actually cost.
            # `rerolls` is how many of those calls were re-answers rather than
            # parts of the first answer -- see `REROLL_TASKS`.
            "by_post": [{"post": i, "rerolls": rerolls.get(i, 0),
                         **_rows._rounded(by_post[i])} for i in sorted(by_post)],
            "turns": turns[:limit], "listed": min(len(turns), limit),
            "truncated": len(turns) > limit}


# ---- one campaign's scenes, over the whole ledger ----
#: How many scene buckets `campaign_scenes` returns. The cap is on the RESPONSE
#: like `SCENE_TURNS`: totals are summed over every row scanned, so a campaign
#: with a thousand scenes still reports its true all-time cost and only its list
#: is cut. The list is ordered by spend, so what is cut is the cheapest tail.
CAMPAIGN_SCENES = 500

#: The bucket key for calls that belong to the campaign but to no scene — a
#: cast suggestion, an intent classification, a voice anchor. They are in the
#: list rather than dropped from it, so the rows sum to the total printed above
#: them; a list that quietly excluded them would be a breakdown that does not
#: add up.
NO_SCENE = ""

#: How `campaign_scenes` may order its buckets, and what each orders BY. The
#: order has to be applied to the whole set before the cap is, or the cap
#: silently turns every other ordering into "these orderings, of the most
#: expensive N" -- a recent cheap scene missing from "most recent" while the
#: view claims to be showing it.
SCENE_ORDERS = ("cost", "recent", "turns")


def _sort_scenes(scenes: list[dict], order: str) -> None:
    """Order `scenes` in place by one of `SCENE_ORDERS`, ties on the scene id.

    TWO PASSES, and that is the trick rather than an inefficiency: every
    ordering here is descending, the tie-break is ascending, and `reverse=True`
    on one key tuple would flip the tie-break too. Python's sort is stable, so
    sorting by the id first and then by the real key leaves equal rows in id
    order -- one rule, three orderings, and no wrapper class whose only job is
    to compare backwards.
    """
    scenes.sort(key=lambda b: b["scene"])
    if order == "recent":
        scenes.sort(key=lambda b: b["last_ts"], reverse=True)
    elif order == "turns":
        scenes.sort(key=lambda b: b["calls"], reverse=True)
    else:
        scenes.sort(key=lambda b: (b["cost_usd"], b["modelled_usd"],
                                   b["estimated_usd"], b["calls"]), reverse=True)


def lifetime_since() -> str:
    """The first day the ledger could hold a row for, as ``YYYY-MM-DD``.

    Derived from the month files that EXIST, not from a fixed window: "what has
    this campaign cost over all time" is a question with a real answer, and
    `MAX_DAYS` would silently turn it into "over the last year". A library with
    no ledger at all answers today, which scans one absent file.

    This is the one read here whose cost grows with the library's age, and it is
    deliberate — it backs the all-time view and nothing on the play path. Every
    other rollup keeps its bounded window.
    """
    root = _ledger.ledger_dir()
    try:
        entries = [path.stem for path in root.iterdir() if path.suffix == ".jsonl"]
    except OSError:
        return _clock._today()
    # Parsed as a real date, not merely matched against `_MONTH`. The regex
    # accepts `2026-00`, which sorts before every real month and which
    # `date.fromisoformat` then raises on inside `_window_files` -- turning one
    # stray filename (a hand edit, or a row whose `ts` came in through the
    # facade) into a 500 on every request to this endpoint. A name that is not
    # a month names no window, so it is skipped like an unparseable row.
    days = [day for day in (_clock._valid_day(f"{stem}-01") for stem in entries) if day]
    return min(days) if days else _clock._today()


def campaign_scenes(campaign: str, *, since: str = "", month: str = "", order: str = "cost",
                    limit: int = CAMPAIGN_SCENES) -> dict:
    """What each of a campaign's scenes has cost, and what the campaign has.

    The scene-by-scene half of #153's cost view, over the ledger's whole history
    by default (`since=""` means `lifetime_since`). Renamed scenes are folded
    into the id they carry today (`_scene_now`), so a scene that was renamed the
    moment its first date was set reports what it cost from its first turn
    rather than from the rename — the same correction `scene_usage` makes for
    one scene, applied to all of them at once.

    `order` is one of `SCENE_ORDERS`, applied to the WHOLE set before `limit`
    cuts it -- which is why it is a parameter here rather than a re-sort on the
    client. A campaign with more buckets than the cap would otherwise have
    every alternative ordering silently mean "of the most expensive N", so a
    recent cheap scene would be missing from a list headed "most recent". An
    unknown order falls back to `cost`, the default this view opens on. Ties
    break on the id so two reads of the same data agree.
    """
    until = _clock._today()
    start = min(_clock._valid_day(since) or lifetime_since(), until)
    if month:
        start, until = _clock.month_bounds(month)
    forward = _rename_trail(campaign, start, until)
    rates = _folding.Rates.current()
    totals = dict(_rows._ZERO)
    buckets: dict[str, dict] = {}
    seen: dict[str, list[str]] = {}
    for row in _ledger._read_rows(start, until, campaign, strict=bool(month)):
        if not _ledger._is_call(row):
            continue
        _folding._add(totals, row, rates)
        scene = row.get("scene")
        sid = _scene_now(scene, row["ts"], forward) if isinstance(scene, str) and scene \
            else NO_SCENE
        _folding._add(buckets.setdefault(sid, dict(_rows._ZERO)), row, rates)
        stamps = seen.setdefault(sid, [row["ts"], row["ts"]])
        stamps[0] = min(stamps[0], row["ts"])
        stamps[1] = max(stamps[1], row["ts"])
    scenes = [{"scene": sid, "first_ts": seen[sid][0], "last_ts": seen[sid][1],
               **_rows._rounded(bucket)} for sid, bucket in buckets.items()]
    order = order if order in SCENE_ORDERS else SCENE_ORDERS[0]
    _sort_scenes(scenes, order)
    limit = max(0, int(limit))
    result = {"campaign": campaign, "since": start, "until": until,
            "generated_at": _clock._now(), "totals": _rows._rounded(totals), "order": order,
            "scenes": scenes[:limit], "listed": min(len(scenes), limit),
            "truncated": len(scenes) > limit}
    if month:
        result["available_months"] = available_months(month)
    return result


def _projected(bucket: dict) -> dict:
    # Only the trend calls this a total. Accounting and budgets continue to
    # keep the three provenance columns apart.
    return {**_rows._rounded(bucket), "estimated_total_usd": _rows._money(
        bucket["cost_usd"] + bucket["estimated_usd"] + bucket["modelled_usd"])}


def available_months(selected: str = "") -> list[str]:
    existing = {p.stem for p in _ledger.ledger_dir().glob("*.jsonl")
                if len(p.stem) == 7 and p.stem[4] == "-"}
    return sorted({key for key in existing if _clock._valid_day(key + "-01")}
                  | {_clock._today()[:7]} | ({selected} if selected else set()))


def monthly_campaigns(month: str = "", *, campaign: str = "") -> dict:
    selected = month or _clock._today()[:7]
    since, until = _clock.month_bounds(selected)
    rates = _folding.Rates.current()
    totals = dict(_rows._ZERO)
    by_campaign: dict[str, dict] = {}
    for row in _ledger._read_rows(since, until, campaign, strict=True):
        if not _ledger._is_call(row):
            continue
        _folding._add(totals, row, rates)
        cid = row.get("campaign")
        key = cid if isinstance(cid, str) and cid else ""
        _folding._add(by_campaign.setdefault(key, dict(_rows._ZERO)), row, rates)
    campaigns_out = [
        {"campaign_id": cid, **_projected(bucket)}
        for cid, bucket in sorted(by_campaign.items()) if cid
    ]
    # The trend ends at the selected month, including an empty selected month.
    trend = []
    cursor = date.fromisoformat(since)
    for _ in range(12):
        key = cursor.strftime("%Y-%m")
        lo, hi = _clock.month_bounds(key)
        bucket = totals if key == selected else dict(_rows._ZERO)
        if key != selected:
            for row in _ledger._read_rows(lo, hi, campaign, strict=True):
                if _ledger._is_call(row):
                    _folding._add(bucket, row, rates)
        trend.append({"month": key, **_projected(bucket)})
        if cursor.year == 1 and cursor.month == 1:
            break
        cursor = cursor - timedelta(days=1)
        cursor = cursor.replace(day=1)
    return {"month": selected, "since": since, "until": until,
            "available_months": available_months(selected), "totals": _projected(totals),
            "campaigns": campaigns_out,
            "unassigned": _projected(by_campaign.get("", dict(_rows._ZERO))),
            "trend": list(reversed(trend))}
