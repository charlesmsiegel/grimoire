"""Tests for per-character post splitting in the orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime

from grimoire.orchestrator.post_splitting import create_response_posts
from grimoire.scenes.narrator_mode import ALL_AT_ONCE, PER_CHARACTER
from grimoire.scenes.types import AuthorKind


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def test_all_at_once_creates_single_narrator_post() -> None:
    posts = create_response_posts(
        response_text="The narrator speaks for everyone.",
        narrator_mode=ALL_AT_ONCE,
        turn_id="turn-1",
        clock=_clock,
    )
    assert len(posts) == 1
    assert posts[0].author_kind == AuthorKind.NARRATOR
    assert posts[0].body == "The narrator speaks for everyone."


def test_per_character_splits_tagged_response() -> None:
    response = (
        "<narrator>Rain falls.</narrator>"
        '<character ref="worlds/w/characters/alice">Alice shivers.</character>'
        '<character ref="worlds/w/characters/bob">Bob opens an umbrella.</character>'
    )
    posts = create_response_posts(
        response_text=response,
        narrator_mode=PER_CHARACTER,
        turn_id="turn-1",
        clock=_clock,
    )
    assert len(posts) == 3
    assert posts[0].author_kind == AuthorKind.NARRATOR
    assert posts[0].body == "Rain falls."
    assert posts[1].author_kind == AuthorKind.NPC
    assert posts[1].author_npc_ref == "worlds/w/characters/alice"
    assert posts[1].body == "Alice shivers."
    assert posts[2].author_kind == AuthorKind.NPC
    assert posts[2].author_npc_ref == "worlds/w/characters/bob"
    assert posts[2].body == "Bob opens an umbrella."


def test_per_character_no_tags_degrades_to_narrator() -> None:
    posts = create_response_posts(
        response_text="Plain prose without any tags.",
        narrator_mode=PER_CHARACTER,
        turn_id="turn-1",
        clock=_clock,
    )
    assert len(posts) == 1
    assert posts[0].author_kind == AuthorKind.NARRATOR
    assert posts[0].body == "Plain prose without any tags."


def test_all_posts_share_turn_id() -> None:
    response = '<character ref="alice">Alice.</character><character ref="bob">Bob.</character>'
    posts = create_response_posts(
        response_text=response,
        narrator_mode=PER_CHARACTER,
        turn_id="turn-42",
        clock=_clock,
    )
    assert all(p.turn_id == "turn-42" for p in posts)


def test_all_posts_have_unique_ids() -> None:
    response = '<character ref="alice">Alice.</character><character ref="bob">Bob.</character>'
    posts = create_response_posts(
        response_text=response,
        narrator_mode=PER_CHARACTER,
        turn_id="turn-1",
        clock=_clock,
    )
    ids = [p.id for p in posts]
    assert len(ids) == len(set(ids))
