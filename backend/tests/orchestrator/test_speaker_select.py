"""Tests for :mod:`grimoire.orchestrator.speaker_select`."""

from __future__ import annotations

import random

from grimoire.orchestrator.speaker_select import (
    parse_speaker_ref,
    select_fallback_speaker,
)


def test_parse_speaker_ref_exact_match() -> None:
    present = ["worlds/w/characters/alice", "worlds/w/characters/bob"]
    assert parse_speaker_ref("worlds/w/characters/alice", present) == "worlds/w/characters/alice"


def test_parse_speaker_ref_trailing_whitespace() -> None:
    present = ["worlds/w/characters/alice"]
    assert (
        parse_speaker_ref("  worlds/w/characters/alice  \n", present)
        == "worlds/w/characters/alice"
    )


def test_parse_speaker_ref_unknown_returns_none() -> None:
    present = ["worlds/w/characters/alice"]
    assert parse_speaker_ref("worlds/w/characters/unknown", present) is None


def test_parse_speaker_ref_empty_returns_none() -> None:
    assert parse_speaker_ref("", ["worlds/w/characters/alice"]) is None


def test_fallback_speaker_picks_least_recent() -> None:
    present = ["alice", "bob", "charlie"]
    recent_speakers = ["charlie", "bob"]
    rng = random.Random(42)
    result = select_fallback_speaker(present, recent_speakers, rng)
    assert result == "alice"


def test_fallback_speaker_all_spoken_picks_least_recent() -> None:
    present = ["alice", "bob"]
    recent_speakers = ["alice", "bob", "alice"]
    rng = random.Random(42)
    result = select_fallback_speaker(present, recent_speakers, rng)
    assert result == "bob"


def test_fallback_speaker_none_spoken_picks_random() -> None:
    present = ["alice", "bob"]
    rng = random.Random(42)
    result = select_fallback_speaker(present, [], rng)
    assert result in present


def test_fallback_speaker_single_npc() -> None:
    result = select_fallback_speaker(["alice"], [], random.Random(0))
    assert result == "alice"
