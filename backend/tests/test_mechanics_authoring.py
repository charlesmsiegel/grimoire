"""Authored mechanics modules (086): the expression language, strict module validation with readable
problems, sheets, recorded-dice checks and revision pinning. Pure; the files are test_mechanicsstore.py."""

from __future__ import annotations

import re
from fractions import Fraction

import pytest

from grimoire.mechanics import (
    TEMPLATES, CheckError, ExprError, ModuleInvalid, SheetInvalid, confirm, fill_sheet, parse_module,
    revision_of, run_check, template, validate_module,
)
from grimoire.mechanics.expr import MAX_DEPTH, MAX_TEXT, evaluate, integer, kind_of, parse


def problems(document: object) -> list[str]:
    return [str(problem) for problem in validate_module(document)]


def minimal() -> dict:
    """The smallest useful module: one sheet, one check."""
    return {
        "format": "grimoire.mechanics/1", "id": "duel", "name": "Duel", "version": "1",
        "sheets": {"fencer": {"label": "Fencer", "fields": [
            {"key": "blade", "label": "Blade", "type": "number", "min": 0, "max": 5, "default": 1},
            {"key": "motto", "label": "Motto", "type": "text"},
        ], "derived": {"edge": "blade * 2"}}},
        "checks": {"thrust": {"label": "Thrust", "sheet": "fencer", "difficulty": 10,
                              "dice": {"count": "1", "sides": 12},
                              "total": "natural + edge + modifier",
                              "outcomes": [{"label": "hit", "when": "margin >= 0"},
                                           {"label": "miss", "when": "margin < 0"}]}},
    }


# -- the expression language ------------------------------------------------------------------------------
@pytest.mark.parametrize("text, value", [
    ("1 + 2 * 3", 7), ("(1 + 2) * 3", 9), ("-3 + 1", -2), ("7 / 2", Fraction(7, 2)), ("floor(7 / 2)", 3),
    ("ceil(7 / 2)", 4), ("floor(-7 / 2)", -4), ("abs(-4)", 4), ("min(3, 1, 2)", 1), ("max(3, 9)", 9),
    ("2 - 1 - 1", 0), ("8 / 2 / 2", 2), ("- - 2", 2),
])
def test_arithmetic_is_exact_and_conventional(text, value):
    assert evaluate(parse(text), {}) == value


@pytest.mark.parametrize("text, value", [
    ("1 < 2 and 2 < 3", True), ("not 1 == 1 or true", True), ("not (1 == 1 or true)", False),
    ("1 != 2", True), ("true == false", False), ("3 >= 3", True),
])
def test_conditions(text, value):
    assert evaluate(parse(text), {}) is value


@pytest.mark.parametrize("text, reason", [
    ("", "empty"), ("1 +", "ends too early"), ("1 2", "after a complete"), ("(1", "expected ')'"),
    ("__import__('os')", "unexpected character"), ("x.y", "unexpected character"), ("a[0]", "unexpected character"),
    ("open(1)", "unknown function"), ("min(1)", "takes 2 to 8 arguments"), ("1 < 2 < 3", "do not chain"),
    ("Strength", "lowercase"), ("1 ** 2", "unexpected '*'"), ("99999999", "larger than"), ("and", "needs something"),
    ("'text'", "unexpected character"), ("1;2", "unexpected character"), ("lambda: 1", "unexpected character"),
])
def test_anything_but_bounded_arithmetic_is_refused_at_parse(text, reason):
    with pytest.raises(ExprError, match=re.escape(reason)):
        parse(text)


def test_size_and_depth_are_bounded():
    with pytest.raises(ExprError, match="at most"):
        parse("1+" * MAX_TEXT + "1")
    with pytest.raises(ExprError, match="nested"):
        parse("(" * (MAX_DEPTH + 1) + "1" + ")" * (MAX_DEPTH + 1))
    with pytest.raises(ExprError, match="beyond"):
        evaluate(parse("999999 * 999999 * 999999"), {})
    with pytest.raises(ExprError, match="division by zero"):
        evaluate(parse("1 / (2 - 2)"), {})


@pytest.mark.parametrize("text, reason", [
    ("1 + (2 > 1)", "numbers on both sides"), ("1 and true", "joins conditions"), ("not 3", "a condition"),
    ("-(1 < 2)", "a number"), ("floor(true)", "takes numbers"), ("1 == true", "compares a number"),
])
def test_types_are_checked_before_anything_runs(text, reason):
    with pytest.raises(ExprError, match=reason):
        kind_of(parse(text), {})


