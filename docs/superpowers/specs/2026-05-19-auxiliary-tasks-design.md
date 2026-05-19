## Auxiliary Tasks — Design

> **Status:** Design ready for implementation plan. Hard deps on `extraction-modes-design.md` (`ExtractionMode.NONE` short-circuit) and `swipes-alternates-design.md` (`rewrite_post` accept → new alternate via `switch_primary_alternate`). Soft dep on `scene-hud-design.md` (in-flight indicator).

**Source idea:** `specs/new/auxiliary-tasks.md`
**Module:** `backend/src/grimoire/auxiliary/` (new), additions to `orchestrator/`, `context/`, `llm_gateway/`

## Purpose

Non-canonical model calls — drafts, rewrites, brainstorms — that **never** silently mutate canonical state. Every model call carries an explicit task kind that drives prompt assembly, suppression rules, and post-response handling.

The defining property: no silent state mutation. Scene files, State Store, Time Engine, drift, mechanics, library, continuity are untouched until the user explicitly accepts an auxiliary output, at which point the data is routed through the canonical path (which for `rewrite_post` means the swipes-alternates `switch_primary_alternate` primitive).

## Task taxonomy

```python
class TaskKind(StrEnum):
    IMPERSONATE_PC      = "impersonate_pc"      # draft active PC's next post
    REWRITE_POST        = "rewrite_post"        # regenerate a previous post with edit instruction
    CONTINUE_AS         = "continue_as"         # extend a specific character's last action / monologue
    WHAT_WOULD_X_SAY    = "what_would_x_say"    # one-off line of dialogue, no scene mutation
    BRAINSTORM          = "brainstorm"          # free idea generation
    EDIT_PROSE          = "edit_prose"          # polish a draft / response
    TRANSLATE           = "translate"           # transform between languages / dialects / registers


@dataclass
class AuxiliaryTask:
    kind: TaskKind
    target_character_ref: str | None = None
    target_post_id: str | None = None
    edit_instruction: str | None = None
    snippet: str | None = None
    steering_hint: str | None = None
    target_language: str | None = None          # translate
    extra_params: dict = field(default_factory=dict)
```

`AuxiliaryTask.lives_in_types/auxiliary.py` (new file).

## Suppression invariants (every task kind)

A canonical-turn checklist; the runtime enforces every line:

- Together-mode tracker block in prompt: NO.
- Tool-use tool declarations: NO.
- `Mechanics.should_roll` called: NO.
- Drift-correction injection in prompt: NO.
- Drift score increments on response: NO.
- Extractor invoked post-response: NO.
- Scene file mutated: NO (only on accept).
- State Store mutated: NO (only on accept).
- Time advanced: NO.
- Library / continuity / facts / commitments / relationships written: NO without explicit accept.

Cancellation or error mid-stream leaves nothing changed; the user can re-run without drift.

**Enforcement spans three modules.** The Context Builder enforces tracker / tools / drift-correction injection (the prompt-side suppressions); the Orchestrator enforces the mechanics / drift / extractor / state-store suppressions (skip the relevant code paths). Both branch on `auxiliary_task is not None` (or specifically `extractor_mode == ExtractionMode.NONE` for the prompt-side checks, which is set by `select_mode` when an auxiliary task is present per `extraction-modes-design.md`).

## Per-task context budget

The Context Builder branches on `auxiliary_task.kind`. The matrix (concrete budgets in `auxiliary/budgets.py`):

| Task | System prompt | Active-PC card | Scene header | Voice anchor | Recent posts |
|---|---|---|---|---|---|
| `impersonate_pc` | impersonate template | full (target PC) | full | active PC + present cast voices | last 6 |
| `rewrite_post` | rewrite template | snapshot at target turn | snapshot at target | original speakers' anchors | last 4 around target post |
| `continue_as` | continue template | scene PC | full | target NPC | last 3 |
| `what_would_x_say` | minimal | none | minimal (location only) | target NPC | last 1–2 referencing context |
| `brainstorm` | minimal | none | none | none | none |
| `edit_prose` | minimal | none | none | none | none |
| `translate` | minimal | none | none | none | none |

Style guide and content boundaries included for everything except `translate`. Mechanics results, archive tier, background tier (except spotlight handed forward into prompt for `impersonate_pc` / `rewrite_post` / `continue_as`) are all dropped.

Voice anchor resolution per task (the open question):
- `impersonate_pc`: active PC's voice + light voice cues for present NPCs (so the draft matches the room).
- `rewrite_post`: the **original speakers** of the target post are extracted (the scene tracks per-post `author_kind` + `author_ref`); their voice anchors are loaded. Plus the active PC if they're a speaker.
- `continue_as`: the `target_character_ref` only.
- `what_would_x_say`: the `target_character_ref` only, minimal sample set.
- Others: none.

