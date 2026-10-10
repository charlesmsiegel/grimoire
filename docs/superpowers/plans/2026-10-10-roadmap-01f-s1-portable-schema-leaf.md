# 01f-S1: The portable-schema leaf — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One rule for what a JSON Schema may contain if it is to be sent to
both OpenAI strict mode and Anthropic's `output_config.format`
(`schemas.check`), one prompt spelling of a schema (`schemas.render`, also the
Jinja filter `schema_json`), and one tolerant reader of a reply's JSON value
(`schemas.find_value`). Delivers 01f-C3 in full. No call site changes
behaviour.

**Architecture:** a new gateway leaf `backend/src/grimoire/schemas.py`,
standard library only (held by an AST test, as `test_wire.py` holds
`wire.py`). The strict-mode budgets move there from `decisions.py`, which
binds the module (`from . import schemas`) and re-exports them, so there is
one copy. `decisions._tally` moves too (as `schemas.tally`), and the
parse helpers `_FENCE`, `_first_wins`, `_loads` move with the reader.
`prompts._env` registers `schemas.render` as `schema_json`.
`decide/system.j2` is untouched (it keeps `tojson`).

**Tech Stack:** Python 3.11 stdlib (`json`, `re`), Jinja2 filter
registration, pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01f-structured-generation-design.md` §3.1, §4 (C3), §7 (`test_schemas.py`), Slices (01f-S1).

## Global Constraints

- `schemas.py` imports only the standard library (and `__future__`).
- `decisions.py` binds `from . import schemas` and reads names off it; it
  never does `from .schemas import X` (CLAUDE.md import rule).
- `decisions.find_object` keeps its exact contract and behaviour.
  **Drift from the spec, on purpose:** the spec says it "calls `find_value`
  and maps a list to None". That would change one case `parse` reads today
  (`test_parse_reads_a_list_holding_one_object`: `[{...}]` reads the inner
  object through the brace span, because the whole text decodes to a list
  and is skipped). So `find_value` takes a keyword `arrays: bool = True`,
  and `find_object` is `find_value(text, arrays=False)`: the same scanner,
  restricted to objects, which is today's candidate order byte for byte.
- No template, prompt or wire byte moves; `test_decide_chain_golden.py`
  passes unchanged.
- `MAX_DEPTH = 10`: OpenAI's "Supported schemas" page, "A schema may have up
  to 5000 object properties total, with up to 10 levels of nesting" (the same
  sentence `decisions.py` cites for `MAX_SCHEMA_PROPERTIES`). Depth counts
  the schema nodes on a path from the root through `properties` values and
  `items`; an `anyOf` branch is an alternative at its parent's depth. A
  decision batch is 4 (batch, item, answers, a question's value).

## Review Focus

- The subset is closed: a key not listed is refused, whatever it is.
- `required` must name every property exactly (as a set, no duplicates),
  `additionalProperties` must be the literal `False`.
- An empty `enum` is refused; enum values are strings (on `type: string`) or
  integers (on `type: integer`, `bool` excluded), never mixed.
- The root must be `type: object` (strict mode's own rule: a root is an
  object and never `anyOf`).
- `render` is `json.dumps(indent=2, sort_keys=True, ensure_ascii=False)`, and
  the filter is that function object, not a copy.
- `find_value` never raises (deep nesting's `RecursionError` included).
- Every `decisions.schema(...)` the decide tests can build passes `check`.

---

### Task 1: `schemas.py` and its tests

**Files:**
- Create: `backend/src/grimoire/schemas.py`
- Create: `backend/tests/test_schemas.py`

**Interfaces:**
- Produces: `SchemaError(ValueError)`; `check(schema: dict) -> None`;
  `render(schema: dict) -> str`; `find_value(text: str, *, arrays: bool = True) -> dict | list | None`;
  `tally(node: object) -> tuple[int, int]` (properties, characters, moved
  verbatim from `decisions._tally`); constants `MAX_ENUM_VALUES`,
  `MAX_ENUM_STRING_CHARS`, `ENUM_STRING_CHARS_ABOVE`,
  `MAX_SCHEMA_STRING_CHARS`, `MAX_SCHEMA_PROPERTIES`, `MAX_DEPTH`, `TYPES`.

- [ ] **Step 1: Failing tests** (`test_schemas.py`)
  - `check` accepts a representative schema using every allowed keyword
    (object, array, string enum, integer enum, number, boolean, null,
    `anyOf` nullable, `description`).
  - Refused, each by its own case: every bound keyword (`minimum`,
    `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `multipleOf`,
    `minItems`, `maxItems`, `minLength`, `maxLength`, `pattern`, `format`),
    `$ref`, `$defs`, `oneOf`, `allOf`, `not`, `const`, an unknown key; a type
    array; an unknown type; an object missing `additionalProperties: false`
    (absent and `True`); a `required` that omits a property, names one that
    is not there, or repeats one; an array without `items`; `items` on a
    non-array; an empty `enum`; mixed/boolean enum values; a non-object
    root; a node with neither `type` nor `anyOf`; depth past `MAX_DEPTH`
    (and exactly `MAX_DEPTH` accepted); each budget past its constant.
  - Every `decisions.schema(...)` over predicates, choices (with and without
    `allow_none`) and scores, `explain` both ways, passes.
  - `render`: sorted keys, indent 2, `'`, `<`, `&` and non-ASCII unescaped.
  - The `schema_json` filter (`prompts._env().filters["schema_json"]`) is
    `schemas.render`, and a template string rendered through it equals
    `render`'s text for keys out of order and a description holding `'`,
    `<`, `&`.
  - `decide/system.j2` rendered through `prompts.render` equals the same
    template rendered by a fresh Jinja environment with no extra filter, and
    its source still spells `tojson(indent=2)`.
  - `find_value`: an object, an array, a fenced value, prose around an
    object, prose around an array (the earlier bracket wins), `[{...}]` is
    the list, nothing/`null`/`"x"`/truncated is None, a repeated key keeps
    its first value, a 100k-deep nest returns None rather than raising;
    `arrays=False` reproduces `decisions.find_object`'s cases.
  - AST: the module imports only `sys.stdlib_module_names` (no relative
    import).
- [ ] **Step 2: Run** `cd backend && PYTHONPATH=src $PY -m pytest tests/test_schemas.py -q` → fails (no module).
- [ ] **Step 3: Implement** `schemas.py` (a recursive `_walk(node, path, depth, budget)` collecting enum counts; `tally` verbatim from decisions; the reader with `_FENCE`, `_first_wins`, `_loads` moved verbatim).

### Task 2: `decisions` and `prompts` bind the leaf

**Files:**
- Modify: `backend/src/grimoire/decisions.py` (constants re-exported from
  `schemas`; `_tally` → `schemas.tally`; `find_object` →
  `schemas.find_value(text, arrays=False)`; the parse helpers removed;
  docstring: "imports nothing from the package but the stdlib leaf `schemas`")
- Modify: `backend/src/grimoire/prompts.py` (`_env` registers
  `schema_json`)

- [ ] **Step 1:** Run `tests/test_decisions.py tests/test_decide_chain_golden.py tests/test_inference_decide*.py tests/test_schemas.py tests/test_import_guard.py tests/test_evals.py tests/test_eval_graders.py` → PASS.
- [ ] **Step 2:** `$PY scripts/verify_templates.py`, `$PY scripts/ratchet.py ruff`, `$PY scripts/ratchet.py mypy` → green / at baseline.
- [ ] **Step 3: Commit** `01f-S1: the portable-schema leaf`.

## Plan gate (substitute review, 2026-10-10)

The Codex CLI is not installed and this session has no subagent tool, so a
rigorous adversarial self-review of the plan against the spec and the code
stood in for `/codex:adversarial-review`. Findings, each folded:

- **B1 (blocking): "find_object calls find_value and maps a list to None"
  would change `parse`.** Confirmed against `decisions.find_object`'s
  candidate order and `test_parse_reads_a_list_holding_one_object`. Folded as
  the `arrays=False` keyword (Global Constraints), recorded as drift.
- **S1: a node carrying both `type` and `anyOf`.** The spec lists both as
  allowed keys and says nothing of the pair; the two providers do not
  document the same reading of it. Folded: `check` refuses a node with both,
  as it refuses one with neither.
- **S2: "never raises" on a non-string.** `find_value(None)` would raise
  `AttributeError` on `.strip()`. Folded: a non-`str` input is None.
- **M1: the per-enum string budget.** The spec names three budgets; the
  per-enum one (`MAX_ENUM_STRING_CHARS` above `ENUM_STRING_CHARS_ABOVE`
  values) is the same page's rule and `decisions._check_choice` enforces it.
  Folded: it moves too, and `check` enforces it per enum.
- **M2: the stdlib AST test must also refuse a relative import** (`from .
  import x` has `module=None` and would read as `""`). Folded, as
  `test_wire.py` does it.

## Review and final gate (substitute review, 2026-10-10)

An adversarial self-review of the slice diff against spec §3.1, C3 and the
S1 acceptance list stood in for `/codex:review` and the final spec gate (no
Codex CLI, no subagent tool). Every acceptance item maps to a test in
`test_schemas.py` (subset refusals, the filter equal to `render`, the
`decide/system.j2` bytes, `find_value`, the AST leaf check, every decision
schema passing). Folded:

- **An unhashable `type`** (`{"type": {...}}`) raised `TypeError` from the
  frozenset membership test instead of `SchemaError`. Now refused as an
  unknown type, with a case for a dict, None and an int.
- **`test_decisions_is_a_leaf`** asserted no sibling import at all; it now
  asserts exactly `{"schemas"}`, and that `schemas` imports nothing from the
  package, so the leaf property is still held where it was.
- **Ratchets:** `_node`'s complexity (ruff C901) split into `_keys` and
  `_type`; `find_value`'s narrowing made explicit for mypy. Both at baseline
  with no baseline change.

Left as drift, on purpose: `find_object` is `find_value(arrays=False)`, not
"`find_value` with a list mapped to None" (plan gate B1); `schemas.depth` and
`schemas.tally` are public (the depth rule needs one statement a test can
ask, and `decisions` uses `tally`).
