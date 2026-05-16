# 20 — Transient State

## Purpose

Transient state is the ephemeral, per-entity per-turn information that changes too often to live in files but is too valuable to lose: a character's current mood, intent, posture, focus, and internal thought; a location's ambient mood and noteworthy detail; a faction's alert level; a scene's emotional temperature. It's the connective tissue between the narrative now and the persistent state model.

The overview already declares this category exists (see `00-overview.md` line 144: *"Transient character/location/faction state (mood, location, intent — too noisy for files)"*). This spec defines its data model, lifecycle, sourcing, decay, surfacing rules, and the interaction with formal facts.

Transient state powers the HUD's Present Cast widget (`19-scene-hud.md`), the inline thought bubbles in the scene pane (`14-frontend.md`), and the Context Builder's spotlight tier (`02-context-builder.md`). It is one of three forms of state in Grimoire:

| Form | Where | Update frequency | Provenance |
|---|---|---|---|
| Static entity data | Markdown + YAML files | Rare (user edits) | Library SSOT or campaign-local override |
| **Transient state** | **SQLite** | **Every turn** | **Extractor, user, mechanics callbacks** |
| Formal facts | SQLite (`facts` table) | When narrative commits something significant | Extractor with high confidence |

The three forms are connected. Transient state is the rough draft; formal facts are the canonical record. Promotion from transient to fact is a user-or-extractor action with audit.

## Responsibilities

- Maintain a fast, queryable store of per-turn entity state
- Accept writes from Extractor (default source), user (HUD edits), and Mechanics (post-roll callbacks)
- Apply per-field decay so stale state doesn't accumulate or mislead
- Provide a read interface for Context Builder, HUD, and other modules
- Surface transient state in the scene pane (thought bubbles, mood badges) per privacy rules
- Support promotion of a transient observation into a formal fact
- Never overwrite a formal fact (transient is weaker than facts; conflicts are surfaced, not silently resolved)

## Non-responsibilities

- Does not store static entity data (Setting, Characters do — via files)
- Does not store formal facts (Continuity does)
- Does not perform extraction (Extractor does); receives writes from the Extractor
- Does not decide rendering rules (HUD config and Frontend privacy settings do); provides the data with provenance flags

## What "transient" means

A transient value:
- Changes turn-to-turn or scene-to-scene
- Is observable in prose but not necessarily promised forever
- Has soft truth value: "winifred is currently guarded" is true now; she may relax in five posts
- Should never block or contradict a formal fact

A non-transient value:
- "winifred has been hiding letters from her uncle" — that's a fact; commit it to Continuity
- "winifred's hair color is brown" — that's static entity data; in her character card
- "winifred has 3 Willpower right now" — that's a mechanical sheet value; in her WoD sheet

When the boundary is unclear, the Extractor proposes transient by default and offers promotion-to-fact as a follow-up action.

## Data model

### Schema fields

Each transient row carries:
- `campaign_id`: campaign scope (transient is always campaign-local)
- `entity_kind`: `character` | `location` | `faction` | `scene`
- `entity_id`: the entity's id (resolved via library cascade)
- `field`: a named slot (see below)
- `value`: the value (string, number, enum)
- `provenance`: who wrote it (`extractor:auto` | `extractor:reviewed` | `user:hud` | `user:edit` | `mechanics:<module-id>`)
- `source_post_id`: which post produced this (for traceability)
- `confidence`: 0.0–1.0 (1.0 for user edits; Extractor confidence for auto)
- `created_at`: timestamp
- `expires_at`: optional decay deadline
- `superseded_by`: optional row id (when replaced)

### Built-in fields per entity kind

**Character:**
- `mood` — short string or emoji ("guarded", "elated", "🥶")
- `intent` — one sentence ("hide her uncle's letter before julian sees it")
- `current_action` — one sentence ("fastening her cloak by the door")
- `posture` — optional ("seated, hands folded")
- `internal_thought` — 1–3 sentences (governed by privacy rules — see below)
- `focus_of_attention` — what the character is paying attention to right now
- `relationship_tone_toward_pc` — enum or -3..+3 ("hostile" | "wary" | "neutral" | "warming" | "trusting" | "intimate")
- `energy_level` — narrative low/normal/high (mechanics modules may override with mechanical fatigue values)

