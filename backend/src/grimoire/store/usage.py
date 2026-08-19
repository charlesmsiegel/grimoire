"""What each LLM call spent, and what a window of them adds up to (#152).

Nothing used to be persisted about a generation once its text landed: how many
tokens it took, what it cost, how long it ran, whether it failed. The local
tokenizer in ``store.context`` counts what grimoire *composed*, which is an
estimate of the prompt and says nothing at all about the reply or the bill. This
module records what the provider itself reported, per call.

Layout, home-scoped rather than campaign-scoped::

    <home>/usage/YYYY-MM.jsonl      one JSON object per line, append-only

Home-scoped because not every call belongs to a campaign — a tagline is
generated against a world, and a connection test against nothing at all — and a
per-campaign ledger would either drop those or invent a campaign for them.
Month-per-file because the only query this serves is "the last N days", so a
30-day window reads at most two files however long the library has been played.

**Rollups are computed on read.** There is no index, no database and no
maintained aggregate: ``summary`` scans the one or two month files in the window
and buckets in memory. A heavy day is a few hundred rows; a heavy year is tens
of thousands, which is milliseconds to parse and keeps the store what the rest
of grimoire promises — files a human can open and read.

Pricing has two sources, and the difference between them is the point:

- ``cost_usd`` with ``cost_basis: "billed"`` is money an account was charged,
  reported by the provider (OpenRouter returns ``usage.cost`` in credits, which
  are USD).
- ``cost_usd`` with ``cost_basis: "equivalent"`` is what the call *would* have
  cost at API rates but did not, because it billed against a subscription
  instead — the Claude Agent path, whose auth is the host's Claude Code login
  (see ``claude_agent``). Summing that into a spend figure would tell someone
  they had spent money they had not.

  So the two never share a total: buckets carry ``cost_usd`` (billed only) and
  ``estimated_usd`` (equivalent only).
- Neither, when a provider reports no price at all — every ``openai_compatible``
  endpoint today, and any OpenRouter reply whose usage block came without a
  cost. The field is then **absent**, not zero, so a re-pricing pass over the
  ledger (#158's per-model override, or the catalog rate) can tell "free" from
  "unknown"; ``unpriced_calls`` is what makes that visible in a rollup rather
  than quietly understating the total.

The token counts follow the same absent-not-zero rule, so a row never claims a
call used no tokens when nobody counted them. A bucket's token total is
therefore a floor whenever ``unpriced_calls`` is non-zero — with these adapters
a usage block is all-or-nothing, so the calls that report no price are the same
ones that report no counts.

**The cache pair is a breakdown, not a component** (#148).
``cache_read_tokens`` and ``cache_write_tokens`` are slices of
``prompt_tokens`` — the part of the prompt that was already cached, and the
part this call paid to put there — so they are summed into columns of their own
and deliberately left out of ``total_tokens``. Folding them in would count a
cached prefix twice. They are what turns "this month cost X" into "this month
cost X, and caching is doing Y about it": a read is billed at a fraction of a
fresh token, a write at a small premium, and without the split a rollup cannot
tell a library whose prompts cache well from one whose prompts thrash. Absent
whenever a provider says nothing about caching, which includes every
``openai_compatible`` endpoint that reports no usage at all.

Retention is deliberately none. A row is ~250 bytes, so a year of heavy play is
single-digit megabytes, and the month files are trivially deletable by hand —
which is a better answer than a pruner that silently destroys the history
somebody enabled this feature to have.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

from . import atomic, paths

#: The row kinds this ledger holds. ``llm`` is the only one written today;
#: the field exists from the first row so image generation (#159) can share the
#: file rather than needing a second one, and so a reader can filter for the
#: kind it understands instead of assuming every row is a chat completion.
KIND_LLM = "llm"

#: A row saying one scene id became another (#153). Not a call, and every
#: rollup skips it -- see `_is_call`. A scene's id is its filename stem, so the
#: first date set on a scene renames it and every store holding that id has to
#: follow (`store.scene_refs`). This ledger cannot follow the way the others do:
#: they rewrite a JSON file under the campaign lock, and rewriting a month file
#: would race the unlocked `O_APPEND` writes that are the whole reason `record`
#: never blocks a turn. So the rename is APPENDED like everything else, and the
#: read side walks the trail (`_aliases`) instead. That also keeps the file what
#: it claims to be: a log of what happened, in the order it happened, that
#: nothing rewrites after the fact.
KIND_RENAME = "rename"

#: Longest window ``summary`` will scan, in days. A rollup is one HTTP request
#: doing unbounded file reads, so the bound is on the query rather than on
#: trust: a year is past any question this view answers, and refusing more
#: keeps ``?days=100000`` from walking every month a library has ever had.
MAX_DAYS = 366

#: When this process started, as an ISO timestamp. "Session" has no server-side
#: concept in grimoire — there is no login, and a browser tab is not a session
#: the backend can see — so the endpoint documents it as *this backend process*,
#: which is the one boundary the server genuinely knows. Restarting the app
#: therefore resets the session bucket, while ``today`` and the 30-day window
#: carry straight over.
_SESSION_START = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

#: The zeroed bucket, and the field list every rollup shares. ``total_tokens``
#: is stored rather than derived so a bucket is self-describing to the frontend
#: reading it, and ``calls``/``errors`` are separate because a failed call still
#: costs time and may still have been billed for tokens it generated.
#:
#: `cache_read_tokens`/`cache_write_tokens` are inside `prompt_tokens` and so
#: are deliberately absent from `total_tokens` -- see `_add`.
_ZERO = {"calls": 0, "errors": 0, "prompt_tokens": 0, "completion_tokens": 0,
         "total_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
         "cost_usd": 0.0, "estimated_usd": 0.0,
         "priced_calls": 0, "unpriced_calls": 0, "duration_ms": 0}

#: Cents-of-a-cent. Provider costs run to eight decimal places on a cheap model,
#: and summing raw floats surfaces as `0.30000000000000004` in a UI that renders
#: the number straight. Rounded on the way OUT only -- rows keep what the
#: provider said.
_CENTS = 6


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _today() -> str:
    """Today, UTC, as ``YYYY-MM-DD``. A function so tests can pin the window
    without freezing the clock for everything else in the process."""
    return time.strftime("%Y-%m-%d", time.gmtime())


def ledger_dir() -> Path:
    return paths.home() / "usage"


#: A well-formed ``YYYY-MM`` prefix, which is the whole of a ledger filename.
#: The check is not paranoia about this module's own ``_now()``: ``ts`` is a
#: parameter of a facade-exported function, so it is a value that names a file,
#: and every id-to-path resolver in this store is required to prove it cannot
#: name anything but a direct child (``paths.safe_id``). Two digits and a dash
#: in a fixed shape is the cheapest possible version of that proof.
_MONTH = re.compile(r"\d{4}-\d{2}$")


def month_path(ts: str) -> Path:
    """The ledger file a row stamped ``ts`` belongs in.

    Slices the timestamp rather than parsing it: the stamp is always this
    module's own ``YYYY-MM-DDTHH:MM:SSZ``, and a full parse would only add a
    way to fail. A prefix that is not a month falls back to the current one
    rather than opening a path of its own -- a misfiled row beats a filename
    built out of whatever was handed in.
    """
    month = ts[:7]
    return ledger_dir() / f"{month if _MONTH.match(month) else _now()[:7]}.jsonl"


def record(*, task: str, kind: str = KIND_LLM, campaign: str = "", scene: str = "",
           model: str = "", connection: str = "", provider: str = "",
           prompt_tokens: int | None = None, completion_tokens: int | None = None,
           cache_read_tokens: int | None = None, cache_write_tokens: int | None = None,
           cost_usd: float | None = None, cost_basis: str = "",
           duration_ms: int = 0, status: str = "ok", error: str = "",
           attempts: int = 1, ts: str | None = None) -> dict | None:
    """Append one call to the ledger. Returns the row, or None if nothing was
    written.

    **Never raises.** This runs on the generating path, inside the SSE
    finalizers, and a bookkeeping row that can fail a turn is a worse bug than
    the one it exists to diagnose — the judgement ``prompt_log.record`` already
    makes, for the same reason. A full disk, a read-only store, a home that
    cannot be created, or a field holding something that will not serialize:
    each costs the row and nothing else.

    Takes no lock, unlike every other writer here. There is nothing to
    serialize: the write is a single ``O_APPEND`` line (see
    ``atomic.append_line``), never a read-modify-write, so concurrent callers
    interleave rows instead of losing them — and taking the campaign lock would
    both stall a turn and exclude the calls that have no campaign to lock.
    """
    ts = ts or _now()
    # The WHOLE body is inside the guard, the row's construction included. The
    # coercions can raise exactly as the write can -- `int()` on a value that is
    # not a number is a ValueError -- and this function's contract is that
    # nothing it does can fail the turn it is accounting for. Building the row
    # above the `try` left precisely that hole.
    try:
        row = {"ts": ts, "kind": kind, "task": task}
        # Optional identity fields are omitted rather than written empty: a
        # tagline has no campaign and no scene, and `"campaign": ""` in the file
        # reads as a campaign whose id is the empty string.
        for key, value in (("campaign", campaign), ("scene", scene), ("model", model),
                           ("connection", connection), ("provider", provider)):
            if value:
                row[key] = value
        # Absent, not zero, when the provider counted nothing -- the same rule
        # the price gets below, and for the same reason. A row saying zero
        # tokens is a row saying the call used none, which is a claim no
        # `openai_compatible` endpoint has made; `0` stays available for the
        # empty reply that genuinely completed none. A rollup adds an absent
        # count as zero either way, so what this buys is a file that can still
        # be re-read honestly later.
        # The cache pair sits under the same rule, and carries one more of its
        # own: both are slices OF `prompt_tokens` rather than counts beside it
        # (#148), so a reader adding them to a total would count a cached prefix
        # twice. `_add` is where that promise is kept for rollups.
        for key, count in (("prompt_tokens", prompt_tokens),
                           ("completion_tokens", completion_tokens),
                           ("cache_read_tokens", cache_read_tokens),
                           ("cache_write_tokens", cache_write_tokens)):
            if count is not None:
                row[key] = int(count)
        if cost_usd is not None:
            # The literal rather than `llm_usage.BILLED`: the store does not
            # import the gateway (#239), and one default spelled in two modules
            # is a cheaper price than that rule.
            row["cost_usd"] = float(cost_usd)
            row["cost_basis"] = cost_basis or "billed"
        row["duration_ms"] = int(duration_ms or 0)
        row["status"] = status
        if error:
            row["error"] = error
        if attempts and attempts != 1:
            row["attempts"] = int(attempts)
        return _append(row, ts)
    except (OSError, TypeError, ValueError):
        # All three, and each is a real escape from a function whose whole
        # contract is that it cannot fail a turn. OSError is the write. A field
        # holding something unserializable -- an object where a model name
        # belongs -- raises TypeError from `json.dumps`, not ValueError, which
        # is the sort of thing only a test finds. ValueError covers `allow_nan`
        # and every `int()`/`float()` above it.
        return None


def _append(row: dict, ts: str) -> dict:
    """Write one row to its month file. Raises -- every caller is inside the
    guard `record` documents, and sharing this is what keeps a second writer
    from quietly acquiring different failure semantics."""
    path = month_path(ts)
    path.parent.mkdir(parents=True, exist_ok=True)
    # `allow_nan=False` is the load-bearing argument. The default writes an
    # infinite float as the bare token `Infinity` and reads it back happily,
    # so a Python-only round trip never notices -- while every other JSON
    # reader rejects the line, and a ledger nothing else can parse is not a
    # ledger. Refusing at the encoder costs one row instead of a month.
    atomic.append_line(path, json.dumps(row, allow_nan=False))
    return row


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Record that scene ids were renamed, so the per-scene view can follow.

    `store.scene_refs.repoint`'s sixteenth store, and the only one that does not
    rewrite what it holds -- see `KIND_RENAME` for why an append-only ledger
    must not, and `_aliases` for how the read side follows the trail instead.

    Never raises, like `record`: this runs inside a rename that has already
    moved the scene file, and failing it would leave the campaign half-renamed
    over a bookkeeping row. A lost trail costs the old turns' cost history, not
    the scene.

    Takes no lock, for `record`'s reason: each line is a single `O_APPEND`
    write, never a read-modify-write.
    """
    ts = _now()
    for old, new in mapping.items():
        if not (isinstance(old, str) and isinstance(new, str)) or old == new:
            continue
        try:
            _append({"ts": ts, "kind": KIND_RENAME, "campaign": cid,
                     "scene": new, "was": old}, ts)
        except (OSError, TypeError, ValueError):
            continue


