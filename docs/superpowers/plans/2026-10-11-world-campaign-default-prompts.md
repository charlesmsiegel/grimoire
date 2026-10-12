# Standing Prompts and Scene Focus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add live-inherited world defaults, additive campaign defaults, and gentle scene focus generated with scene drafts and persisted in scene frontmatter.

**Architecture:** A store-owned default-prompt module owns the two sidecars and their resolution; a scene-owned focus module owns the normalized frontmatter scalar. The shared play composer renders three protected sections, while the existing creation-generation flows produce reviewable focus without another model call. Dedicated editors consume the same APIs and expose failures and layout opt-outs.

**Tech Stack:** Python/FastAPI, Android-compatible pydantic, Jinja, React/TypeScript, pytest and Vitest; no new dependencies.

**Spec:** [`../specs/2026-10-11-world-campaign-default-prompts-design.md`](../specs/2026-10-11-world-campaign-default-prompts-design.md)

**Status:** Approved by the owner for Native/inline execution on 2026-10-11. The specification's adversarial gate approved planning. The amended plan-to-implementation re-review approved implementation with no material findings after the suggestion-hook warning handoff was added; Windows sandbox startup prevented exact-checkout inspection, so it used the supplied amended diff and unchanged public-main contracts. Before product implementation started, the owner chose to pause the feature for baseline repairs after the existing concurrent-append test failed reproducibly. That pause was resolved before feature implementation resumed.

The baseline pause is now resolved. The prerequisite repairs passed the full
`make check` on Windows at repair snapshot `20df292c6`, then two fresh independent
`brutal-review` rounds found no confirmed defects across the full repair branch.
The owner explicitly authorized those local rounds to replace the native review
gates for the baseline repairs only. All ten repair commits are now integrated
into this feature branch; before feature edits, its application sources were
verified identical to that repair snapshot.
The owner resumed Native/inline implementation after that verified baseline.
For the feature's final reviews, the owner also authorized the local independent
`brutal-review` loop to replace the native review gates. Independent review and
the complete implementation verification remain required; no external code
transmission, merge, push, or squash is authorized by that substitution.

Execution checkpoints: Tasks 1–5 are complete (live defaults, atomic scene-focus
creation/lifecycle, protected composition/APIs, same-call generation, saved
ideas and scope-safe editors). Task 3's broad completion selection passed 1,041 tests; Task 4's passed
536 tests. Task 4's frontend checkpoint passed 343 tests plus type-checking,
and template verification passed 460 checks. Task 5's completion selection
passed 611 tests. Its targeted UI tests caught and pinned a
late old-campaign intent replacing a new confirmation, now guarded by question
generation. The ignored SDD ledger owns command logs and exact commit
boundaries. Independent rounds found a world-route mock omission
(fixed without weakening its assertions; all 82 route tests passed) and the
creation instructions' missing explicit narrator/NPC visibility warning
(now pinned in three rendered builders; the 116-test creation/golden selection
passed without regenerating the original golden fixture).

Final independent round 5 reviewed application snapshot `f907cb34a4` and all
fourteen acceptance cases: non-blocking, with no confirmed defect or material
spec gap. It independently passed both real-transport freshness regressions.
Earlier rounds also caught and pinned focus-only intent notices, cleared focus
during calendar filling, active-reply preservation and the post-save in-flight
context race. Isolated browser verification is complete: defaults/reload,
reviewed generated/saved/manual creation, clear/no opener, scalar frontmatter,
live play, layout exclusion/bypass, frozen replay and a held NPC stream.
Only public placeholders and the shared local cassette were used; the isolated
servers are stopped. No live-model quality or real-library savings are claimed.

The final local `make check` at that application snapshot exited 2. Static
ratchets remained at baseline (ruff 1,146; mypy 161; ESLint 727); all 460 template
checks and frontend coverage (4,673 tests across 215 files) passed. Backend:
18,064 passed, 34 skipped, three failed, with 94.14% coverage. The pydantic1
stage did not run because the backend target failed. Failures from that initial run:

- `test_image_hash.py::test_loop_count_is_hashed_as_total_plays`: Pillow's
  animated WebP encoder raises `WebPEncodingError: 1` while creating the input;
  its root cause has not been established.
- `test_frozen_campaign.py::test_the_frozen_campaign_still_reads_the_way_it_was_recorded`:
  the approved focus instructions/schema change the captured suggestion and
  intent builders. The original fixture `home/` and snapshot remain unchanged;
  a deliberate compatibility expectation update still requires verification.
- `test_routes.py::test_saving_an_idea_resolves_its_references_on_the_way_back`:
  the old exact response expectation omits the new empty `focus` field.

On 2026-10-12 the owner requested opening a PR and delegating remaining final
checks to CI rather than continuing local reruns. This explicitly authorizes
pushing this branch and creating a PR against `main`, not merging or squashing.
The PR must disclose the failed local gate and remain a validation handoff,
not a claim that the branch is green. The attached phone is explicitly out of
scope: no device probing, installation or verification. The ignored ledger and
review/run artifacts are retained, rather than discarded at handoff.

CI repair follow-up: the owner then requested fixing the failed checks. Initial
CI run `38221243125` reported the same two stale expectations under Python 3.11,
Python 3.14 and pydantic 1.10; the Windows animated-WebP encoder failure did not
occur in those jobs. The approved generation instructions/schema were correct,
so checkpoint `3bf858809` updates the legacy saved-idea assertion with `focus: ""`
and deliberately refreshes only `snapshot.json` through the prescribed
scratch-copy sweep. Its diff changes exactly the four standard/offscreen
suggestion/intent builder entries; every other snapshot section, including play
prompts, remains unchanged. Neither frozen `home/` nor the original suggestion
golden changes, and no assertion is removed or relaxed.

Both CI failures were reproduced locally before this expectation-only repair.
The post-repair selection passed 84 tests, including the complete frozen-store
semantic/read-only harness, legacy idea route, focus generation, saved-idea store
and original-golden comparison. Ruff/mypy remained at baseline and all 460
template checks passed. Independent repair review and the refreshed full CI run
remain the handoff gates; no further full Windows-suite rerun is claimed. The
separate local WebP encoder issue remains uninvestigated, not silently fixed.

## Global Constraints

