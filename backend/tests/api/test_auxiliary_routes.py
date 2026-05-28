"""REST contract tests for the auxiliary-task routes.

The orchestrator is faked: it captures the start call, records the
returned `AuxiliaryResult`, and exposes accept/discard semantics. The
HTTP surface is what we assert here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from grimoire.auxiliary.types import (
    AuxiliaryResult,
    AuxiliaryTask,
    CommitAction,
    TaskKind,
    commit_action_for,
)
from grimoire.orchestrator.errors import AuxiliaryNotFoundError


class FakeAuxOrchestrator:
    def __init__(self) -> None:
        self._inflight_aux: dict[str, AuxiliaryResult] = {}
        self.calls: list[tuple[str, AuxiliaryTask]] = []
        self.accepted: list[tuple[str, str, str | None]] = []
        self.discarded: list[str] = []

    async def run_auxiliary_task(
        self,
        *,
        campaign_id: str,
        task: AuxiliaryTask,
        on_token: Any = None,
    ) -> AuxiliaryResult:
        self.calls.append((campaign_id, task))
        result = AuxiliaryResult(
            id=f"ar_{len(self._inflight_aux):04d}",
            task=task,
            text=f"reply for {task.kind.value}",
            completed_at=datetime.now(UTC),
            model_used="claude-test",
            tokens=10,
            pending_commit_action=commit_action_for(task.kind),
        )
        self._inflight_aux[result.id] = result
        return result

    async def accept_auxiliary(
        self,
        campaign_id: str,
        result_id: str,
        *,
        edited_text: str | None = None,
    ) -> dict[str, Any]:
        if result_id not in self._inflight_aux:
            raise AuxiliaryNotFoundError(result_id)
        aux = self._inflight_aux.pop(result_id)
        self.accepted.append((campaign_id, result_id, edited_text))
        return {
            "committed": True,
            "action": aux.pending_commit_action.value,
            "result_id": result_id,
            "text": edited_text if edited_text is not None else aux.text,
        }

    async def discard_auxiliary(self, result_id: str) -> bool:
        if result_id not in self._inflight_aux:
            return False
        self._inflight_aux.pop(result_id, None)
        self.discarded.append(result_id)
        return True


def _orch(container) -> FakeAuxOrchestrator:
    orch = FakeAuxOrchestrator()
    container.orchestrator = orch
    return orch


def test_brainstorm_returns_result(client, container) -> None:
    orch = _orch(container)
    response = client.post(
        "/api/campaigns/c1/auxiliary/brainstorm",
        json={"prompt": "next-scene seeds"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == TaskKind.BRAINSTORM.value
    assert body["pending_commit_action"] == CommitAction.COPY.value
    assert body["id"].startswith("ar_")
    # Snippet was wired through.
    assert orch.calls[0][1].snippet == "next-scene seeds"


def test_impersonate_pc_carries_steering_hint(client, container) -> None:
    orch = _orch(container)
    response = client.post(
        "/api/campaigns/c1/auxiliary/impersonate-pc",
        json={"steering_hint": "be bolder"},
    )
    assert response.status_code == 200
    assert response.json()["kind"] == TaskKind.IMPERSONATE_PC.value
    assert orch.calls[0][1].steering_hint == "be bolder"


def test_rewrite_post_requires_post_id_and_instruction(client, container) -> None:
    _orch(container)
    bad = client.post("/api/campaigns/c1/auxiliary/rewrite-post", json={})
    assert bad.status_code == 422
    good = client.post(
        "/api/campaigns/c1/auxiliary/rewrite-post",
        json={"post_id": "p_42", "edit_instruction": "more menacing"},
    )
    assert good.status_code == 200
    body = good.json()
    assert body["kind"] == TaskKind.REWRITE_POST.value
    assert body["pending_commit_action"] == CommitAction.REPLACE_POST.value


def test_continue_as_carries_character(client, container) -> None:
    orch = _orch(container)
    response = client.post(
        "/api/campaigns/c1/auxiliary/continue-as",
        json={"character_ref": "npc_crow", "target_post_id": "p_1"},
    )
    assert response.status_code == 200
    assert orch.calls[0][1].target_character_ref == "npc_crow"
    assert orch.calls[0][1].target_post_id == "p_1"


def test_what_would_x_say(client, container) -> None:
    _orch(container)
    response = client.post(
        "/api/campaigns/c1/auxiliary/what-would-x-say",
        json={"character_ref": "npc_crow", "snippet": "The carriage is late."},
    )
    assert response.status_code == 200
    assert response.json()["pending_commit_action"] == CommitAction.COPY.value


def test_edit_prose(client, container) -> None:
    _orch(container)
    response = client.post(
        "/api/campaigns/c1/auxiliary/edit-prose",
        json={"snippet": "he runs fast", "edit_instruction": "more vivid"},
    )
    assert response.status_code == 200
    assert response.json()["pending_commit_action"] == CommitAction.REPLACE_DRAFT.value


def test_translate(client, container) -> None:
    orch = _orch(container)
    response = client.post(
        "/api/campaigns/c1/auxiliary/translate",
        json={"snippet": "The crow lit on the wall.", "target_language": "French"},
    )
    assert response.status_code == 200
    assert orch.calls[0][1].target_language == "French"


def test_accept_dispatches(client, container) -> None:
    orch = _orch(container)
    start = client.post(
        "/api/campaigns/c1/auxiliary/brainstorm",
        json={"prompt": "x"},
    ).json()
    result_id = start["id"]

    response = client.post(
        f"/api/campaigns/c1/auxiliary/{result_id}/accept",
        json={"edited_text": "edited"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["committed"] is True
    assert body["text"] == "edited"
    assert orch.accepted == [("c1", result_id, "edited")]


def test_accept_missing_returns_404(client, container) -> None:
    _orch(container)
    response = client.post("/api/campaigns/c1/auxiliary/ar_missing/accept", json={})
    assert response.status_code == 404


def test_discard_pops_inflight(client, container) -> None:
    orch = _orch(container)
    start = client.post(
        "/api/campaigns/c1/auxiliary/brainstorm",
        json={"prompt": "x"},
    ).json()
    result_id = start["id"]

    response = client.post(f"/api/campaigns/c1/auxiliary/{result_id}/discard")
    assert response.status_code == 200
    assert response.json() == {"discarded": True, "result_id": result_id}
    assert orch.discarded == [result_id]


def test_discard_missing_returns_404(client, container) -> None:
    _orch(container)
    response = client.post("/api/campaigns/c1/auxiliary/ar_missing/discard")
    assert response.status_code == 404


def test_in_flight_listing(client, container) -> None:
    _orch(container)
    client.post("/api/campaigns/c1/auxiliary/brainstorm", json={"prompt": "a"})
    client.post("/api/campaigns/c1/auxiliary/brainstorm", json={"prompt": "b"})
    response = client.get("/api/campaigns/c1/auxiliary/in-flight")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 2
    assert {it["kind"] for it in items} == {TaskKind.BRAINSTORM.value}
