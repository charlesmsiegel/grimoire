"""Tests for the heuristic + LLM expression classifiers and the routing band."""

from __future__ import annotations

from grimoire.expressions.heuristic import heuristic_classify
from grimoire.expressions.llm_classifier import llm_classify, merge_changes
from grimoire.expressions.routing import (
    ROUTE_AUTO_APPLY,
    ROUTE_DISCARD,
    ROUTE_REVIEW,
    classify_route,
)
from grimoire.types.expressions import ExpressionChange


def test_heuristic_detects_happy_keywords() -> None:
    changes = heuristic_classify(
        scene_post_text='"I knew you\'d come!" winifred laughed, eyes bright with joy.',
        present_characters=[("char_florence", "winifred")],
    )
    assert len(changes) == 1
    assert changes[0].character_id == "char_florence"
    assert changes[0].emotion == "happy"
    assert changes[0].confidence >= 0.55


def test_heuristic_detects_angry_with_punctuation_bump() -> None:
    changes = heuristic_classify(
        scene_post_text='"Get out!" winifred snapped.',
        present_characters=[("char_florence", "winifred")],
    )
    assert len(changes) == 1
    assert changes[0].emotion == "angry"
    # Punctuation bump should push the confidence well above the floor.
    assert changes[0].confidence > 0.7


def test_terminal_emotion_wins_in_multi_emotion_paragraph() -> None:
    changes = heuristic_classify(
        scene_post_text="winifred smiled, then her face fell.",
        present_characters=[("char_florence", "winifred")],
    )
    assert len(changes) == 1
    # ``face fell`` is the terminal cue and maps to sad.
    assert changes[0].emotion in {"sad", "neutral"}
    assert changes[0].emotion == "sad"


def test_no_present_characters_returns_empty() -> None:
    assert heuristic_classify(
        scene_post_text="A dog barked in the distance.",
        present_characters=[],
    ) == []


def test_paragraph_without_named_character_skipped() -> None:
    # No character name in the prose → no attribution.
    changes = heuristic_classify(
        scene_post_text="Somebody laughed in the corner.",
        present_characters=[("char_florence", "winifred")],
    )
    assert changes == []


def test_multiple_characters_separately_classified() -> None:
    changes = heuristic_classify(
        scene_post_text=(
            "winifred laughed at the joke.\n\n"
            "julian glared at the floorboards."
        ),
        present_characters=[("char_florence", "winifred"), ("char_asher", "julian")],
    )
    by_id = {c.character_id: c for c in changes}
    assert by_id["char_florence"].emotion == "happy"
    assert by_id["char_asher"].emotion == "angry"


def test_routing_bands() -> None:
    assert classify_route(0.9) == ROUTE_AUTO_APPLY
    assert classify_route(0.7) == ROUTE_AUTO_APPLY
    assert classify_route(0.6) == ROUTE_REVIEW
    assert classify_route(0.5) == ROUTE_REVIEW
    assert classify_route(0.4) == ROUTE_DISCARD
    assert classify_route(0.0) == ROUTE_DISCARD


async def test_llm_classifier_parses_json() -> None:
    async def fake_call(_prompt: str) -> str:
        return '{"char_florence": "happy", "char_asher": "thoughtful"}'

    changes = await llm_classify(
        llm_call=fake_call,
        scene_post_text="...",
        present_characters=[("char_florence", "winifred"), ("char_asher", "julian")],
    )
    by_id = {c.character_id: c.emotion for c in changes}
    assert by_id == {"char_florence": "happy", "char_asher": "thoughtful"}


async def test_llm_classifier_handles_markdown_fences() -> None:
    async def fake_call(_prompt: str) -> str:
        return '```json\n{"char_florence": "happy"}\n```'

    changes = await llm_classify(
        llm_call=fake_call,
        scene_post_text="...",
        present_characters=[("char_florence", "winifred")],
    )
    assert len(changes) == 1
    assert changes[0].emotion == "happy"


async def test_llm_classifier_drops_unknown_labels() -> None:
    async def fake_call(_prompt: str) -> str:
        return '{"char_florence": "ecstatic"}'

    changes = await llm_classify(
        llm_call=fake_call,
        scene_post_text="...",
        present_characters=[("char_florence", "winifred")],
    )
    assert changes == []


async def test_llm_classifier_accepts_namespaced_extension() -> None:
    async def fake_call(_prompt: str) -> str:
        return '{"char_florence": "wod.seductive"}'

    changes = await llm_classify(
        llm_call=fake_call,
        scene_post_text="...",
        present_characters=[("char_florence", "winifred")],
        module_extensions={"wod": ["seductive"]},
    )
    assert len(changes) == 1
    assert changes[0].emotion == "wod.seductive"


def test_merge_changes_picks_higher_confidence() -> None:
    heur = [
        ExpressionChange(character_id="a", emotion="happy", confidence=0.6),
        ExpressionChange(character_id="b", emotion="sad", confidence=0.9),
    ]
    llm = [
        ExpressionChange(character_id="a", emotion="determined", confidence=0.85),
        ExpressionChange(character_id="c", emotion="angry", confidence=0.7),
    ]
    merged = {c.character_id: c.emotion for c in merge_changes(heur, llm)}
    assert merged == {"a": "determined", "b": "sad", "c": "angry"}
