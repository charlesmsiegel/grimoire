"""The investigation gate's arms (spec 10.1; 12-S7), offline and live.

Theory: an arm is a production path run against a planted campaign in a
throwaway home, its rows harvested and folded into the three money columns
(01a-C1), never added. The investigation arm is `routes.investigation
.investigate` itself; the incumbents are the paths it would replace --
E2's reconcile sweep answer, E3's one retrieval. Offline, the model is a
canned script (`cases.Case.script`), so the arm proves the loop, the checks
and the graders, never a model's quality; `--live` runs the real models in
the same isolation (01a-C2's tripwire refuses the real home).

The offline model is the suite's own scripted tool fake,
`backend/tests/llm_fakes.FakeToolTurns` (AGENTS.md: LLM behaviour comes from
`llm_fakes`, never an inline fake) -- the one eval that imports it, so
`evals/run.py` puts `backend/` on the path for it. The investigation arm
runs as the entry points run it (`investigation.investigate_with_decide`):
with the decide tool and its capture, and live under the budget's own wall.

The gate (spec 10.4) is per entry point: the investigation arm beats its
incumbent on correctness by `cases.MARGIN`, and neither leakage line is
worse. Offline it is reported, never a reason to change a default: turning
a switch on is a later PR that attaches a passing LIVE table
(`evals/README.md`, "Investigation").
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import replace
from pathlib import Path

from evals import costs
from evals import runner as reports
from evals.cases import Case as ReportCase
from evals.graders import Check
from grimoire import inference, schemas, tool_calls, wire
from grimoire.llm import LLMClient
from grimoire.routes import investigation
from grimoire.routes.common import UsableInference
from grimoire.store import routing, usage
from grimoire.store.continuity import candidates, reconcile
from grimoire.store.inference.resolved import Attempt
from grimoire.store.investigation import budget as inv_budget
from grimoire.store.investigation import state as inv_state
from grimoire.store.investigation import tools as inv_tools
from tests.llm_fakes import FakeToolTurns

from . import cases, graders, synth

#: The task each mode's loop runs under.
TASKS = {"continuity": "investigation-continuity", "question": "investigation-question"}
#: The task the investigation arm's decide tool runs under.
DECIDE_TASK = "investigation-decide"
#: A word that appears in no corpus campaign: the hidden route's baseline.
NOWHERE = "zephyrquill"


@contextlib.contextmanager
def isolated():
    previous = os.environ.get("GRIMOIRE_HOME")
    with tempfile.TemporaryDirectory(prefix="grimoire-investigation-eval-") as home:
        os.environ["GRIMOIRE_HOME"] = home
        try:
            yield Path(home)
        finally:
            if previous is None:
                os.environ.pop("GRIMOIRE_HOME", None)
            else:
                os.environ["GRIMOIRE_HOME"] = previous


# ---- the offline model ----
#: The target an offline arm's resolution names: no request leaves the
#: process, so it carries no key worth anything.
OFFLINE_TARGET = wire.Target(provider_id="offline", kind="openrouter", model="script/canned",
                             provider_name="Offline script", api_key="offline",
                             requested_model="script/canned",
                             account=wire.Account(billing="metered"))


def offline_resolution(task: str, operation: str = "generate") -> UsableInference:
    """A resolution of `task` whose one attempt is `OFFLINE_TARGET`."""
    route = routing.route(task)
    return UsableInference(task=task, operation=operation, route=route.key if route else "",
                           role="", via="", scope="none",
                           attempts=(Attempt(OFFLINE_TARGET.provider_id, OFFLINE_TARGET.model,
                                             "", target=OFFLINE_TARGET),),
                           rides=False)


# ---- the arms ----
def _seed(case: cases.Case, planted: synth.Planted) -> investigation.Seed:
    if case.mode == "continuity":
        return investigation.continuity_seed(planted.cid, planted.candidate)
    return investigation.question_seed(planted.cid, case.question)


async def _investigate(case, planted, *, client, resolved, run_id, live, decider) -> dict:
    """The entry point's own run: its decide tool on `decider` (the real
    store's resolution live, the offline target offline), and its budget as
    the route reads it -- with no wall offline only, where a canned script
    answers at once and the isolated home's call budget means nothing."""
    seed = await asyncio.to_thread(_seed, case, planted)
    run_state = inv_state.RunState(cid=planted.cid, mode=case.mode)
    budget = inv_budget.for_entry(case.mode)
    if not live:
        budget = replace(budget, wall_seconds=0)
    outcome = await investigation.investigate_with_decide(
        None, state=run_state, task=TASKS[case.mode], seed=seed, client=client,
        resolved=resolved, budget=budget, run_id=run_id, decide_resolution=lambda: decider)
    return outcome.as_dict()


