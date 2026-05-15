# 24 — Swipes, Alternates, and Forks

## Purpose

Long-form play with an LLM is not a straight line. The model produces a response; the player wants to try another version. The player edits a turn from yesterday and wonders what would have happened differently. The campaign splits into a hypothetical "what if we'd let her leave" branch the player wants to explore without disturbing the main thread.

Three patterns cover this:

| Pattern | What it does | Affects subsequent posts? | Lives where |
|---|---|---|---|
| **Swipe** (alternates) | Regenerate the *latest* response; keep prior versions accessible; switch primary | No (only the latest post varies) | One scene's post; same campaign id |
| **Retcon** | Edit an earlier post and either leave subsequent posts or replay them | Yes (subsequent posts may be replayed) | Same campaign id |
| **Fork** | Branch the whole campaign at a chosen turn; develop both branches independently | Yes (new campaign id) | New campaign |

SillyTavern's "swipe" pattern (one response, many alternates, switch between them with chevrons) is the entry point most users know. Grimoire generalizes it: each alternate carries its own delta set, the State Store rewinds and reapplies cleanly on switch, and the same primitives compose into retcons and forks.

This spec defines the data model, the constraint that swipes only apply to the latest post, the rewind / reapply mechanism, retcon semantics, fork mechanics, and the UI affordances.

## Responsibilities

- Store multiple alternates for any post (latest or historical) with their associated delta sets
- Maintain a primary pointer per post indicating which alternate is the canonical reading
- Rewind and reapply deltas atomically when the user switches the primary
- Constrain swipes to the latest post (no mid-history alternate switching without retcon or fork)
- Implement retcon: edit an earlier post and either replay following turns or leave them
- Implement fork: branch a campaign at a chosen turn, copy state, generate a new campaign id
- Surface alternates, retcon, and fork as first-class UI controls

## Non-responsibilities

- Does not own scene files (Scene Manager does)
- Does not extract deltas from text (Extractor does); receives delta sets as input
- Does not own the audit log (Observability does); records to it
- Does not own the LLM call (Orchestrator does); is invoked by it on regenerate

## The latest-post constraint

Swipes apply only to the **latest post** in a scene (and by transitivity, the latest in the campaign). The reason is the rewind cascade:

When you switch alternates on a mid-history post, every later post was authored under the original alternate's state. Those later posts may reference details, characters, or commitments that no longer exist (or that have shifted) in the alternate's state. A naive switch would silently break continuity.

Grimoire enforces a simple rule:
- **Swipe** (alternate switch) is allowed only on the latest post (no posts after it)
- For changes to mid-history posts, use **Retcon** (which makes the rewind cascade explicit) or **Fork** (which sidesteps the problem by branching)

This keeps swipes safe and fast; complex history changes are routed through paths that name what they actually do.

## Data model

### Alternates on a post

Each post has a list of alternates. Today's primary is one of them. The scene `.md` file always reflects the active primary; alternates live in the YAML sidecar.

```yaml
# data/campaigns/<id>/scenes/0047-camden-pub.yaml
id: 0047-camden-pub
in_game_start: 1879-04-12T19:30:00
posts:
  - id: p_4709
    author: pc
    pc_ref: aleksandr
    text: "I take the bottle off the bar and pour two glasses."
    # PC posts: typically one alternate (no regenerate); future: stored drafts
    primary_alternate_id: a_9001
    alternates:
      - id: a_9001
        text: "I take the bottle off the bar and pour two glasses."
        delta_set_id: ds_5921
        author_kind: user
        created_at: 2024-...

  - id: p_4710
    author: model
    primary_alternate_id: a_9011
    alternates:
      - id: a_9011
        text: "winifred catches your hand as you pour..."
        delta_set_id: ds_5922
        author_kind: model
        model: claude-opus-4-7
        prompt_hash: sha256:...
        created_at: 2024-...
        tokens: 520
      - id: a_9012
        text: "winifred watches in silence as you pour..."
        delta_set_id: ds_5923
        author_kind: model
        model: claude-opus-4-7
        prompt_hash: sha256:...
        created_at: 2024-...
        tokens: 491
```

### Delta sets

Each alternate carries a `delta_set_id` referencing the deltas the Extractor produced for that alternate. When the user switches primary, the State Store:

1. Identifies the chain of delta sets active on the swiped post and any subsequent posts that ran under the old primary
2. Rewinds them in reverse order
3. Applies the new alternate's delta set
4. Emits `primary_switched` events so downstream modules (HUD, Continuity, etc.) update their views

Because swipes are constrained to the latest post, the rewind is trivial: there are no subsequent posts to consider. Rewind one delta set, apply one new delta set, done.

### Scene file rendering

The scene `.md` file is generated from the active primary alternates of each post in order. When a primary switches, the `.md` file is rewritten. The `.md` is a derived view; the YAML sidecar is the source of truth for post content.

```
# 0047-camden-pub.md (rendered from sidecar)

[Aleksandr] I take the bottle off the bar and pour two glasses.

[GM] winifred catches your hand as you pour...
```

A "show alternates" view in the Frontend exposes all alternates inline; the rendered `.md` reflects only the primary.

## Swipe (alternate management)

### Generate a new alternate

When the user clicks "Regenerate" on the latest model post:

1. Orchestrator runs a normal canonical turn against the same player input that produced the post (with optional steering hint or a different seed)
2. The new response streams to the Frontend
3. Extractor produces a new delta set
4. A new alternate is appended to the post's `alternates` list
5. The new alternate is **not** automatically promoted to primary (user picks via the chevrons)

The "Regenerate" UI offers two modes:
- **Regenerate (same prompt)** — same context, different stochastic sample
- **Regenerate with steering** — small instruction added ("more menacing", "less verbose")

### Switch primary

User clicks the chevron under the post:

1. The chosen alternate's id is set as primary
2. State Store rewinds the current primary's delta set
3. State Store applies the chosen alternate's delta set
4. Scene `.md` rewritten
5. HUD and other event subscribers re-render
6. Audit log records the switch

### Pin and unpin

A user can pin an alternate ("don't let auto-purge remove this"). Pinned alternates count toward the per-post cap but never auto-evict.

### Delete an alternate

Drop an alternate the user no longer wants. If the alternate was primary, the user must switch first (UI guards against this). Deletion is recoverable from the audit log for a configurable retention window.

### Caps and retention

- Max alternates per post: 5 by default (configurable). When exceeded, the oldest non-pinned alternate is auto-evicted on new generation.
- Auto-purge inactive alternates: scene-close cleanup optionally removes non-pinned, non-primary alternates after configurable time / scene count.
- Storage cost: each alternate is text + a delta set; trivial compared to images.

### Configuration

```yaml
swipes:
  max_alternates_per_post: 5
  purge_on_scene_close:
    enabled: false
    keep_primary: true
    keep_pinned: true
  auto_purge_older_than_days: null  # null = never
```

## Retcon

Retcon is the deliberate edit of an *earlier* post. Because subsequent posts may depend on what the edited post established, retcon offers two follow-ups:

1. **Leave subsequent posts as-is** — the user accepts that they may now be inconsistent. State Store rewinds the original post's deltas and applies the new ones; subsequent posts' deltas remain. Contradiction detection in Continuity will likely surface issues; the user can address them.
2. **Replay following turns** — the system re-runs each subsequent canonical turn from the retcon point onward, using the retconned post and any earlier turns as context. Each replayed turn produces a new alternate (the original is preserved). The user can step through and accept each.

### Retcon flow

1. User selects a post and chooses "Retcon..."
2. Inline editor: edit the prose directly, or use `rewrite_post` auxiliary task (`23-auxiliary-tasks.md`) to produce a new version
3. User accepts the retconned version
4. System prompts: leave following turns, or replay?
5. If leave: rewind old deltas, apply new deltas, surface contradictions
6. If replay: enter "retcon replay mode" — Orchestrator generates new alternates for each following turn; user reviews each; non-accepted alternates are discarded

### Retcon replay UX

A modal or dedicated view shows the post-by-post replay:

```
Retcon replay — by-night-london, from scene 47 post p_4710

[1/4] p_4710 (retconned)            ✓ accepted
[2/4] p_4711                        Reviewing... [Accept] [Try again] [Cancel]
[3/4] p_4712                        Pending
[4/4] p_4713                        Pending
```

