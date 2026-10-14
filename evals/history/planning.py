"""Retrieval query planning arms beside 09's hybrid (roadmap 10-S3, spec 11).

Theory: each case is a controlled archive whose gold scene shares no two words
with the turn, so 09's file signals alone come back thin or empty; the planner
reply is RECORDED (`evals/recordings/history/plan.<shape>.json`) and replayed
through the real facade (`LLMClient` over a recorded provider), so every arm
runs the production phase -- `routes/history_recall.gather` with its free gate,
its meter, its trace and its merge -- and only the model's words are canned.
Offline the arms run structural and lexical retrieval (09's synthetic vectors
are recorded for 09's own corpus, not for planned questions); live, the real
Fast, Decision and Embedding roles answer and 09's downstream grader judges
the reply. The figures measure planning's mechanics on synthetic recipes, not
model quality, and they are compared in conversation, never committed against
anybody's library.

Arms: `hybrid` (09 alone), `plan` (`auto`, no repair), `plan+repair`
(`auto`), `plan-always` (forced every turn: the upper bound on recall and on
cost). Controls (7-9) must not add a planned round under the free gate.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

from evals import costs
from evals import runner as reports
from evals.cases import Case as ReportCase
from evals.graders import Check
from grimoire import embeddings, inference
from grimoire.llm import LLMClient
from grimoire.routes import common, history_recall
from grimoire.store import (
    campaigns,
    characters,
    chronicle,
    config,
    embed_space,
    inference_keys,
    llm_connections,
    scenes,
    tokens,
    usage,
    worlds,
)
from grimoire.store.appearances import transitions
from grimoire.store.history import model
from grimoire.store.history import plan as history_plan
from grimoire.store.scenes import identity

from . import graders, synth
from . import runner as history_runner

RECORDINGS = Path(__file__).resolve().parents[1] / "recordings" / "history"
ARMS = ("hybrid", "plan", "plan+repair", "plan-always")
#: Every terminal state in which a planned round ran.
ROUND_TERMINALS = frozenset({"planned", "repaired", "unrepaired", "repair_redundant"})
PLANNER_MARK = "You decide what earlier events of a role-play campaign the next reply needs."
REPAIR_MARK = "A previous search already ran"
CHECK_MARK = "Recalled earlier scenes:"
FILLER = ("The room is quiet.", "The ferryman waits.")
#: Five, so recall@5 measures positions four and five rather than repeating
#: recall@3 over a selection capped at three.
DEPTH = 5


@dataclass(frozen=True)
class PlanCase:
    shape: int
    label: str
    query: str
    #: The gold scene's text: the planted fact lives here, in words the
    #: turn never uses.
    gold: str = ""
    fact: str = ""
    #: Scenes that must stay out: the similarly named incident.
    distractors: tuple[str, ...] = ()
    #: Where gold sits among `fillers` older scenes (0 = oldest).
    at: int = 4
    fillers: int = 8
    control: str = ""        # "", "nothing", "found" or "young"

    @property
    def id(self):
        return f"history-plan-{self.shape}"


CASES = (
    PlanCase(1, "generic accusation", "You knew the whole time, didn't you?",
             "Winifred hid the brasscompass from Mara for three winters.", "brasscompass"),
    PlanCase(2, "callback to an old promise", "So, are you going to keep your word or not?",
             "Seraphine vowed to return the copperkey before the thaw.", "copperkey"),
    PlanCase(3, "you said you'd never", "You said you'd never sail again.",
             "Mara swore off the sea after the wreck of the gullwing.", "gullwing", at=0,
             fillers=14),
    PlanCase(4, "relationship reversal", "Why do you trust her now?",
             "Winifred forgave Seraphine at the harbour after the storm, over saltbread.",
             "saltbread"),
    PlanCase(5, "hidden object knowledge", "Where is it now?",
             "Seraphine slipped the amberring under the loose floorboard of the mill.",
             "amberring"),
    PlanCase(6, "similarly named incidents", "Who started it?",
             "Mara lit the second fire at the mill with lampoil.", "lampoil",
             distractors=("The first fire at the mill was lightning on the thatch.",)),
    PlanCase(7, "control: needs nothing", "Lovely weather for a walk.", control="nothing"),
    PlanCase(8, "control: already found", "Mara, where is the silver ledger?",
             "Mara gives Seraphine the silver ledger at the pier.", "silver", control="found"),
    PlanCase(9, "control: young campaign", "Where is the silver ledger?", control="young"),
)
BY_ID = {c.id: c for c in CASES}


def recording(case):
    """The case's recorded replies: `plan`, `repair`, `check` and `final`,
    each the text a model sent (absent: that step is never answered)."""
    path = RECORDINGS / f"plan.{case.shape}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def validation_recordings():
    return json.loads((RECORDINGS / "plan.validation.json").read_text(encoding="utf-8"))


# --- the corpus ---------------------------------------------------------------


def _scene(cid, text, *, cast=()):
    sid = scenes.create_scene(cid, "Saltmarch")
    scenes.append_message(cid, sid, "user", text)
    for aid, vid in cast:
        transitions.appear(cid, sid, "characters", aid, vid, "npc", narrate=False)
    chronicle.absorb(cid, {"id": sid, "one_line": text, "summary": text, "keywords": [],
                           "cast": [], "location": "", "date": ""})
    scenes.mark_absorbed(cid, sid, text, text)
    return sid


def _settings():
    # An isolated home is born with the format markers alone: name the roles
    # the phase resolves (offline the provider is replaced, so nothing is
    # sent anywhere), as an upgraded default library carries them.
    config.write_config(**{inference_keys.role_key("primary", "provider"): "openrouter",
                           inference_keys.role_key("primary", "model"): config.DEFAULT_MODEL},
                        history_recall_depth=str(DEPTH), history_recall_budget="1200",
                        context_budget="0", recap_depth="0", archive_depth="0",
                        prompt_log_depth="0")
    llm_connections.update_connection("openrouter", api_key="sk-synthetic")


def build(case, *, seed=10):
    """Plant one case; gold is written down before any retrieval runs."""
    import random
    rng = random.Random(seed + case.shape)
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    root = campaigns.campaign_root(cid)
    _settings()
    if case.control == "young":
        sid = scenes.create_scene(cid, "Saltmarch")
        scenes.append_message(cid, sid, "user", case.query)
        return synth.Planted(cid, sid, (), frozenset(), case.fact, case.shape, 0)
    cast = ()
    if case.control == "found":
        cast = tuple(characters.create_character(root, n, card={"data": {"name": n}})
                     for n in ("Mara", "Seraphine"))
    gold, forbidden = [], set()
    for n in range(case.fillers):
        if case.gold and n == case.at:
            sid = _scene(cid, case.gold, cast=cast)
            gold.append(synth.Gold(sid, identity.scene_identity(cid, sid), 0, 0))
        # "Already found" needs a full selection with agreement: the gold scene
        # and enough shared-cast fillers to fill the depth.
        _scene(cid, rng.choice(FILLER),
               cast=cast if case.control == "found" and n < DEPTH - 1 else ())
    for text in case.distractors:
        forbidden.add(_scene(cid, text))
    current = scenes.create_scene(cid, "Saltmarch")
    scenes.append_message(cid, current, "user", case.query)
    for aid, vid in cast:
        transitions.appear(cid, current, "characters", aid, vid, "npc", narrate=False)
    return synth.Planted(cid, current, tuple(gold), frozenset(forbidden), case.fact,
                         case.shape, case.fillers + len(gold) + len(case.distractors))


# --- the recorded provider ----------------------------------------------------


class RecordedProvider:
    """A provider adapter that answers the planner and the check from a
    case's recording, by what the request looks like -- the shape of
    `tests/llm_fakes.CassetteProvider`, for the real facade. A request it
    has no reply for answers "", which the phase reads as unusable or
    unknown, never as a plan."""

    def __init__(self, replies: dict):
        self.replies = replies
        self.requests: list[str] = []
        self.checks = 0

    async def stream(self, messages, model="", *args, **kwargs):
        system = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
        user = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "user")
        if PLANNER_MARK in system:
            kind = "repair" if REPAIR_MARK in system else "plan"
        elif CHECK_MARK in user:
            kind = "check" if not self.checks else "final"
            self.checks += 1
        else:
            kind = "other"
        self.requests.append(kind)
        yield self.replies.get(kind, "")

    async def aclose(self):
        return None


# --- the arms -----------------------------------------------------------------


def _arm_settings(arm):
    config.write_config(history_plan="off" if arm == "hybrid" else "auto",
                        history_plan_repair="off" if arm == "plan" else "on")


@contextlib.contextmanager
def _live_targets(targets):
    """Live: the planner and the check resolved from the real store before
    isolation, as 09's live rerank arm is."""
    if not targets:
        yield
        return
    original = common.require_inference
    wanted = {"history-query-plan", "history-sufficiency"}

    def resolve(task, *a, **kw):
        return targets[task] if task in wanted and task in targets else original(task, *a, **kw)
    with patch.object(common, "require_inference", side_effect=resolve):
        yield


