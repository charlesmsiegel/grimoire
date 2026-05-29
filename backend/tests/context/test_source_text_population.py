import pytest

from grimoire.context.assembler import PromptAssembler
from grimoire.context.config import ContextBuilderConfig
from grimoire.context.tokens import cheap_estimator
from grimoire.context.types import BuiltContext, TierItem
from grimoire.types.context import ContextSource
from grimoire.types.state import ContextTier


def _src(kind: str) -> ContextSource:
    return ContextSource(kind=kind, scope="library", owner_id="x", tier=ContextTier.SPOTLIGHT)


@pytest.mark.asyncio
async def test_pack_tier_copies_item_text_onto_source():
    config = ContextBuilderConfig()
    assembler = PromptAssembler(config=config, estimator=cheap_estimator(config.chars_per_token))
    item = TierItem(
        tier=ContextTier.SPOTLIGHT,
        section="cast",
        text="winifred is present and wary.",
        source=_src("character"),
    )
    ctx = BuiltContext(
        composition=None,
        style_text="",
        content_boundaries="",
        system_meta="",
        scene_header="",
        active_pc_card="",
        active_pc_name="",
        mechanics_block="",
        commitments_block="",
        spotlight_items=[item],
        sources=[item.source],
    )
    prompt = await assembler.assemble(ctx, player_input="")
    packed = next(s for s in prompt.sources if s.kind == "character")
    assert packed.text == "winifred is present and wary."