- "World and campaign defaults use optional UTF-8 plain-text sidecars": `<world root>/default_prompt.md` and `<campaign root>/default_prompt.md`.
- "Scene focus is the optional string-scalar key `focus` in the addressed scene's existing Markdown frontmatter."
- "Focus is deliberately one paragraph." Replace `frontmatter.LINE_BREAKS` with spaces and trim outer whitespace; blank clears the key.
- "All prompt wording belongs in Jinja templates."
- "Use `LOCK_IN` so the packer never drops or partially truncates an enabled default or scene focus."
- "Guaranteed inclusion is not guaranteed fulfilment."
- "No new dependencies or desktop-only path assumptions are needed."
- "Repository examples and tests use the existing placeholder set only"; do not inspect or modify the private library.
- "Blank and verbatim-greeting drafts without a metadata-generation call remain model-free."
- "No migration, identity backfill or generation is triggered merely by reading or editing focus."

Read the full repository rules in `CLAUDE.md`, the gate commands in
`CONTRIBUTING.md`, and the atomicity/lock contracts in `docs/store-guarantees.md`.
Those files remain authoritative; this plan does not redefine their guards.

## Review Focus

Each condition below gets a named test in its owning task:

1. Publication succeeds but its activity stamp fails: report a successful save, preserve the published text, and do not invite a retry (Task 1).
2. A focus edit overlaps a transcript append: both changes survive, and a malformed scene is never replaced with a focus-only file (Task 2).
3. Layout data is malformed or repeats an ID, and an authored macro draws randomly: creation/play respect the same explicit disable and one captured expansion is reused for profile variants (Tasks 3–4).
4. A model emits `null`/an object for focus, or an old saved idea lacks it: keep the valid draft with empty focus rather than stringify the value or add a repair call (Task 4).
5. A focus/default read or save finishes after navigation, or a generated focus was cleared: do not overwrite the new scope or resurrect the cleared value (Task 5).

## Delivery and Commit Discipline

The owner explicitly wants frequent checkpoints, not one commit per slice.
Commit coherent spec/plan edits while writing them. During implementation,
commit any independently meaningful substep once its targeted checks pass;
do not wait for the end of a task when an earlier checkpoint is already sound.
Do not commit a deliberately failing TDD phase as though it were a stable
checkpoint. Stage exact task files, not the whole worktree. Preserve unrelated
user changes. Do not squash, push, merge, or implement before the relevant
authority and review gates in `CLAUDE.md` permit it.

From this worktree on Windows, targeted backend iterations use:

```powershell
make test-py-fast PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe TESTS="backend/tests/<task-tests>.py"
```

This is an iteration check, not the repository gate. Frontend iterations run
from `frontend/` with `npx vitest run <task-tests>`; use the full coverage/gate
commands from `CONTRIBUTING.md` before declaring implementation verified.

## File and Interface Map

| Owner | Files | Interface consumed downstream |
|---|---|---|
| Task 1: defaults | New `backend/src/grimoire/store/default_prompts.py`; store facade and lock classification | `read_world(wid)`, `read_campaign(cid)`, `resolve(cid)`, `set_world(wid, text)`, `set_campaign(cid, text)` |
| Task 2: scene focus | New `backend/src/grimoire/store/scenes/focus.py`; scene lifecycle/facade, branch copy keys | `normalize(text)`, `read(cid, sid)`, `set(cid, sid, text)`; `create_scene(..., focus="")` |
| Task 3: API/play context | New `backend/src/grimoire/routes/default_prompts.py`; models/router, composer/layout/templates, API types/client | Six editing endpoints; `PromptField`, `CampaignDefaultPrompt`; three catalog IDs; `layout.enabled_ids(ids)` |
| Task 4: generation | Suggestion/intent builder/parser/templates, routes, saved ideas, frontend draft conversions | String `focus` on suggestions, intent results, saved ideas and `SceneDraft`; same-call generation |
| Task 5: editors | New `frontend/src/components/DefaultPromptEditor.tsx` and `SceneFocusEditor.tsx`; WorldOverview, CampaignHub, SceneInspector, SceneConfirmForm | View/edit/save/cancel, inherited preview, reviewed creation value, scoped warnings |

Store field readers return `{"text": str, "status": "ok" | "empty" | "unreadable"}`.
`default_prompts.resolve(cid)` returns the campaign's field plus
`world: {id: str | None, text: str, status: "ok" | "empty" | "unreadable" | "unavailable"}`.
New Python parameters are keyword-only; preserve every existing positional
parameter. The TypeScript creation client appends an optional fifth argument.
Keep imports at module scope, use module bindings inside `store/`, and verify
the import graph instead of adding a cycle-breaking local import.

---

### Task 1: Default Sidecars and Live Resolution

**Files:**

- Create: `backend/src/grimoire/store/default_prompts.py`
- Modify: `backend/src/grimoire/store/__init__.py`, `backend/src/grimoire/store/locks.py`
- Deliberate public export addition: add only `default_prompts` to both lists in `backend/tests/store_api_baseline.json`, with coverage from `backend/tests/test_store_api_baseline.py`; do not regenerate unrelated facade names.
- Test: new `backend/tests/test_default_prompts_store.py`
- Extend: `backend/tests/test_world_fork.py`, `backend/tests/test_world_bundle.py`, `backend/tests/test_fork_store.py`, `backend/tests/test_backups_store.py`

**Interfaces:**

- Consumes: `campaigns.paths.campaign_root`, `campaigns.read.read_campaign`/`world_root_of`, `worlds.paths.world_root`/`world_exists`, `store.atomic`, existing activity conventions.
- Produces: `read_world(wid: str) -> dict[str, str]`, `read_campaign(cid: str) -> dict[str, str]`, `resolve(cid: str) -> dict`, `set_world(wid: str, text: str) -> dict[str, str]`, `set_campaign(cid: str, text: str) -> dict`.
- Setter results have the GET payload shape: world field; campaign field plus resolved world. Readers create nothing; setters publish the full text once.

- [ ] **Step 1: Write failing storage and lifecycle tests.**

In the new test file, use the existing isolated-store pattern to create only
`Realm` and `Saltmarch`; no new LLM fake. Add these assertions:

```python
def test_live_world_and_additive_campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    assert default_prompts.resolve(cid)["text"] == ""
    default_prompts.set_world(wid, "The river freezes each winter.")
    default_prompts.set_campaign(cid, "This winter the harbor stays open.")
    first = default_prompts.resolve(cid)
    assert first["world"]["text"] == "The river freezes each winter."
    assert first["text"] == "This winter the harbor stays open."
    default_prompts.set_world(wid, "The river freezes each autumn.")
    assert default_prompts.resolve(cid)["world"]["text"] == "The river freezes each autumn."
    default_prompts.set_campaign(cid, " \n ")
    assert default_prompts.resolve(cid)["status"] == "empty"
    assert default_prompts.resolve(cid)["world"]["status"] == "ok"
```

Also add `test_missing_reads_create_no_sidecars`,
`test_nonblank_sidecar_preserves_multiline_text`,
`test_unbound_missing_and_unsafe_world_never_fall_back`,
`test_unreadable_sidecar_has_status_without_prose_logging`,
`test_failed_publication_preserves_previous_text`, and
`test_successful_publication_survives_activity_failure`.
Assert unknown/unsafe scope saves leave the store unchanged and concurrent
campaign saves serialize through the campaign lock. Extend existing fork,
bundle and backup tests to assert independent copies and live world inheritance;
historical forks copy the current campaign field, not an invented past value.

- [ ] **Step 2: Run the new test file red.**

Run the targeted backend command above with
`TESTS="backend/tests/test_default_prompts_store.py"`.
Expected: collection/import failure for the new module or failures on its
missing functions, not an unrelated environment failure.

- [ ] **Step 3: Implement the store-owned field reader, setters and resolver.**

Validate existing scopes before deriving sidecar paths. Read the optional file
directly with narrow `FileNotFoundError` versus decode/IO failure handling.
Resolve only the bound world via the existing resolver; distinguish a valid
but unavailable world from an invalid/unbound reference. An unavailable world
is not a campaign-directory fallback. Preserve nonblank default text; publish
empty text for whitespace-only clears.

`set_campaign` holds `locks.campaign_lock(cid)` across validation and publication.
Register `store.default_prompts` in `DOMAIN_MODULES`; its `wid`-only writer is
world-scoped, not a campaign-mutator exemption. Use `atomic.write_text` and
existing fail-soft activity stamping. HTTP campaign revision stamping remains
on the existing activity middleware path; do not double-count it or turn a
secondary stamp failure into a failed publication.

- [ ] **Step 4: Run targeted store, copy and guard checks green.**

Run `make test-py-fast` with `TESTS` naming the new store file and the four
extended copy/backup files, plus `test_atomic_guard.py`,
`test_lock_domain_guard.py`, `test_paths_guard.py`, `test_import_guard.py` and
`test_path_guard_store.py`. Expected: all targeted tests pass; no private
library or frozen fixture `home/` is written.

- [ ] **Step 5: Commit passing substeps and the completed storage deliverable.**

Stage only the files listed for this task. A defaults-reader/resolver checkpoint
may precede copy-workflow tests once its own checks pass; do not wait for all
downstream prompt/UI work. Suggested final checkpoint message:
`feat: persist and resolve world and campaign defaults`.

### Task 2: Scene Focus Frontmatter and Initial Creation

**Files:**

- Create: `backend/src/grimoire/store/scenes/focus.py`
- Modify: `backend/src/grimoire/store/scenes/__init__.py`, `backend/src/grimoire/store/scenes/lifecycle.py`, `backend/src/grimoire/store/branch.py`, `backend/src/grimoire/store/locks.py`
- Modify: `backend/src/grimoire/routes/models.py` (`NewScene`), `backend/src/grimoire/routes/scenes.py` (`post_scene`)
- Test: new `backend/tests/test_scene_focus_store.py`, `backend/tests/test_scene_focus_routes.py`; extend `backend/tests/test_scene_store.py`, `backend/tests/test_branch_copy.py`, `backend/tests/test_fork_store.py`

**Interfaces:**

- Consumes: existing scene paths, `frontmatter.read_record`, `frontmatter.LINE_BREAKS`, `scenes.locking._serialized`, and `atomic.write_text`.
- Produces: `focus.normalize(text: str) -> str`, `focus.read(cid: str, sid: str) -> dict[str, str]`, `focus.set(cid: str, sid: str, text: str) -> dict[str, str]`.
- Extend `create_scene(cid, title, suggested_date=None, pcless=False, model="", *, focus="") -> str` and its locked inner to publish optional focus in the first scene write. Preserve all existing positional arguments, defaults and call sites.

- [ ] **Step 1: Write failing scalar, mutation and lifecycle tests.**

```python
@pytest.mark.parametrize("boundary", sorted(frontmatter.LINE_BREAKS))
def test_focus_normalization_cannot_inject_metadata(boundary):
    value = f"Offer Mara a choice.{boundary}clock: changed{boundary}---"
    assert focus.normalize(value) == "Offer Mara a choice. clock: changed ---"
```

Add `test_create_publishes_focus_before_first_opener` (inspect the first atomic
scene publication, not a later read alone),
`test_clear_preserves_body_metadata_and_updated`,
`test_focus_edit_and_append_both_survive` (deterministic thread events, no
timing-based sleep), `test_malformed_scene_is_not_overwritten`,
`test_legacy_focus_read_and_save_mint_no_identity`, and
`test_focus_follows_rename_repad_branch_fork_and_delete`.
Round-trip apostrophes, colons and fence-like text. Empty creation omits the
key. Reused scene IDs get no deleted focus. Branch/fork copies are independent
and preserve current focus through transcript cuts and scene end.
Pin edge-whitespace semantics too: `"\x1f Offer Mara a choice. \x1f"` normalizes
to `"Offer Mara a choice."`, while a non-whitespace `\ufeff` remains content.

Create-route tests submit `focus`, assert it is present immediately, and assert
omission remains compatible. Explicit null/object/number requests are invalid.
Use a plain `StrictStr` field where needed for v1/v2 parity, not validators or
`Field` configuration; this retains the spec's string API contract.

- [ ] **Step 2: Run the new focus tests red.**

Run the targeted backend command with
`TESTS="backend/tests/test_scene_focus_store.py backend/tests/test_scene_focus_routes.py backend/tests/test_scene_store.py"`.
Expected: the new module/field is missing or the first-publication assertions
fail for that reason.

- [ ] **Step 3: Implement scene-owned focus and thread it through creation.**

