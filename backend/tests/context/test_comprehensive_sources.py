import pytest

from grimoire.context.assembler import PromptAssembler
from grimoire.context.config import ContextBuilderConfig
from grimoire.context.tokens import cheap_estimator
from grimoire.context.types import BuiltContext
from grimoire.types.state import ContextTier


def _assembler() -> PromptAssembler:
    config = ContextBuilderConfig()
    return PromptAssembler(config=config, estimator=cheap_estimator(config.chars_per_token))


def _ctx(**overrides) -> BuiltContext:
    base = dict(
        composition=None,
        style_text="Write vividly.",
        content_boundaries="",
        system_meta="Worlds in play: WoD (wod)",
        scene_header="The Docks, night.",
        active_pc_card="",
        active_pc_name="",
        mechanics_block="",
        commitments_block="",
        recent_posts_text="narrator: Fog rolls in.\n\nMara: You're late.",
        sources=[],
    )
    base.update(overrides)
    return BuiltContext(**base)


@pytest.mark.asyncio
async def test_emits_system_scene_recent_and_player_input_sources():
    prompt = await _assembler().assemble(_ctx(), player_input="I step onto the pier.")
    kinds = {s.kind: s for s in prompt.sources}
    assert "system" in kinds
    assert kinds["system"].text != ""
    assert kinds["scene_header"].text == "The Docks, night."
    assert kinds["recent_posts"].text.startswith("narrator: Fog rolls in.")
    assert kinds["player_input"].text == "I step onto the pier."
    assert kinds["player_input"].tier == ContextTier.LOCK_IN


@pytest.mark.asyncio
async def test_skips_empty_blocks():
    prompt = await _assembler().assemble(
        _ctx(
            scene_header="",
            recent_posts_text="",
            system_meta="",
            style_text="",
            content_boundaries="",
        ),
        player_input="",
    )
    kinds = {s.kind for s in prompt.sources}
    assert "scene_header" not in kinds
    assert "recent_posts" not in kinds
    assert "player_input" not in kinds


@pytest.mark.asyncio
async def test_new_sources_do_not_change_messages_hash_or_budget():
    ctx = _ctx()
    prompt = await _assembler().assemble(ctx, player_input="hello")
    # budget_used only ever has the four real tiers.
    assert set(prompt.budget_used.keys()) == set(ContextTier)
    # messages_hash is derived from messages only; recompute and confirm.
    from grimoire.context.assembler import _hash_messages

    assert prompt.messages_hash == _hash_messages(prompt.messages)
    # The system/recent/player sources are attribution only — none of their
    # ids leak into the message metadata.
    assert all("source_id" not in (m.metadata or {}) for m in prompt.messages)
