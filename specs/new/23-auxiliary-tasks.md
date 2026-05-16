# 23 — Auxiliary Tasks (Impersonation, Rewrite, Brainstorm)

## Purpose

A normal turn produces a canonical, state-mutating response: the model continues the scene as the GM, the Extractor runs, deltas apply, the scene file gets a new post, time advances. That's the main loop.

But the user often needs the model for **non-canonical** generation: write a draft of my next post in my PC's voice, regenerate a previous post with edits, finish this NPC's monologue, suggest a setting detail, polish the prose I drafted, translate this line of dialect. These calls share a common shape:

- **No state changes** (no fact recorded, no transient state updated, no time advanced, no commitment opened)
- **No mechanics rolls** (the dice are not summoned to help me brainstorm)
- **No tracker injection** (don't ask the model for a Together-mode block when you're impersonating)
- **Different system prompt** (you're not the GM right now; you're the player's pen)
- **Often different context window** (impersonation needs the PC's voice anchor and recent posts; brainstorm needs almost nothing)

SillyTavern RPG-companion implements one form of this: detecting "ephemeral instruct prompts" (impersonation, guided generation) and suppressing tracker injection. Grimoire generalizes it: every model call carries an explicit task kind, and that kind drives the prompt assembly, suppression rules, and post-response handling.

This spec defines the auxiliary task taxonomy, the Context Builder flag, the suppression matrix, the UI affordances, and the safety boundary that auxiliary tasks never silently mutate state.

## Responsibilities

- Define a taxonomy of auxiliary task kinds and their semantics
- Provide the Context Builder with a `auxiliary_task` parameter and the per-task suppression rules
- Route auxiliary results through a non-canonical path: drafts, suggestions, diffs — never silent commits
- Suppress Extractor invocation, Together-mode tracker injection, Tool-use tool declarations, mechanics roll injection, and drift-correction banners for auxiliary turns
- Per-task model routing (auxiliary tasks may use a different model than canonical turns)
- Frontend affordances: clearly distinguish auxiliary output from canonical output; require explicit user action to commit any of it

## Non-responsibilities

- Does not define new LLM provider semantics (uses normal completion / streaming APIs)
- Does not bypass safety / content policy (auxiliary tasks honor the same guards as canonical turns)
- Does not perform state mutations (any commit happens through the canonical path with the user's explicit accept)

## Task taxonomy

Seven task kinds covering the practical use cases:

### 1. `impersonate_pc`

**Goal**: write a draft of the active PC's next post in their voice. The user gets a starting point they can edit before submitting as their canonical turn.

**Input**: optional steering hint ("she's been awake too long; show fatigue")

**Output rendering**: draft text pre-populated in the post composer; user edits and submits to commit

**System prompt** (sketch):

```
You are writing as {PC name}, a character in an ongoing story. Continue
their next action / dialogue in their voice. One paragraph, present
tense. Do not narrate the GM's response. Stop when you've completed
{PC name}'s action.

PC voice anchor:
<voice anchor>

Recent posts (last 6):
<recent posts>

User steering hint: {hint or "none"}
```

**Context budget**: lock-in (PC card, scene header) + spotlight (voice anchor, last 6 posts). Skip background, archive, and continuity.

### 2. `rewrite_post`

**Goal**: regenerate a previously-canonical post with a new instruction ("make Alistair angrier", "tighten the prose", "remove the reference to the locket")

**Input**: target post id + edit instruction

**Output rendering**: side-by-side diff against the original post; user accepts to replace (which runs Extractor on the new content), discards, or generates another alternative

**System prompt** (sketch):

```
You are revising a previous narrative response. Apply this instruction:
"{edit instruction}"

Original response:
<original post text>

Context at the time of the original response:
<the same context that was used originally, if cached>

Produce only the revised response. Maintain narrative voice and continuity
with surrounding posts.
```

**Context budget**: ideally re-uses the original context cache; otherwise minimal context (scene header + adjacent posts). The Extractor runs on accepted rewrites with original-post-replace semantics (`16-observability.md` audit records the rewrite).

### 3. `continue_as`

**Goal**: extend a specific character's last monologue, action, or thought.

**Input**: target character ref + optional steering ("she keeps talking about her sister"), optional starting fragment

**Output rendering**: appended text below the character's last post, marked as auxiliary; user accepts to commit (becomes a normal post by that character).

**System prompt**: voice anchor for the target character + their last 3 turns of dialogue + the scene state.

**Use case**: when an NPC's response was clipped and the user wants the model to finish the thought without re-rolling the whole scene.

### 4. `what_would_x_say`

**Goal**: one-off line of dialogue from a named character, on a topic, with no commitment to the canonical scene.

**Input**: target character ref + prompt topic

**Output rendering**: a small modal or sidebar widget showing the suggested line, with "copy" and "use as their next reply" actions

**System prompt**: voice anchor + maybe one relationship hook to the topic. Minimal context.

**Use case**: writer's-room style prompting — "What would winifred say if asked about the riots?" — for brainstorming or character study, no scene mutation.

### 5. `brainstorm`

**Goal**: free idea generation — possible names for a tavern, what might be in a sealed letter, three ways a faction could react to a betrayal. No character voice, no scene mutation.

**Input**: free-form question / topic + optional seed (a list, an existing constraint)

**Output rendering**: a results panel; user can drag any result into a note pad, save to campaign notes, or promote a single result into a fact or extra via explicit action.

**System prompt**: campaign meta + style guide + the user's question. Skip cast, scene, mechanics entirely.

### 6. `edit_prose`

**Goal**: polish a draft the user typed (or a prior canonical response) — tighten, expand, change POV, fix tense.

**Input**: prose snippet + edit instruction

**Output rendering**: diff against the input; user accepts to replace (when applied to a canonical post, runs Extractor with replace semantics; when applied to a draft, just inserts into the composer)

**System prompt**: style guide + edit instruction. Skip cast, scene, mechanics.

### 7. `translate`

**Goal**: translate or transform a snippet — between languages, between dialects, between formality registers ("make this sound more like a chartered solicitor and less like a duchess").

**Input**: snippet + target form

**Output rendering**: side-by-side; "copy translated" or "replace in source post" actions.

**System prompt**: minimal — the snippet, the target form, the style guide if relevant.

## Suppression matrix

The Context Builder applies these rules when `auxiliary_task` is set:

| Component | Canonical turn | `impersonate_pc` | `rewrite_post` | `continue_as` | `what_would_x_say` | `brainstorm` | `edit_prose` | `translate` |
|---|---|---|---|---|---|---|---|---|
| System prompt (campaign) | full | task-specific | task-specific | task-specific | task-specific | minimal | minimal | minimal |
| Style guide | included | included | included | included | included | included | included | optional |
| Content boundaries | included | included | included | included | included | included | included | included |
| Lock-in: PC card | full | full (target PC) | snapshot at time of original | scene PC | none | none | none | none |
| Lock-in: scene header | full | full | snapshot | full | minimal | none | none | none |
| Lock-in: commitments | full | none | snapshot | none | none | none | none | none |
| Lock-in: mechanics result | full | none | none | none | none | none | none | none |
| Spotlight: cast cards | full | partial (PC + 1-2 closest) | snapshot | target NPC + scene cast | target NPC only | none | none | none |
| Spotlight: voice anchor | speaker | target PC | original speakers | target NPC | target NPC | none | none | none |
| Background tier | full | none | snapshot | none | none | none | none | none |
| Archive tier | full | none | none | none | none | none | none | none |
| Recent posts | last 8 | last 6 | last 4 around target | last 3 | last 1-2 referencing target | none | none | none |
| Together-mode tracker block | yes (if mode=Together) | **no** | **no** | **no** | **no** | **no** | **no** | **no** |
| Tool-use tool declarations | yes (if mode=Tool-use) | **no** | **no** | **no** | **no** | **no** | **no** | **no** |
| Mechanics.should_roll called | yes | **no** | **no** | **no** | **no** | **no** | **no** | **no** |
| Drift-correction injection | yes | **no** | **no** | **no** | **no** | **no** | **no** | **no** |
| Extractor invoked post-response | yes | **no** | only on accept | only on accept | **no** | **no** | only on accept | **no** |
| Scene file mutated | yes (append post) | **no** | only on accept (replace) | only on accept (append) | **no** | **no** | only on accept (replace) | **no** |
| State Store mutated | yes | **no** | only on accept | only on accept | **no** | **no** | only on accept | **no** |
| Time advanced | per Extractor | **no** | **no** | **no** | **no** | **no** | **no** | **no** |
| Observability audit | turn | aux | aux | aux | aux | aux | aux | aux |

The pattern is consistent: auxiliary tasks never write state silently. Commits happen only when the user explicitly accepts an auxiliary output, and at the moment of acceptance the system routes the data through the canonical path (a normal post submission or post-replace operation that triggers the normal Extractor + state-mutation pipeline).

## Interface

### Context Builder

```python
@dataclass
class AuxiliaryTask:
    kind: TaskKind
    target_character_ref: Optional[str] = None
    target_post_id: Optional[str] = None
    edit_instruction: Optional[str] = None
    snippet: Optional[str] = None
    steering_hint: Optional[str] = None
    extra_params: dict = field(default_factory=dict)

class TaskKind(Enum):
    IMPERSONATE_PC = "impersonate_pc"
    REWRITE_POST = "rewrite_post"
    CONTINUE_AS = "continue_as"
    WHAT_WOULD_X_SAY = "what_would_x_say"
    BRAINSTORM = "brainstorm"
    EDIT_PROSE = "edit_prose"
    TRANSLATE = "translate"
```

Context Builder signature gains the parameter (overriding the version in `22-extraction-modes.md`):

```python
class ContextBuilder(Protocol):
    async def build(
        self,
        player_input: str,
        campaign_id: str,
        mechanics_results: list[MechanicsResult] = [],
        extractor_mode: ExtractionMode = ExtractionMode.SEPARATE,
        auxiliary_task: Optional[AuxiliaryTask] = None,
    ) -> AssembledPrompt: ...
```

The implementation branches on `auxiliary_task.kind` to apply the suppression rules and substitute the task-specific system prompt.

### Orchestrator

```python
class Orchestrator(Protocol):
    async def run_auxiliary_task(
        self,
        campaign_id: str,
        task: AuxiliaryTask,
        on_token: Callable[[str], None] = None,
    ) -> AuxiliaryResult: ...
```

```python
@dataclass
class AuxiliaryResult:
    task: AuxiliaryTask
    text: str
    completed_at: datetime
    model_used: str
    tokens: int
    # Acceptance is a separate user action:
    pending_commit_action: Optional[CommitAction]  # impersonate→submit_post; rewrite→replace_post; etc.
```

The Orchestrator never auto-commits. The Frontend takes the `AuxiliaryResult`, renders it appropriately, and invokes the commit action only when the user accepts.

### Commit actions

Each task kind that *can* commit declares its commit action shape:

```python
@dataclass
class CommitAction:
    kind: CommitKind  # submit_post | replace_post | append_post | none
    payload: dict     # text, post-id, etc.
```

For `impersonate_pc`: the user-edited result is submitted as a normal post via `POST /campaigns/{id}/turns` — the canonical Extractor + state-mutation path runs as usual.

For `rewrite_post`: the accepted result replaces the target post; the original is moved to alternates (see `24-swipes-and-forks.md`); Extractor runs on the new content with replace semantics so deltas from the old version are reversed before new deltas apply.

For `continue_as`: the accepted result appends as a new post authored by the target character (NPC-authored posts are allowed; see `10-scene-manager.md`).

For the others (`what_would_x_say`, `brainstorm`, `edit_prose`, `translate`): commit actions vary — copy to clipboard, save to campaign notes, replace draft in composer — none mutate the scene state by default.

## Frontend affordances

### Distinguishing auxiliary output

Auxiliary results are visually distinct from canonical posts:
- Wrapped in a panel with a "Draft" / "Suggestion" / "Rewrite preview" label
- Light dotted border, muted background
- Action bar with: Accept, Discard, Try again, Edit, Copy

The user cannot mistake an auxiliary result for a canonical post.

### Entry points

| Task | Trigger |
|---|---|
| `impersonate_pc` | "Suggest a post" button in the input area; keyboard `?i` |
| `rewrite_post` | "Regenerate with edit..." menu on each model post |
| `continue_as` | "Continue this thought" action on a character's last post in the scene pane |
| `what_would_x_say` | Right-click on a character chip (HUD or cast view) → "Ask what they'd say..." |
| `brainstorm` | Sidebar "Brainstorm" panel; keyboard `?b` |
| `edit_prose` | Menu on a model post → "Edit the prose"; menu on the composer draft → "Polish" |
| `translate` | Menu on selected text → "Translate / restate as..." |

### In-flight indicator

A non-canonical generation in-flight shows in the HUD's status bar as "Auxiliary task: impersonate Aleksandr — running" with a cancel control. Distinct icon and color from canonical turn streaming.

### Acceptance flow examples

**impersonate_pc**:
1. User clicks "Suggest a post" → auxiliary call streams
2. Draft appears in the composer (editable, not committed)
3. User edits as desired and clicks Submit
4. Submit triggers the normal turn submission → canonical Extractor runs

**rewrite_post**:
1. User clicks "Regenerate with edit" on a model post → provides instruction
2. Auxiliary call streams; preview pane shows the new version next to the old
3. User clicks Accept → original post moves to alternates; new post becomes primary; Extractor runs with replace semantics; state-store rewinds old deltas and applies new ones

**brainstorm**:
1. User opens Brainstorm panel, asks "five tavern names for the Camden waterfront"
2. Results listed; each has Copy, Pin to campaign notes, Drop into entity creator
3. None of these mutate scene state

## Per-task model routing

Auxiliary tasks may use a different model than canonical turns. Per-campaign config:

```yaml
model_routing:
  canonical_turn: claude-opus-4-7
  extractor: claude-haiku-4-5
  auxiliary:
    impersonate_pc: claude-opus-4-7      # voice quality matters; use the main model
    rewrite_post: claude-opus-4-7
    continue_as: claude-opus-4-7
    what_would_x_say: claude-sonnet-4-6   # smaller is fine for one-liners
    brainstorm: claude-sonnet-4-6
    edit_prose: claude-sonnet-4-6
    translate: claude-haiku-4-5
```

LLM Gateway exposes a per-task routing API; the Orchestrator picks the model when constructing the call.

## Safety boundary

The defining property of auxiliary tasks: **no silent state mutation**. Concretely:

- Scene files are not written without explicit user accept
- State Store is not mutated without explicit user accept
- Time does not advance from auxiliary tasks
- Mechanics rolls are never called from auxiliary tasks
- Drift scores are not incremented (drift tracking ignores auxiliary calls)
- Library files are never modified
- Continuity / facts / commitments are never written without user accept (and even then, only via the canonical path on commit)

If an auxiliary call is interrupted (cancelled, errored), nothing has changed. The user can re-run without state drift.

## Auxiliary audit

Auxiliary tasks are audited in `16-observability.md`'s logs but in a separate category:

```
[aux] 2024-... task=impersonate_pc campaign=by-night-london pc=aleksandr model=claude-opus-4-7 tokens=512 accepted=true
[aux] 2024-... task=brainstorm campaign=by-night-london model=claude-sonnet-4-6 tokens=240 accepted=false
[aux] 2024-... task=rewrite_post campaign=by-night-london post=p_4710 model=claude-opus-4-7 tokens=620 accepted=true cascaded_replace=true
```

The audit shows acceptance so the user can review how often suggestions are kept.

## Interaction with other modules

- **`02-context-builder.md`**: gains the `auxiliary_task` parameter; suppression rules are implemented in its build pipeline
- **`04-extractor.md`** and **`22-extraction-modes.md`**: extraction is skipped for auxiliary tasks; for tasks that commit, extraction runs only on the accepted commit through the canonical path
- **`05-llm-gateway.md`**: per-task model routing is a Gateway responsibility
- **`06-mechanics.md`**: Mechanics.should_roll is not called from auxiliary tasks; mechanics modules never see auxiliary calls
- **`08-characters.md`**: drift detection ignores auxiliary tasks; the voice anchor for the target character is loaded per task
- **`10-scene-manager.md`**: scene files are only touched on user accept; the Orchestrator's auxiliary path bypasses normal post-append
- **`16-observability.md`**: separate audit category for auxiliary tasks with acceptance tracking
- **`19-scene-hud.md`**: HUD status bar shows in-flight auxiliary tasks; HUD widgets do not refresh from auxiliary task results
- **`24-swipes-and-forks.md`**: `rewrite_post` produces a post replacement that integrates with the swipe / alternates model

## Failure modes

| Failure | Behavior |
|---|---|
| Auxiliary call errors mid-stream | Discard partial output; surface error; no state changed |
| User accepts a rewrite, but Extractor on the new content fails | Reverted: original post restored; user notified |
| Auxiliary task model unavailable | Fall back to the canonical-turn model with a one-line warning |
| Brainstorm produces something the content boundaries forbid | Content boundary system catches it before the user sees it (same path as canonical) |
| User accepts an impersonate_pc draft but the canonical submit fails | Standard turn-submit failure handling; the draft remains in the composer |
| Concurrent auxiliary tasks | Allowed; each has its own status indicator; no shared state |

## Open questions

- **Inline impersonation hints from the model itself**. After a canonical turn, the model could optionally suggest 2-3 short draft continuations for the PC (without committing). Useful for "writer's block" moments. Could be a config toggle. v2.
- **Auxiliary tasks contributing to drift indirectly**. If the user accepts many `rewrite_post` results that pull a character's voice in a new direction, the canonical voice anchor may need to update. Drift tracking on the canonical version handles this; no special handling needed for auxiliary.
- **Brainstorm history**. Brainstorm results are ephemeral by default but the user often wants to revisit. A per-campaign "brainstorm log" panel is worth adding.
- **`continue_as` for the PC**. Could the user ask "continue as my PC" without committing — like `impersonate_pc` but applied to extend their last post rather than draft a new one. Probably worth folding into `continue_as` as a special case where target_character_ref equals the PC.
- **Voice anchor leakage**. An `impersonate_pc` run that pulls strongly from the voice anchor could "regress to the mean" of the anchor. Worth optionally including more recent posts vs the anchor itself in the prompt — a per-PC tuning.
- **Long brainstorms as agents**. A user might want "brainstorm 10 tavern names, then pick the best 3, then write a one-paragraph backstory for each." That's an agentic flow on top of `brainstorm`. v2.
- **Auxiliary tasks across PCs in shared scenes**. Can the user impersonate the *other* PC in a shared scene? Probably no by default (consent / authorship), with a per-campaign override.
- **Caching auxiliary prompts**. Many auxiliary calls have small, stable contexts (voice anchor + scene header). Prompt caching here is a clear cost win for providers that support it.
