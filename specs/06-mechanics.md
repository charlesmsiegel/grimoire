# 06 — Mechanics

## Purpose

Mechanics is the game-system layer. The mechanics module that governs a campaign — WoD, Ars Magica, Blades in the Dark, D&D, or some custom system — interprets mechanical events, owns the schemas of mechanical sheets, dice, content (spells, items with stats, vis sources, Covenants, Nodes), and provides the UI affordances for working with all of it.

Mechanics is **first-class** in Grimoire: it has its own dedicated API surface defined here, not the generic plugin protocol. **Mechanics modules themselves are external packages** — none ship with Grimoire. The user installs the mechanics modules they want by dropping a directory into `data/mechanics/<id>/` and the app discovers it at startup via its `manifest.yaml`.

Crucially: writing a mechanics module is a significant undertaking, but it *does not require knowing Grimoire's internals*. The Mechanics API is self-contained — a module receives the data it needs, returns what it produces, declares its UI via JSON Schema, and never reaches into the app's other modules. You write against the API documented here, the way you'd write a Django app against Django's documented contracts.

Campaigns can also run with `mechanics: null` — no mechanics module selected, pure narrative play. The Mechanics module always exists as an interface; when null is selected, all queries return empty results and the system runs as freeform.

## Responsibilities

- Discover mechanics modules from `data/mechanics/` at startup; load their manifests
- Validate that a mechanics module's manifest matches its declared API version
- Expose a single active mechanics module per campaign (selected at campaign creation)
- Provide schemas for mechanical sheets across multiple entity kinds (characters, items, locations, factions)
- Resolve dice and rolls deterministically given a seed; produce structured results
- Validate narrated mechanical events from prose against the active system's rules
- Answer capability queries: "what can this entity do?"
- Provide character creation workflows
- Provide content browsers (spells, items, vis sources, etc.) per system
- Hook into the turn loop: pre-roll evaluation, scene-level checks, time-tick effects
- Supply JSON Schema declarations for the Frontend to render sheets via the widget library
- Optionally supply a theme.css for visual styling
- Track v2 reservation: custom JS bundles for advanced sheet UI

## Non-responsibilities

- Does not store mechanical state (State Store does; sheets live as `data/campaigns/<id>/sheets/<kind>/<id>.<mechanics-id>.yaml` files)
- Does not narrate outcomes (the LLM does, with mechanics results as input)
- Does not parse prose into events (the Extractor does, then asks Mechanics to validate)
- Does not advance time (Time Engine does, though it consults mechanics for activity durations and effects)
- Does not own character or location data (World owns those files; Characters module handles character-specific behaviors)
- Is not a plugin (the generic plugin protocol is too narrow; see `15-plugins.md` for what plugins are)

## The mechanics module API

A mechanics module is a directory at `data/mechanics/<id>/` containing:

```
data/mechanics/<id>/
├── manifest.yaml           # required
├── mechanics.py            # required; implements the Mechanics protocol
├── sheets/                 # required; JSON Schemas per entity kind
│   ├── character.json
│   ├── item.json           # optional, if items are mechanical in this system
│   ├── location.json       # optional, if locations carry mechanical layer (e.g., Nodes, Covenants)
│   └── faction.json        # optional, if factions have mechanical state
├── content/                # optional; content schemas (spells, items, etc.)
│   ├── spell.json
│   └── ...
├── theme.css               # optional; CSS theme for rendered sheets
└── ui/                     # optional, v2; custom JS bundles per sheet kind
```

### Manifest

```yaml
id: wod-mechanics
name: "World of Darkness Mechanics"
version: "1.2.0"
api_version: "1"                 # which version of the Mechanics API this module targets
author: "..."
homepage: "..."
description: "..."

# What this module covers
sheet_kinds:
  - character                    # required for systems with character sheets
  - location                     # optional; Nodes, Havens
  - item                         # optional; weapons, fetishes

content_kinds:
  - discipline
  - background
  - merit
  - flaw

capabilities:
  - dice
  - combat
  - character_creation
  - npc_ticks
  - time_advancement

ui:
  theme_css: theme.css            # optional
  custom_components:              # v2
    character: ui/character-sheet.js
```

