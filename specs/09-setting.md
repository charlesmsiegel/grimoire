# 09 — Setting

## Purpose

The Setting module is the container for everything in a world. It owns:

- The setting directory (`data/library/settings/<id>/`) and its `setting.yaml` metadata
- File IO and CRUD for every entity kind within a setting: characters, items, locations, lore, factions, greetings
- Composition resolution: when a campaign references multiple settings with `include` filters, Setting walks the refs and returns the right entities
- The setting calendar (months, seasons, holidays)
- Procedural weather (per location, per campaign seed)
- Spatial queries (location adjacency, hierarchy)
- Lore keyword indexing for archive-tier triggers
- Faction state (campaign-scoped; library defines faction identity, campaign tracks current state)
- Setting versioning and version pinning

This is the module that handles the *world* in a campaign. Characters live in settings but have specific behaviors (voice, drift, tier, PC role) handled by `08-characters.md` — that's a behavior layer on top of Setting's storage.

For the on-disk file format details, see `18-library.md`. For sheet schemas attached by mechanics modules, see `06-mechanics.md`.

## Responsibilities

- Manage settings as library directories (create, list, get, delete, version)
- Read/write `setting.yaml` for setting metadata
- Manage all entity kinds within a setting (CRUD; file IO; index updates)
- Resolve entity reads through the read cascade (campaign-local → library refs with `include` filters → fail)
- Apply campaign-local overrides on top of library entities
- Generate weather and atmosphere procedurally
- Maintain the setting calendar
- Maintain location hierarchy and adjacency queries
- Manage faction state (campaign-scoped)
- Maintain lore keyword index for archive-tier triggers
- Promote campaign-local entities (any kind) into a setting on user action
- Surface cross-setting links by shared id ("orchard exists in mythic-europe and faerun")

## Non-responsibilities

- Does not own character-specific behaviors (voice anchors, drift detection, tier management, PC tracking) — those live in `08-characters.md` and layer over Setting's storage
- Does not own mechanical sheets — those are owned by mechanics modules (`06-mechanics.md`) and stored separately in campaign directories
- Does not own scenes (Scene Manager does; scenes reference Setting entities by ref)
- Does not advance time (Time Engine does; Setting provides weather, season, holiday)
- Is not a plugin (Setting is core architecture)

## What Setting actually stores

Setting is the storage layer for setting-internal content. For each entity kind, Setting handles:

| Kind | Library file path | Per-campaign override path | Per-campaign emergent path |
|---|---|---|---|
| characters | `<setting>/characters/<id>.md` | `campaigns/<c>/overrides/settings/<setting>/characters/<id>.yaml` | `campaigns/<c>/emergent/characters/<id>.md` |
| items | `<setting>/items/<id>.md` | `campaigns/<c>/overrides/settings/<setting>/items/<id>.yaml` | `campaigns/<c>/emergent/items/<id>.md` |
| locations | `<setting>/locations/<id>.md` | `campaigns/<c>/overrides/settings/<setting>/locations/<id>.yaml` | `campaigns/<c>/emergent/locations/<id>.md` |
| lore | `<setting>/lore/<id>.md` | `campaigns/<c>/overrides/settings/<setting>/lore/<id>.yaml` | `campaigns/<c>/emergent/lore/<id>.md` |
| factions | `<setting>/factions/<id>.md` | `campaigns/<c>/overrides/settings/<setting>/factions/<id>.yaml` | `campaigns/<c>/emergent/factions/<id>.md` |
| greetings | `<setting>/greetings/<id>.md` | — (greetings aren't overridden) | — (greetings aren't emergent) |

Characters get all the special character-behavior treatment from `08-characters.md`. Other kinds are handled by Setting directly with kind-specific helpers below.

## Setting metadata

`<setting>/setting.yaml`:

```yaml
id: wod-london
name: "London by Night"
description: ...
tags: [wod, vampire, urban, gothic]
genre: ...

calendar:
  epoch: "2024-01-01"
  months:
    - { name: January, days: 31 }
    # ...
  days_per_week: 7
  week_day_names: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]
  seasons: [...]
  holidays:
    - { name: All Hallows Eve, month: 10, day: 31, description: "..." }

atmosphere:
  default_register: "low, urgent, mannered in Elysium"
  default_palette: "neon and rain, oxblood and tarnished gold"

defaults:
  starting_location: elysium
  default_style_guide_id: gothic-horror
  default_image_preset_id: oil-painting

version: 3
```

## Location schema and behaviors

Location frontmatter:

```yaml
---
id: camden-market
name: Camden Market
parent_id: camden                       # spatial hierarchy
kind: outdoor                            # city | building | room | region | outdoor
aliases: [the market]
tags: [camden, nightlife, neutral-ground]
climate_zone: temperate-oceanic
indoor: false
coordinates: {x: 412, y: 309}
permanent_features:
  - Stall rows along the canal
connections:
  - to: chalk-farm
    via: street
    duration_min: 8
typical_occupants: []
---
```

```python
@dataclass
class Location:
    setting_id: str
    id: str
    name: str
    parent_id: Optional[str]
    kind: LocationKind
    aliases: list[str]
    tags: list[str]
    climate_zone: Optional[str]
    indoor: bool
    coordinates: Optional[Coords]
    permanent_features: list[str]
    connections: list[LocationConnection]
    typical_occupants: list[EntityRef]
    description: str
    body: str

@dataclass
class LocationState:                    # campaign-scoped, SQLite
    location_ref: str
    campaign_id: str
    branch_id: str
    weather: Optional[Weather]
    time_of_day: Optional[str]
    occupants: list[EntityRef]
    condition: str
    transient_features: list[str]
    updated_at_turn: Optional[str]
```

Hierarchy is within a setting; cross-setting `parent_id` is not supported (would require cross-library refs). The Context Builder uses hierarchy to find adjacent context — when in Camden Market, Setting provides parent "Camden" compressed plus connected locations.

If a location carries a mechanical layer (a WoD Node, an Ars Magica Covenant), that's a separate sheet file owned by the active mechanics module — see `06-mechanics.md`. The library card describes the narrative; the sheet describes the mechanics.

## Item schema and behaviors

Items as a first-class entity kind are new in this architecture. Named items (a famous weapon, a cursed locket, an enchanted ring) get cards. Generic items in play don't need cards.

```yaml
---
id: the-camden-blade
name: The Camden Blade
aliases: [the blade]
tags: [weapon, magical, claimed-by-the-prince]
provenance: "Forged 1672 by a now-extinct line of Tremere..."
current_holder: prince-of-london
---

# The Camden Blade

A single-edged knife in a fishskin sheath...
```

```python
@dataclass
class Item:
    setting_id: str
    id: str
    name: str
    aliases: list[str]
    tags: list[str]
    provenance: Optional[str]
    current_holder: Optional[EntityRef]  # may be a character ref, library or campaign-local
    description: str
    body: str
```

Mechanical layer (weapon stats, fetish properties, enchantment effects) is owned by the active mechanics module via item sheets. Setting stores the narrative card; sheets live separately.

## Faction schema and behaviors

```yaml
---
id: the-camarilla
name: The Camarilla
kind: vampire-sect
base_location: elysium
leaders: [prince-of-london]
allies: []
rivals: [the-sabbat, the-anarchs]
tags: [vampire, traditionalist, hierarchical]
---
```

```python
@dataclass
class Faction:
    setting_id: str
    id: str
    name: str
    kind: str
    base_location: Optional[EntityRef]
    leaders: list[EntityRef]
    members: list[EntityRef]
    allies: list[FactionRef]
    rivals: list[FactionRef]
    tags: list[str]
    description: str
    body: str

@dataclass
class FactionState:                      # campaign-scoped, SQLite
    faction_ref: str
    campaign_id: str
    branch_id: str
    goals: list[FactionGoal]
    resources: dict
    current_focus: str
    public_perception: str
    secrets: list[str]
```

Faction state changes during time ticks; the Time Engine consults mechanics if any. Setting tracks faction identity (library) and state (campaign-scoped).

## Lore schema and keyword triggers

```yaml
---
id: the-masquerade
title: The Masquerade
tags: [law, vampire, fundamental]
keywords: [masquerade, breach, mortal, exposure]
related_factions: [the-camarilla]
related_characters: []
secrecy: common-knowledge-among-kindred
---
```

```python
@dataclass
class LoreEntry:
    setting_id: str
    id: str
    title: str
    body: str
    tags: list[str]
    keywords: list[str]                  # FTS keyword triggers
    related_locations: list[EntityRef]
    related_factions: list[FactionRef]
    related_characters: list[EntityRef]
    secrecy: SecrecyLevel                # public | common-knowledge | restricted | secret
```

Lore `keywords` trigger archive-tier inclusion when they appear in recent posts. The Context Builder calls `Setting.lore_by_keyword(...)` to find triggered entries. The `secrecy` field controls visibility — secret lore is available to the model but hidden from player-facing views by default.

