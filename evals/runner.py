"""Executing the suite: build a fixture, assemble its prompt, obtain output,
score it.

Two ways to obtain output:

  replay (default)  read the checked-in recordings. Offline, deterministic, no
                    API key — this is the mode pytest runs, and the one that
                    guards prompt-template edits. A native recording holds a
                    decisions endpoint's response bodies, read through its
                    adapter as a live native run reads them.
  live              call the model the app routes each case's task to, once per
                    case -- a decide case (one with a `schema`) down the chain
                    `inference.decide` would send it on (`inference.stages`
                    of its decide resolution), or on one backend forced over
                    its primary (`chain`). Costs money and is never
                    deterministic, so it is opt-in and its result is a
                    report, not a gate.

Isolation is the caller's job: every run_* function here assumes GRIMOIRE_HOME
already points at a fresh, empty directory. That keeps this module usable from
pytest (tmp_path + monkeypatch) and from the CLI (tempfile) without either one
inheriting the other's setup.

A live case is metered by the production meter (`store.usage.meter`, directly
for a generation, through `run_stages` for a decision), so every row it files
lands in that throwaway home's ledger -- the eval scope (spec 01a, §3). Before
the home is deleted, the case's rows are harvested onto `Result.rows`, each
copy stamped `scope: "eval"`, `eval_run` and `case`; nothing is ever filed in
the library's own ledger. A tripwire (`real_home`) refuses a case whose home is
the real one, before its fixture is built and again before the harvest, and a
drain (`drain`) keeps a detached follow-up from outliving the home.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from grimoire import adapters, decisions, inference, openai_compatible, openrouter, wire
from grimoire.store import paths, usage
from grimoire.store.inference import providers
from grimoire.store.inference import resolve as inference_resolve

from . import costs
from .cases import BASELINE, Case
from .graders import Check

if TYPE_CHECKING:
    from grimoire.store.inference.resolved import ResolvedInference

#: `--decide-backend`'s choices: `chain` is what production sends
#: (`inference.stages`); the other two force one backend on the resolution's
#: primary, so the two can be compared on the same model.
CHAIN = "chain"
DECIDE_BACKENDS = (CHAIN, decisions.NATIVE_BACKEND, decisions.STRUCTURED_BACKEND)

#: A case refused by the tripwire: its home is the real one, so nothing was
#: built, sent or harvested.
ISOLATE_ERROR = "eval isolate is not a throwaway home"

#: How long a case that ran through the app may wait, after its last request
#: returned, for the app's detached follow-ups (a turn's rolling summary,
#: scene-break check, tracker) to settle before its rows are harvested. At
#: least the longest single LLM call budget; structural, to be tuned later.
DRAIN_CEILING_S = 120

#: How often the drain looks again.
DRAIN_POLL_S = 0.05

#: What every case after a drain failure reports: the run stops, because a
#: follow-up still running would write into whatever home comes next.
NOT_RUN = "not run: an earlier case's follow-ups are still running"


@dataclass
class Result:
    case: Case
    variant: str
    checks: list[Check]
    output: str
    error: str = ""
    #: What answered a live decide case (`backend_note`); "" otherwise.
    note: str = ""
    #: Every ledger row a live case filed in its isolate, each a copy stamped
    #: `scope`, `eval_run` and `case` (`harvest`). None when nothing was
    #: harvested (replay, or a case refused before it ran).
    rows: tuple[dict, ...] | None = None
    #: Why the isolate's ledger could not be read, when it could not: the
    #: case's cost is then not reported, never zero.
    ledger_error: str = ""
    #: True when `rows` were read while the case's follow-ups were still
    #: running (`FollowUpsRunningError`): what they held then, not the whole.
    partial: bool = False
    #: The case's model work, measured once around it (its drain included),
    #: in ms; None when nothing ran. Never a sum of call durations.
    wall_ms: int | None = None
    #: A decide case's metered requests (`Decision.calls`), and one record
    #: per item (`item_records`). Empty for a generate case, and for a
    #: decide case no item of which answered (no `Decision`): it is then
    #: reported from its rows alone.
    calls: tuple[decisions.CallRecord, ...] = ()
    items: tuple[dict, ...] = ()
    #: Every harvested row folded into one bucket (`costs.fold`, the
    #: production `_add`), every task of the case included, and one bucket
    #: per task. None when the rows could not be read.
    bucket: dict | None = None
    by_task: dict[str, dict] = field(default_factory=dict)
    #: The rates the buckets were folded with (read once, in the real store),
    #: so a later fold of the same rows (the aggregate) prices them alike.
    rates: usage.Rates | None = field(default=None, repr=False, compare=False)

    @property
    def passed(self) -> bool:
        return not self.error and all(c.ok for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]


def prepare(case: Case) -> dict:
    """Build the fixture and assemble its prompt. Returns the context dict with
    `messages` filled in — graders that inspect the PROMPT (owned-lore) read it
    from there, so this must run before grade() even in replay mode."""
    ctx = case.build()
    ctx["messages"] = case.prompt(ctx)
    return ctx


#: The adapter a native recording's response bodies are read through, by
#: `Recording.native`.
NATIVE_ADAPTERS = {"openrouter": openrouter, "openai": openai_compatible}


def native_output(ctx: dict, native: str, text: str) -> str:
    """A native recording read as a live native run reads its replies: `text`
    is a JSON list of decisions response bodies, one per item of the case, in
    order, each read through the `native` adapter's `decision_result` -- the
    production mapping -- and written back as the structured reply its graders
    read (`decisions.render`). Every item is marked answered natively, so a
    rationale is graded n/a, as it is live, and its results are kept beside
    the text (`ctx["native_results"]`): `render` writes every unread answer
    as null, so a refusal, an abstention and a value naming no option would
    otherwise grade as the null a structured reply sent. A body the adapter
    refuses raises: a native recording is hand-authored, and one that no
    longer reads is a broken fixture, not a failed check."""
    bodies = json.loads(text)
    adapter = NATIVE_ADAPTERS[native]
    results = tuple(adapter.decision_result(body, item)
                    for body, item in zip(bodies, ctx["items"], strict=True))
    ctx["native_items"] = frozenset(range(len(results)))
    ctx["native_results"] = dict(enumerate(results))
    return decisions.render(results, ctx["items"], explain=bool(ctx.get("explain")))


def score(case: Case, variant: str, output: str, native: str = "") -> Result:
    """`output` scored by `case`'s graders; with `native`, first read as that
    adapter's response bodies (`native_output`)."""
    ctx = prepare(case)
    if native:
        output = native_output(ctx, native, output)
    return Result(case, variant, list(case.grade(ctx, output)), output)


