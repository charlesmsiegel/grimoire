"""Executing the suite: build a fixture, assemble its prompt, obtain output,
score it.

Two ways to obtain output:

  replay (default)  read the checked-in recordings. Offline, deterministic, no
                    API key — this is the mode pytest runs, and the one that
                    guards prompt-template edits.
  live              call the active LLM connection once per case. Costs money
                    and is never deterministic, so it is opt-in and its result
                    is a report, not a gate.

Isolation is the caller's job: every run_* function here assumes GRIMOIRE_HOME
already points at a fresh, empty directory. That keeps this module usable from
pytest (tmp_path + monkeypatch) and from the CLI (tempfile) without either one
inheriting the other's setup.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .cases import BASELINE, Case
from .graders import Check


@dataclass
class Result:
    case: Case
    variant: str
    checks: list[Check]
    output: str
    error: str = ""

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

def resolve_connection() -> dict:
    """The user's ACTIVE LLM connection, read from the real store.

    Must be called BEFORE GRIMOIRE_HOME is repointed at a fixture — that is the
    whole reason it is a separate function. Reads credentials only; no campaign,
    world or character content is touched. get_active() can write once, running
    the same llm_connections migration the app runs at startup on a library that
    predates connections; nothing else here writes to the real store.
    """
    from grimoire.store import llm_connections

    conn = llm_connections.get_active()
    if conn is None:
        raise RuntimeError(
            "no active LLM connection: pick one on the Configuration page first")
    if conn["kind"] != "claude" and not conn["api_key"]:
        raise RuntimeError(f"connection {conn['id']!r} has no API key set")
    return conn


def live(case: Case, conn: dict, record: bool = False) -> Result:
    """One real generation for `case`, scored against the baseline expectation
    (live output must PASS). With `record`, the reply replaces the baseline
    recording — counterexample variants are never overwritten."""
    from grimoire.llm import LLMClient, LLMError

    ctx = prepare(case)

    async def run() -> str:
        client = LLMClient()
        try:
            return await client.complete(ctx["messages"], conn)
        finally:
            await client.aclose()

    try:
        output = asyncio.run(run())
    except LLMError as exc:
        return Result(case, BASELINE, [], "", f"{exc.kind}: {exc.detail}")

    result = Result(case, BASELINE, list(case.grade(ctx, output)), output)
    if record:
        path = case.baseline.path(case.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")
    return result


def live_all(cases: tuple[Case, ...], conn: dict, isolate, record: bool = False) -> list[Result]:
    out = []
    for case in cases:
        with isolate():
            out.append(live(case, conn, record=record))
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
        lines.append(f"  [{status}] {r.case.id}.{r.variant}")
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
