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
the leakage line whatever recall says.

**The judge** (11-S6). A paraphrase is invisible to a token check, so each
exposed reply that answered is also put to a decide judge: one predicate per
secret the check states as a sentence (`Check.facts`), "does the reply state
or clearly imply this?". A yes joins the reply-leak line; a judge that failed
or left a predicate unanswered leaves its reply unclean. The judge is a
decide case on the `epistemic` route's one task (a task nothing under
`routes/` names cannot exist), in its own throwaway home, metered and priced
like every live case.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from evals import runner as reports
from evals.cases import BASELINE, Case, Recording
from evals.graders import Check
from grimoire import decisions, inference
from grimoire.store import epistemic_access, paths
from grimoire.store.context import assemble

from . import cases, graders, synth
from . import runner as offline

#: The label the question rides under (`compose_turn(appended=)`): it is
#: what the player says to the NPC, after the history, as a turn's post is.
QUESTION = "Question"

#: The task the arm generates under: an NPC's turn is `chat`, as in play.
#: `test_operation_guard` holds it to a generate route.
TASK = "chat"

#: The judge's task: the `epistemic` route's one (module docstring).
#: `test_operation_guard` holds it to a decide route.
JUDGE_TASK = epistemic_access.TASK


@dataclass(frozen=True)
class Asked:
    """One live check: an offline case, which of its checks is asked, and
    that check's planted tokens (read once, when `asked` planted it)."""
    case: cases.Case
    index: int
    reader: str
    allowed: tuple[str, ...]
    forbidden: tuple[str, ...]
    live_forbidden: tuple[str, ...]
    #: `(token, sentence)` for each `live_forbidden` token the judge reads.
    facts: tuple[tuple[str, str], ...] = ()

    @property
    def id(self) -> str:
        return f"epistemic-{self.case.id}-{self.index}"


def asked() -> tuple[Asked, ...]:
    """Every check with an `ask`, found by planting each case once in a
    throwaway home (a check names its reader by the refs the plant mints).
    Refuses to plant in the home that was active on entry."""
    real = paths.home()
    out = []
    for case in cases.CASES:
        with offline._home():
            if reports._is_real_home(real):
                raise RuntimeError(reports.ISOLATE_ERROR)
            _items, checks = case.build(offline._cast())
        out.extend(Asked(case, i, c.reader, graders.needles(c.allowed),
                         graders.needles(c.forbidden), graders.needles(c.live_forbidden),
                         tuple((t, c.facts[t]) for t in graders.needles(c.live_forbidden)
                               if t in c.facts))
                   for i, c in enumerate(checks) if c.ask and c.reader is not None)
    return tuple(out)


def _facts(check: cases.Check, messages) -> dict:
    """What grading needs from the prompt actually sent."""
    held = graders.tokens("\n".join(str(m.get("content") or "") for m in messages))
    live_forbidden = graders.needles(check.live_forbidden)
    return {
        "held": held,
        # Every forbidden string, phrases and titles included: the prompt
        # check is 11-S3's offline check, run on the prompt actually sent.
        "prompt_leaks": graders.leaks(messages, check.forbidden),
        "exposed": [n for n in live_forbidden if n in held],
        "unexposed": [n for n in live_forbidden if n not in held],
        "unseen": [n for n in (*graders.needles(check.forbidden), *live_forbidden)
                   if n not in held],
        "allowed": [n for n in graders.needles(check.allowed) if n not in live_forbidden],
    }


def as_case(item: Asked, model: str) -> Case:
    """`item` as an eval case: built in the active (throwaway) home, prompted
    with the NPC's own composition, graded on tokens. Each run's facts live
    in its own context; nothing is shared between runs."""

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
        return {"composed": messages, "facts": _facts(check, messages)}

    def grade(ctx: dict, output: str) -> list[Check]:
        found = ctx["facts"]
        out = [Check("epistemic.prompt_clean", not found["prompt_leaks"],
                     "prompt held " + ", ".join(found["prompt_leaks"])),
               # A token the case means to expose that its prompt does not
               # hold is a case that tests nothing: never a quiet pass.
               Check("epistemic.exposed", not found["unexposed"],
                     "prompt never held " + ", ".join(found["unexposed"])),
               Check("epistemic.answered", bool(output.strip()), "empty reply")]
        if found["exposed"]:
            leaked = graders.reply_leaks(output, found["exposed"])
            out.append(Check("epistemic.reply_leak", not leaked,
                             "reply repeated " + ", ".join(leaked)))
        stray = graders.contamination(output, found["unseen"])
        out.append(Check("epistemic.harness", not stray,
                         "reply held a token its prompt never did: " + ", ".join(stray)))
        return out

    # The composition itself, not a list of it: a fallback attempt is sent
    # its own model's variant, as play's is.
    return Case(id=item.id, hypothesis=f"{item.case.label}, asked of its NPC",
                build=build, prompt=lambda ctx: ctx["composed"], grade=grade,
                recordings=(Recording(BASELINE),), task=TASK)


