# 11 — Continuity

## Purpose

The Continuity module is the campaign's memory of what's been *promised* and what's been *established*. Facts, commitments, foreshadowing, and contradictions live here. It prevents the campaign from forgetting that winifred said she'd take julian to the orchard, and it flags it when the model contradicts an established fact.

If the State Store is *what happened*, Continuity is *what it means going forward*.

All of Continuity's content is **campaign-scoped**. Facts about a character or place that emerge in one campaign are not visible in others, even if the character is a library asset shared between campaigns. Library characters carry biographical and structural information in their card (`background`, `personality`); time-stamped events live in the campaign that produced them.

## Responsibilities

- Maintain the fact ledger (atomic, retrievable, dated facts — campaign-scoped)
- Maintain the commitment ledger (promises, threats, obligations, foreshadowing — campaign-scoped)
- Track commitment status (open, due, overdue, paid, broken, stale)
- Detect and flag contradictions between new facts and existing facts within the campaign
- Provide queries the Context Builder uses to surface relevant facts and commitments
- Age commitments based on in-game time
- Surface "you've forgotten about X" warnings when commitments stagnate

## Non-responsibilities

- Does not store posts or scenes (State Store + Scene Manager do)
- Does not generate facts from prose (Extractor does, then writes them here)
- Does not summarize (Scene Manager does)
- Does not own characters (Setting owns the storage, Characters owns character-specific behaviors; facts reference characters by ref, which resolves through library)
- Does not surface facts across campaigns — facts are per-timeline by design

## Fact ledger

A fact is an atomic, dated, attributable statement of in-fiction truth:

```python
@dataclass
class Fact:
    id: str
    text: str
    established_in_post: str
    established_at_in_game: InGameTime
    confidence: float
    source: FactSource              # NARRATOR, CHARACTER_TESTIMONY, INFERRED, USER_DECLARED
    speaker_id: Optional[str]       # if from character testimony
    about: FactSubject
    keywords: list[str]
    embedding: Optional[Embedding]
    retired: bool
    retired_in_post: Optional[str]
    contradicts: list[FactId]
    tags: list[str]                 # secret, public, rumor, etc.

@dataclass
class FactSubject:
    character_ids: list[str]
    location_ids: list[str]
    faction_ids: list[str]
    item_ids: list[str]
    scope: str                      # private, household, public, world
```

Facts are produced by the Extractor and written to Continuity. The user can also declare facts directly via UI.

## Fact retirement

Facts can be retired without being deleted:

- **Refuted**: a later fact directly contradicts
- **Superseded**: state changed (e.g., "winifred is in Sion" retired when she returns)
- **Retconned**: user edited the source post

Retired facts remain queryable for archive retrieval. Retirement is a timestamped event, not a deletion.

## Commitments

A commitment is a future-pointing obligation, expectation, or hook:

```python
@dataclass
class Commitment:
    id: str
    kind: CommitmentKind            # PROMISE, THREAT, FORESHADOW, OBLIGATION, MYSTERY
    text: str
    created_in_post: str
    in_game_created_at: InGameTime
    from_id: Optional[str]
    to_id: Optional[str]
    due_by: Optional[InGameTime]    # null = open-ended
    status: CommitmentStatus        # OPEN, PAID, BROKEN, STALE, OVERDUE
    weight: int                     # 1-5: significance
    resolved_in_post: Optional[str]
    tags: list[str]
    related_fact_ids: list[str]
```

## Commitment lifecycle

```
OPEN     -> PAID         (fulfilled in scene)
OPEN     -> BROKEN       (explicitly violated)
OPEN     -> OVERDUE      (due_by passed without resolution)
OPEN     -> STALE        (no due_by, long inactivity)
OVERDUE  -> PAID         (late but resolved)
OVERDUE  -> BROKEN       (acknowledged as broken)
STALE    -> REOPENED     (becomes relevant again)
```

The Time Engine, during advancement, ages commitments:
- OPEN + due_by passes -> OVERDUE
- OPEN + inactivity > N months in-game -> STALE (configurable)

Continuity emits events on status changes; Context Builder picks up overdue and about-to-be-due commitments.

## Foreshadowing in particular

Foreshadowing is a CommitmentKind because narratively it has the same shape: something was planted, expected to pay off later. Without explicit tracking, foreshadowing gets forgotten. Structured continuity ensures the foreshadowing list never silently disappears.

The Frontend surfaces unpaid foreshadowing prominently. When a scene closes, Scene Manager notes which foreshadowing it paid off, and Continuity updates status.

## Contradiction detection

When a new fact is proposed:

```python
async def check_contradictions(self, new_fact: Fact) -> list[ContradictionReport]:
    # 1. Vector search for similar facts (top-K)
    # 2. For each candidate, LLM call: "Does fact A contradict fact B?"
    # 3. Return list of conflicts with confidence and rationale
```

Contradictions don't auto-resolve. They surface to the user:

> Conflict detected:
> - Existing fact (Day 247): "winifred has never left Greenwich County"
> - New fact (Day 312): "winifred visited Sion as a child"
>
> [Keep existing, drop new] [Replace existing] [Both true, no conflict] [Edit either]

Silent contradictions corrode the campaign. Explicit resolution maintains trust.

## Knowledge state per character

Some facts are known only to some characters:

```python
@dataclass
class KnowledgeEntry:
    fact_id: str
    character_id: str
    knows: bool
    learned_in_post: Optional[str]
    source: str                     # told by X, witnessed, deduced
```

Used for:
- Filtering Context Builder output by POV
- Preventing model from making characters react to things they don't know
- Tracking secrets

## Retrieval

Two retrieval paths feed into the Context Builder:

**Direct query (scene-driven)**
```python
await continuity.facts_about(character_ids=[julian, winifred], limit=10)
await continuity.open_commitments(involving=[julian], limit=10)
await continuity.recent_facts(scene=current_scene, limit=20)
```

**Keyword retrieval (prose-driven)**

When proper nouns or topic keywords appear in recent posts, Continuity looks up tagged facts and surfaces them.

## Interface

```python
class Continuity(Protocol):
    # Fact writes
    async def add_fact(self, fact: Fact, source: str) -> FactId: ...
    async def retire_fact(self, id: str, in_post: str, reason: str) -> None: ...
    async def update_fact(self, id: str, patch: dict) -> Fact: ...

    # Fact reads
    async def get_fact(self, id: str) -> Fact: ...
    async def facts_about(self, **subjects) -> list[Fact]: ...
    async def search_facts(self, query: str, top_k: int = 10) -> list[Fact]: ...
    async def recent_facts(self, since: InGameTime, limit: int = 50) -> list[Fact]: ...

    # Contradictions
    async def check_contradictions(self, candidate: Fact) -> list[ContradictionReport]: ...
    async def resolve_contradiction(self, report_id: str, resolution: dict) -> None: ...

    # Commitments
    async def add_commitment(self, c: Commitment, source: str) -> CommitmentId: ...
    async def resolve_commitment(self, id: str, status: CommitmentStatus, in_post: str) -> None: ...
    async def get_commitment(self, id: str) -> Commitment: ...
    async def open_commitments(self, **filters) -> list[Commitment]: ...
    async def overdue_commitments(self, as_of: InGameTime) -> list[Commitment]: ...
    async def stale_commitments(self, threshold: Duration) -> list[Commitment]: ...

    # Knowledge state
    async def knows(self, character_id: str, fact_id: str) -> bool: ...
    async def reveal(self, fact_id: str, to: list[str], in_post: str, source: str) -> None: ...
    async def secrets_of(self, character_id: str) -> list[Fact]: ...

    # Time advancement
    async def age(self, to_time: InGameTime) -> AgingReport: ...
```

## Surfacing to the user

Continuity-derived info appears in:

1. **Context Builder injection**: relevant facts and active commitments per turn
2. **UI panel**: "campaign ledger" view with open commitments, recent facts, contradictions
3. **Pre-scene briefing**: when a scene opens, surface active threads
4. **End-of-session digest**: new facts, new commitments, resolved commitments, pending obligations
5. **Export**: included in EPUB exports as appendices

## Configuration

```yaml
continuity:
  fact_confidence_floor: 0.5
  commitment_stale_threshold: 6M    # 6 months in-game
  contradiction_check:
    enabled: true
    top_k_similar: 5
    model: claude-haiku-4-5
  keyword_retrieval:
    min_keyword_length: 4
    case_insensitive: true
  surface_overdue_in_context: true
  surface_stale_in_context: false   # only on demand
```

## Open questions

- **Fact granularity.** Should a complex disclosure be one fact or several? Heuristic: one per atomic claim. Extractor breaks them up.
- **Inferred facts.** Should the system propose inferred facts ("if A and B, then C")? Possibly, with extra-low confidence and explicit "inference" source.
- **Fact graph visualization.** Future feature.
- **Commitment prioritization.** Heuristic: weight x recency x due-proximity. Tunable.
- **Auto-resolve stale.** Should commitments auto-resolve to "abandoned" after N years? User preference; default to surface, not auto-resolve.
