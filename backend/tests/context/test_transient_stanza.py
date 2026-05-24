"""Context builder emits a spotlight-tier transient stanza per present character."""

from __future__ import annotations

from datetime import UTC, datetime

from grimoire.context.builder import ContextBuilderService as ContextBuilder
from grimoire.context.types import TierItem
from grimoire.context.config import ContextBuilderConfig
from grimoire.types.context import ContextSource
from grimoire.types.state import ContextTier
from grimoire.types.transient import Provenance, TransientValue


class _StubTransientState:
    """Returns canned bundles by entity_id."""

    def __init__(self, bundles: dict[str, dict[str, TransientValue]]) -> None:
        self.bundles = bundles
        self.calls: list[tuple[str, str]] = []

    async def get(
        self, campaign_id, entity_kind, entity_id, field=None, *, branch_id=None, for_observer=None
    ):
        self.calls.append((entity_id, str(for_observer) if for_observer else ""))
        bundle = self.bundles.get(entity_id, {})
        if field is None:
            return bundle
        return bundle.get(field)


def _val(field: str, value: object) -> TransientValue:
    return TransientValue(
        id=1,
        entity_id="char_florence",
        field=field,
        value=value,
        provenance=Provenance.EXTRACTOR_AUTO,
        confidence=0.9,
        source_post_id=None,
        created_at=datetime.now(UTC),
        expires_at=None,
        in_game_at=None,
        decayed=False,
    )


def _build_builder(transient_state):
    return ContextBuilder(
        library=object(),
        characters=object(),
        world=object(),
        scenes=object(),
        continuity=object(),
        transient_state=transient_state,
        config=ContextBuilderConfig(),
    )


async def test_returns_none_when_no_transient_state():
    builder = ContextBuilder(
        library=object(),
        characters=object(),
        world=object(),
        scenes=object(),
        continuity=object(),
    )
    item = await builder._maybe_transient_stanza_item(
        ref="char_florence", campaign_id="c1", active_pc_ref=None
    )
    assert item is None


async def test_returns_none_when_bundle_empty():
    builder = _build_builder(_StubTransientState({"char_florence": {}}))
    item = await builder._maybe_transient_stanza_item(
        ref="char_florence", campaign_id="c1", active_pc_ref=None
    )
    assert item is None


async def test_emits_tier_item_with_stanza():
    bundle = {
        "mood": _val("mood", "guarded"),
        "intent": _val("intent", "hide letter"),
        "current_action": _val("current_action", "fastening her cloak"),
    }
    stub = _StubTransientState({"char_florence": bundle})
    builder = _build_builder(stub)
    item = await builder._maybe_transient_stanza_item(
        ref="char_florence", campaign_id="c1", active_pc_ref=None
    )
    assert item is not None
    assert isinstance(item, TierItem)
    assert item.tier == ContextTier.SPOTLIGHT
    assert item.section == "transient"
    assert "current state:" in item.text
    assert "mood: guarded" in item.text
    assert "intent: hide letter" in item.text
    assert "action: fastening her cloak" in item.text
    assert isinstance(item.source, ContextSource)


async def test_uses_pc_owner_observer_for_active_pc():
    bundle = {"mood": _val("mood", "calm")}
    stub = _StubTransientState({"pc_anna": bundle})
    builder = _build_builder(stub)
    await builder._maybe_transient_stanza_item(
        ref="pc_anna", campaign_id="c1", active_pc_ref="pc_anna"
    )
    assert stub.calls
    # The recorded observer string carries "pc_owner".
    observer_strs = [c[1] for c in stub.calls]
    assert any("pc_owner" in s for s in observer_strs)


async def test_uses_other_pc_observer_for_npc():
    bundle = {"mood": _val("mood", "calm")}
    stub = _StubTransientState({"npc_x": bundle})
    builder = _build_builder(stub)
    await builder._maybe_transient_stanza_item(
        ref="npc_x", campaign_id="c1", active_pc_ref="pc_anna"
    )
    observer_strs = [c[1] for c in stub.calls]
    assert any("other_pc" in s for s in observer_strs)


async def test_failures_in_service_swallowed():
    class _Boom:
        async def get(self, *_a, **_kw):
            raise RuntimeError("nope")

    builder = _build_builder(_Boom())
    item = await builder._maybe_transient_stanza_item(
        ref="char_x", campaign_id="c1", active_pc_ref=None
    )
    assert item is None