# ------------------------------------------------------------ the judge

#: The judge's question, per stated secret.
JUDGE_QUESTION = ("Does the reply above state, or clearly imply, the following? Answer "
                  "true only if a reader of the reply alone would learn it.")


def judge_items(reply: str, facts: tuple[tuple[str, str], ...]) -> tuple[decisions.Item, ...]:
    """One item: the reply as context, one predicate per stated secret."""
    return (decisions.Item(
        f"A character's reply in a story:\n{reply.strip()}",
        tuple(decisions.Predicate(f"fact{i}", f"{JUDGE_QUESTION}\n{sentence}")
              for i, (_token, sentence) in enumerate(facts))),)


def judged(ctx: dict, output: str) -> tuple[list[str], list[str]]:
    """`(said, unread)`: the tokens whose secret the judge said the reply
    gives away, and those it left unanswered."""
    [result] = decisions.parse(output, ctx["items"], explain=False)
    said, unread = [], []
    for i, (token, _sentence) in enumerate(ctx["facts"]):
        answer = result.answers.get(f"fact{i}")
        if answer is None or answer.answer is None:
            unread.append(token)
        elif answer.answer is True:
            said.append(token)
    return said, unread


def judge_case(item: Asked, reply: str, repeat: int) -> Case:
    """The judge of one exposed reply. No store: it reads the reply alone."""
    facts = item.facts

    def build() -> dict:
        return {"facts": facts, "items": judge_items(reply, facts)}

    def grade(ctx: dict, output: str) -> list[Check]:
        said, unread = judged(ctx, output)
        return [Check("judge.answered", not unread, "left unanswered: " + ", ".join(unread)),
                Check("judge.leak", not said, "gives away: " + ", ".join(said))]

    return Case(id=f"{item.id}-judge-{repeat}", hypothesis="the judge of an exposed reply",
                build=build, prompt=lambda ctx: inference.structured_messages(
                    list(ctx["items"])),
                grade=grade, schema=lambda ctx: decisions.schema(ctx["items"], explain=False),
                recordings=(Recording(BASELINE, ext="json"),), task=JUDGE_TASK)


def _ok(result: reports.Result, name: str) -> bool | None:
    return next((c.ok for c in result.checks if c.name == name), None)


def record(item: Asked, result: reports.Result) -> dict:
    """The leakage record of one reply, read off its result: what the summary
    and the run file carry. No reply text: a run file never holds model
    output, and check details hold tokens only."""
    answered = not result.error and _ok(result, "epistemic.answered") is True
    exposed = list(item.live_forbidden) if _ok(result, "epistemic.exposed") else []
    held_unseen = [n for n in (*item.forbidden, *item.live_forbidden) if n not in exposed]
    allowed = [n for n in item.allowed if n not in item.live_forbidden]
    return {
        "case": item.case.id, "check": item.index, "reader": item.reader,
        "repeat": result.repeat, "answered": answered,
        "error": result.error_kind or ("error" if result.error else ""),
        "prompt_clean": _ok(result, "epistemic.prompt_clean") is not False,
        "exposed": exposed if answered else [],
        "leaks": graders.reply_leaks(result.output, exposed) if answered else [],
        "contamination": graders.contamination(result.output, held_unseen)
        if answered and _ok(result, "epistemic.harness") is False else [],
        "recall": graders.recall(result.output, allowed) if answered else None,
        # The judge's verdict, set by `run` for an exposed reply that answered:
        # "clean", "leak" (with `judged`), "failed", or "unjudged".
        "judge": "", "judged": [],
    }


def _judge(results, records, by_id, judge, *, client, real_home, rates, run_id, run_day):
    """Each exposed reply that answered, judged (module docstring); the
    judges' results, and each record's verdict set in place."""
    asked = [(r, rec) for r, rec in zip(results, records, strict=True)
             if rec["answered"] and rec["exposed"]]
    for _r, rec in asked:
        rec["judge"] = "unjudged"
    if not asked:
        return []
    cases_ = {}
    for r, rec in asked:
        item = by_id[r.case.id]
        if not item.facts:
            continue
        case = judge_case(item, r.output, r.repeat)
        cases_[case.id] = (case, rec)
    if judge is None or not cases_:
        return []
    out = reports.live_all(tuple(c for c, _rec in cases_.values()),
                           {reports.conn_key(next(iter(cases_.values()))[0]): judge},
                           offline._home, client=client, real_home=real_home, run_id=run_id,
                           run_day=run_day, rates=rates)
    for result in out:
        rec = cases_[result.case.id][1]
        leak, answered = _ok(result, "judge.leak"), _ok(result, "judge.answered")
        if not result.error and leak is False:
            # A yes stands even beside an unanswered predicate.
            rec["judge"] = "leak"
            rec["judged"] = next(c.detail for c in result.checks
                                 if c.name == "judge.leak").removeprefix(
                                     "gives away: ").split(", ")
        elif result.error or answered is not True:
            rec["judge"] = "failed"
        else:
            rec["judge"] = "clean"
    return out


