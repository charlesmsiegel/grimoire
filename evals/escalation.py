"""Threshold tooling for decision escalation (roadmap 01d-S5; spec 01d §6.3).

Two tools, and the policy override both read:

- **The offline margin sweep** (`sweep`, `--escalation-sweep --case ID`):
  a decide case's checked-in native recordings, read through their adapter
  as a live native run reads them (`runner.native_output`'s mapping), then,
  for each threshold of a fixed grid, which items would escalate. It counts
  through `decisions.triggers` itself -- never a bare `margin < m` -- so it
  agrees with the hop at the boundary (`decisions.MASS_TIE`: a report of
  exactly 0.6 computes a margin a hair under 0.2 and must not fire there).
  No call, no store read beyond the case's own fixture; deterministic.
- **The per-run policy override** (`parse`, `--escalation POLICY_JSON`):
  a `routing.TaskPolicy` for the selected decide cases' tasks, for this run
  only (`runner.overriding` patches `routing.TASK_POLICY` around each call
  and puts it back). A live run with it answers each decide case through
  `inference.decide(escalation=runner.escalator(...))`, so the hop runs
  exactly as it would in the app; without it, nothing escalates, which is
  the comparison's other half (`--out` both, then `--compare`).

Nothing here enables a task. A task's thresholds land with its switching
change, and the evidence goes in `evals/README.md`, "Decision escalation".
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from grimoire import adapters, decisions
from grimoire.store import routing

from . import runner
from .cases import Case, Recording

#: The adapter KIND a native recording's bodies come from, by
#: `Recording.native` (`runner.NATIVE_ADAPTERS`' keys): what a margin
#: threshold is keyed by (`TaskPolicy.margins`, spec §6.2: per reporting
#: endpoint kind, never global).
NATIVE_KINDS: dict[str, str] = {"openrouter": "openrouter", "openai": "openai_compatible"}

#: The sweep's thresholds: 0.05 to `routing.MAX_MARGIN` by 0.05 (spec §6.3).
GRID: tuple[float, ...] = tuple(round(0.05 * n, 2) for n in range(1, 11))

#: The fields `--escalation` may set: the escalation half of a policy. The
#: fallback and 01c's sampling fields are not an escalation's to override.
FIELDS = ("escalate_to", "escalate_on", "question", "margins", "escalate_max",
          "escalate_answers", "reads_declines")


class PolicyError(ValueError):
    """An `--escalation` policy this run refuses, in one sentence."""


def decide_task(case: Case) -> str | None:
    """Why `case` cannot take an escalation policy, or None when it can: it
    must be a decide case (a schema) whose task is on a decide route."""
    route = routing.route(case.task)
    if case.schema is None or route is None or route.operation != "decide":
        return (f"{case.id}: its task {case.task!r} is not on a decide route, "
                f"so nothing it asks can escalate")
    return None


def parse(text: str, task: str, *, sweep: bool = False) -> routing.TaskPolicy:
    """`text` (a JSON object of `FIELDS`) as `task`'s policy for one run, or
    `PolicyError`. Held to the rules `backend/tests/test_task_policy.py`
    holds the code table to that a JSON value can break: a task on a decide
    route; a role in `routing.ESCALATION_ROLES` (never `CALLER` -- an eval
    has no resolver to hand over) that is not the route's own default; a
    non-empty `escalate_on` of known triggers and a `question`; a margin
    per native kind in `(0, MAX_MARGIN]`, required beside `low_margin`; an
    answer filter only beside `low_margin`; `1 <= escalate_max <= 8`. With
    `sweep`, only what the sweep reads is required (`question`,
    `escalate_on`): the grid stands in for `margins`, and no hop runs."""
    route = routing.route(task)
    if route is None or route.operation != "decide":
        raise PolicyError(f"{task!r} is not on a decide route, so it cannot escalate")
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise PolicyError(f"--escalation is not JSON ({exc})") from exc
    if not isinstance(raw, dict):
        raise PolicyError("--escalation takes a JSON object")
    unknown = sorted(set(raw) - set(FIELDS))
    if unknown:
        raise PolicyError(f"--escalation sets only {', '.join(FIELDS)}; not {', '.join(unknown)}")
    policy = routing.TaskPolicy(
        escalate_to=_text(raw, "escalate_to"),
        escalate_on=_texts(raw, "escalate_on"),
        question=_text(raw, "question"),
        margins=_margins(raw.get("margins", {})),
        escalate_max=_count(raw.get("escalate_max", decisions.MAX_ITEMS_PER_CALL)),
        escalate_answers=_texts(raw, "escalate_answers"),
        reads_declines=_flag(raw.get("reads_declines", False)))
    _check(policy, route, sweep=sweep)
    return policy


def _text(raw: dict, key: str) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str):
        raise PolicyError(f"--escalation's {key} is a string")
    return value


def _texts(raw: dict, key: str) -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PolicyError(f"--escalation's {key} is a list of strings")
    return tuple(value)


def _margins(value: Any) -> tuple[tuple[str, float], ...]:
    if not isinstance(value, dict) or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in value.values()):
        raise PolicyError("--escalation's margins map a native kind to a number")
    return tuple(sorted((str(k), float(v)) for k, v in value.items()))


def _count(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PolicyError("--escalation's escalate_max is a whole number")
    return value


def _flag(value: Any) -> bool:
    if not isinstance(value, bool):
        raise PolicyError("--escalation's reads_declines is true or false")
    return value


def _check(policy: routing.TaskPolicy, route: routing.Route, *, sweep: bool) -> None:
    """`parse`'s rules over a built policy (see there)."""
    if not sweep:
        _check_role(policy, route)
    if not policy.escalate_on or not set(policy.escalate_on) <= set(decisions.TRIGGERS):
        raise PolicyError(f"--escalation's escalate_on is some of "
                          f"{', '.join(decisions.TRIGGERS)}")
    if not policy.question:
        raise PolicyError("--escalation names the deciding question")
    _check_margins(policy, sweep=sweep)
    if not 1 <= policy.escalate_max <= decisions.MAX_ITEMS_PER_CALL:
        raise PolicyError(f"escalate_max is 1 to {decisions.MAX_ITEMS_PER_CALL}")
    # What `decide` itself asks before any meter opens.
    try:
        decisions.triggers((), question=policy.question, escalate_on=policy.escalate_on,
                           margins=dict(policy.margins), answers=policy.escalate_answers)
    except ValueError as exc:
        raise PolicyError(f"--escalation: {exc}") from exc


