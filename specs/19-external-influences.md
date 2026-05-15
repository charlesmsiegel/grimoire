# 19 — External Influences and Boundaries

## Purpose

Grimoire shares a problem space with several mature projects — SillyTavern, Marinara Engine, AI Dungeon, KoboldAI Lite, and the broader chat-roleplay tooling ecosystem. These projects have decades of accumulated UX, file formats, and community conventions. Some of their conventions are worth absorbing; most of their architectural choices are at odds with Grimoire's thesis and adopting them piecemeal would dissolve the design.

This spec serves two purposes:

1. **Adopt deliberately.** Specify the small set of patterns Grimoire takes from neighbor projects, at enough depth to implement. Today: a Context Inspector, a SillyTavern character-card importer, expression sprites for characters, and a bounded macro system for greetings and style guides.
2. **Reject deliberately.** Name the anti-patterns Grimoire explicitly does not adopt, with the rationale tied back to design principles. This is the prophylactic against well-meaning future PRs that would slowly turn Grimoire into "yet another roleplay frontend."

The document is normative for what enters Grimoire from outside, and the rejected-patterns section is normative against what does not.

## Non-responsibilities

- Does not catalogue every feature of every neighbor project. Only those Grimoire interacts with, adopts, or pointedly rejects.
- Does not define mechanics. Combat, quests, party state — anything system-specific — is owned by the Mechanics API (`06-mechanics.md`), not absorbed from outside projects.
- Does not define interop protocols (e.g., a runtime bridge to SillyTavern). One-shot import is in scope; live federation is not.
- Does not document upstream projects. Their docs are theirs.

## Boundary tests

When an idea from a neighbor project lands on the table, four tests decide whether to adopt it, adapt it, or reject it. The tests are derived from the design principles in `00-overview.md`.

### Test 1 — Does it respect deterministic context assembly?

The Context Builder assembles each turn's prompt from explicit inputs, in a fixed order, under a token budget (`00-overview.md` principle 9). Any feature that lets the user tune prompt ordering at runtime, inject ad-hoc system prompts, or otherwise make context non-reproducible fails this test.

Adoption shape: features that *observe* or *constrain* assembly (inspection, pinning, exclusion) can pass. Features that *replace* assembly with a user-arranged template cannot.

### Test 2 — Does it preserve files as SSOT?

Library content is markdown + YAML files; campaign narrative is markdown + YAML sidecars. Anything readable, editable, greppable, or shareable lives in files (`00-overview.md` principle 2, `18-library.md`). A feature that requires a database row no human will ever read, or that depends on schema only the running app can understand, fails this test.

Adoption shape: features that read/write files in a documented format pass. Features that introduce opaque blobs do not.

### Test 3 — Does it stay out of the Mechanics lane?

The Mechanics API is the first-class extension surface for game rules (`06-mechanics.md`). Combat, dice, sheets, quests, encumbrance, party state — these belong to mechanics modules, not to the core app. A feature that ships generic combat handling, generic quest tracking, or any other system-shaped behavior duplicates and competes with mechanics. Fails this test.

Adoption shape: presentation layers, content adapters, and developer ergonomics pass. System logic does not.

### Test 4 — Is it bounded?

A bounded feature has a stable surface area: ten widgets, eight emotion labels, four template directives. An unbounded feature has a runtime DSL, a plugin marketplace inside another plugin marketplace, or 25+ orthogonal agents whose interactions are not specified. Unbounded features become the application; Grimoire is the application.

Adoption shape: features with finite, enumerable surface pass. Features that expand by accretion at runtime do not.

A feature must pass all four tests to be adopted in its imported shape. Failing one or more tests does not always mean rejection — sometimes a useful idea inside a bad shape can be re-imagined into a Grimoire-shaped feature. The Context Inspector below is an example: Marinara's "world info inspector" is a tool inside a non-deterministic prompt assembly system, but the underlying idea (let the user see what is going into the call) is even more valuable in a deterministic system, so it is adopted in a Grimoire-shaped form.

## Reference projects

Concretely scoped because vagueness here is how design rot starts.