# ------------------------------------------------------------------ replay

def replay(case: Case, recording) -> Result:
    """Score one checked-in recording and judge it against its declared
    expectation.

    For a counterexample the judgement is set EQUALITY against
    `recording.expect_fail`, not merely "something failed". Both directions are
    real regressions: a check that stopped firing means the grader went blind,
    and a check that started firing means the recording now violates something
    it was not written to violate, so it is no longer isolating what its name
    claims.
    """
    path = recording.path(case.id)
    if not path.exists():
        return Result(case, recording.variant, [], "", f"missing recording: {path}")

    result = score(case, recording.variant, path.read_text(encoding="utf-8"),
                   recording.native)
    if recording.expect_pass:
        return result

    actual = {c.name for c in result.failures}
    expected = set(recording.expect_fail)
    stopped, started = sorted(expected - actual), sorted(actual - expected)
    detail = ""
    if stopped:
        detail = f"checks that no longer fire: {stopped}"
    if started:
        detail = (detail + "; " if detail else "") + f"unexpected failures: {started}"
    return Result(case, recording.variant,
                  [Check(f"{case.id}.counterexample", actual == expected, detail)],
                  result.output)


def replay_all(cases: tuple[Case, ...], isolate) -> list[Result]:
    """`isolate` is a zero-arg context manager factory yielding a fresh
    GRIMOIRE_HOME per (case, variant) — passed in rather than chosen here so
    pytest and the CLI can each isolate their own way."""
    out = []
    for case in cases:
        for recording in case.recordings:
            with isolate():
                out.append(replay(case, recording))
    return out


