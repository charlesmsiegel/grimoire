# Breaking the store's import cycles

## Problem

`docs/codemap.html` reports a 63-file strongly connected component inside
`backend/src/grimoire/store/`. That headline number is a measurement artifact:
the atlas attributes `from . import scenes` to the *package* `grimoire.store`,
so `__init__.py` importing 59 submodules while each submodule imports siblings
collapses the whole package into one node.

Resolved to actual submodules, the picture is smaller but real:

- The module-level graph has **one** true cycle today:
  `scenes → scene_refs → audit → scenes`.
- **53 grimoire-internal imports sit inside function bodies**, each one
  deferring an edge that would otherwise close a loop. Hoisting all 53 to
  module scope produces a single 15-module SCC:
  `appearances, audit, campaign_climate, campaigns, changes, chronicle,
  module_display, modules, overlay, plot, rolls, scene_refs, scenes, sheets,
  worlds`.
- **4 more in-function imports** are third-party rather than cycle-driven:
  `jinja2` in `prompts.py:30` and `store/epub.py:32`, `tiktoken` in
  `store/context.py:723`, `claude_agent_sdk` in `claude_agent.py:35`.

Separately, six store modules have grown past the point where they can be held
in context at once: `module_edit.py` (1297 lines), `scenes.py` (890),
`modules.py` (829), `absorb.py` (784), `sheets.py` (744), `context.py` (733).

Goals: every import at module top, no import cycles, and smaller files.

## Scope

`backend/src/grimoire/store/` only, plus the four third-party lazy imports
elsewhere in `backend/src/grimoire/`. `routes/` has no measured cycles and the
frontend has none; both are out of scope.

## The key observation

Every back-edge in the tangle is a **lifecycle function reaching sideways**,
while every forward edge asks only for **record-access primitives**. Measured
attribute usage across the 15-module SCC:

```
modules   → campaigns : campaign_meta_path, read_campaign      (deferred)
campaigns → modules   : pack_root                              (deferred)

worlds    → campaigns : world_refs                             (deferred)
campaigns → worlds    : world_root, world_exists, canonical_id, WorldNotFound

scenes    → audit     : capture_baseline                       (deferred)
audit     → scenes    : get_location_history
```

`create_campaign` seeds a campaign's climate, modules and sheets;
`delete_world` cascades into campaigns; `_create_scene` snapshots an audit
baseline. Meanwhile nothing outside `campaigns.py` imports `create_campaign`,
nothing outside `worlds.py` imports `delete_world`, and nothing outside
`scenes.py` imports `_create_scene`.

Splitting each record kind along that seam removes all 15 nodes from the SCC
without introducing indirection.

## Layer model

Four layers, each importing strictly downward.

| Layer | Contents | May import |
|---|---|---|
| **L0 primitives** | `paths`, `atomic`, `frontmatter`, `locks`, `proclock`, `scene_ids`, `fence`, `dice`, `expressions` | stdlib only |
| **L1 record cores** | per kind: path resolution, `read_*`, primitive writes, the kind's exception class | L0 |
| **L2 derived readers** | `overlay`, module pack loading and display, `chronicle`, `checks`, sheet reads | L0, L1 |
| **L3 orchestration** | create/delete cascades, `audit`, `absorb`, `context`, `module_edit`, module binding | anything below |

The layers constrain *cross-layer* direction only. Imports within a layer are
allowed as long as they stay acyclic — `scenes/read.py` (L2) importing
`appearances/cast.py` (L2) is fine. The guard test checks for cycles, not for
layer labels; the table is how to think about placement, not a second rule.

## Target structure

Ten modules become subpackages, following the existing `weather/`,
`calendars/` and `climates/` precedent. Roughly 63 → 109 store modules; the
largest file drops from 1297 lines to about 250.

### `campaigns/`

| File | Layer | Contents |
|---|---|---|
| `paths.py` | L1 | `CampaignNotFound`, `_campaigns_dir`, `campaign_root`, `campaign_meta_path`, `world_root_of`, `campaign_exists`, `_manifest_path`, `read_manifest`, `write_manifest` |
| `read.py` | L1 | `read_campaign`, `list_campaigns`, `world_refs`, `touch` |
| `lifecycle.py` | L3 | `create_campaign`, `delete_campaign`, `rename_campaign`, `ensure_campaign_slim`, `_tombstone_deleted_copied_assets`, `_prune_duplicate_files`, `set_campaign_response` |