- **SillyTavern.** The dominant chat-roleplay frontend. Source of the character card PNG format (v2/v3), lorebook keyword triggers, alternate greetings, expression sprite conventions, and the broader ecosystem of community-authored characters on Chub.ai and similar hubs. Grimoire reads SillyTavern's file formats so its users can bring content over; Grimoire does not adopt SillyTavern's prompt management.
- **Marinara Engine.** A more recent agentic chat-roleplay engine ("Fun. Intuitive. Plug-And-Play."). Source of the "world info inspector" idea, the three-mode UX (Conversation / Roleplay / Game), and the 25+ optional agent pattern. Grimoire takes the inspector concept and pointedly rejects the rest.
- **AI Dungeon, KoboldAI Lite, NovelAI, Risu, Agnaistic.** Acknowledged as part of the same problem space. Not currently sources of adoption; revisited if a specific pattern from any of them becomes load-bearing for a Grimoire user.

## Adopted: Context Inspector

### Concept and origin

Marinara surfaces a "world info inspector" — a panel that shows which lorebook entries and prompt fragments were assembled into the current call. The idea is small but unusually high-value in a deterministic system: when context assembly is reproducible, an inspector turns the assembly into a debugging tool with no holes. The user can ask "why is this entity in this prompt?" or "what filled the spotlight tier?" and get a complete answer.

Grimoire already has a post-hoc "What did the model see?" view in `16-observability.md`. The Context Inspector goes further: it is *pre-flight* and *live*. Before a turn is submitted, the user can see what the next call will look like given current state and the draft input. While typing or editing, the preview updates. This makes context assembly legible during play, not only during post-mortem.

### Placement

The Context Inspector lives at the seam between `02-context-builder.md` (which produces the assembly) and `14-frontend.md` (which renders the panel). It does not own assembly logic and does not own state. It is a query interface over the Context Builder's already-deterministic output, plus a small set of user-driven overrides that go through audited deltas.

This spec defines:
- The protocol the Context Builder exposes to support inspection.
- The data shape returned to the Frontend.
- The override model (`pin`, `exclude`) and how it is audited.
- UI affordances that are non-negotiable for the inspector to be useful.

Detailed UI styling lives in `14-frontend.md`; the visual design is intentionally not pinned here.

### Protocol

```python
class ContextInspector(Protocol):
    async def preview(
        self,
        campaign_id: str,
        scene_id: Optional[str],
        draft_input: Optional[str] = None,
        overrides: Optional[ContextOverrides] = None,
    ) -> ContextPreview: ...

    async def explain(
        self,
        preview_or_turn_id: PreviewHandle | TurnId,
        target: ContextTarget,
    ) -> InclusionReason: ...

    async def pin(
        self,
        campaign_id: str,
        entity_ref: EntityRef,
        ttl_turns: int = 3,
        reason: Optional[str] = None,
    ) -> PinId: ...

    async def exclude(
        self,
        campaign_id: str,
        entity_ref: EntityRef,
        ttl_turns: int = 3,
        reason: Optional[str] = None,
    ) -> PinId: ...

    async def clear_pin(self, pin_id: PinId) -> None: ...

    async def diff(
        self,
        preview: ContextPreview,
        against: TurnId | PreviewHandle,
    ) -> ContextDiff: ...
```

`ContextPreview` carries:

```python
@dataclass
class ContextPreview:
    preview_handle: PreviewHandle             # opaque, replayable
    composition_snapshot: CompositionSnapshot
    tiers: list[TierPreview]
    sources: list[ContextSource]              # scope + owner_id + version
    budget: BudgetReport                      # per-tier tokens used vs cap
    rolls_proposed: list[ProposedRoll]
    warnings: list[InspectorWarning]          # over-budget, missing entity, etc.
    generated_at: datetime
```

Each `TierPreview` lists the messages that filled that tier, the entities contributing to it, and the token count. `ContextSource` carries scope (`library` / `campaign-local` / `emergent`), owning asset id, version (where library-pinned), and whether an override was applied.

### Inclusion reasons

The inspector's central trick is that every chunk of the assembled prompt can be traced back to a reason. The Context Builder annotates each chunk with one or more reasons during assembly; the inspector reads those annotations directly.

The canonical reason vocabulary:

| Reason | Meaning |
|---|---|
| `present_in_scene` | Entity is in the scene's present cast |
| `mentioned_in_recent_posts` | Mentioned in the last N posts (N from config) |
| `commitment_open_to_pc` | Has an unresolved commitment with the active PC |
| `keyword_triggered` | Lore or entity matched a keyword in recent prose |
| `relationship_to_present` | Connected by relationship to a present entity |
| `pinned_by_user` | User pinned for this turn (or the next N) |
| `scene_anchor` | Entity is the scene's anchor (location, hosting NPC) |
| `mechanics_relevant` | Mechanics module asserted relevance (e.g., active power, ongoing combat) |
| `style_guide_active` | Style guide applies to this campaign |
| `pc_card` | The PC card itself |
| `composition_default` | Always-included content per composition |

