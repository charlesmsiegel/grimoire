# Auxiliary Tasks

Non-canonical model calls — drafts, rewrites, brainstorms — that never
silently mutate state. Every model call carries an explicit task kind
that drives prompt assembly, suppression rules, and post-response
handling.

The defining property: **no silent state mutation**. Scene files, State
Store, time, drift, mechanics, library, continuity are untouched until the
user explicitly accepts an auxiliary output, at which point the data is
routed through the canonical path.

## Task taxonomy

```python
class TaskKind(Enum):
    IMPERSONATE_PC = "impersonate_pc"        # draft active PC's next post
    REWRITE_POST = "rewrite_post"            # regenerate a previous post with edit instruction
    CONTINUE_AS = "continue_as"              # extend a specific character's last action / monologue
    WHAT_WOULD_X_SAY = "what_would_x_say"    # one-off line of dialogue, no scene mutation
    BRAINSTORM = "brainstorm"                # free idea generation
    EDIT_PROSE = "edit_prose"                # polish a draft / response
    TRANSLATE = "translate"                  # transform between languages / dialects / registers
```

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
```

Each task has its own system prompt and context budget — see the
suppression matrix below for what each task includes / drops.

## Suppression invariants (every task kind)

- Together-mode tracker block: NO.
- Tool-use tool declarations: NO.
- `Mechanics.should_roll` called: NO.
- Drift-correction injection: NO.
- Drift score increments: NO.
- Extractor invoked post-response: NO (runs only on user-accepted commits
  through the canonical path).
- Scene file mutated: NO (only on accept).
- State Store mutated: NO (only on accept).
- Time advanced: NO.
- Library / continuity / facts / commitments: NO without explicit accept.

Cancellation or error mid-stream leaves nothing changed; the user can
re-run without drift.

## Per-task context budget (sketch)

| Task | System prompt | PC card | Scene header | Voice anchor | Recent posts |
|---|---|---|---|---|---|
| `impersonate_pc` | task-specific | full (target PC) | full | target PC | last 6 |
| `rewrite_post` | task-specific | snapshot | snapshot | original speakers | last 4 around target |
| `continue_as` | task-specific | scene PC | full | target NPC | last 3 |
| `what_would_x_say` | task-specific | none | minimal | target NPC | last 1-2 referencing |
| `brainstorm` | minimal | none | none | none | none |
| `edit_prose` | minimal | none | none | none | none |
| `translate` | minimal | none | none | none | none |

Style guide and content boundaries included for everything except
`translate` (optional). Mechanics results never included. Background and
archive tiers dropped for everything except canonical turns.

The Context Builder implements the suppression matrix by branching on
`auxiliary_task.kind`.

## Orchestrator

```python
async def run_auxiliary_task(
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
    pending_commit_action: Optional[CommitAction]   # never auto-applied
```

Commit actions per task:

- `impersonate_pc` → `submit_post` (user-edited text submitted as a normal
  turn; canonical Extractor runs).
- `rewrite_post` → `replace_post` (original moves to alternates per
  `swipes-alternates.md`; Extractor runs with replace semantics).
- `continue_as` → `append_post` (NPC-authored post appended; canonical
  pipeline).
- Others → copy / save to notes / replace draft; no scene mutation by
  default.

## Per-task model routing

```yaml
model_routing:
  canonical_turn: claude-opus-4-7
  extractor: claude-haiku-4-5
  auxiliary:
    impersonate_pc: claude-opus-4-7        # voice quality matters
    rewrite_post: claude-opus-4-7
    continue_as: claude-opus-4-7
    what_would_x_say: claude-sonnet-4-6
    brainstorm: claude-sonnet-4-6
    edit_prose: claude-sonnet-4-6
    translate: claude-haiku-4-5
```

LLM Gateway exposes per-task routing; Orchestrator selects the model when
constructing the call.

## Frontend affordances

Auxiliary results are visually distinct from canonical posts: panel with
"Draft" / "Suggestion" / "Rewrite preview" label, dotted border, muted
background. Action bar: Accept / Discard / Try again / Edit / Copy.
Impossible to mistake an auxiliary output for a canonical post.

Entry points:

| Task | Trigger |
|---|---|
| `impersonate_pc` | "Suggest a post" button; keyboard `?i` |
| `rewrite_post` | "Regenerate with edit..." menu on each model post |
| `continue_as` | "Continue this thought" action on a character's last post |
| `what_would_x_say` | Right-click a character chip → "Ask what they'd say..." |
| `brainstorm` | Sidebar Brainstorm panel; keyboard `?b` |
| `edit_prose` | Menu on a model post → "Edit the prose"; composer → "Polish" |
| `translate` | Menu on selected text → "Translate / restate as..." |

In-flight indicator distinct from canonical streaming (icon + color +
cancel control in HUD status bar).

## Auxiliary audit

Separate audit category in `16-observability.md` with acceptance tracking:

```
[aux] task=impersonate_pc campaign=... pc=... model=... tokens=512 accepted=true
[aux] task=brainstorm campaign=... model=... tokens=240 accepted=false
[aux] task=rewrite_post campaign=... post=p_4710 model=... tokens=620 accepted=true cascaded_replace=true
```

## Interactions

- `extraction-modes.md`: auxiliary tasks force `ExtractionMode.NONE`.
- `06-mechanics.md`: mechanics never see auxiliary calls.
- `08-characters.md`: drift detection ignores auxiliary tasks; the target
  character's voice anchor is loaded per task.
- `10-scene-manager.md`: scene files touched only on accept.
- `scene-hud.md`: HUD status bar shows in-flight auxiliary tasks; HUD
  widgets do *not* refresh from auxiliary results.
- `swipes-alternates.md`: `rewrite_post` integrates with the alternates
  model — accepted output becomes a new alternate and is promoted to
  primary.

## Failure handling

| Failure | Behavior |
|---|---|
| Mid-stream error | Discard partial; surface error; no state changed |
| Accepted rewrite + Extractor failure on new content | Revert: original post restored; user notified |
| Aux model unavailable | Fall back to canonical-turn model with warning |
| Content boundary violation | Caught before user sees output (same path as canonical) |
| Concurrent auxiliary tasks | Allowed; each has its own status indicator |