`paths.py` is the highest-value extraction in the whole refactor: `audit`,
`appearances`, `chronicle`, `changes`, `plot`, `rolls`, `overlay`, `scenes`,
`sheets` and `campaign_climate` all import `campaigns` for nothing but these
names.

### `worlds/`

| File | Layer | Contents |
|---|---|---|
| `paths.py` | L1 | `WorldNotFound`, `_worlds_dir`, `world_root`, `world_meta_path`, `world_exists`, `names_its_directory`, `canonical_id`, `references_world` |
| `read.py` | L1 | `read_world`, `world_name`, `list_worlds` |
| `lifecycle.py` | L3 | `WorldInUse`, `create_world`, `rename_world`, `delete_world` |

### `scenes/`

| File | Layer | Contents |
|---|---|---|
| `paths.py` | L1 | `SceneNotFound`, `_scenes_dir`, `_scene_path`, `_require_campaign` |
| `serialize.py` | L1 | `_label`, `_markers`, `match_name`, `_speaker_and_role`, `_serialized`, `_parse_messages`, `_serialize_messages`, `_block`, `_numbering`, `repad` |
| `read.py` | L2 | `read_scene`, `read_scene_meta`, `list_scenes`, `is_pcless`, `get_dismissed`, `get_location_history`, `get_time_history`, `get_suggested_date`, `trailing_transitions` |
| `turns.py` | L2 | `_parse_turn_sizes`, `get_turn_sizes`, `_set_turn_sizes`, `_reconciled_turn_sizes`, `_trailing_model_run`, `_tracked_suffix_fits`, `_model_blocks`, `TurnSizesDesynced` |
| `write.py` | L2 | `append_message`, `_append_block`, `append_reply`, `split_reply`, `edit_message`, `remove_trailing_assistant_run`, `trim_continuation`, `mark_absorbed`, `stamp_greeting`, `stamp_user_speaker`, `add_dismissed`, `set_pcless`, `set_response`, `RollMessageImmutable` |
| `moment.py` | L2 | `set_location`, `set_datetime`, `_apply_datetime`, `_stamp_start_date` |
| `lifecycle.py` | L3 | `create_scene`, `_create_scene`, `_date_hint`, `rename_scene`, `delete_scene` |

`lifecycle.py` is the only file that touches `audit.capture_baseline` and
`scene_refs.repoint`.

`moment.py` rather than `datetime.py`: `scenes/__init__.py` will do
`from . import moment`, and binding the name `datetime` in a package namespace
that also uses the stdlib module is a trap not worth setting.

### `appearances/`

| File | Layer | Contents |
|---|---|---|
| `paths.py` | L1 | `AppearError`, `_ref`, `_split`, `_path`, `locked_actor_root`, `record`, `_write`, `_lock` |
| `versions.py` | L1 | `set_base`, `actor_hash`, `_version_ext`, `_meta_name`, `_copy_actor`, `_purge_other_versions`, `_set_default`, `_drop_manifest_ref`, `pick_version`, `import_version`, `locked_version` |
| `cast.py` | L2 | `_actor_name`, `players_in_scene`, `player_names`, `scene_cast`, `cast_detail`, `roster`, `roster_names`, `is_appeared` |
| `transitions.py` | L3 | `appear`, `leave`, `repoint_scenes`, `suggestions` |

### `modules/`

| File | Layer | Contents |
|---|---|---|
| `fields.py` | L1 | `assembled_fields`, `numeric_names`, `_pool_group_fields` |
| `pack.py` | L2 | `ModuleError`, `ModuleNotFound`, `ContentNotFound`, `builtin_dir`, `user_dir`, `_safe_mid`, `pack_root`, `load_pack`, `load_pack_at`, `_scan`, `list_modules` |
| `validate.py` | L2 | `_validate_manifest`, `_validate_field`, `_validate_derived`, `_validate_creation`, `_validate_advancement`, `_validate_outcomes`, `_validate_checks`, `_validate_sheets`, `validate_sheet_values`, `_as_list`, `_as_dict` |
| `content.py` | L2 | `_load_rules`, `_load_content`, `read_content`, `read_rule`, `_split_csv` |
| `display.py` | L2 | all of today's `module_display.py` |
| `binding.py` | L3 | `_write_key`, `set_world_module`, `set_campaign_module`, `resolve` |
| `admin.py` | L3 | `create_module`, `delete_module` |