`normalize` replaces each recognized boundary with a space and trims only the
edges. `read` distinguishes empty from unreadable metadata without minting
identity. `set` wears `@locking._serialized`, strictly reads the existing
record, changes only `focus`, and republishes its existing body. Blank removes
the key; never update transcript `updated` or alter posts/response captures.
Register `store.scenes.focus` in the lock domain and expose the module through
the facade without introducing a cycle.

Normalize creation input before adding nonempty focus to initial metadata;
do not perform a second focus PUT. Add `focus` to `branch.COPIED_KEYS`.
Ordinary scene/fork copies, renames and deletion then carry or remove focus
with the file, without any separate identity-keyed focus cleanup.

- [ ] **Step 4: Run focus, creation, copy and architecture tests green.**

Run the targeted backend command over this task's tests and the same store
guards as Task 1, adding `test_frontmatter.py` and `test_pydantic_guard.py`.
Expected: all pass; malformed/failed writes preserve the complete old scene,
and no new legacy identity or private-data migration occurs.

- [ ] **Step 5: Commit passing scalar/creation and lifecycle checkpoints.**

Commit a coherent focus-store/initial-create checkpoint when those tests pass;
commit branch/copy coverage independently once green. Suggested final message:
`feat: retain gentle scene focus in frontmatter`.

### Task 3: Editing APIs and Protected Play Context

**Files:**

- Create: `backend/src/grimoire/routes/default_prompts.py`
- Modify: `backend/src/grimoire/routes/models.py`, `backend/src/grimoire/routes/__init__.py`, `backend/src/grimoire/routes/scenes.py` (context reporting, not replay injection)
- Modify: `backend/src/grimoire/store/context/assemble.py`, `backend/src/grimoire/store/context/layout.py`
- Create: `templates/scene/sections/world_default_prompt.j2`, `templates/scene/sections/campaign_default_prompt.j2`, `templates/scene/sections/scene_focus.j2`
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `templates/README.md`, `scripts/verify_templates.py`, `evals/cases.py`
- Test: new `backend/tests/test_default_prompts_routes.py`, `backend/tests/test_standing_prompt_context.py`; extend `test_scene_focus_routes.py`, `test_route_order.py`, `test_context_layout.py`, `test_response_snapshots.py`, `test_response_extend.py`, `test_context_compare.py`, `test_prompt_log_store.py`, `test_evals.py` (all under `backend/tests/`)

**Interfaces:**

- Consumes: Task 1 resolver, Task 2 scene reader/setter, existing route existence checks, `runs.require_scene_open`, composer catalog/render/pack/capture, and shared macro expansion.
- Produces: six GET/PUT routes from spec section 8. Required body model `PromptText(BaseModel)` has plain `text: StrictStr`. PUT returns the GET shape; no model calls or identity creation.
- Produces: `layout.enabled_ids(section_ids: tuple[str, ...]) -> frozenset[str]`, without importing assembly or constructing another template catalog. Missing IDs are enabled; only literal `False` disables; malformed entries and duplicates follow the existing migration/first-occurrence rules. A disabled custom layout enables every requested ID.
- Produces: stable catalog IDs `world_default_prompt`, `campaign_default_prompt`, `scene_focus`, in that order immediately after `global_system_prompt`, each `LOCK_IN`.
- Produces: optional `prompt_field_statuses` metadata, keyed by those IDs with field statuses from the API (including `unavailable` for the world). This contains no prose and is not a rendered section or token charge. Old captures missing it stay valid; empty/unbound states are not load-error warnings.
- Client methods: `getWorldDefaultPrompt(wid)`, `setWorldDefaultPrompt(wid, text)`, `getCampaignDefaultPrompt(cid)`, `setCampaignDefaultPrompt(cid, text)`, `getSceneFocus(cid, sid)`, `setSceneFocus(cid, sid, text)`.
- Client types: `PromptFieldStatus = "ok" | "empty" | "unreadable"`, `PromptField = {text: string; status: PromptFieldStatus}`, and `CampaignDefaultPrompt = PromptField & {world: {id: string | null; text: string; status: PromptFieldStatus | "unavailable"}}`. Optional status metadata also extends `SceneContext`.

- [ ] **Step 1: Write failing route, layout and composer tests.**

Route tests exercise GET, full-text PUT and clear for each scope; reject
missing/null/object/number `text`; assert 404 for unknown scopes, 409 and no
publication on a closed branch, and no world write through a campaign route.
`test_focus_put_during_generation_changes_no_transcript` proves editing focus
does not acquire a run-ownership guard or cancel an in-flight response.
Assert literal endpoints remain reachable before generic entity routes.

In `test_standing_prompt_context.py`, add:

- `test_unset_fields_preserve_play_prompt_bytes`: compare narrator, NPC and
  opener output with absent, empty and whitespace-only values, including an
  older saved layout. Use existing public fixtures/goldens, never today's
  code to regenerate the frozen fixture's `home/`.
- `test_each_enabled_field_renders_once_in_catalog_order`: world-only,
  campaign-only, focus-only and all-three combinations; clearing one leaves
  the others; identical authored sentences in both defaults are not deduped.
- `test_all_play_builders_use_addressed_scene_focus`: narrator, individual
  NPC, group round, director, offscreen, continue, generated and adapted
  openers. No lore match or changed cast/location removes these sections;
  another scene's focus never appears.
- `test_protected_fields_survive_tiny_positive_budget`: ordinary sections
  are shed first; complete framing/text survives over budget, and captured
  rows match the sent strings and their actual count.
- `test_random_macro_expansion_is_captured_once`: instrument the existing
  macro seam and verify render variants/capture reuse its result. Inspector
  reads of a frozen entry do not reroll it; separate fresh previews may.
- `test_scope_read_failure_warns_without_prompt_prose`: failed optional
  scope is omitted while other scopes render; warnings have zero sent tokens.
  A fatal scene read still fails instead of becoming a focus-only warning.

`test_enabled_ids_matches_layout_apply` compares the three IDs with
`layout.apply(SECTIONS)` under missing IDs, malformed files/entries, duplicate
IDs, relabels, literal false versus other false-like values, and layout off.
Disabling `world_overview` must not disable `world_default_prompt`.

Extend frozen reroll/Keep writing tests: edit and clear all three fields after
capture; replay retains the original strings and old captures gain no fields.
Fresh retry/roll-resume recomposition reads current values. Existing actor/POV,
secrecy and lore-activation assertions remain unchanged.

