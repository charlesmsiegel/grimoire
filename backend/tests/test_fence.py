import json

from grimoire.store.fence import FenceWatcher, parse_roll_body


def run(chunks):
    w = FenceWatcher()
    out = "".join(w.feed(c) for c in chunks)
    out += w.finish()
    return w, out


def test_no_fence_passthrough():
    w, out = run(["Mara ", "leaps."])
    assert out == "Mara leaps." and w.narration == "Mara leaps."
    assert not w.complete and not w.truncated and w.body is None


def test_fence_mid_stream():
    body = '{"check": "athletics", "actor": "characters:mara"}'
    w, out = run(["She lunges—\n", "```roll\n", body, "\n```", "\nleftover ignored"])
    assert w.complete and w.body.strip() == body
    assert w.narration == "She lunges—\n"
    assert "```" not in out and "athletics" not in out


def test_fence_split_across_deltas():
    body = '{"check": "brawl", "actor": "characters:mara"}'
    w, out = run(["punch! ", "``", "`ro", "ll\n", body[:10], body[10:], "\n`", "``"])
    assert w.complete and json.loads(w.body)["check"] == "brawl"
    assert w.narration == "punch! "
    assert "`" not in out


def test_unclosed_fence_truncated():
    w, out = run(["text\n", "```roll\n", '{"check": "brawl"'])
    assert w.truncated and not w.complete
    assert w.narration == "text\n"
    assert w.body.strip() == '{"check": "brawl"'


def test_fence_at_start():
    w, out = run(['```roll\n{"check": "x"}\n```'])
    assert w.complete and w.narration == "" and out == ""


def test_second_fence_ignored():
    w, out = run(['a\n```roll\n{"check": "x"}\n```\n```roll\n{"check": "y"}\n```'])
    assert w.complete and json.loads(w.body)["check"] == "x"


def test_holdback_eventually_emitted():
    w, out = run(["end with backt", "icks ``ok``"])
    assert out == "end with backticks ``ok``"


def test_opener_with_spaces_split_never_leaks():
    body = '{"check": "brawl", "actor": "characters:mara"}'
    for gap in ("", " ", "        ", "\t"):
        for split_at in range(1, 4 + len(gap)):
            opener = f"```{gap}roll\n"
            w = FenceWatcher()
            out = w.feed("go! ") + w.feed(opener[:split_at]) + w.feed(opener[split_at:])
            out += w.feed(body + "\n```")
            out += w.finish()
            assert "`" not in out, f"leaked with gap={gap!r} split={split_at}"
            assert w.complete and w.narration == "go! "


def test_newline_after_backticks_is_not_an_opener():
    w, out = run(["```\ncode\n```", " done"])
    assert w.complete is False and w.body is None
    assert out == "```\ncode\n``` done"


def test_parse_roll_body_strict_and_tolerant():
    fields, problems = parse_roll_body('{"check": "brawl", "actor": "characters:mara", "difficulty": 6}')
    assert fields["check"] == "brawl" and problems == []
    fields, problems = parse_roll_body("{'check': 'brawl', 'difficulty': 6,}")
    assert fields.get("check") == "brawl" and fields.get("difficulty") == 6
    fields, problems = parse_roll_body("utter garbage !!!")
    assert fields == {} and problems