`module_display.py` disappears as a top-level module; `store/__init__.py`
keeps exporting the name for compatibility.

### `sheets/`

| File | Layer | Contents |
|---|---|---|
| `paths.py` | L1 | `SheetError`, `SheetConflict`, `sheet_kind`, `_campaign_dir`, `_campaign_path`, `_world_dir`, `_world_path`, `_next_gen`, `_atomic_write_json` |
| `schema.py` | L1 | `_MUTABLE_TYPES`, `_int_or`, `default_fields`, `_numeric_scope`, `_compute_derived`, `expression_scope`, `_validate_instance`, `instance_errors`, `canonical_field_value` |
| `read.py` | L2 | `_read_path`, `read`, `read_world`, `list_refs`, `world_list_refs`, `world_sheet_modules` |
| `pools.py` | L2 | `_pool_floor`, `_pool_group_fields`, `_pool_budget` |
| `write.py` | L2 | `_validate_write_target`, `_checked_write`, `_stored_snapshot`, `_check_expected`, `write`, `write_world`, `delete`, `set_field`, `_set_field_locked` |
| `creation.py` | L3 | `_assert_world_entity_exists`, `_assert_campaign_entity_exists`, `_checked_creation_write`, `write_creation`, `write_world_creation`, `delete_world` |
| `coverage.py` | L3 | `_type_kinds`, `_tally`, `coverage`, `world_coverage`, `seed` |
| `advance.py` | L3 | `_advancement_cost`, `advance` |

Two name collisions in the tables above are real and intentional, not typos.
`_pool_group_fields` exists separately in today's `modules.py` and `sheets.py`
and stays two distinct private functions. `sheets.delete_world` deletes a
world's *sheets*; it is unrelated to `worlds.delete_world`, and both keep their
current names.

### `audit/`

| File | Layer | Contents |
|---|---|---|
| `baselines.py` | L2 | `_lock`, `_path`, `_write`, `read_baselines`, `schema_stamp`, `capture_baseline`, `baseline_entry_valid`, `baseline_field`, `clear_baselines`, `repoint_scenes` |
| `prompt.py` | L3 | `sheet_scope`, `_field_label`, `render_value`, `sheet_blocks`, `roll_lines`, `build_prompt` |
| `apply.py` | L3 | `AuditParseError`, `parse_output`, `apply_delta`, `materialize` |

### `module_edit/`

The atlas names five concerns in this file; the split separates them plus
`rename` (a single 229-line function with its own helper cluster).

| File | Contents |
|---|---|
| `staging.py` | `locked`, `_staging_root`, `new_mid`, `_publish` |
| `journal.py` | `recover`, `_replay_journal`, `_require_user_root` |
| `packs.py` | `duplicate_module`, `create_module`, `delete_module`, `export_module`, `import_module`, `_member_parts`, `_check_archive` |
| `migrate.py` | `_sheet_files`, `_migrate_file`, `_would_migrate`, `_migrate_preview`, `_file_kind`, `_iter_ref_values`, `_content_ids`, `_sidecar_stats_at`, `_impact`, `_run_migration`, `_campaign_locks`, `_result`, `_sample`, `_apply` |
| `layout.py` | `_edit_tree`, `_specialize_layout`, `_prune_node`, `_prune_layout`, `_layout_name_edit` |
| `rename.py` | `_RenameCollision`, `rename`, `_field_keys`, `_group_scope`, `_rewrite_expr`, `_rewrite_exprs`, `_rewrite_placeholders`, `_rename_map_key`, `_composing_tids`, `_fragment_users` |
| `edits.py` | `set_manifest`, `_read_json`, `_write_json`, `_read_sheets`, `check_proposal_guard`, `upsert_group`, `delete_group`, `upsert_sheet_type`, `delete_sheet_type`, `upsert_check`, `delete_check`, `set_check_defaults`, `_rule_meta`, `upsert_rule`, `delete_rule`, `upsert_content`, `delete_content`, `set_layout`, `set_theme` |

