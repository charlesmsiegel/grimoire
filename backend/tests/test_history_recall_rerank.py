"""09-S5: a soft opinion reorders only answered RRF slots, never admission."""

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from grimoire import decisions, store
from grimoire.llm_errors import LLMError
from grimoire.routes import common, history_recall
from grimoire.store import history_rerank as kit
from grimoire.store import routing
from grimoire.store.history import budget, expand, query
from grimoire.store.inference import facts, migrate
from grimoire.store.inference import resolve as inference_resolve
from tests import inference_baseline, inference_baseline_c
from tests.history_fixtures import absorbed, played
from tests.history_fixtures import cid as cid
from tests.llm_fakes import FakeLLM


def setup_history(cid, count=3):
    olds = [played(cid, text="silver ledger " + str(i)) for i in range(count)]
    for sid in olds:
        absorbed(cid, sid)
    current = played(cid, text="Where is the silver ledger?")
    store.config.write_config(history_recall_rerank="on", recap_depth="0",
                              prompt_log_depth="50")
    store.llm_connections.update_connection("openrouter", api_key="sk-fake")
    return current, list(reversed(olds))


def score_reply(levels):
    return json.dumps({str(i): {"answers": {kit.RELEVANCE_ID: level}}
                       for i, level in enumerate(levels)})


def gather(cid, sid, fake, **kwargs):
    return asyncio.run(history_recall.gather(
        None, cid, sid, seed="", resolved=None, llm_client=fake, embed_client=None, **kwargs))


def test_graded_slots_move_but_unanswered_slot_and_ties_stay(cid):
    sid, order = setup_history(cid, 4)
    fake = FakeLLM([[score_reply([0, None, 3, 3])]])
    ev = gather(cid, sid, fake)
    assert [c.scene.sid for c in ev.candidates] == [order[2], order[1], order[3], order[0]]
    assert [i.scene.sid for i in ev.items] == [order[2], order[1], order[3]]
    signals = [c.rounds[0].rerank for c in ev.candidates]
    assert signals[1].level is None
    assert [s.level for s in signals] == [kit.LEVELS[3], None, kit.LEVELS[3], kit.LEVELS[0]]
    assert all(s.backend == "structured" for s in signals)
    assert fake.calls == 1 and ev.detail()["coverage"]["rerank"] == "ok"
    assert store.tokens.count_tokens(budget.render(ev.items)) <= ev.ceiling


def test_default_off_resolves_nothing(cid, monkeypatch):
    sid, order = setup_history(cid)
    store.config.write_config(history_recall_rerank="off")
    monkeypatch.setattr(common, "require_inference", lambda *a, **k: pytest.fail("off resolved"))
    fake = FakeLLM([[score_reply([3])]])
    ev = gather(cid, sid, fake)
    assert [c.scene.sid for c in ev.candidates] == order and fake.calls == 0
    assert ev.detail()["coverage"]["rerank"] == "off"


def test_unresolvable_route_keeps_order_and_reports_fixed_kind(cid):
    sid, order = setup_history(cid)
    store.llm_connections.update_connection("openrouter", base_url="https://example.test/v1")
    fake = FakeLLM([[score_reply([3])]])
    ev = gather(cid, sid, fake)
    assert [c.scene.sid for c in ev.candidates] == order
    assert ev.detail()["coverage"]["rerank"] == "skipped:missing_key"
    assert fake.calls == 0 and ev.coverage.verdict != "error"


def test_timeout_obeys_remaining_phase_and_preserves_file_evidence(cid, monkeypatch):
    sid, order = setup_history(cid)
    fake = FakeLLM([[score_reply([3, 0, 0])]], stall=True)
    # Freeze only the adapter's phase accounting so file reads cannot spend
    # the 250 ms intended for the stalled provider. plan_deadline keeps its
    # real clock: ignoring the remaining budget still exceeds the 2 s bound.
    phase_now = time.monotonic()
    monkeypatch.setattr(history_recall, "time", SimpleNamespace(monotonic=lambda: phase_now))
    started = time.monotonic()
    ev = gather(cid, sid, fake, deadline=phase_now + 0.25)
    assert time.monotonic() - started < 2
    assert [c.scene.sid for c in ev.candidates] == order and ev.items
    assert ev.detail()["coverage"]["rerank"] == "skipped:timeout"
    assert fake.calls == 1
    rows = [r for r in store.usage.calls(days=1) if r["task"] == kit.TASK]
    assert len(rows) == 1 and rows[0]["error"] == "timeout"


def test_expired_phase_never_resolves_or_calls(cid, monkeypatch):
    sid, order = setup_history(cid)
    monkeypatch.setattr(common, "require_inference", lambda *a, **k: pytest.fail("spent resolved"))
    fake = FakeLLM([[score_reply([3])]])
    ev = gather(cid, sid, fake, deadline=time.monotonic()-1)
    assert [c.scene.sid for c in ev.candidates] == order and fake.calls == 0
    assert ev.detail()["coverage"]["rerank"] == "skipped:deadline"


