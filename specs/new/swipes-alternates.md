# Swipes / Alternates

The orchestrator currently has `regenerate_last`, but it deletes and
replaces — there are no alternates, no primary pointer, no delta-set
chain. This spec adds the alternates model.

## Data model

Per-post `alternates: list[Alternate]` and `primary_alternate_id` in the
scene YAML sidecar. Each alternate:

```python
@dataclass
class Alternate:
    id: str
    post_id: str
    text: str
    delta_set_id: str
    author_kind: AuthorKind        # user | model | aux
    model: Optional[str]
    prompt_hash: Optional[str]
    created_at: datetime
    tokens: Optional[int]
    pinned: bool
    is_primary: bool
```

The scene `.md` file is a rendered view of primary alternates in post
order. The sidecar YAML is SSOT — `.md` is regenerated when a primary
switches.

## Latest-post constraint

Swipe (alternate switching) is allowed only on the latest post. Reason:
switching alternates on a mid-history post would silently break
subsequent posts that were authored under the old primary's state.

Mid-history changes go through Retcon (`retcon.md`) or Fork (`fork.md`).
The Frontend disables chevrons on mid-history posts with a tooltip
explaining the alternative routes.

## Operations

### Regenerate latest

User clicks "Regenerate" on the latest model post:

1. Orchestrator runs a normal canonical turn against the same player
   input (with optional steering hint or model override).
2. Response streams to the Frontend.
3. Extractor produces a new delta set.
4. New alternate appended to the post's `alternates` list.
5. New alternate is **not** auto-promoted to primary — user picks via the
   chevrons.

Regenerate dialog offers: same-prompt (different sample) or with-steering
("more menacing", "less verbose"), with an optional model override.

### Switch primary

1. Chosen alternate's id set as primary.
2. State Store rewinds the current primary's delta set.
3. State Store applies the chosen alternate's delta set.
4. Scene `.md` rewritten.
5. HUD and other event subscribers re-render via `primary_switched`.
6. Audit log records the switch.

Because swipes are constrained to the latest post, the rewind is trivial
— one delta set out, one in.

### Pin / unpin

Pinned alternates count toward the per-post cap but never auto-evict.

### Delete an alternate

If it's the primary, UI guards against deletion (must switch first).
Otherwise the alternate is dropped; recoverable from the audit log for a
configurable retention window.

## Caps and retention

```yaml
swipes:
  max_alternates_per_post: 5         # auto-evict oldest non-pinned on new generation
  purge_on_scene_close:
    enabled: false
    keep_primary: true
    keep_pinned: true
  auto_purge_older_than_days: null   # null = never
```

## State Store work (cross-cutting)

Required underneath swipes / retcon / fork:

- `delta_set_id` as a first-class State Store concept.
- Each alternate points at a delta set.
- Switching primary is rewind-one + apply-one, atomic.
- On apply failure mid-switch, roll back to the prior primary; alternate
  unchanged; surface error.
- Time-travel queries in `16-observability.md` aware of alternates so
  audit replay can include / exclude non-primary lines.

## Backend surface

```
POST   /campaigns/{id}/scenes/{sid}/posts/{pid}/regenerate
GET    /campaigns/{id}/scenes/{sid}/posts/{pid}/alternates
POST   /campaigns/{id}/scenes/{sid}/posts/{pid}/alternates/{aid}/primary
POST   /campaigns/{id}/scenes/{sid}/posts/{pid}/alternates/{aid}/pin
DELETE /campaigns/{id}/scenes/{sid}/posts/{pid}/alternates/{aid}
```

WebSocket events:

```json
{ "type": "alternate_added", "post_id": "...", "alternate": {...} }
{ "type": "primary_switched", "post_id": "...", "from_alt_id": "...", "to_alt_id": "..." }
```

## Frontend

Chevron controls under the latest model post:

```
[GM] winifred catches your hand as you pour...
                                       ◀ 2 of 3 ▶ 📌 🔄 ✏️
```

- `◀ ▶` cycles alternates.
- `2 of 3` indicator.
- `📌` pin.
- `🔄` regenerate (creates a new alternate).
- `✏️` edit — opens `auxiliary-tasks.md`'s `rewrite_post`; the accepted
  output becomes a new alternate and is promoted to primary.

Cross-alternate side-by-side diff: data model supports it; UI is v2 polish.

## Audit log

```
[swipe]  campaign=... post=p_4710 added_alt=a_9012 (regenerate, same prompt)
[switch] campaign=... post=p_4710 from=a_9011 to=a_9012 (rewind+apply 14 deltas)
```

## Performance

- Regenerate latest: one normal-turn cost.
- Switch primary: rewind + reapply of one delta set (typically 5–20 deltas)
  → < 100ms.
