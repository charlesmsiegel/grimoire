# 10 — Scene Manager

## Purpose

The Scene Manager owns the play history. It decides where scenes begin and end, appends posts to scenes, maintains scene metadata, tracks which characters and PCs are present, generates running summaries, and implements the multi-PC advance trigger.

Scenes are the unit of play. A turn is bounded by a scene; each post belongs to exactly one scene. The structured per-scene context drives prompt assembly, image generation, and continuity tracking.

## Storage: markdown + YAML sidecar pairs

Each scene is two files under `data/campaigns/<id>/scenes/`:

```
0001-elysium-opening.md          # the prose (posts in order)
0001-elysium-opening.yaml        # metadata sidecar
0002-chase-through-soho.md
0002-chase-through-soho.yaml
...
```

The `.md` file holds the prose, with each post prefixed by a heading:

```markdown
## Post 1 — narrator
The Prince's tower is candle-lit tonight, the chandeliers dimmed at her request...

## Post 2 — pc:alistair-hyde-smythe
I incline my head — the smallest gesture — and step into the room.

## Post 3 — narrator
The Prince watches you cross the room. Her smile is not friendly...
```

The `.yaml` file holds metadata:

```yaml
id: 0001-elysium-opening
campaign_id: by-night-london
branch_id: main
ordinal: 1
slug: elysium-opening
title: "Elysium Opening"

location_ref: elysium
in_game_start: 2024-10-31T22:00:00
in_game_end: 2024-10-31T23:15:00
greeting_id: elysium-opening

pov_character_ref: alistair-hyde-smythe
present_character_refs:
  - alistair-hyde-smythe
  - prince-of-london
present_pc_refs:
  - alistair-hyde-smythe

mood: "tense civility, jasmine-scented smoke"

post_count: 12
threads_introduced:
  - "The Prince has summoned Alistair for a reason"
threads_paid_off: []

tags: [introduction, political]
closed: false
closed_at_turn: null

last_advance_at_post: 12         # for multi-PC advance tracking
running_summary: |
  Alistair arrives at Elysium...
```

Both files are SSOT. SQLite indexes them (`scenes` and `posts` tables in `03-state-store.md`) but doesn't own them. Editing a scene file directly is supported; the watcher catches the change and reindexes.

Naming: `NNNN-slug.md` where NNNN is a per-campaign zero-padded ordinal and slug is generated from the scene title or first location. Slugs are stable; renaming requires explicit action.

## Responsibilities

- Decide when to start a new scene
- Append posts to the current scene's markdown file; update sidecar
- Maintain scene metadata: present characters, present PCs, location, time
- Generate running summaries periodically; full summary on scene close
- Implement the multi-PC advance trigger: auto-respond for 1 PC; require explicit advance for 2+
- Track which PCs are present in which scenes (per-PC current scene)
- Identify ending heuristics; offer to close scenes; close on user action
- Detect threads (lines of tension introduced) and pay-offs
- Support fork: copy scene files into the new branch's scene directory on first write

## Non-responsibilities

- Does not assemble prompts (Context Builder does; consumes scene data)
- Does not generate images (ImageGen does; subscribes to scene events)
- Does not extract structured state from posts (Extractor does; produces deltas during turn)
- Does not advance in-game time (Time Engine does; consults scene timing hints)
- Does not own character data (Characters does; references by ref)

## Scene boundary detection

Heuristic, but the user always has final say.

Inputs:
- Time gap: a long in-game time gap (e.g., next morning)
- Location change: PC leaves the current location
- Cast turnover: significant change in present cast
- Tonal shift: major mood / activity change
- Explicit signals in prose ("hours later", "the next day", "we adjourned to...")
- User signal: "/end scene", "/new scene", "advance to ..."

The Orchestrator calls `is_scene_break(player_input)` before the LLM is called. Returns:

```python
@dataclass
class SceneBreakDecision:
    is_break: bool
    confidence: float
    reason: str                 # 'time_gap', 'location_change', 'cast_change', 'tonal_shift', 'explicit', 'user_signal'
    proposed_new_scene: Optional[SceneInit]  # if break: starting location, time, present cast
```

Borderline cases (confidence 0.5-0.7) prompt the user. High confidence (>0.8) starts a new scene automatically with rollback option.

## Post authorship

Every post has an author. Authorship determines context (POV) and audit trail.

