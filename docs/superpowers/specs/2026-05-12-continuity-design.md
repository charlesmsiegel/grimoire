# Continuity — Design (Shipped)

> Captures the Continuity module as actually built. The matching "remaining" spec at `2026-05-16-continuity-remaining-design.md` covers everything from the original `specs/11-continuity.md` that did **not** land in this work.

**Commit:** `b48d2c0` — "Implement Continuity module (task 18)" (followed by `24251f0` which added the SQLite store, hybrid search, and LLM judge)
**Module:** `backend/src/grimoire/continuity/`
**Tests:** `backend/tests/continuity/` (test_facts, test_commitments, test_contradictions, test_knowledge, test_aging, test_sqlite_store, test_hybrid_search, test_llm_judge)

## Purpose

Continuity is the campaign's memory of what's been *promised* and what's been *established*. Facts, commitments, foreshadowing, and contradictions live here. It is the seam the Context Builder calls on every turn to surface relevant facts and active commitments, and the seam the Time Engine calls on every advance to age commitments.

Continuity is **campaign-scoped** at the persistence layer: every row in `facts` / `commitments` / `knowledge_state` / `contradiction_reports` carries `(campaign_id, branch_id)` and `SqliteContinuityStore` is bound to one (campaign_id, branch_id) pair at construction (`sqlite_store.py:261-272`).

## Module surface

The public surface is the `Continuity` Protocol (`protocols.py:93`). `ContinuityService` (`service.py:98`) is the default implementation. Persistence, search, and contradiction judgment are three separate Protocols injected at construction so any can be swapped without touching the service:

- `ContinuityStore` — persistence seam
- `FactSearchIndex` — top-K similarity over the fact ledger
- `ContradictionJudge` — pairwise "do these two facts contradict?" judgment

Two implementations of each ship today:

| Protocol | Default | Production |
| --- | --- | --- |
| `ContinuityStore` | `InMemoryContinuityStore` | `SqliteContinuityStore` |
| `FactSearchIndex` | `KeywordFactSearchIndex` (Jaccard over text tokens + stored keywords) | `HybridFactSearchIndex` (FTS5 + sqlite-vec RRF) |
| `ContradictionJudge` | `StubContradictionJudge` (always UNCERTAIN, confidence 0) | `LLMContradictionJudge` |

`ContinuityService()` with no arguments wires the in-memory triple; that's what `main.py:139-143` constructs today. Swapping in the SQLite/hybrid/LLM trio is a constructor-args change.

## Types

All types are dataclasses in `continuity/types.py`:

- `Fact` — atomic dated claim; `id`, `text`, `established_in_post`, `established_at_in_game`, `confidence`, `source`, `about: FactSubject`, optional `speaker_id`, `keywords`, `embedding`, retirement fields (`retired`, `retired_in_post`, `retired_reason`), `contradicts: list[FactId]`, `tags`.
- `FactSubject` — `character_ids`, `location_ids`, `faction_ids`, `item_ids`, `scope` (`private | household | public | world`).
- `Commitment` — `id`, `kind: CommitmentKind`, `text`, `created_in_post`, `in_game_created_at`, `weight (1-5)`, `from_id`, `to_id`, `due_by`, `status: CommitmentStatus`, `resolved_in_post`, `last_activity_at`, `tags`, `related_fact_ids`. `last_activity_at` is not in the original spec; it was added to support aging without scanning related posts.
- `KnowledgeEntry` — `(fact_id, character_id, knows, learned_in_post, source)`.
- `ContradictionReport` — `id`, `candidate_fact`, `conflicts: list[ContradictionCandidate]`, `resolved`, `resolution: dict | None`.
- `ContradictionCandidate` — one (existing fact, similarity, verdict, confidence, rationale) row inside a report.
- `AgingReport` — `from_time`, `to_time`, `became_overdue: list[Commitment]`, `became_stale: list[Commitment]`.
- `InGameTime(day_count, label)` and `Duration(days)` — minimal placeholders. The module deliberately tracks time as an integer day count so it never has to know about per-world calendars; the Time Engine converts its datetime-based `InGameTime` into Continuity's day-count form before calling `age()` (`time_engine/service.py:154`).