### `absorb/`

| File | Contents |
|---|---|
| `prompt.py` | `build_prompt` |
| `parse.py` | `_int05`, `_truthy`, `_confidence`, `extract_object`, `parse_output` |
| `materialize.py` | `materialize`, `_char_name`, `_actor_exists`, `_entity_kind`, `_DossierTargetGone`, `_new_character_provenance`, `_new_character_dossier` |
| `weather.py` | `_weather_edits`, `_apply_weather` |
| `apply.py` | `apply_edits` |
| `snapshots.py` | `relationships_snapshot`, `plot_snapshot`, `group_snapshot`, `state_snapshot`, `_snapshot_line` |

### `context/`

| File | Contents |
|---|---|
| `macros.py` | `_substitute`, `scene_substitutions`, `_datetime_subs`, `_expand_random`, `_expand_rolls`, `_strip_unknown_macros`, `expand_macros` |
| `cast.py` | `_char_name`, `_cast_directory_data`, `_drift_roster`, `cast_datetime_facts`, `_campaign_player_refs` |
| `world_state.py` | `_world_info`, `_today_data`, `_weather_data`, `_character_states`, `_group_states` |
| `mechanics.py` | `_sheet_type_label`, `_sheet_summary_lines`, `_rule_keys_match`, `_mechanics` |
| `story.py` | `_relationship_lines`, `_story_entries`, `_project_history` |
| `assemble.py` | `activate`, `_assemble`, `_system_text`, `build_messages`, `build_director_messages`, `build_opener_messages`, `context_sections` |
| `tokens.py` | `_encoder`, `count_tokens` |

## Cuts verified against the source

Four of the boundaries above were checked in the code rather than inferred
from the import graph:

- **`scenes` ↔ `appearances`.** `player_names`, `scene_cast` and
  `players_in_scene` read only the appearances record and actor roots; they
  touch no scene state. Only `appear`, `leave` and `suggestions` do. So
  `scenes/read.py → appearances/cast.py` becomes a top-level import, while
  `appearances/transitions.py → scenes/write.py` runs the other way between
  different files. Acyclic.