## Greetings

```yaml
---
id: elysium-opening
name: "Elysium Opening"
tags: [introduction, political, no-violence]
starting_location: elysium
starting_time: "2024-10-31T22:00:00"
present_characters: [prince-of-london, alistair-hyde-smythe]
pov_character: alistair-hyde-smythe
mood: "tense civility, jasmine-scented smoke"
season_constraint: autumn               # optional
---

# Elysium Opening
...
```

```python
@dataclass
class Greeting:
    setting_id: str
    id: str
    name: str
    tags: list[str]
    starting_location: EntityRef
    starting_time: datetime
    present_characters: list[EntityRef]
    pov_character: Optional[EntityRef]
    mood: str
    season_constraint: Optional[str]
    body: str                           # opening narration
```

When a campaign is created with a greeting selected, Setting hands the greeting to the Orchestrator, which seeds scene 1: time set, location set, present cast in place, opening narration appended as the first post.

Greetings aren't overridden or emergent — they're library-only opening scenarios.

## Resolved entities

Other modules don't read raw scoped rows; they call resolution APIs:

```python
@dataclass
class ResolvedEntity:
    setting_id: Optional[str]            # None if campaign-local emergent
    kind: str
    id: str
    # ... entity-kind-specific fields ...
    current_state: Optional[StateData]   # campaign-scoped state if applicable
    source_chain: list[ResolutionSource] # for observability
    overrides_applied: list[str]
```

The State Store implements the cascade; the Setting module wraps it for convenience.

## Cross-setting variant lookup

Same asset id across settings = variants (see `18-library.md`). Setting exposes:

```python
async def cross_setting_lookup(
    self,
    asset_id: str,
    kind: str,                          # 'location', 'item', 'lore', 'faction'
    exclude_setting: Optional[str] = None,
) -> list[LibraryEntity]:
    # SELECT * FROM library_index WHERE kind = ? AND asset_id = ?
```

Used by the Frontend to surface "this place also exists in: ...". No `family_id` field; the id is the link.

## Weather

Procedural, seeded per campaign, reproducible:

```python
async def weather_for(
    self,
    location_ref: str,
    in_game_time: InGameTime,
    campaign_id: str,
) -> Weather:
    """Uses location.climate_zone, setting calendar season, per-campaign RNG seed.
    Same inputs = same weather. Forks preserve."""
```

Player can override (the Extractor catches "and it began to rain" and writes a campaign-local weather override).

## Calendar

Loaded from `setting.yaml`. The Time Engine uses it for advancement. If a campaign references multiple settings (crossover) with conflicting calendars, the user picks at composition time.

```python
def calendar_for(self, campaign_id: str) -> SettingCalendar: ...
def season_for(self, when: InGameTime, campaign_id: str) -> Season: ...
def holiday_at(self, when: InGameTime, campaign_id: str) -> Optional[Holiday]: ...
```

## Composition: `include` filters

A campaign can include only some entity kinds from a referenced setting. The composition declares:

```yaml
composition:
  settings:
    - id: faerun
      priority: 1
      include: [characters]              # only the cast
    - id: wod-nyc
      priority: 2
      include: [items, locations, lore]  # the world and its weapons
  mechanics: wod-mechanics
```

When `list_locations_for_campaign(campaign_id)` is called, Setting consults `campaign_setting_refs`, walks each ref in priority order, applies the `include` filter, and returns the merged list. Same for items, lore, factions, greetings. (Characters are listed via the Characters module, which delegates to Setting for storage and adds character-specific resolution.)

## Promotion: campaign-local → library

```python
async def promote_to_library(
    self,
    campaign_id: str,
    kind: str,                          # 'item', 'location', 'lore', 'faction'
    campaign_entity_id: str,
    target_setting_id: str,
) -> str:                                # returns library path
    # 1. Read the emergent entity from campaign-local
    # 2. Render to markdown + YAML frontmatter
    # 3. Write to <library>/settings/<target>/<kind>/<id>.md
    # 4. Watcher picks it up; library_index gains a row
    # 5. Replace emergent record with reference; campaign keeps using it
```

Character promotion is the same operation but routed through the Characters module (which handles character-specific concerns like family/variant linking by id).

## Interface

