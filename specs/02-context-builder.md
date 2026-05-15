# 02 — Context Builder

## Purpose

The Context Builder is the single component responsible for assembling the prompt sent to the LLM each turn. It is the primary fix for context drift, missed characters, and inconsistent voice — by replacing "Claude hopes to load the right files" with a deterministic, budget-aware, layered assembly pipeline.

The Context Builder is **scope-aware**: every entity it loads is resolved through the campaign's composition (campaign-local → library refs → fail) so a campaign that composes `faerun` characters + `wod-nyc` locations gets the right cards from the right sources, transparently. Domain modules (Characters, World) handle the resolution; the Context Builder consumes resolved entities.

This module is heavily inspired by SillyTavern's prompt manager and world info system, with structural improvements around budget management, tier promotion, and cross-scope composition.

## Responsibilities

- Receive turn inputs (player message, mechanics results, scene state, active campaign id)
- Query other modules for resolved entities (Characters, World, Scene Manager, Continuity) — entities are returned with library + campaign-local layers already merged
- Allocate a token budget across content tiers
- Promote/demote characters and content between tiers based on screen time and relevance
- Run retrieval (vector + keyword) scoped to the campaign's effective context (campaign-local + referenced library assets)
- Apply per-campaign style and content boundary guides (resolved from library or inline)
- Produce a final ordered message list ready for the LLM Gateway
- Annotate each context source with its scope (library asset, campaign-local, override) for observability

## Non-responsibilities

- Does not call the LLM (Gateway does)
- Does not decide if a roll is needed (Mechanics does, and returns a result that Context Builder embeds)
- Does not own character data, scene data, or facts (their owning modules do)
- Does not perform scope resolution itself (Characters, World do; Context Builder consumes resolved entities)
- Does not parse responses (Extractor does)

## Interface

```python
class ContextBuilder(Protocol):
    async def build(
        self,
        player_input: str,
        campaign_id: str,
        mechanics_results: list[MechanicsResult] = [],
        extra: Optional[str] = None,
    ) -> AssembledPrompt: ...

    async def estimate(self, player_input: str, campaign_id: str) -> BudgetEstimate: ...
    # estimate is for UI: shows the user how the budget will be spent before they commit
```

```python
@dataclass
class AssembledPrompt:
    messages: list[Message]            # ordered, ready for LLM
    params: ModelParams                # temperature, max_tokens, etc.
    budget_used: dict[Tier, int]       # tokens per tier for audit
    sources: list[ContextSource]       # what got included and why
    summary: str                       # human-readable for logs/UI
```

## The tier system

Context is organized into four tiers. Each tier has a configurable token budget; over-budget content is dropped from the bottom up.

### Lock-in tier (always included)
- Campaign system prompt (voice rules, content boundaries, content style)
- Active PC card (always full detail)
- Current scene header (location, time, present characters, mood)
- Last 2 posts verbatim (recent context)
- Active commitments and open foreshadowing (compact)
- Mechanics results from this turn (if any)

If lock-in tier overflows the budget, that's a configuration error — the system surfaces it rather than silently dropping.

### Spotlight tier (current scene + immediate relevance)
- Full cards for all present characters
- Detailed location description
- Recent posts beyond the lock-in window (configurable, default 6 more)
- Active scene's running summary
- Foreshadowing relevant to present characters

### Background tier (compressed)
- Compressed cards for offscreen characters who are mentioned or relevant
  - "Mentioned in last 10 posts," "has open commitment to PC," "in same household as present chars"
- Adjacent location info (rooms in the same building, faction context for current location)
- Faction state if politically relevant to current scene
- Calendar / world-time context (season, weather, ongoing events)

### Archive tier (retrieval-based)
- Vector-retrieved snippets from past scenes
- Keyword-matched fact ledger entries
- Specific past scenes by tag (e.g., "first meeting with NPC X")

Retrieval queries are built from:
- Player input text
- Names of characters in the current scene
- Active plot threads
- Location

## Tier promotion logic

Characters and content move between tiers automatically:

**Promote to spotlight:**
- Character appears in current scene
- Character is named in last 3 posts
- Character has an active commitment with PC

**Promote to background:**
- Character is in same household / location as scene chars
- Character was in last 10 posts
- Character has any active plot thread with PC
- Character was promoted in last 3 sessions (cooldown to avoid churn)

**Demote:**
- Character has not appeared in N posts (N = configurable, default 20)
- Character's plot threads are all resolved or stale
- Character is from a different era / branch

User can pin a character to a tier (e.g., "always keep Alistair at spotlight") to override automatic promotion.

## The build pipeline

```
build(player_input, campaign_id, mechanics_results, extra):
  0. resolve composition
     ├─ load campaign's asset refs (worlds, style guide, image preset)
     ├─ load campaign's mechanics module id
     └─ resolve style guide (library asset or inline)

  1. resolve scene state
     ├─ current scene from Scene Manager (campaign-scoped)
     ├─ present characters from scene state
     └─ current location from scene state

  2. resolve cast
     ├─ promote present chars to spotlight
     ├─ promote referenced chars to background
     ├─ apply user pins (campaign-scoped)
     └─ for each tier, fetch resolved cards via Characters
         (Characters applies cascade: campaign-local → library refs)

  3. resolve world
     ├─ current location resolved via World (cascade)
     ├─ adjacent locations if relevant
     ├─ weather, time, atmosphere from World (uses campaign seed)
     └─ faction state if politically active (cascade)

  4. resolve continuity
     ├─ active commitments from Continuity (campaign-scoped)
     ├─ unpaid foreshadowing
     ├─ recent facts (last 50 facts in compact form)
     └─ relationship deltas since last scene

  5. resolve archive (parallel)
     ├─ vector retrieval scoped to campaign (campaign-local + ref'd library assets)
     ├─ keyword fact lookup
     └─ explicit scene refs (if player input references past scenes)

  6. budget allocation
     ├─ allocate per tier
     ├─ pack each tier within budget (priority order)
     └─ if lock-in overflows, error
     └─ if other tiers overflow, demote lowest-priority content

  7. assembly
     ├─ assemble in canonical order (system → lock-in → spotlight → background → archive → recent posts → player input)
     ├─ apply resolved style guide
     ├─ apply content boundaries
     ├─ annotate every source with its scope (library asset id, or campaign-local)
     └─ return AssembledPrompt
```

## Canonical message order

```
[System]
  - System prompt
  - Style guide
  - Content boundaries
  - Campaign meta (genre, tone, era)

[Lock-in: structured]
  - Scene header (location, time, present chars, mood, weather)
  - Active PC card
  - Active commitments (compact)
  - Mechanics results (if any)

[Spotlight: structured]
  - Present character cards (full, one per character)
  - Location detail
  - Scene running summary

[Background: structured]
  - Offscreen character cards (compressed)
  - Adjacent locations (compact)
  - Faction state (compact, if relevant)
  - Calendar / weather

[Archive: structured]
  - Retrieved past scenes (3–5 snippets)
  - Relevant fact ledger entries

[Recent posts: conversational]
  - Last 8 posts in standard chat format

[Player input: user message]
```

## Budget management

The Context Builder is configured with a target window size (e.g., 200k tokens for Claude) and per-tier allocations:

```yaml
context_builder:
  total_budget: 180000           # leave headroom for response
  reserve_for_response: 20000
  tiers:
    lock_in:
      max: 8000                  # hard cap
      priority: required          # error if can't fit
    spotlight:
      max: 40000
      priority: high
    background:
      max: 30000
      priority: medium
    archive:
      max: 20000
      priority: low
    recent_posts:
      max: 30000
      n_posts: 8
      priority: high
  retrieval:
    vector_top_k: 8
    keyword_top_k: 5
    similarity_threshold: 0.65
```

Token counts are estimated per-message using the LLM Gateway's tokenizer (or a fast approximation).

## Style guide and content boundaries

Each campaign supplies a style guide and content boundary doc. These are short, opinionated, and always included verbatim in the system block. Examples:

- "Prose style: present-tense, third-limited, focal POV PC. Avoid purple prose. Dialogue tags minimal."
- "Voice anchors: NPC X uses Y speech patterns. NPC Z never uses contractions."
- "Content: explicit content permitted between consenting adult characters; no minors in sexual contexts."

These are user-supplied. A campaign can either reference a library style guide (reusable across campaigns) or provide inline text. The Context Builder resolves whichever applies and embeds it verbatim.

## Retrieval scope

Retrieval is **campaign-scoped**, but the corpus for retrieval includes both campaign-local content and content from referenced library assets:

- **Campaign-local sources**: past scenes, posts, facts, commitments, campaign-local character cards, location overrides
- **Library sources** (filtered by composition): character cards from referenced worlds, location descriptions from referenced worlds, lore entries triggered by keywords

Vector and keyword search filter by `(scope='campaign-local' AND owner_id=campaign_id) OR (scope='library' AND owner_id IN campaign's referenced asset ids)`. This prevents leak from unrelated campaigns and from unreferenced library assets.

## Voice anchors (Characters integration)

When a character is promoted to spotlight, their card includes:
- Identity, background, relationships
- **Voice anchor**: a short prose sample (50–200 words) of the character speaking in their canonical voice
- **Dialogue do/don't list**: explicit guidance ("never apologizes," "uses 'darling' as address")
- Recent emotional state and what they want right now

Voice anchors are critical for drift prevention. See `08-characters.md`.

## Mechanics injection

If the Mechanics returned results for this turn, they are injected as:

```
[Lock-in: mechanics]
Mechanical results for this turn (treat as authoritative; do not contradict):
- {PC} attempted Sword + Finesse vs DR 7. Result: 9 (success by 2).
- {NPC} attempted Stealth + Awareness vs DR 5. Result: 4 (failure by 1).
The narrative should reflect these outcomes.
```

This prevents the model from inventing dice results or contradicting rolls. See `06-mechanics.md`.

## Retrieval implementation

Two retrieval paths run in parallel:

**Vector retrieval** — embeddings over past post chunks, scene summaries, and fact ledger entries. Top-K most similar to the assembled query vector (player input + scene context).

**Keyword retrieval** — explicit lookups: every proper noun in the recent posts is searched against the fact ledger. Hits surface as compact entries ("Fact #4521: winifred promised PC a riding lesson on Day 312").

Both feed into the archive tier with deduplication.

## Drift mitigation hooks

The Context Builder participates in drift mitigation by:
- Always including the voice anchor for the spotlighted speaker(s) of the next post
- Surfacing recent direct dialogue from each spotlighted character (last 3 posts where they spoke)
- Including the "dialogue don't" list as system-level instructions

The Characters module computes drift scores periodically (see `08-characters.md`); when drift is detected, the Context Builder is asked to include extra voice anchors and explicit corrective guidance.

## Open questions

- **Caching.** If the same scene is replayed (regenerate), should the assembled prompt be cached? Probably yes, with a hash key over inputs (including referenced library asset versions, so a library edit invalidates cache).
- **Multi-shot examples?** For very voice-sensitive characters, would few-shot dialogue examples help? Experimentally, yes. Should be a per-character toggle.
- **Cost-aware tiering.** When using expensive models, automatically reduce spotlight depth. When using cheap models, expand it. Configurable but should be transparent.
- **Mining SillyTavern.** Specifically: lorebook entry triggers, world info recursive scanning, character V2/V3 card structure, prompt manager presets. These map cleanly to our tier system.
- **Cross-asset deduplication.** If two referenced worlds both contain a character named "Margaret," both surface in resolved cast. Should the Context Builder dedupe by name (risking incorrect merges) or always show both with source labels (better, default)?
- **Library asset weighting.** Campaigns may want to emphasize one referenced asset over another in retrieval (this campaign primarily uses world A; world B is supplementary). Reflect priority order in retrieval weights? Probably yes; trivial to implement once priority is known.