async def _sweep(case, planted, *, client, resolved, run_id, live, decider) -> dict:
    """E2's incumbent: the sweep's own answer for the one finding -- offline
    (no model) none, which is an abstention; live, the decide call the sweep
    makes, read back by its own `proposals_of`."""
    if client is None or resolved is None:
        return {"state": "completed", "result": None, "trace": {}}
    record = candidates.read(planted.cid)["records"][planted.candidate]
    payload = await asyncio.to_thread(reconcile.build_payload, planted.cid, [{
        "id": planted.candidate, "kind": record["kind"], "refs": record["refs"],
        "signals": record["signals"], "fingerprint": record["fingerprint"]}])
    items = await asyncio.to_thread(reconcile.build_items, payload)
    decision = await inference.decide("continuity-reconcile", items, client=client,
                                      resolved=resolved, campaign=planted.cid, run_id=run_id)
    found = reconcile.proposals_of(payload, decision.items) or {}
    return {"state": "completed", "result": found.get(planted.candidate), "trace": {}}


#: E3's incumbent answers in this shape, citing from what it was shown.
INCUMBENT_SYSTEM = (
    "Answer the reader's question from the history below alone; say so if it does not "
    "answer it. Each item is headed by its ref in brackets. Reply with one JSON object "
    'and nothing else: {"text": "<the answer>", "citations": ["<the refs of the items '
    'that support it>"]}.')


def _incumbent_answer(text: str, shown: list[str]) -> dict | None:
    """The incumbent's reply read as an answer, its citations held to the
    refs it was shown -- the rule `final.settle` holds the investigation
    arm's to (what a tool returned), so both arms are graded alike. A reply
    that is not the object is no answer (an abstention)."""
    found = schemas.find_value(text, arrays=False)
    if not isinstance(found, dict) or not isinstance(found.get("text"), str):
        return None
    cited = found.get("citations")
    cited = [c for c in cited if isinstance(c, str) and c in shown] \
        if isinstance(cited, list) else []
    return {"text": found["text"].strip(), "citations": list(dict.fromkeys(cited))}


async def _retrieval(case, planted, *, client, resolved, run_id, live, decider) -> dict:
    """E3's incumbent: one search over the question, nothing read further --
    offline its evidence only; live, one generated answer over it, citing
    the shown items it says support it."""
    run_state = inv_state.RunState(cid=planted.cid, mode="question")
    spec = inv_tools.toolset(run_state).get("search_history")
    ctx = tool_calls.ToolContext(campaign=planted.cid, scene_identity="", run_id=run_id,
                                 deadline=float("inf"), root="")
    found = json.loads((await spec.fn({"query": case.question[:inv_tools.QUERY_CHARS],
                                        "max": 4}, ctx)).text)
    shown = [i["ref"] for i in found.get("items", [])]
    if client is None or resolved is None:
        return {"state": "completed", "result": None, "trace": {}, "shown": shown}
    evidence = "\n\n".join(f"[{i['ref']}]\n{i['text']}" for i in found.get("items", []))
    messages = [{"role": "system", "content": INCUMBENT_SYSTEM},
                {"role": "user", "content": f"{evidence}\n\nQuestion: {case.question}"}]
    with usage.meter("investigation-question", campaign=planted.cid, run_id=run_id) as m:
        text = await inference.generate("investigation-question", messages, client=client,
                                        resolved=resolved, usage=m.usage, stream=False)
    return {"state": "completed", "result": _incumbent_answer(text, shown), "trace": {},
            "shown": shown}


async def _hidden_route(planted: synth.Planted) -> bool | None:
    """Spec 10.2's hidden route, for the narrator: a search over a word that
    appears only in a hidden post answers exactly as one over a word that
    appears nowhere. None when the case plants no hidden word."""
    if not planted.hidden_terms:
        return None
    run_state = inv_state.RunState(cid=planted.cid, mode="continuity")
    spec = inv_tools.toolset(run_state).get("search_history")
    ctx = tool_calls.ToolContext(campaign=planted.cid, scene_identity="", run_id="hidden",
                                 deadline=float("inf"), root="")
    nowhere = json.loads((await spec.fn({"query": NOWHERE, "max": 4}, ctx)).text)
    clean = True
    for term in planted.hidden_terms:
        hidden = json.loads((await spec.fn({"query": term, "max": 4}, ctx)).text)
        clean = clean and graders.hidden_route_clean(hidden, nowhere)
    return clean


