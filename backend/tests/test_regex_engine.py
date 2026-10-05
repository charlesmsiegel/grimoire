"""The regex rule schema and the engine that applies it (store/regex).

Pure functions over text and dicts, so nothing here needs a store on disk.
"""

from __future__ import annotations

import pytest

from grimoire.store import scenes
from grimoire.store.regex import apply, rules


def _rule(**over) -> dict:
    base = {
        "pattern": "a",
        "replacement": "b",
        "flags": "g",
        "targets": ["model"],
        "applies": ["display", "prompt"],
    }
    base.update(over)
    return rules.normalise(base)


def _entry(rule: dict | None = None, *, level: str = "global", off: bool = False, **over) -> dict:
    return {"level": level, "rule": rule or _rule(**over), "off": off, "source": ""}


def _run(text: str, *entries: dict, role="model", phase="display", depth=0) -> str:
    return apply.run(text, list(entries), role=role, phase=phase, depth=depth)


def test_g_replaces_all_else_first():
    assert _run("aaa", _entry(flags="g")) == "bbb"
    assert _run("aaa", _entry(flags="")) == "baa"


def test_replacement_syntax():
    e = _entry(pattern=r"(?P<w>\w+) (\w+)", replacement="$2 $1|$<w>|$&|{{match}}|$$", flags="")
    assert _run("hi there", e) == "there hi|hi|hi there|hi there|$"


def test_unknown_group_left_literal():
    r = _rule(pattern="x", replacement="$5.00", flags="")
    assert _run("x", _entry(r)) == "$5.00"
    assert rules.warnings(r)


def test_known_groups_warn_nothing():
    r = _rule(pattern=r"(?P<w>\w+)", replacement="$1$<w>$&$$")
    assert rules.warnings(r) == []


def test_two_digit_group_falls_back_to_one():
    # `$10` with one group is `$1` then a literal 0, as in JavaScript.
    assert _run("x", _entry(pattern="(x)", replacement="$10", flags="")) == "x0"


def test_trim_applies_to_match_text():
    e = _entry(pattern="<b>.*?</b>", replacement="*{{match}}*", trim=["<b>", "</b>"], flags="")
    assert _run("<b>hi</b>", e) == "*hi*"


def test_run_order_and_off():
    a = _entry(pattern="a", replacement="b")
    b = _entry(pattern="b", replacement="c")
    assert _run("a", a, b) == "c"
    assert _run("a", b, a) == "b"
    assert _run("a", a, {**b, "off": True}) == "b"


@pytest.mark.parametrize(
    "over, kwargs, expected",
    [
        ({}, {}, None),
        ({"enabled": False}, {}, "disabled"),
        ({}, {"role": "user"}, "not for this role"),
        ({}, {"role": None}, "not for this role"),
        ({"applies": ["prompt"]}, {"phase": "display"}, "not for this phase"),
        ({"applies": ["display"]}, {"phase": "prompt"}, "not for this phase"),
        ({"min_depth": 2}, {"depth": 1}, "outside depth"),
        ({"min_depth": 2}, {"depth": 2}, None),
        ({"max_depth": 3}, {"depth": 3}, None),
        ({"max_depth": 3}, {"depth": 4}, "outside depth"),
        ({"targets": ["user"]}, {"role": "user"}, None),
    ],
)
def test_role_phase_depth_filtering(over, kwargs, expected):
    ask = {"role": "model", "phase": "display", "depth": 0, **kwargs}
    assert apply.skip_reason(_entry(**over), **ask) == expected


def test_switched_off_entry():
    ask = {"role": "model", "phase": "display", "depth": 0}
    assert apply.skip_reason(_entry(off=True), **ask) == "switched off"


def test_store_phase_only_rewrite_stored():
    ask = {"role": "model", "phase": "store", "depth": 99}
    assert apply.skip_reason(_entry(), **ask) == "not for this phase"
    # Depth is ignored for `store`: new text is always the newest.
    e = _entry(rewrite_stored=True, min_depth=5, max_depth=6)
    assert apply.skip_reason(e, **ask) is None
    # A pure write-time rule has no display/prompt phase at all.
    pure = _entry(rewrite_stored=True, applies=[])
    assert apply.skip_reason(pure, **ask) is None
    assert apply.skip_reason(pure, role="model", phase="display", depth=0) == "not for this phase"


def test_role_of():
    assert apply.role_of({"role": "user", "speaker": "You", "content": "hi"}) == "user"
    assert apply.role_of({"role": "assistant", "speaker": "Seraphine", "content": "hi"}) == "model"
    assert apply.role_of({"role": "assistant", "content": "hi"}) == "model"


def test_role_of_synthetic_is_none():
    for speaker in scenes.SYNTHETIC_SPEAKERS:
        assert apply.role_of({"role": "assistant", "speaker": speaker, "content": "x"}) is None
    note = {"role": "assistant", "speaker": scenes.DIRECTOR_SPEAKER, "content": "steer"}
    assert apply.role_of(note) is None
    transition = {"role": "assistant", "speaker": scenes.TRANSITION_SPEAKER, "content": "*x*"}
    assert apply.role_of(transition) is None
    roll = {"role": "assistant", "speaker": scenes.ROLL_SPEAKER, "content": "d20"}
    assert apply.role_of(roll) is None