# -------------------------------------------------------------------- live

def operation(case: Case) -> str:
    """The operation the app runs `case`'s task as: `decide` for a case with a
    schema, `generate` otherwise."""
    return "decide" if case.schema is not None else "generate"


def conn_key(case: Case) -> str:
    """Where `resolve_connections` files `case`'s connection: its task, or
    `<task> (decide)` for a decide case, so one task asked both ways is two
    resolutions rather than one silently serving both."""
    return case.task if case.schema is None else f"{case.task} (decide)"


def resolve_connections(cases: tuple[Case, ...], *, provider: str = "",
                        model: str = "") -> dict[str, ResolvedInference]:
    """`conn_key` -> where the app would send that case's call, read from the
    real store -- one resolution per distinct key, whole: a decide case's
    (`run_stages` builds its chain from it) and a generate case's
    (`inference.generate` sends on it). A decide case resolves its task with
    `operation="decide"`, as `inference.decide`'s callers do, so the Decision
    role (or whatever the route chose) answers it.

    `provider` and `model` (`--provider`/`--model`) override the selection
    for this run, through the seam a reroll uses (`override_inference`), with
    the same meaning and the same refusals. An override is a per-call
    selection: nothing is written to settings.

    Must be called BEFORE GRIMOIRE_HOME is repointed at a fixture — that is the
    whole reason it is a separate function. Resolves through the app's own seam
    (`routes.common.require_inference`): the role or route the Models page
    chose, its fallback, the model's facts and the route's preset -- never the
    legacy `active_connection_id`, frozen for older builds once the store is at
    format 2. Reads settings and credentials only; no campaign, world or
    character content is touched. The resolution can write once, running the
    same `llm_connections` seeding the app runs at startup on a library that
    predates connections; nothing else here writes to the real store.

    A task the seam refuses (no key, a model known unable to do the job, an
    override naming nothing) raises RuntimeError with the seam's own reason.
    """
    from fastapi import HTTPException

    from grimoire.routes.common import override_inference, require_inference

    body = SimpleNamespace(provider=provider, model=model) if provider or model else None
    out: dict[str, ResolvedInference] = {}
    for case in cases:
        key = conn_key(case)
        if key in out:
            continue
        try:
            if body is not None:
                resolved = override_inference(body, case.task,
                                              operation=operation(case))[0]
            else:
                resolved = require_inference(case.task, operation=operation(case))
        except HTTPException as exc:
            detail = exc.detail
            if isinstance(detail, dict):
                detail = detail.get("detail") or detail.get("kind") or ""
            raise RuntimeError(f"{key}: {detail} (choose a model on the Models page)") from exc
        out[key] = resolved
    return out


class BackendRefusedError(ValueError):
    """A forced decide backend the resolution's primary cannot serve, in one
    sentence. Raised before anything is sent."""