Reasons compose. A character can be included for `present_in_scene` *and* `commitment_open_to_pc`. The inspector renders the full set, sorted by precedence.

### User overrides: pin and exclude

The single concession to user tuning is `pin` and `exclude`. Both take an entity ref and a TTL in turns. Pinned entities are guaranteed to appear in context (subject to budget; if pinning blows the budget the inspector raises a warning). Excluded entities are guaranteed to be omitted, regardless of other reasons.

Pins and excludes are recorded as state deltas (`context_pin_added`, `context_exclude_added`, `context_pin_expired`) in the audit log. They are NOT a way to reorder the prompt or inject text — only inclusion and exclusion of already-known entities. This preserves Test 1 (deterministic assembly): the user can constrain inputs but cannot rewrite the assembly logic.

TTL defaults to 3 turns. A pin set for "this turn only" auto-expires after the next `turn_complete` event. Pins survive scene transitions unless explicitly attached to a scene id.

### Diff

`diff(preview, against)` returns a structural diff against a prior turn or another preview: entities added/removed, tier budgets shifted, sources whose versions changed, deltas in the rolls proposed. The diff view is what makes "I tweaked one thing — what else moved?" tractable.

### UI affordances (non-negotiable)

The inspector panel is one of the four debug views named in `16-observability.md` ("What did the model see?") elevated to first-class status during play. The Frontend MUST render at least:

- Token budget bars per tier, color-coded by utilization
- Entity list per tier, click-through to inclusion reason
- Source attribution per chunk: scope, owner, version
- Live update as `draft_input` changes (debounced; see config)
- One-click pin/exclude with TTL selector
- Diff toggle against the previous turn

Out of scope for v1: editing prompt fragments inline, reordering tiers, runtime prompt templates. These would fail Test 1.

### Events

The inspector listens to `context_built` (already emitted by the Orchestrator) for completed assemblies, and produces `context_preview_built` for previews. Pin and exclude actions emit `context_pin_added` / `context_pin_cleared` / `context_pin_expired`.

### Storage

Pins are campaign-local SQLite rows (`context_pins`: pin_id, campaign_id, entity_ref, kind {pin, exclude}, created_at, ttl_turns, ttl_decrement, reason). They are NOT files — pins are transient play state, not content. They appear in the audit log; they do not appear in `data/campaigns/<id>/`.

Previews are not persisted. A `PreviewHandle` is good for the lifetime of a session; if the Frontend wants to compare across sessions it does so against a turn id, not a stale preview.

### Configuration

```yaml
context_inspector:
  preview:
    debounce_ms: 250
    auto_preview_on_input: true
  defaults:
    pin_ttl_turns: 3
    exclude_ttl_turns: 3
  warnings:
    over_budget: true
    missing_pinned_entity: true
    pin_displaced_other_content: true
```

### Open questions

- **Streaming previews vs. snapshot.** A live preview that updates every keystroke is expensive if context assembly touches embeddings. Likely answer: debounce, cache entity resolution per draft session, only recompute embeddings when input crosses a similarity threshold. Benchmark before deciding.
- **Preview during streaming response.** Should the inspector show what the *next* turn's context will look like while the current turn is still streaming? Probably yes; the answer is precomputed from current state plus an empty draft.
- **Multi-PC.** In a shared scene awaiting `advance`, each pending PC post can have its own draft. The inspector should show "the assembly that will result from the advance," which folds all pending posts into the scene tail. Specify in `10-scene-manager.md`'s advance flow.

## Adopted: Character card importer

### Concept and origin

SillyTavern character cards (v2 and v3) are the de facto interop format for AI-roleplay character content. A card is a PNG file with character metadata embedded in a `tEXt` chunk under the key `chara`, base64-encoded JSON. Tens of thousands of community characters exist on Chub.ai and similar hubs in this format. Importing them is a near-trivial win: a user with an existing SillyTavern library can be playing in Grimoire within minutes.

The importer reads SillyTavern v2 and v3 cards (and raw JSON exports), maps the fields into Grimoire's library model, and writes markdown + YAML files into a target setting. It is a one-shot conversion, not a live bridge.

