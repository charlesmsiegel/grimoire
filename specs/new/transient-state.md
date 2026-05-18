# Transient State

Per-entity per-turn ephemeral state: mood, intent, current action,
posture, internal thought, focus, relationship tone, ambient mood, alert
level, emotional temperature. Lives in SQLite (too noisy for files, too
valuable to lose) and powers the HUD's present-cast widget, the scene
pane's thought bubbles, and the Context Builder's spotlight tier.

Transient state is *weaker* than formal facts. Conflicts are flagged, not
silently resolved.

## Storage

One table per entity kind: `transient_character_state`,
`transient_location_state`, `transient_faction_state`,
`transient_scene_state`. Each row:

- `campaign_id`, `entity_id`, `field`, `value` (JSON for non-scalars)
- `provenance` — `extractor:auto` | `extractor:reviewed` | `user:hud` |
  `user:edit` | `mechanics:<module-id>`
- `source_post_id`, `confidence`, `created_at`, `expires_at`,
  `superseded_by`

Index on `(campaign_id, entity_id, field)` filtered by
`superseded_by IS NULL` for fast current-value reads. Writes supersede
(set `superseded_by` on the old row + insert new) — preserves history,
supports undo. Expired rows return null in current-value reads and are
vacuumed in background.

## Built-in fields

**Character:** `mood`, `intent`, `current_action`, `posture`,
`internal_thought`, `focus_of_attention`, `relationship_tone_toward_pc`,
`energy_level`.

**Location:** `ambient_mood`, `noteworthy_detail`, `occupancy_summary`.

**Faction:** `alert_level`, `internal_mood`.

**Scene:** `emotional_temperature`, `dominant_mood`, `pacing`.

`transient_extra.<key>` escape hatch lets mechanics modules add fields
without schema migration. Manifest declaration for canonical mechanics
fields is a later polish.

## Write sources and priority

User > Mechanics > Extractor. The losing write is preserved (not
discarded) via `superseded_by`. User vs mechanics disagreement surfaces a
conflict for the user; rare but real.

Extractor proposes transient updates as part of `ExtractionResult.deltas`
(see `04-extractor.md`). Above auto-apply threshold → written; below →
queued for review. User edits and mechanics callbacks both write at
confidence 1.0.

## Decay

Per-field default lifetime:

| Field | Default |
|---|---|
| `mood` | 10 posts or 1 in-game hour |
| `intent` | 5 posts or until next scene |
| `current_action` | 1 post |
| `posture` | 3 posts |
| `internal_thought` | 1 post |
| `focus_of_attention` | 2 posts |
| `relationship_tone_toward_pc` | scene-scoped; persists across scenes only if reinforced |
| `energy_level` | until next sleep/rest from Time Engine |
| location.`ambient_mood`, `noteworthy_detail` | scene-scoped |
| faction.`alert_level` | persists until changed |
| scene.* | per-scene only |

Decay is computed lazily on read. Overridable per-campaign and per-mechanics
module. When a character is not in the current scene, their decay clock
freezes and resumes when they return.

Reset triggers: scene end (configurable per field), time skip ≥ 24h
(resets mood/intent/posture by default), manual "fresh start".

## Internal thoughts and privacy

Per-character frontmatter:

```yaml
privacy:
  internal_thoughts:
    surface_in_hud: true
    surface_inline: true
    surface_in_context: true
```

Per-campaign presets: "Solo / co-author mode" (all surfaced) vs
"GM-mystery mode" (HUD peek-only, recorded). POV mode auto-hides NPC
thoughts. PC thoughts always visible to that PC's player.

## Promotion to facts

Three paths:
- User clicks "Commit to facts" on a transient field.
- Extractor escalates when a value persists across 5+ posts with new
  evidence.
- Explicit prose cue ("From this moment on, …") → Extractor proposes.

Promotion writes a `facts` row in Continuity and supersedes the transient
row. Transient never overwrites a fact — contradictions go to review.

## Read interface

```python
class TransientState(Protocol):
    async def get(campaign_id, entity_kind, entity_id, field=None) -> ...
    async def set(campaign_id, entity_kind, entity_id, field, value, *,
                  provenance, confidence=1.0, source_post_id=None) -> ...
    async def clear(campaign_id, entity_kind, entity_id, field=None,
                    reason="user:reset") -> None
    async def history(campaign_id, entity_kind, entity_id, field,
                      limit=20) -> list[TransientValue]
```

Context Builder calls `get` with no field arg to fetch the full current
bundle for an entity, then renders a compact stanza in the spotlight
tier:

```
winifred Allard — current state:
  mood: guarded
  intent: hide her uncle's letter before julian sees it
  action: fastening her cloak by the door
  thinking: "Does he know about Sion?"
```

## Performance targets

- Single-entity bundle read: < 5ms.
- Write: < 10ms.
- Bulk HUD aggregation (5 characters × 8 fields): < 50ms.
