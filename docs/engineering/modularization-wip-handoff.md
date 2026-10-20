# Modularization handoff: splitting oversized Python modules

**Branch:** `refactor/modularize-large-python`
**Base:** `refactor/lint-debt-cleanup`, branched from Grimoire main `9a33cafd2183add09ba3e83dc78ad9cb6b73d2b2`.
**Status:** The two route conversions pass the complete relevant local gate recipes. Further production conversions remain outstanding. No PR or merge is authorized.
**Scope:** behavior-preserving restructuring only. No new game features, no changing Grimoire's authoritative file model.

## Continuation outcome

The continuation fetched and fast-forwarded this branch to `e5f3a1d7b`, then
confirmed origin still matched before committing local work. The former blanket
imports are now minimal in the leaves, while both facades preserve the original
bindings. Campaign imports remain explicitly ordered around one shared router.
All 156 model definitions and 126 campaign definitions retain their original
ASTs and comments. Characterization covers the original schemas, exports and
106 ordered routes; original and split campaign OpenAPI match on Pydantic 1/2.

The owner approved moving the relationship-history test's constant patch to
`campaigns._ledger`, the handler's global owner, while retaining the readable
facade export. The overview guard now permits only the exact moved reader and
retains a forbidden-sibling control. Two stale production docstring paths and
two test portability assumptions found during validation were also corrected.

Ruff is back to the original 764 findings from 1,770 in the generated WIP;
mypy stays at 141 and ESLint at 307. Baselines only relocate one existing Ruff
finding and two existing mypy findings to `_ledger`. No lint rule was relaxed.

The full results, theory notes, environment limitations and concrete next-batch
boundaries are recorded in [modularization-validation.md](modularization-validation.md).
Make is unavailable locally, so its recipes were executed directly with their
configured dependencies and coverage settings. The complete backend run passed
22,809 tests with 46 skips and 93.87% coverage; the complete Pydantic-1 run
passed 22,803 with 52 skips. Both returned zero. Templates, all three lint
ratchets, frontend typechecking and 5,783 frontend tests also passed. Python
3.11 runtime and APK validation were not run locally; this is local evidence,
not a claim that every CI job ran.

## Package layout

- `backend/src/grimoire/routes/models.py` was converted to `routes/models/` with `__init__.py` and domain-grouped `_settings.py`, `_worlds.py`, `_mechanics.py`, `_actors.py`, `_scenes.py`, `_continuity.py`, `_extras.py`. The facade re-exports the old model class names. The split preserves the existing Pydantic 1 / Android-compatible conventions.
- `backend/src/grimoire/routes/campaigns.py` was converted to `routes/campaigns/` with a shared `_router.py`, a names helper, and ordered endpoint groups `_settings.py`, `_calendar.py`, `_core.py`, `_images.py`, `_management.py`, `_sync.py`, `_ledger.py`, `_timeline.py`, `_actors.py`, `_pcs.py`. `__init__.py` imports endpoint modules in the original declaration order; characterization pins the complete original route table and shared router identity.
- `backend/tests/test_llm_error_status.py`'s static route scan was updated to include package leaf files and recognize modules or packages.
- The branch inherits the preceding safe lint pass (23 fewer ESLint findings, baseline 330→307) from `refactor/lint-debt-cleanup`. No previous main-branch code is intentionally modified.

Temporary GitHub Actions workflows, source-snapshot tools and the one-shot code generator were used and **then removed**; the generated Python sources themselves are the committed artifact.

## Historical verification before stabilization

- `python -m compileall -q backend/src/grimoire/routes`
- `python -m pytest backend/tests/test_import_guard.py backend/tests/test_route_order.py backend/tests/test_llm_error_status.py backend/tests/test_pydantic_guard.py -q -x --tb=short`

These were the focused checks available at the original WIP commit. The
continuation's broader validation is recorded above and in the linked evidence.

## Original WIP cleanup checklist

The import, API, ordering and diagnostic issues below are resolved by the
continuation. Keep this checklist as context for why a generated split alone
was insufficient; use the current validation record for gate status.

The first attempt to require zero new Ruff findings before committing the generated split **failed**: 764 baseline findings became 1,772 in the uncleaned generated checkout. The differences were `F401` unused imports (~987), `I001` unsorted import blocks (19), `EXE001` (1), and `F841` (1). Some counts may change after deleting the one-shot script.

**Primary cause:** each new leaf module received the former monolithic file's entire import header, creating many unused imports. The `__init__.py` files deliberately re-export names, which the linter may also interpret as unused imports.

1. Replace blanket imports with **minimal actual imports per leaf module**; let Ruff reorder safely. **Do not run indiscriminate `ruff --fix` on `__init__.py` re-exports** without ensuring the former API remains exported. Prefer explicit `from ._module import Name as Name` for public re-exports or an accurate `__all__`.
2. Confirm that model annotations, FastAPI parameter dependencies, decorators, runtime symbols, and test monkeypatch paths resolve under the new physical module placement. The facade exposes names for imports, but patching `routes.campaigns.some_name` does not always change a submodule's globals. Tests must catch this.
3. Check the module-order/route-order behavior. The single `APIRouter` in `_router.py` is registered by ordered eager imports. Keep the route matching behavior identical and extend tests to cover any significant overlapping paths.
4. Reconcile the Ruff and mypy **per-(file,rule) ratchet baselines** to the moved paths **only after** ensuring counts by rule have not increased and no genuine new findings were introduced. The mechanical path moves otherwise look like a new finding in a new file. Do not raise baseline counts to silence actual regressions; any necessary `--accept-regressions` is only for verified path-move rebasing with evidence.
5. Run the complete gates, with exact pinned toolchains: `make check-lint PY=python`, `make check-mypy PY=python`, `make check-templates PY=python`, `make check-py PY=python`, `make check-pydantic1 PY=python`, and frontend checks where relevant. Also run `git diff --check`, check imports and affected API/test expectations.
6. Only then commit a complete, behavior-preserving refactor. Do not merge or open a PR merely because the module split exists.

## Later candidates

Other oversized production Python files include `routes/scenes.py` (~6.3k lines), `store/overlay.py`, `inference.py`, `routes/character_turns.py`, `store/compiled.py`, `routes/runs.py`, and `store/context/assemble.py`. **Do not mechanically slice these by line number.** Reconstruct cohesive ownership, invariants, state, cycles, and patching conventions first (Naur's Theory Building). Preserve state safety and important API contracts. Big test and TS files are separate tasks, not a reason to turn a Python package refactor into a monolithic rewrite.

The continuation inspected these candidates and recorded their responsibilities,
shared-state risks and guard/patch dependencies in the validation note. Further
production conversions remain a separate batch with characterization and the
required reviews; none is bundled into the two-package stabilization.

## Broader roadmap boundary

Grimoire is a local-first fiction-first RP engine; Markdown/frontmatter and human-editable JSON remain authoritative, with disposable databases/indexes. Preserve mechanics-free play. All old roadmap specs except **Mechanics II** were confirmed landed by the owner; Mechanics II is deliberately deferred to the main mechanics-improvement sprint. The separate `roadmapping` branch is exploratory and **must never be merged**. The tech-debt cleanup is intended to be followed by improved, false-positive-calibrated `coding-skills` checks and a hard B+ (87/100) PR gate. That gate is not active now.