The user can cancel at any point; partial retcons leave the campaign in the "leave as-is" state (rewound up to the cancel point, applied new deltas up to the cancel point, rest of timeline retained).

### Retcon vs fork

Retcon mutates the campaign in place. Fork preserves both. A common pattern:
- Fork first ("what-if branch")
- Retcon on the fork
- If the result is good, the user can decide whether to promote the fork as the new main (a swap operation) or keep both campaigns

## Fork

Fork branches a campaign at a chosen turn. Both the original and the fork are real campaigns with full state, independent futures.

### Fork from current

The most common case: "I want to try a different direction from here." Atomic copy of the campaign:

1. Pick a new campaign id and create `data/campaigns/<new-id>/`
2. Copy all narrative files (scenes, sheets, overrides, emergent content) to the new directory
3. Library refs are inherited unchanged (same pinned versions, or `track_latest`)
4. SQLite rows tagged with the original campaign_id are duplicated with the new campaign_id (facts, commitments, transient state, alternates, knowledge state, relationships, etc.)
5. Images: hardlink by default (storage saving); deep-copy as a config option
6. The fork is registered with provenance: `forked_from: <original-id> at turn p_4710`
7. Both campaigns are independent from this moment on

### Fork from earlier turn

Same as fork from current, but only scenes / state up to the chosen turn are copied. The fork's "latest post" is the chosen turn; future scenes don't exist yet. The user starts playing from the fork point.

Implementation: walk the audit log for the original campaign, replay deltas up to the cutoff into a fresh state store for the fork. Faster than naive duplicate-and-truncate because the audit log is the source of truth.

### Fork SQLite implementation

All campaign-scoped SQLite tables have a `campaign_id` column. Fork is:

```sql
-- For each campaign-scoped table, insert rows with the new campaign_id
INSERT INTO facts (...)
SELECT ..., :new_campaign_id, ...
FROM facts
WHERE campaign_id = :original_campaign_id
  AND turn_no <= :fork_at_turn;
-- (Same pattern for commitments, transient_*, knowledge_state, relationships, etc.)
```

For "fork from current", `turn_no <= :fork_at_turn` is "turn_no <= MAX(turn_no)". The audit log is also forked.

### Fork governance

- Forks appear in the campaign list under the parent campaign, indented
- A fork can itself be forked
- Forks can be deleted independently of the parent
- Forks can be **merged back**: a manual operation that re-applies selected turns from the fork into the parent (or accepts the fork as the new parent and archives the original) — v2 polish; the data model supports it

### Use cases

- **What-if**: "what if the negotiation in scene 47 had gone south?" Fork at p_4710, explore the alternative; original continues unchanged.
- **Different PC**: same campaign world, different protagonist focus. Fork at scene 1, change the active PC, replay.
- **Experimental retcon**: "I want to retcon, but I'm not sure." Fork first, retcon on the fork.
- **Two-table game**: same world, two groups of players. Fork at scene 1; each fork develops independently.

## Interface

```python
class SwipeAndFork(Protocol):
    async def regenerate_latest(
        self,
        campaign_id: str,
        scene_id: str,
        steering_hint: Optional[str] = None,
    ) -> Alternate: ...

    async def list_alternates(
        self,
        campaign_id: str,
        post_id: str,
    ) -> list[Alternate]: ...

    async def switch_primary(
        self,
        campaign_id: str,
        post_id: str,
        alternate_id: str,
    ) -> None: ...

    async def pin_alternate(
        self,
        campaign_id: str,
        post_id: str,
        alternate_id: str,
        pinned: bool,
    ) -> None: ...

    async def delete_alternate(
        self,
        campaign_id: str,
        post_id: str,
        alternate_id: str,
    ) -> None: ...

    # Retcon
    async def retcon_post(
        self,
        campaign_id: str,
        post_id: str,
        new_text: str,
        replay_subsequent: bool,
    ) -> RetconResult: ...

    # Fork
    async def fork_campaign(
        self,
        campaign_id: str,
        new_campaign_id: str,
        new_name: str,
        fork_at_post_id: Optional[str] = None,   # None = fork from current
        image_handling: ImageHandling = ImageHandling.HARDLINK,
    ) -> Campaign: ...
```