### The Mechanics protocol (Python)

The module's `mechanics.py` implements:

```python
class MechanicsModule(Protocol):
    # Identity
    id: str                            # matches manifest id
    name: str
    version: str
    api_version: str

    # Sheet schemas (declarative)
    def sheet_schema(self, entity_kind: str) -> Optional[dict]:
        """Returns JSON Schema for the given entity kind, or None if this
        mechanics doesn't extend that kind."""

    def validate_sheet(self, entity_kind: str, sheet: dict) -> ValidationResult: ...

    def initialize_sheet(self, entity_kind: str, entity_id: str) -> dict:
        """Produce a blank sheet of the given kind, with defaults filled in."""

    # Content schemas (spells, items, vis sources, Covenant resources, etc.)
    def list_content_kinds(self) -> list[str]: ...
    def content_schema(self, kind: str) -> dict: ...

    # Powers and capabilities (what can an entity do?)
    def capabilities_of(
        self,
        entity_ref: str,
        sheet: dict,
    ) -> list[Capability]:
        """Given an entity's sheet, return a list of mechanical capabilities
        the entity has. Used by Context Builder, Extractor, UI."""

    def power_definitions(self) -> list[PowerDefinition]:
        """The vocabulary of named powers this system defines.
        e.g., for WoD: every Discipline at every rating."""

    def power_definition(self, power_id: str) -> Optional[PowerDefinition]: ...

    # Dice / rolls
    def evaluate_pre_roll(
        self,
        player_input: str,
        scene: SceneContext,
    ) -> list[ProposedRoll]:
        """Inspect player input and scene context; propose any rolls that should
        be resolved before the LLM responds. Returns empty list if none apply."""

    def resolve_roll(self, roll: Roll, rng_seed: int) -> RollResult:
        """Resolve a roll deterministically. Same roll + same seed = same result.
        Forking a branch preserves seeds."""

    # Narrated event validation
    def validate_narrated_event(
        self,
        event: NarratedEvent,
        scene: SceneContext,
    ) -> ValidationResult:
        """The Extractor identified a mechanical event in prose (e.g., 'Aleksandr
        uses Celerity'). Validate it: does Aleksandr have Celerity? Enough Blood?
        Propose deltas (Blood Pool -1) if valid."""

    # Character creation
    def character_creation_steps(self) -> list[CreationStep]:
        """Return a sequence of creation steps the Frontend walks through.
        Each step is a JSON Schema form. The result is a starting sheet."""

    # Time ticks
    def time_tick(
        self,
        entity_ref: str,
        sheet: dict,
        duration: Duration,
        context: TickContext,
    ) -> list[StateDelta]:
        """When in-game time advances, apply system-specific effects.
        e.g., Ars Magica seasonal advancement; WoD blood loss for vampires;
        D&D long rest recovery."""

    # Discovery / introspection
    def system_summary(self) -> str:
        """A short human-readable description for the campaign creation UI."""
```

`mechanics: null` campaigns don't load any module; the Mechanics module returns trivial empty results for all queries.

## Multi-entity coverage

The `sheet_schema(entity_kind)` interface is what makes mechanics cover more than just rolls. A module declares which entity kinds it extends, and provides a JSON Schema for each.

Examples per system:

### Freeform (`mechanics: null`)

Nothing. Characters, items, locations, factions are pure narrative. No sheets. No rolls. No capabilities.

### WoD

- **character**: Attributes (Physical/Social/Mental triads with dot-rating), Abilities (Talents/Skills/Knowledges), Advantages (Disciplines, Backgrounds, Virtues), state (Humanity/Path, Willpower, Blood Pool, Health, Experience). Splat-specific blocks for Vampire/Werewolf/Mage etc.
- **location**: optional Node schema (Quintessence rating, Tass per period, Resonance, wards) or Haven schema (security, accessibility, amenities).
- **item**: weapon (damage, range, conceal), fetish (level, spirit type, taboo).

### Ars Magica

