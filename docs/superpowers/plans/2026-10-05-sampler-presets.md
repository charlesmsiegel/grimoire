# Sampler presets — implementation plan

Spec: `docs/superpowers/specs/2026-10-05-sampler-presets-design.md` (revised
after its adversarial review). Each task is test-first. Lint ratchets and the
full gate at the end.

## Backend

1. **`llm_sampling.py`** (new gateway leaf; imports nothing from the package
   except `llm_errors` if needed)
   - `PARAMS` table, `STOP_MAX = 16`, `STOP_CHARS = 200`, `OPENAI_STOP_MAX = 4`.
   - `validate(params) -> dict` (bare dict in; `ValueError` naming the param):
     unknown key, bool, wrong type, non-finite, out of bounds; ints accept
     integral floats only; `stop` a list of 1–200-char strings, ≤ 16.
   - `split(conn) -> (applied, dropped)` per the spec's table, reading
     `kind`, `sampler_support`, `sampling.params`, `model_params`.
   - `report(conn) -> dict | None`.
   - Tests: `test_llm_sampling.py`.

2. **Catalog, errors, adapters, facade**
   - `catalog.entry` keeps `supported_parameters` as `params` when a list.
   - `LLMError(..., status=None)`; both HTTP adapters pass the status.
   - `openrouter.stream(..., sampling=None)` / `openai_compatible.stream(...,
     sampling=None)` merge it into the body.
   - `LLMClient._dispatch` splits per attempt, passes `sampling=` only when
     non-empty, debug-logs drops.
   - `LLMClient._routes`: a fallback inherits the primary's `sampling` when its
     scope is `campaign`/`global`.
   - `_resilient`: an attempt that sent params and failed with status 400/422
     raises at once with a preset-naming detail, no fallback, no observe.
   - Tests in `test_llm.py`, `test_openrouter.py`, `test_openai_compatible.py`,
     `test_model_catalog.py`.

3. **`store/sampler_presets.py`** (new) — CRUD on
   `<home>/sampler_presets/<id>.json`, `PRESET_CLEAR`, `from_sillytavern`,
   pure `resolve(task, campaign_meta, cfg, conn, exists)` and the routing
   bundle maps. `delete_preset` clears matching global `preset_*` keys only.
   On the `store` facade. Tests: store, import, resolve.

4. **Keys and fields** — `routing.preset_key`, `routing.PRESET_CONFIG_KEYS`;
   `config._CONFIG_KEYS` + defaults; `llm_connections._FIELDS` gains
   `sampler_preset`, `sampler_support`, and `update_connection` keeps `rev`
   and the sidecar when only those change; `set_campaign_routing` accepts
   campaign-scoped `preset_*` keys.

5. **Routes**
   - CRUD + import in `routes/config.py`, beside `/styles` and `/routing`
     (no new domain router, so the load-bearing include order is untouched).
   - `ConnectionCreate`/`ConnectionUpdate` gain both fields; the handlers
     check `sampler_preset` names a preset; `put_connection` forgets health
     only when the rev moved (compared values, not body keys).
   - `_routing_fields` validates `presets` separately (`routing.refused` only
     knows `route_*` keys; the clear sentinel passes the existence check).
   - Every `_record_prompt` caller (scenes ×5, greetings, mechanics,
     character_turns) passes `conn=`; `character_turns`' own variant capture
     describes the fallback, not the primary relabelled.
   - The end-to-end test drives a REAL `LLMClient` over the shared
     `llm_fakes.ScriptedProvider`, so the split itself is exercised.
   - `common._with_sampling(conn, task, cid)` attaches `sampling` and
     `model_params`; applied in `_standing_connection`, `_override_connection`
     (final conn) and `_fallback_connection` (connection preset). Never raises.
   - `_routing_body`/`_routing_fields`/`RoutingUpdate.presets`.
   - Connection detail gains `sampling`; `put_connection` skips
     `registry.forget` for a sampler-only edit.
   - `get_scene_context` adds `sampling`; `_record_prompt(..., sampling=)` and
     the variant callback records the fallback's report.
   - Tests: `test_sampler_presets_routes.py`.

## Frontend

6. API types and calls; `Model.params?`; `SceneContext.sampling?`.
7. `SamplerPresetEditor.tsx` (list/detail) + Settings section; tests.
8. `ModelRoutingPicker` preset select + report line; tests.
9. `ConnectionEditor` preset select, extended checkbox, sidebar split; tests.
10. `ContextBreakdown` sampler block; tests.

## Finish

11. Full gate (`check-lint`, `check-mypy`, `check-eslint`, `check-templates`,
    `check-py`, `check-web`, `check-pydantic1`).
12. Diff review and spec-conformance review (subagents; Codex is not installed
    in this environment), then commit, push, PR.