def test_capture_and_meter_attribute_post_and_keep_signal_reasons(cid, monkeypatch):
    sid, _order = setup_history(cid)
    loop_thread = threading.get_ident()
    resolved_threads = []
    real = common.require_inference
    def resolve(*a, **k):
        resolved_threads.append(threading.get_ident())
        return real(*a, **k)
    monkeypatch.setattr(common, "require_inference", resolve)
    fake = FakeLLM([[score_reply([0, None, 3])]])
    ev = gather(cid, sid, fake, post=7, run_id="rerank-run")
    assert resolved_threads and all(t != loop_thread for t in resolved_threads)
    rows = [r for r in store.usage.calls(days=1) if r["task"] == kit.TASK]
    assert len(rows) == 1
    assert (rows[0]["campaign"], rows[0]["scene"], rows[0]["post"], rows[0]["run_id"]) == (
        cid, sid, 7, "rerank-run")
    entries = [r for r in store.prompt_log.list_entries(cid, sid) if r["task"] == kit.TASK]
    assert len(entries) == 1 and entries[0]["operation"] == "decide"
    entry = store.prompt_log.read_entry(cid, entries[0]["id"])
    outcome = json.loads(next(s["text"] for s in entry["sections"] if s["id"] == "decision"))
    assert len(outcome["calls"]) == 1
    assert outcome["notes"][""]["post"] == 7
    notes = outcome["notes"][""]["rerank"]
    assert set(notes) == {c.scene.key for c in ev.candidates}
    assert all(set(note) == {"level", "position", "backend"} for note in notes.values())
    json.dumps(ev.detail())
    assert all(c.rounds[0].lexical is not None for c in ev.candidates)


def test_async_retrieval_calls_callback_once_on_full_union_before_depth_fit(cid):
    sid, order = setup_history(cid, 5)
    calls = []
    async def rerank(q, candidates, items):
        calls.append((q, candidates, items))
        return tuple(reversed(candidates)), "ok"
    ev = asyncio.run(store.history.retrieve(cid, sid, query.build(cid, sid), ceiling=1200,
                       embed_client=None, deadline=time.monotonic()+4, rerank=rerank))
    assert len(calls) == 1 and len(calls[0][1]) == 5 and len(calls[0][2]) == 5
    assert [i.scene.sid for i in ev.items] == list(reversed(order))[:3]


def test_route_registration_is_new_decision_default_without_sampling():
    assert history_recall.RERANK_TOP == 8 and history_recall.RERANK_CEILING == 4
    route = routing.route(kit.TASK)
    assert (route.key, route.operation, route.default_role, route.legacy) == (
        "history_check", "decide", "decision", routing.NO_LEGACY)
    assert kit.TASK in routing.NO_LEGACY_TASKS
    policy = routing.policy(kit.TASK)
    assert policy.fallback == "role" and not policy.samples and not policy.escalate_to


def test_rank_unplaced_keeps_slot_and_pointwise_is_native_representable(cid):
    sid, order = setup_history(cid)
    q = query.build(cid, sid)
    preview = store.history.retrieve_preview(cid, sid, q, 1200)
    refs = [c.scene.key for c in preview.candidates]
    answer = decisions.ItemResult({kit.RANK_ID: decisions.Answer(
        decisions.Ranking(((refs[2],), (refs[0],)), rest=(refs[1],)))})
    fake = FakeLLM([[""]], decisions=[answer])
    # The existing resolution seam is exercised with a native-only model.
    facts.set_overrides("openrouter", store.config.DEFAULT_MODEL, {"generate": "no"})
    result, status = asyncio.run(history_recall._rerank(
        cid, sid, q, preview.candidates, {i.scene.key: i for i in preview.items}, client=fake,
        deadline=time.monotonic()+4, ranked=True))
    assert [c.scene.sid for c in result] == [order[2], order[1], order[0]]
    assert status == "ok" and len(fake.native_requests) == 1
    item = fake.native_requests[0][0]
    assert item.questions[0].pointwise and decisions.native_gap(item) == ""
    assert [c.rounds[0].rerank.position for c in result] == [0, None, 1]


@pytest.mark.parametrize("reason", ["refused", "abstained", "unreadable"])
def test_native_declines_keep_slots_without_consulting_distribution(cid, reason):
    sid, order = setup_history(cid)
    facts.set_overrides("openrouter", store.config.DEFAULT_MODEL, {"generate": "no"})
    results = [decisions.ItemResult({kit.RELEVANCE_ID: decisions.Answer(0,
               distribution={"3": 1.0})}),
               decisions.ItemResult({kit.RELEVANCE_ID: decisions.Answer(None, reason,
               distribution={"0": 1.0})}),
               decisions.ItemResult({kit.RELEVANCE_ID: decisions.Answer(3,
               distribution={"0": 1.0})})]
    ev = gather(cid, sid, FakeLLM([[""]], decisions=results))
    assert [c.scene.sid for c in ev.candidates] == [order[2], order[1], order[0]]
    assert ev.candidates[1].rounds[0].rerank.level is None
    assert "distribution" not in json.dumps(ev.detail())


