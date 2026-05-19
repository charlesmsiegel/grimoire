"""Tests for the ContextInspector service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from grimoire.context import (
    ContextBuilderService,
    ContextInspector,
    HandleNotFound,
    PinTarget,
)
from grimoire.context.inspector import _InspectorConfig
from grimoire.types.context import AssembledPrompt, ContextSource
from grimoire.types.inclusion_reasons import InclusionReason
from grimoire.types.llm import Message, MessageRole, ModelParams
from grimoire.types.state import ContextTier

from .test_builder import (
    StubCharacters,
    StubContinuity,
    StubLibrary,
    StubScenes,
    StubWorld,
    _Card,
    _Scene,
)


@dataclass
class StubPinStore:
    pins: list[dict] = field(default_factory=list)

    async def list_active_context_pins(
        self,
        *,
        campaign_id: str,
        branch_id: str,
        current_turn_id: str | None = None,
    ) -> list[dict]:
        return [
            p
            for p in self.pins
            if p["campaign_id"] == campaign_id and p.get("cleared_at") is None
        ]

    async def write_context_pin(self, **kwargs: Any) -> str:
        pin_id = f"ctx_pin_{len(self.pins) + 1:03d}"
        self.pins.append(
            {
                "id": pin_id,
                "campaign_id": kwargs["campaign_id"],
                "branch_id": kwargs["branch_id"],
                "kind": kwargs["kind"],
                "target_kind": (
                    "source" if kwargs.get("target_source_id") else "entity"
                ),
                "target_source_id": kwargs.get("target_source_id"),
                "target_entity_kind": kwargs.get("target_entity_kind"),
                "target_entity_id": kwargs.get("target_entity_id"),
                "cleared_at": None,
                "expires_at_turn_id": None,
            }
        )
        return pin_id

    async def mark_context_pin_cleared(self, *, pin_id: str, cleared_by: str = "user") -> None:
        for p in self.pins:
            if p["id"] == pin_id:
                p["cleared_at"] = "now"

    async def vector_search(self, **kwargs: Any) -> list[Any]:
        return []

    async def keyword_search(self, **kwargs: Any) -> list[Any]:
        return []


def _builder(**overrides: Any) -> ContextBuilderService:
    defaults: dict[str, Any] = {
        "library": StubLibrary(),
        "characters": StubCharacters(),
        "world": StubWorld(),
        "scenes": StubScenes(),
        "continuity": StubContinuity(),
        "state_store": None,
        "gateway": None,
    }
    defaults.update(overrides)
    return ContextBuilderService(**defaults)


async def test_preview_returns_handle_and_summary() -> None:
    chars = StubCharacters(
        cards={"library:worlds/wod/characters/al": _Card(full="# Al")},
        active="library:worlds/wod/characters/al",
    )
    builder = _builder(characters=chars)
    insp = ContextInspector(builder=builder)
    handle, summary = await insp.preview(
        campaign_id="camp",
        player_input="hi",
        session_id="s1",
    )
    assert handle.startswith("ph_")
    assert summary.handle == handle
    assert summary.source_count >= 1
    assert ContextTier.LOCK_IN in summary.per_tier_budget


async def test_get_returns_assembled_prompt() -> None:
    builder = _builder()
    insp = ContextInspector(builder=builder)
    handle, _ = await insp.preview(
        campaign_id="camp", player_input="hi", session_id="s1"
    )
    prompt = await insp.get(session_id="s1", handle=handle)
    assert isinstance(prompt, AssembledPrompt)


async def test_explain_returns_per_source_reasons() -> None:
    chars = StubCharacters(
        cards={"library:worlds/wod/characters/al": _Card(full="# Al")},
        active="library:worlds/wod/characters/al",
    )
    builder = _builder(characters=chars)
    insp = ContextInspector(builder=builder)
    handle, _ = await insp.preview(
        campaign_id="camp", player_input="hi", session_id="s1"
    )
    explanations = await insp.explain(session_id="s1", handle=handle)
    pc = next(e for e in explanations if InclusionReason.PC_CARD in e.inclusion_reasons)
    assert pc.source_id.startswith("src_")


async def test_session_isolation() -> None:
    builder = _builder()
    insp = ContextInspector(builder=builder)
    handle, _ = await insp.preview(
        campaign_id="camp", player_input="hi", session_id="s_a"
    )
    with pytest.raises(HandleNotFound):
        await insp.get(session_id="s_b", handle=handle)


async def test_handle_lru_evicts_oldest() -> None:
    builder = _builder()
    insp = ContextInspector(
        builder=builder,
        config=_InspectorConfig(max_handles=3, handle_ttl_seconds=3600),
    )
    handles: list[str] = []
    for i in range(4):
        h, _ = await insp.preview(
            campaign_id="camp", player_input=f"in-{i}", session_id="s1"
        )
        handles.append(h)
    # First handle has been evicted by LRU.
    with pytest.raises(HandleNotFound):
        await insp.get(session_id="s1", handle=handles[0])
    # Most recent is still alive.
    assert await insp.get(session_id="s1", handle=handles[-1]) is not None


async def test_diff_two_handles_added_removed() -> None:
    """Adding a present character between previews shows as added."""
    npc = "library:worlds/wod/characters/winifred"
    chars1 = StubCharacters(cards={npc: _Card(full="# winifred")})
    scenes1 = StubScenes(scene=_Scene(present_character_refs=[]))
    builder1 = _builder(characters=chars1, scenes=scenes1)
    insp = ContextInspector(builder=builder1)
    handle_a, _ = await insp.preview(
        campaign_id="camp", player_input="quiet", session_id="s1"
    )
    # Swap to a builder where winifred is present.
    chars2 = StubCharacters(cards={npc: _Card(full="# winifred")})
    scenes2 = StubScenes(scene=_Scene(present_character_refs=[npc]))
    insp.builder = _builder(characters=chars2, scenes=scenes2)
    handle_b, _ = await insp.preview(
        campaign_id="camp", player_input="winifred speaks", session_id="s1"
    )
    diff = await insp.diff(a=handle_a, b=handle_b, session_id="s1")
    assert any(s.owner_id == npc for s in diff.entities_added)
    # Removing too: handle A's set vs B's set.


async def test_diff_budget_shifts_per_tier() -> None:
    builder = _builder()
    insp = ContextInspector(builder=builder)
    ha, _ = await insp.preview(
        campaign_id="camp", player_input="a", session_id="s1"
    )
    hb, _ = await insp.preview(
        campaign_id="camp", player_input="b", session_id="s1"
    )
    diff = await insp.diff(a=ha, b=hb, session_id="s1")
    assert set(diff.budget_shifts.keys()) == set(ContextTier)


async def test_diff_against_turn_uses_observability_audit() -> None:
    # Stub a tiny observability with one audit.
    class _Audit:
        assembled_messages = [Message(role=MessageRole.SYSTEM, content="hello")]
        context_sources = [
            ContextSource(
                kind="character",
                scope="library",
                owner_id="library:x/y",
                tier=ContextTier.SPOTLIGHT,
                source_id="src_abc",
            )
        ]
        context_budget_used: dict = {ContextTier.SPOTLIGHT: 100}
        context_messages_hash = "hash"
        model_params = ModelParams()

    class _Obs:
        async def get_turn_audit(self, turn_id: str) -> Any:
            return _Audit()

    builder = _builder()
    insp = ContextInspector(builder=builder, observability=_Obs())
    handle, _ = await insp.preview(campaign_id="camp", player_input="x", session_id="s1")
    diff = await insp.diff(a="t_42", b=handle, session_id="s1")
    # The audit had src_abc, the preview probably doesn't → removed.
    assert any(s.source_id == "src_abc" for s in diff.entities_removed)


async def test_diff_against_turn_without_observability_raises() -> None:
    """If diff is called with a turn id but no observability is wired,
    the error is loud — not a silent fallthrough."""
    builder = _builder()
    insp = ContextInspector(builder=builder, observability=None)
    handle, _ = await insp.preview(campaign_id="camp", player_input="x", session_id="s1")
    with pytest.raises(RuntimeError, match="observability"):
        await insp.diff(a="t_42", b=handle, session_id="s1")


async def test_pin_writes_to_store_and_returns_id() -> None:
    builder = _builder()
    store = StubPinStore()
    insp = ContextInspector(builder=builder, store=store)
    pin_id = await insp.pin(
        campaign_id="camp",
        target=PinTarget(entity_kind="character", entity_id="char_henry"),
    )
    assert pin_id.startswith("ctx_pin_")
    assert len(store.pins) == 1
    assert store.pins[0]["kind"] == "pin"


async def test_exclude_writes_with_exclude_kind() -> None:
    builder = _builder()
    store = StubPinStore()
    insp = ContextInspector(builder=builder, store=store)
    await insp.exclude(
        campaign_id="camp",
        target=PinTarget(source_id="src_abc"),
    )
    assert store.pins[0]["kind"] == "exclude"
    assert store.pins[0]["target_kind"] == "source"


async def test_clear_pin_marks_cleared() -> None:
    builder = _builder()
    store = StubPinStore()
    insp = ContextInspector(builder=builder, store=store)
    pin_id = await insp.pin(
        campaign_id="camp", target=PinTarget(entity_kind="x", entity_id="y")
    )
    await insp.clear_pin(pin_id=pin_id)
    assert store.pins[0]["cleared_at"] == "now"


async def test_pin_without_store_raises() -> None:
    builder = _builder()
    insp = ContextInspector(builder=builder, store=None)
    with pytest.raises(RuntimeError):
        await insp.pin(
            campaign_id="camp", target=PinTarget(entity_kind="x", entity_id="y")
        )


async def test_preview_is_byte_identical_to_canonical_build() -> None:
    """Determinism: ``get(handle)`` equals ``builder.build(same inputs)``."""
    chars = StubCharacters(
        cards={"library:worlds/wod/characters/al": _Card(full="# Al")},
        active="library:worlds/wod/characters/al",
    )
    builder = _builder(characters=chars)
    insp = ContextInspector(builder=builder)
    handle, _ = await insp.preview(
        campaign_id="camp",
        player_input="hi",
        session_id="s1",
        branch_id="camp:main",
    )
    cached = await insp.get(session_id="s1", handle=handle)
    canonical = await builder.build(
        player_input="hi",
        campaign_id="camp",
        branch_id="camp:main",
    )
    assert cached.messages_hash == canonical.messages_hash
