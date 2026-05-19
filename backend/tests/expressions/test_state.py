"""Tests for ``ExpressionStateService``: insert, query, ``as_of_turn`` replay."""

from __future__ import annotations

import logging

import pytest

from grimoire.expressions.service import ExpressionStateService


async def test_set_and_get_current(service: ExpressionStateService) -> None:
    await service.set(
        campaign_id="cmp_1",
        scene_id="s_1",
        character_id="char_florence",
        turn_id="t_1",
        post_id="p_1",
        emotion="determined",
        provenance="user:pc",
    )
    current = await service.current_for("cmp_1", "char_florence")
    assert current is not None
    assert current.emotion == "determined"
    assert current.provenance == "user:pc"


async def test_latest_wins(service: ExpressionStateService) -> None:
    await service.set(
        campaign_id="cmp_1",
        scene_id="s_1",
        character_id="char_x",
        turn_id="t_1",
        post_id="p_1",
        emotion="happy",
        provenance="extractor:auto",
    )
    await service.set(
        campaign_id="cmp_1",
        scene_id="s_1",
        character_id="char_x",
        turn_id="t_5",
        post_id="p_5",
        emotion="angry",
        provenance="extractor:auto",
    )
    current = await service.current_for("cmp_1", "char_x")
    assert current is not None
    assert current.emotion == "angry"


async def test_as_of_turn_returns_historical(service: ExpressionStateService) -> None:
    await service.set(
        campaign_id="cmp_1",
        scene_id="s_1",
        character_id="char_florence",
        turn_id="t_1",
        post_id="p_1",
        emotion="happy",
        provenance="extractor:auto",
    )
    await service.set(
        campaign_id="cmp_1",
        scene_id="s_1",
        character_id="char_florence",
        turn_id="t_5",
        post_id="p_5",
        emotion="angry",
        provenance="extractor:auto",
    )
    current_at_t1 = await service.current_for("cmp_1", "char_florence", as_of_turn="t_1")
    assert current_at_t1 is not None
    assert current_at_t1.emotion == "happy"


async def test_extractor_label_outside_vocab_discarded(
    service: ExpressionStateService, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    result = await service.set(
        campaign_id="cmp_1",
        scene_id="s_1",
        character_id="char_x",
        turn_id="t_1",
        post_id="p_1",
        emotion="stale.label",
        provenance="extractor:auto",
    )
    assert result is None
    current = await service.current_for("cmp_1", "char_x")
    assert current is None
    assert "stale.label" in caplog.text


async def test_namespaced_extension_accepted(service: ExpressionStateService) -> None:
    result = await service.set(
        campaign_id="cmp_1",
        scene_id="s_1",
        character_id="char_x",
        turn_id="t_1",
        post_id="p_1",
        emotion="wod.seductive",
        provenance="user:pc",
        module_extensions={"wod": ["seductive"]},
    )
    assert result is not None
    current = await service.current_for("cmp_1", "char_x")
    assert current is not None
    assert current.emotion == "wod.seductive"


async def test_history_for(service: ExpressionStateService) -> None:
    for turn in ("t_1", "t_2", "t_3"):
        await service.set(
            campaign_id="cmp_1",
            scene_id="s_1",
            character_id="char_x",
            turn_id=turn,
            post_id=f"p_{turn}",
            emotion="happy",
            provenance="extractor:auto",
        )
    history = await service.history_for("cmp_1", "char_x")
    assert [r.turn_id for r in history] == ["t_3", "t_2", "t_1"]
