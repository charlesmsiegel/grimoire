# 18 — Library

## Purpose

The Library holds the content campaigns play with: settings (each containing characters, items, locations, lore, factions, greetings) and top-level style guides and image presets. Library content lives as markdown + YAML files on disk; the State Store indexes them into SQLite for queries, but the files are the authoritative source.

The Library is one of three scopes in Grimoire's architecture:
- **Library** (this spec): user-authored content, files on disk
- **Campaign-local** (`03-state-store.md`): per-campaign play state, mix of files and SQLite
- **Code (external)** (`06-mechanics.md`, `15-plugins.md`): Python packages installed under `data/mechanics/` and `data/plugins/`

This spec is foundational. Specs 00, 03, 08, 09, and 14 are written assuming the model defined here.

## Library file layout

```
data/library/
├── settings/
│   ├── wod-london/
│   │   ├── setting.yaml              # metadata, calendar, atmosphere
│   │   ├── characters/
│   │   │   ├── alistair-hyde-smythe.md
│   │   │   ├── prince-of-london.md
│   │   │   └── ...
│   │   ├── items/
│   │   │   ├── the-camden-blade.md
│   │   │   └── ...
│   │   ├── locations/
│   │   │   ├── camden-market.md
│   │   │   ├── mayfair.md
│   │   │   └── ...
│   │   ├── lore/
│   │   │   └── the-london-by-night.md
│   │   ├── factions/
│   │   │   └── the-camarilla.md
│   │   └── greetings/
│   │       ├── elysium-opening.md
│   │       └── chase-through-soho.md
│   ├── wod-nyc/
│   ├── mythic-europe/
│   ├── faerun/
│   └── ...
├── style-guides/
│   ├── gothic-horror.md
│   └── high-fantasy.md
└── image-presets/
    ├── oil-painting.yaml
    └── modern-cinematic.yaml
```

A setting is one directory with a standard internal layout. Style guides and image presets are top-level because they may be useful across settings.

## Entity kinds

Each setting can hold any subset of these entity kinds; absent subdirectories are simply empty:

| Kind | What it holds | Mechanics may extend? |
|---|---|---|
| **characters** | The cast; PCs and NPCs both live here | Yes (sheets, capabilities) |
| **items** | Named items with narrative weight | Yes (weapons, fetishes, enchanted items) |
| **locations** | Places, with hierarchy and connections | Yes (Nodes, Covenants, Havens) |
| **lore** | Encyclopedic entries with keyword triggers | No |
| **factions** | Organizations, houses, orders | Yes (chronicle-level mechanics) |
| **greetings** | Opening scenarios for new campaigns | No |

Items as a first-class entity kind is new in this architecture. Named items (a famous sword, a cursed locket, an enchanted ring) get cards just like characters do. Generic items in play don't need cards — they exist only in prose.

## File formats

### `setting.yaml`

```yaml
id: wod-london
name: "London by Night"
description: "Modern London under the Masquerade..."
tags: [wod, vampire, urban, gothic]
genre: "urban gothic horror"

calendar:
  epoch: "2024-01-01"
  months:
    - { name: January, days: 31 }
    # ... etc
  days_per_week: 7
  week_day_names: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]
  seasons: [...]
  holidays: [...]

atmosphere:
  default_register: "low, urgent, mannered when in Elysium"
  default_palette: "neon and rain, oxblood and tarnished gold"

defaults:
  starting_location: elysium
  default_style_guide_id: gothic-horror
  default_image_preset_id: oil-painting

version: 3                              # increments when any file in this setting changes
```

### Character cards (`<setting>/characters/<id>.md`)

YAML frontmatter for structured fields; markdown body for prose.