def chain(resolved: ResolvedInference, backend: str = CHAIN) -> tuple[inference.Stage, ...]:
    """The decide chain a live run sends `resolved`'s case down.

    `chain` is `inference.stages(resolved)`, exactly what production sends.
    `native` and `structured` are one stage on the primary, without its
    fallback (`wire.Chain.alone`, as production's own stage is),
    so the two backends can be compared on the SAME model (a native-only
    model against a structured one would compare the models as well). Each
    refuses (`BackendRefusedError`) a primary that cannot take it: `native` a
    connection kind with no decisions endpoint (`adapters.decides_natively`,
    the registry's flag for the KIND),
    a provider preset whose `never` holds `decide_native`, or a model known
    (`resolve.decides_natively`: a `no` that is not a guess; `unknown` is
    allowed, spec 5.3) unable to decide natively; `structured` a primary
    known unable to generate (`resolve.generates`). A chain with no stage at
    all is refused too, rather than failing mid-run."""
    if backend == CHAIN:
        stages = inference.stages(resolved)
        if not stages:
            raise BackendRefusedError(
                f"{resolved.task} resolved to no decide stage: its model can "
                f"neither generate nor decide natively.")
        return stages
    if backend not in DECIDE_BACKENDS:
        raise ValueError(f"unknown decide backend {backend!r}")
    if not resolved.attempts:
        raise BackendRefusedError(
            f"{resolved.task} resolved to no model to force {backend} on.")
    primary = resolved.attempts[0]
    where = f"{primary.model or '(default)'} on {primary.provider_id}"
    if backend == decisions.NATIVE_BACKEND:
        kind = primary.target.kind
        if not adapters.decides_natively(kind):
            raise BackendRefusedError(
                f"--decide-backend native: {where} is a {kind} connection, "
                f"which has no native decisions endpoint.")
        preset = providers.PRESETS.get(primary.provider_preset)
        if preset is not None and "decide_native" in preset.never:
            raise BackendRefusedError(
                f"--decide-backend native: {where} is behind the "
                f"{preset.label} preset, which never decides natively.")
        if not inference_resolve.decides_natively(primary):
            raise BackendRefusedError(
                f"--decide-backend native: {where} is known unable to decide natively.")
    elif not inference_resolve.generates(primary):
        raise BackendRefusedError(
            f"--decide-backend structured: {where} is known unable to generate.")
    return (inference.Stage(backend, wire.Chain(primary.target), None),)


def item_records(decision: decisions.Decision) -> tuple[dict, ...]:
    """One record per item of `decision`: the backend and stage that answered
    it, its answers' reasons (`refused`, `abstained`, `unreadable`, `error`),
    whether any answer carries a distribution, whether an escalation hop's
    call carried it, and `call` -- the index in `decision.calls` of the call
    that answered it (the last call carrying it that did not fail; else the
    last one carrying it; None for an item no call carried). Money is never
    here: a structured call's figures belong to the call, never to its items
    (spec 01a, section 6)."""
    out = []
    for index, result in enumerate(decision.items):
        carried = [n for n, record in enumerate(decision.calls) if index in record.items]
        answered = [n for n in carried if not decision.calls[n].error_kind]
        call = (answered or carried or [None])[-1]
        answers = result.answers.values()
        out.append({
            "index": index, "backend": result.backend,
            "stage": decision.calls[call].stage if call is not None else None,
            "reasons": sorted({a.reason for a in answers if a.reason}),
            "distribution": any(a.distribution is not None for a in answers),
            "escalated": any(decision.calls[n].hop == "escalation" for n in carried),
            "call": call})
    return tuple(out)


def backend_note(decision: decisions.Decision) -> str:
    """What answered `decision`, for the report: `backend: <name>` when one
    backend answered every item; when stages with different backends split
    the batch (`Decision.backend` is ""), each backend with how many items it
    answered, in item order (the backend of the lowest-numbered item it
    answered first) -- `backend: native 3, structured 4`. Items nothing
    answered are not counted. Each failed unit's final error
    (`Decision.errors`) follows, so an item a stage failed shows its cause
    beside the answer check it fails: `...; failed: bad_response: <detail>`."""
    if decision.backend:
        note = f"backend: {decision.backend}"
    else:
        counts = Counter(r.backend for r in decision.items if r.backend)
        note = "backend: " + ", ".join(f"{name} {n}" for name, n in counts.items())
    if decision.errors:
        note += "; failed: " + "; ".join(f"{e.kind}: {e.detail}" for e in decision.errors)
    return note


class FollowUpsRunningError(RuntimeError):
    """A case whose follow-ups outlived `DRAIN_CEILING_S`. Raised through the
    isolate, which keeps its home and does not restore the environment while
    anything might still write (`evals/run.py`'s `temp_home`); `result` is
    the case's failed result."""

    def __init__(self, result: Result):
        super().__init__(result.error)
        self.result = result


