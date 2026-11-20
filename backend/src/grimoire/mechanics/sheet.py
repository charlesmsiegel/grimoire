"""Mechanics sheets and recorded checks under one exact module revision (086).

``fill_sheet`` checks an actor's values against one sheet type of a validated module and computes its
derived values. ``run_check`` grades a check from dice the caller recorded (this package never rolls) and
returns a ``CheckRecord`` that carries everything it was computed from, including the module revision.
``confirm`` re-derives a record under that same revision; a record is never re-graded under another one,
so editing a module changes new checks only.

Nothing here commits a roll, spends a resource or decides that a check applies to the fiction. A record is
an unaccepted proposal (Sprint 075 owns durable roll recording; 062's acceptance rules still hold).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from types import MappingProxyType

from grimoire.mechanics.expr import ExprError, Value, evaluate, integer
from grimoire.mechanics.module import (
    MAX_DICE, NUMERIC, MechanicsModule, ModuleInvalid, Problem, SheetType, parse_module,
)

MAX_MODIFIER = 1_000_000

SheetValue = int | str | tuple[str, ...]


class SheetInvalid(ValueError):
    """Values that do not fit a sheet type; ``problems`` lists each field at fault."""

    def __init__(self, problems: tuple[Problem, ...]) -> None:
        self.problems = problems
        super().__init__("; ".join(str(problem) for problem in problems))


class CheckError(ValueError):
    """A check that cannot be graded from what was supplied, with the reason."""


@dataclass(frozen=True)
class Sheet:
    """One actor's values for a sheet type, valid under exactly ``revision`` of module ``module``."""

    module: str
    revision: str
    sheet_type: str
    values: Mapping[str, SheetValue]
    derived: Mapping[str, int] = field(default_factory=dict)


def _value(sheet: SheetType, key: str, raw: object, problems: list[Problem]) -> SheetValue | None:
    spec = sheet.field(key)
    assert spec is not None
    path = f"values.{key}"
    if spec.type in NUMERIC:
        assert spec.minimum is not None and spec.maximum is not None
        if type(raw) is not int or not spec.minimum <= raw <= spec.maximum:
            problems.append(Problem(path, f"{spec.label} must be a whole number from {spec.minimum} to {spec.maximum}"))
            return None
        return raw
    if spec.type == "text":
        assert spec.max_length is not None
        if type(raw) is not str or len(raw) > spec.max_length:
            problems.append(Problem(path, f"{spec.label} must be text of at most {spec.max_length} characters"))
            return None
        return raw
    assert spec.max_items is not None
    if not isinstance(raw, (list, tuple)) or len(raw) > spec.max_items \
            or any(type(item) is not str or not item.strip() or len(item) > 200 for item in raw):
        problems.append(Problem(path, f"{spec.label} must be a list of at most {spec.max_items} entries, "
                                      "each nonempty text of at most 200 characters"))
        return None
    return tuple(raw)


def _derive(sheet: SheetType, values: Mapping[str, SheetValue]) -> dict[str, int]:
    scope: dict[str, Value] = {f.key: Fraction(values[f.key]) for f in sheet.fields  # type: ignore[arg-type]
                               if f.type in NUMERIC}
    derived: dict[str, int] = {}
    for key, expr in sheet.derived:
        try:
            derived[key] = integer(evaluate(expr, scope), f"derived value {key!r}")
        except ExprError as error:
            raise SheetInvalid((Problem(f"derived.{key}", str(error)),)) from None
        scope[key] = Fraction(derived[key])
    return derived


def _trusted(module: object, use: str) -> MechanicsModule:
    """``module`` if it is exactly what its own document validates to; a value built or altered by hand (say, an
    edited module stamped with an older revision) is refused, so a record never names rules it was not graded by."""
    if type(module) is not MechanicsModule:
        raise TypeError(f"{use} needs a validated MechanicsModule")
    try:
        same = parse_module(module.document) == module
    except ModuleInvalid:
        same = False
    if not same:
        raise CheckError(f"module {module.id} is not what its own document validates to; parse it again")
    return module


