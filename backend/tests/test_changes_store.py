from grimoire.store import changes


def test_line_diff_insert_only():
    d = changes.line_diff("a", "a\nb")
    assert d == [{"op": "equal", "text": "a"}, {"op": "insert", "text": "b"}]


def test_line_diff_delete_only():
    d = changes.line_diff("a\nb", "a")
    assert d == [{"op": "equal", "text": "a"}, {"op": "delete", "text": "b"}]


def test_line_diff_replace_emits_delete_then_insert():
    d = changes.line_diff("a\nold", "a\nnew")
    assert d == [{"op": "equal", "text": "a"},
                 {"op": "delete", "text": "old"},
                 {"op": "insert", "text": "new"}]


def test_line_diff_identical_all_equal():
    assert changes.line_diff("a\nb", "a\nb") == [
        {"op": "equal", "text": "a"}, {"op": "equal", "text": "b"}]


def test_line_diff_empty_sides():
    assert changes.line_diff("", "") == []
    assert changes.line_diff("", "x") == [{"op": "insert", "text": "x"}]
    assert changes.line_diff("x", "") == [{"op": "delete", "text": "x"}]