ARM_FNS = {"investigation": _investigate, "sweep": _sweep, "retrieval": _retrieval}


def _grade(case: cases.Case, planted: synth.Planted, arm: str, ran: dict) -> dict:
    found = ran.get("result")
    trace = ran.get("trace") or {}
    forbidden = cases.forbidden(planted)
    if case.mode == "continuity":
        grade = graders.proposal(case.decision, planted.gold_scenes, found, case.direction)
        valid, selected = True, (found or {}).get("evidence_scenes") or []
        written = [(found or {}).get("reason", "")]
    else:
        grade, valid = graders.answer(case.needles, planted.gold_scenes, found)
        selected = (found or {}).get("citations") or []
        written = [(found or {}).get("text", "")]
        if arm == "retrieval" and found is None:
            # Offline, the incumbent wrote nothing: its recall is whether the
            # one search showed a gold scene, which the report prints apart.
            grade = graders.ABSTAINED
    return {"case": case.id, "mode": case.mode, "arm": arm, "grade": grade,
            "citations_valid": valid, "state": ran.get("state", ""),
            "limit": ran.get("limit", ""), "reason": ran.get("reason", ""),
            "text_leaks": graders.text_leaks(written, forbidden),
            "steered": graders.steered(trace, selected, forbidden) if trace else [],
            "shown_gold": bool({graders._scene(r) for r in ran.get("shown", [])}
                               & planted.gold_scenes) if "shown" in ran else None,
            "steps": len(trace.get("steps") or []),
            "usage": trace.get("usage") or {}}


def _report_case(case: cases.Case) -> ReportCase:
    return ReportCase(case.id, case.hypothesis, dict, lambda ctx: [],
                      lambda ctx, output: [], (), task=TASKS[case.mode])


def run_all(*, live: bool = False, targets: dict | None = None, rates=None, run_id: str = "",
            real_home: Path | None = None, selected: tuple[str, ...] = ()
            ) -> tuple[list, list[dict]]:
    """Every case through its mode's arms, each in a throwaway home: `(report
    results, metric records)`. `targets` (live) maps each task to the real
    store's resolution, read before any home moved; `selected` narrows to
    those modes."""
    run_id = run_id or str(uuid.uuid4())
    targets = targets or {}
    results, records = [], []
    for case in cases.CASES:
        if selected and case.mode not in selected:
            continue
        for n, arm in enumerate(cases.ARMS[case.mode], 1):
            with isolated():
                if real_home is not None and reports._is_real_home(real_home):
                    raise RuntimeError(reports.ISOLATE_ERROR)
                planted = synth.RECIPES[case.recipe]()
                day, started, error = usage._today(), time.monotonic(), ""
                client, resolved = _client(case, arm, planted, live=live, targets=targets)
                decider = (targets.get(DECIDE_TASK) if live
                           else offline_resolution(DECIDE_TASK, "decide"))
                try:
                    ran = asyncio.run(ARM_FNS[arm](case, planted, client=client,
                                                   resolved=resolved, run_id=run_id,
                                                   live=live, decider=decider))
                    hidden = asyncio.run(_hidden_route(planted))
                except Exception as exc:  # noqa: BLE001 - one failed case is graded as failed
                    error, ran, hidden = type(exc).__name__, {"state": "failed"}, None
                finally:
                    if live and client is not None:
                        asyncio.run(client.aclose())
                wall = (time.monotonic() - started) * 1000
                rows = reports.harvest(day, run_id, case.id)
                metric = {**_grade(case, planted, arm, ran), "hidden_route": hidden,
                          "error": error, "wall_ms": wall}
                bucket = costs.fold(rows, rates)
                metric["money"] = {
                    "cost_usd": bucket.get("cost_usd") if costs.billed_calls(bucket) else None,
                    "estimated_usd": bucket.get("estimated_usd")
                    if bucket.get("subscription_calls") else None,
                    "modelled_usd": bucket.get("modelled_usd")
                    if bucket.get("modelled_calls") else None}
                checks = [Check("investigation.no_text_leak", not metric["text_leaks"],
                                ", ".join(metric["text_leaks"])),
                          Check("investigation.no_steered_selection", not metric["steered"],
                                ", ".join(metric["steered"]))]
                if hidden is not None:
                    checks.append(Check("investigation.hidden_route", hidden,
                                        "a hidden-only word answers as a missing one"))
                results.append(reports.Result(
                    _report_case(case), arm, checks, json.dumps(ran.get("result")),
                    error=error, error_kind=error, rows=rows, wall_ms=int(wall), bucket=bucket,
                    by_task=costs.by_task(rows, rates), rates=rates, configs=(f"c{n}",),
                    note=f"{metric['grade']}"))
                records.append(metric)
    return results, records


