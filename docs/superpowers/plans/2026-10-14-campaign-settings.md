# Campaign Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Finish #73 with inherited campaign play settings and one reachable settings page.

**Architecture:** A single campaign-settings store module validates and overlays a small allowlist over global configuration, without introducing a config/campaign import cycle. The HTTP and UI layers expose explicit inheritance and reuse existing campaign editors. Existing consumers resolve the same effective values.

**Tech Stack:** Python 3.11+, FastAPI, string frontmatter, React/TypeScript, pytest and vitest.

**Spec:** `docs/superpowers/specs/2026-10-14-campaign-settings-design.md`

## Global Constraints

- Python 3.11+, Node 18+, existing dependencies only; Android-compatible base code and plain pydantic fields.
- Only the nine settings in the spec; counts 0..2147483647, null means inherit, empty system prompt means suppress.
- Campaign writes hold `locks.campaign_lock(cid)`, use strict record reads and `store.atomic`, and preserve metadata and body.
- No private library access; tests use isolated `GRIMOIRE_HOME` and existing placeholder names and LLM fakes.
- Module-level imports and an acyclic graph; store cross-package imports bind submodules.
- Keep storage global and preserve existing settings surfaces.

## Review Focus

- Empty prompt and zero counts must not be confused with inheritance; test both API and real consumers.
- Multiline and Unicode line boundaries must round-trip without injecting frontmatter keys; test prompt serialization and label rejection.
- Invalid mixed writes and corrupt campaign records must publish nothing; compare bytes and revision before/after.
- History retrieval and context packing must use the same campaign budget/scan/recap settings; exercise both paths.
- A late read/save from campaign A must not install A's settings in campaign B, and switching sections must preserve drafts; test deferred promises and navigation.

## Task 1: Store contract and HTTP endpoint

**Files:** Create `backend/src/grimoire/store/campaign_settings.py`, `backend/tests/test_campaign_settings.py`; modify `backend/src/grimoire/store/locks.py`, `backend/src/grimoire/routes/campaigns.py`.

**Interfaces:**
- `effective_config(cid: str) -> dict[str, str]`: full global config overlaid with valid campaign overrides, for internal consumers only.
- `count(cid: str, key: str) -> int`: count from the effective config using the existing config parser and declared default.
- `view(cid: str) -> dict`: exactly `overrides`, `inherited`, `effective`, each a string map restricted to the allowlist.
- `write(cid: str, fields: dict) -> None`: partial string/null updates; validate all fields before publication, strict read and atomic write under campaign lock, no-op leaves bytes/stamps unchanged.
- GET/PUT `/api/campaigns/{cid}/settings`: view shape above; body is the partial field map, 400 on validation, 404 missing campaign, 409 unreadable campaign.

- [x] Write API tests for inheritance, set/reset, isolation, zero, empty/multiline prompt, invalid mixed fields/types/ranges, no-op, strict read, metadata/body preservation and no credential exposure.
- [x] Run the new tests and observe failure because the endpoint is absent.
- [x] Implement the store module and endpoint, classify its writer, and run focused tests plus import/lock/path/atomic guards.
- [x] Review the task against its spec and correct findings.

## Task 2: Wire effective values through play

**Files:** Modify `backend/src/grimoire/store/context/assemble.py`, `story.py`, `archive.py`, `pack.py`, `history.py`; `backend/src/grimoire/store/history/query.py`; `backend/src/grimoire/routes/history_recall.py`, `scenes.py`; extend `backend/tests/test_campaign_settings.py` and relevant existing context/history/summary tests.

**Interfaces:** Consume Task 1's `effective_config` and `count`. Existing no-campaign numeric helpers remain callable with no arguments; where necessary add an optional config/campaign argument without changing their current default behavior. Thread campaign settings through both the normal packer and frozen profile builder, and through history omissions and preview budgets. Summary/break route reads call `count(cid, key)`.

