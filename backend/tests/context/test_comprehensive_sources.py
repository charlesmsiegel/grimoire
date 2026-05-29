import pytest

from grimoire.context.assembler import PromptAssembler
from grimoire.context.config import ContextBuilderConfig
from grimoire.context.inspector import ContextInspector
from grimoire.context.tokens import cheap_estimator
from grimoire.context.types import BuiltContext, TierItem
from grimoire.types.context import ContextSource
from grimoire.types.inclusion_reasons import InclusionReason
from grimoire.types.state import ContextTier


def _assembler(config: ContextBuilderConfig | None = None) -> PromptAssembler:
    config = config or ContextBuilderConfig()
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


@pytest.mark.asyncio
async def test_recent_posts_source_omits_over_budget_older_posts():
    # Budget of 1 token forces the older block to be dropped from messages;
    # the recent_posts source must then claim only the verbatim tail.
    config = ContextBuilderConfig(recent_posts_budget=1, lock_in_verbatim_posts=2)
    ctx = _ctx(recent_posts_text="p1\n\np2\n\np3\n\np4")
    prompt = await _assembler(config).assemble(ctx, player_input="")
    recent = next(s for s in prompt.sources if s.kind == "recent_posts")
    assert recent.text == "p3\n\np4"
    assert "p1" not in recent.text
    assert "p2" not in recent.text


@pytest.mark.asyncio
async def test_source_text_resolves_user_macro():
    item = TierItem(
        tier=ContextTier.SPOTLIGHT,
        section="cast",
        text="{{user}} draws the blade.",
        source=ContextSource(
            kind="character", scope="library", owner_id="x", tier=ContextTier.SPOTLIGHT
        ),
    )
    ctx = _ctx(spotlight_items=[item], sources=[item.source], active_pc_name="Alistair")
    prompt = await _assembler().assemble(ctx, player_input="")
    char = next(s for s in prompt.sources if s.kind == "character")
    assert char.text == "Alistair draws the blade."
    assert "{{user}}" not in char.text


@pytest.mark.asyncio
async def test_response_format_block_emitted_as_source():
    sources: list[ContextSource] = []
    await _assembler()._append_block_sources(
        sources,
        system_text="",
        response_format="Respond as each NPC in turn.",
        scene_header="",
        mechanics_block="",
        recent_posts_text="",
        player_input="",
    )
    rf = next(s for s in sources if s.kind == "response_format")
    assert rf.text == "Respond as each NPC in turn."
    assert rf.inclusion_reasons == [InclusionReason.RESPONSE_FORMAT]


def test_to_explain_includes_text():
    inspector = ContextInspector(builder=object())
    source = ContextSource(
        kind="character",
        scope="library",
        owner_id="x",
        tier=ContextTier.SPOTLIGHT,
        text="precise body",
    )
    assert inspector._to_explain(source).text == "precise body"
