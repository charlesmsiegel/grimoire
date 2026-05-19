"""Auxiliary runner tests.

The runner builds a prompt with the suppression matrix applied, streams
the gateway response, and parks the result in `_inflight_aux`. No
canonical state is mutated.
"""

from __future__ import annotations

import asyncio

import pytest

from grimoire.auxiliary.types import (
    AuxiliaryResult,
    AuxiliaryTask,
    CommitAction,
    TaskKind,
)


async def test_brainstorm_produces_text_no_state_change(orchestrator, seeded_state):
    task = AuxiliaryTask(kind=TaskKind.BRAINSTORM, snippet="ideas for next scene")
    result = await orchestrator.run_auxiliary_task(campaign_id=seeded_state.campaign_id, task=task)
    assert isinstance(result, AuxiliaryResult)
    assert result.text != ""
    assert result.pending_commit_action == CommitAction.COPY
    # Parked in-flight.
    assert result.id in orchestrator._inflight_aux
    # The fake gateway routes through `auxiliary.brainstorm`.
    assert orchestrator._gateway.seen_tasks[-1] == "auxiliary.brainstorm"
    # The extractor was never called — no canonical mutation path.
    assert orchestrator._extractor.calls == []


async def test_impersonate_pc_returns_pending_submit_action(orchestrator, seeded_state):
    task = AuxiliaryTask(kind=TaskKind.IMPERSONATE_PC)
    result = await orchestrator.run_auxiliary_task(campaign_id=seeded_state.campaign_id, task=task)
    assert result.pending_commit_action == CommitAction.SUBMIT_POST


async def test_concurrent_aux_tasks_demuxed_by_result_id(orchestrator, seeded_state):
    tasks = [
        AuxiliaryTask(kind=TaskKind.BRAINSTORM, snippet="A"),
        AuxiliaryTask(kind=TaskKind.BRAINSTORM, snippet="B"),
        AuxiliaryTask(kind=TaskKind.BRAINSTORM, snippet="C"),
    ]
    results = await asyncio.gather(
        *[
            orchestrator.run_auxiliary_task(campaign_id=seeded_state.campaign_id, task=t)
            for t in tasks
        ]
    )
    ids = {r.id for r in results}
    assert len(ids) == 3
    for r in results:
        assert r.id in orchestrator._inflight_aux


async def test_discard_clears_inflight(orchestrator, seeded_state):
    task = AuxiliaryTask(kind=TaskKind.BRAINSTORM, snippet="x")
    result = await orchestrator.run_auxiliary_task(campaign_id=seeded_state.campaign_id, task=task)
    assert result.id in orchestrator._inflight_aux
    discarded = await orchestrator.discard_auxiliary(result.id)
    assert discarded is True
    assert result.id not in orchestrator._inflight_aux
    # Idempotent.
    assert await orchestrator.discard_auxiliary(result.id) is False


async def test_aux_emits_ws_token_and_complete_events(orchestrator, seeded_state, ws):
    task = AuxiliaryTask(kind=TaskKind.BRAINSTORM, snippet="x")
    result = await orchestrator.run_auxiliary_task(campaign_id=seeded_state.campaign_id, task=task)
    types = [m[1]["type"] for m in ws.messages]
    assert "aux_token" in types
    assert "aux_complete" in types
    completes = [m for _, m in ws.messages if m["type"] == "aux_complete"]
    assert completes[-1]["result_id"] == result.id


async def test_aux_falls_back_when_per_task_route_missing(orchestrator, seeded_state, fake_gateway):
    # Strip the per-task route; expect fallback to `main` with warning.
    fake_gateway._router._routes.pop("auxiliary.brainstorm")
    task = AuxiliaryTask(kind=TaskKind.BRAINSTORM, snippet="x")
    result = await orchestrator.run_auxiliary_task(campaign_id=seeded_state.campaign_id, task=task)
    assert "fallback_to_canonical_model" in result.warnings
    assert fake_gateway.seen_tasks[-1] == "main"


async def test_aux_failure_emits_error_event(orchestrator, seeded_state, fake_gateway, ws):
    fake_gateway.fail_after = 0
    task = AuxiliaryTask(kind=TaskKind.BRAINSTORM, snippet="x")
    with pytest.raises(RuntimeError):
        await orchestrator.run_auxiliary_task(campaign_id=seeded_state.campaign_id, task=task)
    types = [m[1]["type"] for m in ws.messages]
    assert "aux_error" in types
