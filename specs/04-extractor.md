# 04 — Extractor

## Purpose

The Extractor parses each model response and produces a list of structured `StateDelta` proposals. It is the bridge between freetext model output and the structured state model. High-confidence deltas auto-apply; low-confidence ones queue for user review.

All extracted entities — new NPCs, locations, facts, commitments — default to **campaign-local** scope. The Extractor never writes to library scope without explicit user action (promotion). A new NPC named in scene 47 of one campaign does not silently become a library character; she lives in that campaign until the user chooses to share her across campaigns.

This is the second component (after Context Builder) that makes the app meaningfully better than a freeform chat — by ensuring storage happens automatically and consistently rather than relying on the model to remember to write files.

## Responsibilities

- Parse the latest model response for state-relevant content
- Produce typed `StateDelta` objects with confidence scores
- Identify candidate new entities (NPCs introduced, locations mentioned) — always proposed as campaign-local
- Detect mechanical events that the model narrated (wounds, item gains, time passage)
- Detect emotional and relationship changes
- Detect commitments and foreshadowing (campaign-scoped)
- Produce a "needs review" item for anything ambiguous
- Detect when prose modifies a library entity and propose a campaign-local override (never a library edit)

## Non-responsibilities

- Does not apply state changes (State Store does)
- Does not decide if a roll was warranted (Mechanics does, pre-roll)
- Does not summarize scenes (Scene Manager does, on close)
- Does not maintain voice anchors (Characters does)
- Does not write to library scope (only the user, via explicit edit or promote-to-library, modifies library data)

## Interface

```python
class Extractor(Protocol):
    async def extract(
        self,
        response_text: str,
        scene: Scene,
        campaign_id: str,
        prior_state_snapshot: StateSnapshot,
    ) -> ExtractionResult: ...

    async def extract_from_user_text(
        self,
        user_text: str,
        scene: Scene,
        campaign_id: str,
    ) -> ExtractionResult: ...
    # User posts also contain state-relevant info (their declared actions)
```

```python
@dataclass
class ExtractionResult:
    deltas: list[StateDelta]
    candidates: list[EntityCandidate]      # new NPCs, locations, items proposed
    flags: list[ExtractionFlag]            # warnings, contradictions, ambiguities
    confidence_overall: float
```

## Extraction strategies

The Extractor combines multiple strategies, run in parallel, with their outputs deduplicated and merged.

### 1. Rule-based extraction (high confidence, narrow scope)

Regex and pattern matching for high-precision items:
- Time markers: "the next morning," "three days later," "an hour passes"
- Explicit mechanical results from the model echoing dice results
- Direct character actions: "X picked up the Y," "X gave Y to Z"
- Wounds and damage: matched against mechanics-defined patterns
- Inventory keywords: "produces," "hands over," "takes"

These are deterministic, fast, and high-confidence.

### 2. Structured LLM extraction (medium confidence, broad scope)