async def _arm(case, planted, arm, *, live, targets, run_id, provider, space=None):
    client = LLMClient() if live else LLMClient(openrouter=provider)
    embed_client = embeddings.AsyncEmbeddingsClient() if live and space is not None else None
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(_live_targets(targets if live else None))
            if embed_client is not None:
                stack.enter_context(patch.object(embed_space, "endpoint", return_value=space))
            started = time.monotonic()
            evidence = await history_recall.gather(
                None, planted.cid, planted.sid, seed="", resolved=None, llm_client=client,
                embed_client=embed_client, force=arm == "plan-always", run_id=run_id)
            retrieval_ms = (time.monotonic() - started) * 1000
        messages, packed, section, _row = await asyncio.to_thread(
            history_runner._packed, planted, evidence)
        output = ""
        if live and planted.gold:
            with usage.meter("chat", campaign=planted.cid, scene=planted.sid,
                             run_id=run_id) as meter:
                output = await inference.generate("chat", messages, client=client,
                                                  resolved=targets["chat"],
                                                  usage=meter.usage, stream=False)
        return evidence, packed, section, retrieval_ms, output
    finally:
        await client.aclose()
        if embed_client is not None:
            await embed_client.aclose()


def _row_tokens(rows, task):
    """A task's rows and the tokens they reported. A count nobody reported is
    None, never 0 (the recorded provider reports none)."""
    picked = [r for r in rows if r.get("task") == task]

    def total(key):
        seen = [int(r[key]) for r in picked if isinstance(r.get(key), int)]
        return sum(seen) if seen else None
    return {"rows": len(picked), "prompt_tokens": total("prompt_tokens"),
            "completion_tokens": total("completion_tokens")}


