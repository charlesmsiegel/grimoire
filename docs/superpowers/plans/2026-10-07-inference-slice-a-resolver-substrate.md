# Inference refactor — Slice A: resolver substrate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every generation and every embedding resolves through one new resolver (roles, routes, presets, per-role fallback) that reads today's store through a pure legacy→new translation — with zero behaviour change and nothing new written to disk.

**Architecture:** Pure modules under `store/inference/` (key names, legacy translation, the selection/preset/fallback cascade, the resolved-object types) plus one impure module (`store/inference/resolve.py`) that reads config, connections, presets and catalogs and lowers the answer to today's connection-dict shape, so `LLMClient` runs unchanged. The route registry grows from 12 to 15 routes internally while every legacy-facing surface keeps seeing the original 12 with their original task lists. A characterization snapshot taken from the baseline code before anything moves is the proof of neutrality.

**Tech Stack:** Python ≥3.11, FastAPI, pytest. No frontend changes.

**Spec:** `docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md` (§4.4–4.6, §5, §7.3's space id, §11.1, §14 slice A).

**Gate record:** the plan → implementation Codex review could not run (no Codex CLI or credentials in the container); by the user's choice a Claude adversarial review stood in. Its seven findings are folded into this revision.

## Global Constraints

- **Behaviour-neutral (spec rule 3).** For every task, with and without a campaign, the resolved connection id, model, sampling `{preset_id, preset_name, scope, params}`, `model_params`, fallback connection and its sampling, every 409/400 status and `detail` string, and the routing API bodies are identical to the baseline. Task 1's snapshot is the arbiter and is **never regenerated** after Task 1.
- **Writes nothing new.** No new keys in `config.md` or `campaign.md`, no new files. `store/config._CONFIG_KEYS` is not extended in this slice.
- **The facade's fallback is untouched in this slice.** `LLMClient` keeps resolving the global fallback per generation through `build_llm`'s `fallback=_fallback_connection`. The resolver computes the per-role fallback attempt, and a test holds it equal to `_fallback_connection()`, but nothing hands it to the facade until slice C (when roles can differ and per-generation re-resolution is designed with them).
- **Legacy surfaces stay at 12 routes with their original task tuples.** `routing.CONFIG_KEYS`, `PRESET_CONFIG_KEYS`, `routes_for()`, `bundle()`, `refused()`, `GET/PUT /api/routing`, `GET/PUT /api/campaigns/{cid}/routing`, and `sampler_presets.scope_values/inherited/refused` are byte-identical to today.
- **Decide routes are not converted yet.** The three new routes and `continuity` carry `operation="generate"` and `default_role="fast"`; slices F/G flip both together (spec §4.5, §14).
- **New route keys use underscores:** `speaker`, `scene_break`, `voice_drift`.
- **Trim exactly where today trims:** `route_*`, `preset_*` and `embeddings_*` values are `.strip()`ed (as `routing._opinion`, `sampler_presets._opinion` and `embed_space.resolve` do); `active_connection_id` and `fallback_connection_id` are used raw (a padded id names no connection today, and must still name none).
- **Imports** (CLAUDE.md, `test_import_guard.py`): module scope only, acyclic; across packages inside `store/` bind submodules (`from .. import config, llm_connections, routing, sampler_presets`), never names off them. `llm.py` is not modified in this slice.
- **pydantic:** no new models.
- **Privacy:** fixture names are invented (Mara, Saltmarch, Realm …); fake keys look like `sk-test-…`.
- **Test command** (repo root): `make check-py`; one file: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/<file> -q` (Windows: `cd backend; $env:PYTHONPATH="src"; .venv\Scripts\python.exe -m pytest tests\<file> -q`).

## Review Focus

1. **No active connection** (the state a delete of the active connection leaves): a pinned route still runs and still has the global fallback; a provider-named reroll override still works; an unpinned task is 409 "No LLM connection selected". Pinned by Task 1's `no_active` state.
2. **A routed connection that exists but has no key** is a 409 naming the connection and the route's *legacy* label — not walked past, not relabelled. Pinned by `keyless`.
3. **A store from before named connections** (flat `openrouter_key` + `model`, no `llm_connections/`) resolves its first generation to the seeded OpenRouter connection, because `ensure_migrated()` runs before config is read. Pinned by `pre_connections`.
4. **A dangling route next to configured embeddings** must not switch semantic recall off. Pinned by `embed_with_dangling`.
5. **A Claude connection with an empty model** keeps `""` in the selection (the facade substitutes `opus`). Pinned by `claude_active`.

---

## File structure

| File | Responsibility |
|---|---|
| `backend/tests/inference_baseline.py` (new) | States to build + `observe()` |
| `backend/tests/fixtures/inference_baseline.json` (new) | The baseline answer, written once in Task 1 |
| `backend/tests/test_inference_equivalence.py` (new) | `observe() == baseline` |
| `backend/src/grimoire/store/routing.py` (modify) | 15 routes; `operation`, `default_role`, `requires`, `legacy`; legacy views |
| `backend/src/grimoire/store/sampler_presets.py` (modify) | Reads preset keys through `routing.legacy_key` |
| `backend/src/grimoire/store/inference/__init__.py` (new) | Package; binds submodules |
| `backend/src/grimoire/store/inference/keys.py` (new, pure) | Role/route/fallback key names, role inheritance |
| `backend/src/grimoire/store/inference/translate.py` (new, pure) | Legacy layout → new-layout view |
| `backend/src/grimoire/store/inference/cascade.py` (new, pure) | Spec §5.1, §5.2, §5.5 |
| `backend/src/grimoire/store/inference/resolved.py` (new, pure) | `Attempt`, `ResolvedInference` |
| `backend/src/grimoire/store/inference/resolve.py` (new, impure) | Reads the store, runs the cascade, lowers to connection dicts |
| `backend/src/grimoire/routes/common.py` (modify) | `require_inference`, `override_inference`, `_soft_inference`; old helpers removed |
| `backend/src/grimoire/routes/*.py` (modify) | Call sites switched |
| `backend/src/grimoire/store/embed_space.py` (modify) | Resolve through the Embedding role |
| `backend/tests/test_routing_guard.py` (modify) | Follows the new seam; operation check; no bare references |
| `CLAUDE.md` (modify) | "Adding an LLM call site?" names the new seam |

---

### Task 1: Freeze the baseline

**Files:**
- Create: `backend/tests/inference_baseline.py`, `backend/tests/fixtures/inference_baseline.json`, `backend/tests/test_inference_equivalence.py`

**Interfaces:**
- Produces: `inference_baseline.STATES: dict[str, Callable[[TestClient], dict]]` (each builds a store on a fresh `GRIMOIRE_HOME` and returns `{"cid": str}`); `inference_baseline.observe(client, ctx) -> dict`; `python -m tests.inference_baseline --write` regenerates the JSON (used **only** in this task).

- [ ] **Step 1: Write the states**, through the real API/store (`test_routing_routes._seed`'s shape: world "Realm", character "Mara", campaign "Saltmarch Run"):
  - `fresh`: openrouter (seeded) key `sk-test-active`, model `vendor/active`; nothing else.
  - `routed`: connections `local` (`openai_compatible`, `http://localhost:1234/v1`, model `local-model`, `sampler_preset=warm`) and `spare` (openrouter, key, model `vendor/spare`); presets `warm` `{temperature: 0.9}` and `cold` `{temperature: 0.2, top_k: 40}`; openrouter's own `sampler_preset=warm`; global `route_dossier=local`, `route_summary=claude`, `route_voice=gone` (no such connection), `route_tracker=local`, `preset_scene=cold`, `preset_summary=PRESET_CLEAR`, `preset_tracker=nosuch` (dangling preset), `fallback_connection_id=spare`; campaign `route_scene=local`, `preset_scene=warm`, `route_tracker=spare`, `route_absorb=deleted` (no such connection), `preset_dossier=PRESET_CLEAR`; openrouter catalog sidecar (via `llm_connections.set_cached_models` with the current rev) listing `vendor/active` with `params: ["temperature", "top_p"]` and `vendor/bigger` with `params: ["temperature"]`.
  - `keyless`: connection `nokey` (openrouter, no key); `route_absorb=nokey`; `fallback_connection_id=nokey`.
  - `no_active`: as `routed`, then `active_connection_id: ""` written to config.
  - `claude_active`: `active_connection_id=claude` (model `""`); `embeddings_connection_id=local`, `embeddings_model=embed-small`.
  - `embed_with_dangling`: as `claude_active` plus `route_voice=gone`, and `spare`'s `.md` overwritten with invalid UTF-8 bytes while `route_dossier=spare`.
  - `whitespace`: raw frontmatter values `route_dossier: "  local  "`, `fallback_connection_id: "  spare  "`, `embeddings_connection_id: "  local  "`, `embeddings_model: embed-small`.
  - `pre_connections`: no `llm_connections/` directory; `config.md` holds only legacy `openrouter_key: sk-test-legacy` and `model: vendor/legacy`.
- [ ] **Step 2: Write `observe(client, ctx) -> dict`** returning:
  - `"tasks"`: for every task in `sorted(routing.TASK_ROUTE)` plus `""`, for `cid in ("", ctx["cid"])`: on success `{"conn": id, "model": llm.effective_model(conn), "sampling": conn["sampling"], "model_params": conn.get("model_params"), "fallback": {"id", "sampling"} | None}`, the fallback being the second entry of `client.app.state.llm._routes(conn)`; on `HTTPException` `{"status": exc.status_code, "detail": exc.detail}`. Resolve through `routes.common._require_connection(task, cid)` **in this task**.
  - `"overrides"`: `routes.common._override_connection(body, "regenerate", ctx["cid"])` for bodies `{}`, `{connection_id: "spare"}`, `{model: "vendor/bigger"}`, both, `{connection_id: "nope"}`, `{model: "x" * 10_000}`, `{connection_id: "nokey"}` → `{"conn", "model", "sampling", "model_params", "routed"}` or `{"status", "detail"}`.
  - `"display"`: `GET /api/config`'s `send_images_reach`; `routes.config._connection_sampling(id)` for each connection id; the context-breakdown endpoint's sampling for the campaign's scene (the handler at `routes/scenes.py:5130`).
  - `"embedding"`: `store.embed_space.resolve()`.
  - `"routing"`: `GET /api/routing` and `GET /api/campaigns/{cid}/routing` bodies.
  - **Normalisation, applied to the whole result before returning:** every occurrence of a connection's current `rev` string (in `space` or anywhere else) is replaced with `<rev:{id}>`; `fetched_at`-style timestamps, if any appear, with `<time>`. After normalising, assert no 16-hex-character run remains in the serialised output.
- [ ] **Step 3: Add a `__main__`** that writes `fixtures/inference_baseline.json` (sorted keys, indent 2) for all states, and run it on the unmodified tree:

  Run: `cd backend && PYTHONPATH=src .venv/bin/python -m tests.inference_baseline --write`
  Expected: the JSON exists; `keyless/tasks/absorb` is a 409 and its fallback `null`; `no_active/tasks/dossier` resolves to `local` with fallback `spare`; `no_active/tasks/chat` (no cid) is a 409; `pre_connections/tasks/chat` resolves to `openrouter`; `embed_with_dangling/embedding` is not null.

- [ ] **Step 4: Write `test_resolution_matches_the_baseline`** (parametrized over `STATES`) asserting `observe(...) == json[state]`; the module docstring says the JSON is never regenerated after this commit.
- [ ] **Step 5: Run it twice** — `pytest tests/test_inference_equivalence.py -q` → PASS both times (proves determinism).
- [ ] **Step 6: Commit** — `test(inference): freeze today's routing, sampling and fallback resolution as a baseline`

---

### Task 2: The 15-route registry, with the legacy view kept at 12

**Files:**
- Modify: `backend/src/grimoire/store/routing.py`, `backend/src/grimoire/store/sampler_presets.py:193-232`
- Test: `backend/tests/test_routing.py`, `backend/tests/test_routing_routes.py`, `backend/tests/test_sampler_presets_store.py`

**Interfaces:**
- Produces:
  - `Route(NamedTuple)`: existing five fields, then `operation: str = "generate"`, `default_role: str = "primary"`, `requires: tuple[str, ...] = ()`, `legacy: str = ""` (`""` = a legacy route itself).
  - `routing.legacy_key(route: Route) -> str` — `route.legacy or route.key`.
  - `routing.LEGACY_ROUTES: tuple[Route, ...]` — the 12 in today's order, each with its **original task tuple** (a parent's own tasks followed by its split children's tasks — which reproduces today's order exactly: `scene` ends with `response-selector`, `summary` is `("rolling-summary", "scene-break")`, `voice` is `("voice-anchor", "voice-drift")`).
  - `routing.OPERATIONS = ("generate", "decide")`, `routing.DEFAULT_ROLES = ("primary", "fast", "decision")`.
  - `CONFIG_KEYS`, `PRESET_CONFIG_KEYS`, `routes_for(scope)`, `bundle`, `refused` built from `LEGACY_ROUTES`; `route(task)`, `route_by_key`, `TASK_ROUTE` cover all 15 (`TASK_ROUTE["response-selector"] == "speaker"`).
  - Legacy `routing.resolve(task, …)` reads `config_key(legacy_key(route))` and reports `"route": legacy_key(route)`.

- [ ] **Step 1: Write failing tests** in `test_routing.py`:
  - `test_three_routes_split_out_of_their_parents`: `route("response-selector").key == "speaker"`, `route("scene-break").key == "scene_break"`, `route("voice-drift").key == "voice_drift"`; `legacy` is `"scene"`, `"summary"`, `"voice"`; `"response-selector" not in route_by_key("scene").tasks`.
  - `test_legacy_routes_keep_their_original_task_lists`: `{r.key: r.tasks for r in LEGACY_ROUTES}` equals the literal 12-entry mapping of today's `ROUTES` (copy it from the baseline source into the test).
  - `test_legacy_surfaces_still_see_twelve_routes`: `len(LEGACY_ROUTES) == 12`; `CONFIG_KEYS == tuple(f"route_{r.key}" for r in LEGACY_ROUTES)`; neither key tuple contains `speaker`, `scene_break` or `voice_drift`; `routes_for("global") == LEGACY_ROUTES`.
  - `test_every_route_declares_operation_and_default_role`: each `operation in OPERATIONS` and `default_role in DEFAULT_ROLES`; every `operation == "generate"` in this slice; `scene`, `opener`, `suggestions`, `voice` default `primary`, all others `fast`; `route_by_key("image").requires == ("vision",)`.
  - `test_a_split_route_resolves_through_its_parents_key`: `routing.resolve("scene-break", campaign_meta={}, cfg={"route_summary": "x"}, exists=lambda _: True) == {"route": "summary", "connection_id": "x", "scope": "global"}`.
  - Existing tests to change: lines using `routing.ROUTES` for config keys, bundle membership and the bundle parametrization (`test_routing.py:25,31,41,216,219`) switch to `LEGACY_ROUTES`; `test_the_continuity_route_is_spelled_as_section_29_spells_it` compares only the original five fields (`route[:5]`) against its 5-argument literal; `test_routing_routes.py:213-218` (`CAMPAIGN_ROUTES`, `GLOBAL_ONLY`, `test_every_route_has_a_driver`) iterate `LEGACY_ROUTES`.
- [ ] **Step 2: Run** `pytest tests/test_routing.py -q` → the new tests FAIL.
- [ ] **Step 3: Implement.** New routes, placed after their parent:
  - `speaker` — "Next speaker" / "Which character speaks next in group play." — `("response-selector",)`, `campaign_scoped=True`, `default_role="fast"`, `legacy="scene"`.
  - `scene_break` — "Scene-break checks" / "Whether the scene has reached a natural break." — `("scene-break",)`, `True`, `"fast"`, `legacy="summary"`.
  - `voice_drift` — "Voice drift checks" / "Whether a played scene drifted from a character's voice anchor." — `("voice-drift",)`, `True`, `"fast"`, `legacy="voice"`.
  Remove those tasks from `scene`, `summary`, `voice` in `ROUTES`; build `LEGACY_ROUTES` by merging each legacy route's children's tasks back (`route._replace(tasks=…)`). In `sampler_presets.resolve` read `routing.preset_key(routing.legacy_key(got))`; `scope_values`, `inherited`, `refused` keep iterating `routes_for(scope)`.
- [ ] **Step 4: Run** `pytest tests/test_routing.py tests/test_routing_routes.py tests/test_sampler_presets_store.py tests/test_sampler_presets_routes.py tests/test_routing_guard.py tests/test_inference_equivalence.py -q` → PASS.
- [ ] **Step 5: Commit** — `refactor(routing): fifteen routes with operation and default role; legacy surfaces keep twelve`

---

### Task 3: Key names and the legacy translation (pure)

**Files:**
- Create: `backend/src/grimoire/store/inference/__init__.py`, `keys.py`, `translate.py`
- Test: `backend/tests/test_inference_translate.py`

**Interfaces:**
- Produces (`keys.py`):
  - `ROLES = ("primary", "fast", "decision", "embedding")`, `GENERATIVE_ROLES = ("primary", "fast", "decision")`
  - `INHERITS: dict[str, str] = {"fast": "primary", "decision": "fast"}`
  - `PIN = "model"`, `PARTS = ("provider", "model", "preset")`
  - `FORMAT_KEY = "inference_format"`, `CURRENT_FORMAT = "2"`
  - `role_key(role: str, part: str) -> str` → `f"role_{role}_{part}"`
  - `fallback_key(role: str, part: str) -> str` → `f"role_{role}_fallback_{part}"`
  - `use_key(route_key: str) -> str` → `f"use_{route_key}"`
  - `pin_key(route_key: str, part: str) -> str` → `f"use_{route_key}_{part}"`
  - `preset_key(route_key: str) -> str` → `f"preset_{route_key}"`
- Produces (`translate.py`), `Lookup = Callable[[str], dict | None]` returning a raw connection or None (a lookup must never raise):
  - `global_view(cfg: dict, conn: Lookup) -> dict`
  - `campaign_view(meta: dict, conn: Lookup) -> dict`
  - `embedding_role(cfg: dict) -> tuple[str, str]` — `(provider, model)`, **no lookup**: from the view keys when the format is current, else from `embeddings_connection_id` / `embeddings_model`, both stripped.
  - `is_current(meta: dict) -> bool` — `str(meta.get(FORMAT_KEY, "")).strip() == CURRENT_FORMAT`; `global_view`/`campaign_view` return their input unchanged when it is true.

- [ ] **Step 1: Write failing tests** in `test_inference_translate.py` (dict inputs, dict-backed lookup):
  - `test_the_active_connection_becomes_primary` — cfg `{"active_connection_id": "or"}`, `or` = `{"model": "vendor/a", "sampler_preset": "warm"}` → `role_primary_provider="or"`, `role_primary_model="vendor/a"`, `role_primary_preset="warm"`; no `role_fast_provider` / `role_decision_provider` value.
  - `test_the_active_id_is_used_raw` — `active_connection_id="  or  "` → `role_primary_provider == "  or  "` (the cascade's `exists` then rejects it, as today).
  - `test_an_empty_claude_model_stays_empty`.
  - `test_the_global_fallback_backs_every_generative_role` — raw `fallback_connection_id="spare"` → `role_{primary,fast,decision}_fallback_provider == "spare"` with spare's model and preset; no `role_embedding_fallback_*`.
  - `test_embeddings_become_the_embedding_role` and `test_embedding_role_needs_no_lookup` — `embedding_role({"embeddings_connection_id": "  local ", "embeddings_model": " m "}) == ("local", "m")`, called with no lookup at all.
  - `test_a_route_connection_becomes_a_pin` — `route_dossier="  local  "` → `use_dossier="model"`, `use_dossier_provider="local"`, `use_dossier_model="local-model"`, `use_dossier_preset=<local's sampler_preset>`.
  - `test_split_routes_copy_their_parents` — `route_summary="claude"`, `preset_summary=PRESET_CLEAR` → `use_scene_break`/`use_scene_break_provider` and `preset_scene_break == PRESET_CLEAR`, and the same for `summary`.
  - `test_a_dangling_route_id_is_kept_for_the_cascade_to_walk_past` — `route_voice="gone"`, lookup → None → `use_voice="model"`, `use_voice_provider="gone"`, `use_voice_model=""`.
  - `test_a_campaign_view_carries_routes_only` — `{"route_scene": "local", "route_tagline": "x"}` → `use_scene`, `use_speaker` pins; nothing for `tagline`; no `role_*` keys.
  - `test_a_format_two_store_passes_through`.
- [ ] **Step 2: Run** → FAIL (module missing).
- [ ] **Step 3: Implement** `keys.py`, `translate.py`; `store/inference/__init__.py` binds `keys`, `translate`.
- [ ] **Step 4: Run** the file and `pytest tests/test_import_guard.py -q` → PASS.
- [ ] **Step 5: Commit** — `feat(inference): pure legacy-to-roles translation`

---

### Task 4: The selection, preset and fallback cascade (pure)

**Files:**
- Create: `backend/src/grimoire/store/inference/cascade.py`
- Test: `backend/tests/test_inference_cascade.py`

**Interfaces:**
- Consumes: `keys.*`, `routing.Route`.
- Produces:
  - `class Selection(NamedTuple): provider: str; model: str; preset: str`
  - `class Choice(NamedTuple): selection: Selection | None; role: str; via: str; scope: str; fallback: Selection | None` — `role`: the role whose slot supplied the selection (`""` for a pin); `via`: `"route"` (a pin) or `"role"`; `scope`: `"campaign"` | `"global"` | `"none"` (no selection).
  - `role_selection(role: str, *, campaign: dict, glob: dict, exists: Callable[[str], bool]) -> tuple[Selection | None, str, str]` — `(selection, supplying_role, scope)`; walks campaign → global for `role`, then for `INHERITS[role]`, recursively; `embedding` does not inherit.
  - `role_fallback(role: str, *, campaign: dict, glob: dict, exists: Callable[[str], bool]) -> Selection | None` — walks campaign → global `fallback_key(role, …)`, then for `INHERITS[role]`, recursively; **independent of whether any role supplies a selection**.
  - `choose(route: Route | None, *, campaign: dict, glob: dict, exists: Callable[[str], bool]) -> Choice`
  - `preset_for(route: Route | None, selection: Selection | None, *, campaign: dict, glob: dict, known: Callable[[str], bool]) -> tuple[str, str]` — `(preset_id, scope)`, scope in `sampler_presets.SCOPES`; `"connection"` means "the selection's own preset" (label kept for neutrality).

  A slot "is set" when `exists(provider)` is true for its provider (callers pass raw provider strings; `exists` decides). `campaign` is consulted for route and preset keys only when `route.campaign_scoped`.

- [ ] **Step 1: Write failing tests** (dict inputs; `exists` = set membership):
  - The four rows of spec §5.1: `test_a_campaign_route_choice_wins`, `test_a_campaign_role_beats_a_global_pin`, `test_a_global_pin_beats_the_global_role`, `test_the_global_role_answers_last`.
  - `test_the_campaign_role_considered_is_the_one_the_global_route_names` (global `use_summary="primary"`, campaign sets only `role_fast_*` → global Primary).
  - `test_decision_inherits_fast_then_primary`; `test_embedding_does_not_inherit`.
  - `test_a_dangling_pin_is_walked_past`; `test_an_unknown_task_runs_on_primary` (`route=None`); `test_no_primary_anywhere_is_no_selection`.
  - Fallback: `test_fallback_follows_the_role_the_route_uses`; `test_a_pin_uses_its_default_roles_fallback`; `test_a_pin_keeps_its_fallback_when_no_role_is_set` (no `role_primary_*` at all, global `role_primary_fallback_*` set, route pinned → fallback present — Review Focus 1); `test_a_dangling_fallback_is_none`.
  - Presets: `test_route_preset_campaign_then_global_then_selection`; `test_preset_clear_stops_the_walk_with_its_scope` (`("", "global")` and `("", "campaign")`); `test_an_unknown_preset_id_is_no_opinion`; `test_a_global_only_route_ignores_campaign_presets` (`tagline`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** following spec §5.1 steps 1–5 literally (step 3's R: the role the *global* `use_<route>` names, else `route.default_role`), §5.2, §5.5. A choice's fallback is `role_fallback(R_used)` where `R_used` is the role the route resolves through (for a pin, `route.default_role`; for an unknown task, `"primary"`).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(inference): the role/route/preset/fallback cascade`

---

### Task 5: The resolver and the resolved object

**Files:**
- Create: `backend/src/grimoire/store/inference/resolved.py`, `backend/src/grimoire/store/inference/resolve.py`
- Modify: `backend/src/grimoire/store/inference/__init__.py`, `backend/src/grimoire/store/locks.py` (classify if `test_lock_domain_guard` asks)
- Test: `backend/tests/test_inference_resolve.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces (`resolved.py`):
  - `@dataclass(frozen=True) class Attempt: provider_id: str; model: str; preset_id: str; conn: dict` — `conn` is that attempt's lowered connection dict.
  - `@dataclass(frozen=True) class ResolvedInference: task: str; operation: str; route: str; legacy_route: str; role: str; via: str; scope: str; attempts: tuple[Attempt, ...]` with property `conn -> dict | None` (`attempts[0].conn`, or None when nothing resolved) and property `fallback -> dict | None` (`attempts[1].conn` if present).
- Produces (`resolve.py`):
  - `problem(conn: dict) -> str | None` — moved verbatim from `routes/common._connection_problem`.
  - `model_params(conn: dict) -> list[str] | None` — moved from `routes/common._model_params` (returns None for every kind but openrouter, whose effective model is the stored one, so it reads `conn.get("model", "")`).
  - `own_sampling(conn: dict) -> dict` — `conn` with `sampling` (its own preset, scope `"connection"` or `"none"`) and `model_params`; exactly what `_attach_sampling(conn, "", "")` returns today.
  - `resolve(task: str, *, campaign_meta: dict, operation: str = "generate", override: Selection | None = None) -> ResolvedInference`
- Behaviour of `resolve`:
  - Calls `llm_connections.ensure_migrated()` **before** `config.read_config()` (Review Focus 3).
  - Connection lookup: `read_connection_raw`, cached per call; `ConnectionNotFound`, `locks.StoreBusy`, `OSError`, `UnicodeDecodeError` → None. `exists(id)` is "lookup is not None".
  - Views from `translate.global_view` / `translate.campaign_view`; choice from `cascade.choose(routing.route(task), …)`.
  - `override`: a provider with no model takes that provider's own legacy `model` and `sampler_preset` (today's `_override_connection`) and needs no standing selection; a model with no provider needs the standing selection (None → no selection, so the seam's 409 fires as today).
  - Lowering: `{**raw_connection, "model": selection.model, "sampling": {...}}` plus `model_params` when known — the dict `_attach_sampling` builds today. Primary sampling from `cascade.preset_for(route, selection, …)`; fallback sampling from `own_sampling` of the fallback connection.
  - The fallback attempt is omitted when its connection is missing or `problem()` is not None.
  - `legacy_route` is `routing.legacy_key(route)` (`""` for no route).

- [ ] **Step 1: Write failing tests** in `test_inference_resolve.py` against a temp store:
  - `test_a_fresh_store_resolves_every_task_to_the_active_connection`.
  - `test_a_route_pin_lowers_to_that_connections_dict` (`route_dossier=local` → `conn["base_url"]`, sampling preset is local's own).
  - `test_the_resolved_fallback_is_todays_fallback` — for each Task 1 state, for every task, `resolved.fallback["id"]` (or None) equals `routes.common._fallback_connection()`'s id (or None), and its `sampling` matches.
  - `test_a_keyless_fallback_is_no_fallback`.
  - `test_no_active_connection_still_resolves_a_pinned_route` (and an unpinned task → `conn is None`).
  - `test_a_legacy_flat_config_is_migrated_before_it_is_read`.
  - `test_an_unreadable_connection_file_reads_as_missing`.
  - `test_a_provider_only_override_keeps_that_providers_model`; `test_a_model_only_override_keeps_the_standing_provider`; `test_a_provider_override_needs_no_standing_selection`.
  - `test_own_sampling_matches_a_task_less_attach` — for each connection in `routed`, `own_sampling(raw) == routes.common._attach_sampling(raw, "", "")` (written before Task 7 removes the latter).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; bind the modules in `store/inference/__init__.py`.
- [ ] **Step 4: Run** the file and `pytest tests/test_import_guard.py tests/test_lock_domain_guard.py tests/test_paths_guard.py -q` → PASS.
- [ ] **Step 5: Commit** — `feat(inference): resolve a task to provider, model, preset and fallback`

---

### Task 6: Switch the seam and every call site

**Files:**
- Modify: `backend/src/grimoire/routes/common.py`; callers in `routes/scenes.py` (×21), `character_turns.py` (incl. `:654`), `tracker.py:392`, `characters.py`, `worlds.py`, `campaigns.py`, `config.py` (`:64`, `:499`), `continuity.py`, `greetings.py`, `mechanics.py`, `passage_characters.py`
- Modify: `backend/tests/test_routing_guard.py`, `backend/tests/inference_baseline.py`, `backend/tests/test_inference_resolve.py` (the `own_sampling` comparison), and the tests in Step 6
- Test: `backend/tests/test_inference_equivalence.py`

**Interfaces:**
- Consumes: `store.inference.resolve.{resolve, problem, model_params, own_sampling}`, `ResolvedInference`, `cascade.Selection`.
- Produces (`routes/common.py`):
  - `require_inference(task: str = "", cid: str = "", *, operation: str = "generate") -> ResolvedInference` — refuses exactly as `_usable_or_409` does today: `conn is None` → 409 `{"detail": "No LLM connection selected", "kind": "missing_key"}`; `problem(conn)` with `via == "route"` → 409 with today's routed wording using `store.routing.label_for(resolved.legacy_route)`; otherwise `problem` → 409 `{"detail": problem, "kind": "missing_key"}`.
  - `override_inference(body, task: str = "", cid: str = "") -> tuple[ResolvedInference, bool]` — `_override_connection`'s contract verbatim (refusals, messages, `routed` on provider id + effective model).
  - `_soft_inference(resolve: Callable[[], ResolvedInference]) -> tuple[dict | None, str]` — `_soft_connection`'s contract, returning `resolved.conn`.
  - `_fallback_connection` and `build_llm` are **unchanged** except that `_fallback_connection` calls `own_sampling` in place of `_attach_sampling(conn, "", "")`.
  - Removed: `_routed_connection`, `_standing_connection`, `_usable_or_409`, `_with_sampling`, `_attach_sampling`, `_require_connection`, `_override_connection`, `_soft_connection`, `_connection_problem`, `_model_params`.
  - `config._connection_sampling` uses `own_sampling(conn)`; the bundle's `usable` flag and `_route_sampling` use `store.inference.resolve.problem` / `model_params`.
  - Display-only resolutions (`config._send_images_reach`, the two `scenes.py` sites) call `store.inference.resolve.resolve("chat", campaign_meta=_campaign_routing_meta(cid)).conn`, with no refusals.

- [ ] **Step 1: Update `inference_baseline.observe`** to call `require_inference(task, cid).conn` and `override_inference(...)`, mapping to the same JSON shape. Do **not** touch the JSON.
- [ ] **Step 2: Update `test_routing_guard.py`:** the watched name becomes `require_inference` everywhere (including the self-tests); add `test_a_call_sites_operation_matches_its_route` (an `operation=` keyword must be a string literal equal to `routing.route(task).operation`; absent means `"generate"`, which must equal the route's) and `test_the_seam_is_only_ever_called` (any `Name`/`Attribute` reference to `require_inference` that is not the callee of a `Call` fails, unless marked `# routing-ok: <reason>`), each with a self-test that flags a planted violation.
- [ ] **Step 3: Add `test_an_unreadable_campaign_resolves_globally`** — a campaign whose `campaign.md` is invalid UTF-8 → `require_inference(task, cid)` gives the global answer, no exception.
- [ ] **Step 4: Run** `pytest tests/test_inference_equivalence.py tests/test_routing_guard.py -q` → FAIL (names missing).
- [ ] **Step 5: Implement** the seam and switch call sites. Patterns: `_require_connection("t", cid)` → `require_inference("t", cid).conn`; `run_in_threadpool(_require_connection, "t", cid)` → `run_in_threadpool(lambda: require_inference("t", cid).conn)`; `conn, routed = _override_connection(b, "t", cid)` → `resolved, routed = override_inference(b, "t", cid)` then `conn = resolved.conn`; `_soft_connection(lambda: _require_connection("t", cid))` → `_soft_inference(lambda: require_inference("t", cid))`. Every task literal and `cid` argument stays as it was.
- [ ] **Step 6: Update test references** to removed helpers: `test_continuity_read_cost.py:391`, `test_sampler_presets_routes.py:231`, the `own_sampling` comparison in `test_inference_resolve.py` (compare against the frozen baseline's `display` entries instead), and docstring/comment mentions in `conftest.py`, `test_llm_error_status.py`, `test_provider_health.py`, `test_rolling_summary_routes.py`, `test_runs_routes.py`, `test_scene_break_routes.py`, `test_routes.py`.
- [ ] **Step 7: Run** `make check-py` → PASS, including `test_inference_equivalence.py` against the untouched JSON.
- [ ] **Step 8: Commit** — `refactor(routes): every generation resolves through require_inference`

---

### Task 7: Embeddings resolve through the Embedding role

**Files:**
- Modify: `backend/src/grimoire/store/embed_space.py:28-80`
- Test: `backend/tests/test_embed_space_role.py` (new)

**Interfaces:**
- Consumes: `translate.embedding_role`.
- Produces: `embed_space.resolve(cfg: dict | None = None) -> dict | None` — signature, return shape `{model, base_url, key, space}`, `space` string `f"{conn_id}\0{rev}\0{model}"`, None-for-everything-unset, and the `openai_compatible`-only rule all unchanged (slice B widens the kind rule).

- [ ] **Step 1: Write failing tests:**
  - `test_the_embedding_role_is_what_resolves` — monkeypatch `translate.embedding_role` to return `("local", "m2")` while legacy keys say otherwise → `resolve()["model"] == "m2"`.
  - `test_a_format_two_config_resolves_from_role_keys` — `cfg={"inference_format": "2", "role_embedding_provider": "local", "role_embedding_model": "m"}` → resolves with `space` from `local`'s id and rev.
  - `test_other_connections_are_never_read` — spy on `llm_connections.read_connection_raw`: one call, for the embedding provider only.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement:** provider and model from `translate.embedding_role(cfg)`; everything after is unchanged.
- [ ] **Step 4: Run** `pytest tests/test_embed_space_role.py tests/test_embeddings.py tests/test_context_semantic.py tests/test_semsearch_store.py tests/test_continuity_similarity.py tests/test_inference_equivalence.py -q` → PASS.
- [ ] **Step 5: Commit** — `refactor(embeddings): the embedding endpoint is the Embedding role`

---

### Task 8: Docs and the slice's closing checks

**Files:**
- Modify: `CLAUDE.md` ("Adding an LLM call site?" bullet)

- [ ] **Step 1: Update CLAUDE.md's "Adding an LLM call site?" bullet:** resolve with `require_inference(<task>, cid)` (read `.conn` until slice I) and name the task the call meters under; `store/routing.py` maps the task to a route, and the route resolves to a role or a pinned model (`store/inference/`); `test_routing_guard.py` fails a call that names no task, a task no route claims, an operation that differs from its route's, a reference to the seam that is not a call, and a route whose tasks nothing uses. Keep the paragraph's reasoning about connections vs model strings.
- [ ] **Step 2: Run** `pytest tests/test_docs_guard.py -q` → PASS; `grep -rn "_require_connection" backend/src CLAUDE.md AGENTS.md CONTRIBUTING.md` → no hits.
- [ ] **Step 3: Run the full gate:** `make check` → all targets PASS. If `check-lint`/`check-mypy` report a *smaller* count, run `make baseline` and commit the shrunken baseline with this task.
- [ ] **Step 4: Commit** — `docs: an LLM call site resolves through require_inference`
- [ ] **Step 5: Implementation gates (CLAUDE.md):** the Codex review and the final adversarial review against the spec — or, while Codex is unavailable, the same Claude stand-in the plan gate used — asking whether slice A is behaviour-neutral and whether anything in §14's slice-A row was dropped.
