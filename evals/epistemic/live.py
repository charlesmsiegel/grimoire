"""The live leakage arm (spec 11 §8.2, roadmap 11-S5): each asked NPC's reply.

Every check in `cases.py` that carries an `ask` becomes one ordinary eval
case, run through `evals.runner.live` -- the same metering, real-home
tripwire, ledger harvest and three money columns every live case has -- in
a throwaway home of its own, so no two checks share a ledger. The case plants
its scenes, composes the NPC's prompt from the view built for it (as play
composes: the model's own name, the question as Seraphine's line after the
history), and the reply is graded on planted tokens alone.

What the lines mean. 11-S3 keeps every forbidden needle out of an NPC's
prompt, and the offline suite proves it, so a reply can only leak a token its
prompt held. The **reply-leak line** is therefore over *exposed* replies: a
prompt that holds a `live_forbidden` token (case 3's whisper, which Mara's
prompt carries because she was present and which she could not have heard).
A forbidden token in a reply whose prompt never held it is the harness's
fault, reported apart. **Recall** is reported apart from both and is never a
check: a configuration that recalls more and leaks more is a regression on
the leakage line whatever recall says. A paraphrase is invisible to a token
check; 11-S6's decide judge is what will see one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from evals import runner as reports
from evals.cases import BASELINE, Case, Recording
from evals.graders import Check
from grimoire.store.context import assemble

from . import cases, graders, synth
from . import runner as offline

#: The label the question rides under (`compose_turn(appended=)`): it is
#: what the player says to the NPC, after the history, as a turn's post is.
QUESTION = "Question"


@dataclass(frozen=True)
class Asked:
    """One live check: an offline case and which of its checks is asked."""
    case: cases.Case
    index: int
    reader: str

    @property
    def id(self) -> str:
        return f"epistemic-{self.case.id}-{self.index}"


def asked() -> tuple[Asked, ...]:
    """Every check with an `ask`, found by planting each case once in a
    throwaway home (a check names its reader by the refs the plant mints)."""
    out = []
    for case in cases.CASES:
        with offline._home():
            _items, checks = case.build(offline._cast())
        out.extend(Asked(case, i, c.reader) for i, c in enumerate(checks)
                   if c.ask and c.reader is not None)
    return tuple(out)


def _facts(check: cases.Check, messages) -> dict:
    """What grading needs from the prompt actually sent."""
    held = graders.tokens("\n".join(str(m.get("content") or "") for m in messages))
    forbidden = graders.needles(check.forbidden)
    live_forbidden = graders.needles(check.live_forbidden)
    return {
        "held": held,
        "prompt_leaks": [n for n in forbidden if n in held],
        "exposed": [n for n in live_forbidden if n in held],
        "unseen": [n for n in (*forbidden, *live_forbidden) if n not in held],
        "allowed": [n for n in graders.needles(check.allowed) if n not in live_forbidden],
    }


def as_case(item: Asked, model: str, facts: dict[str, dict]) -> Case:
    """`item` as an eval case: built in the active (throwaway) home, prompted
    with the NPC's own composition, graded on tokens. `facts` is filled with
    the prompt's facts under the case id, for the leakage summary."""

    def build() -> dict:
        cast = offline._cast()
        items, checks = item.case.build(cast)
        offline._seat(cast)
        check = checks[item.index]
        view = synth.for_compose(cast.now.cid, cast.now.sid, check.reader, items,
                                 ceiling=6000)
        messages, _detail = assemble.compose_turn(
            cast.now.cid, cast.now.sid, actor_ref=check.reader, history_view=view,
            model=model, describe=False, appended=((QUESTION, "user", check.ask),))
        facts[item.id] = _facts(check, messages)
        return {"composed": messages, "check": check, "facts": facts[item.id]}

    def grade(ctx: dict, output: str) -> list[Check]:
        found = ctx["facts"]
        out = [Check("epistemic.prompt_clean", not found["prompt_leaks"],
                     "prompt held " + ", ".join(found["prompt_leaks"])),
               Check("epistemic.answered", bool(output.strip()), "empty reply")]
        if found["exposed"]:
            leaked = graders.reply_leaks(output, found["exposed"])
            out.append(Check("epistemic.reply_leak", not leaked,
                             "reply repeated " + ", ".join(leaked)))
        stray = graders.contamination(output, found["unseen"])
        out.append(Check("epistemic.harness", not stray,
                         "reply held a token its prompt never did: " + ", ".join(stray)))
        return out

    return Case(id=item.id, hypothesis=f"{item.case.label}, asked of its NPC",
                build=build, prompt=lambda ctx: list(ctx["composed"]), grade=grade,
                recordings=(Recording(BASELINE),), task="chat")