- [x] Add failing behavior tests that set different global/campaign values and inspect assembled prompt text, budget, scan selection, recap/archive selection and summary/break schedule responses.
- [x] Run the focused tests and confirm globals incorrectly win before the fix.
- [x] Update all listed consumers; preserve the opener's explicit full-recap depth and frozen historical settings.
- [x] Run the existing context/history/summary suites and guards, then review against the spec.

## Task 3: Campaign settings page and client

**Files:** Create `frontend/src/routes/CampaignSettingsView.tsx` and `.test.tsx`; modify `frontend/src/api/types.ts`, `client.ts`, `client.test.ts`, `frontend/src/App.tsx`, `frontend/src/routes/CampaignHub.tsx`, `frontend/src/components/SceneInspector.tsx`, `frontend/src/routes/CampaignView.tsx` and relevant tests.

**Interfaces:**
- `CampaignSettingKey`: union of the spec's nine keys.
- `CampaignSettings`: `{ overrides: Partial<Record<CampaignSettingKey, string>>; inherited: Record<CampaignSettingKey, string>; effective: Record<CampaignSettingKey, string> }`.
- `api.getCampaignSettings(cid: string): Promise<CampaignSettings>` uses a fresh request.
- `api.putCampaignSettings(cid: string, fields: Partial<Record<CampaignSettingKey, string | null>>): Promise<CampaignSettings>` announces `notifyConfig` only on success.
- Route `/campaigns/:cid/settings`; a `?section=` selection opens `models`, `context`, `calendar` or `advanced`, with the existing PageShell column pattern.

- [x] Install frontend dependencies with `npm ci` in this worktree (one install).
- [x] Add tests for the read-only values, explicit edit, overrides/clearing/zero, save/cancel/failure, section draft preservation, late reads/saves and campaign changes, and Models/Calendar reuse; run and observe the absent feature fail.
- [x] Implement the page with a keyed per-campaign body, a partial-update draft, existing editor/page styles, read-only default, explicit edit and save/cancel. Preserve the draft across sections. Link to global Settings for storage.
- [x] Add fresh client reads and successful-save notifications with client tests. Add route and hub/inspector links.
- [x] Load effective labels on mount and config-change events in CampaignView; guard late responses. Adjust all relevant mocks to include the new endpoint and assertions that labels still default when that read fails.
- [x] Run TypeScript and the relevant vitest suites, then review the task against the spec.

## Task 4: Whole-change verification and review

**Files:** Update the spec's verification notes and this plan; update ratchet baselines only for proven improvements. Add concise user-facing documentation in `README.md` describing campaign inheritance and the empty-prompt choice.

- [x] Run `make check` with an explicit worktree interpreter; resolve every failure, preserving the coverage floor and architecture guards. The final Windows run used the isolated Python 3.12.12 environment and eight workers recorded below.
- [x] Use `.claude/skills/verify/SKILL.md` for an isolated, mock-backed browser pass if the browser runtime is available; record any tooling limits explicitly.
- [x] Run Codex review against the diff; resolve findings. Run the final adversarial conformance review against diff and spec, resolving gaps.
- [x] Mark completed tasks, record exact verification evidence and remaining limits, and leave a reviewable branch without merging or publishing.


## Verification record

The complete `make check` gate passed on 2026-10-15, on Windows with Python
3.12.12 and eight workers. All 55 changed or new code, test and configuration
inputs matched their SHA-256 fingerprints before and after the run; verification
notes were updated afterwards.

The command was:

```text
make check PY=<worktree>/build/issue73-gate31212/Scripts/python.exe WORKERS=8
```

The environment used a fresh private `PYTEST_DEBUG_TEMPROOT` parent and
`GRIMOIRE_DIST=<worktree>/build/issue73-no-web-dist` (an absent path) to keep
backend tests independent of shared temporary-directory cleanup and the earlier
production frontend build. `PYTEST_ADDOPTS` was
`--maxfail=0 --tb=short --show-capture=no -vv`: all cases ran, with no test
selection changes or coverage exemptions. The coverage floor remained 93%.

