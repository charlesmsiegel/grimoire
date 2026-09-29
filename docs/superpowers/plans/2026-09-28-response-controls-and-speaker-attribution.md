# Response Controls and Speaker Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace response presets with phase-specific word and paragraph targets, make all generation actor-scoped, and assign speaker labels and portraits in the application.

**Architecture:** A small target resolver supplies Opening or Continuation targets to existing prompt assembly. Continuations keep their individual actor engine; opener drafts sequence narrator and NPC calls through the same actor-scoped context, then adopt canonical labeled blocks into the unchanged Markdown transcript. Actor-name validation and read-side identity lookup make portraits reliable without changing stored message format.

**Tech Stack:** FastAPI, Pydantic 1/2, Jinja, Markdown store, pytest, React, TypeScript, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-28-response-controls-and-speaker-attribution-design.md`

## Global Constraints

- Opening defaults: approximately 400 words and 3 paragraphs for the narrator.
- Continuation defaults: approximately 150 words and 2 paragraphs per NPC or later narrator response.
- Preserve the existing `**Name:**` Markdown transcript and greeting formats; never write a generated PC contribution.
- Preserve old transcripts and preset files for read-only compatibility; ignore stored `character_response_mode: combined`.
- Use only placeholder names from `CLAUDE.md` in tests and docs. Do not read or expose the real data store.
- Keep existing lock, run replay, prompt capture, and usage-accounting guarantees; run `make check` with the correct Python executable.

## Review Focus

- A custom legacy preset supplies style but omits length: style still resolves and the new target falls through to the next valid source (Task 1).
- A scene changes cast identity or locked version while its opener draft is running: adoption refuses the stale draft and writes nothing (Task 4).
- The third opener call fails after two completed contributions: retry preserves the first two, regenerates only the missing speaker, and cannot adopt early (Task 4).
- An inherited World actor gains the same full name as a Campaign-local PC: the mutation is rejected before either record changes (Task 6).
- A historical abbreviated or duplicate label has no unique actor: text stays readable and the portrait is neutral (Task 7).

---

### Task 1: Phase target resolution and scoped API

**Files:** Create `backend/src/grimoire/store/response_targets.py`; modify `backend/src/grimoire/routes/models.py`, `backend/src/grimoire/routes/common.py`, `backend/src/grimoire/store/scenes/write.py`, `backend/src/grimoire/store/campaigns/lifecycle.py`, and the three `/response` scope routes; test in `backend/tests/test_response_presets.py` and `backend/tests/test_response_controls_routes.py`.

**Interfaces:** Produce `response_targets.resolve(*, turn=None, scene_meta=None, campaign_meta=None, config=None) -> dict` with `opening`, `continuation`, and per-field `provenance`; each phase has integer `words` and `paragraphs`. New persisted/API keys are `response_opening_words`, `response_opening_paragraphs`, `response_continuation_words`, `response_continuation_paragraphs`. Empty strings mean inherit. The existing `response_presets.resolve` remains only for legacy fallback and style resolution.

- [ ] Write `test_response_targets_default_and_inherit` with `assert resolved["opening"] == {"words": 400, "paragraphs": 3}` and `assert resolved["continuation"] == {"words": 150, "paragraphs": 2}`; add cases for independent per-field scope and turn overrides, invalid stored values falling through, and old preset continuation fallback without changing Opening.
- [ ] Run the focused pytest files and confirm the new-field tests fail.
- [ ] Implement the resolver and update scoped GET/PUT models, field lists, write validation, and response bundles. Reject nonpositive/noninteger writes with 400; preserve global `default_style_id` spelling.
- [ ] Run the focused tests and confirm they pass; commit the resolver and API changes.

### Task 2: Prompt budgets and removal of combined generation

**Files:** Modify `backend/src/grimoire/store/context/assemble.py`, `backend/src/grimoire/store/length_drift.py`, `backend/src/grimoire/routes/scenes.py`, `backend/src/grimoire/routes/character_turns.py`, `backend/src/grimoire/routes/config.py`, `backend/src/grimoire/routes/models.py`, and `templates/scene/sections/response_budget.j2`, `templates/scene/sections/response_format.j2`; remove unused combined-only template sections after checking references. Test in `backend/tests/test_actor_context.py`, `backend/tests/test_character_turns.py`, and `backend/tests/test_model_guidance_routes.py`.

**Interfaces:** Prompt assembly selects `opening` only for the opener narrator, `continuation` for all other actor calls. `character_turns.enabled()` is no longer a config decision; callers route every continuation through `character_turns.start`. Old `character_response_mode` config is ignored and omitted from GET/PUT API. Remove public response-preset management and length-preset endpoints while retaining internal legacy file reads. No model-facing prompt requests `**Name:**` blocks.

- [ ] Write `test_actor_prompt_uses_phase_targets_without_script_format` asserting `"about 150 words"` appears and `"**<Name>:**"` and old block limits do not; write `test_legacy_combined_setting_uses_individual_turns` asserting two response starts have two distinct actor refs after setting the old config value.
- [ ] Run focused tests and confirm the new expectations fail.
- [ ] Wire the new targets into prompt assembly and drift feedback, remove the combined route/config/template branches and obsolete preset management endpoints, and preserve historical transcript parsing.
- [ ] Run focused tests plus `make check-templates`; update or remove tests whose only purpose was the deleted combined path; commit.

### Task 3: Settings UI and one-shot controls

**Files:** Create `frontend/src/components/ResponseTargetsPicker.tsx`; modify `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/routes/ConfigView.tsx`, `frontend/src/routes/CampaignView.tsx`, and route registration/navigation for `ResponsePresetsView`; remove `ResponsePresetPicker.tsx` and `ResponsePresetsView.tsx` once unused. Test in `frontend/src/components/ResponseTargetsPicker.test.tsx`, `frontend/src/routes/ConfigView.test.tsx`, and `frontend/src/routes/CampaignView.test.tsx`.

**Interfaces:** `ResponseBundle` exposes the four raw target strings, effective Opening and Continuation values, provenance, and separate `style_id`. The scene composer sends `ResponseOverride` with only one-shot continuation word/paragraph values. It no longer offers a named preset or combined mode.

- [ ] Write `ResponseTargetsPicker` tests asserting Opening and Continuation numeric values and source labels at each scope; extend `CampaignView.test.tsx` to assert one-shot values in the request and `ConfigView.test.tsx` to assert `queryByLabelText("Scene response mode")` is null. Assert the old preset picker and navigation are absent.
- [ ] Run the focused Vitest files and confirm failures.
- [ ] Replace the picker and chip, update API types/client, and remove unused preset management UI and frontend routes.
- [ ] Run focused Vitest, typecheck, and ESLint baseline; commit.

### Task 4: Actor-scoped opener draft and atomic adoption

**Files:** Modify `backend/src/grimoire/store/context/assemble.py`, `backend/src/grimoire/routes/greetings.py`, `backend/src/grimoire/routes/models.py`, `backend/src/grimoire/routes/runs.py`, and opener templates; add a focused `backend/src/grimoire/routes/opener_draft.py` only if the orchestration would otherwise overload `greetings.py`. Test in `backend/tests/test_draft_runs.py`, `backend/tests/test_runs_routes.py`, and `backend/tests/test_routes.py`.

**Interfaces:** `compose_opener(..., actor_ref: str, prior: list[dict])` uses full opener recap and actor-scoped context. The opener stream emits speaker start `{actor_ref, speaker}`, text deltas, and speaker completion. The first-post request carries ordered `{actor_ref, speaker, content}` contributions plus the cast/name/version snapshot. The backend derives canonical labels and writes all posts under one held adoption operation. Existing manual `FirstPost.text` remains supported.

- [ ] Write `test_opener_drafts_known_speakers_then_adopts` asserting call order `grimoire`, `characters:mara`, `characters:winifred`, no scene messages before Use, earlier contributions in later prompts, and exact `**Grimoire:**`, `**Mara:**`, `**Winifred:**` labels afterward. Add own-label stripping, other-label rejection, no PC prose, and attempt replay assertions.
- [ ] Write `test_opener_retry_and_stale_adoption` asserting a failed third call leaves two complete draft parts, retry calls only the third speaker, Use before completion writes nothing, and any changed cast/name/version or nonempty scene returns 409 without a partial transcript.
- [ ] Run the focused tests and confirm the new behavior fails.
- [ ] Implement draft orchestration, structured stream, retry, and fenced adoption using existing run and scene locks; preserve prompt capture and usage per call. Keep the old manual text adoption and greeting serialization paths.
- [ ] Run focused tests and commit.

### Task 5: Opener preview and greeting save

**Files:** Modify `frontend/src/components/OpenerComposer.tsx`, `frontend/src/api/client.ts`, `frontend/src/api/types.ts`, `frontend/src/components/CampaignWizard.tsx` if it consumes opener output, and relevant `CampaignView`/wizard tests; create `frontend/src/components/OpenerComposer.test.tsx` if no focused test exists.

**Interfaces:** `api.opener` reports actor-scoped stream events and returns a complete structured draft; `api.firstPost` submits that draft. The preview renders known names and actor portraits; Save as greeting sends application-serialized `**Name:**` blocks through the existing greeting API.

- [ ] Write `OpenerComposer` tests asserting speaker order and actor portraits, `Use` disabled on partial failure, retry of only missing speakers, no stale scene navigation, and greeting body containing the three application-written labels.
- [ ] Run focused Vitest and confirm failures.
- [ ] Implement the structured preview, retry, Use, and greeting paths; preserve abandoned draft and network-error behavior.
- [ ] Run focused Vitest and typecheck; commit.

### Task 6: Unique full actor names

**Files:** Create `backend/src/grimoire/store/actor_names.py`; modify World and Campaign actor mutation routes in `backend/src/grimoire/routes/characters.py`, `worlds.py`, `campaigns.py`, and `scenes.py` (emergent actors), plus the store mutation helpers they call where atomic checks must share a lock. Test in a new `backend/tests/test_actor_names.py` and focused existing actor route tests.

**Interfaces:** `actor_names.require_unique(name: str, *, scope: str, scope_id: str, actor_ref: str | None = None) -> None` raises a typed conflict naming the other actor. Compare casefolded, stripped full names across distinct PC and character IDs and every readable version in a World or Campaign effective library. World writes also inspect dependent Campaign effective libraries. The calling mutation returns 409 without a partial write; existing collisions remain readable.

- [ ] Write `test_rejects_duplicate_full_actor_names` asserting a cross-kind, case-insensitive duplicate returns 409 and the original record remains intact. Add parameterized cases for create, version create/update/select, rename, import, Campaign localization, inherited World collisions, same-actor versions allowed, and pre-existing duplicate reads.
- [ ] Run focused tests and confirm conflicting writes currently succeed.
- [ ] Implement one name index and enforce it in every actor write path under the applicable existing lock; return a clear 409 with the conflicting actor.
- [ ] Run focused tests and commit.

### Task 7: Deterministic portraits on scene read

**Files:** Modify `backend/src/grimoire/routes/scenes.py` or `backend/src/grimoire/store/scenes/read.py` for read-side response lookup, `frontend/src/api/types.ts`, and `frontend/src/routes/CampaignView.tsx`; test in `backend/tests/test_response_controls_routes.py` and `frontend/src/routes/CampaignView.test.tsx`.

**Interfaces:** Scene `Message` adds a read-only `actor_ref?: string` populated from the existing response ledger when `response_id` is present. The transcript file remains unchanged. The frontend chooses `actor_ref` first, then an exact full-name cast match, then historical unique-prefix matching; an ambiguous name has no portrait.

- [ ] Write `test_scene_read_exposes_existing_response_actor` asserting a reloaded response message has `actor_ref == "characters:mara"` with no new transcript metadata; write frontend plate tests for that ref, exact opener labels, unique historical prefixes, duplicate labels with neutral portrait, and changed cast/version.
- [ ] Run focused tests and confirm actor identity/portrait gaps.
- [ ] Join response records for returned scene messages without per-message file reads; update plate grouping/portrait selection by actor ID; retain a neutral fallback.
- [ ] Run focused backend and frontend tests; commit.

### Task 8: Integrated verification

**Files:** Update only tests or implementation implicated by failures; do not touch the frozen campaign fixture.

**Interfaces:** All earlier task contracts work together on the feature branch.

- [ ] Run `make check` using the checkout's Python executable; if a lint finding is removed, run `make baseline` and commit the reduced baseline as `CONTRIBUTING.md` requires.
- [ ] Use the repository `verify` skill with an isolated placeholder store to generate, preview, adopt, reload, and continue a scene; confirm narrator and NPC lengths, labels, and portraits. Shut down the isolated services.
- [ ] Inspect `git diff --check` and `git status`; commit any gate fixes. Do not stage `.claude/settings.local.json`.
