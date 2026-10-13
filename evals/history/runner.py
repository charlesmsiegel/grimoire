"""Production retrieval arms, isolated packing, and 01a report integration.

Theory: ablations remove independent signals while keeping eligibility,
expansion and packing identical. Synthetic directions are controlled inputs to
production scoring; the packed prompt, not the pre-pack ranking, is graded.
Reuses the embedding adapter, vector cache, source retriever, prompt composer,
eval harvest and separate money columns. Live mode captures settings before
isolation; every corpus, cache and ledger write stays in a temporary home.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
import time
import uuid
from collections import Counter
from functools import partial
from pathlib import Path
from unittest.mock import patch

import httpx

from evals import costs
from evals import runner as reports
from evals.cases import Case as ReportCase
from evals.graders import Check
from grimoire import embeddings, inference, wire
from grimoire.llm import LLMClient
from grimoire.routes import common, history_recall
from grimoire.store import embed_space, tokens, usage, vectors
from grimoire.store.context import assemble
from grimoire.store.history import model, retrieval, semantic
from grimoire.store.inference import resolve
from grimoire.store.searchdocs import live as live_docs

from . import cases, graders, synth

RECORDING = Path(__file__).resolve().parents[1]/"recordings"/"history"/"vectors-synthetic-history-1.json"


@contextlib.contextmanager
def isolated():
    previous = os.environ.get("GRIMOIRE_HOME")
    with tempfile.TemporaryDirectory(prefix="grimoire-history-eval-") as home:
        os.environ["GRIMOIRE_HOME"] = home
        try:
            yield Path(home)
        finally:
            if previous is None:
                os.environ.pop("GRIMOIRE_HOME", None)
            else:
                os.environ["GRIMOIRE_HOME"] = previous


def recording():
    return json.loads(RECORDING.read_text(encoding="utf-8-sig"))


def replay_space():
    asset = recording()
    options = wire.EmbedOptions()
    return {"model": asset["model"], "base_url": "https://history.invalid/v1", "key": "",
            "space": resolve.space_of({"id": "history-synthetic", "rev": asset["revision"]},
                                      asset["model"], options),
            "provider": "history-synthetic", "provider_name": "synthetic history",
            "provider_kind": "openai_compatible", "options": options}


def replay_vector(text):
    asset = recording()
    if text in asset["queries"]:
        return asset["queries"][text]
    return next(row["vector"] for row in asset["document_rules"] if row["contains"] in text)


def replay_client(seen):
    # A real asynchronous embedding adapter over an offline transport. No
    # second LLM fake: the adapter parses its normal provider response.
    def respond(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={"model": body["model"],
            "data": [{"index": i, "embedding": replay_vector(t)}
                     for i, t in enumerate(body["input"])],
            "usage": {"prompt_tokens": sum(tokens.count_if_loaded(t) for t in body["input"])}})
    return embeddings.AsyncEmbeddingsClient(httpx.AsyncClient(transport=httpx.MockTransport(respond)))


def _report_case(case):
    return ReportCase(case.id, case.label, dict, lambda ctx: ctx.get("messages", []),
                      lambda ctx, output: [], (), task="chat")


def _packed(planted, evidence):
    messages, detail = assemble.compose_turn(planted.cid, planted.sid, history=evidence)
    text = "\n".join(str(m.get("content", "")) for m in messages)
    rows = detail["sections"] if detail else []
    row = next((r for r in rows if r["id"] == "history_recall"), {})
    section = row.get("text", "") if not row.get("dropped") else ""
    return messages, text, section, row


async def _arm(case, planted, arm, *, space, live, targets, seen, run_id, stats):
    client = embeddings.AsyncEmbeddingsClient() if live else replay_client(seen)
    llm_client = LLMClient() if live else None
    score = semantic.score
    widen = semantic.widen
    async def measured_score(*args, **kwargs):
        started = time.monotonic()
        result = await score(*args, **kwargs)
        stats["network_ms"] += (time.monotonic()-started)*1000
        stats["semantic_evaluated"] = len(result.evaluated)
        return result
    async def measured_widen(*args, **kwargs):
        result = await widen(*args, **kwargs)
        stats["semantic_evaluated"] = len(result.evaluated)
        return result
    deadline = time.monotonic()+4
    q = model.Query((case.query,), terms=case.terms,
                    scopes=frozenset() if arm in {"lexical", "semantic"} else model.RELATIONS)
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(embed_space, "endpoint", return_value=space))
            stack.enter_context(patch.object(semantic, "score", measured_score))
            stack.enter_context(patch.object(semantic, "widen", measured_widen))
            if arm == "semantic":
                stack.enter_context(patch.object(retrieval.lexical, "score", side_effect=lambda terms, docs, **kw:
                    [model.LexicalSignal(0, ()) for _ in docs]))
            callback = None
            if arm == "hybrid+rerank":
                original = common.require_inference
                stack.enter_context(patch.object(common, "require_inference", side_effect=lambda task, *a, **kw:
                    targets["history-rerank"] if task == "history-rerank" else original(task, *a, **kw)))
                callback = partial(history_recall._rerank, planted.cid, planted.sid,
                                   client=llm_client, deadline=deadline, run_id=run_id)
            def recall(cid, sid):
                return retrieval.retrieve(
                    cid, sid, q, ceiling=1200,
                    embed_client=client if arm in {"semantic", "hybrid", "hybrid+rerank"} else None,
                    deadline=deadline, rerank=callback, run_id=run_id)
            evidence = model.Evidence(ceiling=1200) if arm == "context-only" else await recall(
                planted.cid, planted.sid)
            if planted.first is not None and arm != "context-only":
                requests = len(seen)
                first = await recall(*planted.first)
                stats["first_scene"] = (first.coverage.verdict == "exhausted"
                                        and first.coverage.tiers == (1,)
                                        and not first.items and len(seen) == requests)
        messages, packed, section, row = await asyncio.to_thread(_packed, planted, evidence)
        output = ""
        if live:
            with usage.meter("chat", campaign=planted.cid, scene=planted.sid, run_id=run_id) as meter:
                output = await inference.generate("chat", messages, client=llm_client,
                    resolved=targets["chat"], usage=meter.usage, stream=False)
        return evidence, packed, section, row, output
    finally:
        await client.aclose()
        if llm_client is not None:
            await llm_client.aclose()


def _metrics(case, planted, arm, evidence, packed, section, row, stats, wall, rows):
    # Context-only uses what the real recap/archive actually packed. Gold is
    # credited only if its planted fact text is in that prompt.
    items = evidence.items
    if arm == "context-only":
        items = tuple(model.EvidenceItem(model.SceneRef(g.sid, g.identity), "Saltmarch")
                      for g in planted.gold if planted.fact in packed)
    else:
        details = {r["key"]: r for r in row.get("history", {}).get("items", [])}
        items = tuple(i for i in items if section and not details.get(i.scene.key, {}).get("shed", False))
    recalls = graders.recalls(planted.gold, items)
    hard = not planted.forbidden.intersection(i.scene.sid for i in items)
    if case.shape == 8:
        hard = hard and all(not i.location for i in evidence.items)
        hard = hard and all("characters:mara" not in r.refs for c in evidence.candidates
                            for s in c.rounds if s.structural for r in s.structural.relations)
        # The excluded name is not a lexical term either, weighted or plain (4).
        hard = hard and all("mara" not in s.lexical.matched for c in evidence.candidates
                            for s in c.rounds if s.lexical)
    if case.shape == 9 and arm != "context-only":
        hard = hard and evidence.coverage.verdict == "exhausted" and evidence.coverage.tiers == (1,)
        hard = hard and stats.get("first_scene", False)
    if case.shape == 10 and arm != "context-only":
        hard = hard and len({c.scene.key for c in evidence.candidates}) == 2
    added = tokens.count_tokens(section) if section else 0
    return {"case": case.id, "shape": case.shape, "arm": arm,
            **{f"recall{k}": v for k, v in recalls.items()},
            "excerpt_recall": graders.excerpt_recall(planted.gold, items, packed),
            "hard_rules": hard, "added_tokens": added, "ceiling": evidence.ceiling,
            "prompt_tokens": tokens.count_tokens(packed), "wall_ms": wall,
            "network_ms": stats["network_ms"], "file_ms": max(0, wall-stats["network_ms"]),
            "semantic_evaluated": stats["semantic_evaluated"],
            "embed_rows": dict(Counter(r["task"] for r in rows if r.get("operation") == "embed")),
            "decide_calls": sum(r.get("operation") == "decide" for r in rows),
            "coverage": evidence.detail()["coverage"]}


def run_all(*, long=False, live=False, targets=None, space=None, rates=None, run_id="", real_home=None):
    run_id = run_id or str(uuid.uuid4())
    results, records = [], []
    arms = (*cases.ARMS, "hybrid+rerank") if live else cases.ARMS
    targets = targets or {}
    space = space if live else replay_space()
    for case in cases.CASES:
        for n, arm in enumerate(arms, 1):
            with isolated():
                if real_home is not None and reports._is_real_home(real_home):
                    raise RuntimeError(reports.ISOLATE_ERROR)
                planted = synth.build(case, long=long)
                if not live:
                    # Preload exact current SearchDocument text/space keys with
                    # the recorded deterministic directions, using the real cache.
                    for doc in live_docs.scene_documents(planted.cid).documents:
                        vectors.save(space["space"], doc.doc.text, replay_vector(doc.doc.text))
                seen, stats = [], {"network_ms": 0.0, "semantic_evaluated": 0}
                started, day = time.monotonic(), usage._today()
                error = ""
                try:
                    evidence, packed, section, row, output = asyncio.run(_arm(
                        case, planted, arm, space=space, live=live, targets=targets,
                        seen=seen, run_id=run_id, stats=stats))
                except Exception as exc:  # noqa: BLE001 - one failed case is graded as failed; the matrix still reports
                    error = type(exc).__name__
                    evidence, packed, section, row, output = model.Evidence(ceiling=1200), "", "", {}, ""
                wall = (time.monotonic()-started)*1000
                rows = reports.harvest(day, run_id, case.id)
                metric = _metrics(case, planted, arm, evidence, packed, section, row, stats, wall, rows)
                metric["offline_transport_requests"] = len(seen)
                metric["error"] = error
                bucket = costs.fold(rows, rates)
                metric["money"] = {"cost_usd": bucket.get("cost_usd") if costs.billed_calls(bucket) else None,
                    "estimated_usd": bucket.get("estimated_usd") if bucket.get("subscription_calls") else None,
                    "modelled_usd": bucket.get("modelled_usd") if bucket.get("modelled_calls") else None}
                checks = [Check("history.hard_rules", metric["hard_rules"], "eligibility/visibility"),
                          Check("history.ceiling", metric["added_tokens"] <= evidence.ceiling, "packed history")]
                if live and planted.gold:
                    checks.append(Check("history.consistency", graders.consistent(output, planted.fact), planted.fact))
                results.append(reports.Result(_report_case(case), arm, checks, output, error=error,
                    error_kind=error, rows=rows, wall_ms=int(wall), bucket=bucket,
                    by_task=costs.by_task(rows, rates), rates=rates, configs=(f"c{n}",),
                    note=f"recall@3={metric['recall3']:.3f}; excerpt={metric['excerpt_recall']:.3f}"))
                records.append(metric)
    return results, records


def aggregate(records):
    out = {}
    for arm in dict.fromkeys(r["arm"] for r in records):
        rows = [r for r in records if r["arm"] == arm]
        out[arm] = {k: sum(r[k] for r in rows)/len(rows)
                    for k in ("recall1", "recall3", "recall5", "excerpt_recall", "added_tokens",
                              "prompt_tokens", "wall_ms", "file_ms", "network_ms")}
        out[arm]["hard_rules"] = all(r["hard_rules"] and not r["error"] for r in rows)
    return out


def gate(records):
    totals = aggregate(records)
    hybrid = totals["hybrid"]["recall3"]
    return (all(r["hard_rules"] and not r["error"] and r["added_tokens"] <= r["ceiling"] for r in records)
            and all(hybrid >= row["recall3"] for row in totals.values())
            and all(hybrid > totals[arm]["recall3"] for arm in ("context-only", "lexical")))


def summary(records):
    lines = ["history retrieval (SYNTHETIC vectors offline; no live quality claim):",
             "arm | recall@1 | recall@3 | recall@5 | excerpt | packed added tokens | file/network/wall ms"]
    for arm, row in aggregate(records).items():
        lines.append(f"{arm} | {row['recall1']:.3f} | {row['recall3']:.3f} | {row['recall5']:.3f} | "
                     f"{row['excerpt_recall']:.3f} | {row['added_tokens']:.1f} | "
                     f"{row['file_ms']:.1f}/{row['network_ms']:.1f}/{row['wall_ms']:.1f}")
    lines.append(f"ordering/eligibility/ceiling gate: {'PASS' if gate(records) else 'FAIL'}")
    return "\n".join(lines)
