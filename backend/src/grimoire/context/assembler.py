"""PromptAssembler — budget allocation and canonical prompt assembly."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from grimoire.context.config import ContextBuilderConfig
from grimoire.context.errors import LockInOverflowError
from grimoire.context.tokens import TokenEstimator, estimate_tokens
from grimoire.context.types import BuiltContext, TierItem, make_source_id
from grimoire.templates import render as render_template
from grimoire.types.composition import Composition
from grimoire.types.context import AssembledPrompt, ContextSource, ToolDeclarationSpec
from grimoire.types.extraction_modes import ExtractionMode
from grimoire.types.inclusion_reasons import InclusionReason
from grimoire.types.llm import Message, MessageRole, ModelParams
from grimoire.types.mechanics import MechanicsResult
from grimoire.types.state import ContextTier


class PromptAssembler:
    def __init__(
        self,
        *,
        config: ContextBuilderConfig,
        estimator: TokenEstimator,
    ) -> None:
        self._config = config
        self._estimator = estimator

    async def assemble(self, ctx: BuiltContext, player_input: str) -> AssembledPrompt:
        messages: list[Message] = []
        budget_used: dict[ContextTier, int] = {t: 0 for t in ContextTier}

        system_text = await self._system_block(ctx)
        if system_text:
            # Cache breakpoint: the system block (style guide, content
            # boundaries, voice) is the largest reliably-static prefix across
            # turns in a scene. Marking it lets caching providers reuse it
            # instead of re-billing it every turn. Providers without explicit
            # caching ignore the hint.
            messages.append(
                Message(
                    role=MessageRole.SYSTEM,
                    content=system_text,
                    cache=True,
                    metadata={"tier": "system"},
                )
            )

        lock_in_text = await self._lock_in_block(ctx)
        lock_in_tokens = await self._tokens(lock_in_text)
        lock_budget = self._config.tiers[ContextTier.LOCK_IN].max_tokens
        if lock_in_tokens > lock_budget:
            raise LockInOverflowError(used=lock_in_tokens, budget=lock_budget)
        if lock_in_text:
            messages.append(
                Message(
                    role=MessageRole.SYSTEM,
                    content=lock_in_text,
                    metadata={"tier": ContextTier.LOCK_IN.value},
                )
            )
            budget_used[ContextTier.LOCK_IN] = lock_in_tokens

        response_fmt = self._response_format_block(ctx)
        if response_fmt:
            messages.append(
                Message(
                    role=MessageRole.SYSTEM,
                    content=response_fmt,
                    metadata={"tier": "response-format"},
                )
            )

        budget_used[ContextTier.SPOTLIGHT] = await self._pack_tier(
            ctx.spotlight_items,
            ContextTier.SPOTLIGHT,
            messages,
            label="Spotlight",
        )
        budget_used[ContextTier.BACKGROUND] = await self._pack_tier(
            ctx.background_items,
            ContextTier.BACKGROUND,
            messages,
            label="Background",
        )
        budget_used[ContextTier.ARCHIVE] = await self._pack_tier(
            ctx.archive_items,
            ContextTier.ARCHIVE,
            messages,
            label="Archive",
        )

        verbatim = self._config.lock_in_verbatim_posts
        if verbatim > 0 and ctx.recent_posts_text:
            older = self._render_older_recent(ctx, verbatim)
            if older:
                older_tokens = await self._tokens(older)
                if older_tokens <= self._config.recent_posts_budget:
                    messages.append(
                        Message(
                            role=MessageRole.SYSTEM,
                            content=older,
                            metadata={"tier": "recent-posts"},
                        )
                    )

        if player_input:
            messages.append(
                Message(
                    role=MessageRole.USER,
                    content=player_input,
                    metadata={"tier": "player-input"},
                )
            )

        if ctx.extra:
            messages.append(
                Message(role=MessageRole.USER, content=ctx.extra, metadata={"tier": "extra"})
            )

        messages = _resolve_runtime_macros(messages, ctx.active_pc_name)

        params = ModelParams(
            temperature=self._config.default_temperature,
            max_tokens=self._config.default_max_tokens,
        )
        sources = list(ctx.sources)
        await self._append_block_sources(
            sources,
            system_text=system_text,
            scene_header=ctx.scene_header,
            mechanics_block=ctx.mechanics_block,
            recent_posts_text=ctx.recent_posts_text,
            player_input=player_input,
        )
        summary = self._summary(ctx, budget_used)
        composition_snapshot = self._composition_snapshot(ctx.composition)
        return AssembledPrompt(
            messages=messages,
            params=params,
            budget_used=budget_used,
            sources=sources,
            summary=summary,
            composition_snapshot=composition_snapshot,
            messages_hash=_hash_messages(messages),
        )

    async def _append_block_sources(
        self,
        sources: list[ContextSource],
        *,
        system_text: str,
        scene_header: str,
        mechanics_block: str,
        recent_posts_text: str,
        player_input: str,
    ) -> None:
        """Emit attribution sources for the always-on prompt blocks that
        otherwise have no ``ContextSource`` — so the inspector's source list
        reconstructs the entire prompt. Attribution only: these do not affect
        ``messages`` or ``budget_used``."""
        blocks = [
            ("system", system_text, InclusionReason.SYSTEM_PROMPT),
            ("scene_header", scene_header, InclusionReason.SCENE_HEADER),
            ("mechanics", mechanics_block, InclusionReason.MECHANICS_RELEVANT),
            ("recent_posts", recent_posts_text, InclusionReason.VERBATIM_RECENT),
            ("player_input", player_input, InclusionReason.PLAYER_INPUT),
        ]
        for kind, text, reason in blocks:
            if not text:
                continue
            sources.append(
                ContextSource(
                    kind=kind,
                    scope="campaign-local",
                    owner_id=None,
                    tier=ContextTier.LOCK_IN,
                    tokens=await self._tokens(text),
                    text=text,
                    source_id=make_source_id(kind, None),
                    inclusion_reasons=[reason],
                )
            )

    def apply_extractor_mode(
        self,
        prompt: AssembledPrompt,
        *,
        extractor_mode: ExtractionMode,
        auxiliary_task: object | None,
    ) -> AssembledPrompt:
        if auxiliary_task is not None or extractor_mode == ExtractionMode.NONE:
            return prompt
        if extractor_mode == ExtractionMode.TOGETHER:
            instruction = self._tracker_instruction_text()
            new_messages = list(prompt.messages)
            new_messages.append(Message(role=MessageRole.SYSTEM, content=instruction))
            return prompt.model_copy(update={"messages": new_messages})
        if extractor_mode == ExtractionMode.TOOL_USE:
            from grimoire.extractor.tool_use import ALL_TOOLS

            tools = [
                ToolDeclarationSpec(
                    name=t.name, description=t.description, parameters=dict(t.schema)
                )
                for t in ALL_TOOLS
            ]
            return prompt.model_copy(update={"tools": tools})
        return prompt

    def _tracker_instruction_text(self) -> str:
        from grimoire.extractor.together import DELIMITER_CLOSE, DELIMITER_OPEN

        return (
            "After your prose, emit a JSON tracker block delimited by "
            f"{DELIMITER_OPEN} and {DELIMITER_CLOSE}. The JSON object must "
            "have the keys: facts (list of {text, confidence?, about?, tags?}), "
            "character_updates (list of {character_id, field, after, before?, "
            "confidence?}). Optional keys: location_updates, faction_updates, "
            "commitments_added, commitments_resolved, new_entities, advance_time, "
            "change_location. Position the tracker after the prose; do not "
            "interleave it with narrative text."
        )

    async def _system_block(self, ctx: BuiltContext) -> str:
        return render_template(
            "context_system_block",
            style_text=ctx.style_text,
            content_boundaries=ctx.content_boundaries,
            system_meta=ctx.system_meta,
            voice_corrective=ctx.voice_corrective,
            scene_mode=ctx.scene_mode,
        ).strip()

    async def _lock_in_block(self, ctx: BuiltContext) -> str:
        return render_template(
            "context_lock_in_block",
            scene_header=ctx.scene_header,
            active_pc_card=ctx.active_pc_card,
            commitments_block=ctx.commitments_block,
            mechanics_block=ctx.mechanics_block,
            verbatim_posts=self._lock_in_verbatim_posts(ctx),
        ).strip()

    def _response_format_block(self, ctx: BuiltContext) -> str:
        from grimoire.scenes.narrator_mode import PER_CHARACTER, PER_CHARACTER_MULTI_CALL

        if ctx.narrator_response_mode == PER_CHARACTER:
            return render_template(
                "context_response_format",
                present_npcs=ctx.present_npcs,
            ).strip()
        if ctx.narrator_response_mode == PER_CHARACTER_MULTI_CALL:
            return render_template(
                "context_response_format",
                variant="single_character",
                character_name=ctx.multi_call_character_name,
                character_ref=ctx.multi_call_character_ref,
            ).strip()
        return ""

    def _lock_in_verbatim_posts(self, ctx: BuiltContext) -> str:
        if not ctx.recent_posts_text:
            return ""
        all_posts = ctx.recent_posts_text.split("\n\n")
        tail = all_posts[-self._config.lock_in_verbatim_posts :]
        return "\n\n".join(tail)

    def _render_older_recent(self, ctx: BuiltContext, verbatim: int) -> str:
        if not ctx.recent_posts_text:
            return ""
        all_posts = ctx.recent_posts_text.split("\n\n")
        if len(all_posts) <= verbatim:
            return ""
        older = all_posts[: len(all_posts) - verbatim]
        if not older:
            return ""
        return render_template("context_recent_older_block", posts=older)

    async def _pack_tier(
        self,
        items: list[TierItem],
        tier: ContextTier,
        messages: list[Message],
        *,
        label: str,
    ) -> int:
        budget = self._config.tiers[tier].max_tokens
        if not items or budget <= 0:
            return 0
        pinned_items = [it for it in items if it.pinned]
        normal_items = sorted(
            (it for it in items if not it.pinned),
            key=lambda it: -it.priority,
        )
        used = 0
        packed: list[str] = []
        for item in pinned_items:
            cost = await self._tokens(item.text)
            packed.append(item.text)
            item.source.tokens = cost
            item.source.text = item.text
            used += cost
        for item in normal_items:
            cost = await self._tokens(item.text)
            if used + cost > budget:
                continue
            packed.append(item.text)
            item.source.tokens = cost
            item.source.text = item.text
            used += cost
        if not packed:
            return 0
        content = render_template("context_tier_block", label=label, items=packed)
        messages.append(
            Message(
                role=MessageRole.SYSTEM,
                content=content,
                metadata={"tier": tier.value},
            )
        )
        return used

    async def _tokens(self, text: str) -> int:
        return await estimate_tokens(text, self._estimator)

    def _summary(self, ctx: BuiltContext, budget_used: dict[ContextTier, int]) -> str:
        parts: list[str] = []
        for tier in ContextTier:
            parts.append(f"{tier.value}={budget_used.get(tier, 0)}")
        return f"Context Builder turn budget: {' '.join(parts)} (sources={len(ctx.sources)})"

    def _composition_snapshot(self, composition: Composition | None) -> dict:
        if composition is None:
            return {}
        return json.loads(composition.model_dump_json())


# ------------------------------------------------------------------ #
# Module-level helpers
# ------------------------------------------------------------------ #


def render_mechanics(results: list[MechanicsResult]) -> str:
    if not results:
        return ""
    lines = ["Mechanical results for this turn (treat as authoritative; do not contradict):"]
    for r in results:
        roll = r.roll
        res = r.result
        actor = roll.actor_ref or "?"
        target = f" vs {roll.target_ref}" if roll.target_ref else ""
        outcome = res.outcome or ("success" if res.successes > 0 else "failure")
        lines.append(
            f"- {actor} attempted {roll.kind}{target} (pool {roll.pool}). "
            f"Result: {res.successes} successes ({outcome})."
        )
        if r.summary:
            lines.append(f"  {r.summary}")
    lines.append("The narrative should reflect these outcomes.")
    return "\n".join(lines)


def render_recent_posts(posts: list[Any]) -> str:
    if not posts:
        return ""
    rendered: list[str] = []
    for post in posts:
        label = getattr(post, "author_label", None)
        if callable(label):
            label = label()
        if not label:
            label = "narrator"
        body = (getattr(post, "body", "") or "").strip()
        rendered.append(f"{label}: {body}")
    return "\n\n".join(rendered)


def _resolve_runtime_macros(messages: list[Message], active_pc_name: str) -> list[Message]:
    pc_name = active_pc_name.strip() if active_pc_name else ""
    pc_name = pc_name or "the player"
    return [
        m.model_copy(update={"content": m.content.replace("{{user}}", pc_name)}) for m in messages
    ]


def _hash_messages(messages: list[Message]) -> str:
    h = hashlib.sha256()
    for msg in messages:
        h.update(msg.role.encode("utf-8"))
        h.update(b"\x00")
        h.update(msg.content.encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest()