def test_a_fraction_is_never_silently_rounded():
    with pytest.raises(ExprError, match="floor"):
        integer(evaluate(parse("5 / 2"), {}), "the total")


# -- validation -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_the_v1_reference_modules_validate_as_templates(name):
    module = parse_module(template(name))
    assert module.revision == revision_of(template(name)) and module.revision.startswith("sha256:")
    assert template(name) is not template(name)  # a fresh copy each time


def test_a_small_module_with_one_sheet_and_one_check():
    module = parse_module(minimal())
    assert list(module.sheets) == ["fencer"] and list(module.checks) == ["thrust"]
    assert module.family == "generic"


def test_revision_ignores_key_order_but_not_meaning():
    document = minimal()
    reordered = dict(reversed(list(document.items())))
    assert revision_of(reordered) == revision_of(document)
    document["checks"]["thrust"]["difficulty"] = 11
    assert revision_of(document) != revision_of(minimal())
    assert revision_of({"a": "1"}) != revision_of({"a": 1})


def test_every_problem_is_reported_with_its_path():
    document = minimal()
    document["sheets"]["fencer"]["fields"][0]["max"] = 99999999
    document["sheets"]["fencer"]["derived"]["reach"] = "blde + 1"
    document["checks"]["thrust"]["outcomes"][0]["whne"] = "x"
    document["colour"] = "red"
    found = problems(document)
    assert "colour: unknown key" not in found  # top-level paths start with $ for unknown keys
    assert "$.colour: unknown key" in found
    assert any(p.startswith("sheets.fencer.fields[0].max: must be a whole number") for p in found)
    assert "sheets.fencer.derived.reach: unknown name 'blde' (did you mean 'blade'?)" in found
    # the check names a sheet with problems of its own, so it is checked once that sheet is valid
    assert not any(p.startswith("checks.") for p in found)


def test_check_problems_are_named_precisely():
    document = minimal()
    document["checks"]["thrust"]["total"] = "naturl + motto + successes"
    document["checks"]["thrust"]["outcomes"].append({"label": "hit", "when": "total"})
    found = problems(document)
    assert "checks.thrust.total: unknown name 'naturl' (did you mean 'natural'?)" in found
    assert "checks.thrust.total: unknown name 'motto': 'motto' is a text field and holds no number" in found
    assert any("'successes' needs dice.target" in p for p in found)
    assert "checks.thrust.outcomes[2].when: must be a condition (true or false)" in found


@pytest.mark.parametrize("key", ["hooks", "script", "Python", "code"])
def test_executable_hooks_are_refused_everywhere(key):
    for where in ("top", "sheet", "check", "dice", "field"):
        document = minimal()
        target = {"top": document, "sheet": document["sheets"]["fencer"], "check": document["checks"]["thrust"],
                  "dice": document["checks"]["thrust"]["dice"],
                  "field": document["sheets"]["fencer"]["fields"][0]}[where]
        target[key] = "import os; os.system('rm -rf /')"
        with pytest.raises(ModuleInvalid, match="executable rule hooks are not supported"):
            parse_module(document)


def test_natural_belongs_to_single_die_checks_only():
    document = minimal()
    document["checks"]["thrust"]["dice"]["count"] = "blade"
    assert any("'natural' is the one die of a single-die check" in p for p in problems(document))


def test_derived_values_are_ordered_and_loops_refused():
    document = minimal()
    document["sheets"]["fencer"]["derived"] = {"b": "a + 1", "a": "blade * 2", "edge": "b + a"}
    module = parse_module(document)
    assert [key for key, _ in module.sheets["fencer"].derived] == ["a", "b", "edge"]
    document["sheets"]["fencer"]["derived"] = {"edge": "1", "x": "y", "y": "x + blade"}
    assert any("loop: x, y" in p for p in problems(document))
    document["sheets"]["fencer"]["derived"] = {"edge": "1", "x": "x + 1"}
    assert any("loop" in p for p in problems(document))


