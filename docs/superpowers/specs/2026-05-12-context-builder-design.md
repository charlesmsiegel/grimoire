# Context Builder — Design (Shipped)

> Captures the Context Builder design as actually built. The matching "remaining" spec at `2026-05-16-context-builder-remaining-design.md` covers everything from the original `specs/02-context-builder.md` that did **not** land in this work.

**Commit:** `b7f1e7c` — "Build Context Builder (task 20)" (templates later extracted in `e13de4a`; "setting → world" rename in `87e0643`)
**Module:** `backend/src/grimoire/context/`
**Tests:** `backend/tests/context/test_builder.py`

## Purpose

The Context Builder assembles the prompt sent to the LLM each turn. It is the single deterministic, tier-aware pipeline that replaces "hope the right files were loaded" with a budget-driven assembly: pull resolved entities from the domain modules, sort them into four tiers, pack each tier inside a token budget, and emit a canonical `AssembledPrompt`. Style guides, content boundaries, mechanics results, and drift correctives are embedded verbatim. Archive retrieval (vector + keyword) is scoped to the campaign's composition.

## Module surface

`ContextBuilderService` (`context/builder.py`) is constructed with duck-typed collaborators so tests can wire in fakes for any subset:

- `library` — `get_composition`, `get_world`, `get_style_guide`
- `characters` — `active_pc`, `get_full_card`, `get_compressed_card`, `drift_corrective_context`
- `world` — `get_location`, `weather_for`, `adjacent_locations`, `lore_for_post`
- `scenes` — `active_scene_for_campaign`, `recent_posts`
- `continuity` — `open_commitments`, `facts_about`
- `mechanics` (optional — currently unused inside the builder; results are passed into `build()` by the orchestrator)
- `gateway` (optional) — `embed`, `estimate_tokens`
- `state_store` (optional) — `vector_search`, `keyword_search`
- `config: ContextBuilderConfig`

Tokenization is delegated to the gateway's `estimate_tokens` when available; the fallback is `len(text) // config.chars_per_token` (`context/tokens.py`).

## Public API

```python
class ContextBuilderService:
    async def build(
        player_input: str,
        campaign_id: CampaignId,
        mechanics_results: list[MechanicsResult] | None = None,
        extra: str | None = None,
        *,
        branch_id: str | None = None,
        pc_ref: str | None = None,
    ) -> AssembledPrompt

    async def estimate(
        player_input: str,
        campaign_id: CampaignId,
        *,
        branch_id: str | None = None,
        pc_ref: str | None = None,
    ) -> BudgetEstimate
```

The matching `ContextBuilder` protocol lives at `backend/src/grimoire/types/protocols.py:916`. The shipped service is a strict superset (adds `branch_id` and `pc_ref` keyword args).

`AssembledPrompt` (`backend/src/grimoire/types/context.py`) carries:

- `messages: list[Message]` — ordered, ready for the gateway
- `params: ModelParams` — temperature + max_tokens
- `budget_used: dict[ContextTier, int]` — per-tier token spend for audit
- `sources: list[ContextSource]` — every contributing source with scope/owner/tier/tokens
- `summary: str` — one-line log line
- `composition_snapshot: dict` — full `Composition` JSON for cache invalidation and reproducibility
- `messages_hash: str` — SHA-256 over `(role, content)` pairs

## The seven-step pipeline

`_build_context` walks the spec-02 pipeline; `_assemble` turns the result into the message list.

