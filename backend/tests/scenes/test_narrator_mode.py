"""Unit tests for :mod:`grimoire.scenes.narrator_mode`."""

from __future__ import annotations

import json

from grimoire.scenes.narrator_mode import (
    ALL_AT_ONCE,
    DEFAULT_RESPONSE_MODE,
    PER_CHARACTER,
    campaign_response_mode,
    effective_response_mode,
    normalize_response_mode,
)


def test_normalize_response_mode_accepts_known_values() -> None:
    assert normalize_response_mode(ALL_AT_ONCE) == ALL_AT_ONCE
    assert normalize_response_mode(PER_CHARACTER) == PER_CHARACTER


def test_normalize_response_mode_rejects_garbage() -> None:
    assert normalize_response_mode(None) is None
    assert normalize_response_mode("round-robin") is None
    assert normalize_response_mode(42) is None
    assert normalize_response_mode("") is None


def test_campaign_response_mode_falls_back_to_default_when_unset() -> None:
    assert campaign_response_mode(None) == DEFAULT_RESPONSE_MODE
    assert campaign_response_mode({"config": None}) == DEFAULT_RESPONSE_MODE
    assert campaign_response_mode({"config": ""}) == DEFAULT_RESPONSE_MODE
    assert campaign_response_mode({"config": "not json"}) == DEFAULT_RESPONSE_MODE
    assert (
        campaign_response_mode({"config": json.dumps({"imagegen": {}})})
        == DEFAULT_RESPONSE_MODE
    )


def test_campaign_response_mode_reads_persisted_value() -> None:
    row = {"config": json.dumps({"narrator": {"response_mode": PER_CHARACTER}})}
    assert campaign_response_mode(row) == PER_CHARACTER


def test_effective_response_mode_prefers_scene_override() -> None:
    row = {"config": json.dumps({"narrator": {"response_mode": ALL_AT_ONCE}})}
    assert (
        effective_response_mode(scene_override=PER_CHARACTER, campaign_row=row)
        == PER_CHARACTER
    )


def test_effective_response_mode_falls_back_to_campaign_when_scene_unset() -> None:
    row = {"config": json.dumps({"narrator": {"response_mode": PER_CHARACTER}})}
    assert (
        effective_response_mode(scene_override=None, campaign_row=row) == PER_CHARACTER
    )


def test_effective_response_mode_falls_back_to_default_when_both_unset() -> None:
    assert (
        effective_response_mode(scene_override=None, campaign_row=None)
        == DEFAULT_RESPONSE_MODE
    )


def test_effective_response_mode_ignores_invalid_override() -> None:
    row = {"config": json.dumps({"narrator": {"response_mode": ALL_AT_ONCE}})}
    assert (
        effective_response_mode(scene_override="garbage", campaign_row=row)
        == ALL_AT_ONCE
    )