@pytest.mark.parametrize("change, message", [
    (lambda d: d["sheets"]["fencer"]["fields"].append({"key": "blade", "label": "B", "type": "dots"}),
     "already a field"),
    (lambda d: d["sheets"]["fencer"]["fields"].append({"key": "modifier", "label": "M", "type": "dots"}),
     "checks supply themselves"),
    (lambda d: d["sheets"]["fencer"]["derived"].update({"blade": "1"}), "same name as a field"),
    (lambda d: d["sheets"]["fencer"]["fields"].append({"key": "k", "label": "K", "type": "ref"}),
     "'ref' fields are not supported"),
    (lambda d: d["sheets"]["fencer"]["fields"].append({"key": "hp", "label": "HP", "type": "resource"}),
     "is required for a resource"),
    (lambda d: d["sheets"]["fencer"]["fields"][0].update({"min": 4, "max": 2}), "below min"),
    (lambda d: d["sheets"]["fencer"]["fields"][0].update({"default": True}), "whole number"),
    (lambda d: d["checks"]["thrust"].update({"sheet": "fencr"}), "did you mean 'fencer'"),
    (lambda d: d["checks"]["thrust"]["dice"].update({"sides": 1}), "from 2 to"),
    (lambda d: d["checks"]["thrust"]["outcomes"].clear(), "1 to 16 outcomes"),
    (lambda d: d.update({"format": "grimoire.mechanics/2"}), "must be 'grimoire.mechanics/1'"),
    (lambda d: d.update({"id": "Duel!"}), "a module id"),
    (lambda d: d.update({"family": "gurps"}), "must be one of"),
    (lambda d: d.update({"sheets": {}}), "1 to 16 sheet types"),
    (lambda d: d.pop("checks"), "checks: is required"),
])
def test_malformed_parts_are_refused_with_a_reason(change, message):
    document = minimal()
    change(document)
    assert any(message in p for p in problems(document)), problems(document)


def test_family_constraints():
    d20 = template("d20_basic")
    d20["checks"]["stealth"]["dice"]["sides"] = 12
    assert any("rolls exactly one d20" in p for p in problems(d20))
    pool = template("pool_basic")
    del pool["checks"]["brawl"]["dice"]["target"]
    found = problems(pool)
    assert any("pool module's check counts successes" in p for p in found)
    assert any("'successes' needs dice.target" in p for p in found)


@pytest.mark.parametrize("document", [None, [], "x", {"format": "grimoire.mechanics/1"}])
def test_not_a_module_at_all(document):
    with pytest.raises(ModuleInvalid):
        parse_module(document)


# -- sheets and checks ------------------------------------------------------------------------------------
def test_a_sheet_takes_defaults_and_refuses_bad_values():
    module = parse_module(template("pool_basic"))
    sheet = fill_sheet(module, "medium", {"wits": 3, "occult": 2})
    assert sheet.values["essence"] == 10 and sheet.values["health"] == 0 and sheet.values["gear"] == ()
    assert sheet.derived == {"sight_pool": 5, "awareness": 4}
    with pytest.raises(SheetInvalid) as refused:
        fill_sheet(module, "medium", {"wits": 6, "occult": "2", "gear": ["", "x"], "luck": 1, "quirk": 7})
    paths = [p.path for p in refused.value.problems]
    assert paths == ["values.luck", "values.wits", "values.occult", "values.quirk", "values.gear"]
    with pytest.raises(SheetInvalid, match="no sheet type"):
        fill_sheet(module, "warrior", {})
    with pytest.raises(SheetInvalid):
        fill_sheet(module, "medium", {"wits": True})


def test_d20_check_from_recorded_dice():
    module = parse_module(template("d20_basic"))
    sheet = fill_sheet(module, "warrior", {"strength": 16, "athletics": 3})
    record = run_check(module, sheet, "athletics", dice=(11,), modifier=1)
    assert (record.total, record.outcome, record.difficulty) == (18, "success", 12)
    assert run_check(module, sheet, "athletics", dice=(20,), difficulty=40).outcome == "critical success"
    assert run_check(module, sheet, "athletics", dice=(1,)).outcome == "critical failure"
    assert run_check(module, sheet, "athletics", dice=(2,)).outcome == "failure"
    confirm(module, record)


def test_pool_check_counts_faces_at_the_target():
    module = parse_module(template("pool_basic"))
    sheet = fill_sheet(module, "medium", {"wits": 3, "occult": 2})
    assert run_check(module, sheet, "perception", dice=(10, 6, 1, 3, 7)).successes == 3
    botch = run_check(module, sheet, "perception", dice=(1, 2, 3, 4, 5))
    assert (botch.successes, botch.outcome) == (0, "botch")
    assert run_check(module, sheet, "perception", dice=(2, 2, 3, 4, 5)).outcome == "failure"
    assert run_check(module, sheet, "perception", dice=(9, 9), modifier=-3).outcome == "success"
    assert run_check(module, sheet, "perception", dice=(9,), modifier=-9).total == 1  # max(1, ...) floor


