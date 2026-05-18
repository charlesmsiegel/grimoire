"""§5 Rule-based weather override detection."""

from __future__ import annotations

from grimoire.extractor.config import ExtractorConfig
from grimoire.extractor.rule_based import extract_rule_based
from grimoire.types.state import DeltaKind


def _run(text: str, *, location_ref: str | None = "library:worlds/w1/locations/town"):
    return list(
        extract_rule_based(
            text,
            campaign_id="camp-1",
            config=ExtractorConfig(),
            source="extractor",
            scene_location_ref=location_ref,
            scene_branch_id="camp-1:main",
        )
    )


def test_detects_began_to_rain() -> None:
    deltas = _run("and it began to rain heavily")
    weather_deltas = [d for d in deltas if d.kind == DeltaKind.OVERRIDE_WRITE]
    assert len(weather_deltas) == 1
    d = weather_deltas[0]
    assert d.target_table == "location_state"
    assert d.target_id == "library:worlds/w1/locations/town"
    assert d.after["weather"]["kind"] == "rain"
    assert d.after["weather"]["source"] == "override"


def test_detects_snow_began_falling() -> None:
    deltas = _run("Suddenly, snow began falling across the rooftops.")
    weather_deltas = [d for d in deltas if d.kind == DeltaKind.OVERRIDE_WRITE]
    assert len(weather_deltas) == 1
    assert weather_deltas[0].after["weather"]["kind"] == "snow"


def test_detects_fog_rolled_in() -> None:
    deltas = _run("a thick fog rolled in from the harbour")
    weather_deltas = [d for d in deltas if d.kind == DeltaKind.OVERRIDE_WRITE]
    assert len(weather_deltas) == 1
    assert weather_deltas[0].after["weather"]["kind"] == "fog"


def test_no_match_returns_no_weather_delta() -> None:
    deltas = _run("they had a pleasant conversation")
    weather_deltas = [d for d in deltas if d.kind == DeltaKind.OVERRIDE_WRITE]
    assert weather_deltas == []


def test_skip_when_no_scene_location() -> None:
    """Without a known location ref we can't write an override row."""
    deltas = _run("it began to storm", location_ref=None)
    weather_deltas = [d for d in deltas if d.kind == DeltaKind.OVERRIDE_WRITE]
    assert weather_deltas == []


def test_only_one_weather_delta_per_pass() -> None:
    deltas = _run("it began to rain and then snow began falling")
    weather_deltas = [d for d in deltas if d.kind == DeltaKind.OVERRIDE_WRITE]
    assert len(weather_deltas) == 1
