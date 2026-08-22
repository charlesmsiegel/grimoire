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

Pricing has three sources, and the difference between them is the point:

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
  cost. The field is then **absent**, not zero, which is what lets a re-pricing
  pass over the ledger tell "free" from "unknown".

  ``store.pricing`` (#158) is that pass. Where the user has typed a per-token
  rate for the model, a rollup prices those rows at it and reports the figure
  as ``modelled_usd`` — a third column, never added to the other two, because
  it is arithmetic this side did rather than a number a provider sent.
  ``unpriced_calls`` then counts only what is left: calls with no reported
  price and no rate to model one, which is what makes a total's incompleteness
  visible rather than quietly understated.

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

from . import atomic, errors, paths, pricing

#: The row kinds this ledger holds. ``llm`` is the only one written today;
#: the field exists from the first row so image generation (#159) can share the
#: file rather than needing a second one, and so a reader can filter for the
#: kind it understands instead of assuming every row is a chat completion.
KIND_LLM = "llm"

#: The attribute an exception sets to say "I am not a provider failure".
#:
#: `Meter` cannot tell these apart by type -- the store does not import the
#: routes that define them (#239) -- and it cannot tell them apart by looking
#: either: `BudgetRefused` is an `LLMError` subclass carrying `kind="timeout"`,
#: which is indistinguishable from a provider that really did time out. But the
#: two mean opposite things. A refusal means the call was NEVER ISSUED because
#: the clock was already gone, and `Abandoned` means the person waiting closed
#: the review. Neither is a failure of anything, and recording them would put
#: rows in the #156 error store for work that was deliberately not done -- with
#: the budget one wearing the same kind as a real timeout, which is the worst
#: possible place for a false positive.
#:
#: So the exception declares it, as a class attribute, and the meter treats it
#: exactly as it treats a cancellation. Absent means True: an ordinary
#: exception IS a failure, which is the safe default for anything new.
NOT_A_FAILURE = "llm_call_failed"

#: A row saying one scene id became another (#153). Not a call, and every
#: rollup skips it -- see `_is_call`. A scene's id is its filename stem, so the
#: first date set on a scene renames it and every store holding that id has to
#: follow (`store.scene_refs`). This ledger cannot follow the way the others do:
#: they rewrite a JSON file under the campaign lock, and rewriting a month file
#: would race the unlocked `O_APPEND` writes that are the whole reason `record`
#: never blocks a turn. So the rename is APPENDED like everything else, and the
#: read side walks the trail (`_scene_now`) instead. That also keeps the file what
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
#:
#: **Three money columns, and no two of them may ever be added.** `cost_usd` is
#: what an account was charged. `estimated_usd` is what a subscription-billed
#: call would have cost at the provider's own API rates and did not
#: (`cost_basis: "equivalent"`). `modelled_usd` is this side's arithmetic over
#: rates the *user* typed (`store.pricing`, #158), for the calls whose provider
#: named no price at all -- the weakest of the three, and the only one grimoire
#: computes rather than receives. Each carries its own call count so a view can
#: say how much of a total is which, and `unpriced_calls` keeps counting only
#: what none of them covers -- and `unmetered_calls` is the slice of THAT which
#: no rate could ever cover, because the provider reported no token counts
#: either. The split exists so a view can tell a reader whether typing a rate
#: would help: for an unmetered call it would not, and sending them to do it is
#: sending them to an action that cannot resolve the warning.
_ZERO = {"calls": 0, "errors": 0, "prompt_tokens": 0, "completion_tokens": 0,
         "total_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
         "cost_usd": 0.0, "estimated_usd": 0.0, "modelled_usd": 0.0,
         "priced_calls": 0, "unpriced_calls": 0, "subscription_calls": 0,
         "modelled_calls": 0, "unmetered_calls": 0, "duration_ms": 0}

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
           attempts: int = 1, post: int | None = None,
           ts: str | None = None) -> dict | None:
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
        # Annotated, so the numeric fields below are not checked against a
        # `dict[str, str]` inferred from these three string values -- a row is
        # mixed by construction, and the alternative is five type: ignores.
        row: dict = {"ts": ts, "kind": kind, "task": task}
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
        # Which player post this generation was answering (#153): the index of
        # the last user-role message in the transcript when the turn was
        # claimed. Only the turn-producing routes pass one -- an absorb or a
        # rolling summary belongs to the scene, not to a post -- so an absent
        # field means "not attributable", never "post 0".
        #
        # An INDEX, and so only as stable as indices are: a cut or a retcon
        # renumbers what follows it, and the ledger is append-only and cannot
        # follow. The rows either dangle past the end of a shortened transcript
        # (invisible, which is the honest outcome for a turn that no longer
        # exists) or, after a retcon that removed a middle post, name the post
        # that took the index. That is the same trade `KIND_RENAME` makes for
        # scene ids, without a rename row to make it recoverable -- so the
        # per-post figure is documented as what the scene's OWN totals are not:
        # a breakdown, correct while the transcript only grows.
        # Tested rather than coerced: `int("x")` raises, and this whole body is
        # inside the guard that keeps a bookkeeping failure from costing a turn
        # -- so an `int()` here would answer a bad index by dropping the entire
        # row. A field that will not serialize is worth less than the call.
        if isinstance(post, int) and not isinstance(post, bool) and post >= 0:
            row["post"] = post
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
                 scene: str = "", model: str = "", post: int | None = None):
        self.task = task
        self.kind = kind
        self.campaign = campaign
        self.scene = scene
        self.model = model
        self.post = post
        self.usage: dict = {}
        self.row: dict | None = None
        self._done = False
        self._t0 = time.monotonic()

    def __enter__(self) -> Meter:
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
        elif getattr(exc, NOT_A_FAILURE, True) is False:
            # Work that was deliberately not done -- see `NOT_A_FAILURE`. The
            # same treatment a cancellation gets, and for the same reason:
            # nothing went wrong, so nothing should say it did.
            self.done("aborted")
        elif isinstance(exc, Exception):
            # `kind` is the LLMError taxonomy the frontend already branches on;
            # anything else is recorded by its type name, which is the only
            # label a non-LLM failure has. `exc` is handed down so the mark is
            # applied by whatever actually wrote the row -- see `done`.
            self.done("error", getattr(exc, "kind", None) or type(exc).__name__,
                      detail=str(exc).strip(), exc=exc)
        else:
            self.done("aborted")
        return False

    def done(self, status: str = "ok", error: str = "",
             detail: str = "", exc: BaseException | None = None) -> dict | None:
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

        **A failure is recorded even when the row is not** (#156), and the two
        conditions are deliberately not the same one. Every LLM call in the app
        runs under a meter, which makes this the one place that sees all of
        them go wrong -- so the error store is instrumented here rather than at
        sixteen call sites, half of which would end up passing no `kind` and
        dropping out of the per-kind counts. `missing_key` is the case that
        forces the ordering: nothing is ever sent, so `usage` stays empty and
        the early return below fires -- and a provider that has never been
        configured is precisely the failure a user most needs written down.

        The ledger is not the error store and this is not a double write. A
        ledger row is one call's *accounting* -- what it cost, how long it took,
        whether it worked -- and its `errors` count is what gives an error
        RATE its denominator. The error store is the failure log, per module,
        including the failures that were never a call at all. `store.metrics`
        reads both and says which number came from where.
        """
        if self._done:
            return self.row
        self._done = True
        if status == "error":
            # `task` is the module axis #156 aggregates on: "dossier",
            # "tagline", "suggestions", "chat" -- what a reader would name, and
            # what they would say was broken.
            errors.record(self.task, error or "unspecified", detail or error,
                          campaign=self.campaign, scene=self.scene, task=self.task)
            if exc is not None:
                # Marked HERE, by the code that wrote the row, and only when it
                # wrote one. Marking from `__exit__` instead meant a meter
                # already finished (`self._done`) still stamped the exception
                # as recorded, so the call site catching it next stayed silent
                # about a failure nothing had written down.
                errors.mark_recorded(exc)
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
            status=status, error=error, attempts=self.usage.get("attempts", 1),
            post=self.post)
        return self.row


def meter(task: str, *, kind: str = KIND_LLM, campaign: str = "", scene: str = "",
          model: str = "", post: int | None = None) -> Meter:
    """A `Meter` for one call. `model` is only a fallback: the facade stamps the
    model the request actually ran on, which differs after a fallback route.

    `post` is the transcript index of the player post this call is answering,
    and is passed only by the routes that answer one — see `record`."""
    return Meter(task, kind=kind, campaign=campaign, scene=scene, model=model,
                 post=post)


# ---- rollups ----
def calls(days: int = 30, campaign: str = "", *,
          since: str = "", until: str = ""):
    """Every generation in the last ``days``, oldest first.

    The public half of `_read_rows`, for a reader that wants the rows rather
    than a rollup -- `store.metrics` needs each call's own `duration_ms` to
    take a percentile of, and a bucket's summed duration cannot be
    un-averaged back into a distribution.

    Same window arithmetic and the same clamp `summary` uses, so "the last 30
    days" means the same span on both halves of the stats page; and the same
    `_is_call` filter, so a rename row cannot enter a latency distribution as a
    call that took no time.

    ``since``/``until`` name the window outright, for a caller that reads more
    than one store into a single report. `store.metrics` does: it takes
    latencies from this ledger and errors from `store.errors`, and each
    resolving "the last 30 days" from its own clock meant a report generated
    across UTC midnight could quote a p90 from one window beside an error count
    from another -- silently, and only ever for the person awake at midnight.
    The caller resolves the pair once and hands the same one to both.
    """
    days = max(1, min(int(days), MAX_DAYS))
    if not (since and until):
        until = _today()
        since = (date.fromisoformat(until) - timedelta(days=days - 1)).isoformat()
    for row in _read_rows(since, until):
        if _is_call(row) and not (campaign and row.get("campaign") != campaign):
            yield row


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


class Rates:
    """The user's rate table (#158), resolved once per model instead of per row.

    A rollup asks "what would this have cost?" for every unpriced row it walks,
    and `pricing.rate_for` scans the wildcard entries each time it is asked. A
    heavy month is tens of thousands of rows across a handful of models, so the
    answer is memoized per model id and the table is read once, at construction.

    Read once also means a rollup is drawn against ONE table: the file could
    otherwise be saved mid-scan and half a report would be priced at the old
    rates and half at the new, with nothing saying so.

    `off()` is the no-estimates table, and it is not the same thing as an empty
    one: a caller that must not model anything (the budget, which measures money
    actually owed) says so rather than relying on the user's file being empty.
    """

    def __init__(self, table: dict[str, dict] | None):
        self.table = table or {}
        self._seen: dict[str, dict | None] = {}

    @classmethod
    def current(cls) -> Rates:
        """The table as it is on disk right now. Never raises — `read_pricing`
        fail-softs to `{}`, so a broken file costs the estimates and not the
        report they were going to sit beside."""
        return cls(pricing.read_pricing())

    @classmethod
    def off(cls) -> Rates:
        return cls(None)

    def entry(self, model: object) -> dict | None:
        if not self.table:
            return None
        key = model if isinstance(model, str) else ""
        if key not in self._seen:
            self._seen[key] = pricing.rate_for(self.table, key)
        return self._seen[key]

    def estimate(self, row: dict) -> float | None:
        """What this row would have cost at the user's rates, or None.

        None for a row nothing prices AND for a row nobody counted — see
        `pricing.estimate`, which is where that second case is decided. The
        counts are read raw rather than through `_int` on purpose: `_int` turns
        an absent count into 0, and 0 is exactly the value that must stay
        distinguishable from "not counted" here.
        """
        entry = self.entry(row.get("model"))
        if entry is None:
            return None
        return pricing.estimate(
            entry,
            prompt_tokens=_count(row.get("prompt_tokens")),
            completion_tokens=_count(row.get("completion_tokens")),
            cache_read_tokens=_count(row.get("cache_read_tokens")),
            cache_write_tokens=_count(row.get("cache_write_tokens")))


def _count(value: object) -> int | None:
    """A token count as the estimator needs it: the number, or None when the
    field was absent or unusable. Deliberately not `_int`, which floors both of
    those to 0 — a rollup adding zero is harmless, but pricing zero tokens
    produces `$0.00` for a call nobody measured."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, value)


def _add(bucket: dict, row: dict, rates: Rates | None = None) -> None:
    """Fold one row into a bucket. Reads defensively — a row is a line from a
    file a human can edit, so a string where a number belongs must cost that
    field rather than the whole report.

    `rates` is the user's per-token table (#158). It only ever touches the rows
    that arrived with no price at all: a modelled figure lands in `modelled_usd`
    and its own count, and the row stops being `unpriced` — it is now priced by
    an estimate, which is a different and weaker claim, made in a different
    column. `None` (the default) models nothing, which is what a caller
    measuring money actually owed passes.
    """
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
        modelled = rates.estimate(row) if rates is not None else None
        if modelled is None:
            bucket["unpriced_calls"] += 1
            # Counted whether or not a rate exists, and read straight off the
            # row rather than from the estimator's verdict: the question is
            # "could ANY rate have priced this", and the answer is no whenever
            # a count is missing -- see `pricing.estimate`, which requires both.
            if _count(row.get("prompt_tokens")) is None \
                    or _count(row.get("completion_tokens")) is None:
                bucket["unmetered_calls"] += 1
        else:
            bucket["modelled_calls"] += 1
            bucket["modelled_usd"] += modelled
        return
    bucket["priced_calls"] += 1
    if row.get("cost_basis") == "equivalent":
        bucket["subscription_calls"] += 1
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


def _money(value: float) -> float:
    """One money column, rounded — but never rounded THROUGH zero.

    `_CENTS` puts the floor at $0.0000005, and a real figure can sit under it:
    twenty tokens at $0.00002/1k is 4e-7. Rounded that becomes `0.0` while the
    call count beside it stays positive, and every cost surface then renders a
    priced call as `$0.00` — the one claim this module exists to prevent, made
    by the rounding rather than by the ledger. A positive value that would
    round away keeps its own precision instead; the views already have a
    `<$0.0001` rendering for figures that small.
    """
    rounded = round(value, _CENTS)
    return value if rounded == 0.0 and value > 0.0 else rounded


def _rounded(bucket: dict) -> dict:
    return {**bucket, "cost_usd": _money(bucket["cost_usd"]),
            "estimated_usd": _money(bucket["estimated_usd"]),
            "modelled_usd": _money(bucket["modelled_usd"])}


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
    rates = Rates.current()

    for row in _read_rows(since, until):
        if not _is_call(row) or (campaign and row.get("campaign") != campaign):
            continue
        _add(totals, row, rates)
        ts = row["ts"]
        if ts >= _SESSION_START:
            _add(session, row, rates)
        if ts[:10] == until:
            _add(today, row, rates)
        _add(by_day.setdefault(ts[:10], dict(_ZERO)), row, rates)
        _add(by_model.setdefault(_label(row.get("model")), dict(_ZERO)), row, rates)
        _add(by_task.setdefault(_label(row.get("task")), dict(_ZERO)), row, rates)
        if isinstance(row.get("campaign"), str) and row["campaign"]:
            _add(by_campaign.setdefault(row["campaign"], dict(_ZERO)), row, rates)

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


def _turn(row: dict, rates: Rates | None = None) -> dict:
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
        # What the user's own rates (#158) say this would have cost, for a turn
        # the provider priced at nothing. Beside `cost_usd` and never instead of
        # it: a turn with a real price keeps it, and a turn with neither stays
        # `null` in both columns so the view can still say "unpriced".
        "modelled_usd": None if _float(row.get("cost_usd")) is not None
                        else (rates.estimate(row) if rates is not None else None),
        "post": _post(row),
        "duration_ms": _int(row.get("duration_ms")),
    }


