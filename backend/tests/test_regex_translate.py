"""The SillyTavern pattern translator (store/regex/translate.py, spec §7).

There is no JavaScript engine here, so the differential check is against match
lists worked out by hand from the ECMAScript rules (and checked against a real
engine once, out of band). Most samples are chosen where handing the JavaScript
text to Python as it stands would give a different list.
"""

import re

import pytest

from grimoire.store.regex import rules, translate

GRIN = "\N{GRINNING FACE}"
LS = "\N{LINE SEPARATOR}"
NBSP = "\N{NO-BREAK SPACE}"
EM_SPACE = "\N{EM SPACE}"
BOM = "\N{ZERO WIDTH NO-BREAK SPACE}"
NEL = "\x85"
E_ACUTE = "\N{LATIN SMALL LETTER E WITH ACUTE}"
O_UMLAUT = "\N{LATIN SMALL LETTER O WITH DIAERESIS}"

JS_SPACE = ("[\\t\\n\\v\\f\\r \\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029"
            "\\u202f\\u205f\\u3000\\ufeff]")
JS_DOT = "[^\\n\\r\\u2028\\u2029]"

E, A, U = translate.EXACT, translate.APPROXIMATE, translate.UNTRANSLATABLE


@pytest.mark.parametrize("body, flags, expected, expected_flags, verdict", [
    (r"(?<n>a)\k<n>", "", "(?P<n>a)(?P=n)", "a", E),
    (r"a\/b", "", "a/b", "a", E),
    ("[^]", "", r"[\s\S]", "a", E),
    ("\\u{1F600}", "u", "\\U0001F600", "a", E),
    (".", "", JS_DOT, "a", E),
    (".", "s", ".", "sa", E),
    ("a$", "", r"a\Z", "a", E),
    (r"\s", "", JS_SPACE, "a", E),
    (r"\S", "", "[^" + JS_SPACE[1:], "a", E),
    (r"\w+", "g", r"\w+", "ga", E),
    (r"\d\D\W\b\B", "", r"\d\D\W\b\B", "a", E),
    (r"[a[]", "", r"[a\[]", "a", E),
    (r"[a-c-e&&]", "", r"[a-c\-e\&\&]", "a", E),
    ("\\uD83D\\uDE00", "", "\\U0001F600", "a", E),
    ("\\u00e9", "", "\\u00e9", "a", E),
    (r"\"hi\"", "gim", '"hi"', "gima", E),
    (r"x{,2}", "", r"x\{,2\}", "a", E),
    ("<think>[\\s\\S]*?</think>", "gi", r"<think>[\s\S]*?</think>", "gia", E),
    ("^a", "m", "^a", "ma", A),
    ("a$", "m", "a$", "ma", A),
    (E_ACUTE, "i", E_ACUTE, "ia", A),
    ("k", "iu", "k", "ia", A),
    (r"(a)?\1b", "", r"(a)?\1b", "a", A),
    # Can match empty under g: JavaScript and Python 3.7+ agree on where.
    (r"\s*$", "g", JS_SPACE + r"*\Z", "ga", E),
    # A repeated group whose capture one repetition can skip: JavaScript
    # clears it each time round, Python keeps the old value.
    ("(?:(a)|b)+", "g", "(?:(a)|b)+", "ga", A),
    ("((a)|b)+", "", "((a)|b)+", "a", A),
    ("(?:(a)?b)*", "", "(?:(a)?b)*", "a", A),
    ("(?:(a)|b){2}", "", "(?:(a)|b){2}", "a", A),
    ("(a)+", "g", "(a)+", "ga", E),
    ("(a|b)+", "", "(a|b)+", "a", E),
    ("(?:(a)b)+", "", "(?:(a)b)+", "a", E),
    ("(?:(a)|b)?", "", "(?:(a)|b)?", "a", E),
    ("\\" + GRIN, "", GRIN, "a", E),
    (r"\p{L}", "u", None, "", U),
    ("[]", "", None, "", U),
    (r"(?<=a+)b", "", None, "a", U),
    ("a", "y", None, "", U),
    ("a", "d", None, "", U),
    ("a", "v", None, "", U),
    (r"[\S]", "", None, "", U),
    (r"\A", "", None, "", U),
    (r"(?i:a)", "", None, "", U),
    (r"a++", "", None, "", U),
    (r"(a)\2", "", None, "", U),
    (r"\1(a)", "", None, "", U),
    (GRIN + "+", "", None, "", U),
    ("[" + GRIN + "]", "", None, "", U),
    ("\\" + GRIN + "+", "", None, "", U),
    ("[\\" + GRIN + "]", "", None, "", U),
    ("\\u{1F600}", "", None, "", U),
    ("a)", "", None, "", U),
    ("(a", "", None, "", U),
])
def test_translate_table(body, flags, expected, expected_flags, verdict):
    result = translate.translate(body, flags)
    assert result["verdict"] == verdict, result["notes"]
    assert result["pattern"] == expected
    assert result["flags"] == expected_flags
    if verdict != E:
        assert result["notes"]


