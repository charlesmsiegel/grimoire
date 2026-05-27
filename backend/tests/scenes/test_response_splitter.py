"""Tests for :mod:`grimoire.scenes.response_splitter`."""

from __future__ import annotations

from grimoire.scenes.response_splitter import ResponseSegment, split_response


def test_single_narrator_tag() -> None:
    text = "<narrator>The wind howls.</narrator>"
    result = split_response(text)
    assert result == [ResponseSegment(kind="narrator", ref=None, body="The wind howls.")]


def test_single_character_tag() -> None:
    text = '<character ref="alice">Alice waves.</character>'
    result = split_response(text)
    assert result == [ResponseSegment(kind="character", ref="alice", body="Alice waves.")]


def test_mixed_tags_preserve_order() -> None:
    text = (
        "<narrator>Rain falls.</narrator>"
        '<character ref="alice">Alice shivers.</character>'
        '<character ref="bob">Bob opens an umbrella.</character>'
    )
    result = split_response(text)
    assert len(result) == 3
    assert result[0] == ResponseSegment(kind="narrator", ref=None, body="Rain falls.")
    assert result[1] == ResponseSegment(kind="character", ref="alice", body="Alice shivers.")
    assert result[2] == ResponseSegment(
        kind="character", ref="bob", body="Bob opens an umbrella."
    )


def test_text_outside_tags_becomes_narrator() -> None:
    text = 'Preamble text.<character ref="alice">Alice speaks.</character>Trailing text.'
    result = split_response(text)
    assert result[0] == ResponseSegment(kind="narrator", ref=None, body="Preamble text.")
    assert result[1] == ResponseSegment(kind="character", ref="alice", body="Alice speaks.")
    assert result[2] == ResponseSegment(kind="narrator", ref=None, body="Trailing text.")


def test_no_tags_returns_single_narrator() -> None:
    text = "Just plain prose with no tags at all."
    result = split_response(text)
    assert result == [
        ResponseSegment(kind="narrator", ref=None, body="Just plain prose with no tags at all.")
    ]


def test_empty_string_returns_empty_list() -> None:
    result = split_response("")
    assert result == []


def test_empty_tag_body_is_dropped() -> None:
    text = '<character ref="alice"></character><character ref="bob">Hello.</character>'
    result = split_response(text)
    assert result == [ResponseSegment(kind="character", ref="bob", body="Hello.")]


def test_adjacent_same_character_merged() -> None:
    text = (
        '<character ref="alice">First part.</character>'
        '<character ref="alice">Second part.</character>'
    )
    result = split_response(text)
    assert result == [
        ResponseSegment(kind="character", ref="alice", body="First part.\n\nSecond part.")
    ]


def test_adjacent_narrator_segments_merged() -> None:
    text = "<narrator>Part one.</narrator><narrator>Part two.</narrator>"
    result = split_response(text)
    assert result == [ResponseSegment(kind="narrator", ref=None, body="Part one.\n\nPart two.")]


def test_same_character_non_adjacent_not_merged() -> None:
    text = (
        '<character ref="alice">First.</character>'
        '<character ref="bob">Middle.</character>'
        '<character ref="alice">Second.</character>'
    )
    result = split_response(text)
    assert len(result) == 3
    assert result[0].ref == "alice"
    assert result[1].ref == "bob"
    assert result[2].ref == "alice"


def test_unclosed_last_tag() -> None:
    text = '<character ref="alice">She begins to speak but the response cuts'
    result = split_response(text)
    assert result == [
        ResponseSegment(
            kind="character",
            ref="alice",
            body="She begins to speak but the response cuts",
        )
    ]


def test_whitespace_only_body_is_dropped() -> None:
    text = '<character ref="alice">   </character><narrator>Scene.</narrator>'
    result = split_response(text)
    assert result == [ResponseSegment(kind="narrator", ref=None, body="Scene.")]


def test_multiline_body_preserved() -> None:
    body = "Line one.\n\nLine two.\nLine three."
    text = f'<character ref="alice">{body}</character>'
    result = split_response(text)
    assert result == [ResponseSegment(kind="character", ref="alice", body=body)]