@pytest.mark.parametrize("call, message", [
    (lambda m, s: run_check(m, s, "perception", dice=(1, 2)), "exactly 5 recorded dice"),
    (lambda m, s: run_check(m, s, "perception", dice=(1, 2, 3, 4, 11)), "from 1 to 10"),
    (lambda m, s: run_check(m, s, "perception", dice=[1, 2, 3, 4, 5]), "one tuple"),
    (lambda m, s: run_check(m, s, "perception", dice=(1, 2, 3, 4, True)), "whole number"),
    (lambda m, s: run_check(m, s, "perception", dice=(1,) * 5, modifier=1.5), "modifier"),
    (lambda m, s: run_check(m, s, "perception", dice=(1,) * 5, difficulty="6"), "difficulty"),
    (lambda m, s: run_check(m, s, "sneak", dice=(1,)), "no check"),
])
def test_a_check_refuses_what_it_cannot_grade(call, message):
    module = parse_module(template("pool_basic"))
    sheet = fill_sheet(module, "medium", {"wits": 3, "occult": 2})
    with pytest.raises(CheckError, match=re.escape(message)):
        call(module, sheet)


def test_a_check_with_no_matching_outcome_fails_closed():
    document = minimal()
    document["checks"]["thrust"]["outcomes"] = [{"label": "hit", "when": "margin >= 0"}]
    module = parse_module(document)
    sheet = fill_sheet(module, "fencer", {})
    with pytest.raises(CheckError, match="no outcome"):
        run_check(module, sheet, "thrust", dice=(1,))


def test_a_check_without_a_difficulty_needs_one_supplied():
    document = minimal()
    del document["checks"]["thrust"]["difficulty"]
    module = parse_module(document)
    sheet = fill_sheet(module, "fencer", {})
    with pytest.raises(CheckError, match="needs a difficulty"):
        run_check(module, sheet, "thrust", dice=(5,))
    assert run_check(module, sheet, "thrust", dice=(5,), difficulty=5).outcome == "hit"


def test_a_forged_sheet_is_not_trusted_either_way():
    from dataclasses import replace
    module = parse_module(minimal())
    sheet = fill_sheet(module, "fencer", {"blade": 2})
    for forged in (replace(sheet, derived={"edge": 99}), replace(sheet, values={**sheet.values, "blade": 50})):
        with pytest.raises((CheckError, SheetInvalid)):
            run_check(module, forged, "thrust", dice=(5,))


def test_proving_scenario_an_edit_affects_new_checks_and_never_a_recorded_one():
    """A malformed module cannot bind; a valid edit changes only checks made under the new revision."""
    original = parse_module(minimal())
    sheet = fill_sheet(original, "fencer", {"blade": 2})
    committed = run_check(original, sheet, "thrust", dice=(6,))
    assert (committed.total, committed.outcome) == (10, "hit")

    malformed = minimal()
    malformed["checks"]["thrust"]["total"] = "natural + edge +"
    with pytest.raises(ModuleInvalid):
        parse_module(malformed)  # no module value exists, so nothing can be bound to it

    edited_document = minimal()
    edited_document["sheets"]["fencer"]["derived"]["edge"] = "blade"
    edited = parse_module(edited_document)
    assert edited.revision != original.revision

    with pytest.raises(CheckError, match="fill it again"):
        run_check(edited, sheet, "thrust", dice=(6,))  # an old sheet is not silently reinterpreted
    fresh = run_check(edited, fill_sheet(edited, "fencer", {"blade": 2}), "thrust", dice=(6,))
    assert (fresh.total, fresh.outcome) == (8, "miss")

    confirm(original, committed)  # the recorded roll still holds under the revision it names
    assert (committed.total, committed.outcome, committed.revision) == (10, "hit", original.revision)
    with pytest.raises(CheckError, match="never re-graded"):
        confirm(edited, committed)


def test_a_tampered_record_does_not_confirm():
    from dataclasses import replace
    module = parse_module(minimal())
    record = run_check(module, fill_sheet(module, "fencer", {}), "thrust", dice=(9,))
    with pytest.raises(CheckError, match="does not match"):
        confirm(module, replace(record, outcome="miss"))
    with pytest.raises(CheckError, match="does not match"):
        confirm(module, replace(record, total=1))


# -- found in review ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("sheet", [["fencer"], {"x": 1}, 7, None])
def test_a_check_naming_its_sheet_with_a_non_text_value_is_a_problem_not_a_crash(sheet):
    document = minimal()
    document["checks"]["thrust"]["sheet"] = sheet
    assert any(p.startswith("checks.thrust.sheet: names no sheet type") for p in problems(document))


