"""Executing the suite: build a fixture, assemble its prompt, obtain output,
score it.

Two ways to obtain output:

  replay (default)  read the checked-in recordings. Offline, deterministic, no
                    API key — this is the mode pytest runs, and the one that
                    guards prompt-template edits.
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
from collections import Counter
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING

from grimoire import decisions, inference, llm
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


def score(case: Case, variant: str, output: str) -> Result:
    ctx = prepare(case)
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

    result = score(case, recording.variant, path.read_text(encoding="utf-8"))
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
                        model: str = "") -> dict[str, dict | ResolvedInference]:
    """`conn_key` -> where the app would send that case's call, read from the
    real store -- one resolution per distinct key: the whole decide
    resolution for a decide case (`run_stages` builds its chain from it), the
    connection dict for a generate case. A decide case resolves its task with
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
    out: dict[str, dict | ResolvedInference] = {}
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
        out[key] = resolved if case.schema is not None else resolved.conn
    return out


class BackendRefusedError(ValueError):
    """A forced decide backend the resolution's primary cannot serve, in one
    sentence. Raised before anything is sent."""


def _alone(conn: dict) -> dict:
    """A copy of `conn` without the fallback it carries (`llm.FALLBACK_KEY`):
    a forced backend measures the primary alone."""
    return {k: v for k, v in conn.items() if k != llm.FALLBACK_KEY}


def chain(resolved: ResolvedInference, backend: str = CHAIN) -> tuple[inference.Stage, ...]:
    """The decide chain a live run sends `resolved`'s case down.

    `chain` is `inference.stages(resolved)`, exactly what production sends.
    `native` and `structured` are one stage on the primary, without its
    fallback, so the two backends can be compared on the SAME model (a
    native-only model against a structured one would compare the models as
    well). Each refuses (`BackendRefusedError`) a primary that cannot take
    it: `native` a connection kind with no decisions endpoint
    (`llm.NATIVE_DECISION_KINDS`) or a provider preset whose `never` holds
    `decide_native`; `structured` a primary known unable to generate
    (`resolve.generates`)."""
    if backend == CHAIN:
        return inference.stages(resolved)
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
    elif not inference_resolve.generates(primary):
        raise BackendRefusedError(
            f"--decide-backend structured: {where} is known unable to generate.")
    return (inference.Stage(backend, _alone(primary.conn), None),)


def backend_note(decision: decisions.Decision) -> str:
    """What answered `decision`, for the report: `backend: <name>` when one
    backend answered every item; when stages with different backends split
    the batch (`Decision.backend` is ""), each backend with how many items it
    answered, in the order they first answer -- `backend: native 3,
    structured 4`. Items nothing answered are not counted."""
    if decision.backend:
        return f"backend: {decision.backend}"
    counts = Counter(r.backend for r in decision.items if r.backend)
    return "backend: " + ", ".join(f"{name} {n}" for name, n in counts.items())


def live(case: Case, target: dict | ResolvedInference, record: bool = False, *,
         client=None, backend: str = CHAIN) -> Result:
    """One real generation for `case`, scored against the baseline expectation
    (live output must PASS). With `record`, the reply replaces the baseline
    recording — counterexample variants are never overwritten.

    A generate case (`target` a connection dict) is one `complete`. A decide
    case (`target` its decide resolution) is answered by `inference.run_stages`
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
    if decide:
        assert not isinstance(target, dict), f"{case.id} is a decide case: pass its resolution"
        stages = chain(target, backend)
    else:
        assert isinstance(target, dict), f"{case.id} is a generate case: pass its connection"
    note = ""

    async def ask(c) -> str:
        nonlocal note
        if not decide:
            return await c.complete(ctx["messages"], target)
        explain = ctx.get("explain", "")
        decision = await inference.run_stages(case.task, ctx["items"], stages, client=c,
                                              explain=explain)
        note = backend_note(decision)
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


def live_all(cases: tuple[Case, ...], conns: dict[str, dict | ResolvedInference], isolate,
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
            continue
        failed += 1
        if r.error:
            lines.append(f"           error: {r.error}")
        for c in r.failures:
            detail = f": {c.detail}" if c.detail else ""
            lines.append(f"           {c.name}{detail}")
    total = len(results)
    lines.append("")
    lines.append(f"{total - failed}/{total} passed" if failed
                 else f"all {total} checks passed")
    return ascii_safe("\n".join(lines))


def ascii_safe(text: str) -> str:
    """`text` with anything the locale code page might not encode replaced.
    Used on every string this harness prints, not just report()'s own."""
    return text.encode("ascii", "replace").decode("ascii")