def test_runtime_error_skipped(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaput")

    monkeypatch.setattr(rules, "expand", boom)
    e = _entry(name="Boom")
    assert _run("aaa", e) == "aaa"
    steps = apply.trace("aaa", [e], role="model", phase="display", depth=0)
    assert len(steps) == 1
    assert steps[0]["applied"] is False
    assert steps[0]["reason"] == "error: kaput"
    assert steps[0]["text_after"] == "aaa"


def test_runtime_error_logged_once(monkeypatch, caplog):
    monkeypatch.setattr(rules, "expand", lambda *a, **k: 1 / 0)
    apply._logged.clear()
    e = _entry()
    with caplog.at_level("WARNING", logger=apply.log.name):
        _run("aaa", e)
        _run("aaa", e)
    assert len(caplog.records) == 1
    assert "aaa" not in caplog.text  # a message's prose never goes in the log


def test_expansion_cap():
    e = _entry(pattern=".", replacement="x" * 5000, flags="gs", name="Boom")
    text = "y" * 100
    assert _run(text, e) == text
    (step,) = apply.trace(text, [e], role="model", phase="display", depth=0)
    assert step["applied"] is False
    assert step["reason"].startswith("error: ")


def test_zero_width_gm():
    assert _run("a\nb", _entry(pattern="^", replacement="> ", flags="gm")) == "> a\n> b"


def test_ascii_flag():
    assert _run("café", _entry(pattern=r"\w+", replacement="[$&]", flags="g")) == "[café]"
    assert _run("café", _entry(pattern=r"\w+", replacement="[$&]", flags="ga")) == "[caf]é"


def test_trace_reports_every_rule_in_order():
    hit = _entry(rule=_rule(id="r-1", name="Hit", pattern="a", replacement="b"), level="world")
    miss = _entry(rule=_rule(id="r-2", name="Miss", enabled=False), level="campaign")
    steps = apply.trace("aa", [hit, miss], role="model", phase="display", depth=0)
    assert [s["rule_id"] for s in steps] == ["r-1", "r-2"]
    assert [s["level"] for s in steps] == ["world", "campaign"]
    assert [s["name"] for s in steps] == ["Hit", "Miss"]
    assert steps[0]["applied"] is True
    assert steps[0]["reason"] is None
    assert steps[0]["matches"] == 2
    assert steps[0]["text_after"] == "bb"
    assert steps[1]["applied"] is False
    assert steps[1]["reason"] == "disabled"
    assert steps[1]["matches"] == 0
    assert steps[1]["text_after"] == "bb"


def test_normalise_fills_defaults_and_mints_id():
    r = rules.normalise({"pattern": "x"})
    assert r["id"].startswith("r-")
    assert r["enabled"] is True
    assert r["replacement"] == ""
    assert r["trim"] == []
    assert r["rewrite_stored"] is False
    assert r["min_depth"] is None and r["max_depth"] is None
    assert r["imported"] is None
    assert r["targets"] and r["applies"]
    assert rules.normalise({"pattern": "x", "id": "r-keep"})["id"] == "r-keep"
    assert rules.mint_id() != rules.mint_id()


def test_normalise_rejects():
    with pytest.raises(rules.RuleError):
        rules.normalise({"pattern": "x", "targets": []})
    with pytest.raises(rules.RuleError):
        rules.normalise({"pattern": "x", "applies": []})
    with pytest.raises(rules.RuleError) as flag:
        rules.normalise({"pattern": "x", "flags": "gy"})
    assert flag.value.field == "flags"
    with pytest.raises(rules.RuleError) as bad:
        rules.normalise({"pattern": "(", "enabled": True}, index=3)
    assert bad.value.field == "pattern"
    assert bad.value.index == 3
    # Saved disabled, the same pattern is kept for the author to fix.
    assert rules.normalise({"pattern": "(", "enabled": False})["pattern"] == "("


def test_normalise_accepts_pure_write_time_rule():
    r = rules.normalise({"pattern": "x", "applies": [], "rewrite_stored": True})
    assert r["applies"] == []


@pytest.mark.parametrize(
    "raw",
    [
        {"pattern": 5},
        {"pattern": "x", "enabled": "yes"},
        {"pattern": "x", "targets": ["narrator"]},
        {"pattern": "x", "applies": ["store"]},
        {"pattern": "x", "trim": "nope"},
        {"pattern": "x", "min_depth": -1},
        {"pattern": "x", "min_depth": True},
        {"pattern": "x", "min_depth": 5, "max_depth": 2},
        {"pattern": "x", "replacement": None},
        {"pattern": "x", "imported": "st"},
    ],
)
def test_normalise_rejects_bad_types(raw):
    with pytest.raises(rules.RuleError):
        rules.normalise(raw)


def test_compile_pattern_is_cached():
    r = _rule(pattern="q+")
    assert rules.compile_pattern(r) is rules.compile_pattern(dict(r))
