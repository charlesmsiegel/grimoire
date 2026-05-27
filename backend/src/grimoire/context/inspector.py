"""ContextInspector — live preview / explain / pin / diff service.

A pre-flight counterpart to the post-hoc ``GET /turns/{turn_id}/prompt``
endpoint. The inspector lets clients ask "what would the next turn's prompt
look like if I sent this input?" without committing the turn, then drill
into per-source inclusion reasons, pin / exclude entities, and diff two
previews (or a preview against a prior canonical turn).

Handles are in-memory, session-scoped, LRU-bounded with idle TTL. The
underlying ``AssembledPrompt`` is byte-identical to what the canonical
builder would produce for the same inputs — preview reuses the builder.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from grimoire.context.builder import ContextBuilderService
from grimoire.types.common import CampaignId, TurnId
from grimoire.types.context import AssembledPrompt, ContextSource
from grimoire.types.inclusion_reasons import InclusionReason
from grimoire.types.state import ContextTier

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------#
# Public payload types                                                        #
# ---------------------------------------------------------------------------#


class TierTokenSummary(BaseModel):
    """Per-tier token roll-up for a preview."""

    tier: ContextTier
    used: int
    budget: int

    @property
    def fill_ratio(self) -> float:
        return 0.0 if self.budget <= 0 else min(1.0, self.used / self.budget)


class PreviewSummary(BaseModel):
    """Quick summary returned alongside a preview handle."""

    handle: str
    per_tier_tokens: dict[ContextTier, int] = Field(default_factory=dict)
    per_tier_budget: dict[ContextTier, int] = Field(default_factory=dict)
    source_count: int = 0
    messages_hash: str = ""


class ContextSourceExplanation(BaseModel):
    """One row in the explain payload."""

    source_id: str
    owner_id: str | None
    kind: str
    scope: str
    tier: ContextTier
    library_version: int | None = None
    inclusion_reasons: list[InclusionReason] = Field(default_factory=list)
    tokens: int = 0
    summary: str = ""


class SourceVersionChange(BaseModel):
    source_id: str
    before: int | None = None
    after: int | None = None


class ContextDiff(BaseModel):
    entities_added: list[ContextSourceExplanation] = Field(default_factory=list)
    entities_removed: list[ContextSourceExplanation] = Field(default_factory=list)
    entities_changed_tier: list[ContextSourceExplanation] = Field(default_factory=list)
    budget_shifts: dict[ContextTier, int] = Field(default_factory=dict)
    source_version_changes: list[SourceVersionChange] = Field(default_factory=list)


class HandleNotFound(KeyError):
    """Raised when a session/handle pair has expired or never existed."""


class PinTarget(BaseModel):
    """Identifies the thing to pin or exclude.

    Either ``source_id`` (precise — a specific assembled chunk) or
    ``entity_kind`` + ``entity_id`` (broad — any source matching the
    (kind, owner_id) tuple).
    """

    source_id: str | None = None
    entity_kind: str | None = None
    entity_id: str | None = None


# ---------------------------------------------------------------------------#
# Service                                                                     #
# ---------------------------------------------------------------------------#


@dataclass
class _InspectorConfig:
    max_handles: int = 50
    handle_ttl_seconds: int = 900


@dataclass
class _CachedPreview:
    prompt: AssembledPrompt
    campaign_id: str
    created_at: float = field(default_factory=time.time)


class ContextInspector:
    """Live-preview / explain / pin / diff over the Context Builder."""

    def __init__(
        self,
        *,
        builder: ContextBuilderService,
        store: Any | None = None,
        observability: Any | None = None,
        config: _InspectorConfig | None = None,
    ) -> None:
        self.builder = builder
        self.store = store
        self._observability = observability
        self.config = config or _InspectorConfig()
        # Key: (session_id, handle). LRU-ordered insertion.
        self._handles: OrderedDict[tuple[str, str], _CachedPreview] = OrderedDict()

    # -------- preview / get / explain ---------------------------------------

    async def preview(
        self,
        *,
        campaign_id: CampaignId,
        player_input: str,
        session_id: str,
        pc_ref: str | None = None,
    ) -> tuple[str, PreviewSummary]:
        prompt = await self.builder.build(
            player_input=player_input,
            campaign_id=campaign_id,
            pc_ref=pc_ref,
        )
        handle = self._make_handle()
        self._handles[(session_id, handle)] = _CachedPreview(
            prompt=prompt,
            campaign_id=campaign_id,
        )
        self._evict_old()
        return handle, self._make_summary(handle, prompt)

    async def get(self, *, session_id: str, handle: str) -> AssembledPrompt:
        cached = self._handles.get((session_id, handle))
        if cached is None:
            raise HandleNotFound(handle)
        # Touch LRU.
        self._handles.move_to_end((session_id, handle))
        return cached.prompt

    async def explain(
        self,
        *,
        session_id: str,
        handle: str,
    ) -> list[ContextSourceExplanation]:
        prompt = await self.get(session_id=session_id, handle=handle)
        return [self._to_explain(s) for s in prompt.sources]

    # -------- diff ----------------------------------------------------------

    async def diff(
        self,
        *,
        a: str | TurnId,
        b: str | TurnId,
        session_id: str | None = None,
    ) -> ContextDiff:
        prompt_a = await self._resolve_to_prompt(a, session_id)
        prompt_b = await self._resolve_to_prompt(b, session_id)
        sources_a = {s.source_id: s for s in prompt_a.sources if s.source_id}
        sources_b = {s.source_id: s for s in prompt_b.sources if s.source_id}
        added = [self._to_explain(s) for sid, s in sources_b.items() if sid not in sources_a]
        removed = [self._to_explain(s) for sid, s in sources_a.items() if sid not in sources_b]
        changed_tier = [
            self._to_explain(s)
            for sid, s in sources_b.items()
            if sid in sources_a and sources_a[sid].tier != s.tier
        ]
        budget_shifts: dict[ContextTier, int] = {}
        for tier in ContextTier:
            before = int(prompt_a.budget_used.get(tier, 0) or 0)
            after = int(prompt_b.budget_used.get(tier, 0) or 0)
            budget_shifts[tier] = after - before
        version_changes: list[SourceVersionChange] = []
        for sid, before in sources_a.items():
            after = sources_b.get(sid)
            if after is None:
                continue
            if (before.library_version or None) != (after.library_version or None):
                version_changes.append(
                    SourceVersionChange(
                        source_id=sid,
                        before=before.library_version,
                        after=after.library_version,
                    )
                )
        return ContextDiff(
            entities_added=added,
            entities_removed=removed,
            entities_changed_tier=changed_tier,
            budget_shifts=budget_shifts,
            source_version_changes=version_changes,
        )

    # -------- pin / exclude -------------------------------------------------

    async def pin(
        self,
        *,
        campaign_id: CampaignId,
        target: PinTarget,
        branch_id: str | None = None,
        ttl_turns: int | None = None,
        created_at_turn_id: str | None = None,
        actor: str = "user",
    ) -> str:
        return await self._write_pin(
            kind="pin",
            campaign_id=campaign_id,
            target=target,
            branch_id=branch_id,
            ttl_turns=ttl_turns,
            created_at_turn_id=created_at_turn_id,
            actor=actor,
        )

    async def exclude(
        self,
        *,
        campaign_id: CampaignId,
        target: PinTarget,
        branch_id: str | None = None,
        ttl_turns: int | None = None,
        created_at_turn_id: str | None = None,
        actor: str = "user",
    ) -> str:
        return await self._write_pin(
            kind="exclude",
            campaign_id=campaign_id,
            target=target,
            branch_id=branch_id,
            ttl_turns=ttl_turns,
            created_at_turn_id=created_at_turn_id,
            actor=actor,
        )

    async def clear_pin(self, *, pin_id: str, actor: str = "user") -> None:
        if self.store is None:
            raise RuntimeError("inspector has no state_store wired")
        await self.store.mark_context_pin_cleared(pin_id=pin_id, cleared_by=actor)

    async def list_pins(
        self,
        *,
        campaign_id: CampaignId,
        branch_id: str | None = None,
        current_turn_id: str | None = None,
    ) -> list[dict]:
        if self.store is None:
            return []
        bid = branch_id or f"{campaign_id}:{self.config.default_branch_suffix}"
        return await self.store.list_active_context_pins(
            campaign_id=campaign_id,
            branch_id=bid,
            current_turn_id=current_turn_id,
        )

    # -------- internals -----------------------------------------------------

    async def _write_pin(
        self,
        *,
        kind: str,
        campaign_id: CampaignId,
        target: PinTarget,
        branch_id: str | None,
        ttl_turns: int | None,
        created_at_turn_id: str | None,
        actor: str,
    ) -> str:
        if self.store is None:
            raise RuntimeError("inspector has no state_store wired")
        if not target.source_id and not (target.entity_kind and target.entity_id):
            raise ValueError("pin/exclude target needs source_id or (entity_kind, entity_id)")
        bid = branch_id or f"{campaign_id}:{self.config.default_branch_suffix}"
        return await self.store.write_context_pin(
            campaign_id=campaign_id,
            branch_id=bid,
            kind=kind,
            target_source_id=target.source_id,
            target_entity_kind=target.entity_kind,
            target_entity_id=target.entity_id,
            created_at_turn_id=created_at_turn_id,
            ttl_turns=ttl_turns,
            created_by=actor,
        )

    async def _resolve_to_prompt(
        self,
        ref: str | TurnId,
        session_id: str | None,
    ) -> AssembledPrompt:
        # Handles look like "ph_<hex>"; anything else is treated as a turn id.
        if isinstance(ref, str) and ref.startswith("ph_"):
            if session_id is None:
                raise ValueError("diff against a handle requires session_id")
            return await self.get(session_id=session_id, handle=ref)
        return await self._load_turn_prompt(ref)

    async def _load_turn_prompt(self, turn_id: str | TurnId) -> AssembledPrompt:
        """Reconstruct an AssembledPrompt from observability audit storage.

        We pull just enough to fuel diff: messages list, sources, budget,
        hash. Anything else stays empty.
        """
        observability = getattr(self, "_observability", None)
        if observability is None:
            raise RuntimeError(
                "inspector has no observability wired; cannot load turn audit for diff"
            )
        audit = await observability.get_turn_audit(str(turn_id))
        assembled_messages = list(getattr(audit, "assembled_messages", []) or [])
        sources = list(getattr(audit, "context_sources", []) or [])
        budget = dict(getattr(audit, "context_budget_used", {}) or {})
        # Normalize budget keys to ContextTier
        norm_budget: dict[ContextTier, int] = {}
        for k, v in budget.items():
            try:
                norm_budget[ContextTier(k)] = int(v)
            except (ValueError, TypeError):
                continue
        return AssembledPrompt(
            messages=assembled_messages,
            params=getattr(audit, "model_params", None) or _default_params(),
            budget_used=norm_budget,
            sources=sources,
            messages_hash=getattr(audit, "context_messages_hash", "") or "",
        )

    def _to_explain(self, source: ContextSource) -> ContextSourceExplanation:
        return ContextSourceExplanation(
            source_id=source.source_id,
            owner_id=source.owner_id,
            kind=source.kind,
            scope=source.scope,
            tier=source.tier,
            library_version=source.library_version,
            inclusion_reasons=list(source.inclusion_reasons),
            tokens=source.tokens,
            summary=source.summary,
        )

    def _make_summary(self, handle: str, prompt: AssembledPrompt) -> PreviewSummary:
        per_tier_budget: dict[ContextTier, int] = {}
        cfg_tiers = getattr(self.builder, "_config", None)
        if cfg_tiers is not None:
            for tier, budget in getattr(cfg_tiers, "tiers", {}).items():
                per_tier_budget[tier] = int(getattr(budget, "max_tokens", 0))
        return PreviewSummary(
            handle=handle,
            per_tier_tokens={t: int(v) for t, v in prompt.budget_used.items()},
            per_tier_budget=per_tier_budget,
            source_count=len(prompt.sources),
            messages_hash=prompt.messages_hash,
        )

    def _make_handle(self) -> str:
        return f"ph_{uuid.uuid4().hex[:16]}"

    def _evict_old(self) -> None:
        now = time.time()
        ttl = self.config.handle_ttl_seconds
        # TTL pass.
        stale = [k for k, c in self._handles.items() if now - c.created_at > ttl]
        for k in stale:
            del self._handles[k]
        # LRU pass.
        while len(self._handles) > self.config.max_handles:
            self._handles.popitem(last=False)


def _default_params() -> Any:
    from grimoire.types.llm import ModelParams

    return ModelParams()


__all__ = [
    "ContextDiff",
    "ContextInspector",
    "ContextSourceExplanation",
    "HandleNotFound",
    "PinTarget",
    "PreviewSummary",
    "SourceVersionChange",
    "TierTokenSummary",
    "_InspectorConfig",
]
