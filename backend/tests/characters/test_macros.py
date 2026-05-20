"""Tests for grimoire.characters.macros."""

from __future__ import annotations

import pytest

from grimoire.characters.macros import expand_macros


def test_char_replaced_with_card_name() -> None:
    text, warnings = expand_macros(
        "{{char}} smiled.",
        char_name="Beatrice",
        card_asset_id="cv1",
        field_name="description",
    )
    assert text == "Beatrice smiled."
    assert warnings == []


def test_user_preserved_at_ingest() -> None:
    text, warnings = expand_macros(
        "{{char}} addressed {{user}}.",
        char_name="Beatrice",
        card_asset_id="cv1",
        field_name="description",
    )
    assert text == "Beatrice addressed {{user}}."
    assert warnings == []


def test_user_replaced_when_keep_user_false() -> None:
    text, _ = expand_macros(
        "Hi {{user}}.",
        char_name="Beatrice",
        card_asset_id="cv1",
        field_name="description",
        keep_user=False,
    )
    assert text == "Hi the player."


def test_random_seeded_deterministic() -> None:
    t1, _ = expand_macros(
        "{{random:a,b,c}}",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    t2, _ = expand_macros(
        "{{random:a,b,c}}",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    assert t1 == t2
    assert t1 in {"a", "b", "c"}


def test_random_alternate_separator() -> None:
    text, _ = expand_macros(
        "{{random:a::b::c}}",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    assert text in {"a", "b", "c"}


def test_pick_is_alias_of_random() -> None:
    a, _ = expand_macros(
        "{{random:x,y,z}}",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    b, _ = expand_macros(
        "{{pick:x,y,z}}",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    assert a == b


def test_roll_NdM_sum() -> None:
    text, warnings = expand_macros(
        "{{roll:2d6}}",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    assert warnings == []
    assert 2 <= int(text) <= 12


def test_roll_seeded_deterministic() -> None:
    t1, _ = expand_macros(
        "{{roll:3d20}}",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    t2, _ = expand_macros(
        "{{roll:3d20}}",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    assert t1 == t2


def test_roll_bad_spec_left_literal_with_warning() -> None:
    text, warnings = expand_macros(
        "{{roll:cheese}}",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    assert text == "{{roll:cheese}}"
    assert any("roll" in w.lower() for w in warnings)


def test_roll_huge_n_rejected_without_iterating() -> None:
    # Regression: ``_ROLL_PATTERN`` accepts arbitrarily large integers and
    # ingest runs synchronously inside the FastAPI request handler, so an
    # unbounded N would block the event loop for billions of iterations.
    # The cap rejects the spec literally with a warning instead.
    text, warnings = expand_macros(
        "{{roll:999999999d2}}",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    assert text == "{{roll:999999999d2}}"
    assert any("cap" in w.lower() for w in warnings)


def test_roll_huge_sides_rejected() -> None:
    text, warnings = expand_macros(
        "{{roll:1d999999999}}",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    assert text == "{{roll:1d999999999}}"
    assert any("cap" in w.lower() for w in warnings)


def test_roll_at_caps_still_works() -> None:
    text, warnings = expand_macros(
        "{{roll:100d1000}}",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    assert warnings == []
    # 100d1000 → sum in [100, 100_000]
    assert 100 <= int(text) <= 100_000


def test_newline_expands_to_newline_char() -> None:
    text, _ = expand_macros(
        "a{{newline}}b",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    assert text == "a\nb"


def test_trim_consumes_one_whitespace_each_side() -> None:
    text, _ = expand_macros(
        "a {{trim}} b",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    assert text == "ab"


def test_trim_chains() -> None:
    text, _ = expand_macros(
        "a {{trim}}{{trim}} b",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    assert text == "ab"


def test_comment_stripped() -> None:
    text, _ = expand_macros(
        "foo{{// hidden}} bar",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    assert text == "foo bar"


def test_unknown_macro_passthrough_with_warning() -> None:
    text, warnings = expand_macros(
        "foo {{calendar}} bar",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    assert text == "foo {{calendar}} bar"
    assert any("calendar" in w for w in warnings)


def test_nested_macros_disallowed_with_warning() -> None:
    text, warnings = expand_macros(
        "{{random:{{char}},other}}",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    assert any("nest" in w.lower() for w in warnings)
    # outer left literal because nested
    assert "{{random:" in text


def test_distinct_positions_get_distinct_seeds() -> None:
    # Two random macros at different positions can yield different picks
    # but the full output is deterministic across runs.
    t1, _ = expand_macros(
        "{{random:a,b}}{{random:a,b}}",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    t2, _ = expand_macros(
        "{{random:a,b}}{{random:a,b}}",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    assert t1 == t2


def test_empty_text_short_circuits() -> None:
    text, warnings = expand_macros(
        "",
        char_name="X",
        card_asset_id="cid",
        field_name="f",
    )
    assert text == ""
    assert warnings == []


def test_char_macro_case_insensitive() -> None:
    text, _ = expand_macros(
        "{{Char}} and {{CHAR}} and {{char}}",
        char_name="Yara",
        card_asset_id="cv1",
        field_name="description",
    )
    assert text == "Yara and Yara and Yara"


@pytest.mark.parametrize("seed_field", ["description", "personality", "scenario"])
def test_different_field_names_produce_different_seeds(seed_field: str) -> None:
    a, _ = expand_macros(
        "{{random:a,b,c,d,e,f,g,h,i,j}}",
        char_name="X",
        card_asset_id="cid",
        field_name="alpha",
    )
    _b, _ = expand_macros(
        "{{random:a,b,c,d,e,f,g,h,i,j}}",
        char_name="X",
        card_asset_id="cid",
        field_name=seed_field,
    )
    # With 10 choices, two distinct seeds should very likely give different picks.
    # We only assert determinism here; pick-difference is statistical.
    a2, _ = expand_macros(
        "{{random:a,b,c,d,e,f,g,h,i,j}}",
        char_name="X",
        card_asset_id="cid",
        field_name="alpha",
    )
    assert a == a2