- **character**: Three templates (magus, companion, grog), each with Characteristics, Personality Traits, Virtues, Flaws. Magi additionally have Arts (5 Techniques × 10 Forms — `grid-rating`), Spells, Twilight Scars.
- **location**: Covenant schema (vis sources with seasonal yield, library catalog, lab specifications, sanctum, mundane resources, magical aura).
- **item**: Enchanted Item schema (effects with Form/Technique/level/uses).

### D&D 5e

- **character**: Class, level, race, ability scores, proficiency bonus, skill list, equipment, spell slots, hit points, conditions.
- **item**: Magical item (rarity, attunement, properties).
- **location**: minimal; mostly narrative.

The mechanics module is the authority on what fields a sheet has. The World module stores the files; the Mechanics module interprets them.

## Powers and capabilities

`capabilities_of(entity_ref, sheet)` returns a list of mechanical things this entity can do, given its current sheet:

```python
@dataclass
class Capability:
    id: str                            # "wod.celerity.3"
    name: str                          # "Celerity 3"
    kind: str                          # "discipline", "spell", "feat", "ritual"
    description: str                   # short prose for context inclusion
    cost: Optional[ResourceCost]       # "1 Blood per turn"
    effect: str                        # mechanical effect summary
    metadata: dict                     # system-specific extras
```

Used by:

- **Context Builder**: when a character is in spotlight, include their capabilities in the prompt ("Aleksandr can use Celerity 3 — extra action per turn for 1 Blood")
- **Extractor**: when prose narrates power use ("Aleksandr blurs across the room"), match against capabilities to identify the mechanical event
- **Frontend**: render a character's capabilities as a tagged list with descriptions on hover
- **Validation**: confirm a narrated power use is one the character actually has

`power_definitions()` returns the *vocabulary* — every power the system defines, at every rating, with description, cost, and effect. The Context Builder can reference these for archive-tier inclusion ("here's what Auspex 3 does" when a character with Auspex 3 is in the scene). Used to ground prose: the model sees the canonical effect of a power, so it narrates consistently.

## Sheet UI rendering

Mechanics modules ship JSON Schema for each sheet kind. The Frontend renders them using a built-in widget library — see `14-frontend.md` for the full widget vocabulary. The mechanics module's schema references widgets by name:

```json
{
  "$schema": "...",
  "type": "object",
  "title": "WoD Character Sheet",
  "properties": {
    "name": { "type": "string", "widget": "text" },
    "attributes": {
      "type": "object",
      "widget": "nested-section",
      "title": "Attributes",
      "properties": {
        "strength": { "type": "integer", "widget": "dot-rating", "min": 1, "max": 5 },
        "dexterity": { "type": "integer", "widget": "dot-rating", "min": 1, "max": 5 },
        "stamina": { "type": "integer", "widget": "dot-rating", "min": 1, "max": 5 },
        ...
      }
    },
    "disciplines": {
      "type": "array",
      "widget": "power-list",
      "items": { "$ref": "#/$defs/PowerEntry" }
    },
    "health": { "widget": "health-track", "rows": 7, "severity_levels": ["bashing", "lethal", "aggravated"] },
    ...
  }
}
```

The Frontend's renderer:

1. Reads the schema for the active mechanics module and entity kind
2. For each property, looks up the named widget
3. Renders the widget with the property's data
4. Edits flow back through the State Store, which writes the YAML sheet file

If a mechanics module references a widget the Frontend doesn't have, the renderer falls back to a generic editor (text or JSON) and surfaces a warning. Widget vocabulary is versioned; the manifest declares which API version it targets.

### Theme CSS

A mechanics module can ship a `theme.css` that styles its sheets. The Frontend isolates per-mechanics CSS by scoping rules to a wrapper class (`.mechanics-wod-mechanics`) so the WoD theme can't bleed into Ars Magica sheets. Implementation detail covered in `14-frontend.md`.

### v2 escape hatch: custom JS bundles