- [ ] **Step 2: Run the new route/composer tests red.**

Run the targeted backend command with
`TESTS="backend/tests/test_default_prompts_routes.py backend/tests/test_scene_focus_routes.py backend/tests/test_standing_prompt_context.py backend/tests/test_context_layout.py"`.
Expected: missing routes/catalog fields/layout helper, not unrelated setup
failures. Reuse `backend/tests/llm_fakes.py` for any route needing a provider.

- [ ] **Step 3: Implement routes and the common composition/capture path.**

Register the new router before `entities`. On focus PUT hold
`locks.campaign_lock(cid)` around `runs.require_scene_open(cid, sid)` and the
serialized store setter. Do not call `ensure_identity` or `scene_held_open`:
this is an authorial-field edit, not a transcript/run mutation. Translate
publication failures through existing route error conventions.

Resolve defaults once per fresh assembly and read only the addressed focus.
Thread text and statuses through prepared data, breakdown and captured variants;
do not look up current fields while replaying a frozen response. Keep status
metadata in prompt-log detail through its existing breakdown payload and in
live context responses, never as invented inspector section text.

Templates emit nothing, including separators/headings, for an empty field.
Frame campaign defaults as additive with explicit local exceptions over the
corresponding world fact. Frame focus with every soft-direction constraint in
spec section 5: opportunities, agency, refusal/diversion, no invented player
actions/knowledge, no rushing/repeated pursuit, and no pressure once addressed.
Render authored strings as data, not executable Jinja. Use existing macros
once in `_render_sections`; protect and account for the whole framed section.
Do not change other sections, secrecy decisions, actor contracts or packing
policy to make the new tests pass.

Implement `enabled_ids` with the existing migration and shared literal-false
interpretation, and parity-test its first-occurrence behavior; do not introduce
an assembly import into `layout` or a second section registry. Add API methods
using existing request/cache invalidation conventions. Editing-field GETs must
not join a read issued before the save; use the existing `fresh: true` request
option and scope/generation guards, not a new cache.

- [ ] **Step 4: Verify API/context, replay, template and offline checks green.**

Run this task's backend tests plus `test_actor_context.py`,
`test_actor_scoped_skip.py`, `test_lore_activation_context.py`,
`test_context.py`, `test_frozen_campaign.py`, and import/lock/atomic/pydantic
guards. Extend template verification with nonempty examples of all three fields
and an empty baseline, then run:

```powershell
make check-templates PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe
make test-py-fast PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe TESTS="backend/tests/test_evals.py backend/tests/test_response_snapshots.py backend/tests/test_response_extend.py backend/tests/test_context_compare.py backend/tests/test_prompt_log_store.py"
```

The offline eval includes complete gentle-focus framing and a synthetic
consolidation/accounting case: replacing repeated text can reduce input;
merely appending defaults increases it. Make no model-quality or automatic
savings claim. If an intentionally extended metadata payload requires a frozen
snapshot expectation update, inspect/update only `snapshot.json` deliberately,
never the fixture `home/` or baseline prompt bytes.

- [ ] **Step 5: Commit coherent passing route, composition and replay checkpoints.**

Editing APIs can be committed before prompt assembly once their own tests pass;
composition/capture and template/eval coverage can be separate checkpoints.
Suggested final message: `feat: compose protected standing prompts and scene focus`.

### Task 4: Same-Call Focus Generation and Saved Drafts

**Files:**