Enums: `FactSource` (NARRATOR, CHARACTER_TESTIMONY, INFERRED, USER_DECLARED), `RetirementReason` (REFUTED, SUPERSEDED, RETCONNED), `CommitmentKind` (PROMISE, THREAT, FORESHADOW, OBLIGATION, MYSTERY), `CommitmentStatus` (OPEN, PAID, BROKEN, OVERDUE, STALE), `ContradictionVerdict` (CONFLICT, NO_CONFLICT, UNCERTAIN), `ContradictionResolutionAction` (KEEP_EXISTING, REPLACE_EXISTING, BOTH_TRUE, EDIT_NEW, EDIT_EXISTING).

`TERMINAL_STATUSES = {PAID, BROKEN}` (`types.py:132`) — these statuses don't age further.

## Public API

```python
class ContinuityService(Continuity):
    # Fact writes
    async def add_fact(fact, source) -> FactId
    async def retire_fact(fact_id, in_post, reason) -> None
    async def update_fact(fact_id, patch) -> Fact

    # Fact reads
    async def get_fact(fact_id) -> Fact                                              # raises FactNotFoundError
    async def facts_about(*, character_ids=None, location_ids=None,
                          faction_ids=None, item_ids=None,
                          limit=50, include_retired=False) -> list[Fact]
    async def search_facts(query, top_k=10) -> list[Fact]
    async def recent_facts(since: InGameTime, limit=50) -> list[Fact]

    # Contradictions
    async def check_contradictions(candidate: Fact) -> ContradictionReport
    async def resolve_contradiction(report_id, resolution: dict) -> None

    # Commitments
    async def add_commitment(c: Commitment, source) -> CommitmentId
    async def resolve_commitment(cid, status, in_post) -> None                       # status must not be OPEN
    async def get_commitment(cid) -> Commitment                                      # raises CommitmentNotFoundError
    async def open_commitments(*, involving=None, limit=50) -> list[Commitment]
    async def overdue_commitments(as_of: InGameTime) -> list[Commitment]
    async def stale_commitments(threshold: Duration) -> list[Commitment]

    # Knowledge state
    async def knows(character_id, fact_id) -> bool
    async def reveal(fact_id, to: list[str], in_post, source) -> None
    async def secrets_of(character_id) -> list[Fact]

    # Time advancement
    async def age(to_time: InGameTime) -> AgingReport
```

Errors raised by the service: `FactNotFoundError`, `CommitmentNotFoundError`, `ContradictionReportNotFoundError`, `ConfidenceFloorError` (all in `service.py:47-60`).

## Key flows

### Adding a fact (`add_fact`, `service.py:119`)

1. Reject if `fact.confidence < config.fact_confidence_floor` (raises `ConfidenceFloorError`).
2. Generate a `fact_<12-hex>` id if `fact.id` is empty.
3. Append `src:<source>` to `fact.tags` if not already present — source attribution is encoded as a tag rather than a separate column.
4. `store.put_fact(fact)`.
5. Return the id.

Contradiction detection is **not** invoked from `add_fact`. Callers (Extractor today; user-declare flow tomorrow) are expected to call `check_contradictions(candidate)` first and resolve before writing.

### Retiring a fact (`retire_fact`, `service.py:135`)

Validates the `reason` string against `RetirementReason` (raises `ValueError` with the allowed values), then writes a copy of the fact with `retired=True`, `retired_in_post=<post>`, `retired_reason=<enum>`. The fact stays queryable when callers pass `include_retired=True`. There is no delete path.