def _check_role(policy: routing.TaskPolicy, route: routing.Route) -> None:
    """A role the hop can resolve (never `CALLER`: an eval has no resolver),
    and not the one the route already runs on."""
    if policy.escalate_to not in routing.ESCALATION_ROLES:
        raise PolicyError(f"--escalation's escalate_to is one of "
                          f"{', '.join(routing.ESCALATION_ROLES)}")
    if policy.escalate_to == route.default_role:
        raise PolicyError(f"the {route.key} route already runs on the "
                          f"{policy.escalate_to} role; a hop there is the same model")


def _check_margins(policy: routing.TaskPolicy, *, sweep: bool) -> None:
    """A margin per native kind in `(0, MAX_MARGIN]`, required beside
    `low_margin` (except on a sweep, whose grid stands in); an answer filter
    only beside `low_margin`."""
    low = "low_margin" in policy.escalate_on
    if low and not policy.margins and not sweep:
        raise PolicyError("low_margin needs a margin per native kind")
    for kind, margin in policy.margins:
        if not adapters.decides_natively(kind):
            raise PolicyError(f"{kind!r} has no native decisions endpoint to read a margin from")
        if not 0 < margin <= routing.MAX_MARGIN:
            raise PolicyError(f"a margin is in (0, {routing.MAX_MARGIN}]; {kind} has {margin}")
    if policy.escalate_answers and not low:
        raise PolicyError("escalate_answers narrows low_margin only")


# ---- the offline sweep ----

def default_policy(case: Case, items: tuple[decisions.Item, ...]) -> routing.TaskPolicy:
    """What the sweep reads when no `--escalation` is given: the task's own
    policy where it names a question, else the case's first question, with
    every trigger and no answer filter."""
    own = routing.policy(case.task)
    question = own.question or (items[0].questions[0].id if items else "")
    return routing.TaskPolicy(escalate_on=own.escalate_on or decisions.TRIGGERS,
                              question=question, escalate_answers=own.escalate_answers)


def sweep(case: Case, policy: routing.TaskPolicy | None = None) -> str:
    """The sweep's text for `case`: per native recording, each item's
    deciding answer and margin, then per threshold of `GRID` which items
    `decisions.triggers` escalates, in its priority order. Plain ASCII;
    the caller isolates the store (the fixture builds in it)."""
    ctx = runner.prepare(case)
    items = tuple(ctx["items"])
    chosen = policy or default_policy(case, items)
    lines = [f"escalation sweep: {case.id} (task {case.task}, "
             f"question {chosen.question!r}, triggers {', '.join(chosen.escalate_on)}"
             + (f", answers {', '.join(chosen.escalate_answers)}"
                if chosen.escalate_answers else "") + ")"]
    natives = [r for r in case.recordings if r.native]
    if not natives:
        lines.append("  no native recording: a structured reply reports no margin")
        return "\n".join(lines)
    for recording in natives:
        kind = NATIVE_KINDS[recording.native]
        results = _results(case, recording, items, kind)
        lines.append(f"  {recording.variant} ({kind}): {len(results)} item(s)")
        lines.append("    item  answer                          margin")
        for index, result in enumerate(results):
            answer = result.answers.get(chosen.question)
            key = (decisions.answer_key(answer) if answer is not None else None) or (
                answer.reason if answer is not None and answer.reason else "-")
            margin = decisions.margin(answer) if answer is not None else None
            shown = f"{margin:+.3f}" if margin is not None else "-"
            lines.append(f"    {index:<4}  {key:<30}  {shown}")
        lines.append("    threshold  escalates")
        for threshold in GRID:
            found = decisions.triggers(results, question=chosen.question,
                                       escalate_on=chosen.escalate_on,
                                       margins={kind: threshold},
                                       answers=chosen.escalate_answers)
            named = ", ".join(f"{t.index} ({t.trigger})" for t in found) or "-"
            lines.append(f"    {threshold:<9.2f}  {len(found)}/{len(results)}: {named}")
    return "\n".join(lines)


def _results(case: Case, recording: Recording, items: tuple[decisions.Item, ...],
             kind: str) -> tuple[decisions.ItemResult, ...]:
    """A native recording's items, read through its adapter (the production
    mapping, as `runner.native_output` reads them) and stamped with the
    server kind the triggers key a threshold by (`ItemResult.served`)."""
    bodies = json.loads(recording.path(case.id).read_text(encoding="utf-8"))
    adapter = runner.NATIVE_ADAPTERS[recording.native]
    return tuple(replace(adapter.decision_result(body, item), backend=decisions.NATIVE_BACKEND,
                         served=(kind, "", str(body.get("model", ""))))
                 for body, item in zip(bodies, items, strict=True))
