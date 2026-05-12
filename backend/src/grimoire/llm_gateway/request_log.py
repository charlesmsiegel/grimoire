"""Writes one row per LLM call to `llm_requests`.

The Observability module owns the full audit trail; this is the slim
per-call record the gateway is responsible for producing (provider,
model, tokens, cost, latency, retries, fallback flag).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from grimoire.storage.db import Database
from grimoire.types.common import CampaignId, TurnId
from grimoire.types.llm import CompletionRequest, TokenUsage


def request_hash(request: CompletionRequest) -> str:
    """Deterministic hash over the canonical fields of a completion request."""
    payload = {
        "model": request.model,
        "system": request.system,
        "messages": [
            {"role": m.role.value, "content": m.content, "name": m.name} for m in request.messages
        ],
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "stop_sequences": list(request.stop_sequences),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class LLMRequestLog:
    def __init__(
        self,
        db: Database,
        *,
        log_response_text: bool = False,
        response_excerpt_chars: int = 200,
    ) -> None:
        self._db = db
        self._log_response_text = log_response_text
        self._response_excerpt_chars = response_excerpt_chars

    async def record(
        self,
        *,
        task: str,
        provider_id: str,
        model: str,
        usage: TokenUsage | None = None,
        cost_usd: float | None = None,
        latency_ms: int = 0,
        retries: int = 0,
        fallback_used: bool = False,
        request_hash: str | None = None,
        response_text: str | None = None,
        error: str | None = None,
        campaign_id: CampaignId | None = None,
        turn_id: TurnId | None = None,
    ) -> str:
        excerpt = self._excerpt(response_text)
        record_id = uuid.uuid4().hex
        prompt_tokens = usage.input_tokens if usage else None
        completion_tokens = usage.output_tokens if usage else None
        total = usage.total_tokens if usage else None
        if usage and not total:
            total = (prompt_tokens or 0) + (completion_tokens or 0)
        await self._db.execute(
            "INSERT INTO llm_requests ("
            "id, campaign_id, turn_id, task, provider, model, "
            "prompt_tokens, completion_tokens, total_tokens, cost_usd, latency_ms, "
            "retries, fallback_used, request_hash, response_excerpt, error, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_id,
                campaign_id,
                turn_id,
                task,
                provider_id,
                model,
                prompt_tokens,
                completion_tokens,
                total,
                cost_usd,
                int(latency_ms),
                int(retries),
                1 if fallback_used else 0,
                request_hash,
                excerpt,
                error,
                datetime.now(UTC).isoformat(),
            ),
        )
        return record_id

    def _excerpt(self, response_text: str | None) -> str | None:
        if response_text is None:
            return None
        if not self._log_response_text:
            return None
        if self._response_excerpt_chars <= 0:
            return ""
        return response_text[: self._response_excerpt_chars]


__all__ = ["LLMRequestLog", "request_hash"]
