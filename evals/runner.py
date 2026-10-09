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
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING

from grimoire import decisions, inference, llm, openai_compatible, openrouter
from grimoire.store.inference import providers
from grimoire.store.inference import resolve as inference_resolve

from .cases import BASELINE, Case
from .graders import Check

if TYPE_CHECKING:
    from grimoire.store.inference.resolved import ResolvedInference

#: `--decide-backend`'s choices: `chain` is what production sends
#: (`inference.stages`); the other two force one backend on the resolution's
#: primary, so the two can be compared on the same model.
CHAIN = "chain"
DECIDE_BACKENDS = (CHAIN, decisions.NATIVE_BACKEND, decisions.STRUCTURED_BACKEND)


@dataclass
class Result:
    case: Case
    variant: str
    checks: list[Check]
    output: str
    error: str = ""
    #: What answered a live decide case (`backend_note`); "" otherwise.
    note: str = ""

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
    fallback (`inference.without_fallback`, as production's own stage is),
    so the two backends can be compared on the SAME model (a native-only
    model against a structured one would compare the models as well). Each
    refuses (`BackendRefusedError`) a primary that cannot take it: `native` a
    connection kind with no decisions endpoint (`llm.NATIVE_DECISION_KINDS`),
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
        kind = primary.conn.get("kind", "openrouter")
        if kind not in llm.NATIVE_DECISION_KINDS:
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
    return (inference.Stage(backend, inference.without_fallback(primary.conn), None),)


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


def live(case: Case, target: ResolvedInference, record: bool = False, *,
         client=None, backend: str = CHAIN) -> Result:
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
    is opened and closed here."""
    from grimoire.llm import LLMClient, LLMError

    ctx = prepare(case)
    decide = case.schema is not None
    if isinstance(target, dict):
        raise TypeError(f"{case.id}: pass its resolution, not a connection")
    if decide:
        stages = chain(target, backend)
    note = ""

    async def ask(c) -> str:
        nonlocal note
        if not decide:
            return await inference.generate(case.task, ctx["messages"], client=c,
                                            resolved=target, stream=False)
        explain = ctx.get("explain", "")
        decision = await inference.run_stages(case.task, ctx["items"], stages, client=c,
                                              explain=explain)
        note = backend_note(decision)
        # Which items a native endpoint answered: it is asked for no
        # rationale, so a grader reads that item's as not applicable.
        ctx["native_items"] = frozenset(
            index for index, result in enumerate(decision.items)
            if result.backend == decisions.NATIVE_BACKEND)
        # And what it answered, as read: `render` writes an unread answer as
        # null, which a grader would read as a structured null.
        ctx["native_results"] = {index: decision.items[index]
                                 for index in ctx["native_items"]}
        return decisions.render(decision.items, ctx["items"], explain=bool(explain))

    async def run() -> str:
        if client is not None:
            return await ask(client)
        own = LLMClient()
        try:
            return await ask(own)
        finally:
            await own.aclose()

    try:
        output = asyncio.run(run())
    except LLMError as exc:
        return Result(case, BASELINE, [], "", f"{exc.kind}: {exc.detail}")

    result = Result(case, BASELINE, list(case.grade(ctx, output)), output, note=note)
    if record:
        path = case.baseline.path(case.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")
    return result


def live_all(cases: tuple[Case, ...], conns: dict[str, ResolvedInference], isolate,
             record: bool = False, *, client=None, backend: str = CHAIN) -> list[Result]:
    """Each case live, on what its task resolved to (`resolve_connections`,
    keyed by `conn_key`); a decide case down `chain(..., backend)`."""
    out = []
    for case in cases:
        with isolate():
            out.append(live(case, conns[conn_key(case)], record=record, client=client,
                            backend=backend))
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
    total = len(results)
    lines.append("")
    lines.append(f"{total - failed}/{total} passed" if failed
                 else f"all {total} checks passed")
    return ascii_safe("\n".join(lines))


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