```python
@dataclass
class Alternate:
    id: str
    post_id: str
    text: str
    delta_set_id: str
    author_kind: AuthorKind                   # user | model | aux (rewrite-accepted)
    model: Optional[str]
    prompt_hash: Optional[str]
    created_at: datetime
    tokens: Optional[int]
    pinned: bool
    is_primary: bool

@dataclass
class RetconResult:
    edited_post_id: str
    replayed_post_ids: list[str]
    cancelled_at_post_id: Optional[str]
    contradictions_detected: list[Contradiction]
```

## UI affordances

### Swipe controls on a post

```
[GM]  winifred catches your hand as you pour...
                                         ◀  2 of 3  ▶   📌  🔄  ✏️
```

- `◀ ▶` chevrons cycle alternates (only on latest post)
- `2 of 3` indicator
- `📌` pin
- `🔄` regenerate (creates a new alternate)
- `✏️` edit (opens rewrite_post auxiliary; on accept, becomes a new alternate)

The chevrons are inactive on mid-history posts (with a tooltip explaining "swipes apply to the latest post only; use Retcon or Fork for earlier posts").

### Regenerate dialog

```
Regenerate response

  ◯ Same prompt — different sample
  ◯ With steering: [______________________]
  ☐ Use a different model: [model picker]

  [Regenerate]   [Cancel]
```

### Retcon flow

A dedicated view (full-screen modal) for retcons because they touch multiple turns:

```
Retcon — by-night-london, post p_4710

Original:
  winifred catches your hand as you pour...

New version:
  winifred steps aside as you pour, eyes on the door.
  [Edit] [Use rewrite_post] [Cancel]

Subsequent posts in this scene (4):
  ◯ Leave as-is
  ◯ Replay (review each new alternate)

[Apply retcon]
```

### Fork dialog

```
Fork campaign

  Source: by-night-london (currently at scene 47, post p_4710)
  Fork from:
    ◉ Current state
    ◯ Earlier post: [____________]

  New campaign name: [_________________]
  New campaign id:   [auto-suggested]

  Image handling:
    ◉ Hardlink (saves disk space)
    ◯ Deep copy

  ☐ Make this the active campaign after forking

  [Fork]   [Cancel]
```

Forks appear in the campaign list:

```
Campaigns
├── by-night-london
│   └── by-night-london-what-if-negotiation   (forked at scene 47)
└── a-saga-in-iberia
```

## Backend contract

```
POST   /campaigns/{id}/scenes/{scene-id}/posts/{post-id}/regenerate
GET    /campaigns/{id}/scenes/{scene-id}/posts/{post-id}/alternates
POST   /campaigns/{id}/scenes/{scene-id}/posts/{post-id}/alternates/{alt-id}/primary
POST   /campaigns/{id}/scenes/{scene-id}/posts/{post-id}/alternates/{alt-id}/pin
DELETE /campaigns/{id}/scenes/{scene-id}/posts/{post-id}/alternates/{alt-id}

POST   /campaigns/{id}/turns/{turn-id}/retcon
POST   /campaigns/{id}/forks

GET    /campaigns/{id}/lineage              # parent campaign + descendants
```

WebSocket events:

```json
{ "type": "alternate_added", "post_id": "...", "alternate": {...} }
{ "type": "primary_switched", "post_id": "...", "from_alt_id": "...", "to_alt_id": "..." }
{ "type": "retcon_started", "post_id": "...", "replay_subsequent": true }
{ "type": "retcon_post_replayed", "post_id": "...", "new_alternate_id": "..." }
{ "type": "retcon_complete", "post_id": "..." }
{ "type": "campaign_forked", "source_campaign_id": "...", "new_campaign_id": "..." }
```

## Audit and observability

Every swipe, retcon, and fork is logged. The audit log in `16-observability.md` gains entries:

```
[swipe]   2024-... campaign=by-night-london post=p_4710 added_alt=a_9012 (regenerate, same prompt)
[switch]  2024-... campaign=by-night-london post=p_4710 from=a_9011 to=a_9012 (rewind+apply 14 deltas)
[retcon]  2024-... campaign=by-night-london post=p_4708 replay_subsequent=true posts_replayed=3
[fork]    2024-... source=by-night-london new=by-night-london-what-if-negotiation at_post=p_4710 image_handling=hardlink
```