For sheets the widget library can't express (a 3D dice roller, a node-graph talent tree, a complex combat tracker), v2 allows mechanics modules to ship pre-built JS bundles in `ui/`. The manifest declares which sheet kinds have custom components. The Frontend dynamically imports the bundle and uses it instead of schema rendering for the named kinds, with a fallback if the bundle fails to load.

v1 ships with the widget library only. The escape hatch is reserved in the API but not implemented.

## Dice and rolls

Rolls are deterministic given a seed. The State Store maintains a per-branch RNG seed; the Orchestrator passes it to `resolve_roll`. Same seed + same roll = same result. Forking a branch preserves seeds for replay.

```python
@dataclass
class Roll:
    id: str                            # unique per turn
    kind: str                          # system-specific: "dice-pool", "attack", "contested"
    actor_ref: Optional[str]           # who's rolling
    target_ref: Optional[str]          # what/whom
    pool: int                          # e.g., 5 dice
    difficulty: Optional[int]
    modifiers: list[RollModifier]
    seed: int

@dataclass
class RollResult:
    roll_id: str
    dice: list[int]                    # raw rolls
    successes: int                     # interpreted result
    botched: bool
    outcome: str                       # human-readable summary
    proposed_deltas: list[StateDelta]  # e.g., Blood -1, Wound +1
    narration_hint: str                # text the LLM can use ("you blur across the room")
```

The Orchestrator includes the result in the prompt as authoritative ("the dice say: 3 successes"), preventing the LLM from contradicting mechanics.

## Pre-roll evaluation

Before the LLM is called, the Orchestrator asks the active mechanics module to inspect player input. The module may propose rolls:

```python
async def evaluate_pre_roll(player_input: str, scene: SceneContext) -> list[ProposedRoll]:
    # "I climb the wall" → propose a Dexterity + Athletics roll (WoD)
    # "I cast Ball of Abysmal Flame" → propose a Casting Total roll (Ars Magica)
    # "I fire my crossbow" → propose an Attack roll (D&D)
```

The Frontend can show proposed rolls before submission and let the player accept/modify/decline. Confirmed rolls are resolved; results are included in the prompt; the LLM narrates around them.

## Narrated event validation

After the LLM responds, the Extractor scans prose for mechanical events. Each candidate is passed to the active mechanics module for validation:

```python
async def validate_narrated_event(event: NarratedEvent, scene: SceneContext) -> ValidationResult:
    # event.kind: "power_use", "damage_taken", "wound", "death", "item_used", "spell_cast"
    # Returns:
    #   valid=True with proposed deltas; or
    #   valid=False with a reason (character doesn't have that power, insufficient resources)
```

If invalid, the validation reason is surfaced for user review (the LLM may have hallucinated a power use). If valid, the deltas apply (Blood -1, Wound +1, Spell slot consumed).

## Character creation

Mechanics modules supply character creation flows:

```python
def character_creation_steps(self) -> list[CreationStep]:
    return [
        CreationStep(id="concept", title="Concept", schema=concept_schema),
        CreationStep(id="splat", title="Splat selection", schema=splat_schema),
        CreationStep(id="attributes", title="Attributes", schema=attr_schema),
        CreationStep(id="abilities", title="Abilities", schema=ability_schema),
        CreationStep(id="advantages", title="Advantages", schema=adv_schema),
        CreationStep(id="finishing", title="Finishing touches", schema=finishing_schema),
    ]
```

Each step is rendered by the Frontend (using the widget library). The final result is a complete sheet conforming to `sheet_schema("character")`, written to `data/campaigns/<id>/sheets/characters/<character-id>.<mechanics-id>.yaml`.

Library characters can also have library-baseline sheets — written by hand or via creation flow before any campaign exists — stored under the character's world. The campaign's sheet (if any) overrides the library baseline.

## Time ticks

When in-game time advances, the Time Engine consults the active mechanics module for system-specific effects:

```python
def time_tick(entity_ref, sheet, duration, context) -> list[StateDelta]:
    # WoD: blood point loss per night for vampires
    # Ars Magica: seasonal advancement, vis production
    # D&D: long rest recovery
```

