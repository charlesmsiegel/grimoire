"""The Decision gate (spec 11 §8.2, roadmap 11-S6): what the Decision stage
answers about the corpus's gated parts, graded against their gold.

Each check in `cases.py` that carries a `gate` is one decide case: planted in
a throwaway home of its own, the actor's view classified as play classifies
it, the gated part found among the parts production would ask about
(`epistemic_items.eligible_for`), and its question built by production's
own builder (`epistemic_items.questions`, then the kit). Live, it runs down
the chain `decide()` sends (`runner.live_all`), metered and harvested like
every live case.

What the lines mean. The **false-`known` line** is the leakage line: a
permissive answer (`known`, `suspected`, `experienced`) where the gold
withholds, counted over the negatives answered. It is reported apart from
accuracy, and its bar is set before any run: **zero, over every repeat**.
`epistemic_decide` stays off until a recorded run meets it (and the play
gate the switch's ratification asks for). An abstention on a positive is a
miss, never a leak.

Escalation is off for the task (it landed so, as every task does), so the
chain the gate runs is the chain production sends.
"""

from __future__ import annotations

from dataclasses import dataclass

from evals import runner as reports
from evals.cases import BASELINE, Case, Recording
from evals.graders import Check
from grimoire import decisions, inference
from grimoire.store import epistemic_access, paths
from grimoire.store.context import epistemic_items

from . import cases, synth
from . import runner as offline

TASK = epistemic_access.TASK


@dataclass(frozen=True)
class Gated:
    case: cases.Case
    index: int
    reader: str
    needle: str
    gold: tuple[str, ...]

    @property
    def id(self) -> str:
        return f"epistemic-gate-{self.case.id}-{self.index}"

    @property
    def negative(self) -> bool:
        return not set(self.gold) & set(cases.PERMISSIVE)


def gated() -> tuple[Gated, ...]:
    """Every check with a `gate`, planted once in a throwaway home to read
    its reader. Refuses to plant in the home that was active on entry."""
    real = paths.home()
    out = []
    for case in cases.CASES:
        with offline._home():
            if reports._is_real_home(real):
                raise RuntimeError(reports.ISOLATE_ERROR)
            _items, checks = case.build(offline._cast())
        out.extend(Gated(case, i, c.reader, c.gate, c.gold)
                   for i, c in enumerate(checks) if c.gate and c.reader is not None)
    return tuple(out)


def part_of(view, needle: str) -> epistemic_items.Part:
    """The one eligible part holding `needle`; a gate case whose part
    production would not ask about tests nothing, so that is an error."""
    found = [p for p in epistemic_items.eligible_for(view) if needle in p.text]
    if len(found) != 1:
        raise ValueError(f"{needle}: {len(found)} eligible parts hold it, not one")
    return found[0]


def plant(item: Gated) -> dict:
    """`item` planted in the active (throwaway) home: its part and question."""
    cast = offline._cast()
    items, _checks = item.case.build(cast)
    offline._seat(cast)
    view = synth.for_compose(cast.now.cid, cast.now.sid, item.reader, items, ceiling=6000)
    part = part_of(view, item.needle)
    name = next(n for n, (aid, _vid) in cast.ids.items() if f"characters:{aid}" == item.reader)
    questions = epistemic_items.questions(
        (part,), actor_ref=item.reader, actor_name=name,
        state=epistemic_items.own_state(cast.now.cid, item.reader))
    return {"part": part, "questions": questions,
            "items": epistemic_access.build_items(questions)}


def answer_of(ctx: dict, output: str) -> str:
    """What production would read off `output`: `access_of`'s class."""
    results = decisions.parse(output, ctx["items"], explain=False)
    decision = decisions.Decision(items=tuple(results), backend="structured")
    [q] = ctx["questions"]
    return epistemic_access.access_of(ctx["questions"], decision)[(q.actor_ref,
                                                                   q.evidence_ref)]


def grade(item: Gated, ctx: dict, output: str) -> list[Check]:
    answer = answer_of(ctx, output)
    leaked = item.negative and answer in cases.PERMISSIVE
    return [Check("gate.false_known", not leaked,
                  f"answered {answer!r} where the gold withholds"),
            Check("gate.gold", answer in item.gold,
                  f"answered {answer!r}; gold is one of {', '.join(item.gold)}")]


def as_case(item: Gated) -> Case:
    def build() -> dict:
        return plant(item)

    def prompt(ctx: dict) -> list[dict]:
        return inference.structured_messages(list(ctx["items"]))

    return Case(id=item.id, hypothesis=f"{item.case.label}: the Decision stage's answer",
                build=build, prompt=prompt, grade=lambda ctx, out: grade(item, ctx, out),
                schema=lambda ctx: decisions.schema(ctx["items"], explain=False),
                recordings=(Recording(BASELINE, ext="json"),), task=TASK)


def record(item: Gated, result: reports.Result) -> dict:
    """One answer's line, read off its result: no reply text."""
    answered = not result.error
    gold = next((c for c in result.checks if c.name == "gate.gold"), None)
    false_known = next((c for c in result.checks if c.name == "gate.false_known"), None)
    return {"case": item.case.id, "needle": item.needle, "repeat": result.repeat,
            "negative": item.negative, "answered": answered,
            "error": result.error_kind or ("error" if result.error else ""),
            "correct": bool(answered and gold is not None and gold.ok),
            "false_known": bool(answered and false_known is not None and not false_known.ok)}


def run(target, *, client=None, real_home=None, rates=None, run_id: str = "",
        run_day: str = "", repeat: int = 1) -> tuple[list[reports.Result], list[dict]]:
    """Every gated part, `repeat` times, each in its own throwaway home.
    `target` is `epistemic-access`'s decide resolution, read from the real
    store before any home moved."""
    real_home = real_home if real_home is not None else paths.home()
    items = gated()
    by_id = {i.id: i for i in items}
    results = reports.live_all(tuple(as_case(i) for i in items),
                               {reports.conn_key(as_case(items[0])): target} if items else {},
                               offline._home, client=client, repeat=repeat,
                               real_home=real_home, run_id=run_id, run_day=run_day,
                               rates=rates)
    return results, [record(by_id[r.case.id], r) for r in results]


def summary(records: list[dict]) -> str:
    """The false-`known` line, with its n, apart from accuracy."""
    if not records:
        return ""
    negatives = [r for r in records if r["negative"] and r["answered"]]
    answered = [r for r in records if r["answered"]]
    lines = [(f"Decision gate: false known: {sum(r['false_known'] for r in negatives)} of "
              f"{len(negatives)} negative answers"
              + "".join(f"\n  {r['case']} #{r['repeat'] + 1}: {r['needle']}"
                        for r in negatives if r["false_known"])),
             (f"Decision gate accuracy (not a leakage measure): "
              f"{sum(r['correct'] for r in answered)} of {len(answered)} answered, "
              f"{len(records) - len(answered)} failed")]
    return "\n".join(lines)


def gate(records: list[dict]) -> bool:
    """Zero false-`known` answers, every gated part answered, and at least
    one negative asked: the bar set before any run (module docstring)."""
    return bool(records) and all(r["answered"] for r in records) \
        and not any(r["false_known"] for r in records) \
        and any(r["negative"] for r in records)
