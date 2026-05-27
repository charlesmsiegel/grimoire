"""Tests for the response format prompt templates."""

from __future__ import annotations

from grimoire.templates import render


def test_default_response_format_renders_npc_list() -> None:
    npcs = [
        {"name": "Alice", "ref": "worlds/w/characters/alice"},
        {"name": "Bob", "ref": "worlds/w/characters/bob"},
    ]
    result = render("context_response_format", present_npcs=npcs)
    assert "Alice" in result
    assert "worlds/w/characters/alice" in result
    assert "Bob" in result
    assert '<character ref="' in result
    assert "<narrator>" in result


def test_default_response_format_with_single_npc() -> None:
    npcs = [{"name": "Alice", "ref": "worlds/w/characters/alice"}]
    result = render("context_response_format", present_npcs=npcs)
    assert "Alice" in result


def test_single_character_variant_renders_character() -> None:
    result = render(
        "context_response_format",
        variant="single_character",
        character_name="Alice",
        character_ref="worlds/w/characters/alice",
    )
    assert "Alice" in result
    assert "worlds/w/characters/alice" not in result or "Alice" in result