- Modify: `backend/src/grimoire/store/suggest.py`, `backend/src/grimoire/store/scene_ideas.py`, `backend/src/grimoire/routes/scenes.py`, `backend/src/grimoire/routes/models.py` (`SceneIdeaCreate`)
- Modify: `templates/scene_suggestions/system.j2`, `templates/scene_suggestions/instruction/standard.j2`, `templates/scene_suggestions/instruction/offscreen.j2`, `templates/scene_intent/system.j2`, `scripts/verify_templates.py`
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/components/sceneDraft.ts`, `frontend/src/components/useSceneSuggestions.ts`, `frontend/src/components/SceneIdeaPicker.tsx`
- Test: new `backend/tests/test_scene_focus_generation.py`; extend `backend/tests/test_suggest_store.py`, `backend/tests/test_suggest_golden.py`, `backend/tests/test_scene_ideas_store.py`, `backend/tests/test_scene_ideas_provenance.py`, `backend/tests/test_suggestion_controls_route.py`, `frontend/src/components/sceneDraft.test.ts`, `frontend/src/components/useSceneSuggestions.test.ts`, `frontend/src/components/SceneIdeaPicker.test.tsx`
- Update typed draft fixtures when adding the required base field: `frontend/src/components/SceneConfirmForm.test.tsx`, `frontend/src/components/SceneConfirmForm.datefill.test.tsx`. Keep existing assertions; new confirmation behavior belongs to Task 5.
- Modify only if their assertions intentionally change: `backend/tests/fixtures/suggest_golden.json`, existing public synthetic suggestion/intent eval cases/recordings under `evals/`

**Interfaces:**

- Consumes: `default_prompts.resolve`, `layout.enabled_ids`, the two existing default section templates, `context.macros.expand_macros`, and `scenes.focus.normalize`.
- Produces: `suggest.creation_defaults(cid: str) -> dict` with `sections: list[str]` (nonempty expanded world then campaign framing) and `statuses: dict`. Resolve/expand once before the generation call; never read previous-scene focus.
- Extend `build_prompt(snapshot, greeting_candidates=None, offscreen=False, direction="", controls=None, *, defaults: dict | None = None) -> list[dict]`. The route supplies its captured defaults; `None` means no supplied blocks because this builder has no campaign ID.
- Extend `build_intent_request(cid, typed, offscreen=False, *, defaults: dict | None = None) -> tuple[list[dict], dict]`. A supplied capture is reused; a direct caller with `None` resolves once. Existing `build_intent_prompt` delegates as before.
- Produce normalized string `focus` on suggestion cards and intent results. Add it as a string, allowing empty, to `_intent_shape`/`intent_schema`; it is required in the new requested schema but optional when parsing old/provider output.
- Extend `scene_ideas.add(..., *, drivers=None, time_anchor=None, focus="") -> str` and its save/read payload; `SceneIdeaCreate.focus: StrictStr = ""`. Existing positional parameters, idempotent standing-match semantics and provenance union are unchanged; a standing match retains the first saved focus rather than silently replacing authored metadata.
- Wire types `SceneSuggestion`, `SceneIntentResult`, `SceneIdea`, `SceneIdeaDraft` gain optional `focus?: string` for older payloads. `SceneDraft` gains required `focus: string` in its shared base, supplied by every converter.
- Extend the existing suggestion-result wrapper and `SceneIntentResult` with optional `prompt_field_statuses`, using Task 3's status shape. This is transient captured read status, not another saved-idea field or a new scene goal state.
- `useSceneSuggestions` retains and exposes `promptFieldStatuses` with the accepted suggestion batch through `SceneSuggestionsState`. Its existing `seq` guard protects statuses as well as cards. Reset on campaign/reference-scene/offscreen changes, clear when a failure removes cards, and replace on every accepted response (missing metadata becomes empty, including unranked responses). While old cards remain visible during regeneration, keep their captured statuses rather than attach a pending request's statuses. Ranking/date retention rules remain unchanged.
- Produce `normalizeSceneFocus(text: string) -> string` in `sceneDraft.ts`, matching every Python line boundary and the backend's edge-whitespace semantics. Do not blindly use JavaScript `trim()`, which strips a content BOM and differs on `\x1f`; test both explicitly. Use it for draft conversion and canonical focus submission. Extend `api.createScene(cid, title?, suggestedDate?, pcless?, focus = "")` without changing the old four arguments.

- [ ] **Step 1: Write failing generation, schema and round-trip tests.**

`test_suggestion_and_intent_generate_focus_in_same_call` uses the shared
`CapturingOpenRouter`/`FakeLLM` seams to assert one existing metadata call,
unchanged task/run/usage ownership, and a returned normalized focus. Cover
standard and offscreen suggestions and typed intent; focus is distinct from
the existing continuity-control `focus_refs`.

`test_missing_or_nonstring_focus_keeps_valid_draft` parameterizes missing,
null, number, array and object focus. The valid title/cast/location remains;
focus is empty, never stringified, with no focus-only repair call. Verify the
new intent schema requests a string and prompt/schema enums still agree.

`test_creation_defaults_follow_layout_without_scene_binding` asserts live
world/campaign values, explicit disables, duplicate/malformed layout parity,
omission when empty, warning metadata on failed reads, and no inherited prior
focus. Instrument random expansion to prove one captured result per call.
Creation has no actor/scene binding: use the existing macro helper with
`subs={}`, `sid=""`, `datetime_subs={}` so it cannot consult another scene or
invent an actor/date. Existing unresolved user/character macro behavior remains
literal; random/roll and unknown-macro handling use the existing engine.

`test_focus_survives_save_list_pick_create` checks normalized metadata through
idea JSON, API responses, frontend draft conversion and initial creation.
Old ideas read empty without rewriting the ledger; greeting-composed ideas
and verbatim greeting drafts infer no focus. Resaving the same standing idea
keeps the original saved focus and existing provenance union. A failed initial
scene write cannot leave a created scene missing requested focus.

Frontend converter tests assert generated/saved/custom focus survives, missing
and greeting focus become empty, and every recognized line boundary normalizes
identically to the backend, including edge-whitespace/BOM cases.
`SceneIdeaPicker.asDraft` sends focus when saving.

`test_suggestion_batch_keeps_its_captured_statuses` in
`useSceneSuggestions.test.ts` checks accepted metadata, regeneration with old
cards still displayed, replacement by a clean/legacy response, errors, and
campaign/reference-scene/offscreen resets. Deferred responses prove that stale
successes cannot replace either current cards or their statuses. Picker tests
assert a generated selection forwards its batch's warnings via `onPicked`;
saved/manual/greeting drafts do not inherit another batch's warnings. Typed
intent forwards its own captured warnings alongside any existing empty-result
notice without changing focus or adding a call.

- [ ] **Step 2: Run the new generation/draft tests red.**

Run the targeted backend command with
`TESTS="backend/tests/test_scene_focus_generation.py backend/tests/test_scene_ideas_store.py backend/tests/test_suggest_store.py"`.
From `frontend/`, run
`npx vitest run src/components/sceneDraft.test.ts src/components/useSceneSuggestions.test.ts src/components/SceneIdeaPicker.test.tsx`.
Expected: missing focus fields/default rendering or failed round trips.

- [ ] **Step 3: Extend existing builders, parsers and draft plumbing.**

Keep prompt instructions in Jinja. Ask suggestions for a gentle, shared-safe
purpose/opportunity with their other metadata; intent extracts a supported or
implied purpose and returns empty when unsupported/open-ended. Preserve the
original typed premise; focus is not a rewritten opener or a required outcome.

Call `creation_defaults` once at each route's existing generation boundary.
Pass the capture into its builder and completion payload, exposing optional
`prompt_field_statuses` outside model prose for creator warnings. Include the
already-rendered default blocks in the system prompt only when nonempty.
Do not import `context.assemble`, broaden snapshot driver reads, change lore
activation, or inject standing defaults into unrelated utility LLM calls.
Supply all new template variables in direct and StrictUndefined verification
renders, including `scene_intent/user.j2`'s included suggestion-user template.

Parse via the existing string-field check and shared focus normalizer. Thread
focus through `_suggestions_payload`, `_intent_payload`, saved-idea add/list,
API types, converter functions and save requests. Keep legacy field validation,
saved-idea provenance and enum fallback behavior intact. Blank/greeting paths
still need no model call, and failed/unconfigured intent generation still
offers manual creation; there is no retroactive focus inference or backfill.

Retain response statuses in `useSceneSuggestions` under the same request guard
as the accepted cards, exposing them in its returned state. `SceneIdeaPicker`
renders the current batch's warnings and forwards a generated selection's
captured warnings through its existing `onPicked(draft, warning?)` contract;
typed intent uses the metadata from its own response. Do not persist warnings
in the draft/idea or reuse them for unrelated saved/manual/greeting selections.

- [ ] **Step 4: Verify generation, provenance and template checks green.**

Run this task's backend tests, `test_suggest_controls.py`,
`test_scene_focus_routes.py`, and the import/pydantic/routing guards; rerun
`make check-templates PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe`.
Run the three frontend test files and `npm run typecheck` from `frontend/`.
Review deliberate suggestion golden/schema changes; old output recordings
without focus remain accepted and do not require live re-recording. Creation
prompts intentionally request a new field; play's unset-field byte baseline
from Task 3 still holds.

- [ ] **Step 5: Commit passing generator and saved-draft substeps.**

Commit template/schema/parser work when its tests pass, then saved-idea/client
round-trip work when green. Suggested final message:
`feat: generate and preserve reviewable scene focus with scene drafts`.

### Task 5: Scoped Editors and Reviewed Scene Creation

**Files:**

- Create: `frontend/src/components/DefaultPromptEditor.tsx`, `frontend/src/components/SceneFocusEditor.tsx`
- Modify: `frontend/src/components/WorldOverview.tsx`, `frontend/src/routes/CampaignHub.tsx`, `frontend/src/components/SceneInspector.tsx`, `frontend/src/components/SceneConfirmForm.tsx`, `frontend/src/components/ContextBreakdown.tsx`, `frontend/src/components/NewSceneChooser.tsx`, `frontend/src/components/SceneIdeaPicker.tsx`, `frontend/src/index.css`
- Test: new `frontend/src/components/DefaultPromptEditor.test.tsx`, `frontend/src/components/SceneFocusEditor.test.tsx`; extend the colocated tests for the seven existing components/routes above, plus `SceneConfirmForm.datefill.test.tsx`

**Interfaces:**

- Consumes: Tasks 3–4 API methods/types, `api.getPromptLayout()`, normalized `SceneDraft.focus`, the existing confirmation write/salvage sequence, and context status metadata.
- Produces: `DefaultPromptEditor({scope})` with `scope: {kind: "world" | "campaign"; id: string}`. Campaign mode also shows the resolved read-only world preview and world-overview link; it never calls the world setter.
- Produces: `SceneFocusEditor({cid, sid, onSaved?})` with optional `onSaved: () => void` to refresh relevant scene/context reads. Focus is saved as plain text, while defaults use existing Markdown display.
- `SceneConfirmForm` owns editable focus initialized from the current draft and sends that reviewed value in the initial `api.createScene` request before opener/cast/location setup. Focus is independent of `premise` and `firstPost`; no follow-up focus PUT or server inference.
- Effective layout warnings refer to the three stable IDs, link to the existing Settings (`/config`) layout editor, and offer no second on/off control. Failed layout reads say inclusion status is unknown.

- [ ] **Step 1: Write failing editor, warning and confirmation tests.**

In the new editor tests cover view -> edit -> save/cancel, unset hints,
multiline defaults versus paragraph focus, returned canonical text, failure
messages, failed-save draft retention, and closed-branch refusal. Campaign
tests assert live inherited preview/link and own-field-only save; failed field
reads are not editable empty drafts.

`test_late_read_and_save_do_not_cross_scope` uses deferred promises: navigate
from one world/campaign/scene to another before resolving the first read or
save; the new scope never displays, saves or refreshes the first scope's value.
An in-flight same-scope refresh must not replace an active edit either.
`test_disabled_and_unknown_layout_are_not_inclusion_promises` covers explicit
disables, layout off, absent new IDs in older data and failed layout reads.

Extend confirmation tests with
`test_cleared_focus_stays_empty_after_other_edits_and_no_opener`: start from a
generated focus, clear it, change title/date/cast/location and choose no opener;
the POST sends `focus: ""` and makes no focus-inference/edit request. Also test
editing the generated paragraph, saved-idea focus, typed-intent focus, manual
fallback after unavailable/failed intent generation, and blank/greeting drafts
with manually entered focus. Cancel before creation writes nothing; an
explicitly saved idea is not removed by cancelling confirmation.

Assert first-publication focus survives opener success/failure/salvage and
the greeting-adaptation path; an opener never writes focus. Keep the existing
busy/dismissal, date-fill and newly-picked-draft remount tests intact.

`test_focus_input_accepts_spaces_and_normalizes_pasted_boundaries` tests
ordinary typing, every supported hard line boundary, soft wrapping, and
canonical submission. Do not trim a just-typed trailing space on every change
and make multiword typing impossible; edge trimming belongs at canonical
submission, while hard breaks are converted to spaces in the paragraph input.

`ContextBreakdown` tests show actual rendered rows and scope-specific unreadable
warnings with no extra token cost, synthetic sent row or savings badge. Frozen
views use captured metadata, not fresh field lookups; old views remain valid.

`test_generated_draft_confirmation_keeps_captured_warnings` follows the real
hook -> picker -> chooser -> confirmation path, including after Back. A
replacement generated batch without metadata clears its earlier warning;
deferred old-scope responses cannot change the current confirmation notice.
Cover both suggestion selection and typed intent, with the existing
empty-intent/fallback notices retained rather than overwritten.

- [ ] **Step 2: Run focused frontend tests red.**

From `frontend/`:

```powershell
npx vitest run src/components/DefaultPromptEditor.test.tsx src/components/SceneFocusEditor.test.tsx src/components/SceneConfirmForm.test.tsx src/components/ContextBreakdown.test.tsx
```

Expected: missing components or missing focus/warning behavior. Update existing
API mocks only with the newly consumed public methods and public placeholder
data; do not add production-only helpers to make a test pass.

- [ ] **Step 3: Implement scope-safe editing and confirmation.**

Place world editing beside About in `WorldOverview`, campaign editing in the
hub's Settings panels, and focus beside Author's notes in `SceneInspector`,
available before an opener and during play. Reuse surrounding panel/editor
conventions. Show explicit loading, unset, unreadable and saving/error states;
only a successful read can initialize an editable field. Retry failed loads,
retain failed save drafts, and reset editing state on a real scope change.

Key async read/save state by scope and guard completion with the existing
request-generation/ref pattern. Revalidate relevant field, inherited preview,
scene metadata and live-context reads after a successful save through existing
client conventions. Never use a remembered world preview as a prompt input or
overwrite the displayed scope with a stale callback.

Add a visibly single-paragraph, soft-wrapping focus input and paste/line-break
normalization, with the shared normalizer used for canonical submission.
Initialize only when a new confirmation draft mounts; other field changes,
date-fill responses, choosing no opener, or opener completion must not
regenerate/reset focus. Preserve the chooser's existing `draftGen` remount
behavior rather than adding an effect that repeatedly restores generated text.
Pass the final focus into the initial create call, retaining existing partial
creation/salvage and no-dismiss-while-writing contracts.

Display creation-time default read warnings from captured status metadata in
the chooser/picker/confirmation flow through its existing notice handoff,
without persisting warning text as focus or adding a model call. In the context
panel render warnings outside section rows; absence of empty sections is not
an error. Old response metadata is optional, not a reason to recompose history.

Help text explains shared actor visibility, compact standing context, live
world inheritance and no automatic deduplication of existing copies. Focus
help explains gentle direction, not character knowledge or a guaranteed
outcome, and manual revision/clear. Include disabled-layout warnings/link and
unknown-status handling in each editor, with no competing per-record switch.

- [ ] **Step 4: Run editor, integration and type checks green.**

From `frontend/` run:

```powershell
npx vitest run src/components/DefaultPromptEditor.test.tsx src/components/SceneFocusEditor.test.tsx src/components/WorldOverview.test.tsx src/routes/CampaignHub.test.tsx src/components/SceneInspector.test.tsx src/components/SceneConfirmForm.test.tsx src/components/SceneConfirmForm.datefill.test.tsx src/components/SceneIdeaPicker.test.tsx src/components/useSceneSuggestions.test.ts src/components/NewSceneChooser.test.tsx src/components/ContextBreakdown.test.tsx src/components/sceneDraft.test.ts
npm run typecheck
```

Expected: targeted assertions and TypeScript pass, including unrelated existing
creation/editor contracts. Proceed to the final verification checklist below;
passing this subset alone is not the repository gate.

- [ ] **Step 5: Commit passing editors and confirmation checkpoints separately.**

Defaults editors and their placement can be committed when green, without
waiting for focus confirmation/context warnings. Commit each later coherent
passing substep and its tests. Suggested final checkpoint message:
`feat: edit standing prompts and review scene focus before creation`.

## Acceptance Ownership

The originating spec remains the acceptance authority. This map prevents a
downstream implementer from treating the store/UI tests as the whole feature.

| Spec section 10 case | Owning task/check |
|---|---|
| 1: unset play bytes and old layout | Task 3 baseline and layout parity |
| 2: independent sections/clear/no dedup | Tasks 1–3 resolver and composition matrix |
| 3: every play builder and addressed focus | Task 3 builder matrix; Task 2 ownership/copies |
| 4: live inheritance/scope safety | Tasks 1 and 3 resolver/route/composer tests |
| 5: protected packing and exact accounting | Task 3 tiny-budget/capture tests and offline accounting eval |
| 6: frozen versus fresh/macros | Task 3 replay/recomposition/variant tests |
| 7: failures and store guards | Tasks 1–3 publication, warning and architecture tests |
| 8: string APIs/scalar safety/unchanged transcript | Tasks 2–3 normalization, creation and editing route tests |
| 9: editors/clear/navigation | Task 5 component tests and isolated verification |
| 10: copy/bundle/frozen compatibility | Tasks 1–3 lifecycle and frozen-fixture checks |
| 11: focus lifecycle/no identity/malformed scene | Task 2 lifecycle, concurrency and strict-write tests |
| 12: whole soft-direction framing/no secrecy changes | Task 3 templates/eval plus actor/lore regression tests |
| 13: same-call generation/schema/defaults | Task 4 builder/parser/schema/usage tests |
| 14: saved round trip/initial write/manual fallback | Tasks 2, 4 and 5 store/API/converter/confirmation tests |

## Final Implementation Verification and Handoff

These checks are for the later authorised implementation, not evidence that
unwritten code already passes. Follow review stages from `CLAUDE.md`; do not
substitute this checklist for them.

- [ ] Run the repository gate from the worktree, using the main checkout's
  explicit interpreter rather than the worktree's default missing virtualenv:

  ```powershell
  make check PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe
  ```

  Record actual results and fix failures at their cause. For deliberately
  reduced ratcheted lint findings, follow the baseline-update rules in
  `CONTRIBUTING.md`; do not add exemptions or refresh unrelated baselines.

- [x] Read and use the repository's `verify` skill at verification time. Drive
  an isolated public-placeholder store with its mocked provider: world/campaign
  defaults; generated, saved and manual focus; review/edit/clear; no-opener
  creation; live play/context rows; layout disable; reload and frozen replay.
  Inspect the scene file's scalar and transcript. Use the browser skill when
  operating the in-app browser. Do not use the real library or live model calls.

- [ ] Confirm the immutable frozen fixture `home/` is unchanged, unset play
  prompts retain their baseline bytes, and no private names/data reached docs,
  fixtures, screenshots or commits. Inspect the complete diff against the spec
  acceptance map, not only each task's newly passing tests.

- [x] Resolve the mandatory implementation review findings and final spec/diff
  review before declaring completion. Commit coherent passing fixes as they
  land; do not postpone all fixes into a final slice-sized commit.

- [ ] Report tested behavior, actual gate/review results, remaining limitations
  and branch/worktree location. Do not merge, push or squash without authority;
  frequent checkpoints are intentionally available for the owner's later squash.

The remaining unchecked runtime/compatibility items are delegated to PR CI by
the owner's latest instruction; they are not silently treated as passing.

### Implementation theory note

Theory: Defaults are additive scope-owned writer inputs, resolved live; focus is authored scene metadata, not a guaranteed event or completion state.
Instead of: Materializing world copies or overriding whole campaign text, which would lose live inheritance and explicit exceptions.
Reused: Atomic publication, campaign locks, strict frontmatter, the protected composer, existing generation calls and fresh request transport.
New concept: A shared authorial-field edit session and a local composition revision; neither owns the transcript or the running reply.
Assumes: Authors keep these shared inputs appropriate for every writer; no test can guarantee a provider follows gentle guidance.
Cost: Optional-field reads, protected prompt tokens and a fresh preview GET after publication; creation adds no model call.
Watch: Do not reintroduce transcript reloads or stale shared GETs on focus save; frozen responses must retain their captured inputs.

## Plan Handoff

The owner approved this plan and its recommended **Native** execution with
“Go for it” on 2026-10-11. Execute these five tightly dependent tasks inline,
following their test and frequent-commit checkpoints. The mandatory plan
review approved the amended plan with no material findings. The baseline
prerequisite is now addressed and the owner resumed feature implementation.
Continue all five tasks inline with passing checkpoints and the approved final
local review loop; no integration into main is authorized.