0. **Composition** — `library.get_composition(campaign_id)`. Resolves the style guide (inline body wins; otherwise `library.get_style_guide(id)`) and renders a `system_meta` line naming the worlds in play.
1. **Scene state** — `scenes.active_scene_for_campaign(campaign_id, branch_id or "main")` plus `scenes.recent_posts(scene_id, n=config.recent_posts_n)`. The scene header is rendered from title/location_ref/in_game_start/mood/present_character_refs.
2. **Cast** — active PC (passed-in `pc_ref` or `characters.active_pc(campaign_id)`) becomes a lock-in card via `get_full_card`. Present-but-not-PC characters become spotlight items. Any token in the last few post bodies that starts with `library:` or `campaign:` is treated as a mention; up to `background_character_limit` (default 6) of them are pulled via `get_compressed_card` into background. Finally, `drift_corrective_context` is called for the active PC and every present character; the joined output is the **voice corrective** snippet injected into the system block.
3. **World** — `_parse_location_ref` recognises `library:worlds/<world>/locations/<id>`. From there: full location description → spotlight; current `weather_for` → spotlight; `adjacent_locations` (compact names list) → background. The scene's `running_summary` is also pushed to spotlight when present.
4. **Continuity** — `open_commitments(limit=20)` becomes the lock-in commitments block (first 10 rendered with optional `due_by.day_count`). `facts_about(limit=8)` becomes background fact items.
5. **Archive retrieval** — parallel vector + keyword paths. The query is built from player input + present-character refs + location ref + the bodies of the last three posts. Vector path: `gateway.embed(retrieval.embedding_task, [query], campaign_id=...)` then `store.vector_search(query_vector=..., campaign_id=..., include_library=..., top_k=retrieval.vector_top_k)`. Keyword path: `store.keyword_search(query=..., campaign_id=..., kinds=retrieval.keyword_kinds, top_k=...)`. Hits are deduped by `ref` (vector wins). Hits are scored with `priority = int(score * 10)` (vector) or `* 5` (keyword).
5a. **Lore triggers** — `world.lore_for_post(player_input, campaign_id)`. Each triggered lore body (clamped to 400 chars) goes into archive with a `library:worlds/<id>/lore/<id>` source.
6. **Budget allocation** — handled inside `_assemble`. Items are sorted by `-priority` and packed until the tier budget is exhausted; over-budget items are silently dropped. Lock-in is special-cased: if the rendered lock-in block exceeds its tier budget, `LockInOverflowError` is raised (spec: "configuration error — surface it rather than silently dropping").
7. **Assembly** — see "Canonical message order" below.

## Canonical message order

`_assemble` builds the message list in this order:

1. **System block** (`context_system_block` template) — concatenates style guide, content boundaries, `system_meta` (worlds in play), and the voice corrective snippet.
2. **Lock-in block** (`context_lock_in_block` template) — scene header, active PC card, commitments block, mechanics block, and the trailing `lock_in_verbatim_posts` (default 2) posts rendered verbatim. Emitted as a single SYSTEM message.
3. **Spotlight tier block** — `_pack_tier` with `label="Spotlight"`. Items packed high-priority first within `tiers[SPOTLIGHT].max_tokens` (default 40k). Wrapped by `context_tier_block`. Per-item token cost is stamped back into `item.source.tokens` for the audit trail.
4. **Background tier block** — same, default 30k.
5. **Archive tier block** — same, default 20k.
6. **Older recent posts block** (`context_recent_older_block`) — posts beyond the lock-in verbatim window, gated on `recent_posts_budget` (default 30k) as an all-or-nothing check.
7. **User message** — `player_input` as `MessageRole.USER`.
8. **Extra** (optional) — `extra` text appended as a second USER message when supplied.

## Tier promotion logic (as implemented)

The full spec-02 promotion matrix is not yet built. Today's behaviour:

- **Lock-in:** active PC card (always), scene header, mechanics results, open commitments, last 2 posts verbatim.
- **Spotlight:** characters in `scene.present_character_refs` (excluding the active PC); the spotlighted location's description; current weather; scene running summary.
- **Background:** non-present characters whose refs appear as `library:…` / `campaign:…` tokens in the last few post bodies (capped by `background_character_limit`); adjacent location names; recent facts.
- **Archive:** vector hits, keyword hits, lore keyword triggers.

User pins, "last 10 posts" mention scans, household / faction adjacency rules, and the "active commitment with PC" promotion path from spec 02 are not implemented — see the remaining doc, §3.

## Source attribution

Every contributing item carries a `ContextSource` (`backend/src/grimoire/types/context.py`):

```python
ContextSource(kind=..., scope=..., owner_id=..., tier=..., tokens=..., summary=...)
```

Refs beginning with `library:` get `scope="library"` and `owner_id=ref`; other refs get `scope="campaign-local"` and `owner_id=campaign_id`. Library locations and lore use synthetic refs (`library:worlds/<world>/locations/<id>` and `library:worlds/<world>/lore/<id>`). Vector and keyword hits propagate the scope reported by the store.

## Caching surface