def _client(case, arm, planted, *, live, targets):
    """The arm's model and resolution: offline, the case's script for the
    investigation arm and none for an incumbent (which then makes no call);
    live, the app's client on the real store's resolution."""
    if live:
        task = TASKS[case.mode] if arm != "sweep" else "continuity-reconcile"
        return LLMClient(), targets.get(task)
    if arm != "investigation":
        return None, None
    return FakeToolTurns(*case.script(planted)), offline_resolution(TASKS[case.mode])


# ---- the gate ----
def _rate(rows: list[dict]) -> float:
    return sum(r["grade"] == graders.CORRECT for r in rows) / len(rows) if rows else 0.0


def aggregate(records: list[dict]) -> dict:
    out: dict = {}
    for mode, arms in cases.ARMS.items():
        for arm in arms:
            rows = [r for r in records if r["mode"] == mode and r["arm"] == arm]
            if not rows:
                continue
            out[f"{mode}/{arm}"] = {
                "cases": len(rows), "correct": _rate(rows),
                "abstained": sum(r["grade"] == graders.ABSTAINED for r in rows) / len(rows),
                "text_leaks": sum(bool(r["text_leaks"]) for r in rows),
                "steered": sum(bool(r["steered"]) for r in rows),
                "hidden_route_failures": sum(r["hidden_route"] is False for r in rows),
                "errors": sum(bool(r["error"]) for r in rows),
                "budget_exhausted": sorted({r["limit"] for r in rows if r["limit"]}),
                "wall_ms": sum(r["wall_ms"] for r in rows) / len(rows)}
    return out


def uncovered(records: list[dict], mode: str) -> frozenset[str]:
    """Spec 10.2's corpus items for `mode` that no record's case stands for,
    in both arms."""
    by_id = {c.id: c for c in cases.CASES}
    covered = [{by_id[r["case"]].covers for r in records
                if r["mode"] == mode and r["arm"] == arm and r["case"] in by_id}
               for arm in cases.ARMS[mode]]
    return cases.COVERAGE[mode] - frozenset.intersection(*map(frozenset, covered))


def gate(records: list[dict]) -> dict[str, bool]:
    """Per entry point (spec 10.4): the investigation arm beats its incumbent
    by `cases.MARGIN` on correctness, with no leakage line worse and no
    error, over a run that covered every one of spec 10.2's items for it
    (`cases.COVERAGE`). E1 is absent until 12-S6, and so cannot pass."""
    totals = aggregate(records)
    out: dict[str, bool] = {"rp": False}
    for mode, (incumbent, challenger) in cases.ARMS.items():
        b, c = totals.get(f"{mode}/{incumbent}"), totals.get(f"{mode}/{challenger}")
        if b is None or c is None or uncovered(records, mode):
            out[mode] = False
            continue
        out[mode] = (c["correct"] - b["correct"] >= cases.MARGIN
                     and c["text_leaks"] <= b["text_leaks"] and c["steered"] <= b["steered"]
                     and c["hidden_route_failures"] == 0 and c["errors"] == 0)
    return out


def summary(records: list[dict], *, live: bool) -> str:
    lines = [f"investigation gate ({'LIVE' if live else 'OFFLINE: canned scripts, no model'}):",
             "mode/arm | cases | correct | abstained | text leaks | steered | hidden route | errors"]
    for key, row in aggregate(records).items():
        lines.append(f"{key} | {row['cases']} | {row['correct']:.2f} | {row['abstained']:.2f} | "
                     f"{row['text_leaks']} | {row['steered']} | "
                     f"{row['hidden_route_failures']} | {row['errors']}")
    verdict = gate(records)
    lines.append("rp (E1): absent until 12-S6 -- no arm, no leakage line, cannot pass")
    for mode in cases.ARMS:
        missing = sorted(uncovered(records, mode))
        lines.append(f"{mode}: {'PASS' if verdict[mode] else 'FAIL'} "
                     f"(margin {cases.MARGIN:.2f} over its incumbent)"
                     + (f"; corpus items not run: {', '.join(missing)}" if missing else ""))
    return "\n".join(lines)