**Location:**
- `ambient_mood` — short label ("tense", "festive", "mournful", "alert")
- `noteworthy_detail` — one sentence ("smoke still rising from the broken hearth")
- `occupancy_summary` — quick label ("empty", "few patrons", "packed")

**Faction:**
- `alert_level` — enum ("dormant" | "watchful" | "mobilizing" | "active" | "open conflict")
- `internal_mood` — short label ("fractious", "united", "fearful")

**Scene:**
- `emotional_temperature` — short label ("simmering", "warm", "cold", "explosive")
- `dominant_mood` — what's in the air
- `pacing` — narrative tempo cue ("languid", "tense", "rushed")

These fields are the *default* set; the schema supports arbitrary additional keys as `transient_extra.<key>`. Mechanics modules and user power-tools can add fields without a schema migration; the HUD only renders the keys it knows about, with a generic fallback.

### Storage

SQLite, four tables, plus an audit table:

```sql
CREATE TABLE transient_character_state (
  id INTEGER PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  character_id TEXT NOT NULL,
  field TEXT NOT NULL,
  value TEXT,                       -- JSON-encoded for non-scalars
  provenance TEXT NOT NULL,
  source_post_id TEXT,
  confidence REAL NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER,
  superseded_by INTEGER,
  UNIQUE (campaign_id, character_id, field, created_at)
);

CREATE INDEX idx_tcs_current
  ON transient_character_state (campaign_id, character_id, field)
  WHERE superseded_by IS NULL;
```

Analogous tables for `transient_location_state`, `transient_faction_state`, `transient_scene_state`.

Reading "the current mood of winifred in campaign by-night-london" is one indexed query. Writing a new value supersedes the previous row (sets `superseded_by` on the old row and inserts a new row) — preserves history for audit, allows undo. Decay sets `expires_at` and treats expired rows as null in current-value queries; expired rows are kept until a periodic vacuum runs.

## Sources

Transient state is written by three sources, with priority on conflict:

### 1. Extractor (default source)

After every model response, the Extractor proposes transient updates as part of `ExtractionResult.deltas` (see `04-extractor.md`). Examples:
- "winifred's hand twitched at the mention of Sion" → `mood = guarded`, `internal_thought = "Does julian know?"`
- "julian stepped back" → `posture = stepped back from winifred`

Confidence is propagated. Above auto-apply threshold → written directly. Below → queued for review.

### 2. User edits (highest priority)

Through the HUD or character detail:
- Click mood emoji → quick mood picker
- Inline edit current_action
- Manually set internal_thought

User edits are confidence 1.0, provenance `user:hud` or `user:edit`. They cannot be overwritten by Extractor without flagging as a contradiction.

### 3. Mechanics callbacks

Mechanics modules can write transient state via the Mechanics API:
- After a wound roll: `mood = pained`, `posture = wincing`
- After a fatigue check: `energy_level = drained`
- After an emotional roll (e.g., Frenzy in WoD): `mood = berserk`

Provenance `mechanics:<module-id>`. Confidence 1.0 (mechanics is authoritative for the things mechanics governs).

### Conflict resolution

When two sources disagree within the same turn:
- User > Mechanics > Extractor
- The losing write is preserved with `superseded_by` pointing to the winner
- If User and Mechanics disagree, surface a conflict to the user (rare; user can pick)

## Lifecycle and decay

Transient state goes stale. A character's "mood: guarded" from 30 posts ago shouldn't keep haunting the Context Builder.

### Per-field decay

Each field has a default decay policy:

| Field | Default lifetime |
|---|---|
| `mood` | 10 posts or 1 in-game hour, whichever is longer |
| `intent` | 5 posts or until next scene |
| `current_action` | 1 post (replaced each turn) |
| `posture` | 3 posts |
| `internal_thought` | 1 post (one-shot, then null unless renewed) |
| `focus_of_attention` | 2 posts |
| `relationship_tone_toward_pc` | scene-scoped; persists across scenes only if reinforced |
| `energy_level` | until next sleep / rest event from Time Engine |
| location.`ambient_mood` | scene-scoped |
| location.`noteworthy_detail` | scene-scoped |
| faction.`alert_level` | persists until changed (no decay; factions are slow) |
| scene.`emotional_temperature` | per-scene only |