def _metrics(case, planted, arm, evidence, packed, section, retrieval_ms, wall, rows):
    trace = evidence.plan
    items = evidence.items
    recalls = graders.recalls(planted.gold, items)
    triggered = bool(trace is not None and trace.trigger)
    rounds = trace is not None and trace.terminal in ROUND_TERMINALS
    plans = [s for s in trace.steps if s.task == "history-query-plan"] if trace else []
    valid = [s for s in plans if s.outcome in ("ok", "declined")]
    forbidden_first = bool(items) and items[0].scene.sid in planted.forbidden
    return {
        "case": case.id, "shape": case.shape, "arm": arm, "control": case.control,
        **{f"recall{k}": v for k, v in recalls.items()},
        "excerpt_recall": graders.excerpt_recall(planted.gold, items, packed),
        "gain": len(trace.gain) if trace else 0,
        "triggered": triggered, "planned_round": rounds,
        "false_trigger": bool(case.control) and rounds,
        "plan_calls": len(plans), "valid_plans": len(valid),
        "drops": sum(sum(p.get("dropped", {}).values()) for p in trace.plans) if trace else 0,
        "steps": len(trace.steps) if trace else 0,
        "terminal": trace.terminal if trace else "",
        "cheap_retrieval_failed": bool(trace and trace.cheap_retrieval_failed),
        "forbidden_first": forbidden_first,
        "planner": _row_tokens(rows, "history-query-plan"),
        "check": _row_tokens(rows, "history-sufficiency"),
        "added_tokens": tokens.count_tokens(section) if section else 0,
        "ceiling": evidence.ceiling, "items": len(items),
        "retrieval_ms": retrieval_ms, "wall_ms": wall,
        "coverage": evidence.coverage.verdict,
    }


def _report_case(case):
    return ReportCase(case.id, case.label, dict, lambda ctx: ctx.get("messages", []),
                      lambda ctx, output: [], (), task="history-query-plan")


def _checks(case, arm, metric, live, output, fact):
    checks = [Check("plan.no_error", not metric["error"], metric["error"]),
              Check("plan.bounded", metric["items"] <= DEPTH
                    and metric["added_tokens"] <= metric["ceiling"],
                    f"{metric['items']} items, {metric['added_tokens']} tokens")]
    if case.control and arm in ("plan", "plan+repair"):
        checks.append(Check("plan.control_quiet", not metric["planned_round"],
                            f"terminal {metric['terminal']!r} on a control"))
    if case.control == "young" and arm in ("plan", "plan+repair"):
        checks.append(Check("plan.young_not_run", metric["terminal"] == "not_run:exhausted",
                            metric["terminal"]))
    if live and fact:
        checks.append(Check("history.consistency", graders.consistent(output, fact), fact))
    return checks


