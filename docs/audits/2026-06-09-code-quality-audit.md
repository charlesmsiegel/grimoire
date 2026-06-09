# Code Quality Audit — 2026-06-09

Scope: full repo — `backend/src/grimoire/` (354 files, ~85k lines) and `frontend/src/` (~41k lines).
Method: deterministic AST analyzers (complexity, dead code, duplication, mutation/exception/global-state
hazards) over the whole backend, plus judgment review of every module group. Mechanical findings were
triaged against false positives (e.g. Pydantic default-list "hazards" were discarded — Pydantic
deep-copies field defaults). Findings marked **✅** were re-verified directly against the source during
this audit; the rest were produced by module-level review and spot-checked.

**Baseline:** `ruff check` and `ruff format --check` are clean; there are no unused imports or trivially
dead symbols. The codebase is in good *lint* health — the issues below are design, error-handling, and
consistency debt that linters don't see.

---

## Verdict in one paragraph

The architecture (three scopes, files-as-SSOT, Protocol boundaries, event bus) is sound and mostly
respected. The two systemic problems are: **(1) a silent-failure culture** — 556 `except Exception`
sites, at least 165 of which log-and-continue, several on state-mutation paths where a swallowed error
means silently lost or half-applied campaign state; and **(2) god services** — six classes between
1,400 and 2,500 lines (StateStore: 101 methods; SceneManager: 67; OrchestratorService: 65) whose
internal seams already exist but aren't enforced, which has produced the third problem: **coordinators
and neighbors reaching into each other's privates** (`_host._reverse_turn_deltas`, `store.db.fetchone`,
`self._context._characters`, `scene_manager._active_scene`). There is very little dead code and only
mild over-engineering; duplication is mostly "didn't reach for the canonical helper" drift, which the
repo's own CLAUDE.md already names as a review smell.

---

## 1. Correctness risks (fix first)

### 1.1 ✅ P0 — Retcon can silently destroy state deltas
`orchestrator/retcon.py:136-173` — `_retcon_leave_as_is` reverses the turn's deltas
(`self._host._reverse_turn_deltas`), edits the post, then re-extracts and re-applies inside a
`try/except Exception` that only logs a warning. If extraction or re-application fails, the old deltas
are gone, no new ones replace them, and the API call still returns success. Net effect: a retcon with a
flaky LLM call silently rolls back world state without telling anyone.
**Fix:** treat extraction failure as fatal for the retcon — restore the reversed deltas (or stage the
reversal and commit only after re-extraction succeeds) and surface the error.

### 1.2 ✅ High — Pre-roll resumption has no atomicity and loses the pending pre-roll on failure
`orchestrator/service.py:1976-1986` — `resolve_pre_roll` sets `state.pending_pre_roll = None` *before*
the `try`, then awaits `_continue_turn_after_pre_roll` (a 184-line pipeline that streams, extracts, and
applies deltas). If the pipeline raises midway: the pre-roll is unrecoverable (cleared), any
already-applied deltas stay applied, and the lock is released (correctly — but into a half-applied
turn). There is no compensation/rollback concept for a failed turn pipeline anywhere in the turn loop;
the same gap exists at `service.py:1468-1519` where `inventory.apply_from_deltas` failure after
`apply_routing` success just logs (`"inventory apply failed; continuing turn"`).
**Fix:** make the post-extraction apply phase all-or-nothing (the delta log already supports reversal —
use it as the compensation mechanism), and don't clear `pending_pre_roll` until the pipeline commits.

### 1.3 ✅ High — Eight StateStore write methods can leave file and index/delta-log diverged
`state_store/store.py:369-754` — `write_override`, `merge_override`, `delete_override`,
`delete_emergent`, `write_emergent`, `write_sheet`, `write_content`, `write_image_metadata` all write
the file to disk first, then update the index + delta log inside `_txn()`. `write_library_file`
(store.py:221-305) handles exactly this hazard with `except BaseException: _restore_file(...)` — the
other eight have no recovery, so a transaction failure leaves the file changed with no delta-log record
(undo history now lies about the file). Files-as-SSOT means a watcher pass eventually re-indexes, but
the delta log divergence does not self-heal.
**Fix:** apply `write_library_file`'s snapshot/restore pattern to all eight (one shared
`_snapshot_file_before()` helper — see §5.2 — makes this a small change).