class Meter:
    """One call's usage, from before the request to the ledger row after it.

    The facade fills ``usage`` in place — that is the whole reason it is a plain
    dict handed down rather than a return value: ``LLMClient.stream`` is an
    async generator, so there is nothing to return, and the numbers arrive in
    the provider's *final* frame, after the caller has already consumed every
    delta it cares about. See ``llm.LLMClient.stream``.

    Used as a context manager, an exception on the way out is recorded as a
    failed call carrying its kind, and then re-raised untouched::

        with store.usage.meter("chat", campaign=cid, scene=sid) as m:
            text = await client.complete(messages, conn, usage=m.usage)

    Recording happens exactly once however the meter ends, so a caller that
    finishes explicitly and is then unwound by an exception files one row rather
    than two.
    """

    def __init__(self, task: str, *, kind: str = KIND_LLM, campaign: str = "",
                 scene: str = "", model: str = ""):
        self.task = task
        self.kind = kind
        self.campaign = campaign
        self.scene = scene
        self.model = model
        self.usage: dict = {}
        self.row: dict | None = None
        self._done = False
        self._t0 = time.monotonic()

    def __enter__(self) -> "Meter":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        """Record what happened, then let the exception through untouched
        (returning False re-raises).

        A cancellation is not a failure, and telling them apart matters: an
        `asyncio.CancelledError` unwinds this block at every `.complete()` site
        the moment a client disconnects, and counting that as a provider error
        would make a user who closes a tab mid-suggestion look like a provider
        failing them. `_fence_stream` already takes that care on the streamed
        path; this is the same rule for the one-shot ones.

        The test is "is it an `Exception`?" rather than a list of cancellation
        types, because the things that are *not* -- `CancelledError`,
        `GeneratorExit`, `KeyboardInterrupt` -- are precisely the ones that mean
        the caller or the process went away rather than the call going wrong.
        """
        if exc is None:
            self.done()
        elif isinstance(exc, Exception):
            # `kind` is the LLMError taxonomy the frontend already branches on;
            # anything else is recorded by its type name, which is the only
            # label a non-LLM failure has.
            self.done("error", getattr(exc, "kind", None) or type(exc).__name__)
        else:
            self.done("aborted")
        return False

    def done(self, status: str = "ok", error: str = "") -> dict | None:
        """File the row. A second call is a no-op — see the class docstring.

        **A request that never went out is not a row.** `llm._stamp` fills the
        holder with the route about to be tried *before* the first provider
        attempt, so a holder that is still empty means nothing was ever sent —
        which really happens: absorb's budget refuses a step it has no time left
        for (`BudgetRefused`) and closes the coroutine unawaited, and a caller
        can raise between opening the meter and the call. Recording those would
        put calls that cost nothing, took no time and never happened into every
        rollup, and would make an absorb that ran out of budget look like one
        that made a dozen free requests.
        """
        if self._done:
            return self.row
        self._done = True
        if not self.usage:
            return None
        cost = self.usage.get("cost_usd")
        self.row = record(
            task=self.task, kind=self.kind, campaign=self.campaign, scene=self.scene,
            model=self.usage.get("model") or self.model,
            connection=self.usage.get("connection", ""),
            provider=self.usage.get("provider", ""),
            prompt_tokens=self.usage.get("prompt_tokens"),
            completion_tokens=self.usage.get("completion_tokens"),
            cache_read_tokens=self.usage.get("cache_read_tokens"),
            cache_write_tokens=self.usage.get("cache_write_tokens"),
            cost_usd=cost, cost_basis=self.usage.get("cost_basis", ""),
            duration_ms=int((time.monotonic() - self._t0) * 1000),
            status=status, error=error, attempts=self.usage.get("attempts", 1))
        return self.row