def run_all(*, live=False, targets=None, space=None, rates=None, run_id="", real_home=None,
            arms=ARMS):
    run_id = run_id or str(uuid.uuid4())
    results, records = [], []
    for case in CASES:
        for n, arm in enumerate(arms, 1):
            with history_runner.isolated():
                if real_home is not None and reports._is_real_home(real_home):
                    raise RuntimeError(reports.ISOLATE_ERROR)
                planted = build(case)
                _arm_settings(arm)
                provider = RecordedProvider(recording(case))
                started, day = time.monotonic(), usage._today()
                error = ""
                try:
                    evidence, packed, section, retrieval_ms, output = asyncio.run(_arm(
                        case, planted, arm, live=live, targets=targets or {}, run_id=run_id,
                        provider=provider, space=space))
                except Exception as exc:  # noqa: BLE001 - one failed case is graded failed; the matrix still reports
                    error = type(exc).__name__
                    evidence, packed, section, retrieval_ms, output = (
                        model.Evidence(ceiling=1200), "", "", 0.0, "")
                wall = (time.monotonic() - started) * 1000
                rows = reports.harvest(day, run_id, case.id)
                metric = _metrics(case, planted, arm, evidence, packed, section, retrieval_ms,
                                  wall, rows)
                metric["error"] = error
                metric["requests"] = dict(Counter(provider.requests))
                bucket = costs.fold(rows, rates)
                metric["money"] = {
                    "cost_usd": bucket.get("cost_usd") if costs.billed_calls(bucket) else None,
                    "estimated_usd": bucket.get("estimated_usd")
                    if bucket.get("subscription_calls") else None,
                    "modelled_usd": bucket.get("modelled_usd")
                    if bucket.get("modelled_calls") else None}
                metric["unpriced_calls"] = int(bucket.get("unpriced_calls") or 0)
                checks = _checks(case, arm, metric, live, output, planted.fact)
                results.append(reports.Result(
                    _report_case(case), arm, checks, output, error=error, error_kind=error,
                    rows=rows, wall_ms=int(wall), bucket=bucket,
                    by_task=costs.by_task(rows, rates), rates=rates, configs=(f"c{n}",),
                    note=f"recall@3={metric['recall3']:.3f}; terminal={metric['terminal']}"))
                records.append(metric)
    return results, records


# --- validation: recorded replies through the reader ----------------------------


VALIDATION_INPUT = history_plan.PlanInput(
    window="Mara: You knew the whole time, didn't you?",
    cast=(history_plan.Record("characters:mara", "mara", "characters"),
          history_plan.Record("characters:seraphine", "seraphine", "characters")),
    mentioned=tuple(history_plan.Record(f"items:i{n}", f"thing {n}", "items")
                    for n in range(1, 5)),
    depth=DEPTH)


@dataclass
class Validation:
    id: str
    passed: bool
    detail: str = ""
    tokens: int = field(default=0)


def run_validation():
    """Each recorded reply read by `plan.read` against `VALIDATION_INPUT`,
    compared with what the recording says it must parse to -- a `Plan`'s
    fields, or null for no plan -- and the maximal plan held to
    `PLAN_MAX_TOKENS`."""
    offered = history_plan.offered(VALIDATION_INPUT)
    out = []
    for entry in validation_recordings()["replies"]:
        got, why = history_plan.read(entry["reply"], offered, DEPTH)
        want = entry["expect"]
        if want is None:
            passed, detail = got is None and why == entry.get("why", why), why
        else:
            seen = {"needs_history": got.needs_history if got else None,
                    "subjects": list(got.subjects) if got else None,
                    "questions": list(got.questions) if got else None,
                    "terms": list(got.terms) if got else None,
                    "max_scenes": got.max_scenes if got else None}
            mismatched = {k: seen[k] for k, v in want.items() if seen.get(k) != v}
            passed, detail = got is not None and not mismatched, json.dumps(mismatched)
        count = tokens.count_tokens(entry["reply"])
        if entry.get("max_tokens"):
            passed = passed and count <= history_recall.PLAN_MAX_TOKENS
        out.append(Validation(entry["id"], passed, detail, count))
    return out


# --- aggregate, gate and summary -------------------------------------------------