### 1.4 High — Scene append/delete sequencing can desync file, sidecar, and memory
`scenes/manager.py:727-754` (append_post: `.md` write → in-memory mutation → sidecar write, no
rollback on partial failure) and `scenes/manager.py:1231-1292` (delete_post rewrites the `.md` with
shifted orders before rebuilding records; a sidecar failure after the rewrite leaves the two files
disagreeing about post order). Same family as 1.3: multi-file mutation with no ordering/rollback
discipline.
**Fix:** define one canonical order (mutate memory last; write-then-rename for files) and a shared
two-file-commit helper for `.md` + sidecar.

### 1.5 High — Silent failures on state-read/maintenance paths
The pattern "catch `Exception`, log (sometimes at `debug`), return empty/default" appears on paths
where the caller cannot distinguish *empty* from *broken*:
- `characters/service.py:628-633` — `list_pcs` swallows state-load failures with `except Exception: pass`;
  a corrupt row reads as "PC has no scene".
- `watcher/watcher.py:460-464, 513-514, 707-716` — parse failures, scene-manager reindex failures, and
  *all* processing errors are logged and dropped; the index silently drifts from disk with no surfaced
  signal (no quarantine entry, no error event, no failure counter).
- ~~`state_store/store.py:1003-1029` — inventory rebuild skips unparseable holder files silently~~
  *(correction: already fixed via #553 — the rebuild now logs a WARNING per unparseable file and
  counts skips; it is the model the other five sites should follow)*