def fill_sheet(module: MechanicsModule, sheet_type: str, values: Mapping[str, object]) -> Sheet:
    """``values`` as a sheet of ``sheet_type``: unknown keys and wrong values are refused, absent fields
    take their defaults. ``SheetInvalid`` lists every field at fault."""
    _trusted(module, "fill_sheet")
    sheet = module.sheets.get(sheet_type) if type(sheet_type) is str else None
    if sheet is None:
        raise SheetInvalid((Problem("sheet_type", f"module {module.id} has no sheet type {sheet_type!r}"),))
    if not isinstance(values, Mapping):
        raise SheetInvalid((Problem("values", "must be an object of field values"),))
    problems: list[Problem] = []
    for key in values:
        if type(key) is not str or sheet.field(key) is None:
            problems.append(Problem(f"values.{key}", f"{sheet.label} has no field {key!r}"))
    filled: dict[str, SheetValue] = {}
    for spec in sheet.fields:
        made = _value(sheet, spec.key, values[spec.key], problems) if spec.key in values else spec.default
        if made is not None:
            filled[spec.key] = made  # type: ignore[assignment]
    if problems:
        raise SheetInvalid(tuple(problems))
    return Sheet(module.id, module.revision, sheet.id, MappingProxyType(filled),
                 MappingProxyType(_derive(sheet, filled)))


@dataclass(frozen=True)
class CheckRecord:
    """A graded check and everything it was graded from. An unaccepted proposal, not a commitment."""

    module: str
    revision: str
    check: str
    sheet_type: str
    values: Mapping[str, SheetValue]
    modifier: int
    difficulty: int | None
    dice: tuple[int, ...]
    total: int | None
    successes: int | None
    outcome: str


def _grade(module: MechanicsModule, sheet: Sheet, check_id: str, dice: tuple[int, ...], modifier: int,
           difficulty: int | None) -> CheckRecord:
    check = module.checks.get(check_id) if type(check_id) is str else None
    if check is None:
        raise CheckError(f"module {module.id} has no check {check_id!r}")
    if check.sheet != sheet.sheet_type:
        raise CheckError(f"{check.label} uses a {module.sheets[check.sheet].label} sheet, not "
                         f"{module.sheets[sheet.sheet_type].label}")
    if type(modifier) is not int or not -MAX_MODIFIER <= modifier <= MAX_MODIFIER:
        raise CheckError(f"modifier must be a whole number from {-MAX_MODIFIER} to {MAX_MODIFIER}")
    if difficulty is None:
        difficulty = check.difficulty
    elif type(difficulty) is not int or not -MAX_MODIFIER <= difficulty <= MAX_MODIFIER:
        raise CheckError(f"difficulty must be a whole number from {-MAX_MODIFIER} to {MAX_MODIFIER}")
    sheet_type = module.sheets[check.sheet]
    scope: dict[str, Value] = {f.key: Fraction(sheet.values[f.key]) for f in sheet_type.fields  # type: ignore[arg-type]
                               if f.type in NUMERIC}
    scope.update({key: Fraction(value) for key, value in sheet.derived.items()})
    scope["modifier"] = Fraction(modifier)
    uses_difficulty = any(expr.names() & {"difficulty", "margin"} for expr in
                          [check.dice.count, *([check.dice.target] if check.dice.target else []),
                           *([check.total] if check.total else []), *(o.when for o in check.outcomes)])
    if difficulty is None and uses_difficulty:
        raise CheckError(f"{check.label} needs a difficulty and has none of its own")
    scope["difficulty"] = Fraction(difficulty if difficulty is not None else 0)
    try:
        count = integer(evaluate(check.dice.count, scope), "the number of dice")
        target = None if check.dice.target is None else integer(evaluate(check.dice.target, scope), "the target")
    except ExprError as error:
        raise CheckError(str(error)) from None
    if not 1 <= count <= MAX_DICE:
        raise CheckError(f"{check.label} rolls {count} dice; a check rolls 1 to {MAX_DICE}")
    if type(dice) is not tuple or len(dice) != count:
        raise CheckError(f"{check.label} needs exactly {count} recorded dice, one tuple of faces")
    if any(type(face) is not int or not 1 <= face <= check.dice.sides for face in dice):
        raise CheckError(f"every recorded face must be a whole number from 1 to {check.dice.sides}")
    scope.update({"dice": Fraction(count), "dice_sum": Fraction(sum(dice)), "ones": Fraction(dice.count(1)),
                  "highest": Fraction(max(dice)), "lowest": Fraction(min(dice))})
    if check.single_die:
        scope["natural"] = Fraction(dice[0])
    successes = None
    if target is not None:
        successes = sum(face >= target for face in dice)
        scope["successes"] = Fraction(successes)
    try:
        if check.total is not None:
            total: int | None = integer(evaluate(check.total, scope), "the total")
        elif successes is not None:
            total = successes
        else:
            total = sum(dice)
        scope["total"] = Fraction(total)
        scope["margin"] = Fraction(total - (difficulty if difficulty is not None else 0))
        outcome = next((o.label for o in check.outcomes if evaluate(o.when, scope) is True), None)
    except ExprError as error:
        raise CheckError(str(error)) from None
    if outcome is None:
        raise CheckError(f"no outcome of {check.label} matches this roll; its outcomes must cover every roll")
    return CheckRecord(module.id, module.revision, check.id, sheet.sheet_type, sheet.values, modifier, difficulty,
                       dice, total, successes, outcome)


