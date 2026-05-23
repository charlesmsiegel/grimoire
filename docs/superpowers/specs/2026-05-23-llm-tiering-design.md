# LLM Tiering, Integrated Extraction, Configurable Summaries

**Status:** Draft
**Date:** 2026-05-23
**Author:** Grimoire dev session (Claude pairing)
**Scope:** Backend LLM gateway routing, orchestrator turn pipeline, scene-summary cadence, campaign settings UI.

## Problem

A typical post submission makes more LLM calls than the user expects. The audit:

| Call | When | Today's cost |
|---|---|---|
| Narrator main response | Every turn | Heavy model, prose generation |
| Extraction (deltas) | Every turn | Heavy model, second call to re-read the narrator response |
| Scene-break classifier | When input plausibly ends a scene | Heavy model (whatever `is_scene_break` resolves to) |
| Continuity drift judge | When new facts arrive | Heavy model |
| Running summary | Every 5 posts | Heavy model, fire-and-forget |
| Final summary | At scene close | Heavy model |
| Embeddings | Continuously, queued | Embedding model |
| World atmosphere / location generator | On creation / emergent location | Heavy model, one-shot |

Three problems:

1. **Per-turn cost is ~2 heavy LLM calls** (narrator + extraction) where one would do if the model emitted prose and deltas together.
2. **No tiering.** Cheap classification calls (drift, scene-break, "what would X say") go to the same model as the narrator. There's no way to route generation to a strong model and classification to a cheap one without editing per-task entries by hand.
3. **In-scene summaries fire on a hardcoded cadence.** No off switch, no rate control.

## Goals

1. Single Heavy LLM call per turn for narrator + delta extraction (with fallback when the model misbehaves).
2. Two-tier routing (Heavy + Light) selectable per campaign in the UI, with a built-in task→tier mapping.
3. Configurable in-scene summary cadence (including "never") and an independent toggle for the final-summary-on-close call.
4. Embedding stays its own tier (one route).
5. Existing per-task routing keeps working — tiers are an additional layer, not a replacement.

## Non-goals

- Combining multiple Light calls (e.g., drift + scene-break) into one structured Light call. Worth doing later; out of scope here.
- Provider-side structured output / tool-calling. The inline delta block is provider-neutral and streams cleanly.
- Auxiliary tasks (rewrite, continue-as, etc.) don't get an integrated-delta change — they're already single-purpose.

## Design

### §1 Tier abstraction in the gateway

Three logical tiers, each backed by a single route string (`provider.model`):

- **Heavy** — generative work (prose, summaries, integrated extraction, rewrites, brainstorms, atmosphere).
- **Light** — classification + mechanical transforms (drift judge, scene-break classifier, translate, what-would-X-say, edit-prose, location-generator, fallback extraction).
- **Embedding** — vector embeddings.

**Storage.** New top-level block in `campaign.yaml`:

```yaml
model_tiers:
  heavy: deepseek.deepseek-v4-pro
  light: deepseek.deepseek-v4-flash
  embedding: voyage.voyage-3
```

**Resolution order** in `LLMGateway.resolve_route(task, campaign_id)`:

1. Check `model_routing[task]` — wins if set (back-compat).
2. Else `model_tiers[tier_for_task(task)]`.
3. Else app-wide default (today's behavior).

**Built-in `_TASK_TIER`** (in new `llm_gateway/tiers.py`):

| Task | Tier | Rationale |
|---|---|---|
| `main` | Heavy | Narrator prose + integrated deltas |
| `scenes.running_summary` | Heavy | Generation of multi-paragraph summary |
| `scenes.final_summary` | Heavy | Generation of summary + key beats |
| `auxiliary.rewrite_post` | Heavy | Full prose rewrite |
| `auxiliary.continue_as` | Heavy | Generates continuation prose |
| `auxiliary.brainstorm` | Heavy | Exploratory generation |
| `world.atmosphere` | Heavy | Prose generation (one-shot per world) |
| `drift_check` | Light | Yes/no classification of contradiction |
| `scene_break_classifier` | Light | Yes/no classification with confidence |
| `auxiliary.translate` | Light | Mechanical transform |
| `auxiliary.what_would_x_say` | Light | Short hint, not full prose |
| `auxiliary.edit_prose` | Light | Small mechanical polish |
| `world.location_generator` | Light | Generates schema-fitting frontmatter blob |
| `extractor.*` | Light | Fallback path only (see §2); kept cheap |
| `library.embed` | Embedding | Specialized provider |

### §2 Integrated narrator + extraction

**Prompt change.** When the new `integrated_deltas` campaign flag is on, `ContextBuilder` appends a `# Output format` stanza to the system message instructing the model to end its response with a fenced block:

```
<!--deltas-->
{ ...json delta envelope... }
<!--/deltas-->
```

**JSON schema** (small, additive; matches a subset of the existing extractor output):

```json
{
  "facts": [{"text": "...", "subject_ref": "...", "tags": ["..."]}],
  "relationship_events": [
    {"a": "...", "b": "...", "summary": "...", "delta": {...}}
  ],
  "time_advance": {"minutes": 0},
  "emergent_locations": [{"name": "...", "kind": "...", "description": "..."}],
  "transient_updates": [...]
}
```

Each top-level key is optional. The starter implementation handles `facts`, `relationship_events`, and `time_advance`. Other keys present in the model's JSON are *ignored* in PR 2 (not run through the standalone extractor as a per-key fallback — partial-key fallback would be hard to reason about). The only fallback path is wholesale: when `inline_deltas is None` (missing block or parse failure), the standalone extractor runs and produces the full delta set as today.

**Parser.** New `extractor/inline_parser.py`:

```python
def extract_delta_block(text: str) -> tuple[str, dict | None]:
    """Return (prose_with_block_stripped, parsed_deltas_or_None)."""
```

Regex `(?s)<!--deltas-->\s*(\{.*?\})\s*<!--/deltas-->`; JSON-parse the inner block. On miss or parse error, return `(text, None)`.

**Adapter.** A second helper converts the parsed JSON to an `ExtractionResult` so the orchestrator's existing `_apply_routing` pipeline doesn't change.

**Orchestrator flow** (`_continue_turn_after_pre_roll` in `orchestrator/service.py`):

1. Stream main response into `raw_text`.
2. `prose, inline_deltas = extract_delta_block(raw_text)`.
3. Save `prose` as the narrator post body (the user never sees the delta block).
4. If `inline_deltas is not None`: feed into `_apply_routing` directly. **Skip** the standalone extraction call.
5. **Fallback:** if `inline_deltas is None`, run today's extraction call (`_do_extract`) as a second LLM hit (Light tier per `_TASK_TIER`). Log a `WARNING` with the turn id, `integrated_deltas_fallback` event so the user can see fallback rate via observability.

**Net effect.** Typical turn drops from ~2 Heavy calls (narrator + Heavy extraction) to 1 Heavy call. Fallback turns are 1 Heavy + 1 Light, still cheaper than today.

### §3 Configurable summary cadence

**Storage.** `campaigns.config["summaries"]`:

```json
{ "summaries": { "running_every_n_posts": 5, "final_on_close": true } }
```

- `running_every_n_posts: int >= 0` — `0` disables in-scene summaries entirely. Default `5` (matches current).
- `final_on_close: bool` — toggle the final summary at scene close. Default `true`.

**SceneManager wiring.**

- `SceneManager` reads the per-campaign summary config when it appends a post. The existing cadence check at `scenes/manager.py:551-560` is updated to use the per-campaign value rather than `self.config.running_summary_every_n_posts` (which becomes the default-for-new-campaigns fallback). A `running_every_n_posts == 0` value skips the `RUNNING_SUMMARY_DUE` emit entirely.
- `close_scene` (`scenes/manager.py:417`) reads `final_on_close`. When `false`, it sets `scene.final_summary = scene.running_summary or ""`, marks `key_beats = []`, and skips the `_final_summary` LLM call.

**API.** New endpoint pair following the storage/narrator/generation pattern:

```
GET  /api/campaigns/{id}/summaries
PUT  /api/campaigns/{id}/summaries
```

Payload: `{ running_every_n_posts: int, final_on_close: bool }`.

### §4 Routing UI rework

**Routing tab** (`frontend/src/routes/CampaignSettings.tsx`, `RoutingTab`):

Default-collapsed view shows three model pickers stacked vertically:

- Heavy model (provider + model dropdown — reuses existing model-picker component)
- Light model
- Embedding model

Below them: `<details>` expander labeled "Advanced: per-task overrides" with the current per-task list, each row showing its **resolved** model (italicized, smaller text) plus a picker to override. An override clears via an explicit "Use tier default" reset.

**New `Summaries` tab** between `Narrator` and `Generation`:

- Number input: "Running summary every N posts (0 = never)" — default 5, min 0
- Checkbox: "Generate final summary when scene closes" — default checked

Help text under each field explaining cost trade-off.

**Integrated-deltas toggle** lives on the existing General tab (since it's a feature flag, not a model choice):

- Checkbox: "Combine narrator + delta extraction into one LLM call" — default ON for new campaigns, OFF for migrated campaigns.

### §5 App-wide defaults

Two new fields in `AppSettings` (the app-level settings page, distinct from per-campaign settings):

- Default Heavy model — shipped default: **`deepseek.deepseek-v4-pro`**
- Default Light model — shipped default: **`deepseek.deepseek-v4-flash`**

When a new campaign is created, its `model_tiers` block is initialized from these app defaults. Embedding default uses whichever embedding provider the app already has configured (existing flow).

### §6 Migration

Existing campaigns:

- `model_tiers` absent → tier resolution falls through to per-task routes (resolution step 1 wins for tasks they set, else step 3 falls to app default). **Behavior unchanged.**
- `integrated_deltas` flag absent → treated as `false`. **Behavior unchanged.**
- `summaries` absent → uses today's defaults (5 / true). **Behavior unchanged.**

New campaigns:

- `model_tiers` populated from app defaults at creation.
- `integrated_deltas` defaults to `true`.
- `summaries` defaults to `{running_every_n_posts: 5, final_on_close: true}`.

Opt-in path for existing campaigns: visit Settings → Routing → set Heavy + Light. Visit Settings → General → toggle integrated deltas. Visit Settings → Summaries → adjust cadence.

### §7 Observability

Three new event types on the existing observability bus:

- `tier_resolved` — emitted on every gateway call, payload `{task, tier, route, source: "per_task"|"tier"|"default"}`. Lets the existing observability view show which tier each task hit.
- `integrated_deltas_fallback` — emitted when the inline parser misses, payload `{turn_id, reason: "no_block"|"json_parse"|"schema"}`. Drives a fallback-rate metric.
- `summary_skipped` — emitted when `running_every_n_posts == 0` would have fired, or `final_on_close == false` skipped the final summary, payload `{scene_id, reason}`.

### §8 Files touched

| File | Change |
|---|---|
| `backend/src/grimoire/llm_gateway/tiers.py` *(new)* | `_TASK_TIER` constant + `tier_for_task()` helper |
| `backend/src/grimoire/llm_gateway/gateway.py` | tier-aware route resolution; read `model_tiers` YAML block |
| `backend/src/grimoire/extractor/inline_parser.py` *(new)* | `extract_delta_block`, JSON-to-ExtractionResult adapter |
| `backend/src/grimoire/orchestrator/service.py` | wire inline parse + fallback in `_continue_turn_after_pre_roll`; observability emits |
| `backend/src/grimoire/context/builder.py` | append `# Output format` system stanza when `integrated_deltas` enabled |
| `backend/src/grimoire/scenes/manager.py` | honor `running_every_n_posts == 0`; honor `final_on_close` in `close_scene` |
| `backend/src/grimoire/api/campaigns.py` | new endpoints `/tiers`, `/summaries`; expose `integrated_deltas` via existing campaign PATCH |
| `backend/src/grimoire/api/config.py` | default Heavy / Light model fields on the app-config surface |
| `frontend/src/routes/CampaignSettings.tsx` | new Summaries tab; RoutingTab simplified to 3 pickers + Advanced expander; integrated-deltas checkbox on General |
| `frontend/src/routes/AppSettings.tsx` | default Heavy / Light model pickers |
| `frontend/src/api/campaign.ts` | client methods for new endpoints |

### §9 Testing

- Unit tests for `extract_delta_block`: well-formed block, missing block, malformed JSON, schema mismatch, block in middle of text (refuse — only end matches).
- Unit tests for `tier_for_task`: every entry in `_TASK_TIER` returns the expected tier; unknown task returns `None` (falls back to default).
- Integration test in orchestrator: turn with integrated_deltas ON, well-formed response → 1 LLM call, deltas applied. Turn with malformed response → fallback to standalone extractor, 2 LLM calls, deltas applied.
- Integration test for `running_every_n_posts == 0` → no `RUNNING_SUMMARY_DUE` event.
- Integration test for `final_on_close == false` → scene closes with no final summary call.
- Backend route tests for the new GET/PUT endpoints (status 200 on roundtrip, validation rejects negative N, etc.).

### §10 Rollout plan (single PR or sequenced)

Suggest two PRs:

**PR 1 — tiering + summary cadence** (no behavior change for existing campaigns):
- Tier resolution in gateway.
- `_TASK_TIER` table.
- Summary cadence config + endpoints + scene-manager wiring.
- Routing/Summaries UI.
- App-level defaults for Heavy/Light.

**PR 2 — integrated extraction** (opt-in; flag default off for migration safety, switched to on for new campaigns):
- Prompt stanza in context builder.
- Inline parser + adapter.
- Orchestrator wiring + fallback.
- Observability `integrated_deltas_fallback` event.
- General-tab toggle.

This way PR 1 is low-risk, broadly useful infrastructure; PR 2 is the riskier prompt-format change that can ship behind a flag and be promoted to default once we trust the fallback rate is low.

## Open questions (none — design approved 2026-05-23)