def test_text_that_is_not_unicode_is_a_problem_not_a_crash():
    document = minimal()
    document["name"] = "Du\ud800el"
    assert problems(document) == ["$: holds a value that is not plain JSON text (such as a lone surrogate)"]


def test_a_derived_value_using_a_broken_one_is_not_reported_twice():
    document = minimal()
    document["sheets"]["fencer"]["derived"] = {"edge": "blade +", "big": "edge + 1", "odd": "blade > 1",
                                               "worse": "odd + 1"}
    found = problems(document)
    assert len(found) == 2 and found[0].startswith("sheets.fencer.derived.edge:"), found
    assert found[1] == "sheets.fencer.derived.odd: must be a number, not a condition"


def test_bad_dice_do_not_hide_the_totals_and_outcomes_problems():
    document = minimal()
    document["checks"]["thrust"]["dice"]["count"] = "nope"
    document["checks"]["thrust"]["total"] = "qqq"
    document["checks"]["thrust"]["outcomes"][0]["when"] = "zzz"
    paths = [p.split(":")[0] for p in problems(document)]
    assert paths == ["checks.thrust.dice.count", "checks.thrust.total", "checks.thrust.outcomes[0].when"]


@pytest.mark.parametrize("formula, reason", [("blade / 2", "not a whole number"),
                                             ("10 / (blade - blade)", "division by zero")])
def test_a_sheet_type_that_cannot_fill_with_its_own_defaults_is_a_problem(formula, reason):
    document = minimal()
    document["sheets"]["fencer"]["derived"]["edge"] = formula
    assert any(p.startswith("sheets.fencer.derived.edge:") and reason in p for p in problems(document))


def test_a_forged_module_cannot_stamp_a_record_with_another_revision():
    from dataclasses import replace
    original = parse_module(minimal())
    edited_document = minimal()
    edited_document["sheets"]["fencer"]["derived"]["edge"] = "blade * 50"
    forged = replace(parse_module(edited_document), revision=original.revision)
    sheet = fill_sheet(original, "fencer", {"blade": 2})
    for call in (lambda: run_check(forged, sheet, "thrust", dice=(6,)), lambda: fill_sheet(forged, "fencer", {})):
        with pytest.raises(CheckError, match="parse it again"):
            call()


def test_a_forged_sheet_is_refused_as_a_check_error():
    from dataclasses import replace
    module = parse_module(minimal())
    sheet = fill_sheet(module, "fencer", {"blade": 2})
    for forged in (replace(sheet, values={**sheet.values, "blade": 50}), replace(sheet, sheet_type="nope"),
                   replace(sheet, values=None), replace(sheet, derived={"edge": 99})):
        with pytest.raises(CheckError):
            run_check(module, forged, "thrust", dice=(5,))
    record = run_check(module, sheet, "thrust", dice=(5,))
    with pytest.raises(CheckError):
        confirm(module, replace(record, values=None))


@pytest.mark.parametrize("text, reason", [("１２ + ٣", "unexpected character"), ("café", "unexpected character")])
def test_only_ascii_digits_and_names(text, reason):
    with pytest.raises(ExprError, match=reason):
        parse(text)


def test_leading_zeros_are_just_a_number():
    assert evaluate(parse("00000001"), {}) == 1


@pytest.mark.parametrize("text", ["1 + 2", " 1", "1 ", "1 + 2"])
def test_other_spaces_are_named_wherever_they_are(text):
    with pytest.raises(ExprError, match="plain spaces"):
        parse(text)


def test_plain_spaces_tabs_and_line_breaks_separate_parts():
    assert evaluate(parse(" 1\t+\n2\r\n "), {}) == 3


def test_grading_a_trusted_module_again_does_not_revalidate_it(monkeypatch):
    import grimoire.mechanics.sheet as sheet_module
    module = parse_module(minimal())
    sheet = fill_sheet(module, "fencer", {"blade": 2})
    calls = []
    real = sheet_module.parse_module
    monkeypatch.setattr(sheet_module, "parse_module", lambda doc: calls.append(1) or real(doc))
    confirm(module, run_check(module, sheet, "thrust", dice=(5,)))
    assert calls == []
    other = parse_module(minimal())  # an equal module that is another value is checked once
    run_check(other, fill_sheet(other, "fencer", {"blade": 2}), "thrust", dice=(5,))
    assert calls == [1]