`AssembledPrompt.messages_hash` is the SHA-256 of `role\x00content\x01` for every message in order. `composition_snapshot` is a JSON dump of the resolved `Composition`. Together these are the inputs a caller would key a regenerate-cache on (the spec-02 open question on caching). The builder itself does not cache; it surfaces the hash so callers can.

## Mechanics injection

`_render_mechanics(results)` renders the spec-02 lock-in mechanics block verbatim:

```
Mechanical results for this turn (treat as authoritative; do not contradict):
- {actor} attempted {kind}[ vs {target}] (pool {pool}). Result: {successes} successes ({outcome}).
  {summary}
The narrative should reflect these outcomes.
```

The orchestrator passes the resolved `MechanicsResult` list into `build(...)`; the builder folds it into the lock-in block template.

## Retrieval scope

`store.vector_search` and `store.keyword_search` are called with `campaign_id` and (for vector) `include_library=config.retrieval.include_library` (default `True`). The store is expected to filter by `(scope='campaign-local' AND owner_id=campaign_id) OR (scope='library' AND owner_id IN campaign's referenced asset ids)` — spec 02 §Retrieval scope. The builder does not enforce that filter itself; it trusts the store.

`config.retrieval.embedding_task` defaults to `"extractor.embed"` so the same gateway-routed embedding task as the extractor is reused.

## Configuration (`ContextBuilderConfig`)

`context/config.py`:

```python
ContextBuilderConfig(
    total_budget=180_000,
    reserve_for_response=20_000,
    tiers={
        LOCK_IN:    TierBudget(max_tokens=8_000,  priority="required"),
        SPOTLIGHT:  TierBudget(max_tokens=40_000, priority="high"),
        BACKGROUND: TierBudget(max_tokens=30_000, priority="medium"),
        ARCHIVE:    TierBudget(max_tokens=20_000, priority="low"),
    },
    recent_posts_budget=30_000,
    recent_posts_n=8,
    lock_in_verbatim_posts=2,
    retrieval=RetrievalConfig(
        vector_top_k=8,
        keyword_top_k=5,
        similarity_threshold=0.65,            # configured; store decides whether to honour
        embedding_task="extractor.embed",
        include_library=True,
        keyword_kinds=("fact",),
    ),
    chars_per_token=4,
    background_character_limit=6,
    default_temperature=1.0,
    default_max_tokens=4_096,
)
```

The `TierBudget.priority` field is informational; only the `max_tokens` is enforced today. `similarity_threshold` is plumbed through but is the store's concern, not the builder's.

## Templates

All prompt fragments are Jinja2 templates under `backend/src/grimoire/templates/` (relocated there in `e13de4a`):

- `context_system_block/default.j2` — style + boundaries + worlds-in-play + voice corrective
- `context_lock_in_block/default.j2` — scene header + active PC + commitments + mechanics + verbatim posts
- `context_tier_block/default.j2` — `# {label}\n{items}` wrapper for spotlight/background/archive
- `context_recent_older_block/default.j2` — older posts beyond the verbatim window
- `context_location/default.j2` — name + description + body + features

Templates render through `grimoire.templates.render(name, **kwargs)`.

## Error handling

- **Lock-in overflow:** `LockInOverflowError(used, budget)` raised before any messages are emitted. Spec-mandated.
- **Domain calls (library, characters, world, scenes, continuity):** wrapped in `_safe_call` / per-call `try/except`. Failures log at DEBUG and produce empty results — the turn proceeds with less context rather than failing.
- **Archive (gateway / store):** any exception in embed or search returns `[]`. The builder never fails a turn because retrieval failed.
- **Missing optional collaborators:** `gateway=None` or `state_store=None` simply disables archive retrieval; the builder degrades gracefully.

## Test wiring

`backend/tests/context/test_builder.py` defines stub `StubLibrary`, `StubCharacters`, `StubWorld`, `StubScenes`, `StubContinuity` classes with just the surface the builder actually calls. The `_builder(**overrides)` helper composes these defaults so each test only specifies what it cares about. Coverage includes the user-message base case, system-block composition, lock-in vs spotlight separation, mechanics injection presence/absence, lock-in overflow, archive end-to-end (gateway + store stubs), scene header rendering, verbatim recent posts, commitments rendering, location + weather rendering, source-scope attribution, `estimate()` per-tier breakdown, `messages_hash` determinism, drift corrective injection, lore keyword triggers, composition snapshot preservation, and the "background chars use compressed cards, not full" invariant.
