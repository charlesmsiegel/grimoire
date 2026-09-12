# Character Responses Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Individual, context-isolated character responses with bounded automatic handoffs and editable response history.

**Architecture:** Reuse scene reservations and detached SSE runs. A response ledger owns stable identities, round membership, frozen prompts and variants; a bounded orchestrator runs one assigned speaker at a time. Context projection occurs before rendering/packing and model guidance remains selected by actual dispatch.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, existing atomic Markdown/JSON store; TypeScript/React/Vitest. No new dependencies.

**Spec:** docs/superpowers/specs/2026-09-11-character-turns.md

## Global Constraints

- All prompt prose lives in editable templates.
- No private library data in source. Reuse Mara, Winifred, Seraphine, Realm, Saltmarch placeholders.
- Each NPC contributes at most once automatically per PC round; narrator gets one bounded slot.
- Continue and Respond as X each authorize exactly one response, including repeats.
- Reroll replaces one response and never executes its handoff.
- Delete removes one response and never starts generation.
- Failed/incomplete control metadata never starts a successor.
- Snapshots are durable, independent of optional prompt logs, with frozen unprofiled fallback.
- Other actors contribute stable reference and name plus observed transcript only.
- Applied mechanics are never undone/reapplied through prose editing.
- Preserve scene identity, run ownership, cancellation, attempt deduplication, and atomic/lock/overlay contracts.
- No additional mandatory closeout model pass.

## Task 1: Speaker context and complete voice evidence

**Files:** store/context/assemble.py, new store/context/actor.py, model_guidance.py, context exports, scene templates, new tests/test_actor_context.py and model-guidance snapshot tests.

**Interfaces:** Add optional actor_ref: str | None and eligible_speakers: list[dict] | None to compose_turn and compose_director_turn, default None preserves combined callers. Actor refs use kind:id; narrator uses grimoire. Output remains PreparedMessages plus breakdown. Public roster entries are {ref, name}; prompt rendering receives response_actor and response_candidates. Provide PreparedMessages.snapshot() -> dict and PreparedMessages.from_snapshot(snapshot, model) -> PreparedMessages, serializing already frozen variants, including empty-profile variant.

- [x] Add failing integration tests with two NPCs whose private cards/state/owned lore contain distinct sentinel secrets. Assert active actor sees own evidence, other names and public transcript, excludes all other secrets. Assert known/public knowledge remains and forbidden future-state reroll reconstruction is not needed.
- [x] Add tests inherited mes_example selection retains whole exchanges, no hard character slicing, no example promoted into current facts; reasonable bounded protection under packing pressure.
- [x] Add presence-interval tests: a newly arrived actor must not receive earlier private dialogue. Project only posts within recorded presence intervals; when historical visibility is unknown, use conservative current interval plus explicit own knowledge. Narrator retains scene scope.
- [x] Replace blanket anchor-over-example priority: explicit authored constraints/facts remain authoritative; inferred tendencies and examples are labeled evidence. Show both sources in existing inspector/evidence UI with an author-conflict note and edit path, without extra model calls. Add conflicting anchor/example test to prove neither is silently erased.
- [x] Implement pre-render actor projection, own state/relationships/knowledge and applicable mechanics; exclude unscoped GM recap/private commitments/plots/secret archives when no knowledge attribution permits them. Use existing public/secrecy and actor knowledge structures rather than string post-filtering.
- [x] Add assigned-speaker template: only this actor's prose, no PC invention, observable reactions and optional next action. Shared response contract uses trailing fenced JSON:
```text
```handoff
{"next":"characters:winifred"}
```
```
  next is an eligible stable ref, grimoire, or null. Roll fence interrupts before handoff. Ordinary state metadata must remain parseable; keep its ordering coordinated with Task 2.
- [x] Implement frozen snapshot import/export without rereading templates/state, preserving actual-model guidance and fallback to frozen unprofiled prompts.
- [x] Run focused context/model tests, frozen render harness and import guard; report exact evidence and commit only owned files.

## Task 2: Durable responses, bounded orchestration and mechanics integration

**Files:** new store/responses.py and response protocol helper, routes/character_turns.py, narrow scenes.py/streaming.py/mechanics.py integration, scene read/write serialization as needed, store locks/routing/config registration, tests/test_character_turns.py, tests/test_responses.py, shared tests/llm_fakes.py extensions only when needed.

**Interfaces:** ChatTurn gains speaker_ref?: str. Existing POST chat uses new engine by default; empty/director sends are single contributions. Config character_response_mode is individual by default, combined opt-out. Scene message JSON adds optional response_id, response_status (complete/incomplete), context_changed, response_can_reroll. Preserve legacy fields and transcript role/speaker semantics. Endpoints:
- GET /campaigns/{cid}/scenes/{sid}/responses/{rid}: record metadata and variants (do not expose private frozen prompt body in ordinary response).
- POST .../responses/{rid}/regenerate: same regenerate body/attempt header and detached SSE contract as existing generation.
- DELETE .../responses/{rid}: one response, returns refreshed scene or standard mutation report.
- POST .../responses/{rid}/variants/{vid}/activate: promote one completed variant with mechanics/context guards.

Task 1 compose signature and PreparedMessages snapshot are dependencies; implement protocol/storage first independently. Task 3 consumes these endpoints.

