# Route package stabilization implementation plan

> **For agentic workers:** Use superpowers:executing-plans for the native implementation; use an independent Codex reviewer at each required checkpoint.

**Goal:** Finish the existing models and campaigns package conversions without changing HTTP behavior or authoritative storage.

**Architecture:** Models own request shapes by domain; their facade preserves existing imports. Campaign leaves own endpoint behavior and share one router, registered in original declaration order. Store modules continue owning persistence, locks, copy-on-write and derived caches.

**Tech stack:** Python 3.11+, FastAPI, Pydantic 1/2, pinned Ruff 0.16.0 and mypy 2.3.0.

**Spec:** `docs/engineering/modularization-wip-handoff.md` and the owner's continuation request. Reference source is commit `d5c4d6777`, where the two monolithic files still exist; starting WIP is `e5f3a1d7b`.

## Global constraints

- No real library access, fixture regeneration, new dependencies, feature changes or relaxed lint rules.
- Preserve every former module binding, including private helpers, with explicit re-exports.
- Preserve Python 3.11, Pydantic 1 and Android packaging compatibility.
- Preserve route order, signatures, dependencies, decorator markers and request schemas.
- Do not merge, open a PR, touch roadmapping or implement Mechanics II.
- Expand scope only after both packages pass the complete relevant gates.

## Review focus

- Import sorting can reorder registration: pin the entire ordered campaign route table against the original source.
- Annotation-only imports are runtime inputs: construct OpenAPI and compare the old/new models and operations under both installed Pydantic versions.
- A facade alias does not redirect assignment: inventory existing patches and preserve request behavior; settle the one constant-patch seam explicitly before editing it.
- Architecture guards may name the old module: relocate the overview permission narrowly and plant a forbidden sibling import.
- Equal lint totals can hide new errors: match surviving diagnostics to original symbol/source, then reconcile path keys.

## Task 1: Characterize the original contracts

Files: `backend/tests/test_route_package_contract.py`, `backend/tests/fixtures/route_package_contract.json`.

- [x] Capture exports and ordered route metadata from the original monoliths, without importing any private library or regenerating frozen fixtures.
- [x] Test model fields/schema contracts, facade identities, shared-router identity, endpoint markers and a cold OpenAPI build. Keep version-specific schema representations separate where needed.
- [x] Reproduce the existing relationship-history cap test and overview-guard failures before fixing either.
- [x] Inventory module-global monkeypatches: the known scalar seam is `RELATIONSHIP_HISTORY_PAGE`; patches on shared store modules retain identity.

## Task 2: Stabilize imports and ownership

Files: `backend/src/grimoire/routes/models/*.py`, `backend/src/grimoire/routes/campaigns/*.py`, `backend/tests/test_overview_guard.py`; possibly `backend/tests/test_relationship_history_routes.py` after the explicit seam decision.

- [x] Mark facade imports as explicit aliases before any automated unused-import fix. Restore former campaign imports to the facade, preserving all old bindings.
- [x] Remove only unused imports in leaves with pinned Ruff; retain evaluated annotations and dependency functions. Sort imports within registration groups, separated by explained `isort: split` markers.
- [x] Move the overview importer permission to the exact `_settings` owner while keeping any compatibility re-export permission explicit; prove a sibling remains forbidden.
- [x] Resolve the constant-patch dependency explicitly; do not claim ordinary re-exporting preserves assignment. Add durable ownership/theory notes.
- [x] Run characterization, architecture guards and affected route tests; inspect source ASTs to confirm function/class bodies are unchanged.

## Task 3: Rebase diagnostics and run the gate

Files: `lint-baselines/ruff.json`, `lint-baselines/mypy.json`, handoff and verification notes.

- [x] Collect pinned-tool diagnostics before/after. Compare by rule and original source/symbol; investigate every unmatched error instead of accepting equal totals.
- [x] Regenerate baselines through the established ratchet, accepting only verified relocation keys. Keep ESLint at 307 or below.
- [x] Run lint, mypy, templates, backend coverage, Pydantic-1 full suite, ESLint and frontend tests/typecheck with this checkout's source and the configured interpreter. If Make is unavailable, run its exact recipes and report that distinction.
- [x] Inspect remaining production hotspots and record cohesive candidates; no additional package conversion until the immediate gate passes.
- [x] Run independent Codex implementation review, resolve findings, then an adversarial spec-versus-diff review. Record evidence and commit small reviewable units; do not publish or merge.

## Checkpoint record

Spec review identified five acceptance gaps: assignment seams, exact route/HTTP contracts, guard ownership, diagnostic identity and interpreter/full-gate evidence. The tasks above cover all five. Independent Codex plan review approved characterization and import/guard cleanup. The owner explicitly approved moving the scalar test patch to its owning leaf while preserving the readable facade export; no custom module proxy is introduced.

Full validation exposed two stale prose references to `campaigns.py` and
pre-existing environment assumptions in tests. Fix the references to their
actual leaves. Verify native case distinction after fsutil rather than trusting
its exit code, which can be zero with an access-denied message; retain every
record-identity assertion. Verify that an unsent coroutine cannot resume rather
than inspecting `cr_frame`, which Python 3.12.0 retains after closing it. An
independent Codex diagnosis reproduced both outside Grimoire and confirmed the
relevant production files are unchanged. These adaptations change no engine
behavior and do not relax a lint rule or an asserted store invariant.