def _is_real_home(real_home: Path | None) -> bool:
    """Whether the active home is `real_home` (None checks nothing)."""
    if real_home is None:
        return False
    try:
        return paths.home().resolve() == Path(real_home).resolve()
    except OSError:
        return str(paths.home()) == str(real_home)


def drain(ctx: dict) -> bool:
    """Wait for a case's app to settle: True once `ctx["app"]` (a case that
    ran through the app hands it over) has no live run, False past
    `DRAIN_CEILING_S`. Then `ctx["shutdown"]`, when given, ends the app's
    lifespan. A case that made its calls directly (every case today) has
    neither, and returns at once."""
    app = ctx.get("app")
    if app is not None:
        deadline = time.monotonic() + DRAIN_CEILING_S
        while app.state.runs.any_live() is not None:
            if time.monotonic() >= deadline:
                return False
            time.sleep(DRAIN_POLL_S)
    shutdown = ctx.get("shutdown")
    if shutdown is not None:
        shutdown()
    return True


def harvest(run_day: str, run_id: str, case_id: str) -> tuple[dict, ...]:
    """Every call row filed in the active home since `run_day` (the run's
    start, so a run across UTC midnight keeps its earlier rows), each a copy
    stamped `scope: "eval"`, `eval_run` and `case`. The read is strict: an
    unreadable ledger raises `OSError` rather than reading as no rows, which
    would print as a free case. The isolate holds nothing else, so this is
    every row the case filed."""
    rows = [row for row in usage._read_rows(run_day, usage._today(), strict=True)
            if usage._is_call(row)]
    return tuple({**row, "scope": "eval", "eval_run": run_id, "case": case_id}
                 for row in rows)


def _settle(case: Case, ctx: dict, output: str, *, real_home: Path | None, run_day: str,
            run_id: str, rates: usage.Rates | None) -> tuple[tuple[dict, ...], str, int] | None:
    """After a case's model work: drain it (`FollowUpsRunningError` past the
    ceiling), check the tripwire again (None when it trips), then harvest:
    its rows, why the ledger could not be read when it could not, and how
    long the drain took (ms), which is part of the case's wall time."""
    started = time.monotonic()
    if not drain(ctx):
        raise _still_running(case, output, run_day, run_id, rates)
    drained = int((time.monotonic() - started) * 1000)
    if _is_real_home(real_home):
        return None
    rows, ledger_error = _harvested(run_day, run_id, case.id)
    return rows, ledger_error, drained


def _metered(rows: tuple[dict, ...], ledger_error: str,
             rates: usage.Rates | None) -> dict:
    """`Result`'s money fields for harvested `rows`: nothing when the ledger
    could not be read (its cost is not reported, never zero)."""
    if ledger_error:
        return {"rows": rows, "ledger_error": ledger_error, "rates": rates}
    return {"rows": rows, "ledger_error": "", "rates": rates,
            "bucket": costs.fold(rows, rates), "by_task": costs.by_task(rows, rates)}


def _harvested(run_day: str, run_id: str, case_id: str) -> tuple[tuple[dict, ...], str]:
    """`harvest`, and the reason it could not read the ledger ("" when it
    could)."""
    try:
        return harvest(run_day, run_id, case_id), ""
    except OSError as exc:
        return (), str(exc)


def _still_running(case: Case, output: str, run_day: str, run_id: str,
                   rates: usage.Rates | None) -> FollowUpsRunningError:
    """The failure of a case whose follow-ups outlived the drain. What its
    ledger holds so far is still read (reading writes nothing), so the spend
    it already made is reported, marked partial, rather than dropped."""
    rows, ledger_error = _harvested(run_day, run_id, case.id)
    return FollowUpsRunningError(Result(
        case, BASELINE, [], output,
        f"follow-ups still running past {DRAIN_CEILING_S}s; "
        f"its isolate is kept at {paths.home()}",
        partial=True, **_metered(rows, ledger_error, rates)))