Completed checks:

- The complete backend coverage suite passed **22,037 tests**, with 42 skips,
  in 1,041.95 seconds. Coverage was **93.70%**, above the unchanged 93% floor.
- The complete compatibility suite passed **22,031 tests**, with 48 skips,
  in 1,028.73 seconds. The gate created its own environment, verified Pydantic
  **1.10.26** with FastAPI **0.115.14**, and passed `pip check`.
- The full frontend gate passed **243 files and 5,275 tests**, plus TypeScript.
  Coverage was 89.08% statements, 84.35% branches, 84.59% functions and 91.71%
  lines. The production frontend build passed.
- Ruff 1,136, mypy 161 and ESLint 702 findings matched their ratchets; all 551
  template checks passed. `make baseline` recorded the one removed Calendar
  ESLint warning. Architecture and documentation guards passed in the full gate.
- Isolated headless Edge checks passed zero/empty/multiline settings,
  inheritance reset, campaign isolation, retained Calendar/Models drafts,
  route navigation, internal Models row protection, explicit Cancel and
  desktop/phone layouts. No page errors or model calls. Real-editor component
  regressions cover before-unload and pending/failed saves, including
  newer-format refusals that preserve a read-only draft. The in-app browser
  runtime lacked its Windows sandbox helper, so verification used the repo's
  isolated mock-backed setup with standalone Playwright and installed Edge.
  Verification servers were stopped afterwards.
- Codex spec and plan checkpoints passed after resolving ambiguous zero cadence
  and oversized integer conversion. Implementation reviews corrected retained
  editor drafts, malformed prompts and invalid Unicode error responses. Every
  subsequent verification fix passed code review and final adversarial
  conformance review; no actionable findings remain.

The full gate exposed additional verification work, now resolved:

- Captures use real placeholder campaigns in the decision and extension
  fixtures, so campaign-owned budgets are exercised. Golden wire matrices share
  an `asyncio.Runner` per observation instead of exhausting Windows loopback
  sockets; the helper retains its original three-argument call for other tests.
- History, investigation and epistemic evals share `evals.runner.isolated_home`.
  It closes process-local compiled-cache handles before restoring the previous
  root and deleting the temporary library. Twelve lifecycle regressions cover
  normal/error exits with an existing or unset previous root, including checking
  the actual SQLite handle is closed on platforms that permit unlinking it.
- Absorb's budget recognizes its own timer's expiration even if its accounting
  clock has not advanced to the deadline. Tests retain upstream timeout reasons
  and caller cancellation, and preserve the genuine in-flight cancellation case.
- The reranking timeout fixture reserves 250 ms for provider work without making
  disk preparation a timing assumption. Its real two-second bound, evidence and
  metering assertions remain; an ignored-budget negative control failed that
  bound as intended. Separate expired-phase coverage remains intact.
- The search scaling guard calibrates a common CPU-time batch with standard
  `timeit`, avoiding zero-duration samples. It retains 20k/80k inputs, alternating
  best-of-five samples and the `<8` ratio bound. The whole search suite passed;
  restoring list-based quadratic deduplication in memory failed the guard at
  19.4x scaling. The corrected benchmark also passed with coverage instrumentation.
- The store facade deliberately exports the new `campaign_settings` domain.
  Its API baseline adds only that name to `all` and `dir`; all other entries
  remain unchanged. Frozen campaign and golden wire fixtures were not regenerated.

An early compiled-delete subprocess timeout was also reproduced on unchanged
HEAD `92a55a50dd8360d8a46eb50de590e50296d7c7f3`. Private pytest temporary roots
resolved it without changing the child deadline or deletion assertions. Both
complete backend suites now pass. The implementation is ready for review on the
local `codex/issue-73-campaign-settings` branch.