### Field mapping

SillyTavern v2 fields map as follows. Where a Grimoire concept does not exist on the source side, the importer leaves the field empty for the user to fill in.

| SillyTavern v2 field | Grimoire destination |
|---|---|
| `name` | Character title; basis for asset id slug |
| `description` | Main markdown body, under a `## Description` heading |
| `personality` | `## Personality` section in body |
| `scenario` | Becomes a greeting (see below) |
| `first_mes` | Greeting's opening narration |
| `mes_example` | `## Example interactions` section, used by Characters for voice anchoring |
| `alternate_greetings[]` | Additional greeting files in the setting |
| `system_prompt` | Mapped to a campaign-scoped `system_addendum` field; **never** written into the character card itself (avoids leaking prompt-engineering into reference content) |
| `post_history_instructions` | Discarded with a warning. Grimoire owns post-history via the Context Builder; per-card overrides fail Test 1 |
| `character_book` (lorebook) | Setting-level `lore/` entries (see below) |
| `tags` | Character frontmatter `tags` list |
| `creator`, `creator_notes`, `character_version` | Character frontmatter `provenance` block |
| `extensions.depth_prompt` | Discarded with a warning (prompt-engineering concern; Grimoire does not adopt runtime prompt injection) |
| Avatar PNG (the file itself) | Stored as character avatar; see "Sprites and avatars" below |

V3 adds an `extensions` mechanism with arbitrary keys. The importer reads known v3 extensions (`risuai`, `chub`, `regex_scripts`) and discards them with one entry per discard in the import report. Regex scripts in particular are rejected by policy, not just unsupported (see "Rejected anti-patterns").

### Scenario and greetings

ST's `scenario` + `first_mes` pair is greeting-shaped: a brief setup plus an opening narration. The importer creates one greeting per (scenario, first_mes) tuple, where `alternate_greetings[]` produce additional greetings sharing the same scenario but different opening narration. Each greeting is written to `data/library/settings/<target-setting>/greetings/<character-slug>--<greeting-slug>.md`.

If the source card has no `scenario`, the importer creates a greeting with just the `first_mes` and an empty scenario block.

### Lorebooks

ST's `character_book` is a list of lorebook entries, each with keys (keyword triggers) and content. Grimoire's lore (`18-library.md`) has the same shape. The importer writes each entry to `data/library/settings/<target-setting>/lore/<slug>.md` with a YAML frontmatter block carrying `keys`, `priority`, `enabled`, and any other ST lorebook fields that map cleanly.

Two modes:

- **Setting-scoped (default).** Lorebook entries become setting-level lore, visible to any campaign using that setting. Appropriate when the lorebook describes the world the character lives in.
- **Character-scoped (opt-in).** Lorebook entries are namespaced to the character (`lore/<character-slug>/<entry-slug>.md`) and trigger only when the character is in scene. Appropriate when the lorebook describes the character's personal context (their employer, their secrets).

The importer asks once per import, with a recommendation based on heuristic: if any lorebook entry's keys reference the character name, default to character-scoped; otherwise setting-scoped.

### Target setting and asset ids

Three target modes:

1. **Existing setting.** User picks a setting. Imported content is merged in, with collision handling (see below).
2. **New setting from this card.** A new setting directory is created, named from the user's input. The card becomes its sole inhabitant initially.
3. **Bulk import bucket.** A catch-all setting named `imported` (created on demand). Useful when a user is bulk-importing dozens of community cards without curating settings yet.

Asset ids are slugged from the character name (lowercase, ASCII-folded, hyphenated). On collision, the importer appends a numeric suffix (`-2`, `-3`) and surfaces the duplication in the import report. The user can rename in their text editor after import; the watcher picks it up.

### Conflict and merge handling

When importing into an existing setting:

| Situation | Behavior |
|---|---|
| New asset id, no collision | Write directly |
| Asset id collision, content byte-identical | Skip with note |
| Asset id collision, content differs | Suffix new id, write, surface in report; the user resolves |
| Lorebook key conflicts with existing lore entry | Same as above for lore files |
| Avatar PNG already exists for that id | Suffix and write; never overwrite library assets |

The importer never overwrites a library file. The watcher and SQLite index handle the new files normally.

### Import report

Each import run produces an import report — a markdown file written to `data/library/imports/<timestamp>-<source-name>.md` — listing every file created, every field discarded, every collision resolved. This is the audit trail for "where did this character come from?"