Computation lives in `auxiliary/budgets.resolve_voice_targets(task, scene)`. Returns a `list[CharacterRef]` consumed by the Context Builder's voice-loading path.

## Orchestrator entry point

```python
async def run_auxiliary_task(
    self,
    campaign_id: str,
    task: AuxiliaryTask,
    *,
    on_token: Callable[[str], Awaitable[None]] | None = None,
    branch_id: str | None = None,
) -> AuxiliaryResult: ...


@dataclass
class AuxiliaryResult:
    id: str                                       # ar_<uuid>; lives in memory until accept/discard
    task: AuxiliaryTask
    text: str
    completed_at: datetime
    model_used: str
    tokens: int
    pending_commit_action: CommitAction
```

`CommitAction` is a structured enum:

```python
class CommitAction(StrEnum):
    SUBMIT_POST     = "submit_post"      # impersonate_pc → user accepts → canonical turn pipeline
    REPLACE_POST    = "replace_post"     # rewrite_post  → swap_delta_set + switch_primary
    APPEND_POST     = "append_post"      # continue_as   → append a new NPC-authored post
    COPY            = "copy"             # what_would_x_say / brainstorm / edit_prose / translate
    REPLACE_DRAFT   = "replace_draft"    # edit_prose / translate on composer draft
```

The `CommitAction` is determined server-side from `task.kind`; the frontend dispatches the corresponding accept-endpoint. The "Edit" action on a draft is just an inline text edit before submit — no separate API; the user types in the composer, then submits via the canonical submit path.

## Per-task model routing

```yaml
model_routing:
  canonical_turn: claude-opus-4-7
  extractor: claude-haiku-4-5
  auxiliary:
    impersonate_pc:   claude-opus-4-7        # voice quality matters
    rewrite_post:     claude-opus-4-7
    continue_as:      claude-opus-4-7
    what_would_x_say: claude-sonnet-4-6
    brainstorm:       claude-sonnet-4-6
    edit_prose:       claude-sonnet-4-6
    translate:        claude-haiku-4-5
```

LLM Gateway routing (`backend/src/grimoire/llm_gateway/routing.py:RouteResolver`) gains lookup for keys of the form `auxiliary.<task_kind>`. The Orchestrator passes `task=f"auxiliary.{task.kind.value}"` when calling `resolve(...)`. Fallback chain: if the per-task model is unavailable, fall back to `canonical_turn` with a warning attached to the result (`AuxiliaryResult.warnings: list[str]`).

## REST surface

```
POST   /campaigns/{id}/auxiliary/impersonate-pc       body: {steering_hint?}
POST   /campaigns/{id}/auxiliary/rewrite-post         body: {post_id, edit_instruction}
POST   /campaigns/{id}/auxiliary/continue-as          body: {character_ref, target_post_id, steering_hint?}
POST   /campaigns/{id}/auxiliary/what-would-x-say     body: {character_ref, snippet}
POST   /campaigns/{id}/auxiliary/brainstorm           body: {prompt}
POST   /campaigns/{id}/auxiliary/edit-prose           body: {snippet, edit_instruction}
POST   /campaigns/{id}/auxiliary/translate            body: {snippet, target_language}

POST   /campaigns/{id}/auxiliary/{result_id}/accept   body: {edited_text?}
POST   /campaigns/{id}/auxiliary/{result_id}/discard

GET    /campaigns/{id}/auxiliary/in-flight             # list streaming aux tasks
```

The streaming POST returns the `result_id` immediately and streams tokens via WebSocket. Token stream uses a new WS event distinct from canonical-turn streaming so the frontend can route to the auxiliary panel:

```json
{ "type": "aux_token",    "result_id": "ar_...", "delta": "..." }
{ "type": "aux_complete", "result_id": "ar_...", "tokens": 512, "model": "claude-opus-4-7" }
{ "type": "aux_error",    "result_id": "ar_...", "error": "..." }
```

In-flight results live in `Orchestrator._inflight_aux: dict[result_id, AuxiliaryResult]` in memory (rebuilt empty on restart). On `accept`/`discard`/`server-restart-during-flight`, the entry is removed. There is **no SQLite persistence** for in-flight aux results — they're transient. Once accepted, the result is committed via the canonical path and lives in the audit log; once discarded, the result is gone.

## Accept dispatch

The accept endpoint reads `task.kind` and routes:

