# Code Removal Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline; independent Codex reviews gate this work.

**Goal:** Implement approved report items 1-9, retain all HTTP capabilities, then delete the report.
**Architecture:** Consolidate pure shared rules below their consumers, using module imports and preserving public hash aliases. Remove only declarations proven unused.
**Tech Stack:** Python/FastAPI, TypeScript/React, pytest/vitest.
**Spec:** ../specs/2026-10-18-code-removal-design.md

## Constraints and review focus

Preserve text fallback spelling, blank-versus-absent deadlines, oversized current history batches, untrimmed valid provenance refs, and ordered UTF-8 snapshot hashes. Leave item-10 routes/models, frozen fixture homes and real libraries untouched. Keep unique failure explanations. The in-session Codex reviewer approved the spec; external CLI review was blocked by automatic approval review.

### Task 1: Pin shared-rule contracts

- [x] Run existing retention, identity, provenance, hash and import regressions as a clean baseline.
- [x] Add compact characterization coverage for text coercion and accepted provenance spelling, using existing functions first; run green before extracting.

### Task 2: Consolidate pure rules

- [x] Replace seven local text helpers with fieldtext.text; retain distinct paid-absorb and malformed-field rationale at consumers.
- [x] Move continuity retargeting to continuity/retarget.py and update identity/materializer calls, without a reverse import.
- [x] Move history trimming to _retention.py; call trim(entries, keep, rows=RETENTION, max_bytes=MAX_BYTES) in each ledger so monkeypatched constants still apply.
- [x] Move provenance shape validators to _idea_provenance.py; bind as idea_shapes in suggest, whose public idea_provenance function must remain.
- [x] Move snapshot hashing to _actor_hash.py; retain dir_content_hash aliases in characters and pcs.
- [x] Run focused regressions and architecture guards.

### Task 3: Remove unused declarations

- [x] Remove the report's 22 client properties and their now-unused helper/type; preserve every HTTP route.
- [x] Remove _entity_image_promote, _sse_response and _selector_item, keeping still-used imports.
- [x] Retarget selector tests to _selector_inputs(...)[0], update eval/helper comments and obsolete mocks. Preserve the weather test's atomic-replace protection with still-existing controls.
- [x] Run affected backend tests, frontend typecheck and weather/art/run tests.

### Task 4: Verify, review and hand back

- [x] Run lint/type/template checks; regenerate only smaller ratcheted baselines where findings disappeared.
- [x] Request independent Codex implementation review, resolve material findings, then request adversarial spec-compliance review.
- [x] Transfer the reviewed patch to the original checkout, checking for intervening edits first.
- [x] Run all available make-check commands with existing dependencies; record unavailable tooling or dependencies explicitly without weakening guards.
- [x] Delete REFACTORING_REPORT.md, confirm item-10 route signatures/paths unchanged, count net production-source reduction and report exact verification results.

## Verification

Ran the gate commands directly because make is unavailable. Full backend coverage: 22,627 passed, 46 skipped, 93.85% coverage (93% floor). Pydantic v1 compatibility: 22,621 passed, 52 skipped. Frontend coverage: 5,783 passed; typecheck passed. Ruff, mypy and ESLint ratchets passed at their existing baselines; template verification passed all 553 checks. Independent implementation and final spec-compliance reviews approved the resulting patch.

The frozen public store facade, 574 HTTP handlers and 205 route classes remain unchanged. New root helper modules use private names to avoid extending that facade. Net production-source reduction is 205 physical lines, including the new shared modules and excluding tests and documentation. The approved report has been removed.