def run_check(module: MechanicsModule, sheet: Sheet, check_id: str, *, dice: tuple[int, ...],
              modifier: int = 0, difficulty: int | None = None) -> CheckRecord:
    """Grade ``check_id`` for ``sheet`` from recorded ``dice``. The sheet must be valid under this very
    revision: a sheet filled under another revision is refused, never silently reinterpreted."""
    _trusted(module, "run_check")
    if type(sheet) is not Sheet:
        raise TypeError("run_check needs a filled Sheet")
    if (sheet.module, sheet.revision) != (module.id, module.revision):
        raise CheckError(f"this sheet was filled under {sheet.module} {sheet.revision}, not {module.id} "
                         f"{module.revision}; fill it again under the module it is checked with")
    if not isinstance(sheet.values, Mapping):
        raise CheckError("this sheet holds no field values")
    try:
        refilled = fill_sheet(module, sheet.sheet_type, dict(sheet.values))  # a forged Sheet value is not trusted
    except SheetInvalid as error:
        raise CheckError(f"this sheet's values do not fit its module: {error}") from None
    if refilled != sheet:
        raise CheckError("this sheet's values or derived values are not what its module computes")
    return _grade(module, sheet, check_id, dice, modifier, difficulty)


def confirm(module: MechanicsModule, record: CheckRecord) -> None:
    """Re-derive ``record`` under the exact revision it names; ``CheckError`` if it disagrees or the module is
    another revision. A record is never re-graded under an edited module."""
    _trusted(module, "confirm")
    if type(record) is not CheckRecord:
        raise TypeError("confirm needs a CheckRecord")
    if (record.module, record.revision) != (module.id, module.revision):
        raise CheckError(f"this record was graded under {record.module} {record.revision}; it is confirmed "
                         "only against that revision, never re-graded under another")
    if not isinstance(record.values, Mapping):
        raise CheckError("the record holds no sheet values")
    try:
        sheet = fill_sheet(module, record.sheet_type, dict(record.values))
    except SheetInvalid as error:
        raise CheckError(f"the record's sheet values do not fit: {error}") from None
    again = _grade(module, sheet, record.check, record.dice, record.modifier, record.difficulty)
    if again != record:
        raise CheckError("the record does not match what its own revision computes")


__all__ = ["CheckError", "CheckRecord", "Sheet", "SheetInvalid", "confirm", "fill_sheet", "run_check"]