The report is committed to the library; the user can read it, delete it, or leave it. Future imports of the same source are cross-referenced if a prior report exists.

### Protocol

```python
class CharacterCardImporter(Protocol):
    async def inspect(self, source: ImportSource) -> CardInspection: ...
    async def import_one(
        self,
        source: ImportSource,
        target: ImportTarget,
        options: ImportOptions,
    ) -> ImportResult: ...
    async def import_batch(
        self,
        sources: list[ImportSource],
        target: ImportTarget,
        options: ImportOptions,
    ) -> list[ImportResult]: ...

@dataclass
class ImportSource:
    kind: Literal["png_v2", "png_v3", "json_v2", "json_v3", "directory"]
    path: Path

@dataclass
class ImportTarget:
    setting_id: Optional[str]              # None → create new
    new_setting_name: Optional[str]
    lorebook_scope: Literal["setting", "character"]

@dataclass
class ImportOptions:
    discard_system_prompt: bool = False    # default: map to campaign system_addendum
    discard_extensions: bool = True
    on_collision: Literal["suffix", "skip", "fail"] = "suffix"
    write_avatar: bool = True
```

### Non-goals

- The importer does NOT execute character-card prompt logic (depth prompts, regex scripts, ST extensions). These are explicitly rejected; see the anti-patterns section.
- The importer does NOT support live bidirectional sync. v1 is one-shot import only.
- The importer does NOT produce SillyTavern-format output. That is an Export concern (`13-export.md`) and is a stretch goal for v2.

### Open questions

- **Avatars vs. sprites.** ST's avatar is a single PNG. Marinara and ST emotion packs add sprite sets keyed by emotion. The importer reads only the base avatar; sprite import is a separate workflow (next section). Decide whether the importer should *offer* to scaffold a sprite directory when it sees a ST emotion folder colocated with the card.
- **Provenance and licensing.** Many community cards have unclear licensing. Should the importer surface a license-unknown warning per import? Probably yes; cheap to implement.
- **PNG metadata stripping.** ST cards sometimes carry EXIF/IPTC blocks with personal metadata. The importer should strip non-essential PNG metadata when storing the avatar, keeping only the `chara` chunk for round-trip fidelity.

## Adopted: Expression sprites

### Concept and origin

SillyTavern and Marinara both support character expression sprites: a per-character set of images, keyed by emotion, displayed alongside or instead of a single static avatar. As the character speaks, the displayed sprite changes to match the emotional tone of the line. The effect is small visually but disproportionately effective at making roleplay feel like roleplay rather than text chat.

Sprites are a presentation-layer feature. They touch nothing in the deterministic context assembly, the state model, or mechanics. They are a clean fit for adoption.

### File layout

Character cards become optionally directory-shaped to hold sprite assets:

```
data/library/settings/<setting>/characters/
├── alistair-hyde-smythe.md                # simple form, no sprites
├── beatrice/                              # directory form, with sprites
│   ├── card.md                            # the character markdown
│   ├── avatar.png                         # base avatar (= neutral sprite if no neutral.png)
│   └── sprites/
│       ├── neutral.png
│       ├── happy.png
│       ├── angry.png
│       ├── thoughtful.png
│       └── ...
```

The Library spec (`18-library.md`) is amended to recognize both forms. `characters/<id>.md` and `characters/<id>/card.md` are equivalent; the indexer treats them the same. The asset id is the file stem in the simple form and the directory name in the directory form.

Style guides and image presets remain file-only — they have no asset bundle.

### Emotion vocabulary

A small, stable vocabulary of emotion labels — the **Grimoire Expression Vocabulary** (GEV). v1:

```
neutral, happy, sad, angry, surprised, fearful, disgusted,
smug, thoughtful, embarrassed, determined, hurt, tired, suspicious
```

Fourteen labels. Chosen to cover the common emotional range in TTRPG and roleplay prose without exploding into fine-grained taxonomy. Anything finer (e.g., distinguishing "smug" from "satisfied") is left to per-mechanics extension or per-character overrides — not core.

Fallback chain: if a requested emotion has no sprite, fall back to `neutral.png`; if no `neutral.png`, fall back to `avatar.png`; if no avatar, no sprite displayed for that character (the UI shows the character name only).

A mechanics module can declare extension labels via its manifest (`expression_vocabulary_extensions: [seductive, terrified, awakened]` etc.), which the importer and Frontend honor. Extensions namespace under the mechanics id to avoid collision (`wod.seductive`).

