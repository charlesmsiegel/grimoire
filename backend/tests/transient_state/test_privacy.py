"""Privacy resolution: per-character frontmatter + POV mode + observer kinds."""

from __future__ import annotations

import pytest

from grimoire.transient_state.privacy import (
    PrivacyResolver,
    PrivacyView,
    apply_privacy_filter,
    resolve_privacy,
)
from grimoire.types.characters import (
    Character,
    CharacterPrivacy,
    CharacterRole,
    InternalThoughtsPrivacy,
)
from grimoire.types.transient import EntityKind, ObserverKind, Provenance, TransientValue


def _make_character(role: CharacterRole = CharacterRole.MAJOR_NPC, **overrides):
    privacy = CharacterPrivacy(internal_thoughts=InternalThoughtsPrivacy(**overrides))
    return Character(
        id="char_florence",
        name="winifred",
        role=role,
        privacy=privacy,
    )


def test_defaults_all_true_for_author():
    c = _make_character()
    view = resolve_privacy(c, observer=ObserverKind.AUTHOR)
    assert view == PrivacyView(hud=True, inline=True, context=True)


def test_audience_with_default_frontmatter_sees_all():
    c = _make_character()
    view = resolve_privacy(c, observer=ObserverKind.AUDIENCE)
    assert view == PrivacyView(hud=True, inline=True, context=True)


def test_audience_blocked_when_character_marks_private():
    c = _make_character(
        surface_in_hud=False,
        surface_inline=False,
        surface_in_context=False,
    )
    view = resolve_privacy(c, observer=ObserverKind.AUDIENCE)
    assert view == PrivacyView(hud=False, inline=False, context=False)


def test_pc_owner_always_sees_own_thoughts():
    c = _make_character(
        role=CharacterRole.PC,
        surface_in_hud=False,
        surface_inline=False,
        surface_in_context=False,
    )
    view = resolve_privacy(c, observer=ObserverKind.PC_OWNER, is_self=True)
    assert view == PrivacyView(hud=True, inline=True, context=True)


def test_pov_mode_strips_npc_thoughts():
    c = _make_character()
    view = resolve_privacy(
        c,
        observer=ObserverKind.AUDIENCE,
        pov_mode=True,
        is_pc=False,
    )
    assert view == PrivacyView(hud=False, inline=False, context=False)


def test_pov_mode_does_not_strip_pc_thoughts():
    c = _make_character(role=CharacterRole.PC)
    view = resolve_privacy(
        c,
        observer=ObserverKind.PC_OWNER,
        pov_mode=True,
        is_pc=True,
        is_self=True,
    )
    assert view == PrivacyView(hud=True, inline=True, context=True)


def test_mixed_visibility_per_surface():
    c = _make_character(
        surface_in_hud=True,
        surface_inline=False,
        surface_in_context=False,
    )
    view = resolve_privacy(c, observer=ObserverKind.AUDIENCE)
    assert view.hud is True
    assert view.inline is False
    assert view.context is False


def test_apply_privacy_filter_strips_internal_thought_for_audience():
    from datetime import UTC, datetime

    now = datetime.now(UTC)

    def _val(field: str, value: str) -> TransientValue:
        return TransientValue(
            id=1,
            entity_id="x",
            field=field,
            value=value,
            provenance=Provenance.EXTRACTOR_AUTO,
            confidence=0.9,
            source_post_id=None,
            created_at=now,
            expires_at=None,
            in_game_at=None,
            decayed=False,
        )

    bundle = {
        "mood": _val("mood", "guarded"),
        "internal_thought": _val("internal_thought", "secret"),
    }
    filtered = apply_privacy_filter(
        resolver=None,
        entity_kind=EntityKind.CHARACTER,
        entity_id="x",
        bundle=bundle,
        campaign_id="c1",
        observer=ObserverKind.AUDIENCE,
    )
    assert "mood" in filtered
    assert "internal_thought" not in filtered


def test_apply_privacy_filter_passes_through_non_character():
    from datetime import UTC, datetime

    bundle = {
        "ambient_mood": TransientValue(
            id=1,
            entity_id="loc",
            field="ambient_mood",
            value="tense",
            provenance=Provenance.EXTRACTOR_AUTO,
            confidence=0.9,
            source_post_id=None,
            created_at=datetime.now(UTC),
            expires_at=None,
            in_game_at=None,
            decayed=False,
        )
    }
    filtered = apply_privacy_filter(
        resolver=None,
        entity_kind=EntityKind.LOCATION,
        entity_id="loc",
        bundle=bundle,
        campaign_id="c1",
        observer=ObserverKind.AUDIENCE,
    )
    assert filtered == bundle


async def test_privacy_resolver_with_loader():
    c = _make_character(surface_in_context=False)

    async def loader(_campaign: str, _id: str):
        return c

    resolver = PrivacyResolver(character_loader=loader)
    view = await resolver.resolve("c1", "char_florence", ObserverKind.AUDIENCE)
    assert view.context is False
    assert view.hud is True


async def test_privacy_resolver_returns_open_for_author():
    resolver = PrivacyResolver()
    view = await resolver.resolve("c1", "char_x", ObserverKind.AUTHOR)
    assert view == PrivacyView.all_open()


@pytest.mark.parametrize(
    "observer",
    [ObserverKind.AUTHOR, ObserverKind.PC_OWNER, ObserverKind.OTHER_PC, ObserverKind.AUDIENCE],
)
def test_resolve_privacy_does_not_crash_for_any_observer(observer: ObserverKind):
    c = _make_character()
    view = resolve_privacy(c, observer=observer)
    assert isinstance(view, PrivacyView)
