import pytest

from grimoire.files.slug import parse_scene_filename, scene_filename, slugify


def test_basic_slug() -> None:
    assert slugify("Elysium Opening") == "elysium-opening"


def test_collapses_punctuation_and_spaces() -> None:
    assert slugify("The Prince's Tower — Act I") == "the-prince-s-tower-act-i"


def test_strips_leading_trailing_hyphens() -> None:
    assert slugify("---hello---") == "hello"
    assert slugify("!!!") == "untitled"


def test_unicode_normalized_to_ascii() -> None:
    assert slugify("Café Résumé") == "cafe-resume"


def test_non_latin_falls_back_to_untitled() -> None:
    assert slugify("漢字") == "untitled"


def test_max_len_truncates_without_trailing_hyphen() -> None:
    text = "a-very-long-title-that-keeps-going-and-going"
    slug = slugify(text, max_len=20)
    assert len(slug) <= 20
    assert not slug.endswith("-")


def test_max_len_must_be_positive() -> None:
    with pytest.raises(ValueError):
        slugify("x", max_len=0)


def test_scene_filename_format() -> None:
    assert scene_filename(1, "Elysium Opening") == "0001-elysium-opening.md"
    assert scene_filename(42, "Chase!!", ext="yaml") == "0042-chase.yaml"


def test_scene_filename_custom_width() -> None:
    assert scene_filename(7, "x", width=2) == "07-x.md"


def test_scene_filename_strips_leading_dot_in_ext() -> None:
    assert scene_filename(1, "x", ext=".yaml") == "0001-x.yaml"


def test_scene_filename_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError):
        scene_filename(-1, "x")
    with pytest.raises(ValueError):
        scene_filename(1, "x", width=0)


def test_parse_scene_filename_round_trip() -> None:
    parts = parse_scene_filename("0017-the-camden-blade.md")
    assert parts.ordinal == 17
    assert parts.slug == "the-camden-blade"
    assert parts.ext == "md"


def test_parse_scene_filename_yaml_sidecar() -> None:
    parts = parse_scene_filename("0001-elysium-opening.yaml")
    assert parts.ordinal == 1
    assert parts.ext == "yaml"


def test_parse_scene_filename_rejects_bad_pattern() -> None:
    with pytest.raises(ValueError):
        parse_scene_filename("nope.md")
    with pytest.raises(ValueError):
        parse_scene_filename("0001-Bad_Slug.md")
    with pytest.raises(ValueError):
        parse_scene_filename("0001-elysium-opening")