```markdown
---
id: alistair-hyde-smythe
name: Alistair Hyde-Smythe
role: pc                                 # pc | major_npc | minor_npc | ensemble | named_flavor
aliases: ["Hyde-Smythe", "Sir Alistair"]
age: "perpetually 34"
tags: [vampire, ventrue, london, primogen]

voice:
  summary: "Cultured, restrained, occasionally cruel. Long pauses. Old money."
  register: "high formal English, present tense for menace"
  samples:
    - "We have established certain boundaries, you and I. I would prefer they remain unbroken."
  address_terms:
    pc_intimate: "darling"
    elders: "Honored"
  dos:
    - "Pauses before answering"
    - "Refers to mortals as 'kine' only in private"
  donts:
    - "Never raises his voice"

image:
  base_prompt: "elegant man in tailored Savile Row suit, slate gray hair, pale, cold blue eyes"
  negative_prompt: "modern casual, smiling broadly"
  canonical_seed: 49271
---

# Alistair Hyde-Smythe

## Appearance
...

## Personality
...

## Background
...
```

Mechanical sheets for this character don't live in the library file — they live as separate YAML files under a campaign's `sheets/characters/` directory, keyed by the active mechanics module. The character file is the narrative card; the sheet is the system-specific overlay.

### Location cards (`<setting>/locations/<id>.md`)

```markdown
---
id: camden-market
name: Camden Market
parent_id: camden
kind: outdoor
aliases: [the market, Camden]
tags: [camden, nightlife, neutral-ground]
climate_zone: temperate-oceanic
indoor: false
coordinates: {x: 412, y: 309}
permanent_features:
  - Stall rows along the canal
  - Stables converted to bars
connections:
  - to: chalk-farm
    via: street
    duration_min: 8
  - to: kings-cross
    via: rail
    duration_min: 12
typical_occupants: []
---

# Camden Market

The market thrums after dark — a hundred tongues, the canal's slow water, the smell of fried onions and rum.
...
```

### Item cards (`<setting>/items/<id>.md`)

```markdown
---
id: the-camden-blade
name: The Camden Blade
aliases: [the blade]
tags: [weapon, magical, claimed-by-the-prince]
provenance: "Forged 1672 by a now-extinct line of Tremere..."
current_holder: prince-of-london
---

# The Camden Blade

A single-edged knife in a fishskin sheath, the steel cold even in summer...
```

Like characters and locations, items get optional mechanical sheets attached per campaign via the mechanics module — a WoD campaign might attach a weapon sheet (damage, range, conceal); an Ars Magica campaign might attach an Enchanted Item sheet (effects, level, opening enchantments). The library card describes what the item *is*; the sheet describes what it *does* under a system.

### Lore (`<setting>/lore/<id>.md`)

```markdown
---
id: the-masquerade
title: The Masquerade
tags: [law, vampire, fundamental]
keywords: [masquerade, breach, mortal, exposure]
related_factions: [the-camarilla]
related_characters: []
secrecy: common-knowledge-among-kindred
---

# The Masquerade

The first law of the Camarilla: do not reveal the existence of vampires to mortals.
...
```

Lore entries have `keywords` that trigger archive-tier inclusion when they appear in recent posts. SillyTavern lorebook pattern, scoped to settings.

### Factions (`<setting>/factions/<id>.md`)

```markdown
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

# The Camarilla
...
```

Faction identity is library; faction *state* (current goals, focus, resources, public perception) is per-campaign and lives in SQLite.

### Greetings (`<setting>/greetings/<id>.md`)

```markdown
---
id: elysium-opening
name: "Elysium Opening"
tags: [introduction, political, no-violence]
starting_location: elysium
starting_time: "2024-10-31T22:00:00"
present_characters: [prince-of-london, alistair-hyde-smythe]
pov_character: alistair-hyde-smythe       # placeholder for PC pattern
mood: "tense civility, jasmine-scented smoke, candles low"
---

# Elysium Opening

The Prince's tower is candle-lit tonight, the chandeliers dimmed at her request.
You can taste the politics in the air — every Kindred present has come for a reason.
The Prince watches you cross the room.
...
```

