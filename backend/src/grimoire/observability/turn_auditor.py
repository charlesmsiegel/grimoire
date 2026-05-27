"""Subscribes to Orchestrator events and assembles a ``TurnAudit`` per turn.

The Orchestrator emits ``turn_started`` / ``context_built`` /
``model_response_received`` / ``deltas_extracted`` / ``turn_complete``
events with payloads carrying the relevant slots. The auditor collects
these into a buffer keyed by turn id and flushes a final ``TurnAudit``
record on ``turn_complete``. Any modules that want richer audit fields
can emit a dedicated ``turn_audit_fragment`` event whose payload is a
dict that gets merged into the buffer.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from grimoire import events
from grimoire.event_bus import Event, EventBus, Subscription
from grimoire.observability.audit import AuditStore
from grimoire.observability.config import AuditConfig
from grimoire.types.common import TurnId
from grimoire.types.observability import TurnAudit

logger = logging.getLogger(__name__)


class TurnAuditor:
    """Subscribes to the bus and writes a TurnAudit per completed turn."""

    def __init__(
        self,
        *,
        event_bus: EventBus,
        audit_store: AuditStore,
        config: AuditConfig | None = None,
    ) -> None:
        self._bus = event_bus
        self._store = audit_store
        self._config = config or AuditConfig()
        self._buffers: dict[TurnId, dict[str, Any]] = {}
        self._subs: list[Subscription] = []

    def start(self) -> None:
        """Subscribe to the relevant events. Idempotent."""
        if self._subs:
            return
        self._subs = [
            self._bus.subscribe(events.TURN_STARTED, self._on_turn_started),
            self._bus.subscribe(events.CONTEXT_BUILT, self._on_context_built),
            self._bus.subscribe(events.MODEL_RESPONSE_RECEIVED, self._on_model_response),
            self._bus.subscribe(events.LLM_RESPONSE_RECEIVED, self._on_llm_response),
            self._bus.subscribe(events.DELTAS_EXTRACTED, self._on_deltas_extracted),
            self._bus.subscribe(events.TURN_AUDIT_FRAGMENT, self._on_fragment),
            self._bus.subscribe(events.TURN_COMPLETE, self._on_turn_complete),
        ]

    def stop(self) -> None:
        for sub in self._subs:
            sub.unsubscribe()
        self._subs.clear()

    # ------------------------------------------------------------------ #
    # Handlers
    # ------------------------------------------------------------------ #

    async def _on_turn_started(self, event: Event) -> None:
        if not self._config.enabled:
            return
        turn_id = _str(event.payload.get("turn_id"))
        if not turn_id:
            return
        self._buffers[turn_id] = {
            "turn_id": turn_id,
            "campaign_id": _str(event.payload.get("campaign_id")),
            "branch_id": "main",
            "scene_id": _str(event.payload.get("scene_id")),
            "started_at": _ts(event.timestamp),
            "player_input": _str(event.payload.get("player_input")),
            "options": event.payload.get("options") or {},
        }

    async def _on_context_built(self, event: Event) -> None:
        buf = self._buf(event)
        if buf is None:
            return
        budget = event.payload.get("budget_used") or {}
        buf["context_budget_used"] = budget
        if hash_val := event.payload.get("messages_hash"):
            buf["context_messages_hash"] = hash_val
        # ``context_summary`` and ``composition_snapshot`` must be either a
        # dict with the schema's required keys or ``None``; the orchestrator
        # currently sources these from ``AssembledPrompt`` which stores them
        # as plain primitives (``str`` / ``dict``). A bare ``""`` or ``{}``
        # would fail ``TurnAudit.model_validate`` and silently drop the row,
        # so we only buffer non-empty dicts.
        summary = event.payload.get("context_summary")
        if isinstance(summary, dict) and summary:
            buf["context_summary"] = summary
        if sources := event.payload.get("context_sources"):
            buf["context_sources"] = sources
        snap = event.payload.get("composition_snapshot")
        if isinstance(snap, dict) and snap:
            buf["composition_snapshot"] = snap
        if self._config.capture_full_prompt and (
            (messages := event.payload.get("assembled_messages")) is not None
        ):
            buf["assembled_messages"] = messages

    async def _on_llm_response(self, event: Event) -> None:
        """Translate the gateway's ``llm_response_received`` payload into the
        ``llm_*`` audit fields. The gateway uses unprefixed keys (``provider``,
        ``model``, ``latency_ms``, ``retries`` …) while the audit record uses
        the ``llm_`` prefix; this handler bridges them.
        """
        buf = self._buf(event)
        if buf is None:
            return
        payload = event.payload
        if val := payload.get("provider"):
            buf["llm_provider"] = val
        if val := payload.get("model"):
            buf["llm_model"] = val
        usage = payload.get("usage") or {}
        if usage.get("input_tokens") is not None:
            buf["llm_prompt_tokens"] = int(usage.get("input_tokens") or 0)
        if usage.get("output_tokens") is not None:
            buf["llm_completion_tokens"] = int(usage.get("output_tokens") or 0)
        if (val := payload.get("cost_estimate_usd")) is not None:
            buf["llm_cost_usd"] = float(val)
        if (val := payload.get("latency_ms")) is not None:
            buf["llm_latency_ms"] = int(val)
        if (val := payload.get("retries")) is not None:
            buf["llm_retries"] = int(val)
        if val := payload.get("finish_reason"):
            buf["llm_finish_reason"] = val
        if (val := payload.get("params")) is not None:
            buf["llm_params"] = val

    async def _on_model_response(self, event: Event) -> None:
        buf = self._buf(event)
        if buf is None:
            return
        if self._config.capture_response and (text := event.payload.get("response_text")):
            buf["response_text"] = text
        for key in (
            "llm_provider",
            "llm_model",
            "llm_params",
            "llm_prompt_tokens",
            "llm_completion_tokens",
            "llm_cost_usd",
            "llm_latency_ms",
            "llm_finish_reason",
            "llm_retries",
        ):
            if key in event.payload:
                buf[key] = event.payload[key]

    async def _on_deltas_extracted(self, event: Event) -> None:
        buf = self._buf(event)
        if buf is None:
            return
        if self._config.capture_extracted_deltas and (
            (deltas := event.payload.get("deltas")) is not None
        ):
            buf["extracted_deltas"] = deltas
        if (strategies := event.payload.get("strategies_run")) is not None:
            buf["extraction_strategies_run"] = strategies
        if (duration := event.payload.get("duration_ms")) is not None:
            buf["extraction_duration_ms"] = int(duration)
        if (flags := event.payload.get("flags")) is not None:
            buf["extraction_flags"] = flags

    async def _on_fragment(self, event: Event) -> None:
        buf = self._buf(event)
        if buf is None:
            return
        for k, v in event.payload.items():
            if k in {"turn_id", "campaign_id"}:
                continue
            buf[k] = v

    async def _on_turn_complete(self, event: Event) -> None:
        buf = self._buf(event)
        if buf is None:
            return
        completed_at = _ts(event.timestamp)
        buf["completed_at"] = completed_at
        started_at = buf.get("started_at")
        if started_at:
            duration = (completed_at - started_at).total_seconds() * 1000
            buf["duration_ms"] = int(duration)
        try:
            audit = TurnAudit.model_validate(buf)
        except Exception:
            logger.exception("turn audit assembly failed")
            self._buffers.pop(buf["turn_id"], None)
            return
        try:
            await self._store.record(audit)
        except Exception:
            logger.exception("turn audit persist failed")
        finally:
            self._buffers.pop(buf["turn_id"], None)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _buf(self, event: Event) -> dict[str, Any] | None:
        if not self._config.enabled:
            return None
        turn_id = _str(event.payload.get("turn_id"))
        if not turn_id:
            return None
        buf = self._buffers.get(turn_id)
        if buf is None:
            # turn_started was missed; create a minimal stub so we can still
            # write something.
            buf = {
                "turn_id": turn_id,
                "campaign_id": _str(event.payload.get("campaign_id")),
                "branch_id": "main",
                "scene_id": _str(event.payload.get("scene_id")),
                "started_at": _ts(event.timestamp),
            }
            self._buffers[turn_id] = buf
        return buf


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _ts(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, tz=UTC)


__all__ = ["TurnAuditor"]