```python
@dataclass
class Post:
    id: str
    scene_id: str
    order_in_scene: int
    author_kind: AuthorKind          # 'pc', 'narrator', 'npc', 'system'
    author_pc_ref: Optional[str]     # set when author_kind = 'pc'
    author_npc_ref: Optional[str]    # set when author_kind = 'npc' (NPC monologues, etc.)
    body: str
    is_player: bool                  # true if a human typed it; false if the model
    created_at: datetime
    turn_id: str
```

The markdown file shows the author in the post heading. The sidecar's `post_count` and SQLite's `posts` table track for query.

## Multi-PC advance trigger

The crux of multi-PC scenes. Decision is made per submission:

```python
async def on_post_submitted(self, scene_id: str, post: Post) -> AdvanceDecision:
    scene = await self.load(scene_id)
    present_pcs = scene.present_pc_refs

    if len(present_pcs) <= 1:
        # Single PC (or none): auto-respond
        return AdvanceDecision(auto_respond=True, reason="single_pc_scene")

    # 2+ PCs present: do not auto-respond; wait for explicit advance
    return AdvanceDecision(auto_respond=False, reason="multi_pc_pending_advance")
```

The Orchestrator uses this to decide whether to call the LLM immediately or wait. When the user clicks Advance:

```python
async def on_advance_requested(self, scene_id: str) -> AdvanceResult:
    scene = await self.load(scene_id)
    pending_posts = await self.posts_since_last_advance(scene_id)
    if not pending_posts:
        raise NothingToAdvance()

    # Mark this advance point
    scene.last_advance_at_post = scene.post_count
    await self.save_metadata(scene)

    return AdvanceResult(scene=scene, pending_posts=pending_posts)
```

The Orchestrator then calls Context Builder with the scene and pending posts, calls the LLM, and the response is appended as the next post.

A PC entering or leaving a scene mid-flow changes the auto/advance state:

- 1 PC → 2 PCs: auto-respond stops; next post requires Advance
- 2 PCs → 1 PC: auto-respond resumes; pending posts are flushed via implicit advance

The Frontend gets `advance_disabled` / `advance_enabled` WebSocket events to update the UI.

## Per-PC current scenes

Each PC tracks their current scene (where they "are" right now):

```python
# Stored in CharacterState.current_scene_id (campaign-scoped)
# Set by Scene Manager when:
#  - A PC posts in a scene (becomes their current)
#  - A new scene is created with that PC present
#  - The user explicitly switches a PC to a scene
```

The Frontend's PC switcher shows each PC and their current scene. Most of the time each PC is in their own scene (different threads); occasionally they share a scene (the crossover case).

## Running summary

Compressed view of what's happened in the scene so far. Maintained incrementally:

- Updated every N posts (default 5) by a background job calling the LLM with the scene's recent posts and the previous running summary
- Used in Context Builder for scenes too long to include verbatim
- Stored in the sidecar's `running_summary` field

```python
async def update_running_summary(self, scene_id: str) -> str:
    scene = await self.load(scene_id)
    recent_posts = await self.recent_posts(scene_id, n=8)
    new_summary = await self._llm_summarize(
        previous=scene.running_summary,
        new_posts=recent_posts,
    )
    scene.running_summary = new_summary
    await self.save_metadata(scene)
    return new_summary
```

## Scene close

When the user signals "end scene" or the heuristic strongly suggests it:

1. Generate a final summary (richer than the running summary)
2. Extract key beats (the 3-5 most important moments)
3. Identify resolved and unresolved threads
4. Set `closed = true`, `closed_at_turn`, `in_game_end`
5. Emit `scene_ended` event

Threads that are unresolved get tagged for the Continuity module to track.

## Threads

A *thread* is a line of tension or a pending question introduced in a scene that the campaign should pay off later. Examples:
- "The Prince has summoned Alistair for a reason"
- "Someone left a black rose at the door"
- "Beatrice noticed a sigil she didn't recognize"

Threads are detected by the Extractor (LLM-assisted) or marked manually. Scene Manager tracks `threads_introduced` and `threads_paid_off` per scene; Continuity (`11`) maintains the campaign-wide thread ledger.

## Interface