- `impersonate_pc` → submit the (optionally edited) text as a normal user-authored PC turn. Calls the existing `submit_post` orchestrator path. Canonical Extractor runs.
- `rewrite_post` → builds a new `Alternate` for the target post using the text and a fresh extraction, then `switch_primary_alternate(post_id, new_alt_id)`. Per `swipes-alternates`, the swap is atomic.
- `continue_as` → append a new NPC-authored post to the scene (canonical append path; Extractor runs).
- `what_would_x_say` / `brainstorm` / `edit_prose` / `translate` → no scene mutation. Default action is "copy to clipboard"; the frontend handles that. For `edit_prose` + `translate` when invoked from the composer, the accept dispatch substitutes the composer draft (no API needed beyond returning the text).

Each accept emits an audit line:
```
[aux-accept] task=rewrite_post campaign=... post=p_4710 alt=a_9012 cascaded_replace=true model=opus
[aux-accept] task=impersonate_pc campaign=... pc=pc_florence accepted_edits=true
```

For `rewrite_post`, the audit also emits the swipes `[switch]` line — both are kept (one for the auxiliary, one for the primary switch); the open question is resolved as **both emitted**, so observability can attribute cause and effect.

## Concurrent tasks

Multiple auxiliary tasks may run simultaneously. Each has its own `result_id` and WS events demuxed by that id. The frontend renders each in its own panel; there's no server-side queueing.

Concurrent canonical turn + auxiliary task: allowed. The auxiliary task **must not** see partial canonical state (the auxiliary uses the scene's current primaries; if the canonical turn is mid-stream, its post hasn't yet been appended, so the auxiliary sees the pre-turn scene — correct behavior).

## Suppression at the orchestrator

`run_auxiliary_task` is a fork of the canonical turn loop with these branches:

1. **Context build:** `mode=ExtractionMode.NONE`, `auxiliary_task=task`. Context Builder applies suppression matrix internally.
2. **Per-task system prompt:** loaded from `auxiliary/prompts/<kind>.j2` (Jinja templates).
3. **Model routing:** `route = llm_gateway.resolve(f"auxiliary.{kind}")`.
4. **No pre-roll, no mechanics, no drift-corrective injection, no contextual rolls** — skipped explicitly.
5. **Stream LLM response:** straight pass-through to `on_token`; no Together tracker buffering (`select_mode` returned NONE).
6. **Post-response:** no Extractor call. The full text is captured into `AuxiliaryResult.text`; `result_id` is registered in `_inflight_aux`.
7. **No state write, no event emission to canonical observers.** Only `aux_*` WS events.

The fork is intentional — sharing the canonical loop with conditionals would be brittle. The new module `orchestrator/auxiliary_runner.py` holds the auxiliary loop; it imports the canonical helpers (context builder caller, LLM gateway caller) but not the canonical turn shape.

## Frontend

Distinct visual treatment:
- Auxiliary result panels render with dotted border + muted background + label badge ("Draft" / "Suggestion" / "Rewrite preview" / "Brainstorm" / etc.).
- Action bar per panel: Accept / Discard / Try again / Edit / Copy.
- Try again invokes the same endpoint with `result_id_to_replace` so the panel is replaced rather than stacked (configurable — user can opt for stacking).

Entry points:

| Task | Trigger |
|---|---|
| `impersonate_pc` | "Suggest a post" button in composer; keyboard `?i` |
| `rewrite_post` | Per-post menu → "Regenerate with edit..." (inline, opens edit_instruction modal) |
| `continue_as` | Per-post action → "Continue this thought" on a character's post |
| `what_would_x_say` | Right-click a present-cast chip → "Ask what they'd say..." |
| `brainstorm` | Sidebar Brainstorm panel; keyboard `?b` |
| `edit_prose` | Per-post menu → "Edit the prose"; composer menu → "Polish" |
| `translate` | Selected-text menu → "Translate / restate as..." |

In-flight indicator: dedicated icon + color in the HUD status area (the HUD spec exposes a hook; in-flight aux count is read via `GET /auxiliary/in-flight`). Click expands to a panel listing in-flight tasks with cancel controls.

## Configuration

```yaml
auxiliary:
  enabled: true
  default_fallback_model: claude-opus-4-7        # when per-task model unavailable
  in_flight_warning_threshold: 5                 # warn user if 5+ aux tasks open
  per_task:
    brainstorm:
      max_tokens: 1500
    rewrite_post:
      max_tokens: 4000
    translate:
      max_tokens: 2000
```

## Cross-spec hooks