- `context/archive.py:185-226, 265-268, 300-302` — lore triggers, vector search, keyword search all
  return `[]` on any exception (first attempt isn't even logged); context assembly silently degrades.
- `time_engine/service.py:1017-1021` — a failing NPC-tick callable falls back to an *empty* tick with
  no degradation marker in the digest.
- `imagegen/service.py:1003-1006` — corrupt imagegen campaign config silently resets to `{}`.

**Fix (policy, not whack-a-mole):** adopt a rule — *read paths may degrade but must log at WARNING with
a counter/metric; write/mutation paths must not catch-and-continue.* The 47 `# pragma: no cover -
defensive` blocks (largely in time_engine) should each get either a test that triggers them or a
narrower exception type.

### 1.6 Medium — Concurrent read-merge-write on relationships
`characters/service.py:847-920` — `update_relationship` fetches, merges in memory, upserts; two
concurrent updates lose one delta (no version column, no per-ref lock). Low likelihood single-user,
but the orchestrator's background jobs make it reachable.

---

## 2. Encapsulation failures

The Protocol-boundary architecture is real, but three bypass routes have grown around it:

### 2.1 ✅ Coordinators are "friend classes" of the Orchestrator
The sanctioned `host=self` coordinator pattern in practice means coordinators call the host's privates:
`retcon.py:137` (`self._host._reverse_turn_deltas`), `auxiliary.py:155` (`self._host._new_post`),
`fork.py:57` (`self._host._campaigns`), and several call `scenes._find_post` (a SceneManager private)
— `alternates.py:64`, `auxiliary.py:126`, `retcon.py:71,117`. The split into coordinator files is
currently cosmetic: the coupling surface is undiminished and invisible (no interface documents what a
coordinator may touch).
**Fix:** define a narrow `OrchestratorHost` Protocol (the ~6 operations coordinators legitimately
need), have coordinators depend on that, and promote `_find_post` to a public SceneManager method.

### 2.2 ✅ Orchestrator modules run raw SQL through `store.db`
`alternates.py:59`, `auxiliary.py:43,197`, `delta_applier.py:96`, and extensively `fork.py:48-385`
(including `execute`/`acquire` *writes*) query SQLite directly instead of going through StateStore
methods. This couples the orchestrator to table schemas and bypasses the owning module — the exact
pitfall CLAUDE.md warns about. `fork.py` is the worst offender (campaign duplication does direct
multi-table writes).
**Fix:** add the missing StateStore methods (`campaign_exists()`, fork-copy operations) and ban
`store.db` access outside `state_store/`/`storage/` (greppable; see §8 ratchets).

### 2.3 ✅ Duck-typed reaches into other services' privates
- `orchestrator/auxiliary.py:192` — `getattr(self._context, "_characters", None)` pulls the Characters
  service out of the ContextBuilder's privates. CLAUDE.md documents the orchestrator's narrow Characters
  dependency (`find_cast_ref`); this bypass contradicts that documented seam — inject Characters
  explicitly instead.
- `characters/service.py:577-580` — reaches into `self._drift._post_fetcher`; lines 173/177 reach into
  `_cache._active_pc` / `_cache._view_cache`.
- `scenes/importer.py:112-147` — mutates `scene_manager._active_scene` / `_pc_current_scene` and calls
  `write_sidecar()` directly, bypassing the manager's locking and events.
- `orchestrator/service.py:1183,1520,1569` — imports `strip_tracker_block` from
  `grimoire.extractor.together` (a strategy module); `api/campaigns/settings.py:107` imports `Tier`
  from `llm_gateway.tiers` while the gateway's `__init__` deliberately exports only `Route`.

### 2.4 Export bypasses the read cascade
`export/sources.py`, `export/data.py:99-206` — the export snapshot builder constructs
`data_root / "campaigns" / id / ...` paths and parses YAML by hand instead of reading through
StateStore/the cascade. Campaign-local overrides applied by the cascade may not be reflected in
exports, and a layout change breaks all five adapters. Related: `export/service.py:155` hardcodes
adapter knowledge in the service (`bytes_per_word = 8 if adapter.id == "epub" else 6`) — sizing belongs
on the adapter Protocol.

### 2.5 ✅ State and hand-rolled wiring in the API layer
- `api/imports.py:62` — `_PREVIEW_CACHE` module-level mutable dict with hand-rolled GC; domain state
  living in a router module (untestable, unbounded between GCs). Move into a small service object.
- `api/hud.py:32-52` — `_get_hud`/`_get_hud_config` re-implement the present-or-503 dependency pattern
  with `getattr(container, "extras", {})` chains returning `Any`, instead of the typed `api.deps`
  providers the repo convention mandates.
- ✅ Inline `HTTPException(404, ...)` instead of domain `*NotFoundError` + `map_lookup_errors`: ~20
  sites across `api/transient_state.py`, `api/imagegen.py`, `api/library.py` (×8),
  `api/observability.py` (×5), `api/expressions.py`, `api/auxiliary.py`, `api/context.py`,
  `api/campaigns/*`. The convention exists precisely so 404 semantics stay uniform.
- `api/campaigns/sheets.py:15-88` — fat endpoint: 73 lines of orchestration (DB query → module lookup →
  inventory assembly → sheet-creation loop) that belongs in a service method; `sheets.py:54-62` also
  swallows list failures into `ids = []`.

### 2.6 Bootstrap setter-injection temporal coupling
`bootstrap.py:230-444` — services are constructed then completed later via `set_continuity()`,
`set_metrics()`, `set_gateway()`, etc. Each setter is a partially-initialized-service hazard during
future refactors. Where the cycle is real (scenes ↔ continuity), document it; where it isn't, move to
constructor injection.

---

## 3. God classes and complexity hotspots

Deterministic measurements (cyclomatic/cognitive complexity, length), worst first:

| Location | Symbol | Measure |
|---|---|---|
| `llm_gateway/gateway.py:361` | `register_provider_defaults` | cognitive 76 (threshold 15), cyclo 34 |
| `time_engine/service.py:1037` | `_run_faction_ticks` | cognitive 74, cyclo 37 |
| `llm_gateway/gateway.py:1329` | `embed` | 231 lines |
| `scenes/analysis.py:261` | `make_adaptive_scene_analyzer` | 231 lines, cyclo 42 |
| `context/builder.py:274` | `_build_context` | 221 lines |
| `characters/ingest.py:94` | `ingest_character_card_v2` | 167 lines, cyclo 43 |
| `export/snapshot.py:190` | `build_snapshot` | cognitive 61, cyclo 36 |
| `imagegen/prompt.py:197` | `PromptComposer.compose` | cognitive 61, cyclo 38 |
| `extractor/together.py:116` | `project_tracker_to_deltas` | cyclo 40 |
| `world/calendar_service.py:48` | `_calendar_from_entity` | cyclo 40 |
| `state_store/backup.py:29` | `run_backup` | cognitive 58 |
| `watcher/watcher.py:230` | `FileWatcher.scan_now` | cognitive 57 |
| `orchestrator/service.py:1330` | `_continue_turn_after_pre_roll` | 184 lines |
| `bootstrap.py:97,301` | `build_content_services` / `build_llm_services` | 167 / 172 lines |

God classes (method/attribute counts from AST; LCOM = lack-of-cohesion metric):

| Class | Size | Natural seams |
|---|---|---|
| `StateStore` (`state_store/store.py`, 2,525 lines) | 101 methods | library file I/O · campaign content files · campaign SQLite state · search/embeddings · delta log · inventory — already distinct method clusters; extract as composed sub-stores behind the existing Protocol |
| `SceneManager` (`scenes/manager.py`, 1,673 lines) | 67 methods, LCOM 84 | lifecycle · posts · summarization · cast-change queue |
| `OrchestratorService` (`orchestrator/service.py`, 2,305 lines) | 65 methods, 24 attrs | the coordinator pattern is the right idea — finish it with a real host interface (§2.1) and move the turn pipeline stages out of 184/135-line methods |
| `LLMGatewayService` (`llm_gateway/gateway.py`, 1,792 lines) | 54 methods, LCOM 148 | routing · retry/fallback (duplicated twice, §5.4) · embeddings+cache · pricing · health |
| `ImageGenService` (`imagegen/service.py`, 1,484 lines) | 49 methods, LCOM 25 | backend registry · job pipeline · metadata/tags · config |
| `TimeEngineService` (`time_engine/service.py`, 1,679 lines) | 27 methods + 6 injected callables, 14 attrs | advancement pipeline (`_run_pipeline` orders 11 steps) vs. clock/calendar facade |

**Recommendation:** don't schedule a big-bang split. Apply the repo's own coordinator pattern *with a
typed host interface* when a file is next touched, and hold the line with a complexity gate on new code
(§8). The turn-pipeline duplication (§5.5) is the one split worth doing proactively because it is also
a correctness hazard (two copies of delta application drift).

---

## 4. Dead code (verified)

Genuinely little — ruff is clean and the analyzer's 2,710 raw "dead code" hits were almost entirely
false positives (Protocol params, re-exports). What survives verification:

- ✅ `orchestrator/auxiliary.py:189` — `_ = AuxiliaryAlreadyCommittedError` *after* a `return`:
  unreachable linter-appeasement. Delete (and the import if then unused).
- `mechanics/service.py:74` — `_SHEET_KIND_DEFAULT` constant, zero references.
- `extractor/config.py:49` — `retry_on_parse_failure` is never set to anything but its default by any
  caller; the extractor's `_noop_list/_noop_llm/_noop_heuristic` strategy-disable paths are exercised
  only by tests. Either test the off-paths for real or hardcode.
- `watcher/classifier.py:218,236,252` — `DIR_TO_KIND.get(d) or d` fallback is unreachable given the
  guards above each call.
- `characters/service.py:766` — `pending_pc_inputs_since_last_advance()` has no production caller
  (test-only public API).
- Drift feature half-wired: `character_state.drift_score` / `tier_pin` are written
  (`characters/service.py:504-560` via event subscriber) but nothing reads them to make a decision —
  speculative until the consumer exists.
- `orchestrator/service.py:134-151` — `_NullAutoDisable` permanent stub; `config.py:38`
  `per_campaign_concurrency: int = 1` never varied.
- `world/calendars/holidays_seed.py` (849 lines) — ~25 holiday sets built eagerly at import; only
  reachable via two lookup functions. Cold data inline; fine to keep, better as lazy data files.

---

## 5. Duplication & convention drift

CLAUDE.md names "private reimplementation of canonical helpers" a review smell. Current drift:

1. ✅ **JSON-from-LLM extraction ×3** — `extractor/llm_strategy.py:68` `_extract_json_payload` (3-stage
   fence/whole/bare-brace parser) and `continuity/llm_judge.py` `_extract_json` duplicate
   `grimoire.util.extract_json_object` (which `scenes/default_summarizers.py` correctly uses).
2. ✅ **JSON (de)serialization ×3** — `state_store/store.py:86-99` `_json_dumps`/`_json_loads` and
   `state_store/indexers.py:31` `_json_or_none` vs `util.safe_json_dumps/loads` (the store's variant
   silently drops `default=str` — a behavioral divergence, not just cosmetic).
3. ✅ **`_maybe_json` byte-identical ×2** — `library/service.py:928` and `library/composition.py:426`.
4. **Gateway retry/fallback state machine ×2** — `_invoke_complete` (gateway.py:744) vs `_stream_inner`
   (gateway.py:969); the analyzer independently flagged twin blocks at 462/479 and 1458/1501. A retry
   policy change must currently be made twice.
5. **Turn pipeline ×2** — `orchestrator/service.py:1404-1476` vs `1616-1642`
   (`_continue_turn_after_pre_roll` vs `_run_speaker_loop`): extract → resolve cast changes →
   apply-routing implemented twice. This is the duplication most likely to cause a real bug.
6. ✅ **Inline `_Gateway` Protocol ×4** — `scenes/default_summarizers.py:27` (+ second copy at 176 via
   the analyzer) and `scenes/analysis.py:42` + `analysis.py:53`, where `_AdaptiveGateway` is
   *character-identical* to `_Gateway` ten lines above it. One shared `GatewayLike` protocol.
7. ✅ **ID generation drift** — `util.new_id("prefix")` is the convention, but 13+ sites use raw
   `uuid.uuid4()`: worse, post/turn IDs are dashed in `scenes/manager.py:1653` (`str(uuid4())`) and
   dashless in `scenes/importer.py:169` (`uuid4().hex`) — two formats for the same field.
8. ✅ **Raw YAML I/O** — `hud/config.py:200,212`, `transient_state/config.py:45`,
   `mechanics/authoring.py:199-287` (×4), `scenes/storage.py:353` call `yaml.safe_load/safe_dump`
   directly against the "never call yaml.safe_load" rule (`grimoire.files.yaml_io`).
9. **Continuity fact queries fetch-all-then-filter ×3** — `facts_about`/`recent_facts`/`facts_known_by`
   (`continuity/service.py:311-428`) each pull `list_facts()` and filter in Python; push filters into
   the store (it has SQLite + FTS under it).
10. Smaller twins worth folding when touched: adaptive summarizer rolling/final passes
    (`default_summarizers.py:190-334`, ~90% identical), `scenes/indexer.py:369/387` loops,
    `characters/sheet_manager.py:463/549` try-blocks, export plugins' identical `_data_root()` ×3,
    `characters/service.py:619,659` local `import json as _json`.

---

## 6. Over-engineering — mostly acquitted

- **26 Protocols in `types/protocols.py`, most with exactly one implementation.** In this codebase
  that's the *documented* boundary mechanism, not speculative abstraction — keep. But prune unused
  Protocol *methods* (dead interface surface found on several), and resist adding Protocols for
  intra-module classes.
- The analyzer's "thin wrapper" flags (SceneLedger, CostTrackerService, ErrorStore, CastChangeStore,
  ExtrasMirror, InventoryPersistence) are **false positives** — they're table gateways that own SQL for
  one domain; that's the right pattern here.
