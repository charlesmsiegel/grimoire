"""Normalisation helpers the intent router matches on."""

from grimoire.intent.text import (
    first_word, grammar_form, marker_of, norm, split_sentences, strip_marker, wrapped,
)


def test_norm_curly_and_case():
    assert norm("  I’m  “Here” ") == "i'm \"here\""


def test_grammar_form_strips_punctuation_and_please():
    assert grammar_form("Please, open the Map!") == "open the map"
    assert grammar_form("Never mind,") == "never mind"


def test_first_word_strips_separator():
    assert first_word("Grimoire, pause") == "grimoire"
    assert first_word("Grimoire's pages") == "grimoire's"
    assert first_word("   ") == ""


def test_marker_of():
    assert marker_of("Grimoire, pause") == ("command", True)
    assert marker_of("grimoire pause") == ("command", False)
    assert marker_of("Grimoire ,  pause") == ("command", True)
    assert marker_of("Director: x") == ("director", True)
    assert marker_of("GM, x") == ("director", True)
    assert marker_of("narrator x") == ("director", False)
    assert marker_of("OOC: x") == ("director", True)
    assert marker_of("ooc x") == ("director", False)
    assert marker_of("((x))") == ("director", True)
    assert marker_of("Grimoire's pages glow") == (None, False)
    assert marker_of("Hello") == (None, False)


def test_strip_marker():
    assert strip_marker("Director, make this tense") == "make this tense"
    assert strip_marker("  Grimoire ,  pause ") == "pause"
    assert strip_marker("director make this tense") == "make this tense"
    assert strip_marker("OOC: keep it short") == "keep it short"
    assert strip_marker("((keep it short))") == "keep it short"
    assert strip_marker("Hello there") == "Hello there"


def test_wrapped():
    assert wrapped('"Run!"', '"', '"') == "Run!"
    assert wrapped("“Run!”", '"', '"') == "Run!"
    assert wrapped('"a" "b"', '"', '"') is None
    assert wrapped("*draws sword*", "*", "*") == "draws sword"
    assert wrapped("*a* *b*", "*", "*") is None
    assert wrapped('""', '"', '"') == ""
    assert wrapped('* *', '*', '*') == ""
    assert wrapped("Run", '"', '"') is None


def test_split_sentences():
    assert split_sentences("I open it. Grimoire, pause.") == ["i open it.", "grimoire, pause."]
    assert split_sentences('"Wait. Go!" I say') == ['"wait. go!" i say']
    assert split_sentences("stop. stop!") == ["stop.", "stop!"]
    assert split_sentences("Hello?! Fine...") == ["hello?!", "fine..."]
