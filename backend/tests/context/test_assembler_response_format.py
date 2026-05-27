"""Tests for response format message injection in PromptAssembler."""

from __future__ import annotations

import pytest

from grimoire.context.assembler import PromptAssembler
from grimoire.context.config import ContextBuilderConfig
from grimoire.context.tokens import cheap_estimator
from grimoire.context.types import BuiltContext
from grimoire.scenes.narrator_mode import ALL_AT_ONCE, PER_CHARACTER, PER_CHARACTER_MULTI_CALL


def _make_ctx(**overrides) -> BuiltContext:
    defaults = dict(
        composition=None,
        style_text="",
        content_boundaries="",
        system_meta="",
        scene_header="Test scene",
        active_pc_card="Test PC",
        active_pc_name="Player",
        mechanics_block="",
        commitments_block="",
        voice_corrective="",
        narrator_response_mode=ALL_AT_ONCE,
        present_npcs=[],
    )
    defaults.update(overrides)
    return BuiltContext(**defaults)


@pytest.fixture
def assembler() -> PromptAssembler:
    return PromptAssembler(config=ContextBuilderConfig(), estimator=cheap_estimator())


@pytest.mark.asyncio
async def test_no_response_format_for_all_at_once(assembler: PromptAssembler) -> None:
    ctx = _make_ctx(narrator_response_mode=ALL_AT_ONCE)
    prompt = await assembler.assemble(ctx, "player input")
    for msg in prompt.messages:
        assert "Response format" not in msg.content


@pytest.mark.asyncio
async def test_response_format_injected_for_per_character(assembler: PromptAssembler) -> None:
    npcs = [{"name": "Alice", "ref": "worlds/w/characters/alice"}]
    ctx = _make_ctx(narrator_response_mode=PER_CHARACTER, present_npcs=npcs)
    prompt = await assembler.assemble(ctx, "player input")
    contents = [msg.content for msg in prompt.messages]
    response_fmt = [c for c in contents if "Response format" in c]
    assert len(response_fmt) == 1
    assert "Alice" in response_fmt[0]
    assert '<character ref="' in response_fmt[0]


@pytest.mark.asyncio
async def test_response_format_injected_for_multi_call(assembler: PromptAssembler) -> None:
    ctx = _make_ctx(
        narrator_response_mode=PER_CHARACTER_MULTI_CALL,
        present_npcs=[],
        multi_call_character_name="Alice",
        multi_call_character_ref="worlds/w/characters/alice",
    )
    prompt = await assembler.assemble(ctx, "player input")
    contents = [msg.content for msg in prompt.messages]
    response_fmt = [c for c in contents if "Response format" in c]
    assert len(response_fmt) == 1
    assert "Alice" in response_fmt[0]


@pytest.mark.asyncio
async def test_response_format_appears_after_lock_in(assembler: PromptAssembler) -> None:
    npcs = [{"name": "Alice", "ref": "worlds/w/characters/alice"}]
    ctx = _make_ctx(narrator_response_mode=PER_CHARACTER, present_npcs=npcs)
    prompt = await assembler.assemble(ctx, "player input")
    tiers = [msg.metadata.get("tier") for msg in prompt.messages]
    lock_in_idx = next(i for i, t in enumerate(tiers) if t == "lock-in")
    fmt_idx = next(i for i, t in enumerate(tiers) if t == "response-format")
    assert fmt_idx == lock_in_idx + 1