def test_u_flag_is_dropped_with_a_note():
    result = translate.translate("\\u{1F600}", "gu")
    assert result["verdict"] == E
    assert result["flags"] == "ga"
    assert any("u flag" in n for n in result["notes"])


def test_untranslatable_lookbehind_carries_pythons_message():
    notes = translate.translate(r"(?<=a+)b", "")["notes"]
    assert any("look-behind" in n for n in notes)


def test_untranslatable_reason_names_the_construct():
    assert "property escape" in translate.translate(r"\p{L}", "u")["notes"][-1]
    assert "empty class" in translate.translate("[]", "")["notes"][-1]
    assert "sticky" in translate.translate("a", "y")["notes"][-1]


# (JavaScript body, flags, [(sample, the matches JavaScript finds with g)]).
DIFFERENTIAL = [
    (r"(?<n>a)\k<n>", "", [("aa", ["aa"]), ("aba", []), ("aaaa", ["aa", "aa"])]),
    (r"a\/b", "", [("a/b c a/b", ["a/b", "a/b"]), ("ab", [])]),
    ("[^]", "", [("a\nb", ["a", "\n", "b"])]),
    ("\\u{1F600}", "u", [("x" + GRIN + "y", [GRIN]), ("xy", [])]),
    # `.` stops at \r and U+2028 as well as \n.
    (".", "", [("a\nb\rc" + LS + "d", ["a", "b", "c", "d"])]),
    # `$` is the very end, not before a final newline.
    ("a$", "", [("a\n", []), ("ba", ["a"]), ("a\na", ["a"])]),
    # No U+0085 in JavaScript's \s; U+FEFF is in it.
    (r"\s", "", [("a" + NBSP + "b" + EM_SPACE + "c" + NEL + "d" + BOM + "e\tf",
                  [NBSP, EM_SPACE, BOM, "\t"])]),
    (r"\w+", "g", [("h" + E_ACUTE + "llo w" + O_UMLAUT + "rld_1",
                    ["h", "llo", "w", "rld_1"])]),
    (r"\d{2,}", "", [("1 22 333 \N{ARABIC-INDIC DIGIT ONE}\N{ARABIC-INDIC DIGIT TWO}",
                      ["22", "333"])]),
    (r"\bcat\b", "", [("cat concat cat_ cat.", ["cat", "cat"])]),
    ("<think>[\\s\\S]*?</think>\\s*", "gi",
     [("a<THINK>x\ny</think>\n b<think></think>c",
       ["<THINK>x\ny</think>\n ", "<think></think>"])]),
    (r"\"(.*?)\"", "g", [('say "hi" and "bye"', ['"hi"', '"bye"'])]),
    (r"x{,2}", "", [("x{,2} xx", ["x{,2}"])]),
    (r"[a-c-e]+", "", [("abc-e d", ["abc-e"])]),
]

_BITS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "a": re.ASCII}


@pytest.mark.parametrize("body, flags, samples", DIFFERENTIAL)
def test_translate_exact_differential(body, flags, samples):
    result = translate.translate(body, flags)
    assert result["verdict"] == E, result["notes"]
    bits = 0
    for f in result["flags"]:
        bits |= _BITS.get(f, 0)
    compiled = re.compile(result["pattern"], bits)
    for text, js_matches in samples:
        assert [m.group(0) for m in compiled.finditer(text)] == js_matches, text


# (JavaScript body, sample, replacement, what JavaScript's replace gives with g).
CAPTURE_RESETS = [
    ("(?:(a)|b)+", "ab", "[$1]", "[]"),
    ("(a)+", "aa", "[$1]", "[a]"),
    ("(a|b)+", "ab", "[$1]", "[b]"),
    ("(?:(a)b)+", "aab", "[$1]", "a[a]"),
]


@pytest.mark.parametrize("body, text, replacement, js_result", CAPTURE_RESETS)
def test_repeated_group_captures(body, text, replacement, js_result):
    """Exact only where Python's replacement agrees with JavaScript's."""
    result = translate.translate(body, "g")
    rule = rules.normalise({"pattern": result["pattern"], "flags": result["flags"],
                            "replacement": replacement})
    ours = rules.compile_pattern(rule).sub(lambda m: rules.expand(replacement, m, []), text)
    assert (result["verdict"] == E) == (ours == js_result)
