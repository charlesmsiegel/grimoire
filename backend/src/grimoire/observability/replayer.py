"""Turn replay (spec 16 §turn replay).

Replay is implemented as: load the audit, optionally fork the branch,
replay the LLM call (with optional substitutions), capture the new
response, and return a diff against the original. We do not re-run
extraction against new state — that's spec 16 §turn replay: replay
records what *would* have been extracted as a delta diff.

Replay determinism caveat: providers rarely honor ``seed`` reliably
across deployments. Callers should treat the result as "same prompt,
same model version, often-but-not-always the same response."
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from grimoire.observability.audit import AuditStore
from grimoire.types.common import TurnId
from grimoire.types.llm import CompletionRequest, Message, MessageRole
from grimoire.types.observability import (
    ReplayOptions,
    ReplayResult,
    ReplaySubstitution,
    TurnAudit,
)

logger = logging.getLogger(__name__)


class _CampaignForker(Protocol):
    async def fork_campaign(
        self,
        *,
        campaign_id: str,
        new_campaign_id: str | None = None,
        new_name: str | None = None,
    ) -> Any: ...


class _Completer(Protocol):
    async def complete(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: str | None = None,
    ) -> Any: ...


class TurnReplayerService:
    """Concrete TurnReplayer.

    The replayer is intentionally agnostic about *how* the original
    audit's prompt was assembled — it accepts any audit with a
    non-empty ``response_text`` and at least one captured ``llm_*``
    field, plus a prompt buffer (recovered from ``context_messages``
    in the audit's saved prompt_messages payload).
    """

    def __init__(
        self,
        *,
        audit_store: AuditStore,
        gateway: _Completer,
        forker: _CampaignForker | None = None,
        task: str = "replay",
    ) -> None:
        self._audit_store = audit_store
        self._gateway = gateway
        self._forker = forker
        self._task = task

    def set_forker(self, forker: _CampaignForker | None) -> None:
        """Wire the campaign forker after construction.

        The forker (the orchestrator) is built in a later phase than the
        replayer, so it is injected once it exists.
        """
        self._forker = forker

    async def replay(self, turn_id: TurnId, opts: ReplayOptions | None = None) -> ReplayResult:
        opts = opts or ReplayOptions()
        audit = await self._audit_store.get(turn_id)
        if audit is None:
            raise KeyError(f"unknown turn {turn_id!r}")

        warnings: list[str] = []
        forked_campaign_id: str | None = None
        if opts.on_fork:
            if self._forker is None:
                warnings.append("no campaign forker provided; fork skipped")
            else:
                result = await self._forker.fork_campaign(
                    campaign_id=audit.campaign_id,
                    new_name=f"replay-{turn_id[:8]}",
                )
                forked_campaign_id = getattr(result, "new_campaign_id", None)

        request = self._build_request(audit, opts.substitute)
        try:
            response = await self._gateway.complete(
                self._task,
                request,
                campaign_id=audit.campaign_id,
            )
        except Exception as exc:
            warnings.append(f"replay LLM call failed: {exc}")
            return ReplayResult(
                turn_id=turn_id,
                new_response_text="",
                delta_diff=[],
                forked_campaign_id=forked_campaign_id,
                warnings=warnings,
            )

        new_text = getattr(response, "text", None) or ""
        delta_diff = _text_diff(audit.response_text, new_text)
        return ReplayResult(
            turn_id=turn_id,
            new_response_text=new_text,
            delta_diff=delta_diff,
            forked_campaign_id=forked_campaign_id,
            warnings=warnings,
        )

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _build_request(self, audit: TurnAudit, sub: ReplaySubstitution | None) -> CompletionRequest:
        """Rebuild a CompletionRequest from an audit, applying overrides."""
        model = audit.llm_model or ""
        params = dict(audit.llm_params or {})
        if sub is not None:
            if sub.model:
                model = sub.model
            if sub.temperature is not None:
                params["temperature"] = sub.temperature

        # Prefer the verbatim ``assembled_messages`` stored in the audit
        # (captured when AuditConfig.capture_full_prompt is true). Falling
        # back to a player-input stub means the model rarely sees what it
        # originally saw, so the resulting response_text diff is mostly
        # noise — only acceptable for very old / compressed audits.
        messages: list[Message] = []
        if sub and sub.prompt_edit:
            messages = [Message(role=MessageRole.USER, content=sub.prompt_edit)]
        elif audit.assembled_messages:
            for raw in audit.assembled_messages:
                try:
                    messages.append(Message.model_validate(raw))
                except Exception:
                    logger.warning("replay: failed to rehydrate stored message: %r", raw)
            if sub and sub.extra_context:
                messages.append(Message(role=MessageRole.USER, content=sub.extra_context))
        else:
            user_body = audit.player_input or ""
            if sub and sub.extra_context:
                user_body = f"{sub.extra_context}\n\n{user_body}".strip()
            if not user_body:
                user_body = "(replay)"
            messages = [Message(role=MessageRole.USER, content=user_body)]

        return CompletionRequest(
            model=model,
            messages=messages,
            max_tokens=int(params.get("max_tokens", 4096)),
            temperature=float(params.get("temperature", 1.0)),
            seed=params.get("seed"),
        )


def _text_diff(original: str, replayed: str) -> list[dict[str, Any]]:
    """Coarse line-level diff returned as JSONable records.

    We deliberately keep this lightweight: replay UIs are expected to
    compute a proper word/character diff client-side.
    """
    if original == replayed:
        return [{"kind": "unchanged", "length": len(original)}]
    return [
        {"kind": "original", "text": original},
        {"kind": "replayed", "text": replayed},
    ]


__all__ = ["TurnReplayerService"]