def aggregate(records):
    out = {}
    for arm in dict.fromkeys(r["arm"] for r in records):
        rows = [r for r in records if r["arm"] == arm]
        targets = [r for r in rows if not r["control"]]
        controls = [r for r in rows if r["control"]]
        out[arm] = {
            **{k: sum(r[k] for r in targets) / max(len(targets), 1)
               for k in ("recall1", "recall3", "recall5", "excerpt_recall", "gain")},
            "trigger_rate": sum(r["triggered"] for r in rows) / max(len(rows), 1),
            "false_trigger_rate": sum(r["false_trigger"] for r in controls)
            / max(len(controls), 1),
            "plan_validity": (sum(r["valid_plans"] for r in rows)
                              / sum(r["plan_calls"] for r in rows)
                              if any(r["plan_calls"] for r in rows) else None),
            "drops": sum(r["drops"] for r in rows),
            "steps_per_turn": sum(r["steps"] for r in rows) / max(len(rows), 1),
            "rows_per_turn": sum(r["planner"]["rows"] + r["check"]["rows"] for r in rows)
            / max(len(rows), 1),
            "tokens_per_turn": _per_turn(rows),
            "wall_ms": sum(r["wall_ms"] for r in rows) / max(len(rows), 1),
            "terminals": dict(Counter(r["terminal"] for r in rows if r["terminal"])),
            "cheap_retrieval_failed_rate": sum(r["cheap_retrieval_failed"] for r in rows)
            / max(len(rows), 1),
            # The three columns, each summed on its own and never added
            # together; a column no case reported stays None, and the arm's
            # unpriced calls say a reported total is a floor.
            "money": {column: _column(rows, column)
                      for column in ("cost_usd", "estimated_usd", "modelled_usd")},
            "unpriced_calls": sum(r.get("unpriced_calls", 0) for r in rows),
        }
    return out


def _column(rows, column):
    seen = [r["money"][column] for r in rows
            if r.get("money", {}).get(column) is not None]
    return round(sum(seen), 6) if seen else None


def _money(row):
    parts = [f"{name} {_shown(row['money'][column], '.4f')}"
             for name, column in (("billed", "cost_usd"), ("subscription", "estimated_usd"),
                                  ("modelled", "modelled_usd"))]
    unpriced = f"; {row['unpriced_calls']} unpriced" if row["unpriced_calls"] else ""
    return ", ".join(parts) + unpriced


def _per_turn(rows):
    """Planner and check tokens per turn, or None when no row reported any."""
    counts = [v for r in rows for part in ("planner", "check")
              for v in (r[part]["prompt_tokens"], r[part]["completion_tokens"]) if v is not None]
    return sum(counts) / max(len(rows), 1) if counts else None


def _shown(value, spec):
    return "n/a" if value is None else format(value, spec)


def gate(records, validation):
    """What the offline suite holds: every validation case parses as
    documented, no arm erred or overran, and the controls stay quiet under
    the free gate. The DEFAULT's gate (spec 11: `plan+repair` beating
    `hybrid` at recall@3 on cases 1-6 within a cost bound agreed in
    conversation) is `default_gate`, reported, never enforced here."""
    return (all(v.passed for v in validation)
            and all(not r["error"] and r["items"] <= DEPTH
                    and r["added_tokens"] <= r["ceiling"] for r in records)
            and not any(r["false_trigger"] for r in records
                        if r["arm"] in ("plan", "plan+repair")))


def default_gate(records):
    totals = aggregate(records)
    if "hybrid" not in totals or "plan+repair" not in totals:
        return False
    return (totals["plan+repair"]["recall3"] > totals["hybrid"]["recall3"]
            and totals["plan+repair"]["false_trigger_rate"] == 0)


def summary(records, validation):
    lines = [("history planning (SYNTHETIC recipes, recorded planner replies offline; "
              "no live quality claim):"),
             ("arm | recall@1 | recall@3 | recall@5 | gain | trigger | false trigger | "
              "valid plans | steps/turn | rows/turn | tokens/turn | wall ms")]
    for arm, row in aggregate(records).items():
        lines.append(f"{arm} | {row['recall1']:.3f} | {row['recall3']:.3f} | "
                     f"{row['recall5']:.3f} | {row['gain']:.2f} | {row['trigger_rate']:.2f} | "
                     f"{row['false_trigger_rate']:.2f} | {_shown(row['plan_validity'], '.2f')} | "
                     f"{row['steps_per_turn']:.2f} | {row['rows_per_turn']:.2f} | "
                     f"{_shown(row['tokens_per_turn'], '.1f')} | {row['wall_ms']:.1f}")
        lines.append(f"  terminals: {row['terminals']}")
        lines.append(f"  money (USD, never added): {_money(row)}")
    lines.append(f"validation: {sum(v.passed for v in validation)}/{len(validation)} parse "
                 "as documented")
    lines.append(f"offline gate: {'PASS' if gate(records, validation) else 'FAIL'}; "
                 f"default gate (reported only): "
                 f"{'met' if default_gate(records) else 'not met'}")
    return "\n".join(lines)
