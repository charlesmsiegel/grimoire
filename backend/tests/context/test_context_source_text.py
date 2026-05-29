from grimoire.types.context import ContextSource
from grimoire.types.state import ContextTier


def test_context_source_has_text_field_defaulting_empty():
    src = ContextSource(kind="character", scope="library", owner_id="x", tier=ContextTier.SPOTLIGHT)
    assert src.text == ""


def test_context_source_text_round_trips_through_model_dump():
    src = ContextSource(
        kind="character",
        scope="library",
        owner_id="x",
        tier=ContextTier.SPOTLIGHT,
        text="exact rendered text",
    )
    assert src.model_dump(mode="json")["text"] == "exact rendered text"