def test_a_rerank_that_graded_nothing_says_so(cid):
    """Every answer declined and no call failed: RRF order stands, and the
    row says the rerank went unanswered rather than `ok`."""
    sid, order = setup_history(cid)
    facts.set_overrides("openrouter", store.config.DEFAULT_MODEL, {"generate": "no"})
    results = [decisions.ItemResult({kit.RELEVANCE_ID: decisions.Answer(None, "abstained")})
               for _ in range(3)]
    ev = gather(cid, sid, FakeLLM([[""]], decisions=results))
    assert [c.scene.sid for c in ev.candidates] == [order[0], order[1], order[2]]
    assert ev.detail()["coverage"]["rerank"] == "skipped:unanswered"


def test_failed_provider_is_captured_without_its_prose_and_keeps_evidence(cid):
    sid, order = setup_history(cid)
    fake = FakeLLM([[""]], error=LLMError("network", "PRIVATE_PROVIDER_ECHO"))
    ev = gather(cid, sid, fake)
    assert [c.scene.sid for c in ev.candidates] == order and ev.items
    assert ev.detail()["coverage"]["rerank"] == "skipped:network"
    entries = [r for r in store.prompt_log.list_entries(cid, sid) if r["task"] == kit.TASK]
    entry = store.prompt_log.read_entry(cid, entries[0]["id"])
    text = next(s["text"] for s in entry["sections"] if s["id"] == "decision")
    assert "PRIVATE_PROVIDER_ECHO" not in text and '"error_kind": "network"' in text


def test_top_eight_prompt_view_is_expanded_once_and_tail_retains_slots(cid, monkeypatch):
    sid, order = setup_history(cid, 10)
    calls = []
    real = expand.excerpts
    def excerpts(*a, **k):
        calls.append(a[1].sid)
        return real(*a, **k)
    monkeypatch.setattr(expand, "excerpts", excerpts)
    fake = FakeLLM([[score_reply([0, 0, 0, 0, 0, 0, 0, 3])]])
    ev = gather(cid, sid, fake)
    assert [c.scene.sid for c in ev.candidates] == [order[7], *order[:7], *order[8:]]
    assert sorted(calls) == sorted(order[:8])
    assert fake.calls == 1 and len(fake.schemas[0]["properties"]) == 8
    assert all(c.rounds[0].rerank is None for c in ev.candidates[8:])
    assert "**Saltmarch**" in str(fake.messages)


def test_preview_identifies_skipped_rerank_without_resolving(cid, monkeypatch):
    sid, _ = setup_history(cid)
    monkeypatch.setattr(common, "require_inference", lambda *a, **k: pytest.fail("preview resolved"))
    ev = store.history.retrieve_preview(cid, sid, query.build(cid, sid), 1200)
    assert ev.detail()["coverage"]["rerank"] == "skipped:preview"


BASELINE_STATES = [(suite, state) for suite in (inference_baseline, inference_baseline_c)
                   for state in sorted(suite.STATES)]


@pytest.mark.parametrize("suite,state", BASELINE_STATES,
                         ids=[suite.__name__.split(".")[-1]+":"+state
                              for suite, state in BASELINE_STATES])
def test_new_route_uses_decision_defaults_in_all_frozen_states_and_formats(suite, state, tmp_path):
    """New defaults are checked separately; the old frozen cells stay exact."""
    base = suite if suite is inference_baseline else suite.base
    with base.client_at(tmp_path) as client:
        ctx = suite.STATES[state](client)
        for phase in ("legacy", "migrated", "retired"):
            if phase == "migrated":
                with mock.patch.object(migrate, "_retire", lambda *_a: None):
                    status = (inference_baseline.migrate_state(state)
                              if suite is inference_baseline else migrate.ensure())
                assert status.state == "done"
            elif phase == "retired":
                assert migrate.ensure().state == "done"
            for scope in ("", ctx["cid"]):
                new = inference_resolve.resolve(kit.TASK, scope, operation="decide")
                existing = inference_resolve.resolve("turn-intent", scope, operation="decide")
                assert new.route == "history_check" and new.operation == "decide"
                assert (new.role, new.via, new.scope, new.missing, new.fallback_missing,
                        new.fallback_problem, new.rides) == (
                    existing.role, existing.via, existing.scope, existing.missing,
                    existing.fallback_missing, existing.fallback_problem, existing.rides)
                assert [(a.provider_id, a.model, a.preset_id, a.decision_mode) for a in new.attempts] == [
                    (a.provider_id, a.model, a.preset_id, a.decision_mode) for a in existing.attempts]
            observed = suite.observe(client, ctx)
            assert kit.TASK not in observed["tasks"]
            if suite is inference_baseline_c:
                assert kit.TASK not in observed["c"]["lowered"]