A second LLM call with a structured output schema. The Extractor sends:
- The new response text
- A compact snapshot of relevant state (so the model knows what's already known)
- A schema for the expected output

The schema requests a list of typed deltas: facts established, character state changes, location changes, commitments made, etc. Output is JSON, parsed and validated.

This is the workhorse. It runs against every response. It uses a cheaper/faster model than the main one (e.g., Haiku tier).

### 3. Heuristic flagging

The Extractor watches for known drift signals:
- A character is named but their card was not in context this turn → flag possible inconsistency
- A new proper noun appears that isn't in the library → candidate NPC
- A location is described in detail that contradicts the location card → flag contradiction
- A fact is asserted that contradicts an existing fact → flag contradiction
- A wound or stat change is narrated but no mechanical roll happened → flag missing mechanic

Flags are surfaced to the UI but don't block the turn.

## Output schema

The structured LLM extraction targets this schema:

```json
{
  "facts": [
    {
      "text": "winifred revealed she has been writing to her uncle in Sion",
      "about": {"character_ids": ["winifred"], "location_ids": ["sion"]},
      "confidence": 0.9
    }
  ],
  "character_updates": [
    {
      "character_id": "vivienne",
      "field": "emotional_state",
      "before": "calm",
      "after": "guarded",
      "evidence": "She crossed her arms and didn't meet his eyes",
      "confidence": 0.7
    }
  ],
  "new_characters": [
    {
      "proposed_name": "Margaux",
      "role": "minor NPC",
      "evidence": "A new maid named Margaux brought the tea",
      "confidence": 0.8
    }
  ],
  "scene_changes": [
    {
      "kind": "location_change",
      "to_location": "the orchard",
      "evidence": "They walked out to the orchard after breakfast",
      "confidence": 0.95
    }
  ],
  "time_advances": [
    {
      "delta": "PT2H",                 // ISO 8601 duration
      "evidence": "Two hours passed",
      "confidence": 1.0
    }
  ],
  "commitments": [
    {
      "kind": "promise",
      "text": "winifred promised to teach julian to ride",
      "from": "winifred",
      "to": "julian",
      "due": null,
      "confidence": 0.9
    }
  ],
  "inventory_changes": [
    {
      "character_id": "julian",
      "item": "silver ring",
      "delta": "+1",
      "evidence": "vivienne slipped the silver ring onto his finger",
      "confidence": 0.95
    }
  ],
  "mechanical_events": [
    {
      "kind": "wound",
      "character_id": "julian",
      "amount": "light bashing",
      "evidence": "He took a glancing blow to the shoulder",
      "confidence": 0.8
    }
  ],
  "relationship_changes": [
    {
      "from": "julian",
      "to": "winifred",
      "field": "trust",
      "delta": "+1",
      "evidence": "He felt a flicker of warmth that surprised him",
      "confidence": 0.6
    }
  ],
  "commitment_resolutions": [
    {
      "commitment_id": "c_4521",
      "outcome": "paid",
      "evidence": "She made good on her promise and taught him the saddle",
      "confidence": 0.9
    }
  ]
}
```

## Confidence scoring

Confidence is computed per-delta based on:

- **Strategy**: rule-based deltas start at 0.95; LLM-based start at the model's reported confidence
- **Evidence strength**: explicit narration ("winifred said") > implication ("winifred's hand twitched") > inference
- **Consistency with existing state**: contradicting an established fact reduces confidence
- **Speaker authority**: the GM-voice narrator is more authoritative than a character's claim ("she lied that she had..." is a low-confidence fact)

Thresholds (configurable):
- `>= 0.85`: auto-apply
- `0.6 – 0.85`: queue for review
- `< 0.6`: drop with log entry

## Entity candidates

When a new proper noun or unknown character appears, the Extractor proposes an entity candidate. New entities **always default to campaign-local scope**. These are surfaced as UI prompts:

> "A character named **Margaux** was introduced as a maid. Add her to this campaign?"
> [Add as minor NPC (campaign-local)] [Add and edit] [Ignore]

After adding, a secondary "promote to library" action is available in the character's detail view for the user to share her with other campaigns.

Approved candidates trigger Characters's character creation flow with the LLM-suggested initial card at campaign-local scope; the user can refine.

The same pattern applies to locations introduced in prose, factions named in dialogue, lore mentioned in passing — all start campaign-local, can be promoted later.

## Library-targeted changes

When the Extractor detects prose modifying an entity that resolves through a library asset (e.g., the model describes vivienne's appearance differently from her library card), it does **not** propose a library edit. Instead it proposes a campaign-local override:

> "vivienne's appearance was described as having 'a streak of grey at her temple.' Her library card doesn't mention this."
> [Add as campaign-local override] [Edit library card] [Treat as transient (drop)]

This protects library assets from drift caused by per-campaign play. Library edits are explicit, user-driven actions.

## Handling player text

Player input is also extracted. Players narrate their own actions ("julian walks to the window, his hand trembling"), and these are state-relevant. The Extractor processes both player and model text.

Rule of thumb: things the player declares about their PC's internal state are taken at face value (high confidence). Things the player declares about NPCs or the world are proposed but flagged (the GM-model's response gets to ratify or deny).

## Contradictions and the contradiction log

When a delta contradicts existing state, the Extractor doesn't silently apply or drop it. Instead:

1. The new delta is queued for review with the contradiction flagged
2. The existing fact or state is included in the review item
3. The user resolves: keep old, replace with new, or merge

This catches retcons, model hallucinations, and player corrections.

## Performance

Extraction runs in parallel with the user's reading of the response (the response streams to the UI; extraction starts immediately). For a typical post, target extraction latency is < 3 seconds using a fast model.

If extraction is still running when the user submits the next turn, the turn waits briefly; if extraction times out (configurable), the turn proceeds with partial extraction and the remainder is queued for review.

## Configuration

```yaml
extractor:
  model: claude-haiku-4-5            # cheap fast model
  parallel_strategies:
    - rule_based
    - structured_llm
    - heuristic_flags
  auto_apply_threshold: 0.85
  review_threshold: 0.60
  timeout_seconds: 30
  max_new_entities_per_turn: 5      # safety against runaway NPC inflation
```

## Open questions

- **Self-consistency check.** Should the Extractor be run twice with different models and the results merged? Probably overkill for v1.
- **Player-authored state.** How much should we trust player declarations vs. GM ratification? The above heuristic is a starting point; needs tuning.
- **Schema evolution.** The output schema will grow. Versioning the schema and migrating stored deltas is needed long-term.
- **Confidence calibration.** The threshold values are guesses. We need a feedback loop: when users reject auto-applied deltas, lower the threshold; when they always approve queued ones, raise it.