### Detection

Expression detection is an Extractor strategy (`04-extractor.md`). After each model response, the extractor:

1. Identifies which character speaks each paragraph (already a Characters-module concern, see `08-characters.md`).
2. For each character's spoken paragraphs in the turn, asks a small classification: which GEV label best fits this paragraph?
3. Emits `expression_changed` deltas: `{character_id, emotion, scene_id, post_id, confidence}`.

The classification can be rule-based (keyword + punctuation heuristic) for cheap detection, or LLM-based for higher accuracy. The Extractor's existing confidence-and-review-queue flow applies: low-confidence classifications are queued, not auto-applied.

### Display

The Frontend renders the current expression for each character relevant to the scene. The rules (specified in `14-frontend.md`):

- The character speaking the latest paragraph displays their current expression.
- In multi-character scenes, recent speakers retain their last-known expression until they speak again.
- A PC's own expression is set by the player (UI control), not by the extractor.
- A character not in the scene has no expression displayed.

The display is informational, not load-bearing. Disabling sprite display in settings hides the panel without changing anything else.

### Storage

Expression deltas are scene-local rows in SQLite (`expression_state`): turn_id, scene_id, character_id, emotion, set_at. The "current expression" view is a query, not a stored value.

Sprite files are content (in the Library) and are part of normal library export/backup. The watcher picks up new sprite files just like any other library file.

### Configuration

```yaml
expression:
  enabled: true
  detection:
    strategy: "rule_based"               # or "llm_classifier"
    llm_model: null                       # required if strategy = llm_classifier
    min_confidence_for_auto_apply: 0.7
  display:
    transition_ms: 200
    show_for_present_only: true
```

### Open questions

- **Sprite art conventions.** Community sprites come in many sizes, orientations, transparency conventions. Grimoire should not impose strict size requirements but should document a recommended profile (transparent PNG, ~512×768, character centered). Decide whether to auto-normalize on import or just document.
- **Animated sprites.** GIF or APNG support could come later. v1 is static images only.
- **Multiple emotions per paragraph.** A paragraph that swings from anger to grief could plausibly need two sprite changes. v1 picks one (the terminal emotion of the paragraph); v2 might subdivide.

## Adopted: Variant macros

### Concept and origin

SillyTavern's macro system supports template directives like `{{random: A,B,C}}` and `{{user}}` in greetings and instructions. Marinara has a similar (more elaborate) system documented in its `MACROS.md`. The useful part is small: when authoring opening narration for a greeting, the author wants to vary "the weather is overcast" vs "a cold drizzle is falling" without writing N nearly-identical greeting files.

Grimoire adopts a tightly bounded macro system, scoped to **greetings, style guides, and image presets only**. Not character cards (those are authored reference content; templates obscure the actual prose). Not scenes (those are model output and edited by the user; templates would create surprise). Not lore (encyclopedic content; not a place for variation).

### Surface

Four directives, total:

- `{{pick: a | b | c}}` — pick one variant uniformly at random.
- `{{pick weighted: 3:a | 1:b}}` — weighted random pick.
- `{{calendar: now}}` / `{{calendar: now+1d}}` — emit a date/time from the campaign's in-game calendar.
- `{{pc: name}}` / `{{pc: pronoun}}` — substitute attributes of the active PC. Multi-PC: the PC whose post triggered the turn.

That's the full surface. No conditionals, no loops, no variable assignment, no nested directives, no user-defined functions. If a use case requires more than these four directives, the use case belongs in mechanics or in an authored variant file, not in the template language.

### Expansion timing

Macros expand at consumption time, not at file-load time. The Context Builder expands macros when assembling the prompt; the State Store does not store the expanded form. This means:

- Re-reading the same greeting on the same turn produces stable output (seeded by turn id).
- Re-reading on a later turn may produce different output.
- The audit record captures both the source template and the expanded output for that turn.

Seed source: `hash(turn_id, template_path, directive_index)`. Deterministic within a turn; varied across turns.

### Errors

Malformed macro syntax raises a library-validation warning at index time (the file still loads; the warning appears in the Library view). Unknown directives are passed through unchanged with a warning. The Context Builder never silently swallows broken templates.

### Configuration

No configuration. The directive set is fixed; the expansion model is fixed.

### Open questions

