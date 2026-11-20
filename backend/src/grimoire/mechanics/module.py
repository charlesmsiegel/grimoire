"""Authored mechanics modules: a manifest, sheet types and named checks, strictly validated (086).

A module is a plain JSON document its author owns. ``validate_module`` reports every problem it finds,
each with the path of the part at fault and a reason a person can act on; ``parse_module`` returns the
validated, immutable ``MechanicsModule`` or raises ``ModuleInvalid`` with those problems. A module with any
problem has no value, so it can never be published or bound.

A validated module has a content ``revision``: a digest of its document's meaning (key order and
whitespace do not matter, every value does). Sheets and check records pin that revision, so an edit makes a
new module and never alters what was computed under the old one.

Rules from an authored module never run code. A document asking for executable hooks or scripts is
refused (Sprint 007's obligation; isolation is that sprint's moonshot).
"""

from __future__ import annotations

import difflib
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from fractions import Fraction

from grimoire.mechanics.expr import (
    FUNCTIONS, KEYWORDS, NAME, Expr, ExprError, Kind, Value, evaluate, integer, kind_of, parse,
)

FORMAT = "grimoire.mechanics/1"
FAMILIES = ("generic", "d20", "pool")
FIELD_TYPES = ("number", "dots", "resource", "track", "text", "list")
NUMERIC = frozenset({"number", "dots", "resource", "track"})
#: Prototype input bounds, not game limits.
MAX_SHEETS, MAX_FIELDS, MAX_DERIVED, MAX_CHECKS, MAX_OUTCOMES = 16, 64, 32, 64, 16
MAX_LABEL, MAX_TEXT_FIELD, MAX_LIST_ITEMS = 120, 2000, 100
MAX_DICE, MAX_SIDES, MAX_NUMBER = 100, 1000, 1_000_000
#: Names the roller supplies: in every check formula, and once the dice are read.
CHECK_INPUTS = ("modifier", "difficulty")
DICE_NAMES = ("dice", "dice_sum", "ones", "highest", "lowest")
#: Keys a module may never carry: authored rules compute, they do not execute.
#: Names a field or derived value may not take: the roller's, the dice's, the grader's and the language's own.
RESERVED = frozenset(CHECK_INPUTS + DICE_NAMES + ("natural", "successes", "total", "margin")) | KEYWORDS \
    | frozenset(FUNCTIONS)
EXECUTABLE = frozenset({"hooks", "hook", "script", "scripts", "code", "python", "javascript", "lua", "exec"})


