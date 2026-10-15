# Greeting Wizard Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preview greeting availability from proposed PC tags before creating a campaign (#68).

**Architecture:** A world GET projects existing greetings through the existing pure availability engine with no play history. A small read-only preview component owns debouncing and request lifetime; the wizard supplies the selected PC's or draft's tags.

**Tech Stack:** FastAPI, Python, React, TypeScript, pytest, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-14-greeting-wizard-preview-design.md`

## Global Constraints

- No added dependencies; Python remains 3.11+, Node 18+, and pydantic usage remains v1/v2 compatible.
- No role-tag namespace, character-as-player tags (#18), storage migration, or PC editor integration.
- Fixtures use repository placeholder names; backend tests isolate the store.
- Campaign draft tags remain free strings; CSV normalization mirrors persisted PC tags.
- Preview never writes records or makes model calls. Existing campaign behavior stays intact.

## Review Focus

- A greeting with ID `availability` remains readable through its existing detail URL.
- Draft labels resolve to IDs; punctuation and comma-separated tags preview the eventual PC storage semantics.
- A world switch while PC/vocabulary reads are pending never admits the old world's results.
- A tag/world change or unmount during a pending preview ignores old successes and failures and clears stale rows.
- Empty worlds, blocked greetings, loading, and failures remain distinguishable and never prevent navigation.

### Task 1: World preview route and client

**Files:** Modify `backend/src/grimoire/routes/greetings.py`, `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/api/client.test.ts`; create `backend/tests/test_greeting_availability_preview.py`.

**Interfaces:** Consume `greetings.list_greetings(root)`, `read_plotmap(root)`, and `availability(items, plotmap, played, player_tags, skipped)`. Produce `GET /api/worlds/{wid}/greeting-availability?tags=a,b`, `GreetingAvailabilityPreview` (engine fields only), and `api.previewGreetings(wid: string, tags: string[]): Promise<GreetingAvailabilityPreview[]>`.

- [x] Write route tests asserting: empty -> `[]`; missing/unsafe world -> 404; exact IDs/subsets unlock; predecessors remain blocked; location and offscreen fields survive; `availability` detail still works; the home tree is byte-identical before/after preview; comma/space input agrees with a subsequently created PC's actual availability. Add a client test for GET URL encoding and no POST body.
- [x] Run `make test-py-fast TESTS=backend/tests/test_greeting_availability_preview.py PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe` and `cd frontend; npx vitest run src/api/client.test.ts -t 'greeting preview'`; observe missing endpoint/method failures.
- [x] Implement the thin world route, CSV normalization, engine response type, and encoded client GET. Keep campaign-only fields on `Availability`.
- [x] Re-run the new tests plus `test_greetings_store.py`, `test_playing_store.py`, `test_greeting_list_edges.py`, and the whole client test file. Expect all to pass.

### Task 2: Wizard preview and request lifetime

**Files:** Create `frontend/src/components/GreetingAvailabilityPreview.tsx` and its test; modify `frontend/src/routes/CampaignWizard.tsx` and its test.

**Interfaces:** Consume Task 1's client method and response type. Produce a preview component with `{ world: string; tags: string[] }` props, keyed by world and effective tag contents in the wizard; no record-writing callbacks.

- [x] Add component tests asserting available/blocked rows and reasons; loading/empty/error; 250-ms debouncing; late successes/failures after changing key; timer cleanup on unmount. Add wizard tests for new tags (IDs rather than labels), selecting/deselecting existing PCs, no create calls during preview, and stale PC/vocabulary reads after switching worlds.
- [x] Run `cd frontend; npx vitest run src/components/GreetingAvailabilityPreview.test.tsx src/routes/CampaignWizard.test.tsx`; observe missing preview failures.
- [x] Implement the read-only component using effect cleanup and a 250-ms timer. Render blocked reasons and an offscreen label. Wire step 2 to effective tags; guard and clear world-PC/vocabulary reads when the world changes.
- [x] Re-run both component/wizard suites and `npx tsc -b`; expect all to pass.

### Task 3: Gate and independent review

- [x] Run `make check PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe`. Record actual results; update smaller lint baselines only if the gate reports resolved findings. Outcome: frontend/lint/templates passed; main backend coverage stalled at 92% and was interrupted. The separately completed pydantic-1 suite reported 9 failures and 10 setup errors in unchanged history/investigation tests. See the validation record; the full gate is not green.
- [x] Run Codex review against the working diff, then the final adversarial review against the diff and originating spec. Resolve material findings with reproducing tests and the relevant verification.
- [x] Verify the final diff contains only this issue's work and report the result, validation, and material limitations. Keep any review rulings with the validation record.

## Review record

The spec's first Codex review found that `/greetings/availability` would shadow
an existing greeting ID. The spec now uses `/greeting-availability` and requires
a compatibility test; the second Codex spec review approved it. A separate
review questioned CSV handling of a draft tag containing a comma. Source
inspection shows PC persistence already joins and re-splits those tags
(`pcs._write_meta`, `_tags_of`), so CSV correctly previews the eventual stored
PC; Task 1 explicitly verifies that agreement instead of changing storage.