- **Localization.** If a future spec adds locale-aware date/time formatting, the `calendar` directive grows a format argument. Not v1.
- **Per-greeting overrides.** Should a greeting be able to declare seed-source semantics ("vary per session" vs "vary per turn")? Defer until a real use case appears.

## Rejected anti-patterns

The following patterns appear in neighbor projects and would, if adopted, break Grimoire's design. Each rejection is paired with the alternative Grimoire provides for the underlying need.

### Rejected: agent grab-bag

**The pattern.** Marinara ships 25+ optional agents (world-state, quest tracker, combat handler, expression detector, background selector, prose analyzer, DJ, CYOA chooser, …). Each agent is an independently-prompted LLM call augmenting or critiquing the main response.

**Why rejected.** Fails Test 3 (Mechanics lane) and Test 4 (bounded). Combat, quests, world state, mechanical effects belong to the Mechanics API. Implementing them as loose prompts duplicates and competes with mechanics modules, produces unaudited state changes, and makes campaign behavior depend on which subset of agents the user happened to enable.

**Grimoire's alternative.** Typed modules with explicit responsibilities (Continuity, Scene Manager, Time Engine, Extractor) for cross-cutting concerns. The Mechanics API for system-specific behavior. The Extractor's strategy pipeline for "look at the output and extract something" — that pipeline is the right place for new behaviors, because everything it produces is a typed delta routed through the State Store with audit and confidence.

### Rejected: runtime prompt reordering

**The pattern.** SillyTavern's preset system lets the user drag-and-drop the order of prompt fragments at runtime, inject system prompts mid-conversation, and arrange chunks into arbitrary configurations per persona, per character, per chat.

**Why rejected.** Fails Test 1 (deterministic assembly). The Context Builder is the single component that assembles each turn's prompt, from explicit typed inputs, in a fixed order, under a token budget. Letting the user reorder at runtime makes the assembled prompt non-reproducible, breaks replay, and makes drift diagnosis impossible.

**Grimoire's alternative.** The Context Inspector (above). Users see exactly what is being assembled and can constrain inputs (`pin`, `exclude`) but cannot rewrite the assembler. Style guides and content boundaries handle the legitimate "I want this kind of prose" need without becoming a prompt-editing surface.

### Rejected: regex scripts and runtime DSLs

**The pattern.** SillyTavern's regex scripts intercept and rewrite model output (and player input) based on regex matches, with optional placement controls and per-character scoping. Used to redact, reformat, inject side narration, trigger custom behaviors.

**Why rejected.** Fails Test 2 (files as SSOT — regex scripts are opaque) and Test 4 (bounded — a regex DSL with runtime side effects has no natural ceiling). Worst of all, they live alongside reference content (character cards) and silently mutate output, making "what did the model actually say?" unanswerable.

**Grimoire's alternative.** If a behavior needs to inspect output and act, it is an Extractor strategy or an Orchestrator event handler — typed code in a known module, producing typed deltas with audit. Output is not silently rewritten; if the system wants to redact or annotate, it does so as a structured post-process step the user can see.

### Rejected: three hard chat modes

**The pattern.** Marinara bifurcates the UI into Conversation, Roleplay, and Game modes, each with different affordances, default agents, and visual treatments. The user picks a mode per chat.

**Why rejected.** Mostly redundant. Grimoire's scene abstraction with `mechanics: null` already covers "conversation" (one PC, freeform style guide, no mechanics). With mechanics installed and a more immersive style guide, it covers "roleplay." With multiple PCs and combat-aware mechanics, it covers "game." Adding a mode selector duplicates choices already encoded in setting, composition, and mechanics, and creates a "wrong mode" failure category.

**Grimoire's alternative.** Style guides, composition, and mechanics. The user picks what kind of campaign they want by choosing those three things, not by picking a UI mode.

### Rejected: AI-generated content into library SSOT

**The pattern.** Marinara's "AI lorebook maker" generates lore entries automatically and writes them as if they were authored. SillyTavern has similar tooling in community extensions.

**Why rejected.** Fails Test 2 in spirit if not letter. Library is the user's authored content; writing generated content directly into it creates canon that the user did not author but that the system treats as authoritative. Subsequent campaigns inherit hallucinated facts.

**Grimoire's alternative.** Generated content goes to the **emergent** scope (`data/campaigns/<id>/emergent/`), which is campaign-local and clearly separated from library. The user can review and promote emergent content into the library via an explicit action — the same flow used for any other emergent entity (`00-overview.md`, "Mid-play emergent character → promotion"). If a future "generate a setting from a prompt" tool exists, it writes to a *staging* directory, not directly to `data/library/`, and the user reviews and accepts files individually.