Useful for replay (`16-observability.md` includes time-travel queries; retcons and forks are first-class events in those queries).

## Interaction with other modules

- **`03-state-store.md`**: rewind / reapply mechanics; delta-set lifecycle; campaign_id scoping for forks
- **`04-extractor.md`** and **`22-extraction-modes.md`**: each alternate's delta set is produced by extraction at the time it was generated; the State Store stores them keyed by delta_set_id
- **`10-scene-manager.md`**: scene file is regenerated from primary alternates after primary switches
- **`11-continuity.md`**: contradiction detection runs after rewind/apply; surfaces issues from "leave as-is" retcons
- **`16-observability.md`**: separate audit categories for swipe, switch, retcon, fork; time-travel queries respect alternates
- **`20-transient-state.md`**: transient state is part of the delta set; rewinds correctly
- **`23-auxiliary-tasks.md`**: `rewrite_post` on a model post creates a new alternate; on accept it becomes primary
- **`13-export.md`**: exports default to primary alternates; an "include alternates as footnotes" option is available for archival exports

## Performance

- Regenerate latest: a normal turn-cost (one LLM call + extraction)
- Switch primary: rewind + reapply of one delta set (typically 5-20 deltas) → < 100ms
- Retcon "leave as-is": same as switch primary → < 100ms
- Retcon "replay subsequent": one canonical turn cost per replayed post (linear in number of replayed posts)
- Fork from current: SQLite COPY + file copy / hardlinks → < 1s for a typical campaign
- Fork from earlier turn: audit log replay → typically 1-5s for a long campaign

## Failure modes

| Failure | Behavior |
|---|---|
| Regenerate streams partial then errors | Discard partial; no alternate created; user can retry |
| Switch primary but delta apply fails mid-way | Roll back to prior primary; surface error; alternate unchanged |
| Delete the current primary | UI guards against it; backend rejects |
| Fork mid-flight (during canonical turn streaming) | Fork is queued; runs after the turn completes |
| Hardlinks fail (e.g., cross-device) | Fall back to deep copy automatically; log warning |
| Retcon replay produces a contradictory alternate | Each alternate is offered for user review; user can re-roll, accept, or cancel the retcon mid-replay |
| Audit log corruption affecting fork-from-earlier | Falls back to copy-and-truncate strategy; logged as degraded |
| User opens two forks concurrently | Allowed; each campaign has independent state and event streams |

## Open questions

- **Auto-pin "good" alternates**. Should the system propose pinning an alternate the user accepted via the chevron? Probably a soft suggestion, not auto-pin.
- **Cross-alternate diff view**. Viewing two alternates side by side with prose diff highlighted is valuable; the data model supports it; UI is a v2 polish.
- **Branched alternates** (alternate-of-alternate). Currently a flat list per post. Could be a tree (an alternate gets regenerated, producing alternates-of-alternate). Worth supporting if users ask; flat list works for the common case.
- **Fork merge-back**. The data model preserves enough provenance to merge fork-N back into the parent at a specified turn. Concrete UI for this is v2; the API can be added when needed.
- **Auto-fork on heavy retcon**. If the user retcons with replay 5+ posts, prompt: "this is a substantial change; would you like to fork first?" Easy nudge.
- **Storage cost of high alternate counts**. With caps at 5 per post and ~1000 posts, ~5000 alternates per campaign — fine for text. Images attached to alternates would change this; for v1 images are scene-scoped, not alternate-scoped, so it's not an issue.
- **What if the user switches alternates on a post whose prompt has since been invalidated** (e.g., a library asset version changed)? The alternate's `prompt_hash` carries the prompt's content hash; switching to an alternate generated under a different library version is allowed but flagged.
- **Streaming a regenerate while extraction of the previous primary is still running**. The Extractor for the prior primary should complete; the regenerate enqueues. Backpressure rules belong in the Orchestrator (`01-orchestrator.md`).
- **Naming forks**. Auto-suggested names ("by-night-london-fork-1") are bad; the dialog should prompt for a descriptive name. Worth adding a "describe the divergence" optional field as audit color.