- Real (small) instances: `_NullAutoDisable` (§4), never-varied config knobs (§4),
  `WatchedFile` carrying 11 optional fields of which each kind uses ~5 (`watcher/classifier.py:45-85`)
  — consider per-kind dataclasses; `characters/service.py:1029-1044` pass-through `_ingest`/
  `_finalize_import` middle-men into `sheet_manager` privates.

---

## 7. Frontend

1. **Zod convention is 96% unenforced.** `api/client.ts` supports a schema parameter, but only 2 of ~56
   campaign endpoints validate (`campaignApi.list`, `listCastChanges`); the library API validates none;
   62 `Record<string, unknown>` in `routes/`. Five `as unknown as SheetSchema` casts
   (`ContentBrowser.tsx:111`, `CastView.tsx:428`, `MechanicsView.tsx:179`, `CharacterCreation.tsx:150`,
   +1) bypass validation entirely. Either commit to Zod at the boundary or drop the claim — the current
   state has the cost of both and the safety of neither.
2. **God components.** `routes/campaign/PostItem.tsx` — 617 lines, 17 `useState` across four
   independent features (alternates, edit, aux actions, delete) with an implicit state machine;
   `InputArea.tsx` 13 `useState` (suggestion + polish are the same async shape twice);
   `EntityEditorView.tsx:104-141` mirrors server state into 8 client states. Extract per-feature hooks
   (`usePostEditor`, `useAuxAction`) / `useReducer`.