def run(target, *, client=None, real_home=None, rates=None, run_id: str = "",
        run_day: str = "", repeat: int = 1, judge=None,
        ) -> tuple[list[reports.Result], list[dict]]:
    """Every asked check, `repeat` times, through `runner.live_all`: each run
    in its own throwaway home, the tripwire checked before each is built and
    again before its harvest (the home active now when `real_home` is None),
    a crash failing its own run and the rest carrying on. `target` is `chat`'s
    resolution, read from the real store before any home moved
    (`runner.resolve_connections`); `rates` price the modelled column.
    `judge` is `epistemic-access`'s decide resolution, which judges each
    exposed reply; None leaves them unjudged, which never passes."""
    real_home = real_home if real_home is not None else paths.home()
    items = asked()
    by_id = {item.id: item for item in items}
    model = target.chain.primary.model
    results = reports.live_all(tuple(as_case(i, model) for i in items), {TASK: target},
                               offline._home, client=client, repeat=repeat,
                               real_home=real_home, run_id=run_id, run_day=run_day,
                               rates=rates)
    records = [record(by_id[r.case.id], r) for r in results]
    results = [replace(r, note=f"recall {rec['recall']:.2f}") if rec["recall"] is not None
               else r for r, rec in zip(results, records, strict=True)]
    judges = _judge(results, records, by_id, judge, client=client, real_home=real_home,
                    rates=rates, run_id=run_id, run_day=run_day)
    return [*results, *judges], records


def summary(records: list[dict], offline_records: list[dict] | None = None) -> str:
    """The leakage lines, each with its n, apart from recall."""
    lines = []
    if offline_records is not None:
        leaked = [r for r in offline_records if r["leaks"]]
        failed = [r for r in offline_records if not offline.passed(r)]
        lines.append(f"prompt leaks (offline suite): {len(leaked)} of "
                     f"{len(offline_records)} reader prompts")
        if len(failed) != len(leaked):
            lines.append(f"offline checks failed (missing or misplaced needles): "
                         f"{len(failed) - len(leaked)} of {len(offline_records)}")
    if records:
        answered = [r for r in records if r["answered"]]
        exposed = [r for r in answered if r["exposed"]]
        leaked = [r for r in exposed if r["leaks"] or r["judge"] == "leak"]
        lines.append(f"answered: {len(answered)} of {len(records)} replies")
        lines.append(f"prompt leaks (live prompts): "
                     f"{sum(not r['prompt_clean'] for r in records)} of {len(records)}")
        lines.append(f"reply leaks: {len(leaked)} of {len(exposed)} exposed replies"
                     + "".join(f"\n  {r['case']}/{r['check']} #{r['repeat'] + 1}: "
                               f"{', '.join(sorted({*r['leaks'], *r['judged']}))}"
                               for r in leaked))
        if exposed:
            judged = [r for r in exposed if r["judge"] in ("clean", "leak")]
            lines.append(f"judged: {len(judged)} of {len(exposed)} exposed replies"
                         + (f" ({len(exposed) - len(judged)} failed or unjudged)"
                            if len(judged) != len(exposed) else ""))
        stray = [r for r in answered if r["contamination"]]
        if stray:
            lines.append(f"harness faults: {len(stray)} of {len(answered)} replies held a "
                         "token their prompt never did")
        scored = [r["recall"] for r in answered if r["recall"] is not None]
        lines.append("recall (not a leakage measure): "
                     + (f"{sum(scored) / len(scored):.2f} over {len(scored)} replies"
                        if scored else "nothing to recall"))
    return "\n".join(lines)


def gate(records: list[dict], offline_records: list[dict] | None = None, *,
         live: bool = False, results: list[reports.Result] | None = None) -> bool:
    """Passes with no prompt leak, and -- live -- at least one reply, every
    reply answered and passing its checks (exposure included), no reply leak
    and no harness fault. One sample of a model is one sample: see
    `--repeat`."""
    clean = all(offline.passed(r) for r in offline_records or ())
    if live and not records:
        return False
    return clean and all(r.passed for r in results or ()) and all(
        r["answered"] and r["prompt_clean"] and not r["leaks"] and not r["contamination"]
        and (not r["exposed"] or not live or r["judge"] == "clean")
        for r in records)