- **`modules` ↔ `module_display`.** Both deferred imports
  (`module_display.py:65`, `:98`, each annotated "deferred: modules imports
  us") want exactly one name: `assembled_fields`. Moving it to
  `modules/fields.py` lets `pack.py` and `display.py` both import downward.
  Acyclic.
- **`scenes` ↔ `audit`.** `capture_baseline` uses `modules.resolve`,
  `campaigns.campaign_root`, `sheets.list_refs` and `locks` — never `scenes`.
  `audit/prompt.py` is the part that reads `get_location_history`. So
  `scenes/lifecycle.py → audit/baselines.py` and `audit/prompt.py →
  scenes/read.py` do not meet. Acyclic.
- **`worlds` ↔ `campaigns`.** `delete_world` needs only
  `campaigns.world_refs`; `create_campaign` needs only `worlds`' path and
  existence helpers.

## Leaked privates become interfaces

`audit` currently reads `sheets._MUTABLE_TYPES` and calls
`sheets._set_field_locked`. After the split these are named exports of
`sheets/schema.py` and `sheets/write.py` respectively, imported like any other
public name.

## Compatibility

`store/__init__.py` keeps its current export list unchanged, so all 338
`from grimoire.store import x` call sites and every test continue to work.
Each new subpackage's `__init__.py` re-exports its members, so
`store.campaigns.create_campaign` and `store.scenes.SceneNotFound` still
resolve.

47 sites import a store submodule directly, across seven targets: `weather`
(19), `calendars` (11), `frontmatter` (10), `paths` (4), `scene_ids` (1),
`fence` (1), `climates` (1). Four are L0 modules that are not moving; the
other three are already subpackages this refactor does not restructure. No
direct-import site is affected.

`store/__init__.py`'s import order is load-bearing today. Once the graph is
acyclic it stops being — and the guard test is what demonstrates that, rather
than a claim in a docstring.

## Third-party lazy imports

All four move to module top:

| Site | Import | Treatment |
|---|---|---|
| `prompts.py:30` | `jinja2` | plain top-level import; it is a base dependency |
| `store/epub.py:32` | `jinja2` | same |
| `store/context.py:723` | `tiktoken` | `try/except ImportError` setting a module-level `None` sentinel; the existing heuristic fallback checks it |
| `claude_agent.py:35` | `claude_agent_sdk` | same sentinel pattern |

The sentinel keeps the `desktop` and `claude` extras genuinely optional, which
Android depends on, while satisfying the imports-at-top rule.

## Enforcement

A new `backend/tests/test_import_guard.py`, written in the AST-parsing style
of `test_atomic_guard.py` and `test_overlay_guard.py`:

1. Fails on any grimoire-internal import inside a function body.
2. Fails on any third-party or stdlib import inside a function body.
3. Builds the module-level import graph over `backend/src/grimoire/` and fails
   on any cycle, naming the offending path.

The guard's scan scope is `backend/src/grimoire/` only. Tests, `scripts/` and
`evals/` are not covered — a test that imports inside a function to exercise
import behavior is legitimate.
4. Exemptions are function-scoped and must state a reason; a marker inside a
   string literal does not count, matching `test_overlay_guard.py:462`.

Resolution must be per-submodule: `from . import scenes` inside the store is
an edge to `grimoire.store.scenes`, not to the package. Attributing it to the
package is exactly the mistake that produced the phantom 63-file SCC.

## Sequencing

Ordered so the graph is strictly less cyclic after every step, and each step
is independently testable.

0. Fix three `os.stat` stubs in `backend/tests/test_atomic.py` (lines 150,
   379, 402). They accept one positional argument, but Python 3.11.15's
   `pathlib` calls `os.stat(path, follow_symlinks=...)`, so a `tmp_path`
   cleanup during teardown crashes pytest with an INTERNALERROR instead of
   reporting results. Prerequisite for a trustworthy baseline.
1. Land the guard test **xfail-marked**, so it reports the exact starting set
   of violations.
2. Leaf extractions: `campaigns/paths.py`, `worlds/paths.py`,
   `modules/fields.py`, `appearances/cast.py`. This alone removes most
   back-edges.

   Each of these converts `foo.py` into a `foo/` package whose `__init__.py`
   re-exports everything, with only the extracted names moved out and the
   remaining body landing in `foo/_rest.py` for the moment. That keeps the
   step small and independently landable; the per-kind split in step 3 is what
   dissolves `_rest.py` into its final files. No `_rest.py` survives step 3.
3. Per-kind lifecycle splits, one kind per commit.
4. The four pure-size splits: `module_edit`, `absorb`, `context`, `sheets`.
5. Hoist the four third-party imports; drop the xfail.

## Verification

- `backend/.venv/bin/python -m pytest backend -q` after every step.
  Baseline on this container: **2712 passed, 2 failed** — both failures
  (`test_atomic.py::test_a_read_only_record_is_not_silently_replaced`,
  `test_assets_store.py::test_lookup_survives_a_sibling_vanishing_mid_scan`)
  are artifacts of running as uid 0, where chmod-based read-only assertions
  cannot hold. On a normal user account the suite is green.
- `scripts/verify_templates.py` and `evals/run.py` if any template-adjacent
  code moves.
- The guard test itself, unmarked, at the end.
- Per `CLAUDE.md`: `/codex:adversarial-review` against this spec before
  planning, again against the plan before implementation, `/codex:review`
  against the diff before calling it done, and a final
  `/codex:adversarial-review` against diff plus spec.

## Non-goals

- Migrating the 331 `from grimoire.store import x` call sites to deep paths.
  `store/__init__.py` stays a compatibility facade.
- `routes/` and the frontend. Neither has measured cycles.
- Behavior changes of any kind. Every step is a pure move plus import
  rewrite; a diff that changes logic is a mistake in this refactor.
- Replacing lifecycle cascades with events or a registry. The seed steps in
  `create_campaign` are legible as a sequence and should stay that way.