3. **No shared dialog.** ~15 files hand-roll the same `modal-backdrop / modal / modal-actions`
   structure and open/close state, despite Radix Dialog being a project dependency.
4. **Uncancelled async effects.** `PostItem.tsx:578-617` (CostLabel IntersectionObserver fetch) and
   `SideHud.tsx:236-273` use a local `cancelled` flag but never abort the request — stale responses
   race on fast navigation. Thread `AbortSignal` through the API client.

---

## 8. Ratchets — keep the cleared classes cleared

1. **Ban the bypasses mechanically** (ruff `lint.flake8-tidy-imports.banned-api`):
   `yaml.safe_load`/`yaml.safe_dump` (→ `grimoire.files.yaml_io`), `uuid.uuid4` (→ `util.new_id`)
   outside `util.py`, and add `BLE001` (blind `except`) — start with per-file ignores for the existing
   556 sites and shrink.
2. **Complexity gate for new code:** enable mccabe (`C901`, max ~15). The existing offenders get
   per-file ignores; new code holds the line.
3. **Greppable boundary checks in CI** (the module-boundary reviewer exists for diffs; add repo-wide):
   `\.db\.(fetch|execute|acquire)` outside `state_store/`/`storage/`/table-gateway modules;
   `_host\._` outside a documented host interface; `HTTPException\(status_code=404` outside `api/util.py`.