def meter(task: str, *, kind: str = KIND_LLM, campaign: str = "", scene: str = "",
          model: str = "") -> Meter:
    """A `Meter` for one call. `model` is only a fallback: the facade stamps the
    model the request actually ran on, which differs after a fallback route."""
    return Meter(task, kind=kind, campaign=campaign, scene=scene, model=model)


# ---- rollups ----
def _read_rows(since: str, until: str):
    """Every well-formed row stamped in ``[since, until]``, oldest file first.

    Tolerant by design, in both directions. A file that cannot be read is
    skipped -- a rollup is a report, and refusing to draw it because one month
    is locked by a sync client is worse than drawing it short. A line that will
    not parse is skipped too: `atomic.append_line` documents the one way a torn
    line can be produced, and a hand-edited or half-synced file produces the
    rest.
    """
    for path in _window_files(since, until):
        try:
            # A line at a time, not `read_text().splitlines()`. A year of heavy
            # play is a few tens of megabytes, and slurping a month file holds
            # the whole of it plus a list of every line in memory at once -- for
            # a report that never needs two rows at the same time.
            with open(path, encoding="utf-8") as f:
                for line in f:
                    row = _row(line, since, until)
                    if row is not None:
                        yield row
        except (OSError, ValueError):    # ValueError covers invalid UTF-8
            # Mid-file as well as on open: a decode error surfaces on the read
            # that hits the bad bytes, and a report drawn short beats no report.
            continue