The module returns deltas the State Store applies (with audit). The Time Engine is system-agnostic; mechanics fills in the rules.

## Discovery and loading

At startup, the app scans `data/mechanics/` for subdirectories containing `manifest.yaml`:

```
1. For each subdirectory:
   a. Parse manifest.yaml.
   b. Verify api_version is supported.
   c. Import mechanics.py as a Python module.
   d. Instantiate the MechanicsModule class.
   e. Validate the instance against the protocol.
   f. Register the module.
2. Build a registry of available modules by id.
```

Errors during load are logged and surfaced in the UI ("wod-mechanics failed to load: import error in mechanics.py"). The app continues without the failed module.

Reloading: a file watcher on `data/mechanics/` can trigger module reload during development. Production: restart to pick up changes.

## Switching modules mid-campaign

A campaign's mechanics module is recorded in `campaign.yaml`. Switching is supported but flagged:

- Sheets keyed by the old module's id are not deleted, but become inactive
- Sheets for the new module are missing; the Frontend prompts for bulk creation
- Capabilities, content, and rolls follow the new module
- The user can switch back; old sheets are still there

Use cases: "let's try the same world with Blades instead of WoD"; "we want to drop mechanics and finish in narrative mode" (switch to null).

## Interface (for callers)

The Orchestrator and other modules call the Mechanics module through a thin façade in the app:

```python
class Mechanics(Protocol):
    # Per-campaign access
    async def active_module(self, campaign_id: str) -> Optional[MechanicsModule]: ...
    # Returns None for `mechanics: null` campaigns.

    # Convenience pass-throughs (delegate to active module)
    async def sheet_schema(self, campaign_id: str, entity_kind: str) -> Optional[dict]: ...
    async def get_sheet(self, campaign_id: str, entity_ref: str) -> Optional[dict]: ...
    async def update_sheet(self, campaign_id: str, entity_ref: str, patch: dict) -> dict: ...
    async def capabilities_of(self, campaign_id: str, entity_ref: str) -> list[Capability]: ...

    async def evaluate_pre_roll(
        self,
        campaign_id: str,
        player_input: str,
        scene: SceneContext,
    ) -> list[ProposedRoll]: ...

    async def resolve_roll(self, campaign_id: str, roll: Roll) -> RollResult: ...
    async def validate_narrated_event(
        self,
        campaign_id: str,
        event: NarratedEvent,
        scene: SceneContext,
    ) -> ValidationResult: ...

    async def time_tick(
        self,
        campaign_id: str,
        entity_ref: str,
        duration: Duration,
        context: TickContext,
    ) -> list[StateDelta]: ...

    # Registry
    async def list_installed_modules(self) -> list[ModuleManifest]: ...
    async def module_info(self, module_id: str) -> Optional[ModuleManifest]: ...
```

For `mechanics: null` campaigns, all convenience methods return empty/None.

## Configuration

```yaml
mechanics:
  root: ./data/mechanics
  reload_on_file_change: false        # production false; dev can enable
  validation:
    strict_sheets: true                # validate sheets against schema on write
    strict_events: false               # validation failures warn rather than block
  rng:
    per_branch_seed: true              # forks preserve seeds
  defaults:
    no_mechanics_warning: false        # campaigns with mechanics: null don't warn
```

## What the API does not (yet) allow

- Custom UI components beyond the widget library (v2 only)
- Cross-module communication (one mechanics referencing another)
- Server-side roll requests over network (single-user local; v2 multiplayer would add this)
- Plugin-style hot-swap of mechanics modules during a session

## Open questions (deferred)

- **Sheet versioning across module updates.** If wod-mechanics 1.2 adds a field, do existing 1.1 sheets get auto-migrated? Defer to module's responsibility for now.
- **Sandbox / safety of module code.** Mechanics modules run unrestricted Python. v2 might sandbox via subprocess or WASM if untrusted modules become a concern.
- **Network requests from modules.** Some modules might want online dice rollers or external content lookups. Allowed but discouraged; mechanics should generally be self-contained.
- **Localization.** Schema labels, capability descriptions, content fields. v2.