A greeting is everything needed to open a campaign: where, when, who, mood, opening narration. The Frontend offers greetings as quick-starts during campaign creation; selecting one seeds scene 1. Greetings can be skipped — a campaign can start with a blank slate.

### Style guides (`library/style-guides/<id>.md`)

```markdown
---
id: gothic-horror
name: "Gothic Horror"
tags: [horror, modern, atmospheric]
---

# Gothic Horror prose style

Present tense, third-limited, focal POV PC. Sensory detail emphasized — temperature,
light quality, sound at the edge of hearing. Dialogue tags minimal.

## Constraints
- Violence is felt, not catalogued. Damage is described in terms of what it costs,
  not in mechanical detail.
- Mortal characters are clearly mortal — no instinctive supernatural knowledge.
- Period and place: contemporary; British vocabulary where setting is London.
```

### Image presets (`library/image-presets/<id>.yaml`)

```yaml
id: oil-painting
name: "Gothic oil painting"
style_preamble: "oil painting, dark academia, soft candlelight, muted palette, slight chiaroscuro"
default_negative_prompt: "anime, cartoon, bright daylight, photorealistic glare, lowres"
default_params:
  sampler: "DPM++ 2M Karras"
  steps: 28
  cfg_scale: 6.5
backend_overrides:
  automatic1111:
    model: sd_xl_base_1.0
  dalle:
    model: dall-e-3
    style: natural
tags: [painterly, gothic, atmospheric]
```

## Indexing into SQLite

At startup the app scans `data/library/` and parses every file. Each entity is indexed into `library_index`:

```sql
CREATE TABLE library_index (
  id TEXT PRIMARY KEY,                  -- composite path, e.g. "settings/wod-london/characters/alistair-hyde-smythe"
  setting_id TEXT,                      -- "wod-london"; null for top-level (style-guides, image-presets)
  kind TEXT NOT NULL,                   -- 'character', 'item', 'location', 'lore', 'faction', 'greeting',
                                        --   'setting', 'style_guide', 'image_preset'
  asset_id TEXT NOT NULL,               -- "alistair-hyde-smythe"; used for cross-setting variant lookup
  name TEXT,
  path TEXT NOT NULL,                   -- absolute file path
  frontmatter JSON NOT NULL,
  body TEXT,
  body_compressed TEXT,                 -- optional auto-summary for background-tier inclusion
  tags JSON,
  keywords JSON,
  file_mtime TIMESTAMP NOT NULL,
  content_hash TEXT NOT NULL,
  indexed_at TIMESTAMP NOT NULL,
  version INTEGER NOT NULL
);

CREATE INDEX idx_libidx_setting ON library_index(setting_id);
CREATE INDEX idx_libidx_kind ON library_index(kind);
CREATE INDEX idx_libidx_asset ON library_index(asset_id);    -- for cross-setting variant lookup

CREATE VIRTUAL TABLE library_index_fts USING fts5(
  name, body, tags, keywords,
  content='library_index', content_rowid='rowid'
);
```

The index is a cache. Delete `campaigns.sqlite` and the app rebuilds it from files on startup. The files are SSOT.

## File watcher

A file watcher (Python `watchdog`) monitors `data/library/`. On change:

- File created → parse, insert row, embed, emit `library_file_changed`
- File modified → parse, update row, re-embed if body changed, emit `library_file_changed`
- File deleted → remove row, mark embeddings stale, emit `library_file_changed`
- Directory renamed → emit warning; require manual reconciliation

`library_file_changed` events flow to the Frontend (library views update in real time) and to campaigns (pinned ones surface upgrade prompts).

## Character variants across settings — by shared id, not a family field

There is **no `family_id` field**. Variants of the same character across settings are recognized by sharing the same asset id. If both `settings/faerun/characters/drizzt.md` and `settings/mythic-europe/characters/drizzt.md` exist with id `drizzt`, the app queries `library_index WHERE kind = 'character' AND asset_id = 'drizzt'` and surfaces them as variants.