### Rejected: depth prompts and post-history instructions on character cards

**The pattern.** SillyTavern v2 character cards carry `system_prompt`, `post_history_instructions`, and (via extensions) `depth_prompt` — strings injected at specific positions in the assembled prompt, controlled by the card author.

**Why rejected.** Fails Test 1 — the card author is editing the assembled prompt at runtime, in a position-aware way, on every turn the character appears. This is the inverse of "the Context Builder assembles deterministically." Community cards routinely include long depth prompts and complex post-history-instructions that work in SillyTavern's slot-based prompt system and would not even be meaningful in Grimoire's tier-based assembly.

**Grimoire's alternative.** The importer reads these fields and logs them in the import report so the user can see what was discarded. A `system_addendum` field on the campaign (not the character card) lets the user add a stable string to every turn's system tier in a single, visible location. If a behavior really needs character-specific prompt influence, it belongs in the character's authored card prose (which the Context Builder includes via standard inclusion reasons), not in a side channel.

### Rejected: live bidirectional sync with neighbor frontends

**The pattern.** Some community projects support live bridges to other tools — SillyTavern reading from a backend, Marinara mirroring to a remote endpoint.

**Why rejected.** Out of scope and a maintenance burden. Grimoire is single-user local-first in v1. A bridge that depends on another project's API ties Grimoire's release cycle to theirs.

**Grimoire's alternative.** One-shot import (this spec) and Export (`13-export.md`). Round-trip is possible via export-then-import; live bridges are not.

## Cross-references

This spec touches several modules. Authoritative details remain in those specs; this spec describes only the parts adopted from external influences.

| Module | What this spec adds or constrains |
|---|---|
| `02-context-builder.md` | Inclusion reason annotations on every chunk; macro expansion at consumption time |
| `04-extractor.md` | New strategy: expression detection emitting `expression_changed` deltas |
| `08-characters.md` | Per-paragraph speaker → expression mapping for the sprite display |
| `13-export.md` | Stretch: SillyTavern-format export as the inverse of the importer |
| `14-frontend.md` | Context Inspector panel; sprite display rules |
| `16-observability.md` | Inspector elevates the "What did the model see?" debug view into a live pre-flight tool |
| `18-library.md` | Directory-form character cards (`characters/<id>/card.md`); sprite directory layout; importer's target structure |

Each of those specs is amended to point back here for the externally-influenced details and to remain authoritative for the module-internal details.

## Process: how new external influences enter

When a future contributor wants to adopt a pattern from another project:

1. Identify the pattern. Name the source project, the concrete feature, and the underlying user need.
2. Run the four boundary tests. Write the result down — passing, failing, or "passes if reshaped."
3. If it fails one or more tests, propose either a reshape that passes them or an alternative Grimoire-shaped feature that meets the same user need.
4. Add a section to this spec — either "Adopted: …" or "Rejected: …" — with the same shape as the existing entries.
5. Cross-reference the touched module specs and amend them.

The point of this discipline is that influences arrive as design decisions, not as accreted features. A project the size of Grimoire is a tractable thing because every part of it can be held in one head; that property only survives if new surface area is added deliberately.

## Open questions

- **Provenance metadata.** Every imported asset has provenance (source URL, original creator, license). Should provenance be a first-class library frontmatter field across all entity kinds, not just imports? Probably yes — it would also serve user-authored content.
- **Sprite generation.** If Grimoire's ImageGen module can produce coherent character art (`12-imagegen.md`), generating a sprite set from a base avatar is a plausible automation. Stretch — needs sprite-consistency techniques that are themselves an open research area.
- **Reverse exports.** A `grimoire-to-silly-tavern` exporter would let users move out as easily as they moved in, and is good ecosystem citizenship. Specify in `13-export.md` once the importer is stable.
- **Plugin-shaped extensions to this spec.** Should "external project adapters" themselves be plugins, so that, e.g., a Risu importer can be installed without modifying Grimoire? Probably yes for the import side; the importer protocol above is plugin-shaped already.
- **Macro evaluation cost.** If a greeting has many `pick` directives and a long prose body, expansion is cheap but still O(N) per turn. Cache expanded greetings per turn-id if the same greeting is read multiple times in one turn (it can be, from different modules).
