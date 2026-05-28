"""Tests for the SceneSuggestionEngine."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from grimoire.scenes.ledger import SceneLedger
from grimoire.scenes.suggest import SceneSuggestionEngine, SuggestionContext
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db
from grimoire.types.llm import CompletionResponse, TokenUsage


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(stamp_migrated_db(tmp_path / "test.sqlite"), pool_size=2)
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def ledger(db):
    return SceneLedger(db)


def _mock_gateway(suggestions: list[dict]) -> AsyncMock:
    gateway = AsyncMock()
    gateway.complete = AsyncMock(
        return_value=CompletionResponse(
            text=json.dumps(suggestions),
            model="test",
            finish_reason="stop",
            usage=TokenUsage(),
        )
    )
    return gateway


def _ctx(campaign_id: str = "c1", **overrides) -> SuggestionContext:
    defaults = dict(
        campaign_id=campaign_id,
        recent_summaries=["The party escaped the catacombs."],
        open_threads=["Missing shipment unresolved."],
        active_pcs=["alistair", "mirella"],
        last_location="Thornwall",
        in_game_time="Day 12, evening",
        unused_greeting_names=[],
    )
    defaults.update(overrides)
    return SuggestionContext(**defaults)


async def test_suggest_returns_ledger_plus_generated(
    ledger: SceneLedger,
) -> None:
    await ledger.add(
        campaign_id="c1",
        summary="The harbor at dawn.",
        source="greeting",
        greeting_id="gr-1",
    )
    await ledger.add(campaign_id="c1", summary="A meeting with the Archon.", source="llm")

    generated = [
        {
            "summary": "Bandits on the road.",
            "proposed_location": "South Road",
            "proposed_cast": ["alistair"],
        },
        {"summary": "A letter arrives.", "proposed_location": "Camp", "proposed_cast": ["mirella"]},
        {
            "summary": "A storm rolls in.",
            "proposed_location": "Plains",
            "proposed_cast": ["alistair"],
        },
    ]
    gateway = _mock_gateway(generated)
    engine = SceneSuggestionEngine(ledger=ledger, gateway=gateway)
    result = await engine.suggest(_ctx())

    assert len(result["ledger_picks"]) == 2
    # 5 total - 2 ledger = 3 generated
    assert len(result["generated"]) == 3
    assert result["generated"][0]["summary"] == "Bandits on the road."
    gateway.complete.assert_called_once()
    prompt_text = gateway.complete.call_args[0][1].messages[0].content
    assert "Generate 3 scene suggestions" in prompt_text


async def test_suggest_caps_ledger_at_3(ledger: SceneLedger) -> None:
    for i in range(5):
        await ledger.add(campaign_id="c1", summary=f"Idea {i}", source="llm")

    generated = [
        {"summary": "Fresh idea 1.", "proposed_location": "X", "proposed_cast": []},
        {"summary": "Fresh idea 2.", "proposed_location": "Y", "proposed_cast": []},
    ]
    gateway = _mock_gateway(generated)
    engine = SceneSuggestionEngine(ledger=ledger, gateway=gateway)
    result = await engine.suggest(_ctx())

    assert len(result["ledger_picks"]) <= 3
    assert len(result["generated"]) >= 2


async def test_suggest_handles_malformed_llm_response(ledger: SceneLedger) -> None:
    gateway = AsyncMock()
    gateway.complete = AsyncMock(
        return_value=CompletionResponse(
            text="not valid json",
            model="test",
            finish_reason="stop",
            usage=TokenUsage(),
        )
    )
    engine = SceneSuggestionEngine(ledger=ledger, gateway=gateway)
    result = await engine.suggest(_ctx())

    assert result["ledger_picks"] == []
    assert result["generated"] == []


async def test_suggest_empty_ledger_generates_5(ledger: SceneLedger) -> None:
    generated = [
        {"summary": "A.", "proposed_location": "X", "proposed_cast": []},
        {"summary": "B.", "proposed_location": "Y", "proposed_cast": []},
        {"summary": "C.", "proposed_location": "Z", "proposed_cast": []},
        {"summary": "D.", "proposed_location": "W", "proposed_cast": []},
        {"summary": "E.", "proposed_location": "V", "proposed_cast": []},
    ]
    gateway = _mock_gateway(generated)
    engine = SceneSuggestionEngine(ledger=ledger, gateway=gateway)
    result = await engine.suggest(_ctx())

    assert len(result["ledger_picks"]) == 0
    assert len(result["generated"]) == 5
    # Verify prompt asked for 5
    call_args = gateway.complete.call_args
    prompt_text = call_args[0][1].messages[0].content
    assert "Generate 5 scene suggestions" in prompt_text
