# 08 — Characters

## Purpose

The Characters module is the character-specific behavior layer over Setting's storage. Setting owns the character files (`<setting>/characters/<id>.md`) and handles CRUD; Characters provides the *behaviors* that characters need but other entity kinds don't:

- Voice anchors (canonical voice, sample dialogue, address terms, dos and don'ts)
- Drift detection (voice consistency over time, with corrective context)
- Context tier management (lock-in, spotlight, background, archive)
- PC role tracking (which characters are PCs in this campaign, multi-PC coordination)
- Cross-setting variant queries (Drizzt in faerun and mythic-europe, by shared id)
- Compressed card views (full, compressed, voice-only, capsule)
- Relationship state (campaign-scoped affection, trust, awareness)
- Promotion of campaign-local emergent characters into a setting

Characters does not own data. It calls into Setting for reads/writes of the underlying files. It calls into Mechanics for sheet schemas and capability queries when a character has a mechanical sheet. Think of Characters as the "character service" — a behavior facade over multiple storage and rule sources.

## Responsibilities

- Provide character-aware reads and writes through Setting
- Maintain voice anchors and recommend rotation of dialogue samples
- Detect voice drift in recent play and surface corrective context
- Recommend context tiers based on scene presence, recent mentions, commitments, and user pins
- Track PC roles and active-PC state per campaign
- Coordinate multi-PC turn flow (advance trigger; see also `10-scene-manager.md`)
- Provide cross-setting variant lookup for characters
- Generate compressed views of characters (full / compressed / voice / capsule)
- Maintain campaign-scoped relationship state
- Promote campaign-local emergent characters into a setting on user action
- Import characters from external formats (SillyTavern V2/V3 cards, JSON, plain text)

## Non-responsibilities

- Does not own character files (Setting does; Characters reads/writes through Setting's API)
- Does not own mechanical sheets (Mechanics does; sheets live as separate files per `06-mechanics.md`)
- Does not assemble prompts (Context Builder does; uses Characters as a data source)
- Does not own scenes (Scene Manager does; scenes reference characters by ref)
- Does not own non-character entities (Setting handles items, locations, lore, factions, greetings directly)

## Character schema

The library file format is owned by Setting (see `18-library.md` and `09-setting.md`). The schema as seen by Characters:

```python
@dataclass
class Character:
    setting_id: Optional[str]            # None if campaign-local emergent
    id: str
    name: str
    role: CharacterRole                  # pc | major_npc | minor_npc | ensemble | named_flavor
    aliases: list[str]
    age: Optional[str]
    tags: list[str]

    voice: VoiceAnchor                   # see below
    image: Optional[ImagePromptTemplate]
    structural_relationships: list[StructuralRelationship]   # library-level

    description: str                     # short
    body: str                            # markdown body of the card

    file_path: str
    file_mtime: datetime
    version: int

@dataclass
class VoiceAnchor:
    summary: str                         # 1-2 sentences
    register: str                        # formal, casual, archaic, etc.
    samples: list[str]                   # 3-5 lines of canonical dialogue
    speech_patterns: list[str]
    address_terms: dict[str, str]        # how this character addresses specific others
    dos: list[str]
    donts: list[str]
```

## Per-campaign character state

Campaign-scoped, in SQLite (see `03-state-store.md`):

```python
@dataclass
class CharacterState:
    character_ref: str                   # "library:settings/wod-london/characters/alistair-hyde-smythe"
                                         #   or "campaign:emergent/the-bartender"
    campaign_id: str
    branch_id: str
    location_ref: Optional[str]
    emotional_state: str
    physical_state: str
    immediate_intent: str
    knowledge_state: dict
    last_action: Optional[str]
    last_screen_time_turn: Optional[str]
    visible_to_pc: bool
    drift_score: float
    tier_pin: Optional[ContextTier]
```

## Resolved characters

```python
@dataclass
class ResolvedCharacter:
    setting_id: Optional[str]
    id: str
    # ... all card fields, with override patch applied ...
    current_state: CharacterState        # campaign-scoped state
    capabilities: list[Capability]       # from active mechanics module, if any
    source_chain: list[ResolutionSource]
    overrides_applied: list[str]
```

The State Store applies the cascade (campaign-local emergent → library refs → fail), Characters wraps it for callers and asks Mechanics for capabilities if a sheet exists.

## PC role and multi-PC

A character's `role` field can be `pc`. A campaign has a list of PCs (with `owner` field for future multiplayer):

```python
@dataclass
class PCEntry:
    character_ref: str
    name: str                           # display name, may differ from character.name
    owner: str                          # 'local' in v1; account id in v2
    active: bool                        # currently active in this session
```

Characters owns multi-PC coordination:

```python
class Characters(Protocol):
    # PC management
    async def list_pcs(self, campaign_id: str) -> list[PCEntry]: ...
    async def add_pc(self, campaign_id: str, character_ref: str, name: str, owner: str) -> PCEntry: ...
    async def remove_pc(self, campaign_id: str, character_ref: str) -> None: ...
    async def set_active_pc(self, campaign_id: str, character_ref: str) -> None: ...

    # Per-PC current scene
    async def current_scene_for_pc(self, campaign_id: str, character_ref: str) -> Optional[SceneRef]: ...
    async def set_current_scene_for_pc(
        self,
        campaign_id: str,
        character_ref: str,
        scene_ref: SceneRef,
    ) -> None: ...

    # Multi-PC turn semantics
    async def present_pcs_in_scene(self, scene_ref: SceneRef) -> list[PCEntry]: ...
    async def should_auto_respond(self, scene_ref: SceneRef) -> bool:
        """True if scene has exactly 1 present PC. False if 2+ (advance required)."""

    async def pending_pc_inputs_since_last_advance(self, scene_ref: SceneRef) -> list[Post]: ...
```

When a scene has 2+ present PCs, the Orchestrator does not auto-respond after a post. The user signals "Advance" to trigger the LLM, which reads the accumulated PC inputs. See `10-scene-manager.md` for the full multi-PC flow.

PCs in different scenes (the common case) each get their own auto-responsive scene; the user switches between PCs in the Frontend.

## Voice anchors

The single most important drift-prevention tool. A good anchor has:

1. **Summary line** — one or two sentences describing how this character speaks
2. **Register** — formal, casual, archaic, low, technical
3. **Samples** — 3-5 lines of canonical dialogue (rotated periodically)
4. **Speech patterns** — verbal tics, vocabulary peculiarities
5. **Address terms** — how this character addresses specific others
6. **Dos** — must-do speech traits
7. **Don'ts** — what the character never says or does in dialogue

The anchor is authored in the library character file. Campaigns can override the anchor via a campaign-local override (campaign-scoped voice tweaks for chronicle-specific changes — "in this campaign vivienne has loosened up").

## Drift detection

```python
async def check_drift(
    self,
    character_ref: str,
    campaign_id: str,
    window: int = 10,
) -> DriftReport:
    # 1. Pull last N posts where the character spoke (from scene files / SQLite posts table)
    # 2. Send to drift-check model with resolved voice anchor + samples
    # 3. Model returns drift score (0-1) plus specific evidence
    # 4. Update character_state.drift_score; flag if above threshold
    # 5. Return a corrective context snippet for the next prompt
```

When drift is detected:
- Surface a UI badge ("Alistair drifting — voice loosening")
- Inject corrective voice anchors in the next prompt featuring this character
- Optionally offer to regenerate the last response with stronger voice guidance

## Tier management

Each character has a context tier per campaign (lock-in, spotlight, background, archive). Characters recommends; Context Builder applies.

Recommendation rules:
- Present in current scene → spotlight
- Mentioned in recent posts → background
- Open commitments to PC → at least background
- User tier pin → forced tier
- Inactivity → demotion over time

Tier pins are campaign-local.

## Cross-setting variant lookup

Variants of the same character across settings are recognized by shared asset id (no `family_id` field). If `wod-london/characters/alistair-hyde-smythe.md` and `wod-nyc/characters/alistair-hyde-smythe.md` both exist, they're variants.

```python
async def cross_setting_lookup(
    self,
    character_id: str,
    exclude_setting: Optional[str] = None,
) -> list[Character]: ...
```

UI surfaces "Alistair (wod-london) — also exists in: wod-nyc." Variants are independent — editing one has no effect on others.

## Compressed card views

The Context Builder needs different depths at different tiers:

```python
class Characters(Protocol):
    async def get_full_card(self, ref: str, campaign_id: str) -> str: ...
    async def get_compressed_card(self, ref: str, campaign_id: str) -> str: ...
    async def get_voice_only(self, ref: str, campaign_id: str) -> str: ...
    async def get_capsule(self, ref: str, campaign_id: str) -> str: ...
```

Views are generated from the resolved character (library + override + state + capabilities). Cached; cache invalidates on any source change.

## Relationships

Bidirectional relationships are campaign-scoped (per-timeline; library-level relationships are *structural* — "vivienne is winifred's sister" — and stored on the character card).

```python
@dataclass
class Relationship:
    from_ref: str
    to_ref: str
    types: list[RelationshipType]        # spouse, parent, sibling, ally, rival, lover, etc.
    state: RelationshipState
    history: list[RelationshipEvent]

@dataclass
class RelationshipState:
    affection: int
    trust: int
    dominance: int
    intimacy: int
    awareness: AwarenessState
    custom: dict
```

## Mechanical capability integration

When a character has a mechanical sheet (under the active mechanics module), Characters calls Mechanics to surface capabilities:

```python
async def capabilities_of(self, character_ref: str, campaign_id: str) -> list[Capability]:
    sheet = await self.state.get_sheet(character_ref, mechanics_module_id, campaign_id)
    if sheet is None:
        return []
    return await self.mechanics.capabilities_of(campaign_id, character_ref)
```

Used by Context Builder for spotlight-tier context ("Alistair has Celerity 3 and Dominate 2"). Used by Frontend for capability lists in the character view. Used by Extractor to validate narrated power use.

## Promotion: campaign-local → library

When an emergent NPC should become a library character:

```python
async def promote_to_library(
    self,
    campaign_id: str,
    character_id: str,                  # the campaign-local id
    target_setting_id: str,
) -> str:                                # returns the new library path
    # Delegates to Setting.promote_to_library with kind='character'.
    # Characters wraps it to handle character-specific concerns:
    # - Render voice anchor properly
    # - Preserve drift_score / tier_pin if explicitly carried
    # - Migrate any campaign-local sheet to library-level sheet if applicable
```

## Import

The user has many SillyTavern character cards (the `wod-london-roster`, `opt-roster`, `emberport-roster` skills today). Characters provides import helpers:

```python
async def import_sillytavern(
    self,
    card: bytes,
    target_setting_id: str,
) -> ImportResult: ...
async def import_charx(self, charx: bytes, target_setting_id: str) -> ImportResult: ...
async def import_plaintext(self, text: str, target_setting_id: str) -> ImportResult: ...
```

Imports propose mappings (frontmatter fields, voice anchor extraction) and on confirmation write character files under the target setting.

## Interface

```python
class Characters(Protocol):
    # Character CRUD (delegated to Setting, but exposed here for convenience)
    async def list_in_setting(self, setting_id: str) -> list[Character]: ...
    async def get(self, setting_id: str, id: str) -> Character: ...
    async def create(self, setting_id: str, character: CharacterData) -> Character: ...
    async def update(self, setting_id: str, id: str, patch: dict) -> Character: ...
    async def delete(self, setting_id: str, id: str) -> None: ...

    # Emergent (campaign-local) characters
    async def create_emergent(
        self,
        campaign_id: str,
        character: CharacterData,
        source: str,
    ) -> str: ...
    async def update_emergent(self, campaign_id: str, id: str, patch: dict) -> Character: ...
    async def delete_emergent(self, campaign_id: str, id: str) -> None: ...

    # Overrides on library characters
    async def upsert_override(
        self,
        campaign_id: str,
        character_ref: str,
        patch: dict,
        source: str,
    ) -> None: ...

    # Resolution
    async def resolve(self, character_ref: str, campaign_id: str) -> ResolvedCharacter: ...
    async def list_for_campaign(
        self,
        campaign_id: str,
        filter: CharacterFilter = ...,
    ) -> list[ResolvedCharacter]: ...

    # Cross-setting variants
    async def cross_setting_lookup(
        self,
        character_id: str,
        exclude_setting: Optional[str] = None,
    ) -> list[Character]: ...

    # Views
    async def get_full_card(self, ref: str, campaign_id: str) -> str: ...
    async def get_compressed_card(self, ref: str, campaign_id: str) -> str: ...
    async def get_voice_only(self, ref: str, campaign_id: str) -> str: ...
    async def get_capsule(self, ref: str, campaign_id: str) -> str: ...

    # State
    async def update_state(
        self,
        ref: str,
        campaign_id: str,
        branch_id: str,
        state: CharacterState,
        source: str,
    ) -> None: ...
    async def mark_screen_time(self, ref: str, campaign_id: str, turn_id: str) -> None: ...

    # Tier
    async def recommend_tiers(self, scene: Scene) -> dict[CharacterRef, ContextTier]: ...
    async def pin_tier(self, ref: str, campaign_id: str, tier: ContextTier) -> None: ...

    # Drift
    async def check_drift(self, ref: str, campaign_id: str, window: int = 10) -> DriftReport: ...
    async def drift_corrective_context(self, ref: str, campaign_id: str) -> str: ...

    # PCs
    async def list_pcs(self, campaign_id: str) -> list[PCEntry]: ...
    async def add_pc(self, campaign_id: str, character_ref: str, name: str, owner: str) -> PCEntry: ...
    async def remove_pc(self, campaign_id: str, character_ref: str) -> None: ...
    async def set_active_pc(self, campaign_id: str, character_ref: str) -> None: ...

    async def current_scene_for_pc(self, campaign_id: str, character_ref: str) -> Optional[SceneRef]: ...
    async def set_current_scene_for_pc(
        self,
        campaign_id: str,
        character_ref: str,
        scene_ref: SceneRef,
    ) -> None: ...

    async def present_pcs_in_scene(self, scene_ref: SceneRef) -> list[PCEntry]: ...
    async def should_auto_respond(self, scene_ref: SceneRef) -> bool: ...

    # Mechanical capabilities
    async def capabilities_of(self, ref: str, campaign_id: str) -> list[Capability]: ...

    # Relationships (campaign-scoped)
    async def get_relationships(self, ref: str, campaign_id: str) -> list[Relationship]: ...
    async def update_relationship(
        self,
        from_ref: str,
        to_ref: str,
        campaign_id: str,
        delta: dict,
    ) -> None: ...

    # Promotion
    async def promote_to_library(
        self,
        campaign_id: str,
        character_id: str,
        target_setting_id: str,
    ) -> str: ...

    # Import
    async def import_sillytavern(self, card: bytes, target_setting_id: str) -> ImportResult: ...
    async def import_charx(self, charx: bytes, target_setting_id: str) -> ImportResult: ...
    async def import_plaintext(self, text: str, target_setting_id: str) -> ImportResult: ...

    # Search
    async def search(
        self,
        query: str,
        setting_id: Optional[str] = None,
        scope: str = "all",
        campaign_id: Optional[str] = None,
    ) -> list[Character]: ...
```

## Configuration

```yaml
characters:
  drift:
    check_every_n_appearances: 5
    check_model: claude-haiku-4-5
    drift_score_threshold: 0.4
  voice_anchor:
    sample_dialogue_rotation: true
    max_samples: 5
  capsules:
    auto_generate: true
  imports:
    sillytavern_v2: true
    sillytavern_v3: true
    charx: true
    plaintext: true
  promotion:
    require_confirmation: true
  cross_setting_lookup:
    case_sensitive: false
  multi_pc:
    auto_advance_with_single_pc: true
    require_advance_with_multiple_pcs: true
```

## Open questions (deferred)

- **Auto-draft voice anchors for emergent characters.** When a new NPC appears, auto-draft an anchor from their first scene? Yes, with user review.
- **Visualization.** Relationship graph, variant lineage. UI consideration; data model supports it.
- **Sheet versioning.** When mechanical sheets change (XP spent), are old versions kept? Yes via delta log; snapshot-per-session is nice-to-have.
- **Cross-setting renaming.** Renaming `alistair-hyde-smythe` to `hyde-smythe` in one setting breaks the variant link. A `rename` operation that updates references is a v2 idea.
- **PC scene placement.** When a PC has no current scene (just joined a campaign or just finished one), what's the default? Probably "show the campaign overview"; UI decision.
- **Body field structure.** The markdown body is unstructured; canonical headings (Appearance, Personality, Background) are encouraged via templates but not enforced.
