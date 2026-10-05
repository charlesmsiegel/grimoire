# Sampler presets — implementation plan

Spec: `docs/superpowers/specs/2026-10-05-sampler-presets-design.md`.

Each task is test-first: write the failing test, make it pass, run the
module's suite. `make check-py` / `check-web` / the lint ratchets at the end.

## Backend

1. **`llm_sampling.py`** (new, gateway leaf, imports nothing from the store)
   - `PARAMS`: ordered table `name → {label, type, min, max}`; `STOP_MAX`,
     `STOP_CHARS`.
   - `validate(params) -> dict` raising `ValueError` naming the param (unknown
     key, wrong type, out of bounds, bool masquerading as number); ints accept
     integral floats; `stop` is a list of non-empty strings.
   - `SUPPORT`: per kind, the set of params sent; `supported(kind, extended)`.
   - `split(kind, params, extended) -> (applied, dropped)`; `dropped` sorted in
     `PARAMS` order; unknown kinds behave as `openrouter` (the facade's default).
   - `report(conn) -> dict | None` — the inspector block for a connection that
     carries a `sampling` attachment.
   - Tests: `test_llm_sampling.py`.

2. **Adapters + facade**
   - `openrouter.stream(..., sampling=None)` and `_payload` merge `sampling`.
   - `openai_compatible.stream(..., sampling=None)` merges into the payload.
   - `claude_agent` unchanged (receives nothing).
   - `LLMClient._dispatch`: `applied, dropped = llm_sampling.split(...)` for the
     attempt's own `conn`; pass `sampling=applied` only when non-empty (keeps
     the existing exact-kwargs tests true); debug-log `dropped`.
   - Tests: payload assertions in `test_openrouter.py`,
     `test_openai_compatible.py`; dispatch/fallback-kind split in `test_llm.py`.

3. **`store/sampler_presets.py`** (new)
   - `_dir() = home()/"sampler_presets"`; `PRESET_CLEAR = "⁣none"`.
   - `list_presets`, `read_preset(id)` (None for unsafe/missing/malformed),
     `create_preset(name, params, notes, source)`, `update_preset`,
     `delete_preset` (sweeps `preset_*` keys in config.md and every
     connection's `sampler_preset` *before* unlinking — `llm_connections`'
     ordering argument).
   - `from_sillytavern(obj) -> (params, report)` per the spec's table.
   - Pure `resolve(task, *, campaign_meta, cfg, conn, exists) -> {preset_id,
     scope, route}` and `bundle(...)` for the routing picker.
   - Registered on the `store` facade.
   - Tests: `test_sampler_presets_store.py`, `test_sampler_presets_import.py`,
     `test_sampler_presets_resolve.py`.

4. **Config / routing / connections / campaign**
   - `routing.preset_key(route)`, `routing.PRESET_CONFIG_KEYS`; add to
     `config._CONFIG_KEYS` and defaults.
   - `llm_connections._FIELDS += ("sampler_preset", "sampler_support")`.
   - `set_campaign_routing` accepts campaign-scoped `preset_*` keys too.
   - `ConnectionCreate/Update` gain the two fields
     (`sampler_support: Literal["", "standard", "extended"]`).

5. **Routes**
   - New `routes/samplers.py`: CRUD + `/import` (multipart file *or* JSON body —
     JSON body `{name, data}` to stay off `python-multipart` edge cases; the
     frontend reads the file with `FileReader`). Registered in `routes/__init__`.
   - `common._with_sampling(conn, task, cid)` attaches `conn["sampling"]`;
     called from `_require_connection`, `_override_connection` and
     `_fallback_connection` (task-less: connection preset only). Never raises.
   - `_routing_body` adds `presets`, `preset_effective`, `preset_inherited`,
     `preset_inherited_from`, `preset_catalog` (id/name list);
     `_routing_fields` accepts `presets` and validates ids (the clear sentinel
     allowed). `RoutingUpdate.presets: dict | None`.
   - `get_scene_context` adds `sampling` (report for the standing chat
     connection, `None` when unresolvable).
   - `_record_prompt(..., sampling=...)` stores the report in the snapshot;
     call sites pass `llm_sampling.report(conn)`; the fallback-variant capture
     records the fallback connection's report via a new `on_variant` arg is
     out of reach (it only receives the model) — record `sampling` for the
     primary only and leave it absent on variant snapshots (the inspector
     renders nothing rather than a wrong block).
   - Tests: `test_sampler_presets_routes.py` (CRUD, import, routing bundle +
     PUT, context block, snapshot block, end-to-end that a chat turn's
     provider call receives the applied params through the fake).

## Frontend

6. **API** — types `SamplerParams`, `SamplerPreset`, `SamplerCatalog`,
   `SamplingReport`, `ImportReport`; `api.listSamplerPresets`, `create…`,
   `update…`, `delete…`, `importSamplerPreset`; `RoutingBundle` preset fields;
   `setGlobalRouting/setCampaignRouting` accept a `presets` map;
   `SceneContext.sampling?`; `LLMConnection.sampler_preset/sampler_support`.

7. **`SamplerPresetEditor.tsx`** — list/detail per CLAUDE.md (`.editor`,
   `.editor-list` with `+ New preset` and `Import from SillyTavern…`,
   `.detail-view`/`.detail-sidebar` with Edit, form with one input per param,
   blank = unset). Import shows the four-list report. Mounted as a Settings
   section "Sampler presets" in the "What the model sees" group.
   Tests: row → read-only view, Edit → form, `+ New` → form, import report.

8. **Routing picker** — a preset select per row (inherit / no preset / each
   preset) with the inherited label. Tests extend `ModelRoutingPicker.test`.

9. **Connection editor** — preset select + (openai_compatible) the extended
   checkbox; shown in the view sidebar. Test extends `ConnectionEditor.test`.

10. **Inspector** — `SamplerLine` in `ContextBreakdown`: preset + scope,
    applied chips, dropped list with the per-kind reason. Test in
    `ContextBreakdown`/`SceneInspector` tests.

## Finish

11. `make check-lint check-mypy check-eslint check-templates`, `check-py`,
    `check-web`, `check-pydantic1`; `make baseline` only if a count fell.
12. Review gate on the diff (subagent; Codex unavailable here), then the
    spec-conformance review, then commit, push, PR.