The SQLite store has no `retired_reason` column; `sqlite_store.py:104` encodes it as a `retired_reason:<value>` entry in the `tags` JSON column and parses it back on read.

### Updating a fact (`update_fact`, `service.py:154`)

Patches are a `dict` of allowed top-level field names. Unknown keys raise `ValueError`. The nested `about: FactSubject` is special-cased: a dict patch is merged field-wise; a `FactSubject` instance replaces it wholesale.

### Searching facts

- `facts_about(...)` filters the full fact list by subject id overlap. If the caller passes no id filters it returns everything (subject-filter-or-pass semantics, `service.py:83-95`), then sorts by `established_at_in_game` desc and slices to `limit`.
- `search_facts(query, top_k)` delegates to the injected `FactSearchIndex`. Today this is keyword-based by default; in production with `HybridFactSearchIndex` it merges FTS5 keyword hits with sqlite-vec cosine hits via reciprocal-rank fusion (k=60, `hybrid_search.py:81`). If no embedder is wired, the vector half returns `[]` and the result is keyword-only.
- `recent_facts(since, limit)` linearly scans non-retired facts whose `established_at_in_game >= since`.

### Contradiction detection (`check_contradictions`, `service.py:220`)

1. If `config.contradiction_check.enabled` is False, return an empty report immediately (still persisted so callers have a report id).
2. `search.search(candidate.text, top_k=config.contradiction_check.top_k_similar)` — find the top-K similar existing facts.
3. For each candidate (skipping any whose id matches the candidate fact's id), call `judge.judge(candidate, existing)`. If the judge returned no similarity score but the search index supplied one, splice it in.
4. Keep only `ContradictionVerdict.CONFLICT` entries with `confidence > 0` (UNCERTAIN entries are dropped at this stage).
5. Build a `ContradictionReport(id="contra_<12-hex>", candidate_fact=candidate, conflicts=[...])` and persist via `store.put_contradiction_report`.

The `LLMContradictionJudge` (`llm_judge.py`) short-circuits one case before calling the model: if both facts have `source=CHARACTER_TESTIMONY` and different `speaker_id`s, it returns UNCERTAIN with rationale "distinct testimonies — judgement deferred". The model receives the rendered `continuity_judge_system` + `continuity_judge_user` templates (`templates/continuity_judge_user/default.j2`), responds with a JSON object `{"verdict", "confidence", "rationale"}`, and the judge clamps `confidence` to `[0, 1]`, truncates `rationale` to 280 chars, and defaults to UNCERTAIN on any parse failure.

### Resolving a contradiction (`resolve_contradiction`, `service.py:250`)

Resolution dict shape:

```
{
  "action": "keep_existing" | "replace_existing" | "both_true"
            | "edit_new" | "edit_existing",
  "in_post": <post_id>,
  "patch": <dict, for edit_*>,
  "target_fact_id": <optional, when multiple conflicts>,
}
```

When `target_fact_id` is omitted and the report has conflicts, the first conflict's existing fact is the implicit target. The five actions:

- `KEEP_EXISTING` — drop the candidate; do not write it.
- `REPLACE_EXISTING` — retire the target with reason REFUTED, write the candidate with the target id appended to `candidate.contradicts`.
- `BOTH_TRUE` — write the candidate as a normal fact; no retirement.
- `EDIT_NEW` — apply `patch` to the candidate, then write.
- `EDIT_EXISTING` — apply `patch` to the target, then write.

Resolved reports are written back with `resolved=True` and `resolution=<dict>`. Calling `resolve_contradiction` twice raises `ValueError("already resolved")`.

### Commitment lifecycle and aging (`age`, `service.py:448`)

`add_commitment` mints a `com_<12-hex>` id, appends `src:<source>` to tags, defaults `last_activity_at` to `in_game_created_at` if unset.

`resolve_commitment(cid, status, in_post)`:
- Refuses `status=OPEN` (raises `ValueError`).
- Sets `resolved_in_post` only when the new status is in `TERMINAL_STATUSES` ({PAID, BROKEN}).

`age(to_time)` walks all commitments, computes `from_time` as the oldest live `last_activity_at`, and applies these transitions:

| From | Condition | To |
| --- | --- | --- |
| OPEN | `due_by is not None and due_by < to_time` | OVERDUE |
| OPEN | `due_by is None and (to_time - max(last_activity_at, in_game_created_at)) >= config.commitment_stale_threshold` | STALE |

Terminal statuses are skipped. The `AgingReport` returns `became_overdue` and `became_stale` (the spec also lists REOPENED — not implemented; see remaining doc).

### Open/overdue/stale queries

- `open_commitments(involving=None, limit=50)` reads OPEN + OVERDUE rows, optionally filters by id overlap on `from_id`/`to_id`, sorts by `(−weight, due_by or +∞, in_game_created_at)`, slices to `limit`.
- `overdue_commitments(as_of)` walks every non-terminal commitment and returns those whose `due_by < as_of`, sorted by `(due_by, −weight)`. (It looks at `due_by` directly rather than the OVERDUE status — so an OPEN with a passed `due_by` will appear here even if `age` hasn't run yet.)
- `stale_commitments(threshold)` returns commitments with `status=STALE`, sorted by `in_game_created_at`. The `threshold` arg is currently accepted but ignored — stale-flagging happens in `age`; the parameter exists to satisfy the protocol.

### Knowledge state

- `knows(character_id, fact_id)` → `True` iff a knowledge entry exists with `knows=True`.
- `reveal(fact_id, to: list[str], in_post, source)` writes a `KnowledgeEntry(knows=True, learned_in_post=in_post, source=source)` per character. Missing fact raises `FactNotFoundError`.
- `secrets_of(character_id)` lists facts the character knows that are tagged `secret` and aren't retired.

## SQLite persistence

`SqliteContinuityStore` (`sqlite_store.py:254`) binds to the State Store's `facts`, `commitments`, `knowledge_state`, and `contradiction_reports` tables (migrations 005 and 009). One instance = one (campaign_id, branch_id) pair so every read and write naturally scopes.

Round-trip details:
- `InGameTime` is serialized as a JSON `{"day_count", "label"}` string into the `in_game_when` / `due_by` / `in_game_created_at` columns.
- `FactSubject` is serialized as a JSON object into the `about` column.
- `keywords`, `contradicts`, `tags`, `related_fact_ids` are JSON-encoded lists.
- `retired_reason` is encoded as a tag (`retired_reason:<value>`) because the schema predates that field.
- `Commitment.last_activity_at` is encoded inside a `_meta:{...}` tag for the same reason.
- `ContradictionReport.candidate_fact` and each `ContradictionCandidate` are serialized via dedicated `_serialise_fact` / `_serialise_candidate` helpers (`sqlite_store.py:569-660`) and stored as JSON blobs.

Writes use `INSERT … ON CONFLICT(id) DO UPDATE SET …` for upsert semantics. The contradiction-report `created_at` is set on first insert; `resolved_at` is populated only when `resolved=True`.

The `facts_fts` FTS5 virtual table and the after-insert/update/delete triggers (migration 005) keep keyword search in sync with the `facts` table.

## Hybrid search

`HybridFactSearchIndex` (`hybrid_search.py:36`) does keyword + vector and merges via reciprocal-rank fusion:

```
fused[id] += 1 / (rrf_k + rank + 1)         # rrf_k=60
```

Each half oversamples `top_k * 2` candidates, then the fused top-K is materialised via `store.get_fact`. Retired facts are filtered post-fusion unless `include_retired=True`.

Keyword half (`_keyword_search`): `bm25(facts_fts)` ranking over the FTS5 table, joining to `facts` for scope (`campaign_id`, `branch_id`, `retired`). `_sanitise_fts_query` strips FTS5 operator characters and joins tokens with `OR` so a free-text query never errors on a colon or quote.

Vector half (`_vector_search`): only runs if a `QueryEmbedder` was provided. Embeds the query via `embedder.embed(embed_task, [query])` (default task `"extractor"`), packs as little-endian float32 bytes, and runs `vec_distance_cosine` against the `embeddings` table where `source_kind='fact'`. Any embedder failure logs and degrades to keyword-only.

## Configuration

```python
ContinuityConfig(
    fact_confidence_floor=0.5,
    commitment_stale_threshold=Duration.months(6),  # 180 days
    contradiction_check=ContradictionCheckConfig(
        enabled=True,
        top_k_similar=5,
        model_route="drift_check",      # LLM Gateway task name
    ),
    keyword_retrieval=KeywordRetrievalConfig(
        min_keyword_length=4,
        case_insensitive=True,
    ),
    surface_overdue_in_context=True,
    surface_stale_in_context=False,
)
```

Today only `fact_confidence_floor`, `commitment_stale_threshold`, `contradiction_check.{enabled, top_k_similar}` and `keyword_retrieval.min_keyword_length` are consumed by the service. `model_route`, `case_insensitive`, `surface_overdue_in_context`, `surface_stale_in_context` exist for API stability and are deferred to the remaining-design spec.

## Error handling

- `add_fact` with `confidence < floor` → `ConfidenceFloorError`.
- `retire_fact` / `get_fact` / `update_fact` on a missing id → `FactNotFoundError`.
- `retire_fact` with an unknown reason string → `ValueError` listing the valid enum values.
- `update_fact` with an unknown field name in the patch → `ValueError`.
- `resolve_commitment(..., status=OPEN)` → `ValueError`.
- `resolve_contradiction` on a missing report → `ContradictionReportNotFoundError`. On an already-resolved report → `ValueError`. With a `target_fact_id` not in the report's conflicts → `ValueError`.
- `LLMContradictionJudge`: any LLM exception is caught, logged at WARNING, and returns UNCERTAIN with rationale `"judge unavailable: <ExceptionType>"`. An unparseable response returns UNCERTAIN with rationale `"unparseable judge response"`. Confidence out of `[0,1]` is clamped, not rejected.
- `HybridFactSearchIndex`: any FTS or vector SQL error is logged via `logger.exception` and that half returns `[]` (the other half still contributes).

## Surfacing today

Continuity is wired into:

- **Context Builder** (`context/builder.py:547-600`) — `_render_commitments` puts up to 10 open commitments into the `LOCK_IN` tier as `Active commitments: …`. `_continuity_background` emits up to 8 recent facts into the `BACKGROUND` tier as `Fact: …`. Both swallow exceptions and degrade to empty.
- **Time Engine** (`time_engine/service.py:478-481`) — on every advance, converts to `ContinuityInGameTime(day_count=…)` and calls `continuity.age(...)`. The returned `AgingReport` is included in the time-advance result.
- **Export** (`export/snapshot.py:219-221`, `export/epub.py:601-704`) — EPUB exports include a `Continuity Ledger` appendix with facts and commitments when the `continuity` appendix is enabled.
- **API** (`api/campaigns.py:787-835`) — `GET /campaigns/{id}/facts`, `POST /campaigns/{id}/facts`, `GET /campaigns/{id}/commitments`. Each endpoint over-fetches and filters by `campaign_id` client-side because today's `ContinuityService` instance is shared across campaigns (see remaining doc §1).

## Test wiring

Tests construct `ContinuityService()` with defaults (in-memory store, keyword index, stub judge). The `service` fixture is in `tests/continuity/conftest.py:54`. The SQLite store has its own integration tests (`test_sqlite_store.py`) that exercise round-tripping every type and prove branch isolation. The hybrid search tests (`test_hybrid_search.py`) cover both the embedder-present and embedder-absent paths.