async def _ask(case: Case, ctx: dict, target: ResolvedInference,
               stages: tuple[inference.Stage, ...] | None,
               client) -> tuple[str, decisions.Decision | None]:
    """The case's model work on `client`: a generation (`stages` None) or a
    decision down `stages`, as the reply text its graders read and the
    `Decision` when there is one."""
    if stages is None:
        # The production meter, into this case's throwaway home: no campaign
        # and no scene, as the case plays none.
        with usage.meter(case.task) as m:
            text = await inference.generate(case.task, ctx["messages"], client=client,
                                            resolved=target, usage=m.usage, stream=False)
        return text, None
    explain = ctx.get("explain", "")
    decision = await inference.run_stages(case.task, ctx["items"], stages, client=client,
                                          explain=explain)
    # Which items a native endpoint answered: it is asked for no rationale,
    # so a grader reads that item's as not applicable.
    ctx["native_items"] = frozenset(
        index for index, result in enumerate(decision.items)
        if result.backend == decisions.NATIVE_BACKEND)
    # And what it answered, as read: `render` writes an unread answer as
    # null, which a grader would read as a structured null.
    ctx["native_results"] = {index: decision.items[index] for index in ctx["native_items"]}
    return decisions.render(decision.items, ctx["items"], explain=bool(explain)), decision


def _model_work(case: Case, ctx: dict, target: ResolvedInference,
                stages: tuple[inference.Stage, ...] | None,
                client) -> tuple[str, decisions.Decision | None, Exception | None, int]:
    """`_ask` on `client`, or on one `LLMClient` opened and closed here:
    the reply, the decision, the provider's `LLMError` when it failed, and
    how long `_ask` took (ms) -- the model work alone, never the client's
    open and close."""
    from grimoire.llm import LLMClient, LLMError

    span: list[float] = []

    async def timed(c) -> tuple[str, decisions.Decision | None]:
        span.append(time.monotonic())
        try:
            return await _ask(case, ctx, target, stages, c)
        finally:
            span.append(time.monotonic())

    async def run() -> tuple[str, decisions.Decision | None]:
        if client is not None:
            return await timed(client)
        own = LLMClient()
        try:
            return await timed(own)
        finally:
            await own.aclose()

    def took() -> int:
        return int((span[-1] - span[0]) * 1000) if len(span) == 2 else 0

    try:
        output, decision = asyncio.run(run())
    except LLMError as exc:
        return "", None, exc, took()
    return output, decision, None, took()


