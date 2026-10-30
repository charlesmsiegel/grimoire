"""Marker matching and character-trigram voice profiles."""

from grimoire.evals.text import contains_marker, cosine, longest_shared_run, normalize, trigram_counts


def test_markers_match_whole_phrases_across_case_width_and_spacing():
    assert contains_marker("He said the DUKE  betrayed\nus", "duke betrayed")
    assert contains_marker("ＤＵＫＥ Aldous", "duke")
    assert contains_marker("It was the duke!", "duke")
    assert not contains_marker("The dukedom fell.", "duke")
    assert not contains_marker("archduke", "duke")


def test_marker_regex_characters_are_literal():
    assert contains_marker("Rule 3.1 (amended) applies", "3.1 (amended)")
    assert not contains_marker("Rule 341 amended applies", "3.1 (amended)")
    assert not contains_marker("aXb", "a.b")


def test_normalize_is_stable_after_casefold():
    assert normalize("  Straße\tＡＢＣ ") == "strasse abc"
    assert normalize(normalize("ﬁ Ǆ")) == normalize("ﬁ Ǆ")


def test_cosine_bounds():
    profile = trigram_counts("Ledger's closed.")
    assert cosine(profile, profile) == 1.0
    assert cosine(trigram_counts("aaaa"), trigram_counts("zzzz")) == 0.0
    assert cosine(trigram_counts(""), profile) == cosine(profile, trigram_counts("")) == 0.0
    assert cosine(profile, trigram_counts("")) == 0.0


def test_longest_shared_run_uses_normalised_text():
    assert longest_shared_run("THE VAULT stays shut", "the  vault stays shut") == len("the vault stays shut")
    assert longest_shared_run("abc", "xyz") == 0


def test_typographic_quotes_dashes_and_invisible_characters_cannot_hide_a_marker():
    assert contains_marker("The duke\u2019s betrayal is known", "duke's betrayal")
    assert contains_marker("a well\u2011known secret", "well-known")
    assert contains_marker("the hidden\u200bkey", "hidden key")
    assert contains_marker("the hidden\u2060key", "hidden key")
    assert contains_marker("the hid\u00adden key", "hidden key")


def test_markers_in_unspaced_scripts_match_inside_running_text():
    assert contains_marker("秘密は公爵が裏切った", "公爵")
    assert contains_marker("ฉันรู้ความลับของดยุค", "ความลับ")
    assert contains_marker("人々が集まった", "人々")
    assert not contains_marker("The dukedom fell.", "duke")  # spaced scripts keep boundaries