def _row(line: str, since: str, until: str) -> dict | None:
    """One ledger line as a row inside the window, or None to skip it."""
    if not line.strip():
        return None
    try:
        row = json.loads(line)
    except ValueError:
        return None
    if not isinstance(row, dict):
        return None
    ts = row.get("ts")
    if not isinstance(ts, str) or not since <= ts[:10] <= until:
        return None
    return row


def _window_files(since: str, until: str) -> list[Path]:
    """The month files a ``[since, until]`` window can touch, in order.

    Derived from the window rather than by globbing the directory, so the read
    cost is the window's size and not the library's age: a 30-day summary opens
    one file or two whether the store holds three months or three hundred.
    """
    root = ledger_dir()
    months, cursor = [], date.fromisoformat(since).replace(day=1)
    end = date.fromisoformat(until).replace(day=1)
    while cursor <= end:
        months.append(root / f"{cursor.strftime('%Y-%m')}.jsonl")
        # First of the next month, without a calendar table: day 28 is in every
        # month, and +4 days from it is always in the next one.
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def _is_call(row: dict) -> bool:
    """True for a row that represents a generation, i.e. everything a rollup is
    allowed to count. Today the only other kind is `KIND_RENAME`, which carries
    a campaign and a scene and would otherwise land in every bucket keyed on
    either -- a free call, in a task named "unknown"."""
    return row.get("kind") != KIND_RENAME