UI shows "Drizzt (faerun) — also exists in: mythic-europe." Each variant is fully independent. Editing one has no effect on others. If you rename a character in one setting, the link breaks (cost of renaming). If you want variants kept in voice-sync, that's manual.

Same id-matching applies to items, locations, lore, factions. The id is the link. Nothing else.

## Composition (campaign references library)

A campaign references one or more library settings, plus optional style guide and image preset, plus exactly one mechanics choice (a module id, or `null`):

```python
@dataclass
class Composition:
    settings: list[SettingRef]
    mechanics: Optional[str]               # mechanics module id, or None
    style_guide_id: Optional[str]
    image_preset_id: Optional[str]

@dataclass
class SettingRef:
    setting_id: str
    priority: int                          # 1 = highest
    include: list[str]                     # ['characters', 'items', 'locations', 'lore', 'factions', 'greetings']
                                           # default: all
    bound_at_version: int
    track_latest: bool                     # if true, always read latest from library_index
```

Most campaigns have one `SettingRef` with everything included. Crossovers compose multiple refs, often with `include` filters to pull different kinds from different settings.

### Read cascade

```
1. Look in SQLite for a campaign-local emergent entity matching the ID.
2. If not found, walk the campaign's setting refs in priority order:
   For each ref, query library_index filtered by setting_id and kind (respecting include).
3. Return the first match.
4. Apply any campaign-local override (in campaigns/<id>/overrides/) on top.
5. If nothing matches anywhere, the entity is missing.
```

### Overrides

A campaign edit to a library entity creates a campaign-local override file at `data/campaigns/<id>/overrides/settings/<setting>/<kind>/<entity>.yaml`. The library file is untouched. The override is a YAML patch with only the changed fields.

Setting / Characters modules apply the patch when returning the entity to callers.

A "Save back to library" action propagates an override into the underlying library file (writes the file, increments version, clears the override).

### Version pinning

Each setting ref records `bound_at_version`. When the underlying setting changes:

- `track_latest: true` → reads current files immediately
- `track_latest: false` (default) → continues reading at `bound_at_version` until explicit upgrade

Pinned reads consult `library_snapshots` first:

```sql
CREATE TABLE library_snapshots (
  campaign_id TEXT,
  branch_id TEXT,
  library_id TEXT,                       -- the library_index.id
  version INTEGER,
  frontmatter JSON,
  body TEXT,
  snapshot_at TIMESTAMP,
  PRIMARY KEY (campaign_id, branch_id, library_id)
);
```

Snapshots are written on bind (and on each explicit upgrade). Upgrade is a user action with a diff preview.

Storage cost: pinned snapshots duplicate the library content. Deduplicate-by-hash is a v2 optimization.

## Promotion: campaign-local → library

When a campaign-local emergent entity should become library content (e.g., a memorable NPC introduced mid-play, a notable item, a location the PC discovered):

```python
async def promote_to_library(
    campaign_id: str,
    entity_kind: str,                  # 'character', 'item', 'location', 'lore', 'faction'
    campaign_entity_id: str,
    target_setting_id: str,
) -> str:                              # returns library path
    # 1. Read the entity from campaign-local SQLite/files
    # 2. Render to markdown + YAML frontmatter
    # 3. Write to data/library/settings/<target>/<kind>/<id>.md
    # 4. Watcher picks it up; library_index gains a row
    # 5. Replace campaign-local record with a reference to the library row
    #    (or convert to an override if the campaign has continued mutations)
    # 6. Migrate embeddings; relink
```

UI: in the campaign's Cast / World view, an emergent entity has a "Promote to library..." action. Selecting a target setting shows the diff and confirms.

Demote (reverse promotion) is supported: remove from library (delete file). Campaigns that referenced it get a dangling-ref warning and an option to copy down to campaign-local.

## Interface