def record(item: Asked, result: reports.Result, found: dict | None) -> dict:
    """The leakage record of one reply: what the summary and the run file
    carry. No reply text: a run file never holds model output."""
    answered = not result.error and bool(result.output.strip()) and found is not None
    found = found or {"exposed": [], "unseen": [], "allowed": [], "prompt_leaks": []}
    return {
        "case": item.case.id, "check": item.index, "reader": item.reader,
        "repeat": result.repeat, "answered": answered,
        "error": result.error_kind or ("error" if result.error else ""),
        "prompt_leaks": list(found["prompt_leaks"]),
        "exposed": list(found["exposed"]),
        "leaks": graders.reply_leaks(result.output, found["exposed"]) if answered else [],
        "contamination": graders.contamination(result.output, found["unseen"])
        if answered else [],
        "recall": graders.recall(result.output, found["allowed"]) if answered else None,
    }


def run(target, *, client=None, real_home=None, rates=None, run_id: str = "",
        run_day: str = "", repeat: int = 1,
        live: Callable = reports.live) -> tuple[list[reports.Result], list[dict]]:
    """Every asked check, `repeat` times, each in its own throwaway home.
    `target` is `chat`'s resolution, read from the real store before any
    home was repointed (`runner.resolve_connections`); `real_home` arms the
    tripwire, checked before each case is built and again before its
    harvest; `rates` (read in the real store) price the modelled column."""
    model = target.chain.primary.model
    facts: dict[str, dict] = {}
    results, records = [], []
    for item in asked():
        case = as_case(item, model, facts)
        for n in range(repeat):
            facts.pop(item.id, None)
            with offline._home():
                result = live(case, target, client=client, real_home=real_home,
                              run_id=run_id, run_day=run_day, rates=rates)
            result = replace(result, configs=("c1",), repeat=n)
            results.append(result)
            records.append(record(item, result, facts.get(item.id)))
    return results, records


def summary(records: list[dict], offline_records: list[dict] | None = None) -> str:
    """The leakage lines, each with its n, apart from recall."""
    lines = []
    if offline_records is not None:
        bad = [r for r in offline_records if not offline.passed(r)]
        lines.append(f"prompt leaks (offline suite): {len(bad)} of {len(offline_records)} "
                     "reader prompts")
    if records:
        answered = [r for r in records if r["answered"]]
        exposed = [r for r in answered if r["exposed"]]
        leaked = [r for r in exposed if r["leaks"]]
        lines.append(f"answered: {len(answered)} of {len(records)} replies")
        lines.append(f"prompt leaks (live prompts): "
                     f"{sum(bool(r['prompt_leaks']) for r in records)} of {len(records)}")
        lines.append(f"reply leaks: {len(leaked)} of {len(exposed)} exposed replies"
                     + "".join(f"\n  {r['case']}/{r['check']} #{r['repeat'] + 1}: "
                               f"{', '.join(r['leaks'])}" for r in leaked))
        stray = [r for r in answered if r["contamination"]]
        if stray:
            lines.append(f"harness faults: {len(stray)} replies held a token their "
                         "prompt never did")
        scored = [r["recall"] for r in answered if r["recall"] is not None]
        lines.append("recall (not a leakage measure): "
                     + (f"{sum(scored) / len(scored):.2f} over {len(scored)} replies"
                        if scored else "nothing to recall"))
    return "\n".join(lines)


def gate(records: list[dict], offline_records: list[dict] | None = None) -> bool:
    """Passes with no prompt leak, every reply answered, no reply leak and
    no harness fault. One sample of a model is one sample: see `--repeat`."""
    clean = all(offline.passed(r) for r in offline_records or ())
    return clean and all(r["answered"] and not r["prompt_leaks"] and not r["leaks"]
                         and not r["contamination"] for r in records)