- [x] Add failing pure protocol tests for split fences, torn/invalid JSON, unknown/PC/absent/repeated ref, extra keys and narrator repeat. State and roll fences remain hidden and distinguishable. Successful response is persisted before validating/executing successor.
- [x] Add failing store tests: stable identity across earlier deletion/edit, identical repeated text, legacy migration; one selected replacement retains later replies and marks context_changed; variants remain until replacement accepted.
- [x] Implement durable response/round records using existing atomic helpers and campaign locks. Reconcile transcript edits through authoritative IDs, not ambiguous text-match identities. Handle scene rename/delete/replay lifecycles deliberately. Persist prompt snapshot and pending step before dispatch; completed contributions are never replayed on reconnect/retry.
- [x] Add engine tests using shared ScriptedProvider/FakeLLM: Mara -> Winifred -> Mara costs only two calls; single NPC needs no selector; initial selector may stop; Continue/manual exactly one, missing handoff stops, cancellation after first cannot launch second, failed second preserves first.
- [x] Implement structured public-only initial selector template and metered routed call; skip for explicit actor or single NPC. Chain within existing parent reservation/identity fence and emit existing delta frames plus response boundaries as needed. Capture/meter every actual call. Aggregate followups once on parent settlement, not per contribution.
- [x] Implement individual delete/reroll/variant operations. Reroll uses only saved prompt snapshot and optional explicit steer, ignores handoff, rejects legacy missing snapshot with historical_context_unavailable. Before edits, reject applied mechanics at/after affected boundary; invalidate pending proposals, rolling summary and transient state from affected point. Do not touch completed roll audit.
- [x] Add roll integration tests: pause persists current actor/round/slot and releases reservation; accept/decline reacquires existing mechanics run, validates identity/watermark, resumes same actor before eligible handoff. Never spend twice or reuse pre-roll handoff.
- [x] Persist round eligibility/budget through pause and retry; attribute selector and response usage to the same round/PC post plus response identity where applicable.
- [x] Integrate retry/resume and error outcomes. Partial response visible incomplete, no handoff; retry addresses unfinished step rather than regenerating successful earlier responses. Add runtime mode fallback and config validation.
- [x] Run focused routes/streaming/scene-freeze/proposal/recovery tests and all architecture guards; commit owned files, report exact results.

## Task 3: Individual controls and character creation from prose

**Files:** frontend src/api scene types/functions; CampaignView.tsx and focused extracted response-controls component; config setting; new passage-character dialog; routes/passage_characters.py, templates/character_from_passage/*, store evidence helper if required, route registration; focused backend/frontend tests.

**Interfaces:** Consume Task 2 response fields/endpoints; api.chat body speaker_ref selects explicit actor. Add POST .../responses/{rid}/character-draft via existing detached draft contract, body {name, passage}; draft returns name, description, mes_example and attributed quote evidence. Add POST .../responses/{rid}/character with {name, description, mes_example, passage, existing_ref?}; user-reviewed creation/attach, return character/version/name. Use existing campaign-local overlay.create_character and cast seating path. Source response ID and selected text snapshot retained in campaign-local evidence, not templates or public repo.

- [x] Write failing frontend tests for one-response Delete/Reroll, Continue and Respond as selected present NPC, disabled while busy, changed-context/incomplete badges, variant selection, legacy replay action. Preserve explicit cut-from-here as separate existing destructive action.
- [x] Implement client and UI. Keep streaming response boundaries so multiple speakers display distinct entries; refetch authoritative scene on settlement. Existing attempt registry/reconnect remains authoritative. Followups remain server-owned.
- [x] Add config individual/combined option for comparing behavior, individual default; no model-specific sampling changes.
- [x] Write failing extraction tests: multi-person narrator passage excludes another speaker's quotes; no spoken evidence yields empty examples; attach-existing avoids duplicate and preserves inherited card via campaign-local overlay; source changed between draft/save rejected or reviewed snapshot explicitly retained.
- [x] Implement optional draft generation using existing draft run helper and routing/meter, bounded neighboring observable context. Extracted dialogue must be supported by selected text and attributed to reviewed identity. Do not invent quotes; user reviews name, description and examples before save. Sparse evidence produces sparse character. Preserve exact source response identity + snapshot.
- [x] Implement Create character from this passage on narrator responses with editable selected passage/name and attach-existing option. Reuse editor conventions and existing errors/loading/shortcuts, then refresh cast.
- [x] Run focused frontend tests/typecheck and backend draft/evidence/scene-freeze guards; commit owned files.

## Task 4: Integration review and verification

- [x] Review per-task diffs against spec and task contracts before accepting each task. Resolve concrete findings and rerun covering checks.
- [x] Verify model-specific guidance still reflects actual dispatch and saved reroll context; exact unknown model uses unprofiled historical snapshot.
- [x] Deliberately regenerate frozen snapshot only if prompt changes require it; never change frozen home fixture.
- [x] Run backend tests under coverage, frontend typecheck/test coverage, templates, ruff/mypy/eslint ratchets and architecture guards; reduce ratchets only when earned. Make is unavailable here, so execute target equivalents and record platform/dependency limits.
- [x] Run final independent diff review then adversarial diff-versus-spec review; resolve omissions.
- [x] Commit final named files on feature/character-turns, leave unrelated .claude/settings.local.json untouched. Report tested behavior and remaining live-model quality uncertainty. No paid live calls, push or merge.

## Progress

- Branch created; prior reviewed voice/model implementation checkpoint f57941443.
- Spec review PASS with explicit historical/mechanics/privacy/legacy decisions.

Plan review additions: presence-interval privacy, explicit evidence precedence and author visibility, durable round budget and per-round usage attribution.


Implementation and gate execution completed on `feature/character-turns`.
See [validation results](../validation/2026-09-11-character-turns.md) for exact
passing checks, remaining Windows failures, review coverage, and live-model limits.
The full Windows suites are not claimed green; the coverage threshold was retained.