def live(case: Case, target: ResolvedInference, record: bool = False, *,
         client=None, backend: str = CHAIN, real_home: Path | None = None,
         run_id: str = "", run_day: str = "",
         rates: usage.Rates | None = None) -> Result:
    """One real generation for `case`, scored against the baseline expectation
    (live output must PASS). With `record`, the reply replaces the baseline
    recording — counterexample variants are never overwritten.

    `target` is the case's resolution (`resolve_connections`). A generate case
    is one joined `inference.generate`, as the app's own calls are. A decide
    case is answered by `inference.run_stages`
    down `chain(target, backend)` -- by default the chain `inference.decide`
    sends, each stage on its own backend -- over the case's `items` and
    `explain`; the answers are written back as the structured reply
    (`decisions.render`) the case's graders read, and `Result.note` says which
    backend answered (`backend_note`). `client` is the facade to send
    through, owned by the caller (a test's fake); by default one `LLMClient`
    is opened and closed here.

    Every call is metered into the active home, and once the case's model
    work is done and drained (`drain`) its rows are harvested onto
    `Result.rows` (`harvest`; `run_id` and `run_day` name the run). With
    `real_home`, a case whose active home IS that home is refused before its
    fixture is built (`ISOLATE_ERROR`), and again before the harvest; None
    skips both checks. A case whose follow-ups outlive `DRAIN_CEILING_S`
    raises `FollowUpsRunningError`. `rates` prices modelled figures (read once
    in the real store by the caller)."""
    if isinstance(target, dict):
        raise TypeError(f"{case.id}: pass its resolution, not a connection")
    if _is_real_home(real_home):
        return Result(case, BASELINE, [], "", ISOLATE_ERROR)
    run_day = run_day or usage._today()
    ctx = prepare(case)
    try:
        stages = chain(target, backend) if case.schema is not None else None
        output, decision, failure, asked = _model_work(case, ctx, target, stages, client)
    except Exception:
        # Anything but a provider's error: still drained before it passes
        # through the isolate, which restores the environment -- a follow-up
        # left running would then file its row in the real library.
        if not drain(ctx):
            raise _still_running(case, "", run_day, run_id, rates) from None
        raise
    note = backend_note(decision) if decision is not None else ""
    error = f"{failure.kind}: {failure.detail}" if failure is not None else ""
    settled = _settle(case, ctx, output, real_home=real_home, run_day=run_day,
                      run_id=run_id, rates=rates)
    if settled is None:
        return Result(case, BASELINE, [], output, ISOLATE_ERROR)
    rows, ledger_error, drained = settled
    metrics = {"wall_ms": asked + drained, **_metered(rows, ledger_error, rates)}
    if decision is not None:
        metrics.update(calls=decision.calls, items=item_records(decision))
    if error:
        return Result(case, BASELINE, [], "", error, **metrics)

    result = Result(case, BASELINE, list(case.grade(ctx, output)), output, note=note,
                    **metrics)
    if record:
        path = case.baseline.path(case.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")
    return result


def live_all(cases: tuple[Case, ...], conns: dict[str, ResolvedInference], isolate,
             record: bool = False, *, client=None, backend: str = CHAIN,
             real_home: Path | None = None, run_id: str = "", run_day: str = "",
             rates: usage.Rates | None = None) -> list[Result]:
    """Each case live, on what its task resolved to (`resolve_connections`,
    keyed by `conn_key`); a decide case down `chain(..., backend)`. Each case
    runs inside its own `isolate()`, checked against `real_home` before its
    fixture is built (`live`). One run id and start day name every case's
    rows. A case whose follow-ups are still running stops the run: its
    isolate is kept (`FollowUpsRunningError` passes through it), and every case
    after it reports `NOT_RUN` rather than running into a home a straggler
    may still write to.

    `real_home` is the tripwire's reference; when the caller names none, it
    is the home active NOW, before the first isolate -- so the tripwire is
    never off for a caller that forgot it."""
    real_home = real_home if real_home is not None else paths.home()
    run_id = run_id or str(uuid.uuid4())
    run_day = run_day or usage._today()
    out: list[Result] = []
    for number, case in enumerate(cases):
        try:
            with isolate():
                out.append(live(case, conns[conn_key(case)], record=record, client=client,
                                backend=backend, real_home=real_home, run_id=run_id,
                                run_day=run_day, rates=rates))
        except FollowUpsRunningError as exc:
            out.append(exc.result)
            out.extend(Result(later, BASELINE, [], "", NOT_RUN) for later in cases[number + 1:])
            break
    return out


# ------------------------------------------------------------------ report

def report(results: list[Result]) -> str:
    """A plain-ASCII report.

    Piped output on Windows encodes with the locale code page, and a report
    that raises UnicodeEncodeError instead of printing the failure it found is
    worse than no report. Authoring details in ASCII is not sufficient on its
    own: provider error strings, exception messages and slices of model output
    all reach these lines and none of them are ours to constrain. So the whole
    report is transcoded on the way out.
    """
    lines, failed = [], 0
    for r in results:
        status = "ok  " if r.passed else "FAIL"
        note = f"  ({r.note})" if r.note else ""
        lines.append(f"  [{status}] {r.case.id}.{r.variant}{note}")
        lines.extend(_metrics(r))
        if r.passed:
            lines.extend(_not_applicable(r))
            continue
        failed += 1
        if r.error:
            lines.append(f"           error: {r.error}")
        for c in r.failures:
            detail = f": {c.detail}" if c.detail else ""
            lines.append(f"           {c.name}{detail}")
        lines.extend(_not_applicable(r))
    lines.extend(_aggregate_block(results))
    total = len(results)
    lines.append("")
    lines.append(f"{total - failed}/{total} passed" if failed
                 else f"all {total} checks passed")
    return ascii_safe("\n".join(lines))


#: The indent of a case's detail lines.
_DETAIL = "           "


def _stages(calls: tuple[decisions.CallRecord, ...]) -> str:
    """Per stage, its backend and how many of its calls returned out of how
    many it made: `(stage 0: native 3/3; stage 1: structured 2/2)`."""
    seen: dict[tuple[int, str], list[int]] = {}
    for record in calls:
        tally = seen.setdefault((record.stage, record.mode), [0, 0])
        tally[0] += 0 if record.error_kind else 1
        tally[1] += 1
    return " (" + "; ".join(f"stage {stage}: {mode} {ok}/{n}"
                            for (stage, mode), (ok, n) in sorted(seen.items())) + ")"


def _metrics(r: Result) -> list[str]:
    """A live case's metrics: its wall time, its calls (per stage for a
    decision), its tokens and its three money columns, never added together
    -- or, an unreadable ledger, that its cost is not reported, never
    `calls 0`. A case that ran more than one task adds a `by task` block.
    Nothing for a result with no harvest (replay)."""
    if r.rows is None:
        return []
    wall = f"wall {costs.seconds(r.wall_ms)}  " if r.wall_ms is not None else ""
    if r.ledger_error or r.bucket is None:
        return [f"{_DETAIL}{wall}cost: not reported (ledger unreadable)"]
    line = f"{_DETAIL}{wall}{costs.bucket_line(r.bucket)}"
    if r.calls:
        calls = f"calls {r.bucket['calls']}"
        line = line.replace(calls, calls + _stages(r.calls), 1)
    if r.partial:
        line += "  (partial: follow-ups still running)"
    lines = [line]
    if len(r.by_task) > 1:
        lines.append(f"{_DETAIL}by task:")
        lines.extend(f"{_DETAIL}  {task}: {costs.bucket_line(bucket)}"
                     for task, bucket in r.by_task.items())
    return lines


def _aggregate_block(results: list[Result]) -> list[str]:
    """Every live case's rows keyed by route, backend and hop, each folded at
    its case's rates. Wall time is per case, so it is `-` here; a hop's own
    time is the sum of its calls' durations, labelled `call time`, never
    wall."""
    costed = [r for r in results if r.rows is not None and not r.ledger_error]
    if not costed:
        return []
    lines = ["", "by route / backend / hop:"]
    for entry in costs.aggregate((r.rows or (), r.rates) for r in costed):
        bucket = entry["bucket"]
        line = (f"  {entry['route']} / {entry['backend']} / {entry['hop']}: wall -  "
                f"{costs.bucket_line(bucket)}")
        if entry["hop"] != costs.NO_HOP:
            line += f"  call time {costs.seconds(bucket['duration_ms'])}"
        lines.append(line)
    short = sum(1 for r in results
                if r.rows is not None and (r.partial or r.ledger_error))
    if short:
        lines.append(f"  ({short} case(s) not fully costed: partial or ledger unreadable)")
    return lines


def _not_applicable(r: Result) -> list[str]:
    """A passing check that was not applicable (its detail says `n/a`, e.g. a
    native item's rationale), listed so the report shows it was not graded
    rather than dropping it silently."""
    return [f"           {c.name}: {c.detail}" for c in r.checks
            if c.ok and c.detail.startswith("n/a")]


def ascii_safe(text: str) -> str:
    """`text` with anything the locale code page might not encode replaced.
    Used on every string this harness prints, not just report()'s own."""
    return text.encode("ascii", "replace").decode("ascii")