```python
class Library(Protocol):
    # Discovery / listing
    async def list_settings(self) -> list[SettingMeta]: ...
    async def get_setting(self, setting_id: str) -> SettingMeta: ...
    async def list_in_setting(
        self,
        setting_id: str,
        kind: str,                        # 'character', 'item', 'location', 'lore', 'faction', 'greeting'
    ) -> list[LibraryEntity]: ...
    async def get_entity(self, setting_id: str, kind: str, entity_id: str) -> LibraryEntity: ...

    # Top-level assets
    async def list_style_guides(self) -> list[LibraryEntity]: ...
    async def list_image_presets(self) -> list[LibraryEntity]: ...
    async def get_style_guide(self, id: str) -> LibraryEntity: ...
    async def get_image_preset(self, id: str) -> LibraryEntity: ...

    # Greetings (setting-level)
    async def list_greetings(self, setting_id: str) -> list[Greeting]: ...
    async def get_greeting(self, setting_id: str, id: str) -> Greeting: ...

    # Cross-setting variant lookup (id-based)
    async def variants_of(self, asset_id: str, kind: str) -> list[LibraryEntity]: ...

    # Writes (mediated; writes the file, updates the index)
    async def create_setting(self, id: str, meta: dict) -> SettingMeta: ...
    async def create_entity(
        self,
        setting_id: str,
        kind: str,
        entity_id: str,
        frontmatter: dict,
        body: str,
    ) -> LibraryEntity: ...
    async def update_entity(
        self,
        setting_id: str,
        kind: str,
        entity_id: str,
        frontmatter_patch: Optional[dict] = None,
        body: Optional[str] = None,
    ) -> LibraryEntity: ...
    async def delete_entity(self, setting_id: str, kind: str, entity_id: str) -> None: ...

    # Promotion
    async def promote_to_library(
        self,
        campaign_id: str,
        entity_kind: str,
        campaign_entity_id: str,
        target_setting_id: str,
    ) -> str: ...

    # Composition (per-campaign)
    async def get_composition(self, campaign_id: str) -> Composition: ...
    async def set_composition(self, campaign_id: str, composition: Composition) -> None: ...
    async def upgrade_setting_ref(
        self,
        campaign_id: str,
        setting_id: str,
    ) -> UpgradeReport: ...

    # Resolution (used by Setting, Characters, Context Builder)
    async def resolve(
        self,
        entity_id: str,
        campaign_id: str,
    ) -> ResolvedEntity: ...

    # Dependents (who's using this library entity)
    async def dependents(self, setting_id: str, kind: str, entity_id: str) -> list[CampaignRef]: ...
```

## Configuration

```yaml
library:
  root: ./data/library
  watch: true
  scan_on_startup: true

  indexing:
    embed_on_index: true
    embedding_provider: sentence-transformers     # references the active embedding plugin
    incremental: true

  version_pinning:
    default: pinned                              # pinned | track_latest
    snapshot_on_bind: true

  files:
    character_filename_pattern: "{id}.md"
    location_filename_pattern: "{id}.md"
    item_filename_pattern: "{id}.md"
    encoding: utf-8

  promotion:
    confirm_required: true
```

## Open questions (deferred)

- **Multi-library setups.** v1 has one library root. Multi-library (e.g., per-project libraries) is a future option; schema supports it via a `library_root` qualifier.
- **Library sharing.** Zip a setting folder, share it, unzip into another user's library. Supported by structure; no other tooling for v1.
- **Renaming and variant links.** If a user renames `drizzt` → `drizzt-do-urden` in one setting, the id-based variant link breaks. A `rename` operation that updates references is a v2 idea.
- **Setting forks.** Easy via directory copy + id rewrite. The app offers a "fork setting" action.
- **Cross-variant location families.** If the pattern "the same place exists across setting variants with different mechanical layers" proves common, a v2 sync feature might help. For now: independent files with shared ids.
- **Parameterized greetings.** Runtime parameters ("a greeting templated with the PC's name and chosen patron"). v2.
- **Snapshot deduplication.** Pinned snapshots can duplicate a lot. Content-addressed snapshot store keyed by hash is a clear v2 optimization.