def _add(bucket: dict, row: dict) -> None:
    """Fold one row into a bucket. Reads defensively — a row is a line from a
    file a human can edit, so a string where a number belongs must cost that
    field rather than the whole report."""
    bucket["calls"] += 1
    # Only `error`, not "anything that is not ok". A cancelled turn is recorded
    # as `aborted` -- the player pressed Cancel or navigated away -- and folding
    # that into an error rate would make a user who reads a lot of half-answers
    # and stops look like a provider that is failing them. Both are still
    # `calls`, because both were generated and both may have been billed.
    if row.get("status") == "error":
        bucket["errors"] += 1
    prompt = _int(row.get("prompt_tokens"))
    completion = _int(row.get("completion_tokens"))
    bucket["prompt_tokens"] += prompt
    bucket["completion_tokens"] += completion
    bucket["total_tokens"] += prompt + completion
    # NOT folded into `total_tokens`, and this is the line to read twice: a
    # cache read is part of the prompt the provider already counted, so adding
    # it would bill a cached prefix to the total twice over (#148). They get
    # their own columns because they answer a question the totals cannot --
    # how much of what was sent was already there, which is the whole measure
    # of whether prompt caching is working.
    bucket["cache_read_tokens"] += _int(row.get("cache_read_tokens"))
    bucket["cache_write_tokens"] += _int(row.get("cache_write_tokens"))
    bucket["duration_ms"] += _int(row.get("duration_ms"))
    cost = _float(row.get("cost_usd"))
    if cost is None:
        bucket["unpriced_calls"] += 1
        return
    bucket["priced_calls"] += 1
    if row.get("cost_basis") == "equivalent":
        bucket["estimated_usd"] += cost
    else:
        bucket["cost_usd"] += cost


def _int(value: object) -> int:
    """A non-negative count, or 0. Negatives are floored rather than summed: a
    row is a line in a file a human can edit, and one hand-typed `-999999` that
    subtracts from a month's total is a rollup nobody can reconcile against a
    provider's invoice. `bool` is excluded because it is an `int` subclass and
    `True` would otherwise count as one token."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _float(value: object) -> float | None:
    """A finite number, or None for anything else — including the absent field
    that means "this call was never priced", which is the distinction the whole
    `unpriced_calls` count rests on."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value == value and abs(value) != float("inf") else None


def _rounded(bucket: dict) -> dict:
    return {**bucket, "cost_usd": round(bucket["cost_usd"], _CENTS),
            "estimated_usd": round(bucket["estimated_usd"], _CENTS)}


def _ranked(buckets: dict[str, dict]) -> list[dict]:
    """Buckets as a list, busiest first — by calls, then by name so the order is
    stable across two summaries of the same data."""
    return [{"key": key, **_rounded(bucket)}
            for key, bucket in sorted(buckets.items(),
                                      key=lambda kv: (-kv[1]["calls"], kv[0]))]


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
    days = max(1, min(int(days), MAX_DAYS))
    until = _today()
    since = (date.fromisoformat(until) - timedelta(days=days - 1)).isoformat()

    totals, session, today = dict(_ZERO), dict(_ZERO), dict(_ZERO)
    by_day: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    by_task: dict[str, dict] = {}
    by_campaign: dict[str, dict] = {}

    for row in _read_rows(since, until):
        if not _is_call(row) or (campaign and row.get("campaign") != campaign):
            continue
        _add(totals, row)
        ts = row["ts"]
        if ts >= _SESSION_START:
            _add(session, row)
        if ts[:10] == until:
            _add(today, row)
        _add(by_day.setdefault(ts[:10], dict(_ZERO)), row)
        _add(by_model.setdefault(_label(row.get("model")), dict(_ZERO)), row)
        _add(by_task.setdefault(_label(row.get("task")), dict(_ZERO)), row)
        if isinstance(row.get("campaign"), str) and row["campaign"]:
            _add(by_campaign.setdefault(row["campaign"], dict(_ZERO)), row)

    return {
        "days": days, "since": since, "until": until, "campaign": campaign,
        "generated_at": _now(), "session_started": _SESSION_START,
        "totals": _rounded(totals), "session": _rounded(session),
        "today": _rounded(today),
        # Chronological, unlike the other three: a day breakdown is a time
        # series and a chart reads it left to right.
        "by_day": [{"key": day, **_rounded(by_day[day])} for day in sorted(by_day)],
        "by_model": _ranked(by_model), "by_task": _ranked(by_task),
        "by_campaign": _ranked(by_campaign),
    }