4. **Frontend:** ESLint `no-restricted-syntax` for `as unknown as`; make the API client's Zod schema
   parameter required (use `z.unknown()` as the explicit opt-out so exceptions are visible).

---

## 9. Suggested order of work

| Priority | Work | Size |
|---|---|---|
| P0 | 1.1 retcon delta restore; 1.2 pre-roll/turn-pipeline compensation | small / medium |
| P0 | 1.5 error-handling policy on the five listed silent paths (watcher, list_pcs, archive, npc tick, imagegen config) — inventory rebuild already fixed (#553) | medium, mechanical |
| 1 | 1.3 + 1.4 file/index write-ordering: shared snapshot-restore + two-file-commit helpers | small |
| 2 | Convention convergence batch (one PR each, zero risk): §5.1-5.3 JSON helpers, §5.6 shared `_Gateway`, §5.7 `new_id`, §5.8 `yaml_io`, §2.5 `map_lookup_errors`, §4 dead-code deletions | small × 6 |
| 3 | §2.1/2.2 OrchestratorHost Protocol + StateStore methods for fork/auxiliary SQL | medium |
| 4 | §5.5 unify turn pipeline (then the OrchestratorService split falls out) | medium |
| 5 | Frontend Zod ratchet + dialog component + PostItem split | medium |
| opportunistic | God-class seams (§3) when next touching each file — not as a standalone project | — |

Everything in priority 2 is behavior-preserving and safe to batch; the P0 items need a
characterization test first (pin current behavior, then fix).

---

## 10. Cross-reference against the GitHub issue backlog

> **Update (same day):** every "not tracked" entry below has since been filed as #583–#598
> (correctness: #583 retcon, #584 pre-roll, #585 state-store writes, #586 scenes, #587 silent reads,
> #588 relationship race; encapsulation: #589 privates, #590 raw SQL, #591 export cascade, #592 api
> hygiene; cleanup: #593 dead code, #594 continuity perf; frontend: #595 Zod, #596 dialog,
> #597 InputArea; gates: #598), with detail comments added to #518, #521, #522, #538, #545, #550,
> #551, #561, #564. The table is kept as the as-audited snapshot.
>
> A follow-up *mechanical* sweep (`_host\._`, `self\._\w+\._\w+`, `getattr(obj, "_…")`,
> cross-package `_private` imports) found §2.1/§2.3 sites this report's reading-based pass missed —
> most notably the `RetconReplaySession` friend-class (`retcon_replay.py`, ~11 reach-ins via
> `self._orch`). **#589's body is the canonical, complete inventory**, including the explicit
> out-of-scope tier (same-package collaborator privates → #521). Method lesson for the next audit:
> sweep this category with greps; reading alone under-reports it.

Checked 2026-06-09 against all 45 open issues plus targeted closed-issue searches. The repo has
already run three audit passes (orchestrator simplification audit → #518–#523; code-quality /
coding-standards sweep → #535–#556; python-simplifier + API-surface audits → #530–#533, #561–#565),
so much of this report's *architecture and convention* material is already tracked. The
*correctness* section largely is not.

| This report | Backlog status |
|---|---|
| §1.1 retcon silent delta destruction | **not tracked** (closed #101/#113 covered apply-rollback and downstream flagging, not re-extraction failure) |
| §1.2 pre-roll cleared before `try`; inventory failure mid-turn | **not tracked** (closed #98/#101 handled player-post rollback, a different pipeline stage) |
| §1.3 StateStore 8 write methods without snapshot/restore | **not tracked** (#521 splits the class; says nothing about write-ordering) |
| §1.4 scene `.md`/sidecar/memory desync | **not tracked** |
| §1.5 silent failures | inventory rebuild **fixed** (#553, closed); generic sweep #552 (S110/S112/BLE001) exists; the 5 named sites **not individually tracked** |
| §1.6 relationship read-merge-write race | **not tracked** |
| §2 encapsulation | partial: campaign-exists SQL ×6 → #523 §2; `host: Any` typing → #520; inline 404s → #523 §1 (counts ~130); SceneManager setters → #521. `_find_post`, importer `_active_scene`, export cascade bypass, `bytes_per_word`, `_PREVIEW_CACHE`, hud deps **not tracked** |
| §3 god classes / complexity | **tracked**: #518 (TurnCoordinator, full dependency map), #521 (StateStore-first decomposition), #538 (C901, 93 fns), #565 (deep nesting incl. `run_backup`) |
| §4 dead code | family-level only (#523 §6 `emit_typed`, #554 ERA001, #530–#533 orphan routes); the 8 named items **not tracked** |
| §5 duplication / drift | **mostly tracked**: #522 (slugify ×8, `new_id` ×16, JSON extractors, `now_iso`), #561 (`_maybe_json`, intra-file clones), #523 (YAML I/O, 404s, dispatch tables). Turn-pipeline ×2 and continuity fetch-all ×3 **not tracked** |
| §6 over-engineering | #520 is sharper: all 17 service Protocols have zero importers |
| §7 frontend | partial: #540 PostItem, #542/#549 fetch hooks, #543, #545 (1 of 5 casts), #548, #556. Zod 2/56 endpoints, ~15 hand-rolled modals, InputArea, 4 remaining casts **not tracked** |
| §8 ratchets | mostly tracked (#550/#538/#535/#552/#536/#537/#539); the repo-grep checks (`store.db`, `_host._`, inline 404) and required-Zod-param **not tracked** |

Backlog items this report missed: ASYNC240 blocking I/O in async (#555), vector→BLOB triplication
and the `_parse_character_ref` canonicalization divergence (#522 §4–5), tiered-confidence routing ×3
and `emit_typed` (#523), data clumps / `kind` vs `entity_kind` (#564), boolean-blindness (#563), the
existing `useResource`/`useApi` hooks (#549), and the API-surface (#530–#533) and test-strategy
(#524–#529) families, which were out of scope here.
