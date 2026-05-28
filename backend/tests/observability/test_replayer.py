"""Tests for ``TurnReplayerService``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from grimoire.observability.audit import AuditStore
from grimoire.observability.replayer import TurnReplayerService
from grimoire.types.llm import CompletionRequest
from grimoire.types.observability import ReplayOptions, ReplaySubstitution, TurnAudit


@dataclass
class _FakeCompletion:
    text: str = "new response"


class _FakeGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, CompletionRequest, str | None]] = []
        self.next_text: str = "new response"
        self.raise_next: Exception | None = None

    async def complete(
        self, task: str, request: CompletionRequest, campaign_id: str | None = None
    ) -> Any:
        self.calls.append((task, request, campaign_id))
        if self.raise_next:
            raise self.raise_next
        return _FakeCompletion(text=self.next_text)


@dataclass
class _FakeForkResult:
    new_campaign_id: str = "c_forked"


class _FakeForker:
    def __init__(self) -> None:
        self.fork_calls: list[dict] = []

    async def fork_campaign(self, **kwargs: Any) -> _FakeForkResult:
        self.fork_calls.append(kwargs)
        return _FakeForkResult()


async def _seed(db, **overrides) -> tuple[AuditStore, TurnAudit]:
    audit_store = AuditStore(db)
    audit = TurnAudit(
        turn_id="t_seed",
        campaign_id="c_1",
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        player_input="I open the door.",
        response_text="The door opens slowly.",
        llm_provider="anthropic",
        llm_model="claude-3-haiku",
        llm_params={"temperature": 0.7, "max_tokens": 1024},
    )
    audit = audit.model_copy(update=overrides)
    await audit_store.record(audit)
    return audit_store, audit


async def test_replay_calls_gateway_and_returns_diff(db) -> None:
    audit_store, _ = await _seed(db)
    gateway = _FakeGateway()
    forker = _FakeForker()
    replayer = TurnReplayerService(audit_store=audit_store, gateway=gateway, forker=forker)

    result = await replayer.replay("t_seed", ReplayOptions(on_fork=False))
    assert result.new_response_text == "new response"
    assert gateway.calls
    _, request, campaign_id = gateway.calls[0]
    assert campaign_id == "c_1"
    assert request.model == "claude-3-haiku"
    assert request.temperature == 0.7
    assert any(m.content == "I open the door." for m in request.messages)


async def test_substitution_overrides_model_and_temperature(db) -> None:
    audit_store, _ = await _seed(db)
    gateway = _FakeGateway()
    replayer = TurnReplayerService(audit_store=audit_store, gateway=gateway)

    await replayer.replay(
        "t_seed",
        ReplayOptions(
            on_fork=False,
            substitute=ReplaySubstitution(model="claude-sonnet-4-6", temperature=0.2),
        ),
    )
    _, request, _ = gateway.calls[0]
    assert request.model == "claude-sonnet-4-6"
    assert request.temperature == 0.2


async def test_prompt_edit_overrides_message_body(db) -> None:
    audit_store, _ = await _seed(db)
    gateway = _FakeGateway()
    replayer = TurnReplayerService(audit_store=audit_store, gateway=gateway)
    await replayer.replay(
        "t_seed",
        ReplayOptions(
            on_fork=False,
            substitute=ReplaySubstitution(prompt_edit="completely new prompt"),
        ),
    )
    _, request, _ = gateway.calls[0]
    assert request.messages[0].content == "completely new prompt"


async def test_replay_unknown_turn_raises(db) -> None:
    audit_store = AuditStore(db)
    gateway = _FakeGateway()
    replayer = TurnReplayerService(audit_store=audit_store, gateway=gateway)
    try:
        await replayer.replay("nope", ReplayOptions())
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


async def test_replay_handles_gateway_failure(db) -> None:
    audit_store, _ = await _seed(db)
    gateway = _FakeGateway()
    gateway.raise_next = RuntimeError("provider down")
    replayer = TurnReplayerService(audit_store=audit_store, gateway=gateway)
    result = await replayer.replay("t_seed", ReplayOptions(on_fork=False))
    assert result.new_response_text == ""
    assert any("provider down" in w for w in result.warnings)


async def test_on_fork_false_skips_fork_call(db) -> None:
    audit_store, _ = await _seed(db)
    gateway = _FakeGateway()
    forker = _FakeForker()
    replayer = TurnReplayerService(audit_store=audit_store, gateway=gateway, forker=forker)
    result = await replayer.replay("t_seed", ReplayOptions(on_fork=False))
    assert forker.fork_calls == []
    assert result.forked_campaign_id is None


async def test_on_fork_true_calls_forker(db) -> None:
    audit_store, _ = await _seed(db)
    gateway = _FakeGateway()
    forker = _FakeForker()
    replayer = TurnReplayerService(audit_store=audit_store, gateway=gateway, forker=forker)
    result = await replayer.replay("t_seed", ReplayOptions(on_fork=True))
    assert len(forker.fork_calls) == 1
    assert forker.fork_calls[0]["campaign_id"] == "c_1"
    assert result.forked_campaign_id == "c_forked"


async def test_set_forker_wires_after_construction(db) -> None:
    audit_store, _ = await _seed(db)
    gateway = _FakeGateway()
    replayer = TurnReplayerService(audit_store=audit_store, gateway=gateway)
    result = await replayer.replay("t_seed", ReplayOptions(on_fork=True))
    assert any("no campaign forker" in w for w in result.warnings)

    forker = _FakeForker()
    replayer.set_forker(forker)
    result = await replayer.replay("t_seed", ReplayOptions(on_fork=True))
    assert len(forker.fork_calls) == 1
