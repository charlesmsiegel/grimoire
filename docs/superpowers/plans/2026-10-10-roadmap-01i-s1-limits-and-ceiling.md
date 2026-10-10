# 01i-S1: The resolved fact and the ceiling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every target the resolver builds carries `limits: wire.Limits` (the
model's window and max output, each with its source), `Attempt.limits`
returns it, and `store/inference/limits.py` turns a resolution into the token
count a prompt may hold (`prompt_ceiling`), with the reply reserved.

**Architecture:** `catalog.entry` gains `max_output`. `wire.py` gains
`LIMIT_SOURCES`, `Limit`, `Limits` and `Target.limits`. A new pure module
`store/inference/limits.py` holds `of(row, model_facts)`, `reply_reserve`,
`prompt_ceiling`, `Ceiling` and `DEFAULT_REPLY_RESERVE`. `resolve._typed` (the
one builder `_attempt` and `target_for` share) calls `limits.of` with the row
and facts it already holds and hands the result to `_target`'s new `limits`
parameter. `facts._view` reads the two stated keys so the resolver sees them
before S2 can write them. Nothing reads the new fields on the turn path.

**Tech Stack:** Python 3.11 stdlib dataclasses / NamedTuple, pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01i-context-window-fact-design.md` §3, §4, §5, §7 (C1, C2), §9 (backend: `test_catalog.py` → `test_model_catalog.py`, `test_inference_limits.py`, `test_inference_resolve*.py`, byte-identity).

## Global Constraints

- `wire.py` stays standard-library only (`test_wire.py`'s AST check).
- `limits.py` imports `wire` (`from ... import wire`) and `resolved` as a submodule (`from . import resolved`), nothing else of the package; `resolve` binds it as `from . import limits` (CLAUDE.md import-binding rule). Acyclic: `resolved` never imports `limits`.
- `limits.of` never raises and never returns `0`; a value is a positive int or `None` exactly when its source is `"unknown"`.
- No new store read on the turn path: `of` is fed the row and facts `_attempt` already read.
- No prompt, request body or surface changes. `llm_sampling` keeps reading `features.max_tokens`.
- pydantic untouched; no store write.

## Review Focus

- `reply_reserve` never reserves `max_output` itself; it only caps at it.
- `prompt_ceiling` walks `attempts[0]` and `attempts[1]` only when `rides` — a non-riding fallback, or a decide resolution's separate stage, never lowers it.
- `tokens is None` only when no walked attempt's window is known; `0` only for a known window with no room, always with a `reason`.
- `binding` is a `(provider_id, model)` tuple, never a joined string.
- A target built by hand (`resolved.UNBUILT`, `wire_kit`) reads unknown.
- Equality of resolved targets with hand-built ones in existing tests (a catalog fixture row with `context` now gives a non-default `limits`).

---

### Task 1: `catalog.entry` gains `max_output`

**Files:** Modify `backend/src/grimoire/catalog.py`; Test `backend/tests/test_model_catalog.py`.

**Interfaces:** `catalog.entry(raw)["max_output"]`: positive int, key absent otherwise. `catalog._whole(value) -> int | None` (`_context`'s rule, factored; `_context` uses it).

- [ ] Failing tests: `test_max_output_is_read_from_openrouters_top_provider`, `test_max_output_is_read_from_an_anthropic_rows_max_tokens` (and `features.max_tokens` unchanged), `test_a_whole_float_max_output_is_still_a_cap`, `test_a_max_output_that_is_not_a_positive_integer_is_absent` (param: `0, -1, True, 1.5, "8192", None`), `test_a_vllm_or_ollama_row_has_no_max_output`.
- [ ] Implement: `_max_output(raw)` reads `top_provider.max_completion_tokens` (when `top_provider` is a dict) through `_whole`; `_anthropic` sets `out["max_output"]` from `raw["max_tokens"]` through `_whole` (absent when it says nothing); `features.max_tokens` keeps `_positive_int`.
- [ ] Run `tests/test_model_catalog.py tests/test_anthropic.py` → PASS.

### Task 2: `wire.Limit`, `wire.Limits`, `Target.limits`, `Attempt.limits`

**Files:** Modify `backend/src/grimoire/wire.py`, `backend/src/grimoire/store/inference/resolved.py`; Test `backend/tests/test_wire.py`.

**Interfaces:** `wire.LIMIT_SOURCES = ("user", "catalog", "unknown")`; `class Limit(NamedTuple): value: int | None; source: str`; `wire.UNKNOWN_LIMIT = Limit(None, "unknown")`; `@dataclass(frozen=True) class Limits: window: Limit = UNKNOWN_LIMIT; max_output: Limit = UNKNOWN_LIMIT`; `Target.limits: Limits = field(default_factory=Limits)`; `Attempt.limits` property → `self.target.limits`.

- [ ] Failing tests: `test_a_target_built_by_hand_has_unknown_limits` (wire_kit target, `resolved.UNBUILT`, a hand `Attempt`), `test_limits_are_frozen`.
- [ ] Implement; run `tests/test_wire.py` → PASS.

### Task 3: `store/inference/limits.py`

**Files:** Create `backend/src/grimoire/store/inference/limits.py`; add it to `store/inference/__init__.py`; Test: new `backend/tests/test_inference_limits.py`.

**Interfaces:**
- `of(row: Mapping | None, model_facts: Mapping | None) -> wire.Limits` — window: facts `context_window` (`user`), else `row["context"]` (`catalog`), else unknown; max output: facts `max_output`, else `row["max_output"]`, else unknown. A value counts only if a positive int (bool excluded) below `2**31` (facts) / a positive whole number (row, `_context`'s rule).
- `DEFAULT_REPLY_RESERVE = 4096`.
- `reply_reserve(attempt: resolved.Attempt, *, max_tokens: int | None = None) -> int` — per-call `max_tokens` (positive int), else the preset's sent `max_tokens` read from `controls["effective"][controls["controls"]["max_tokens"]["wire"]]`, else `min(DEFAULT_REPLY_RESERVE, window // 4)` (the default alone when the window is unknown); then capped at a known `max_output`.
- `@dataclass(frozen=True) class Ceiling: tokens: int | None; window: int | None; reserve: int; complete: bool; binding: tuple[str, str] | None; reason: str`.
- `prompt_ceiling(resolved: resolved.ResolvedInference, *, reserve: int | None = None, max_tokens: int | None = None) -> Ceiling`.

- [ ] Failing tests (`test_inference_limits.py`):
  - `of`: `test_the_users_word_outranks_the_catalog_for_each_value_alone`, `test_the_catalog_is_read_when_nothing_is_stated`, `test_nothing_known_is_unknown_never_zero`, `test_a_malformed_facts_or_row_value_contributes_nothing` (param over `0, -1, True, 1.5, "8192", 2**31, None`), `test_of_never_raises_on_a_non_mapping`.
  - `reply_reserve`: `test_a_per_call_cap_outranks_the_presets`, `test_a_sent_max_tokens_is_the_reserve`, `test_the_openai_api_reserve_is_its_max_completion_tokens`, `test_the_anthropic_api_reserve_is_its_default_max_tokens`, `test_an_unset_max_tokens_reserves_the_default_or_a_quarter_window`, `test_every_reserve_is_capped_at_a_known_max_output`, `test_max_output_is_never_itself_the_reserve` (a 128k cap in a 131k window leaves a usable ceiling).
  - `prompt_ceiling`: `test_the_smaller_window_binds_when_a_fallback_rides`, `test_a_fallback_that_does_not_ride_is_ignored`, `test_a_decide_resolutions_separate_stage_is_ignored` (resolver-built native primary with a structured fallback, `rides` False), `test_one_unknown_window_is_incomplete`, `test_no_known_window_is_none_never_zero`, `test_a_preset_larger_than_the_window_floors_at_zero_with_a_reason`, `test_a_per_call_cap_is_reserved_not_the_default` (8192, no preset), `test_an_explicit_reserve_is_honoured`, `test_binding_is_a_tuple_for_a_model_id_with_a_slash`, `test_nothing_resolved_is_none_and_incomplete`.
- [ ] Implement `limits.py` (pure, reads nothing). `reason` (only when `tokens == 0`) names the reserve's origin: "The preset asks for 32,000 reply tokens; vendor/m's window is 8,192." / "The call asks for …" / "A reply reserve of …".
- [ ] Run → PASS.

### Task 4: the resolver builds the limits; facts read the stated keys

**Files:** Modify `backend/src/grimoire/store/inference/resolve.py` (`_typed`, `_target`), `backend/src/grimoire/store/inference/facts.py` (`_view`, module docstring); Test `backend/tests/test_inference_resolve.py`, `backend/tests/test_inference_facts.py` (+ the three whole-view equality cases in it and `test_inference_capabilities.py`).

**Interfaces:** `_target(..., model_facts, limits: wire.Limits)`; `_typed` computes `limits.of(row, model_facts)` once and passes it. `facts._view` adds `"context_window"` and `"max_output"`: `int | None` (positive int, bool excluded, below `2**31`), via `facts.stated_limit(value) -> int | None` (the one rule, which S2's write also checks with).

- [ ] Failing tests (`test_inference_resolve.py`): `test_an_attempts_limits_are_of_its_row_and_facts` (catalog row with `context`/`max_output`, facts stated in the file → `attempt.limits == limits.of(row, attempt.facts)` and the user source wins for the stated one), `test_a_reroll_override_resolves_its_own_limits`, `test_an_embedding_attempt_judged_without_the_catalog_reads_only_stated_limits` (`embed_attempt(..., catalog=False)`), `test_a_catalog_cached_under_an_old_rev_gives_unknown_limits`, `test_target_for_carries_the_same_limits`. (`test_inference_facts.py`): `test_the_stated_limits_are_read_and_a_malformed_one_is_none`.
- [ ] Implement; update the three whole-view equality literals to include the two `None` keys.
- [ ] Run `tests/test_inference_resolve.py tests/test_inference_facts.py tests/test_inference_capabilities.py tests/test_inference_equivalence.py tests/test_adapter_registry.py` → PASS.

### Task 5: verify nothing else moved

- [ ] `tests/test_lore_golden.py`, the frozen-campaign tests (`tests/test_frozen_campaign*.py`), `test_import_guard.py`, `test_wire.py`, `test_paths_guard.py`, `test_atomic_guard.py`, `test_lowering_retired_guard.py`, `test_routing_guard.py`, `test_operation_guard.py`, `test_pydantic_guard.py` → PASS; full `pytest -n 4` → PASS except the known root-only `test_atomic.py::test_a_read_only_record_is_not_silently_replaced`.
- [ ] `scripts/ratchet.py ruff` and `mypy` at baseline.
- [ ] Commit `01i-S1: the context window as a resolved fact, and the prompt ceiling`.

## Plan gate (substitute review, 2026-10-10)

The Codex CLI is not installed and no subagent tool was available to this
session, so a rigorous adversarial self-review of the plan against the spec
and the code at `bd24132` stood in for `/codex:adversarial-review`.

- **Re-read since the baseline.** `resolve._attempt`/`_target`/`_typed`,
  `resolved.Attempt`, `catalog`, `facts._view` and `llm_sampling.effective`
  are as the spec cites them (line numbers moved by a few). 01s has not
  landed (S4's concern).
- **Folded: where `limits.of` is called.** The spec puts the call in
  `_attempt` and a parameter on `_target`; `target_for` would then build its
  own. `_typed` is the one builder both already share, so the call goes
  there, and `_target` takes the result as a parameter. Same contract, one
  call site.
- **Folded: `_view` in S1.** The S1 scope says the facts reader treats the
  stated fields as optional, which needs `_view` to carry them (the resolver
  reads facts only through it). A side effect is that `GET .../facts`
  (`**known`) already shows the two stated keys as `null` in S1; that is
  inert and S2 documents it. The three whole-view equality literals in the
  tests gain the two keys.
- **Folded: one validation rule.** `facts.stated_limit` is the facts-side
  rule (int, not bool, `0 < v < 2**31`) that S2's write reuses; `limits.of`
  restates it for a hand-built facts dict, and reads catalog values with
  `_context`'s rule (a whole float counts) without importing `catalog`, so
  `limits` imports only `wire` and `resolved`.
- **Checked, no change: decide resolutions.** A native attempt's controls
  are `not_applicable` (`effective == {}`), so its reserve is the default;
  its separate stage never rides, so `prompt_ceiling` never walks it.
- **Checked, no change: `UsableInference`.** It is a `ResolvedInference`
  subclass rebuilt field-by-field (`_narrowed`), so the targets, and their
  limits, carry over.
- **Risk noted:** a test comparing a resolved target with a hand-built one
  over a catalog row that states `context` will now differ; Task 4 runs the
  suites that do that.

## Review and final gate (substitute review, 2026-10-10)

An adversarial self-review of the slice diff against spec sections 3, 4, 5,
7 and 9 stood in for `/codex:review` and the final spec gate (no Codex CLI,
no subagent tool). Every section 9 backend case for S1 maps to a test:
`test_model_catalog.py` (five new cases), `test_inference_limits.py` (37),
`test_inference_resolve.py` (seven `01i` cases, including a resolver-built
native decide primary whose structured fallback is a separate stage), and
`test_inference_facts.py` (the stated keys read, a malformed one `None`).
The byte-identity check is the unchanged full suite: `test_lore_golden.py`
and the frozen-campaign sweep pass with `snapshot.json` untouched.

- **Folded:** `binding` names the model the attempt SENDS
  (`target.model`), falling back to `attempt.model` only for a hand-built
  attempt, so an unset Claude model binds as the model its facts are kept
  under rather than `""`.
- **Folded:** an explicit `reserve` below zero is floored at 0 rather than
  growing the ceiling past the window.
- **Kept, noted:** `limits.of` refuses a catalog value of `2**31` or more as
  well as a stated one (the spec bounds only the stated value). No real
  window is that large, and one rule for both keeps a hand-edited sidecar
  from producing a ceiling no wire could carry.
- **Kept, noted:** `_target`'s new parameter is named `sizes`, so it does
  not shadow the `limits` module it is computed with.
- **Drift, deliberate:** `GET .../facts` already shows `context_window` and
  `max_output` (as `null`) in this slice, because `_view` reads them; S2
  makes them writable and adds the resolved `limits`.

Results: full backend suite 16351 passed, 5 skipped (the known root-only
`test_atomic.py` case deselected); ruff and mypy ratchets all at baseline.