@dataclass(frozen=True)
class Problem:
    """One thing wrong with a document: where (a dotted path, ``$`` for the whole) and why."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class ModuleInvalid(ValueError):
    """A document that is not a valid module; ``problems`` lists every reason found."""

    def __init__(self, problems: tuple[Problem, ...]) -> None:
        self.problems = problems
        super().__init__("; ".join(str(problem) for problem in problems[:5])
                         + (f"; and {len(problems) - 5} more" if len(problems) > 5 else ""))


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    type: str
    default: object
    minimum: int | None = None
    maximum: int | None = None
    max_length: int | None = None
    max_items: int | None = None


@dataclass(frozen=True)
class SheetType:
    id: str
    label: str
    fields: tuple[Field, ...]
    #: Derived values in an order where each uses only fields and earlier derived values.
    derived: tuple[tuple[str, Expr], ...] = ()

    def field(self, key: str) -> Field | None:
        return next((f for f in self.fields if f.key == key), None)

    def scope(self) -> dict[str, Kind]:
        names: dict[str, Kind] = {f.key: "number" for f in self.fields if f.type in NUMERIC}
        names.update({key: "number" for key, _ in self.derived})
        return names


@dataclass(frozen=True)
class Dice:
    count: Expr
    sides: int
    #: Faces at or above the target count as successes; without one, no ``successes`` are counted.
    target: Expr | None = None


@dataclass(frozen=True)
class Outcome:
    label: str
    when: Expr


@dataclass(frozen=True)
class Check:
    id: str
    label: str
    sheet: str
    dice: Dice
    total: Expr | None
    difficulty: int | None
    outcomes: tuple[Outcome, ...]

    @property
    def single_die(self) -> bool:
        return self.dice.count.text.strip() == "1"


@dataclass(frozen=True)
class MechanicsModule:
    id: str
    name: str
    version: str
    family: str
    sheets: Mapping[str, SheetType]
    checks: Mapping[str, Check]
    revision: str
    document: Mapping[str, object] = field(repr=False, compare=False)


# -- reading helpers -------------------------------------------------------------------------------------------
class _Report:
    def __init__(self) -> None:
        self.problems: list[Problem] = []

    def add(self, path: str, message: str) -> None:
        self.problems.append(Problem(path, message))


def _suggest(name: str, known: object) -> str:
    close = difflib.get_close_matches(name, sorted(known), n=1)  # type: ignore[call-overload]
    return f" (did you mean {close[0]!r}?)" if close else ""


def _keys(report: _Report, path: str, value: Mapping[str, object], required: tuple[str, ...],
          optional: tuple[str, ...]) -> bool:
    """Report every unknown, executable or missing key; whether the required ones are all there (so the
    rest of this part can still be checked and reported)."""
    for key in value:
        if type(key) is not str:
            report.add(path, "keys must be text")
        elif key.lower() in EXECUTABLE:
            report.add(f"{path}.{key}", "executable rule hooks are not supported: an authored module's rules "
                       "compute with formulas and never run code")
        elif key not in required and key not in optional:
            report.add(f"{path}.{key}", f"unknown key{_suggest(key, required + optional)}")
    missing = [key for key in required if key not in value]
    for key in missing:
        report.add(f"{path}.{key}", "is required")
    return not missing


def _mapping(report: _Report, path: str, value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        report.add(path, "must be an object")
        return None
    return value


def _ident(report: _Report, path: str, value: object, what: str) -> bool:
    if type(value) is not str or NAME.fullmatch(value) is None:
        report.add(path, f"{what} must be 1-64 lowercase letters, digits and _, starting with a letter")
        return False
    return True


def _text(report: _Report, path: str, value: object, limit: int = MAX_LABEL) -> str | None:
    if type(value) is not str or not value.strip() or len(value) > limit:
        report.add(path, f"must be nonempty text of at most {limit} characters")
        return None
    return value


def _int(report: _Report, path: str, value: object, low: int, high: int) -> int | None:
    if type(value) is not int or not low <= value <= high:
        report.add(path, f"must be a whole number from {low} to {high}")
        return None
    return value


def _expr(report: _Report, path: str, value: object, scope: Mapping[str, Kind], want: Kind,
          hint: Mapping[str, str] | None = None) -> Expr | None:
    try:
        expr = parse(value)
    except ExprError as error:
        report.add(path, str(error))
        return None
    unknown = [name for name in sorted(expr.names()) if name not in scope]
    for name in unknown:
        why = (hint or {}).get(name)
        report.add(path, f"unknown name {name!r}" + (f": {why}" if why else _suggest(name, scope)))
    if unknown:
        return None
    try:
        kind = kind_of(expr, dict(scope))
    except ExprError as error:
        report.add(path, str(error))
        return None
    if kind != want:
        report.add(path, "must be a condition (true or false)" if want == "bool"
                   else "must be a number, not a condition")
        return None
    return expr


# -- sheets ----------------------------------------------------------------------------------------------------
_FIELD_KEYS = {
    "number": ("min", "max", "default"),
    "dots": ("max", "default"),
    "resource": ("max", "default"),
    "track": ("max", "default"),
    "text": ("max_length", "default"),
    "list": ("max_items",),
}


def _field(report: _Report, path: str, raw: object) -> Field | None:
    value = _mapping(report, path, raw)
    if value is None:
        return None
    kind = value.get("type")
    if kind == "ref":
        report.add(f"{path}.type", "'ref' fields are not supported yet: a reference must name a source-qualified "
                   "record, which this slice cannot check")
        return None
    if kind not in FIELD_TYPES:
        report.add(f"{path}.type", f"must be one of {', '.join(FIELD_TYPES)}")
        return None
    if not _keys(report, path, value, ("key", "label", "type"), _FIELD_KEYS[kind]):  # type: ignore[index]
        return None
    ok = _ident(report, f"{path}.key", value["key"], "a field key")
    if ok and value["key"] in RESERVED:
        report.add(f"{path}.key", f"{value['key']!r} is a name checks supply themselves; choose another")
        ok = False
    label = _text(report, f"{path}.label", value["label"])
    if not ok or label is None:
        return None
    key = value["key"]
    assert isinstance(key, str)
    if kind == "number":
        low = _int(report, f"{path}.min", value.get("min", 0), -MAX_NUMBER, MAX_NUMBER)
        high = _int(report, f"{path}.max", value.get("max", MAX_NUMBER), -MAX_NUMBER, MAX_NUMBER)
        if low is None or high is None:
            return None
        if low > high:
            report.add(f"{path}.max", f"is below min {low}")
            return None
        default = _int(report, f"{path}.default", value.get("default", low), low, high)
        return None if default is None else Field(key, label, "number", default, low, high)
    if kind in ("dots", "resource", "track"):
        cap = 10 if kind == "dots" else MAX_NUMBER
        if kind != "dots" and "max" not in value:
            report.add(f"{path}.max", f"is required for a {kind}")
            return None
        high = _int(report, f"{path}.max", value.get("max", 5), 1, cap)
        if high is None:
            return None
        default = _int(report, f"{path}.default", value.get("default", high if kind == "resource" else 0), 0, high)
        return None if default is None else Field(key, label, kind, default, 0, high)  # type: ignore[arg-type]
    if kind == "text":
        limit = _int(report, f"{path}.max_length", value.get("max_length", MAX_TEXT_FIELD), 1, MAX_TEXT_FIELD)
        if limit is None:
            return None
        default = value.get("default", "")
        if type(default) is not str or len(default) > limit:
            report.add(f"{path}.default", f"must be text of at most {limit} characters")
            return None
        return Field(key, label, "text", default, max_length=limit)
    items = _int(report, f"{path}.max_items", value.get("max_items", MAX_LIST_ITEMS), 1, MAX_LIST_ITEMS)
    return None if items is None else Field(key, label, "list", (), max_items=items)


def _derived(report: _Report, path: str, raw: object, fields: tuple[Field, ...],
             broken: frozenset[str] = frozenset()) -> tuple[tuple[str, Expr], ...]:
    """``broken``: keys of this sheet's fields that have problems of their own; formulas may use them
    without a second, misleading 'unknown name'."""
    value = _mapping(report, path, raw)
    if value is None:
        return ()
    if len(value) > MAX_DERIVED:
        report.add(path, f"at most {MAX_DERIVED} derived values")
        return ()
    taken = {f.key for f in fields}
    text_fields = {f.key: f"{f.key!r} is a {f.type} field and holds no number" for f in fields if f.type not in NUMERIC}
    parsed: dict[str, Expr] = {}
    for key, formula in value.items():
        if not _ident(report, f"{path}.{key}", key, "a derived name"):
            continue
        assert isinstance(key, str)
        if key in RESERVED:
            report.add(f"{path}.{key}", f"{key!r} is a name checks supply themselves; choose another")
            continue  # not added to broken: in a check, the name means what the check supplies
        if key in taken:
            report.add(f"{path}.{key}", "has the same name as a field")
            continue
        try:
            parsed[key] = parse(formula)
        except ExprError as error:
            report.add(f"{path}.{key}", str(error))
            broken = broken | {key}
    # Order derived values so each uses only fields and those before it; a cycle is a problem.
    numeric = {f.key for f in fields if f.type in NUMERIC} | (broken - taken)
    ordered: list[tuple[str, Expr]] = []
    remaining = dict(parsed)
    while remaining:
        ready = [k for k, e in remaining.items() if all(n in numeric or n in dict(ordered) or n not in remaining
                                                         for n in e.names())]
        if not ready:
            report.add(path, f"derived values depend on each other in a loop: {', '.join(sorted(remaining))}")
            return ()
        for key in sorted(ready):
            ordered.append((key, remaining.pop(key)))
    good: list[tuple[str, Expr]] = []
    scope: dict[str, Kind] = {k: "number" for k in numeric}
    for key, expr in ordered:
        checked = _expr(report, f"{path}.{key}", expr.text, scope, "number", text_fields)
        if checked is not None:
            good.append((key, checked))
        scope[key] = "number"  # a broken one has its own problem; what uses it is not told it is unknown
    return tuple(good)


def _sheet(report: _Report, path: str, sheet_id: str, raw: object) -> SheetType | None:
    value = _mapping(report, path, raw)
    if value is None or not _keys(report, path, value, ("label", "fields"), ("derived",)):
        return None
    label = _text(report, f"{path}.label", value["label"])
    raw_fields = value["fields"]
    if not isinstance(raw_fields, (list, tuple)) or not 1 <= len(raw_fields) <= MAX_FIELDS:
        report.add(f"{path}.fields", f"must be a list of 1 to {MAX_FIELDS} fields")
        return None
    fields: list[Field] = []
    broken: set[str] = set()
    before = len(report.problems)
    for index, raw_field in enumerate(raw_fields):
        made = _field(report, f"{path}.fields[{index}]", raw_field)
        if made is None:
            if isinstance(raw_field, Mapping) and type(raw_field.get("key")) is str:
                broken.add(raw_field["key"])  # type: ignore[arg-type]
            continue
        if any(f.key == made.key for f in fields):
            report.add(f"{path}.fields[{index}].key", f"{made.key!r} is already a field of this sheet")
            continue
        fields.append(made)
    derived = _derived(report, f"{path}.derived", value.get("derived", {}), tuple(fields), frozenset(broken))
    if label is None or len(report.problems) != before:
        return None
    # A sheet type must at least fill with its own defaults: a derived value that is a fraction or divides by
    # zero there is a problem now, not a surprise on the first sheet.
    scope: dict[str, Value] = {f.key: Fraction(f.default) for f in fields if f.type in NUMERIC}  # type: ignore[arg-type]
    for key, expr in derived:
        try:
            scope[key] = Fraction(integer(evaluate(expr, scope), f"with every field at its default, {key!r}"))
        except ExprError as error:
            report.add(f"{path}.derived.{key}", str(error))
            return None
    return SheetType(sheet_id, label, tuple(fields), derived)


# -- checks ----------------------------------------------------------------------------------------------------
def _check(report: _Report, path: str, check_id: str, raw: object, sheets: Mapping[str, SheetType],
           family: str) -> Check | None:
    value = _mapping(report, path, raw)
    if value is None or not _keys(report, path, value, ("label", "sheet", "dice", "outcomes"),
                                  ("total", "difficulty")):
        return None
    before = len(report.problems)
    label = _text(report, f"{path}.label", value["label"])
    sheet_id = value["sheet"]
    sheet = sheets.get(sheet_id) if type(sheet_id) is str else None
    if sheet is None:
        known = sorted(sheets)
        report.add(f"{path}.sheet", "names no sheet type of this module" + (_suggest(sheet_id, known)
                   if type(sheet_id) is str else "") + (f"; the sheet types are {', '.join(known)}" if known else ""))
        return None
    difficulty = None
    if "difficulty" in value:
        difficulty = _int(report, f"{path}.difficulty", value["difficulty"], -MAX_NUMBER, MAX_NUMBER)
    sheet_scope: dict[str, Kind] = {**sheet.scope(), **{name: "number" for name in CHECK_INPUTS}}
    hint = {f.key: f"{f.key!r} is a {f.type} field and holds no number" for f in sheet.fields if f.type not in NUMERIC}
    dice = _mapping(report, f"{path}.dice", value["dice"])
    if dice is None or not _keys(report, f"{path}.dice", dice, ("count", "sides"), ("target",)):
        return None
    count = _expr(report, f"{path}.dice.count", dice["count"], sheet_scope, "number", hint)
    sides = _int(report, f"{path}.dice.sides", dice["sides"], 2, MAX_SIDES)
    target = None
    if "target" in dice:
        target = _expr(report, f"{path}.dice.target", dice["target"], sheet_scope, "number", hint)
    dice_ok = count is not None and sides is not None and ("target" not in dice or target is not None)
    # The dice's shape (one die? a target?) is read from the text even when a formula has a problem, so the
    # total and outcomes are still checked and reported.
    single = type(dice["count"]) is str and dice["count"].strip() == "1"
    has_target = "target" in dice
    rolled: dict[str, Kind] = {**sheet_scope, **{name: "number" for name in DICE_NAMES}}
    why = {"natural": "'natural' is the one die of a single-die check; this check's dice.count is not 1",
           "successes": "'successes' needs dice.target, the face a die must reach to count",
           "margin": "'margin' (total - difficulty) is known only once the total is; use it in outcomes",
           "total": "'total' is known only once it is computed; use it in outcomes", **hint}
    if single:
        rolled["natural"] = "number"
    if has_target:
        rolled["successes"] = "number"
    if family == "d20" and (not single or (sides is not None and sides != 20) or has_target):
        report.add(f"{path}.dice", "a d20 module's check rolls exactly one d20 (count 1, sides 20) and has no target")
    if family == "pool" and (not has_target or "total" in value):
        report.add(f"{path}.dice", "a pool module's check counts successes: it needs dice.target and no total "
                   "(its total is the successes)")
    total = None
    if "total" in value:
        total = _expr(report, f"{path}.total", value["total"], rolled, "number", why)
    graded: dict[str, Kind] = {**rolled, "total": "number", "margin": "number"}
    raw_outcomes = value["outcomes"]
    outcomes: list[Outcome] = []
    if not isinstance(raw_outcomes, (list, tuple)) or not 1 <= len(raw_outcomes) <= MAX_OUTCOMES:
        report.add(f"{path}.outcomes", f"must be a list of 1 to {MAX_OUTCOMES} outcomes, checked in order")
    else:
        for index, raw_outcome in enumerate(raw_outcomes):
            at = f"{path}.outcomes[{index}]"
            outcome = _mapping(report, at, raw_outcome)
            if outcome is None or not _keys(report, at, outcome, ("label", "when"), ()):
                continue
            outcome_label = _text(report, f"{at}.label", outcome["label"])
            when = _expr(report, f"{at}.when", outcome["when"], graded, "bool", why)
            if outcome_label is not None and when is not None:
                if any(o.label == outcome_label for o in outcomes):
                    report.add(f"{at}.label", f"{outcome_label!r} is already an outcome of this check")
                else:
                    outcomes.append(Outcome(outcome_label, when))
    if len(report.problems) != before or label is None or not dice_ok:
        return None
    assert count is not None and sides is not None
    return Check(check_id, label, sheet.id, Dice(count, sides, target), total, difficulty, tuple(outcomes))


# -- the document ----------------------------------------------------------------------------------------------
def _canonical(value: object) -> str:
    """An injective, platform-independent rendering of decoded JSON, for the revision digest."""
    if value is None or type(value) is bool:
        return {None: "n", True: "t", False: "f"}[value]  # type: ignore[index]
    if type(value) is int:
        return f"i{value};"
    if type(value) is float:
        return f"x{value!r};"
    if type(value) is str:
        return f"s{len(value)}:{value}"
    if isinstance(value, Mapping):
        items = sorted(value.items())
        return "{" + "".join(_canonical(k) + _canonical(v) for k, v in items) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + "".join(_canonical(v) for v in value) + "]"
    raise TypeError(f"not JSON: {type(value).__name__}")


def revision_of(document: Mapping[str, object]) -> str:
    """The content revision of a module document: ``sha256:`` and 64 hex digits."""
    return "sha256:" + hashlib.sha256(_canonical(document).encode("utf-8")).hexdigest()


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _build(document: object) -> tuple[MechanicsModule | None, tuple[Problem, ...]]:
    report = _Report()
    value = _mapping(report, "$", document)
    if value is None:
        return None, tuple(report.problems)
    if not _keys(report, "$", value, ("format", "id", "name", "version", "sheets", "checks"), ("family", "notes")):
        return None, tuple(report.problems)
    if value["format"] != FORMAT:
        report.add("format", f"must be {FORMAT!r}")
    _ident(report, "id", value["id"], "a module id")
    _text(report, "name", value["name"])
    _text(report, "version", value["version"], 32)
    if "notes" in value:
        _text(report, "notes", value["notes"], MAX_TEXT_FIELD)
    family = value.get("family", "generic")
    if family not in FAMILIES:
        report.add("family", f"must be one of {', '.join(FAMILIES)}")
        family = "generic"
    sheets: dict[str, SheetType] = {}
    attempted: set[str] = set()
    raw_sheets = _mapping(report, "sheets", value["sheets"])
    if raw_sheets is not None:
        if not 1 <= len(raw_sheets) <= MAX_SHEETS:
            report.add("sheets", f"a module has 1 to {MAX_SHEETS} sheet types")
        for sheet_id, raw in raw_sheets.items():
            if _ident(report, f"sheets.{sheet_id}", sheet_id, "a sheet type id"):
                attempted.add(sheet_id)  # type: ignore[arg-type]
                made = _sheet(report, f"sheets.{sheet_id}", sheet_id, raw)  # type: ignore[arg-type]
                if made is not None:
                    sheets[made.id] = made
    checks: dict[str, Check] = {}
    raw_checks = _mapping(report, "checks", value["checks"])
    if raw_checks is not None:
        if not 1 <= len(raw_checks) <= MAX_CHECKS:
            report.add("checks", f"a module has 1 to {MAX_CHECKS} checks")
        for check_id, raw in raw_checks.items():
            if not _ident(report, f"checks.{check_id}", check_id, "a check id"):
                continue
            named = raw.get("sheet") if isinstance(raw, Mapping) else None
            if type(named) is str and named in attempted and named not in sheets:
                continue  # its sheet type has problems of its own; the check is checked once that sheet is valid
            made_check = _check(report, f"checks.{check_id}", check_id, raw, sheets, family)  # type: ignore[arg-type]
            if made_check is not None:
                checks[made_check.id] = made_check
    if report.problems:
        return None, tuple(report.problems)
    try:
        revision = revision_of(value)
    except (TypeError, ValueError):
        return None, (Problem("$", "holds a value that is not plain JSON text (such as a lone surrogate)"),)
    return MechanicsModule(value["id"], value["name"], value["version"], family,  # type: ignore[arg-type]
                           MappingProxyType(sheets), MappingProxyType(checks), revision,
                           _freeze(value)), ()  # type: ignore[arg-type]


def validate_module(document: object) -> tuple[Problem, ...]:
    """Every problem with ``document``; empty when it is a valid module."""
    return _build(document)[1]


def parse_module(document: object) -> MechanicsModule:
    """The validated module ``document`` describes; ``ModuleInvalid`` lists every problem otherwise."""
    module, problems = _build(document)
    if module is None:
        raise ModuleInvalid(problems)
    return module


__all__ = ["CHECK_INPUTS", "DICE_NAMES", "FAMILIES", "FIELD_TYPES", "FORMAT", "Check", "Dice", "Field",
           "MechanicsModule", "ModuleInvalid", "Outcome", "Problem", "SheetType", "parse_module", "revision_of",
           "validate_module"]