```python
class SceneManager(Protocol):
    # CRUD
    async def list_scenes(self, campaign_id: str, branch_id: str) -> list[Scene]: ...
    async def get_scene(self, scene_id: str) -> Scene: ...
    async def get_scene_file_path(self, scene_id: str) -> Path: ...
    async def load_scene_body(self, scene_id: str) -> str: ...

    # Active scene tracking
    async def active_scene_for_campaign(self, campaign_id: str, branch_id: str) -> Optional[Scene]: ...
    async def active_scene_for_pc(self, campaign_id: str, pc_ref: str) -> Optional[Scene]: ...

    # Scene lifecycle
    async def start_scene(self, init: SceneInit) -> Scene: ...
    async def close_scene(self, scene_id: str) -> SceneCloseReport: ...

    # Posts
    async def append_post(self, scene_id: str, post: Post) -> None: ...
    async def get_posts(self, scene_id: str, range: Optional[tuple] = None) -> list[Post]: ...
    async def posts_since_last_advance(self, scene_id: str) -> list[Post]: ...
    async def recent_posts(self, scene_id: str, n: int = 10) -> list[Post]: ...

    # Presence
    async def add_present_character(self, scene_id: str, character_ref: str) -> None: ...
    async def remove_present_character(self, scene_id: str, character_ref: str) -> None: ...
    async def set_pov(self, scene_id: str, character_ref: str) -> None: ...

    # Decisions
    async def is_scene_break(self, scene_id: str, player_input: str) -> SceneBreakDecision: ...
    async def on_post_submitted(self, scene_id: str, post: Post) -> AdvanceDecision: ...
    async def on_advance_requested(self, scene_id: str) -> AdvanceResult: ...

    # Summarization
    async def update_running_summary(self, scene_id: str) -> str: ...

    # Threads
    async def add_thread(self, scene_id: str, thread: Thread, kind: str) -> None: ...
                                                          # kind: 'introduced' or 'paid_off'
    async def list_threads(self, scene_id: str) -> SceneThreads: ...

    # Editing
    async def edit_post(self, post_id: str, new_body: str, source: str) -> None: ...
                                                          # triggers retcon flow
    async def delete_post(self, post_id: str, source: str) -> None: ...

    # Fork (copy-on-write)
    async def fork_scenes_for_branch(self, campaign_id: str, branch_id: str) -> None: ...
```

## Events emitted

- `scene_started` — new scene created
- `scene_ended` — scene closed
- `post_appended` — post added (always)
- `pc_post_appended` — post added by a PC (subset of post_appended for multi-PC tracking)
- `advance_requested` — user clicked Advance in a multi-PC scene
- `advance_disabled` / `advance_enabled` — PC entered or left a scene
- `running_summary_updated`
- `thread_introduced` / `thread_paid_off`

## File watcher integration

If a user edits a scene file directly:
1. Watcher detects change
2. Scene Manager re-parses the file
3. Updates the SQLite scenes/posts indexes
4. Emits `scene_file_changed` event
5. Frontend refreshes the view

Editing a scene file mid-play is risky (could conflict with in-flight writes), but it's allowed. Last-write-wins with hash-based conflict detection — see `03-state-store.md`.

## Configuration

```yaml
scene_manager:
  break_detection:
    enabled: true
    confidence_threshold_auto: 0.8
    confidence_threshold_prompt: 0.5
  running_summary:
    update_every_n_posts: 5
    model: claude-haiku-4-5
    max_tokens: 500
  thread_detection:
    enabled: true
    model: claude-haiku-4-5
  files:
    scene_naming_pattern: "{ordinal:04d}-{slug}"
    post_heading_pattern: "Post {n} — {author}"
  multi_pc:
    require_advance_with_multiple_pcs: true
    show_pending_count_in_ui: true
```

## Open questions (deferred)

- **Scene branching mid-scene.** A user wants to fork from post 7 in scene 3 — supported via the State Store's branching, but UX needs care. v2 polish.
- **Cross-PC scene visibility.** Should PC A see PC B's scenes by default in the Frontend? Probably no — each PC sees their own threads, with a campaign overview showing all. UX decision.
- **Scene rename.** Renaming a slug changes the filename; the file is renamed and indexes update. Trivial but needs UI affordance.
- **Multi-PC ordering.** If PC A and PC B both submit before Advance, who goes first in the LLM's response? Probably temporal order (whoever posted first); LLM addresses both.
- **Idle PCs in a multi-PC scene.** If PC A is present but doesn't post for several rounds, do they still block auto-advance? Yes — present means present until removed. Adding "afk" status is a v2 idea.
