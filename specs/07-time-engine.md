# 07 — Time Engine

## Purpose

The Time Engine handles in-game time advancement. When the PC experiences elapsed time — a night's sleep, a week of travel, a month of training — the world also experiences it. NPCs do things, factions move, the weather changes, scheduled events happen.

The Time Engine operates **per-campaign**. Each campaign has its own clock and its own time-advancement history. Library characters that appear in multiple campaigns experience different timelines in each; a library character aging by a year in Campaign A does not affect Campaign B. NPC ticks resolve through the campaign's composition: the NPCs that exist for this campaign are the union of referenced library settings + campaign-local NPCs.

This is explicitly **not** a real-time background simulation. The Time Engine does nothing when the app is closed. It runs only when:

1. A scene narrates time passing
2. The player requests an explicit time skip
3. A mechanically-defined activity has a fixed duration (Ars Magica seasonal lab work, training montages, long journeys)
4. The player closes a scene that implicitly elapses time (e.g., "and then in the morning...")

## Responsibilities

- Maintain the current in-game date/time
- Detect time passage from extracted deltas and explicit requests
- Run NPC ticks for elapsed periods
- Run faction ticks for elapsed periods
- Run weather and atmospheric ticks
- Coordinate scheduled events (mail, holidays, NPC schedules)
- Coordinate with Mechanics for system-specific tick effects
- Produce a digest of what happened during elapsed time
- Roll forward calendar-based bookkeeping (commitments come due, foreshadowing goes stale, characters age)

## Non-responsibilities

- Does not advance time on its own without user/scene trigger
- Does not narrate offscreen events to the user (it produces structured digests; the LLM or UI presents them)
- Does not interpret rules (Mechanics does)

## Interface

```python
class TimeEngine(Protocol):
    def current(self) -> InGameTime: ...
    def calendar(self) -> SettingCalendar: ...

    async def advance(
        self,
        duration: Duration,
        reason: TimeAdvanceReason,
        scene_id: Optional[str] = None,
    ) -> TimeAdvanceResult: ...

    async def skip_to(
        self,
        target: InGameTime,
        reason: TimeAdvanceReason,
    ) -> TimeAdvanceResult: ...

    async def schedule_event(self, event: ScheduledEvent) -> EventId: ...
    async def cancel_event(self, event_id: EventId) -> None: ...
    async def upcoming_events(self, within: Duration) -> list[ScheduledEvent]: ...

    def subscribe_calendar(self, handler: Callable) -> SubscriptionId: ...
```

```python
@dataclass
class TimeAdvanceResult:
    from_time: InGameTime
    to_time: InGameTime
    duration: Duration
    npc_summaries: dict[CharacterId, NpcTickSummary]
    faction_summaries: dict[FactionId, FactionTickSummary]
    scheduled_events_triggered: list[ScheduledEvent]
    weather_changes: list[WeatherChange]
    commitments_due: list[Commitment]
    commitments_overdue: list[Commitment]
    mechanics_deltas: list[StateDelta]      # from Mechanics.time_tick calls
    digest: str                              # human-readable summary
```

## Triggers for time advancement

```python
class TimeAdvanceReason(Enum):
    EXPLICIT_USER       # user clicked "skip 1 week"
    SCENE_NARRATION     # extractor detected "a week passed"
    SCENE_BREAK         # new scene starts at a later time
    ACTIVITY_DURATION   # mechanics activity completed (e.g., training)
    SCHEDULED_EVENT     # a scheduled event triggers advancement
```

The Orchestrator's turn loop checks for time advancement after extraction. If the Extractor reports a `time_advances` delta, the Time Engine processes it. If the player requests a skip via UI, the Time Engine processes it directly.

## NPC tick architecture

The expensive part of time advancement is the NPC tick. For every "significant" offscreen NPC, the engine runs a simulation step covering the elapsed period.

### Significance filter

Not every NPC ticks. We filter by:

- **Always tick**: NPCs flagged "major" or "spotlight" by Characters; characters with open commitments to the PC; characters in the same household as the PC
- **Conditionally tick**: NPCs mentioned in the last 20 posts; NPCs with active plot threads
- **Background only**: NPCs in distant locations or who haven't appeared recently — these get faction-level treatment, not individual ticks

This caps cost. For a campaign with 50+ NPCs, maybe 8–15 actually get a tick on any given skip.

### Tick scope

For each ticked NPC, the engine produces a `NpcTickSummary`:

```python
@dataclass
class NpcTickSummary:
    character_id: str
    duration: Duration
    state_at_end: dict                  # mood, location, condition
    activities: list[str]               # what they did
    relationships_changed: list[RelationshipDelta]
    new_facts_about_them: list[Fact]    # things that became true
    secrets_kept: list[str]             # things they know now but PC doesn't
    next_intent: str                    # what they're trying to do next
```

The summary respects POV: the PC only learns what they would plausibly learn. Secret affairs, hidden plans, and offscreen revelations stay in `secrets_kept` until the fiction surfaces them.

### Tick generation

For each NPC tick, the engine produces a structured-output LLM call:

```
Inputs:
  - NPC card (full)
  - NPC's current state (location, mood, ongoing concerns)
  - NPC's relationships and obligations
  - Calendar events during the period
  - Setting-level context (weather, faction state, current events)
  - Other NPCs' tick results (if already computed, for consistency)

Schema:
  {
    "activities": [list of things they did],
    "location_at_end": "...",
    "mood_at_end": "...",
    "new_facts": [...],
    "relationship_changes": [...],
    "secrets_kept": [...],
    "next_intent": "...",
    "should_seek_pc": bool,           // do they want to find/contact the PC?
    "events_pc_would_witness": [...]  // things the PC would know
  }
```

Tick model is cheap (Haiku-tier or local). Ticks for unrelated NPCs run in parallel; ticks for related NPCs run sequentially with prior results in context (so two characters' versions of the same event don't contradict).

### Resolution coherence

When two NPCs interact during a tick (e.g., "winifred and vivienne spent the week planning a party"), the engine produces a single shared interaction record visible to both ticks. This prevents drift where each NPC's version of the same event diverges.

Implementation: before running individual ticks, run a "shared events" pass that produces inter-NPC events for the period. Then individual ticks reference these.

## Faction ticks

Factions get coarser ticks than NPCs. A faction tick covers:

- Goal progress (each faction has goals; ticks advance them based on resources and obstacles)
- Resource changes
- Leader actions
- Conflicts with other factions

Faction ticks run at month-level granularity even for week-scale skips, because faction movement is naturally slow.

## Weather and atmosphere

The Setting module owns weather data. The Time Engine consults it during advancement:

```python
weather_at_end = await self.setting.weather_for(
    location=current_location,
    in_game_time=to_time,
)
```

Weather is largely deterministic from the setting's climate model + a seeded RNG, so it's cheap and reproducible.

## Scheduled events

Events that should happen at specific in-game times:

- Holidays (Christmas, Passover, festivals defined by the setting)
- Recurring schedules ("the mail comes every Tuesday")
- One-off events ("winifred's birthday on day 312")
- Plot beats ("the king dies in three months")

Stored as `ScheduledEvent` rows. The Time Engine checks them during advancement and triggers them if their time falls in the elapsed window. Triggered events become state changes and can be surfaced as part of the digest.

## Commitment aging

The Continuity module tracks commitments (promises, foreshadowing, obligations). The Time Engine processes them during advancement:

- Commitments with `due_by < now` become "overdue"
- Commitments without due dates age toward "stale" (default: 6 months in-game)
- Overdue/stale commitments are surfaced in the digest for player attention

This is the mechanic that prevents foreshadowing from being silently forgotten.

## Mechanics integration

Every loaded Mechanics gets a `time_tick` call per character per advancement:

```python
for character in all_characters:
    mechanic_deltas = await self.mechanics.time_tick(character, duration)
    await self.state_store.apply_deltas(mechanic_deltas, source=time_advance_id)
```

Mechanic effects of time:
- Aging rolls (Ars Magica, Vampire long-form)
- Fatigue recovery
- Wound healing
- Training/study progress
- Vis source accumulation (Ars Magica)
- Spell preparation
- Long-term effects (curses, blessings) ticking down

## Activity-based advancement

When a character commits to a long activity (training, study, travel), it has a duration. The user can advance through it directly:

```
Player: "I want to train sword for the next two months."
[UI offers] "Advance 2 months and resolve training?"
[Click] → Time Engine advances, Mechanics resolves training, NPC ticks happen
```

This is the proper home for the kinds of long-form advancement Ars Magica wants to do. It's not background simulation — it's player-initiated.

## Digest generation

After advancement, the engine produces a human-readable digest. Two layers:

1. **Structured digest**: the raw data (events, NPC summaries, weather, scheduled events). Used by the State Store and Context Builder.
2. **Narrative digest**: an optional LLM-generated prose summary of the elapsed time, suitable for showing the player. ("Three weeks passed. The estate prepared for winter. winifred received a letter from her uncle in Sion. vivienne was unusually quiet at meals. The first snow fell on day 287.")

The narrative digest is shown when the player returns to the campaign post-advancement, before the next scene starts.

## Configuration

```yaml
time_engine:
  npc_tick_model: claude-haiku-4-5
  npc_tick_parallelism: 4
  significant_npc_filter:
    - role: major
    - has_open_commitment: true
    - in_household: true
    - in_recent_posts: 20
  faction_tick_resolution: month
  weather_seed_per_campaign: true
  digest_narrative: true                # generate prose digest
  digest_model: claude-haiku-4-5
  scheduled_event_pre_notice: 7d        # warn user 1 week before scheduled events
```

## Open questions

- **Reversibility.** Advancing time creates a lot of deltas. Undo is supported but expensive. Should there be a "checkpoint before advance" prompt for big skips? Probably yes.
- **NPC consistency over many ticks.** Drift accumulates. A drift check post-tick that flags wild deviations would help.
- **Time precision.** Do we track to the hour? The day? Configurable per campaign — some sagas care about minutes (heists), some care about seasons (Ars Magica).
- **In-fiction time vs. real time.** Some scenes happen in seconds of in-game time but take thousands of words to narrate. Do we track narration-time-budgets separately? Probably not for v1.
- **Cross-character time skips.** If the PC is unconscious and the campaign continues from an NPC POV, who is the Time Engine following? Multi-POV is out of scope for v1.
