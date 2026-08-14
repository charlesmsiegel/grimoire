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
_ZERO = {"calls": 0, "errors": 0, "prompt_tokens": 0, "completion_tokens": 0,
         "total_tokens": 0, "cost_usd": 0.0, "estimated_usd": 0.0,
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
           prompt_tokens: int = 0, completion_tokens: int = 0,
           cost_usd: float | None = None, cost_basis: str = "",
           duration_ms: int = 0, status: str = "ok", error: str = "",
           attempts: int = 1, ts: str | None = None) -> dict | None:
    """Append one call to the ledger. Returns the row, or None if nothing was
    written.

    **Never raises.** This runs on the generating path, inside the SSE
    finalizers, and a bookkeeping row that can fail a turn is a worse bug than
    the one it exists to diagnose — the judgement ``prompt_log.record`` already
    makes, for the same reason. A full disk, a read-only store or a home that
    cannot be created costs the row and nothing else.

    Takes no lock, unlike every other writer here. There is nothing to
    serialize: the write is a single ``O_APPEND`` line (see
    ``atomic.append_line``), never a read-modify-write, so concurrent callers
    interleave rows instead of losing them — and taking the campaign lock would
    both stall a turn and exclude the calls that have no campaign to lock.
    """
    ts = ts or _now()
    row = {"ts": ts, "kind": kind, "task": task}
    # Optional identity fields are omitted rather than written empty: a tagline
    # has no campaign and no scene, and `"campaign": ""` in the file reads as a
    # campaign whose id is the empty string.
    for key, value in (("campaign", campaign), ("scene", scene), ("model", model),
                       ("connection", connection), ("provider", provider)):
        if value:
            row[key] = value
    row["prompt_tokens"] = int(prompt_tokens or 0)
    row["completion_tokens"] = int(completion_tokens or 0)
    if cost_usd is not None:
        row["cost_usd"] = float(cost_usd)
        row["cost_basis"] = cost_basis or "billed"
    row["duration_ms"] = int(duration_ms or 0)
    row["status"] = status
    if error:
        row["error"] = error
    if attempts and attempts != 1:
        row["attempts"] = int(attempts)
    try:
        path = month_path(ts)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic.append_line(path, json.dumps(row))
    except (OSError, ValueError):
        # ValueError as well as OSError: a value that will not serialize
        # (a stray object in `model`, an infinite float from a malformed
        # provider reply) raises from `json.dumps` rather than from the write,
        # and must not escape either.
        return None
    return row


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
        if exc is None:
            self.done()
        else:
            # `kind` is the LLMError taxonomy the frontend already branches on;
            # anything else is recorded by its type name, which is the only
            # label a non-LLM failure has. Neither is allowed to suppress the
            # exception -- returning False re-raises.
            self.done("error", getattr(exc, "kind", None) or type(exc).__name__)
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
            prompt_tokens=self.usage.get("prompt_tokens", 0),
            completion_tokens=self.usage.get("completion_tokens", 0),
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
        if campaign and row.get("campaign") != campaign:
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
