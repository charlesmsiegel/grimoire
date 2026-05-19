"""REST contract tests for the retcon replay routes (spec
2026-05-19-retcon-design §Backend surface)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from grimoire.orchestrator.errors import (
    RetconBatchClosedError,
    RetconBatchNotFoundError,
    RetconInFlightError,
)
from grimoire.types.orchestrator import ReplayBatchStateView, RetconResult


@dataclass
class FakeOrchestrator:
    calls: list[tuple] = field(default_factory=list)
    view: ReplayBatchStateView | None = None
    raise_on_retcon: Exception | None = None
    raise_on_get: Exception | None = None
    raise_on_action: Exception | None = None

    async def retcon_post(
        self,
        post_id: str,
        new_text: str,
        *,
        campaign_id: str | None = None,
        replay_subsequent: bool = False,
    ) -> RetconResult:
        self.calls.append(("retcon_post", post_id, new_text, campaign_id, replay_subsequent))
        if self.raise_on_retcon is not None:
            raise self.raise_on_retcon
        return RetconResult(
            post_id=post_id,
            original_text="orig",
            new_text=new_text,
            replay_batch_id=("rb_x" if replay_subsequent else None),
        )

    async def get_replay_state(self, campaign_id: str, batch_id: str) -> ReplayBatchStateView:
        self.calls.append(("get_replay_state", campaign_id, batch_id))
        if self.raise_on_get is not None:
            raise self.raise_on_get
        if self.view is None:
            return _default_view(campaign_id, batch_id)
        return self.view

    async def accept_replay(self, campaign_id: str) -> ReplayBatchStateView:
        self.calls.append(("accept", campaign_id))
        if self.raise_on_action is not None:
            raise self.raise_on_action
        return _default_view(campaign_id, "rb_x", current_index=1)

    async def try_again_replay(self, campaign_id: str) -> ReplayBatchStateView:
        self.calls.append(("try_again", campaign_id))
        if self.raise_on_action is not None:
            raise self.raise_on_action
        return _default_view(campaign_id, "rb_x")

    async def cancel_replay(self, campaign_id: str) -> ReplayBatchStateView:
        self.calls.append(("cancel", campaign_id))
        if self.raise_on_action is not None:
            raise self.raise_on_action
        return _default_view(campaign_id, "rb_x", completed=True)


def _default_view(campaign_id: str, batch_id: str, **overrides: Any) -> ReplayBatchStateView:
    base = dict(
        batch_id=batch_id,
        campaign_id=campaign_id,
        edited_post_id="p_1",
        subsequent_post_ids=["p_2", "p_3"],
        current_index=0,
        current_post_id="p_2",
        current_alternate_id="a_new",
        completed=False,
    )
    base.update(overrides)
    return ReplayBatchStateView(**base)


@pytest.fixture
def wire(container, client):
    container.orchestrator = FakeOrchestrator()
    return container


def test_retcon_post_replay_returns_batch_id(wire, client) -> None:
    response = client.post(
        "/api/campaigns/c1/turns/t_1/retcon",
        json={"post_id": "p_1", "new_text": "edit", "replay_subsequent": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["replay_batch_id"] == "rb_x"
    assert wire.orchestrator.calls[0] == ("retcon_post", "p_1", "edit", "c1", True)


def test_retcon_post_leave_as_is(wire, client) -> None:
    response = client.post(
        "/api/campaigns/c1/turns/t_1/retcon",
        json={"post_id": "p_1", "new_text": "edit"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["replay_batch_id"] is None
    assert wire.orchestrator.calls[0] == ("retcon_post", "p_1", "edit", "c1", False)


def test_retcon_post_in_flight_is_409(wire, client) -> None:
    wire.orchestrator.raise_on_retcon = RetconInFlightError("c1")
    response = client.post(
        "/api/campaigns/c1/turns/t_1/retcon",
        json={"post_id": "p_1", "new_text": "edit", "replay_subsequent": True},
    )
    assert response.status_code == 409


def test_get_replay_state(wire, client) -> None:
    response = client.get("/api/campaigns/c1/retcon/replay/rb_x")
    assert response.status_code == 200
    body = response.json()
    assert body["batch_id"] == "rb_x"
    assert body["current_post_id"] == "p_2"


def test_get_replay_state_unknown_batch_is_404(wire, client) -> None:
    wire.orchestrator.raise_on_get = RetconBatchNotFoundError("rb_nope")
    response = client.get("/api/campaigns/c1/retcon/replay/rb_nope")
    assert response.status_code == 404


def test_accept_route(wire, client) -> None:
    response = client.post("/api/campaigns/c1/retcon/replay/rb_x/accept")
    assert response.status_code == 200
    body = response.json()
    assert body["current_index"] == 1
    assert ("accept", "c1") in wire.orchestrator.calls


def test_try_again_route(wire, client) -> None:
    response = client.post("/api/campaigns/c1/retcon/replay/rb_x/try-again")
    assert response.status_code == 200
    assert ("try_again", "c1") in wire.orchestrator.calls


def test_cancel_route(wire, client) -> None:
    response = client.post("/api/campaigns/c1/retcon/replay/rb_x/cancel")
    assert response.status_code == 200
    body = response.json()
    assert body["completed"] is True
    assert ("cancel", "c1") in wire.orchestrator.calls


def test_action_on_closed_batch_is_409(wire, client) -> None:
    wire.orchestrator.view = _default_view("c1", "rb_x", completed=True)
    response = client.post("/api/campaigns/c1/retcon/replay/rb_x/accept")
    assert response.status_code == 409


def test_action_on_unknown_batch_is_404(wire, client) -> None:
    wire.orchestrator.raise_on_get = RetconBatchNotFoundError("rb_x")
    response = client.post("/api/campaigns/c1/retcon/replay/rb_x/accept")
    assert response.status_code == 404


def test_accept_propagates_orchestrator_closed_error(wire, client) -> None:
    # get_replay_state returns an open batch, but the action itself raises
    # because the session-level state is closed.
    wire.orchestrator.raise_on_action = RetconBatchClosedError("rb_x")
    response = client.post("/api/campaigns/c1/retcon/replay/rb_x/accept")
    assert response.status_code == 409
