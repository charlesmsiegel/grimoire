# Inference refactor — Slice A: resolver substrate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every generation and every embedding resolves through one new resolver (roles, routes, presets, per-role fallback) that reads today's store through a pure legacy→new translation — with zero behaviour change and nothing new written to disk.

**Architecture:** Pure modules under `store/inference/` (key names, legacy translation, the selection/preset cascade, the resolved-object types) plus one impure module (`store/inference/resolve.py`) that reads config, connections, presets and catalogs and lowers the answer to today's connection-dict shape, so `LLMClient` runs unchanged. The route registry grows from 12 to 15 routes internally while every legacy-facing surface (config keys, routing API, picker, sampler-preset scopes) keeps seeing the original 12. A characterization snapshot taken from the baseline code before anything moves is the proof of neutrality.

**Tech Stack:** Python ≥3.11, FastAPI, pytest. No frontend changes.

**Spec:** `docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md` (§4.4–4.6, §5, §7.3's space id, §11.1, §14 slice A).

## Global Constraints

- **Behaviour-neutral (spec rule 3).** For every task, with and without a campaign, the resolved connection id, model, sampling `{preset_id, preset_name, scope, params}`, `model_params`, fallback connection and its sampling, and every 409/400 status and `detail` string are byte-identical to the baseline. Task 1's snapshot is the arbiter; it is never regenerated after Task 1.
- **Writes nothing new.** No new keys in `config.md` or `campaign.md`, no new files. `store/config._CONFIG_KEYS` is not extended in this slice.
- **Legacy surfaces stay at 12 routes.** `routing.CONFIG_KEYS`, `routing.PRESET_CONFIG_KEYS`, `routing.routes_for()`, `routing.bundle()`, `GET/PUT /api/routing`, `GET/PUT /api/campaigns/{cid}/routing`, and `sampler_presets.scope_values/inherited/refused` see exactly today's 12 routes and keys.
- **Decide routes are not converted yet.** In this slice the three new routes and `continuity` carry `operation="generate"` and `default_role="fast"`; slices F/G flip both together (spec §14 safety rule; the spec's §4.5 table shows the final values).
- **New route keys use underscores:** `speaker`, `scene_break`, `voice_drift` (frontmatter-key hygiene; task names keep their hyphens).
- **Imports** (CLAUDE.md, `test_import_guard.py`): module scope only, acyclic; across packages inside `store/` bind submodules (`from .. import config, llm_connections, routing, sampler_presets`), never names off them. `llm.py` must not import the store (#239).
- **pydantic** usage stays v1/v2-agnostic; this slice adds no models.
- **Privacy:** fixture names are invented (Mara, Saltmarch, Realm …); fake keys look like `sk-test-…`.
- **Test command** (from the repo root): `make check-py` for the whole suite; for one file `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/<file> -q` (Windows: `cd backend; $env:PYTHONPATH="src"; .venv\Scripts\python.exe -m pytest tests\<file> -q`). In a worktree pass `PY=` per CLAUDE.md.

## Review Focus

1. **A routed connection that exists but has no key** must still be a 409 naming the connection and the route's *legacy* label ("routed for summaries & scene-break checks") — not walked past to the active connection, and not labelled with a new route's name. Pinned by Task 1's `keyless` state.
2. **A Claude connection with an empty model** must resolve to the stored `""` (the facade substitutes `opus`), never have `opus` written into the selection — otherwise prompt logs and usage rows change. Pinned by Task 1's `claude_active` state.
3. **An unreadable `campaign.md`** must resolve as "no campaign opinion" (global answer), never raise. Test added in Task 7.
4. **A configured fallback that is keyless, dangling, or the same connection as the primary** must yield no fallback (the primary's own error stands). Pinned by Task 1's `keyless` and `routed` states; same-id drop stays in `llm._same_route`.
5. **Whitespace around stored ids** (`route_dossier: "  local  "`) is trimmed exactly as `routing._opinion` trims today. Test added in Task 3.

---

## File structure

| File | Responsibility |
|---|---|
| `backend/tests/inference_baseline.py` (new) | States to build + `observe()` — what "resolution" means, in one place |
| `backend/tests/fixtures/inference_baseline.json` (new) | The baseline answer, written once in Task 1 |
| `backend/tests/test_inference_equivalence.py` (new) | `observe() == baseline` |
| `backend/src/grimoire/store/routing.py` (modify) | 15 routes; `operation`, `default_role`, `requires`, `legacy`; legacy views |
| `backend/src/grimoire/store/sampler_presets.py` (modify) | Reads preset keys through `routing.legacy_key` |
| `backend/src/grimoire/store/inference/__init__.py` (new) | Package; binds submodules |
| `backend/src/grimoire/store/inference/keys.py` (new, pure) | Role/route/fallback key names, role inheritance |
| `backend/src/grimoire/store/inference/translate.py` (new, pure) | Legacy layout → new-layout view |
| `backend/src/grimoire/store/inference/cascade.py` (new, pure) | Spec §5.1, §5.2, §5.5 |
| `backend/src/grimoire/store/inference/resolved.py` (new, pure) | `Attempt`, `ResolvedInference`, `FALLBACK` |
| `backend/src/grimoire/store/inference/resolve.py` (new, impure) | Reads the store, runs the cascade, lowers to connection dicts |
| `backend/src/grimoire/llm.py` (modify) | Honour a per-call fallback carried on the connection |
| `backend/src/grimoire/routes/common.py` (modify) | `require_inference`, `override_inference`, `_soft_inference`; old helpers removed |
| `backend/src/grimoire/routes/*.py` (modify) | ~35 call sites switched |
| `backend/src/grimoire/store/embed_space.py` (modify) | Resolve through the Embedding role |
| `backend/tests/test_routing_guard.py` (modify) | Follows the new seam; operation check |
| `CLAUDE.md` (modify) | "Adding an LLM call site?" names the new seam |

---

### Task 1: Freeze the baseline

**Files:**
- Create: `backend/tests/inference_baseline.py`, `backend/tests/fixtures/inference_baseline.json`, `backend/tests/test_inference_equivalence.py`

**Interfaces:**
- Produces: `inference_baseline.STATES: dict[str, Callable[[TestClient], dict]]` (each builds a store and returns `{"cid": str}`); `inference_baseline.observe(client, ctx) -> dict`; `python -m tests.inference_baseline --write` regenerates the JSON (used **only** in this task).

- [ ] **Step 1: Write the states** in `inference_baseline.py`, each through the real API/store on a fresh `GRIMOIRE_HOME` (reuse `test_routing_routes._seed`'s shape: world "Realm", character "Mara", campaign "Saltmarch Run"):
  - `fresh`: openrouter (seeded) key `sk-test-active`, model `vendor/active`; nothing else.
  - `routed`: connections `local` (`openai_compatible`, `http://localhost:1234/v1`, model `local-model`, `sampler_preset=warm`), `spare` (openrouter, key, model `vendor/spare`); presets `warm` `{temperature: 0.9}`, `cold` `{temperature: 0.2, top_k: 40}`; openrouter's own `sampler_preset=warm`; global `route_dossier=local`, `route_summary=claude`, `route_voice=gone` (no such connection), `preset_scene=cold`, `preset_summary=PRESET_CLEAR`, `fallback_connection_id=spare`; campaign `route_scene=local`, `preset_scene=warm`, `route_tracker=spare`; openrouter catalog sidecar for `vendor/active` with `params: ["temperature","top_p"]` (via `llm_connections.set_cached_models` with the current rev).
  - `keyless`: connection `nokey` (openrouter, no key); `route_absorb=nokey`; `fallback_connection_id=nokey`.
  - `claude_active`: `active_connection_id=claude` (model `""`); `embeddings_connection_id=local`, `embeddings_model=embed-small` (with `local` as in `routed`).
  - `whitespace`: `route_dossier="  local  "` written raw into `config.md` frontmatter.
- [ ] **Step 2: Write `observe(client, ctx) -> dict`** returning:
  - `"tasks"`: for every task in `sorted(routing.TASK_ROUTE)` plus `""`, for `cid in ("", ctx["cid"])`: on success `{"conn": id, "model": llm.effective_model(conn), "sampling": conn["sampling"], "model_params": conn.get("model_params"), "fallback": {"id", "sampling"} | None}` where the fallback is the second entry of `client.app.state.llm._routes(conn)` (after `fallback_sampling`); on `HTTPException` `{"status": exc.status_code, "detail": exc.detail}`. Resolve through `routes.common._require_connection(task, cid)` **in this task**.
  - `"overrides"`: `routes.common._override_connection(body, "regenerate", ctx["cid"])` for bodies `{}`, `{connection_id: "spare"}`, `{model: "vendor/bigger"}`, both, `{connection_id: "nope"}`, `{model: "x" * 10_000}`, `{connection_id: "nokey"}` (where it exists) → `{"conn", "model", "sampling", "routed"}` or `{"status", "detail"}`.
  - `"embedding"`: `store.embed_space.resolve()`.
  - `"routing"`: `GET /api/routing` and `GET /api/campaigns/{cid}/routing` JSON bodies.
- [ ] **Step 3: Add a `__main__` that writes** `fixtures/inference_baseline.json` (sorted keys, indent 2) for all states, and run it on the unmodified tree:

  Run: `cd backend && PYTHONPATH=src .venv/bin/python -m tests.inference_baseline --write`
  Expected: the JSON exists; it contains a `status: 409` entry for `absorb` in `keyless` and `fallback: null` there; `routed`'s `dossier` resolves to `local`.

- [ ] **Step 4: Write `test_resolution_matches_the_baseline`** (parametrized over `STATES`) asserting `observe(...) == json[state]`, with a module docstring saying the JSON is never regenerated after this commit.
- [ ] **Step 5: Run it** — `pytest tests/test_inference_equivalence.py -q` → PASS.
- [ ] **Step 6: Commit** — `test(inference): freeze today's routing, sampling and fallback resolution as a baseline`

---

### Task 2: The 15-route registry, with the legacy view kept at 12

**Files:**
- Modify: `backend/src/grimoire/store/routing.py`, `backend/src/grimoire/store/sampler_presets.py:193-232`
- Test: `backend/tests/test_routing.py`, `backend/tests/test_sampler_presets_store.py`

**Interfaces:**
- Produces:
  - `Route(NamedTuple)`: existing five fields, then `operation: str = "generate"`, `default_role: str = "primary"`, `requires: tuple[str, ...] = ()`, `legacy: str = ""` (`""` = this route is itself a legacy route).
  - `routing.legacy_key(route: Route) -> str` — `route.legacy or route.key`.
  - `routing.LEGACY_ROUTES: tuple[Route, ...]` — the 12, in today's order.
  - `routing.OPERATIONS = ("generate", "decide")`, `routing.DEFAULT_ROLES = ("primary", "fast", "decision")`.
  - `CONFIG_KEYS`, `PRESET_CONFIG_KEYS`, `routes_for(scope)`, `bundle`, `refused` built from `LEGACY_ROUTES`; `route(task)`, `route_by_key`, `TASK_ROUTE` cover all 15.
  - Legacy `routing.resolve(task, …)` reads `config_key(legacy_key(route))` and reports `"route": legacy_key(route)`.

- [ ] **Step 1: Write failing tests** in `test_routing.py`:
  - `test_three_routes_split_out_of_their_parents`: `routing.route("response-selector").key == "speaker"`, `route("scene-break").key == "scene_break"`, `route("voice-drift").key == "voice_drift"`; their `legacy` are `"scene"`, `"summary"`, `"voice"`; `"response-selector" not in route_by_key("scene").tasks`.
  - `test_legacy_surfaces_still_see_twelve_routes`: `len(routing.LEGACY_ROUTES) == 12`; `routing.CONFIG_KEYS == tuple(f"route_{r.key}" for r in routing.LEGACY_ROUTES)`; no `route_speaker` / `preset_scene_break` in either key tuple; `routes_for("global") == routing.LEGACY_ROUTES`.
  - `test_every_route_declares_operation_and_default_role`: each `operation in OPERATIONS`, `default_role in DEFAULT_ROLES`; in this slice every `operation == "generate"`; `scene/opener/suggestions/voice` default `primary`, all others `fast`; `route_by_key("image").requires == ("vision",)`.
  - `test_a_split_route_resolves_through_its_parents_key`: `routing.resolve("scene-break", campaign_meta={}, cfg={"route_summary": "x"}, exists=lambda _: True) == {"route": "summary", "connection_id": "x", "scope": "global"}`.
  - Update the existing assertions that compare against `routing.ROUTES` for config keys to use `LEGACY_ROUTES`.
- [ ] **Step 2: Run** `pytest tests/test_routing.py -q` → the new tests FAIL.
- [ ] **Step 3: Implement** the registry. New routes, placed after their parent; labels and hints:
  - `speaker` — "Next speaker" / "Which character speaks next in group play." — tasks `("response-selector",)`, `campaign_scoped=True`, `default_role="fast"`, `legacy="scene"`.
  - `scene_break` — "Scene-break checks" / "Whether the scene has reached a natural break." — `("scene-break",)`, `True`, `"fast"`, `legacy="summary"`.
  - `voice_drift` — "Voice drift checks" / "Whether a played scene drifted from a character's voice anchor." — `("voice-drift",)`, `True`, `"fast"`, `legacy="voice"`.
  Remove those tasks from `scene`, `summary`, `voice`. Leave the parents' labels and hints unchanged (the legacy picker still shows them, and they still govern the split tasks there). In `sampler_presets.resolve` read `routing.preset_key(routing.legacy_key(got))`; `scope_values`, `inherited`, `refused` iterate `routing.routes_for(scope)` and are otherwise unchanged.
- [ ] **Step 4: Run** `pytest tests/test_routing.py tests/test_sampler_presets_store.py tests/test_routing_routes.py tests/test_inference_equivalence.py tests/test_routing_guard.py -q` → PASS (the snapshot proves the split tasks still resolve as before).
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
  - `preset_key(route_key: str) -> str` → `f"preset_{route_key}"` (same spelling as `routing.preset_key`)
- Produces (`translate.py`), where `Lookup = Callable[[str], dict | None]` returns a raw connection or None:
  - `global_view(cfg: dict, conn: Lookup) -> dict`
  - `campaign_view(meta: dict, conn: Lookup) -> dict`
  - Both return `cfg`/`meta` unchanged when `str(value of FORMAT_KEY).strip() == CURRENT_FORMAT`; otherwise a new flat dict holding only new-layout keys.

- [ ] **Step 1: Write failing tests** in `test_inference_translate.py` (pure — dict inputs and a dict-backed lookup):
  - `test_the_active_connection_becomes_primary`: cfg `{"active_connection_id": "or"}`, conn `or` = `{"model": "vendor/a", "sampler_preset": "warm"}` → view has `role_primary_provider="or"`, `role_primary_model="vendor/a"`, `role_primary_preset="warm"`; `role_fast_provider` and `role_decision_provider` absent or `""`.
  - `test_an_empty_claude_model_stays_empty`: active `claude` with `model: ""` → `role_primary_model == ""`.
  - `test_the_global_fallback_backs_every_generative_role`: `fallback_connection_id="spare"` → `role_{primary,fast,decision}_fallback_provider == "spare"` with spare's model and preset; no `role_embedding_fallback_*` key.
  - `test_embeddings_become_the_embedding_role`: `embeddings_connection_id="local"`, `embeddings_model="embed-small"` → `role_embedding_provider="local"`, `role_embedding_model="embed-small"`, no `role_embedding_preset`.
  - `test_a_route_connection_becomes_a_pin`: `route_dossier="local"` → `use_dossier="model"`, `use_dossier_provider="local"`, `use_dossier_model="local-model"`, `use_dossier_preset=<local's sampler_preset>`.
  - `test_split_routes_copy_their_parents`: `route_summary="claude"`, `preset_summary=PRESET_CLEAR` → `use_scene_break == "model"`, `use_scene_break_provider == "claude"`, `preset_scene_break == PRESET_CLEAR`, and the same for `summary` itself.
  - `test_a_dangling_route_id_is_kept_for_the_cascade_to_walk_past`: `route_voice="gone"` with lookup → None → `use_voice="model"`, `use_voice_provider="gone"`, `use_voice_model=""`.
  - `test_whitespace_is_trimmed`: `route_dossier="  local  "` → `use_dossier_provider == "local"` (Review Focus 5).
  - `test_a_campaign_view_carries_routes_only`: campaign meta `{"route_scene": "local", "route_tagline": "x"}` → `use_scene`/`use_speaker` pins present; nothing for `tagline` (not campaign-scoped); no `role_*` keys.
  - `test_a_format_two_store_passes_through`: `global_view({"inference_format": "2", "role_primary_provider": "p"}, …)` returns the input dict unchanged.
- [ ] **Step 2: Run** `pytest tests/test_inference_translate.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** `keys.py` and `translate.py`. `translate` imports `..routing` (for `ROUTES`, `legacy_key`, `config_key`, `preset_key`) and `.keys`; it reads legacy keys with `str(v or "").strip()`. `store/inference/__init__.py` binds `keys` and `translate` (later tasks add their modules).
- [ ] **Step 4: Run** the test file → PASS; run `pytest tests/test_import_guard.py -q` → PASS.
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
  - `class Choice(NamedTuple): selection: Selection | None; role: str; via: str; scope: str; fallback: Selection | None` — `role` is the role whose slot supplied the selection (`""` for a pin); `via` is `"route"` (a pin) or `"role"`; `scope` is `"campaign"` or `"global"` (where the supplying key lives) or `"none"` when `selection is None`.
  - `role_selection(role: str, *, campaign: dict, glob: dict, exists: Callable[[str], bool]) -> tuple[Selection | None, str, str]` — `(selection, supplying_role, scope)`; walks `campaign role → global role` for `role`, then for `INHERITS[role]`, recursively; `embedding` does not inherit.
  - `choose(route: Route | None, *, campaign: dict, glob: dict, exists: Callable[[str], bool]) -> Choice`
  - `preset_for(route: Route | None, selection: Selection | None, *, campaign: dict, glob: dict, known: Callable[[str], bool]) -> tuple[str, str]` — `(preset_id, scope)`, scope in `sampler_presets.SCOPES`; `"connection"` means "the selection's own preset" (label kept for neutrality).

  A selection "is set" when `exists(provider)` is true for its trimmed provider. `campaign` is consulted for route keys only when `route.campaign_scoped`.

- [ ] **Step 1: Write failing tests** (dict inputs; `exists` = membership in a set):
  - The four rows of spec §5.1, one test each:
    - `test_a_campaign_route_choice_wins` (campaign `use_summary=model` pin → that pin, `via="route"`, `scope="campaign"`).
    - `test_a_campaign_role_beats_a_global_pin` (global `use_summary` pin; campaign `role_fast_*` set; route default `fast` → campaign Fast, `via="role"`, `role="fast"`, `scope="campaign"`).
    - `test_a_global_pin_beats_the_global_role` (global pin, no campaign opinion → pin).
    - `test_the_global_role_answers_last` (nothing but global `role_primary_*` → Primary via inheritance for a Fast route, `role="primary"`).
  - `test_the_campaign_role_considered_is_the_one_the_global_route_names`: global `use_summary="primary"`, campaign sets `role_fast_*` only → global Primary (campaign Fast is not R).
  - `test_decision_inherits_fast_then_primary`; `test_embedding_does_not_inherit`.
  - `test_a_dangling_pin_is_walked_past` (pin provider not in `exists` → falls to role).
  - `test_an_unknown_task_runs_on_primary` (`route=None`).
  - `test_no_primary_anywhere_is_no_selection` (`selection is None`, `scope == "none"`).
  - `test_fallback_comes_from_the_supplying_role` (Fast inherits Primary → Primary's fallback) and `test_a_pin_uses_its_default_roles_fallback`; `test_a_dangling_fallback_is_none`.
  - Presets: `test_route_preset_campaign_then_global_then_selection`; `test_preset_clear_stops_the_walk_with_its_scope` (returns `("", "global")`); `test_an_unknown_preset_id_is_no_opinion`; `test_a_global_only_route_ignores_campaign_presets` (`tagline`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `cascade.py` following spec §5.1 steps 1–5 literally (step 3's R is the role the *global* `use_<route>` names, else `route.default_role`), §5.2, §5.5. The fallback for a `via="route"` choice is `role_selection(route.default_role, …)`'s supplying role's fallback; for a role choice it is the supplying role's: campaign `role_<r>_fallback_*` → global, set only if `exists`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(inference): the role/route/preset cascade`

---

### Task 5: The resolver and the resolved object

**Files:**
- Create: `backend/src/grimoire/store/inference/resolved.py`, `backend/src/grimoire/store/inference/resolve.py`
- Modify: `backend/src/grimoire/store/inference/__init__.py`
- Test: `backend/tests/test_inference_resolve.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces (`resolved.py`):
  - `FALLBACK = "_fallback"` — the key on a lowered connection dict that carries its fallback (a dict or `None`).
  - `@dataclass(frozen=True) class Attempt: provider_id: str; model: str; preset_id: str; conn: dict` — `conn` is the lowered connection dict for this attempt (no `FALLBACK` key).
  - `@dataclass(frozen=True) class ResolvedInference: task: str; operation: str; route: str; legacy_route: str; role: str; via: str; scope: str; attempts: tuple[Attempt, ...]; conn: dict | None` — `conn` is `attempts[0].conn` plus `FALLBACK: attempts[1].conn if len(attempts) > 1 else None`; `None` when nothing resolved.
- Produces (`resolve.py`):
  - `problem(conn: dict) -> str | None` — moved verbatim from `routes/common._connection_problem`.
  - `model_params(conn: dict) -> list[str] | None` — moved from `routes/common._model_params`; uses `conn.get("model", "")` (the function returns None for every kind but openrouter, where the effective model is the stored one).
  - `resolve(task: str, *, campaign_meta: dict, operation: str = "generate", override: Selection | None = None) -> ResolvedInference`
- Behaviour of `resolve`:
  - Reads `config.read_config()`; connections through `llm_connections.read_connection_raw`, cached per call, any of `ConnectionNotFound`, `locks.StoreBusy`, `OSError`, `UnicodeDecodeError` → None.
  - Views from `translate.global_view` / `translate.campaign_view`; choice from `cascade.choose(routing.route(task), …)`.
  - `override` replaces non-empty parts of the selection. While the store is legacy, an override naming a provider but no model takes **that provider's legacy `model`** and its `sampler_preset` (today's `_override_connection` semantics).
  - Lowering an attempt: `{**raw_connection, "model": selection.model, "sampling": {...}}` plus `model_params` when known — the same dict `_attach_sampling` builds today. Sampling for the primary from `cascade.preset_for(route, selection, …)`; for the fallback from `cascade.preset_for(None, fallback_selection, …)` (its own preset, scope `"connection"` or `"none"`), matching today's task-less `_fallback_connection`.
  - The fallback attempt is omitted when its connection is missing or `problem()` is not None.
  - `legacy_route` is `routing.legacy_key(route)` (or `""` for no route) — messages keep using it.

- [ ] **Step 1: Write failing tests** in `test_inference_resolve.py` against a real temp store (`monkeypatch.setenv("GRIMOIRE_HOME", …)`):
  - `test_a_fresh_store_resolves_every_task_to_the_active_connection` (conn id `openrouter`, `via="role"`, `role="primary"`).
  - `test_a_route_pin_lowers_to_that_connections_dict` (`route_dossier=local` → `conn["id"] == "local"`, `conn["base_url"]` is local's, `conn["sampling"]["preset_id"]` is local's own preset).
  - `test_the_fallback_rides_on_the_primary_dict` (`conn[FALLBACK]["id"] == "spare"`; `FALLBACK not in conn[FALLBACK]`).
  - `test_a_keyless_fallback_is_no_fallback` (`conn[FALLBACK] is None`).
  - `test_no_active_connection_resolves_to_nothing` (`resolved.conn is None`).
  - `test_an_unreadable_connection_file_reads_as_missing` (write garbage bytes to a routed connection's `.md` → walked past).
  - `test_a_provider_only_override_keeps_that_providers_model` and `test_a_model_only_override_keeps_the_standing_provider`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** both modules; bind them in `store/inference/__init__.py`.
- [ ] **Step 4: Run** the file → PASS; `pytest tests/test_import_guard.py tests/test_lock_domain_guard.py tests/test_paths_guard.py -q` → PASS (classify the package in `store/locks.py` as `OUTSIDE_DOMAIN` with reason "global inference configuration; takes no campaign id" if the guard asks).
- [ ] **Step 5: Commit** — `feat(inference): resolve a task to provider, model, preset and fallback`

---

### Task 6: The facade honours a fallback carried on the connection

**Files:**
- Modify: `backend/src/grimoire/llm.py` (`LLMClient.stream`, `complete`, `_routes`)
- Test: `backend/tests/test_llm.py`

**Interfaces:**
- Produces: `llm.FALLBACK = "_fallback"` (must equal `store.inference.resolved.FALLBACK`; a test asserts it, as `test_image_description_draft.py` pins the kinds partition).
- Behaviour: `LLMClient._routes(conn)` reads and strips `FALLBACK` — the primary it returns carries no such key. When the key was present, its value (dict or `None`) is the fallback and the `fallback=` resolver is **not** called; when absent, today's resolver path runs unchanged. `fallback_sampling(primary, fallback)` and `_same_route` still apply. Every place `stream()`/`complete()` uses the connection before `_routes` (labels, capture, health observation) must read the stripped primary, which the adapter test below proves for the dispatch path.

- [ ] **Step 1: Write failing tests:**
  - `test_a_fallback_on_the_connection_replaces_the_resolver` (resolver would return A; conn carries B; exhaustion reaches B; resolver never called).
  - `test_a_carried_none_means_no_fallback_even_with_a_resolver`.
  - `test_without_the_key_the_resolver_still_answers` (today's behaviour).
  - `test_the_key_never_reaches_an_adapter` (the fake provider's received `conn` has no `FALLBACK`).
  - `test_the_fallback_key_is_one_string` (`llm.FALLBACK == store.inference.resolved.FALLBACK`).
- [ ] **Step 2: Run** `pytest tests/test_llm.py -q -k fallback` → new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_llm.py tests/test_llm_lifecycle.py tests/test_llm_error_status.py -q` → PASS.
- [ ] **Step 5: Commit** — `feat(llm): a connection may carry its own fallback`

---

### Task 7: Switch the seam and every call site

**Files:**
- Modify: `backend/src/grimoire/routes/common.py`; every caller in `routes/` (`scenes.py` ×21, `character_turns.py` ×3, `characters.py` ×3, `worlds.py` ×2, `campaigns.py`, `config.py`, `continuity.py`, `greetings.py`, `mechanics.py`, `passage_characters.py`)
- Modify: `backend/tests/test_routing_guard.py`, `backend/tests/inference_baseline.py`, and the tests listed in Step 6
- Test: `backend/tests/test_inference_equivalence.py`, `backend/tests/test_routing_routes.py`

**Interfaces:**
- Consumes: `store.inference.resolve.resolve`, `problem`, `model_params`; `ResolvedInference`.
- Produces (`routes/common.py`):
  - `require_inference(task: str = "", cid: str = "", *, operation: str = "generate") -> ResolvedInference` — resolves, then refuses exactly as `_usable_or_409` does today: `conn is None` → 409 `{"detail": "No LLM connection selected", "kind": "missing_key"}`; `problem(conn)` with `via == "route"` → 409 with today's routed wording using `store.routing.label_for(resolved.legacy_route)`; otherwise `problem` → 409 `{"detail": problem, "kind": "missing_key"}`.
  - `override_inference(body, task: str = "", cid: str = "") -> tuple[ResolvedInference, bool]` — today's `_override_connection` contract (refusals, messages, `routed` computed on provider id + effective model), built on `resolve(..., override=Selection(...))`.
  - `_soft_inference(resolve: Callable[[], ResolvedInference]) -> tuple[dict | None, str]` — `_soft_connection`'s contract, returning `resolved.conn`.
  - Removed: `_routed_connection`, `_standing_connection`, `_usable_or_409`, `_with_sampling`, `_attach_sampling`, `_require_connection`, `_override_connection`, `_soft_connection`, `_connection_problem` (callers use `store.inference.resolve.problem`), `_model_params` (→ `store.inference.resolve.model_params`).
  - Kept: `_fallback_connection` (still passed to `build_llm`; reimplemented as the Primary role's fallback from `resolve("", campaign_meta={})`, so tests that build an `LLMClient` with it keep their meaning). Slice I removes it.
  - Display-only resolutions (`config.py` images-reach hint; `scenes.py` context breakdown and diff) call `store.inference.resolve.resolve("chat", campaign_meta=…)` and read `.conn`, with no refusals.

- [ ] **Step 1: Update `inference_baseline.observe`** to call `require_inference(task, cid).conn` and `override_inference(...)`, mapping results to the same JSON shape. Do **not** touch the JSON.
- [ ] **Step 2: Update `test_routing_guard.py`:** the watched name becomes `require_inference` (all occurrences, including the self-tests); add `test_a_call_sites_operation_matches_its_route` — an `operation=` keyword, when present, must be a string literal equal to `routing.route(task).operation`, and its absence means `"generate"`, which must equal the route's operation; add a self-test proving it flags `require_inference("chat", operation="decide")`.
- [ ] **Step 3: Write `test_an_unreadable_campaign_resolves_globally`** in `test_inference_resolve.py`-style but through `require_inference(task, cid)` with a `campaign.md` replaced by invalid UTF-8 bytes → the global answer, no exception (Review Focus 3).
- [ ] **Step 4: Run** `pytest tests/test_inference_equivalence.py tests/test_routing_guard.py -q` → FAIL (names missing).
- [ ] **Step 5: Implement** the seam in `routes/common.py` and switch every call site mechanically: `_require_connection("t", cid)` → `require_inference("t", cid).conn`; `conn, routed = _override_connection(b, "t", cid)` → `resolved, routed = override_inference(b, "t", cid); conn = resolved.conn`; `_soft_connection(lambda: _require_connection("t", cid))` → `_soft_inference(lambda: require_inference("t", cid))`. Keep every task literal and `cid` argument exactly as it was.
- [ ] **Step 6: Update test references** to removed helpers: `test_continuity_read_cost.py:391`, `test_sampler_presets_routes.py:231`, `test_routes.py:361-390` (keep testing `_fallback_connection`), comments mentioning `_require_connection` in `conftest.py`, `test_llm_error_status.py`, `test_provider_health.py`, `test_rolling_summary_routes.py`, `test_runs_routes.py`, `test_scene_break_routes.py`, `test_routes.py`.
- [ ] **Step 7: Run** `make check-py` → PASS, including `test_inference_equivalence.py` against the untouched JSON.
- [ ] **Step 8: Commit** — `refactor(routes): every generation resolves through require_inference`

---

### Task 8: Embeddings resolve through the Embedding role

**Files:**
- Modify: `backend/src/grimoire/store/embed_space.py:28-80`
- Test: `backend/tests/test_embeddings.py` (or a new `test_embed_space_role.py`)

**Interfaces:**
- Consumes: `translate.global_view`, `keys.role_key`, `llm_connections.read_connection_raw`.
- Produces: `embed_space.resolve(cfg: dict | None = None) -> dict | None` — same signature and return shape (`{model, base_url, key, space}`), same `space` string `f"{conn_id}\0{rev}\0{model}"`, same "None for every kind of not set up", same `openai_compatible`-only rule (slice B widens it).

- [ ] **Step 1: Write failing tests:**
  - `test_the_embedding_role_is_what_resolves`: monkeypatch `translate.global_view` to return a view whose `role_embedding_provider/model` differ from the legacy keys → `resolve()` follows the view.
  - `test_a_format_two_config_resolves_from_role_keys`: pass `cfg={"inference_format": "2", "role_embedding_provider": "local", "role_embedding_model": "m"}` explicitly → resolves with `space` built from `local`'s id and rev.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement:** read provider/model from `translate.global_view(cfg, lookup)` under `keys.role_key("embedding", "provider"/"model")`; everything after is unchanged.
- [ ] **Step 4: Run** `pytest tests/test_embeddings.py tests/test_context_semantic.py tests/test_semsearch_store.py tests/test_continuity_similarity.py tests/test_inference_equivalence.py -q` → PASS.
- [ ] **Step 5: Commit** — `refactor(embeddings): the embedding endpoint is the Embedding role`

---

### Task 9: Docs, gate, and the slice's closing checks

**Files:**
- Modify: `CLAUDE.md` ("Adding an LLM call site?" bullet), `docs/superpowers/specs/2026-10-07-inference-backend-refactor-design.md` (mark slice A landed in §14 only if that is the repo's habit — check the continuity capstone spec's history first)

- [ ] **Step 1: Update CLAUDE.md's "Adding an LLM call site?" bullet:** resolve with `require_inference(<task>, cid)` (`.conn` until slice I), name the task the call meters under; `store/routing.py` maps the task to a route, and the route resolves to a role or a pinned model (`store/inference/`); `test_routing_guard.py` fails a call that names no task, a task no route claims, an operation that differs from its route's, and a route whose tasks nothing uses. Keep the paragraph's existing reasoning about connections vs model strings.
- [ ] **Step 2: Run** `pytest tests/test_docs_guard.py -q` → PASS; grep the tree for `_require_connection` → only historical mentions in `docs/superpowers/` remain.
- [ ] **Step 3: Run the full gate:** `make check` → all targets PASS. If `check-lint`/`check-mypy` report a *smaller* count, run `make baseline` and commit the shrunken baseline with this task (CLAUDE.md: an improvement fails the ratchet too).
- [ ] **Step 4: Commit** — `docs: an LLM call site resolves through require_inference`
- [ ] **Step 5: Gates (CLAUDE.md):** run `/codex:review` against the slice's diff; then `/codex:adversarial-review` against the diff **and** the spec, asking specifically whether slice A is behaviour-neutral and whether anything in §14's slice-A row was dropped. Resolve findings before opening the PR.