def _post(row: dict) -> int | None:
    """The player post this row was answering, or None when it names none.
    `_int` is deliberately not used: it floors an absent field to 0, and 0 is a
    real post index."""
    post = row.get("post")
    if isinstance(post, bool) or not isinstance(post, int) or post < 0:
        return None
    return post


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
    """
    trail: dict[str, list[tuple[str, str]]] = {}
    for row in _read_rows(since, until):
        if row.get("kind") != KIND_RENAME or row.get("campaign") != campaign:
            continue
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
    until = _today()
    start = _scan_since(since, until)
    # Whether the scan starts LATER than the scene did. `_scan_since` floors a
    # long-lived scene's window at `MAX_DAYS`, so a scene played across more
    # than a year is scanned from part-way through its own life -- and the
    # per-post breakdown then simply has no bucket for its older posts, which
    # in a transcript is indistinguishable from a post that cost nothing. The
    # flag is what lets the view say "this breakdown does not reach that far"
    # instead of implying it is complete.
    clamped = bool(_valid_day(since)) and start > _valid_day(since)
    trail = _rename_trail(campaign, start, until)
    rates = Rates.current()
    totals = dict(_ZERO)
    by_task: dict[str, dict] = {}
    by_post: dict[int, dict] = {}
    rerolls: dict[int, int] = {}
    turns: list[dict] = []
    for row in _read_rows(start, until):
        if not _is_call(row) or row.get("campaign") != campaign:
            continue
        # Asked per row rather than resolved to a set of ids up front: an id can
        # belong to this scene for a stretch and to another scene before and
        # after it, which no per-id answer can express. See `_scene_now`.
        if _scene_now(row.get("scene"), row["ts"], trail) != scene:
            continue
        _add(totals, row, rates)
        _add(by_task.setdefault(_label(row.get("task")), dict(_ZERO)), row, rates)
        post = _post(row)
        if post is not None:
            bucket = by_post.setdefault(post, dict(_ZERO))
            _add(bucket, row, rates)
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
            "generated_at": _now(), "totals": _rounded(totals),
            "by_task": _ranked(by_task),
            # Keyed by transcript index, ascending, so a view can walk it beside
            # the messages it is rendering. Every reroll of a post lands in that
            # post's bucket, which is the whole question this answers: what did
            # getting THIS reply, over however many attempts, actually cost.
            # `rerolls` is how many of those calls were re-answers rather than
            # parts of the first answer -- see `REROLL_TASKS`.
            "by_post": [{"post": i, "rerolls": rerolls.get(i, 0),
                         **_rounded(by_post[i])} for i in sorted(by_post)],
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
    root = ledger_dir()
    try:
        entries = [path.stem for path in root.iterdir() if path.suffix == ".jsonl"]
    except OSError:
        return _today()
    # Parsed as a real date, not merely matched against `_MONTH`. The regex
    # accepts `2026-00`, which sorts before every real month and which
    # `date.fromisoformat` then raises on inside `_window_files` -- turning one
    # stray filename (a hand edit, or a row whose `ts` came in through the
    # facade) into a 500 on every request to this endpoint. A name that is not
    # a month names no window, so it is skipped like an unparseable row.
    days = [day for day in (_valid_day(f"{stem}-01") for stem in entries) if day]
    return min(days) if days else _today()


def campaign_scenes(campaign: str, *, since: str = "", order: str = "cost",
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
    until = _today()
    start = min(_valid_day(since) or lifetime_since(), until)
    forward = _rename_trail(campaign, start, until)
    rates = Rates.current()
    totals = dict(_ZERO)
    buckets: dict[str, dict] = {}
    seen: dict[str, list[str]] = {}
    for row in _read_rows(start, until):
        if not _is_call(row) or row.get("campaign") != campaign:
            continue
        _add(totals, row, rates)
        scene = row.get("scene")
        sid = _scene_now(scene, row["ts"], forward) if isinstance(scene, str) and scene \
            else NO_SCENE
        _add(buckets.setdefault(sid, dict(_ZERO)), row, rates)
        stamps = seen.setdefault(sid, [row["ts"], row["ts"]])
        stamps[0] = min(stamps[0], row["ts"])
        stamps[1] = max(stamps[1], row["ts"])
    scenes = [{"scene": sid, "first_ts": seen[sid][0], "last_ts": seen[sid][1],
               **_rounded(bucket)} for sid, bucket in buckets.items()]
    order = order if order in SCENE_ORDERS else SCENE_ORDERS[0]
    _sort_scenes(scenes, order)
    limit = max(0, int(limit))
    return {"campaign": campaign, "since": start, "until": until,
            "generated_at": _now(), "totals": _rounded(totals), "order": order,
            "scenes": scenes[:limit], "listed": min(len(scenes), limit),
            "truncated": len(scenes) > limit}


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
    # `Rates.off()`, said out loud rather than left to the default: a budget
    # measures money owed, and an estimate over rates the user typed is not
    # that. Charging a cap for a modelled figure would have somebody hit their
    # limit on arithmetic nobody was invoiced for. `unpriced_calls` still says
    # the figure is a floor, which is the honest version of the same warning.
    rates = Rates.off()
    for row in _read_rows(since, until):
        if not _is_call(row) or row.get("campaign") != campaign:
            continue
        _add(totals, row, rates)
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