Decay is computed lazily on read: a query returns null for expired fields. The Context Builder treats nulls as "no current state — unknown" and the HUD shows an empty slot.

Decay policy is overridable per-campaign and per-mechanics module. A mechanics module can declare custom decay (e.g., WoD's "still in torpor" persists indefinitely).

### Persistence across scenes

`mood` and `intent` carry across scene boundaries by default — characters don't forget their feelings between scenes. The user can configure scene-boundary reset for specific fields (e.g., always reset `current_action` and `focus_of_attention` at scene start).

When a character is not present in the current scene, their transient state freezes (no decay clock advance) — it resumes when they return.

### Reset

Three reset triggers:
- Scene end (per-field configurable; defaults: reset `current_action`, `focus_of_attention`, `scene.*`)
- Time skip (per-field; defaults: reset `mood`, `intent`, `posture` after a 24h+ skip)
- User "fresh start" — manual reset of a character's transient state from the character detail view

Resets are logged as deltas with provenance `user:reset` or `system:scene-end`.

## Internal thoughts and privacy

`internal_thought` is the most narratively powerful and most privacy-sensitive transient field. SillyTavern RPG-companion renders these as floating bubbles next to character avatars; Grimoire follows that pattern but governs visibility carefully.

### Per-character privacy flags

On each character's frontmatter:

```yaml
privacy:
  internal_thoughts:
    surface_in_hud: true        # show in Present Cast widget
    surface_inline: true        # show as bubble next to last action in scene pane
    surface_in_context: true    # include in Context Builder spotlight
```

A "mystery cult leader" character can have all three set to false. The Extractor will still record her thoughts (so the model can use them when she's spotlighted), but they won't be displayed to the player.

### Per-campaign privacy profile

Two presets:
- **Solo / co-author mode** (default): all NPC thoughts surfaced. The player is co-authoring an omniscient narrative.
- **GM-mystery mode**: NPC thoughts surfaced in HUD only via a "peek" toggle that records when used. The player can opt in to a mystery and choose to break their own fourth wall.

The PC's own thought is always surfaced to that PC's player; thoughts of the OTHER PCs (in shared-scene campaigns) follow the campaign's privacy profile.

### POV filtering

The Frontend's POV mode (`14-frontend.md`) — when the user is reading the scene as their PC rather than as omniscient narrator — automatically hides all NPC thoughts regardless of per-character flags. Toggle is one click.

## Interaction with formal facts

Transient state is *weaker* than facts. Three principles:

### Transient never overwrites a fact

If a fact says "winifred trusts julian completely" and a turn's prose suggests "winifred's intent: deceive julian," the Extractor:
1. Records the transient `intent = deceive julian` (still useful for the next turn's voice)
2. Flags a contradiction with the fact for review
3. Does **not** silently mutate the fact

The user resolves: keep the fact (transient was misread), update the fact (the relationship has changed), or merge.

### Promotion path

A transient value can be promoted to a fact via:

- **User action**: "Commit to facts" button on a transient field — opens the fact editor pre-filled
- **Extractor escalation**: when a transient value persists across 5+ posts and is reinforced by new evidence, the Extractor proposes promotion in the review queue
- **Explicit prose cue**: "From this moment on, ..." — Extractor recognizes commit-cues and proposes promotion

Promotion writes a row to Continuity's `facts` table and supersedes the transient row.

### Demotion / re-narration

If the user retcons a scene that established a fact, the fact's source can become invalid. Continuity handles demotion / archival of facts; transient state mirrors that by clearing any transient rows sourced from the affected posts.

## Read interface

```python
class TransientState(Protocol):
    async def get(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        field: Optional[str] = None,
    ) -> dict[str, TransientValue] | TransientValue: ...

    async def set(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        field: str,
        value: Any,
        provenance: Provenance,
        confidence: float = 1.0,
        source_post_id: Optional[str] = None,
    ) -> TransientValue: ...

    async def clear(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        field: Optional[str] = None,   # None = clear all
        reason: str = "user:reset",
    ) -> None: ...

    async def history(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        field: str,
        limit: int = 20,
    ) -> list[TransientValue]: ...
```

```python
@dataclass
class TransientValue:
    field: str
    value: Any
    provenance: str
    source_post_id: Optional[str]
    confidence: float
    created_at: datetime
    expires_at: Optional[datetime]
    is_current: bool                  # false if superseded or expired
```

The Context Builder calls `get(...)` with no field arg to fetch a character's full current transient bundle, then includes the populated fields in the spotlight tier as a compact stanza:

```
winifred Allard — current state:
  mood: guarded
  intent: hide her uncle's letter before julian sees it
  action: fastening her cloak by the door
  thinking: "Does he know about Sion?"
```

## Audit and observability

Every transient write is a row; supersession is recorded; no destructive update. The audit log (`16-observability.md`) joins transient rows to their source posts and extracts a "what did the system believe about winifred's mood over the last hour" timeline.

The character detail view has a "transient history" tab showing the time-series of each field with provenance — useful for debugging Extractor performance and reviewing drift.

## Integration points

| Module | Interaction |
|---|---|
| Extractor | Default writer; produces transient updates from prose |
| Characters | Reads transient state for voice anchor decoration; runs drift checks that consider mood persistence |
| Setting | Owns the location-level subset (ambient_mood, noteworthy_detail) |
| Continuity | Receives promotions to facts; surfaces contradictions with transient |
| Context Builder | Reads current transient state into spotlight tier |
| Scene Manager | Resets scene-scoped transient on scene end; reads scene.emotional_temperature for HUD |
| HUD (`19-scene-hud.md`) | Renders mood/intent/action/thought; routes edits through this module |
| Mechanics | Optional writer (post-roll callbacks) |
| Time Engine | Triggers decay clock advance |
| Observability | Audits writes; supports time-series queries |

## Performance

- `get` for one entity's full bundle: < 5ms (indexed)
- `set`: < 10ms (insert + index)
- Bulk `get` for HUD aggregation (present cast of 5 characters × 8 fields): < 50ms
- Decay vacuum: runs every N hours in background; never blocks reads

A campaign accumulates ~50-200 transient rows per turn (across all entities). At 1000 turns that's 100k rows — trivial for SQLite with the index above.

## Failure modes

- **Extractor proposes a transient update that conflicts with a fact** — flagged, not applied to facts; transient still recorded for traceability
- **User sets a value the Extractor then keeps overriding** — the user-source write has higher priority; Extractor proposals against a user-written field are routed to review for the next 3 posts (debouncing)
- **Decay clock goes backward (time retcon)** — vacuum runs on retcon to clear / restore expired rows as appropriate
- **Mechanics callback writes to a field the user just set** — per the conflict-resolution rule, the user's write wins; the mechanics proposal is surfaced as a conflict for the user to pick. If the mechanics module truly needs authoritative state (e.g., a wound's posture effect), that state belongs on the mechanics sheet — transient state is narrative and respects user > mechanics unconditionally.

## Open questions

- **Field schema vs free-form** — the built-in fields are listed; should mechanics modules be able to *declare* additional canonical fields (with their own decay policies) rather than relying on the `transient_extra.*` escape hatch? Probably yes; manifest declaration is easy to add.
- **History UI cost** — the audit history per field can grow long; cap rendering to last 20 with "load more"
- **Cross-character thoughts** — sometimes the same beat updates two characters' thoughts in tandem ("they exchanged a look"). The Extractor should emit paired updates; this spec doesn't define a "linked transient" type but the data model accommodates it via shared source_post_id.
- **Mechanics modules wanting their own field schemas** — e.g., wod-mechanics may want a richer `frenzy_state` field. The `transient_extra.*` escape hatch covers it; if it becomes common, promote to manifest declaration.
- **Transient-to-prompt rendering format** — the compact stanza above is one option; alternative is interleaving with the character card. Worth a few A/B trials with real campaigns.
- **Privacy auditing** — if a screenshot/export includes transient data, the privacy filter must be applied. Worth a tag on every export path.
- **Read-through cache** — at high turn frequency, hitting SQLite per-widget per-event is fine but a small in-process cache (TTL 1s) reduces load if mechanics widgets re-query a lot.