def _label(value: object) -> str:
    """A bucket key that is safe to render. A hand-edited row can hold anything,
    and a dict reaching the frontend as a key would take the panel down the way
    `prompt_log`'s validation exists to prevent."""
    return value if isinstance(value, str) and value else "unknown"


# ---- one scene's turns ----
#: How many rows `scene_usage` lists, newest first. The cap is on the RESPONSE,
#: not on the scan: the totals beside the list are summed over every row in the
#: window, so a scene played for a week still reports its true cost and only its
#: list is short. `truncated` says when that happened, because a list that
#: silently stops is a list somebody reads as complete.
SCENE_TURNS = 200


def _valid_day(text: object) -> str:
    """``YYYY-MM-DD`` if that is what this is, else ``""``.

    Parsed rather than pattern-matched, for the reason `campaigns.read` gives
    about stamps: a regex accepts ``2026-13-45``, and `date.fromisoformat` on
    that is a ValueError one call further on, inside a report that must not
    fail because a scene's frontmatter was hand-edited.
    """
    if not isinstance(text, str):
        return ""
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return ""


#: How far back a scan reaches when the caller supplies no usable start date --
#: the same default window every other read here takes.
_DEFAULT_DAYS = 30


def _scan_since(since: object, until: str) -> str:
    """Where a scan asked to start at ``since`` actually starts.

    ``since`` is a caller's date — the scene's own ``created`` stamp, for the
    panel asking what one scene cost — so it decides the window, and the window
    is the one cost this module bounds. Three clamps, each for a real value a
    campaign.md or a scene's frontmatter can hold:

    - absent or unparseable falls back to `_DEFAULT_DAYS`, which is what every
      other read here defaults to;
    - further back than `MAX_DAYS` is clamped to it, so a scene created two
      years ago costs the same read as one created last month;
    - *after* today — a scene stamped in the future by a wrong clock or a hand
      edit — scans today alone rather than producing an empty window that
      `_window_files` would walk backwards.
    """
    day = _valid_day(since)
    floor = (date.fromisoformat(until) - timedelta(days=MAX_DAYS - 1)).isoformat()
    if not day:
        return (date.fromisoformat(until) - timedelta(days=_DEFAULT_DAYS - 1)).isoformat()
    return min(max(day, floor), until)


def _turn(row: dict) -> dict:
    """One ledger row as the per-turn view sees it.

    Projected, never passed through. A row is a line in a file a human can edit,
    and handing the frontend whatever it holds is how a dict where a model name
    belongs takes a panel down — the judgement `_label` already makes for bucket
    keys. Counts come back through `_int` so the numbers a view sums are
    numbers, and `cost_usd` stays **None rather than 0.0** when the provider
    priced nothing, so a turn nobody costed reads as unpriced instead of free.
    """
    prompt = _int(row.get("prompt_tokens"))
    completion = _int(row.get("completion_tokens"))
    return {
        "ts": _label(row.get("ts")), "task": _label(row.get("task")),
        "model": _label(row.get("model")), "status": _label(row.get("status")),
        "error": row.get("error") if isinstance(row.get("error"), str) else "",
        # `_int` floors a hand-edited negative to 0, and a call that happened
        # was attempted at least once -- the field is only written when it is
        # more than one (`record`), so absent means exactly one too.
        "attempts": _int(row.get("attempts")) or 1,
        "prompt_tokens": prompt, "completion_tokens": completion,
        # Same rule as `_add`: the cache pair is a slice OF the prompt (#148),
        # so it sits beside the total and never inside it.
        "total_tokens": prompt + completion,
        "cache_read_tokens": _int(row.get("cache_read_tokens")),
        "cache_write_tokens": _int(row.get("cache_write_tokens")),
        "cost_usd": _float(row.get("cost_usd")),
        "cost_basis": _label(row.get("cost_basis")) if row.get("cost_basis") else "",
        "duration_ms": _int(row.get("duration_ms")),
    }


#: How far `_aliases` will follow a rename trail before giving up. A scene is
#: renamed once or twice in its life (the first date set, an edited title), so
#: any real chain is short -- and this is a file a human can edit, where
#: ``a -> b -> a`` is one typo away from looping forever inside a report.
_MAX_RENAMES = 64


