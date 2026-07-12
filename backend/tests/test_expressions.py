import pytest

from grimoire.store import expressions


@pytest.mark.parametrize(
    "text,scope,expected",
    [
        ("1 + 2 * 3", {}, 7),
        ("floor((strength - 10) / 2)", {"strength": 15}, 2),
        ("floor((strength - 10) / 2)", {"strength": 9}, -1),
        ("min(dex, 5) + max(brawl, 1)", {"dex": 7, "brawl": 0}, 6),
        ("ceil(essence / 2)", {"essence": 5}, 3),
        ("abs(0 - hp)", {"hp": 4}, 4),
        ("hp_max - hp", {"hp": 3, "hp_max": 10}, 7),
        ("10 // 3", {}, 3),
        ("-vigor + 2", {"vigor": 3}, -1),
        ("2 if wits > 3 else 1", {"wits": 5}, 2),
        ("2 if wits > 3 else 1", {"wits": 2}, 1),
        ("wits > 2 and brawl > 0", {"wits": 3, "brawl": 1}, True),
        ("not (wits > 2)", {"wits": 1}, True),
    ],
)
def test_evaluate(text, scope, expected):
    assert expressions.evaluate(text, scope) == expected


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os')",          # call to non-whitelisted name
        "a.b",                        # attribute access
        "a[0]",                       # subscript
        "[x for x in y]",             # comprehension
        "lambda: 1",                  # lambda
        "'s'",                        # string literal
        "f'{a}'",                     # f-string
        "pow(2, 3)",                  # non-whitelisted call
        "a ** 2",                     # power operator (not whitelisted)
        "a % 2",                      # modulo (not whitelisted)
        "(1,)",                       # tuple
        "{1: 2}",                     # dict
        "a := 1",                     # walrus
        "1; 2",                       # not a single expression
        "def f(): pass",              # statement
        "",                           # empty
    ],
)
def test_rejects_forbidden(text):
    with pytest.raises(expressions.ExpressionError):
        expressions.parse(text)


def test_names():
    assert expressions.names("dex + min(brawl, 5)") == {"dex", "brawl"}


def test_unknown_name_at_eval():
    with pytest.raises(expressions.ExpressionError, match="unknown name"):
        expressions.evaluate("dex + 1", {})


def test_parse_error_names_construct():
    with pytest.raises(expressions.ExpressionError, match="Attribute"):
        expressions.parse("a.b")


def test_short_circuit_and_or():
    assert expressions.evaluate("a > 0 and b > 0", {"a": -1, "b": 0}) is False
    assert expressions.evaluate("a > 0 and b > 0", {"a": -1}) is False  # b never evaluated
    assert expressions.evaluate("a > 0 or b > 0", {"a": 1}) is True


@pytest.mark.parametrize("text,scope", [
    ("1 / a", {"a": 0}),
    ("min()", {}),
])
def test_runtime_errors_become_expression_errors(text, scope):
    with pytest.raises(expressions.ExpressionError):
        expressions.evaluate(text, scope)


def test_overflow_becomes_expression_error():
    with pytest.raises(expressions.ExpressionError):
        expressions.evaluate("a / 2", {"a": 10**400})


@pytest.mark.parametrize("text", ["+a", "True", "min(a, b=1)"])
def test_spec_whitelist_is_exact(text):
    with pytest.raises(expressions.ExpressionError):
        expressions.parse(text)


def test_non_string_input_raises_expression_error():
    with pytest.raises(expressions.ExpressionError):
        expressions.parse(5)  # type: ignore[arg-type]
