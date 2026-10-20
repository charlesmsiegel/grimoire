"""One call holder and terminal usage record per metered attempt."""

from __future__ import annotations

import time

from .. import errors
from . import _ledger, _rows


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
            text = await operations.generate("chat", messages, client=client,
                                             resolved=resolved, usage=m.usage,
                                             stream=False)

    Recording happens exactly once however the meter ends, so a caller that
    finishes explicitly and is then unwound by an exception files one row rather
    than two.
    """

    def __init__(self, task: str, *, kind: str = _rows.KIND_LLM, campaign: str = "",
                 scene: str = "", model: str = "", post: int | None = None,
                 round_id: str = "", response_id: str = "", run_id: str = "",
                 loop_turn: int | None = None, tool_calls: int = 0):
        self.task = task
        self.kind = kind
        self.campaign = campaign
        self.scene = scene
        self.model = model
        self.post = post
        self.round_id = round_id
        self.response_id = response_id
        #: The caller's run (01g-C3). `tool_calls` may be set after the meter
        #: opens, since a loop learns the count only once the call returned;
        #: `done` reads all three when it files.
        self.run_id = run_id
        self.loop_turn = loop_turn
        self.tool_calls = tool_calls
        self.usage: dict = {}
        if isinstance(run_id, str) and run_id:
            # Seeded for the capture, never read back for the row: a seeded
            # holder is still unsent (`sent`).
            self.usage[_rows.RUN_KEY] = run_id
        self.row: dict | None = None
        self._done = False
        self._t0 = time.monotonic()

    def __enter__(self) -> Meter:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        """Record what happened, then let the exception through untouched
        (returning False re-raises).

        A cancellation is not a failure, and telling them apart matters: an
        `asyncio.CancelledError` unwinds this block at every joined `generate` site
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
        elif getattr(exc, _rows.NOT_A_FAILURE, True) is False:
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
        attempt, so a holder it never stamped (`sent`: nothing in it but the
        keys a caller places before the call) means nothing was ever sent —
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
        forces the ordering: refused before the facade stamps anything,
        nothing is sent, so `usage` stays unsent (`sent`: at most the keys a
        caller placed before the call) and the early return below fires -- and
        a provider that has never been
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
        if not _rows.sent(self.usage):
            return None
        cost = self.usage.get("cost_usd")
        served = self._served()
        self.row = _ledger.record(
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
            post=self.post, round_id=self.round_id, response_id=self.response_id,
            images=self.usage.get("images", 0), run_id=self.run_id,
            loop_turn=self.loop_turn, tool_calls=self.tool_calls, **served)
        try:
            self.usage.pop(_rows.ESTIMATE_KEY, None)
            self.usage.pop(_rows.ENDED_KEY, None)
        except Exception:  # noqa: BLE001 - housekeeping after the row; never fatal
            pass
        return self.row

    #: What served the call, as the facade filed it into the holder
    #: (`llm._stamp`, `llm_usage.account`): each one a `record` field.
    SERVED = ("operation", "provider_id", "requested_model", "role", "preset",
              "billing", "decision_mode", "hop")

    def _served(self) -> dict:
        """The `SERVED` fields from the holder, `str` values only, and
        `tokens_estimated`. Guarded whole: a holder that cannot be read costs
        these fields, never the row (ruling I3).

        Counts nothing and loads nothing. The flag is the facade's: it is True
        only when the facade estimated a count (`ESTIMATED`) for an attempt
        whose stream ended on its own (`ENDED_KEY`)."""
        try:
            got = {key: self.usage.get(key) for key in self.SERVED}
            served: dict = {key: value for key, value in got.items()
                            if isinstance(value, str)}
            served["tokens_estimated"] = (self.usage.get(_rows.ESTIMATED) is True
                                          and self.usage.get(_rows.ENDED_KEY) is True)
            return served
        except Exception:  # noqa: BLE001 - see the docstring
            return {}


def meter(task: str, *, kind: str = _rows.KIND_LLM, campaign: str = "", scene: str = "",
          model: str = "", post: int | None = None,
          round_id: str = "", response_id: str = "", run_id: str = "",
          loop_turn: int | None = None, tool_calls: int = 0) -> Meter:
    """A `Meter` for one call. `model` is only a fallback: the facade stamps the
    model the request actually ran on, which differs after a fallback route.

    `post` is the transcript index of the player post this call is answering,
    and is passed only by the routes that answer one — see `record`.

    A round groups the selector and its character contributions; a response ID
    groups that contribution and its replacement attempts. These stable optional
    identities survive transcript index changes without rewriting the ledger.
    Selectors have no response identity; older callers have neither.

    `run_id`, `loop_turn` and `tool_calls` say which run of a caller the call
    belonged to (01g-C3); see `record` and `Meter`."""
    return Meter(task, kind=kind, campaign=campaign, scene=scene, model=model,
                 post=post, round_id=round_id, response_id=response_id,
                 run_id=run_id, loop_turn=loop_turn, tool_calls=tool_calls)