def _aliases(campaign: str, scene: str, since: str, until: str) -> dict[str, str]:
    """Every id this scene's rows can be filed under, mapped to the stamp after
    which that id stopped being this scene's. `scene` itself maps to ``""``,
    meaning no cutoff.

    A scene's id is its filename stem, so setting a date on it renames it
    (`scenes.moment`) -- and that happens to most scenes, usually a turn or two
    in. The rows already written carry the id the scene had then. Without this,
    the panel that asks what a scene cost would answer with the spend since its
    last rename and call it the scene's, which is the quiet kind of wrong this
    whole module is trying not to be.

    **The cutoff is not decoration.** `paths.uniquify` checks only what exists
    *now*, so the moment a scene is renamed off ``001--x`` that id is free for
    the next scene to take -- and its rows would then be charged to the scene
    that used to hold it. An alias is only an alias for the rows written before
    the rename that gave it up.

    A second pass over the window rather than a bigger first one: renames are
    rare, so this collects only `KIND_RENAME` rows and stays constant-memory,
    where folding it into the main scan would mean holding every row of the
    campaign to find out which ones belonged.
    """
    forward: dict[str, tuple[str, str]] = {}
    for row in _read_rows(since, until):
        if row.get("kind") != KIND_RENAME or row.get("campaign") != campaign:
            continue
        old, new, ts = row.get("was"), row.get("scene"), row.get("ts")
        if not (isinstance(old, str) and isinstance(new, str) and isinstance(ts, str)):
            continue
        if old and new and old != new:
            # Last row wins: a `was` seen twice means the scene was renamed back
            # to it and away again, and the later hop is the one still true.
            forward[old] = (new, ts)
    ids = {scene: ""}
    for old, (_, ts) in forward.items():
        # The scene's CURRENT id never takes a cutoff, whatever the trail says.
        # A title renamed and renamed back (a -> b -> a, one typo and its fix)
        # walks a chain that returns to where it started, and letting that write
        # a cutoff onto the live id would drop every row this scene has filed
        # since -- the trail silencing the very scene it exists to follow.
        if old == scene:
            continue
        cursor, seen = old, 0
        while cursor in forward and seen < _MAX_RENAMES:
            cursor = forward[cursor][0]
            seen += 1
            if cursor == scene:
                # The cutoff is THIS id's own rename, not the end of the chain:
                # `a` stopped being this scene the moment a->b happened, whatever
                # b did later.
                ids[old] = ts
                break
    return ids


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
    when the list is cut. A campaign or scene id that never appears answers with
    zeroes — a scene that has generated nothing has spent nothing, which is an
    answer, and the route above is what distinguishes it from a typo.
    """
    until = _today()
    start = _scan_since(since, until)
    ids = _aliases(campaign, scene, start, until)
    totals = dict(_ZERO)
    by_task: dict[str, dict] = {}
    turns: list[dict] = []
    for row in _read_rows(start, until):
        if not _is_call(row) or row.get("campaign") != campaign:
            continue
        cutoff = ids.get(row.get("scene"))
        # `None` is "not this scene's id at all"; a cutoff is "not any more,
        # after this stamp" -- see `_aliases` for why an id can change hands.
        if cutoff is None or (cutoff and row["ts"] > cutoff):
            continue
        _add(totals, row)
        _add(by_task.setdefault(_label(row.get("task")), dict(_ZERO)), row)
        turns.append(_turn(row))
    # Sorted, not reversed. Rows are appended in completion order and two
    # concurrent calls interleave, so file order is *nearly* chronological and
    # reversing it would put a slow turn that finished late above a fast one
    # that started after it. A stable sort on the stamp says what the reader
    # means by "newest".
    turns.sort(key=lambda turn: turn["ts"], reverse=True)
    limit = max(0, int(limit))
    return {"campaign": campaign, "scene": scene, "since": start, "until": until,
            "generated_at": _now(), "totals": _rounded(totals),
            "by_task": _ranked(by_task),
            "turns": turns[:limit], "listed": min(len(turns), limit),
            "truncated": len(turns) > limit}


# ---- budgets ----
#: The share of a budget at which the warning starts. A budget that only speaks
#: once it has already been broken is a receipt, not a warning -- the point is to
#: be told while the session can still be ended.
WARN_FRACTION = 0.8

#: The two periods a campaign budget can be declared over. `MONTHLY` is the
#: current UTC calendar month, which is how providers themselves bill; `TOTAL`
#: is what the campaign has cost over the ledger's whole scannable history.
MONTHLY, TOTAL = "monthly", "total"
PERIODS = (MONTHLY, TOTAL)

#: What `budget` reports. `OFF` is a campaign with no budget set, and is
#: deliberately not `OK`: "you are within your budget" and "you have not asked
#: for one" are different answers, and a banner that fires on the second is a
#: banner people turn off.
OFF, OK, WARN, OVER = "off", "ok", "warn", "over"


def normalize_period(period: object) -> str:
    """A period this module understands. Anything else is `MONTHLY`, which is
    the safer default of the two: a month's cap read as an all-time one would
    fire the warning for spend the user had already accepted.

    Case- and space-insensitive, because the value reaches here from a
    hand-edited `campaign.md` as readily as from the form -- and reading
    ``Total`` as ``monthly`` would be a silent, invisible downgrade of what
    somebody plainly wrote."""
    if not isinstance(period, str):
        return MONTHLY
    period = period.strip().lower()
    return period if period in PERIODS else MONTHLY


def normalize_limit(limit: object) -> float:
    """A budget as a positive number of dollars, or 0.0 for "no budget".

    Takes a string as readily as a number: campaign frontmatter is
    string-scalar (`store.frontmatter`), so the stored value arrives here as
    ``"12.5"`` and a hand edit can make it ``"twelve"``. Zero, negative,
    infinite and unparseable all mean the same thing — there is no budget —
    because the alternative is a campaign pinned permanently to `OVER` by a
    typo, warning about every call it ever makes.
    """
    if isinstance(limit, str):
        try:
            limit = float(limit.strip() or 0)
        except ValueError:
            return 0.0
    value = _float(limit)
    return value if value is not None and value > 0 else 0.0


def period_window(period: str, until: str = "") -> tuple[str, str]:
    """The ``[since, until]`` a period covers, both ``YYYY-MM-DD``.

    `TOTAL` is `MAX_DAYS` back rather than genuinely all time, and that bound is
    the same one `summary` takes: this store answers windows, and a query whose
    cost grows with the library's age is the thing `_window_files` exists to
    avoid. A campaign older than a year reports the last year of its spend, and
    the window comes back in the payload so the view can say so.

    **The month is UTC, not the reader's.** Every stamp in this ledger is
    (`_now`), so a monthly budget rolls over at UTC midnight on the 1st, which
    is up to a day off from the month a provider's invoice or the user's own
    calendar would draw. Stated rather than fixed: the fix is a timezone this
    store has no way to know -- the backend serves a browser that never tells it
    one -- and inventing the host's would make the same library report different
    spend on two machines.
    """
    until = _valid_day(until) or _today()
    if normalize_period(period) == TOTAL:
        return (date.fromisoformat(until) - timedelta(days=MAX_DAYS - 1)).isoformat(), until
    return until[:8] + "01", until


def budget(campaign: str, limit_usd: object, period: object = "") -> dict:
    """Where a campaign stands against its budget (#153).

    ``limit_usd`` and ``period`` come straight off ``campaign.md``'s
    frontmatter, unparsed — normalizing them is this function's job, because it
    is the only place that knows what an unusable one has to mean (see
    `normalize_limit`).

    **A campaign with no budget is not scanned at all.** It reports
    ``{"level": "off"}`` and no spend fields, rather than a zero: nobody asked
    what this campaign cost, and a `spent_usd: 0.0` in the payload is a number a
    view will render as a fact. That also keeps the common case — most campaigns
    never set a budget — free of a file scan on every campaign load.

    Only ``cost_usd`` counts against the cap. Subscription-billed calls
    (``estimated_usd``) are reported beside it and never summed in, for the
    reason the module docstring gives: charging a budget for money nobody paid
    would have a Claude Agent user hitting their cap on their first evening.
    ``unpriced_calls`` is what says the figure is a floor.
    """
    limit = normalize_limit(limit_usd)
    period = normalize_period(period)
    if not limit:
        return {"limit_usd": 0.0, "period": period, "level": OFF,
                "warn_fraction": WARN_FRACTION}
    since, until = period_window(period)
    totals = dict(_ZERO)
    for row in _read_rows(since, until):
        if not _is_call(row) or row.get("campaign") != campaign:
            continue
        _add(totals, row)
    spent = round(totals["cost_usd"], _CENTS)
    fraction = spent / limit
    level = OVER if spent >= limit else WARN if fraction >= WARN_FRACTION else OK
    return {"limit_usd": limit, "period": period, "since": since, "until": until,
            "spent_usd": spent, "estimated_usd": round(totals["estimated_usd"], _CENTS),
            "unpriced_calls": totals["unpriced_calls"], "calls": totals["calls"],
            # Rounded like the money it is derived from, and for the same
            # reason: 0.7999999999999999 renders as 79.99999999999999%.
            "fraction": round(fraction, 4), "level": level,
            "warn_fraction": WARN_FRACTION}