```python
class Setting(Protocol):
    # Setting management
    async def list_settings(self) -> list[SettingMeta]: ...
    async def get_setting(self, setting_id: str) -> SettingMeta: ...
    async def create_setting(self, id: str, meta: SettingMeta) -> SettingMeta: ...
    async def update_setting_meta(self, setting_id: str, patch: dict) -> SettingMeta: ...
    async def delete_setting(self, setting_id: str) -> None: ...
    async def fork_setting(self, source_id: str, new_id: str) -> SettingMeta: ...

    # Generic per-kind CRUD (characters delegated to 08-characters; everything else here)
    async def list_in_setting(self, setting_id: str, kind: str) -> list[LibraryEntity]: ...
    async def get(self, setting_id: str, kind: str, id: str) -> LibraryEntity: ...
    async def create(self, setting_id: str, kind: str, entity: dict) -> LibraryEntity: ...
    async def update(self, setting_id: str, kind: str, id: str, patch: dict) -> LibraryEntity: ...
    async def delete(self, setting_id: str, kind: str, id: str) -> None: ...

    # Per-campaign resolution
    async def resolve(self, entity_ref: str, campaign_id: str) -> ResolvedEntity: ...
    async def list_for_campaign(
        self,
        campaign_id: str,
        kind: str,
        filter: dict = {},
    ) -> list[ResolvedEntity]: ...

    # Spatial (locations)
    async def adjacent_locations(self, ref: str, campaign_id: str) -> list[ResolvedEntity]: ...
    async def path_between(self, a: str, b: str, campaign_id: str) -> list[LocationConnection]: ...
    async def locations_within(self, parent_ref: str, campaign_id: str, depth: int = 1) -> list[ResolvedEntity]: ...

    # Cross-setting variant lookup
    async def cross_setting_lookup(
        self,
        asset_id: str,
        kind: str,
        exclude_setting: Optional[str] = None,
    ) -> list[LibraryEntity]: ...

    # Lore
    async def search_lore(self, query: str, campaign_id: str) -> list[LoreEntry]: ...
    async def lore_by_keyword(self, keyword: str, campaign_id: str) -> list[LoreEntry]: ...

    # Factions
    async def faction_state(self, ref: str, campaign_id: str, branch_id: str) -> FactionState: ...
    async def update_faction_state(
        self,
        ref: str,
        campaign_id: str,
        branch_id: str,
        patch: dict,
    ) -> None: ...

    # Greetings
    async def list_greetings(self, setting_id: str) -> list[Greeting]: ...
    async def get_greeting(self, setting_id: str, id: str) -> Greeting: ...

    # Weather
    async def weather_for(
        self,
        location_ref: str,
        when: InGameTime,
        campaign_id: str,
    ) -> Weather: ...
    async def override_weather(
        self,
        location_ref: str,
        when: InGameTime,
        weather: Weather,
        campaign_id: str,
        source: str,
    ) -> None: ...

    # Calendar
    def calendar_for(self, campaign_id: str) -> SettingCalendar: ...
    def season_for(self, when: InGameTime, campaign_id: str) -> Season: ...
    def holiday_at(self, when: InGameTime, campaign_id: str) -> Optional[Holiday]: ...

    # Composition (delegated to State Store; surfaced here for convenience)
    async def get_composition(self, campaign_id: str) -> Composition: ...
    async def upgrade_setting_ref(
        self,
        campaign_id: str,
        setting_id: str,
    ) -> UpgradeReport: ...

    # Promotion (non-character kinds; characters go through Characters module)
    async def promote_to_library(
        self,
        campaign_id: str,
        kind: str,
        campaign_entity_id: str,
        target_setting_id: str,
    ) -> str: ...
```

## Configuration

```yaml
setting:
  weather:
    enabled: true
    seed_per_campaign: true
    model: rule_based
  lore:
    keyword_match: true
    keyword_min_length: 4
    max_lore_in_archive: 5
  atmosphere_auto_generate: true
  composition:
    multiple_calendars_policy: pick     # pick | merge_warn | error
```

## Open questions (deferred)

- **Map UI.** Worth having? v2 candidate; schema supports coordinates and connections.
- **Travel mechanics.** Setting handles description; mechanics handles mechanical effects.
- **Procedural location generation.** "I enter a tavern" — auto-generate one? Yes via LLM, campaign-local emergent, user review.
- **Cross-setting lore sharing.** Some lore appears across variants (a religion in two settings). v1: duplicate; v2: lore families if patterns emerge.
- **Player vs. model lore.** `secrecy_level` handles this; Frontend hides player-secret lore.
- **Crossover calendars.** User picks at composition time when multiple settings disagree.
- **Setting forks via directory copy.** Easy and supported; UI provides a fork action.