- **`extraction-modes`**: `select_mode(... aux_task=task)` returns `ExtractionMode.NONE`; Context Builder suppression cascade is shared.
- **`swipes-alternates`**: `rewrite_post.accept` calls `regenerate_post`-like path (create alternate from the auxiliary's text + freshly-extracted deltas), then `switch_primary_alternate`. The auxiliary spec wires through; the swipes spec owns the primitive.
- **`scene-hud`**: in-flight indicator + cancel control. The HUD reads `/auxiliary/in-flight` for badge counts.
- **`characters`**: drift detection ignores auxiliary tasks (the Orchestrator's drift hook isn't called). Voice anchor loading per task is handled by `auxiliary/budgets.resolve_voice_targets`.
- **`mechanics`**: never sees auxiliary calls (`Mechanics.should_roll` is gated by the canonical turn loop, which the auxiliary runner doesn't enter).
- **`observability`**: separate audit category. The auxiliary loop emits `[aux] task=... result_id=... ...` lines; the canonical pipeline emits `[aux-accept]` lines on commit.

## Audit log examples

```
[aux] task=brainstorm campaign=... model=sonnet tokens=240 accepted=false
[aux] task=impersonate_pc campaign=... pc=pc_florence model=opus tokens=512 accepted=true result=ar_a1b2
[aux-accept] task=impersonate_pc campaign=... result=ar_a1b2 submitted_as=p_4711
[aux] task=rewrite_post campaign=... post=p_4710 model=opus tokens=620 accepted=true result=ar_c3d4
[aux-accept] task=rewrite_post campaign=... result=ar_c3d4 cascaded_replace=true alt=a_9012
[switch] campaign=... post=p_4710 from=a_9011 to=a_9012 (rewind 14 / apply 17)
```

## Failure handling

| Failure | Behavior |
|---|---|
| Mid-stream error | Discard partial; `aux_error` WS event; no state changed |
| Accepted rewrite_post → extractor fails on new text | Roll back the in-flight alternate (rewind its delta set); original primary intact; user notified; result stays in panel for retry |
| Aux model unavailable | Fall back to canonical-turn model; result includes `warnings: ["fallback_to_canonical_model"]` |
| Content boundary violation | Caught before user sees output (same path as canonical) |
| Concurrent aux tasks beyond threshold | UI shows warning; no hard cap |
| Restart with in-flight aux | All `_inflight_aux` cleared; clients see WS reconnect, panels close |
| User clicks Accept twice rapidly | Second call gets `409 AUX_ALREADY_COMMITTED` |

## Test wiring

`backend/tests/auxiliary/test_runner.py` (new):
- Each TaskKind runs against a fixture campaign; assert no canonical state mutation occurred (scene file untouched, state store unchanged).
- Cancellation mid-stream cleans up `_inflight_aux`.
- Concurrent runs demuxed by `result_id`.

`backend/tests/auxiliary/test_budget.py`:
- Per-task voice-target resolution returns the expected character refs.
- Per-task budget exclusions (no archive tier, no mechanics, etc.).

`backend/tests/auxiliary/test_accept_dispatch.py`:
- `impersonate_pc.accept` → canonical submit; canonical extractor runs.
- `rewrite_post.accept` → new alternate appears in scene sidecar; primary switched; original alternate preserved; rewind+apply transactional.
- `continue_as.accept` → new post appended.
- copy-type tasks have no state change on accept.

`backend/tests/context/test_auxiliary_suppression.py`:
- Mechanics tools omitted.
- Tracker instructions omitted.
- Drift-correction injection absent.
- Per-task voice anchors loaded.

## Wiring touchpoints

- `backend/src/grimoire/auxiliary/types.py` (new): TaskKind, AuxiliaryTask, AuxiliaryResult, CommitAction.
- `backend/src/grimoire/auxiliary/budgets.py` (new): per-task suppression + voice-target resolution.
- `backend/src/grimoire/auxiliary/prompts/*.j2` (new): per-task system prompts (Jinja).
- `backend/src/grimoire/orchestrator/auxiliary_runner.py` (new): the auxiliary loop.
- `backend/src/grimoire/orchestrator/service.py`: add `run_auxiliary_task` + accept-dispatch methods (`accept_auxiliary`, `discard_auxiliary`).
- `backend/src/grimoire/context/builder.py`: branch on `auxiliary_task` for budget + suppression.
- `backend/src/grimoire/llm_gateway/routing.py`: per-task route keys.
- `backend/src/grimoire/api/auxiliary.py` (new): 7 + 2 + 1 = 10 REST routes.
- `backend/src/grimoire/api/stream.py`: emit `aux_token` / `aux_complete` / `aux_error`.
- `frontend/src/routes/campaign/Auxiliary/` (new): panels, accept/discard UI, entry-point affordances.
- `frontend/src/api/auxiliary.ts` (new): client.

## Out of scope (v1)

- Persisted auxiliary history (results live in memory only).
- Aux-on-aux composition (you can't auxiliary-rewrite an auxiliary result before accepting).
- Cross-campaign aux (each result is bound to one campaign).
- Streaming partial auto-extraction (extractor explicitly never runs on auxiliary output).
