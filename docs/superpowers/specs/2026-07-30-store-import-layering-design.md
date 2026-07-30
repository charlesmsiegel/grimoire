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
| `paths.py` | L1 | `CampaignNotFound`, `_campaigns_dir`, `campaign_root`, `campaign_meta_path`, `campaign_exists`, `_manifest_path`, `read_manifest`, `write_manifest` |
| `read.py` | L1 | `read_campaign`, `list_campaigns`, `world_refs`, `touch`, `world_root_of`, `_NO_WORLD` |
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
| `locking.py` | L1 | `_serialized` |
| `serialize.py` | L1 | `_label`, `_markers`, `match_name`, `_speaker_and_role`, `_parse_messages`, `_serialize_messages`, `_block`, `_append_block`, `_numbering`, `RESERVED_LABELS`, `ROLE_TO_LABEL`, `_MARKER`, `_SAFE_LABEL`, `ROLL_SPEAKER`, `TRANSITION_SPEAKER`, `SYNTHETIC_SPEAKERS` |
| `read.py` | L2 | `read_scene`, `read_scene_meta`, `list_scenes`, `is_pcless`, `get_dismissed`, `get_location_history`, `get_time_history`, `get_suggested_date`, `trailing_transitions` |
| `turns.py` | L2 | `_parse_turn_sizes`, `get_turn_sizes`, `_set_turn_sizes`, `_reconciled_turn_sizes`, `_trailing_model_run`, `_tracked_suffix_fits`, `_model_blocks`, `TurnSizesDesynced` |
| `write.py` | L2 | `append_message`, `append_reply`, `split_reply`, `edit_message`, `remove_trailing_assistant_run`, `trim_continuation`, `mark_absorbed`, `stamp_greeting`, `stamp_user_speaker`, `add_dismissed`, `set_pcless`, `set_response`, `RollMessageImmutable`, `RESPONSE_FIELDS` |
| `moment.py` | L2 | `set_location`, `set_datetime`, `_apply_datetime`, `_stamp_start_date` |
| `lifecycle.py` | L3 | `create_scene`, `_create_scene`, `_date_hint`, `rename_scene`, `delete_scene`, `repad` |

`lifecycle.py` is the only file that touches `audit.capture_baseline` and
`scene_refs.repoint`.

`moment.py` rather than `datetime.py`: `scenes/__init__.py` will do
`from . import moment`, and binding the name `datetime` in a package namespace
that also uses the stdlib module is a trap not worth setting.

`_serialized` gets its own `locking.py` rather than sharing `serialize.py`.
The names are a coincidence: `_serialized` runs a mutation under the campaign
lock, while `serialize.py` is transcript marshalling. It decorates 19
functions that end up in four different files, all of which import it. The
decorator's lock is reentrant (`locks.campaign_lock`), so spreading its call
sites across files does not change locking behavior.

### `appearances/`

| File | Layer | Contents |
|---|---|---|
| `paths.py` | L1 | `AppearError`, `_ref`, `_split`, `_path`, `locked_actor_root`, `record`, `_write`, `repoint_scenes`, `ACTOR_KINDS` |
| `versions.py` | L1 | `set_base`, `actor_hash`, `_version_ext`, `_meta_name`, `_copy_actor`, `_purge_other_versions`, `_set_default`, `_drop_manifest_ref`, `_lock`, `pick_version`, `import_version`, `locked_version` |
| `cast.py` | L2 | `_actor_name`, `players_in_scene`, `player_names`, `scene_cast`, `cast_detail`, `roster`, `roster_names`, `is_appeared` |
| `transitions.py` | L3 | `appear`, `leave`, `suggestions` |

### `modules/`

| File | Layer | Contents |
|---|---|---|
| `fields.py` | L1 | `assembled_fields`, `numeric_names`, `_pool_group_fields`, `CONTENT_KINDS` |
| `pack.py` | L2 | `ModuleError`, `ModuleNotFound`, `ContentNotFound`, `builtin_dir`, `user_dir`, `_safe_mid`, `pack_root`, `load_pack`, `load_pack_at`, `_scan`, `list_modules`, `_load_rules`, `_load_content`, `_split_csv`, `DEFAULT_BUILTIN_DIR`, `_MID_RE` |
| `validate.py` | L2 | `_validate_manifest`, `_validate_field`, `_validate_derived`, `_validate_creation`, `_validate_advancement`, `_validate_outcomes`, `_validate_checks`, `_validate_sheets`, `validate_sheet_values`, `_as_list`, `_as_dict`, `FIELD_TYPES`, `SHEET_KINDS`, `RESERVED_NAMES`, `ROLL_SCOPE_NAMES`, `_RAISABLE_TYPES`, `_PLACEHOLDER` |
| `content.py` | L2 | `read_content`, `read_rule` |
| `display.py` | L2 | all of today's `module_display.py`, `_entry`, `_read_json`, `_LayoutError`, `_type_scope`, `_union_scope`, `_Expander`, `_load_theme`, `_load_layout`, `load_display`, `COLOR_KEYS`, `CORNER_STYLES`, `DOT_SHAPES`, `FONT_KEYS`, `FONT_STACKS`, `_HEX`, `_MISSING`, `MAX_DEPTH`, `MAX_NODES`, `NODE_FORMS` |
| `binding.py` | L3 | `_write_key`, `set_world_module`, `set_campaign_module`, `resolve` |
| `admin.py` | L3 | `create_module`, `delete_module` |

### Two hazards these subpackages introduce

**A submodule may not share a name with a public function of the same module.**
`sheets.read`, `sheets.write`, `sheets.coverage`, `sheets.advance`,
`module_edit.rename` and `absorb.materialize` are all existing public
functions. A `sheets/read.py` would be shadowed the moment
`sheets/__init__.py` runs `from .reader import read`, because the function
overwrites the package attribute that held the submodule. A later
`from ..sheets import read` then binds the *function*, and
`read.list_refs(...)` raises `AttributeError: 'function' object has no
attribute 'list_refs'` — at call time, having passed both import and the
cycle guard. Verified on a prototype. Hence `reader.py`, `writer.py`,
`tally.py`, `advancement.py`, `renaming.py` and `materializer.py`: the
*facade function names are unchanged*, only the file names differ.

**Moving code changes `__file__`, and one module computes a path from it.**
`backend/src/grimoire/store/modules.py:39` defines
`DEFAULT_BUILTIN_DIR = Path(__file__).resolve().parent / "builtin_modules"`.
Relocated to `store/modules/pack.py` that resolves to
`store/modules/builtin_modules/`, which does not exist, so `pack_root` would
raise `ModuleNotFound` for every shipped pack. The data directory stays at
`store/builtin_modules/` (Android packaging references it); `pack.py` must
walk one extra level up:

```python
DEFAULT_BUILTIN_DIR = Path(__file__).resolve().parent.parent / "builtin_modules"
```

Any other `__file__`-relative path in a module being moved needs the same
treatment — grep for `__file__` before splitting a module.

`module_display.py` disappears as a top-level module. Note that
`store/__init__.py` does **not** currently export it — neither its `from . import
(...)` list nor `__all__` mentions it. `from grimoire.store import module_display`
works today only as a side effect of `modules.py:18` binding it as an attribute
of the package. `backend/tests/test_module_display.py:3` relies on exactly that.
So this step must *add* an explicit `module_display` alias to `store/__init__.py`
rather than preserve one, or update that test. Keeping the alias is preferred:
`test_module_display.py:361` patches `module_display._load_theme`, and an alias
to the same module object leaves that working.

### `sheets/`

| File | Layer | Contents |
|---|---|---|
| `paths.py` | L1 | `SheetError`, `SheetConflict`, `sheet_kind`, `_campaign_dir`, `_campaign_path`, `_world_dir`, `_world_path`, `_next_gen`, `_atomic_write_json`, `FILE_KINDS` |
| `schema.py` | L1 | `_MUTABLE_TYPES`, `_int_or`, `default_fields`, `_numeric_scope`, `_compute_derived`, `expression_scope`, `_validate_instance`, `instance_errors`, `canonical_field_value` |
| `reader.py` | L2 | `_read_path`, `read`, `read_world`, `list_refs`, `world_list_refs`, `world_sheet_modules` |
| `pools.py` | L2 | `_pool_floor`, `_pool_group_fields`, `_pool_budget` |
| `writer.py` | L2 | `_validate_write_target`, `_checked_write`, `_stored_snapshot`, `_check_expected`, `write`, `write_world`, `delete`, `set_field`, `_set_field_locked` |
| `creation.py` | L3 | `_assert_world_entity_exists`, `_assert_campaign_entity_exists`, `_checked_creation_write`, `write_creation`, `write_world_creation`, `delete_world` |
| `tally.py` | L3 | `_type_kinds`, `_tally`, `coverage`, `world_coverage`, `seed` |
| `advancement.py` | L3 | `_advancement_cost`, `advance` |

Two name collisions in the tables above are real and intentional, not typos.
`_pool_group_fields` exists separately in today's `modules.py` and `sheets.py`
and stays two distinct private functions. `sheets.delete_world` deletes a
world's *sheets*; it is unrelated to `worlds.delete_world`, and both keep their
current names.

### `audit/`

| File | Layer | Contents |
|---|---|---|
| `baselines.py` | L2 | `_lock`, `_path`, `_write`, `read_baselines`, `schema_stamp`, `capture_baseline`, `baseline_entry_valid`, `baseline_field`, `clear_baselines`, `repoint_scenes`, `_LOCKS`, `_LOCKS_GUARD` |
| `prompt.py` | L3 | `sheet_scope`, `_field_label`, `render_value`, `sheet_blocks`, `roll_lines`, `build_prompt` |
| `apply.py` | L3 | `AuditParseError`, `parse_output`, `apply_delta`, `materialize` |

### `module_edit/`

The atlas names five concerns in this file; the split separates them plus
`rename` (a single 229-line function with its own helper cluster).

| File | Contents |
|---|---|
| `packfile.py` | `_read_json`, `_write_json`, `_read_sheets` — the shared pack-file accessors |
| `scope.py` | `_RenameCollision`, `_field_keys`, `_group_scope`, `_fragment_users` — the small shared helpers every other file reaches for |
| `staging.py` | `locked`, `_staging_root`, `new_mid`, `_publish`, `_M` |
| `packs.py` | `duplicate_module`, `create_module`, `delete_module`, `export_module`, `import_module`, `_member_parts`, `_check_archive`, `MAX_MEMBERS`, `MAX_UNCOMPRESSED`, `_DRIVE_OR_UNC` |
| `migrate.py` | `_sheet_files`, `_migrate_file`, `_would_migrate`, `_migrate_preview`, `_file_kind`, `_iter_ref_values`, `_content_ids`, `_sidecar_stats_at`, `_impact`, `_run_migration`, `_campaign_locks`, `_result`, `_sample`, `_apply`, `recover`, `_replay_journal`, `_require_user_root` |
| `layout.py` | `_edit_tree`, `_specialize_layout`, `_prune_node`, `_prune_layout`, `_layout_name_edit` |
| `renaming.py` | `rename`, `check_proposal_guard`, `_rewrite_expr`, `_rewrite_exprs`, `_rewrite_placeholders`, `_rename_map_key`, `_composing_tids`, `_RENAME_KINDS`, `_SAFE_KEY` |
| `edits.py` | `set_manifest`, `upsert_group`, `delete_group`, `upsert_sheet_type`, `delete_sheet_type`, `upsert_check`, `delete_check`, `set_check_defaults`, `_rule_meta`, `upsert_rule`, `delete_rule`, `upsert_content`, `delete_content`, `set_layout`, `set_theme` |

### `absorb/`

| File | Contents |
|---|---|
| `prompt.py` | `build_prompt` |
| `parse.py` | `_int05`, `_truthy`, `_confidence`, `extract_object`, `parse_output` |
| `materializer.py` | `materialize`, `_char_name`, `_actor_exists`, `_entity_kind`, `_DossierTargetGone`, `_new_character_provenance`, `_new_character_dossier`, `_CARD_FIELDS` |
| `weather.py` | `_weather_edits`, `_apply_weather` |
| `apply.py` | `apply_edits`, `_BROWSABLE_KINDS` |
| `snapshots.py` | `relationships_snapshot`, `plot_snapshot`, `group_snapshot`, `state_snapshot`, `_snapshot_line` |

### `context/`

| File | Contents |
|---|---|
| `macros.py` | `_substitute`, `scene_substitutions`, `_datetime_subs`, `_expand_random`, `_expand_rolls`, `_strip_unknown_macros`, `expand_macros`, `_LITERAL_MACROS`, `_MACRO_TOKEN`, `_RANDOM_MACRO`, `_ROLL_MACRO` |
| `cast.py` | `_char_name`, `_cast_directory_data`, `_drift_roster`, `cast_datetime_facts`, `_campaign_player_refs` |
| `world_state.py` | `activate`, `_world_info`, `_today_data`, `_weather_data`, `_character_states`, `_group_states` |
| `mechanics.py` | `_sheet_type_label`, `_sheet_summary_lines`, `_rule_keys_match`, `_mechanics` |
| `story.py` | `_relationship_lines`, `_story_entries`, `_project_history` |
| `assemble.py` | `_assemble`, `_system_text`, `build_messages`, `build_director_messages`, `build_opener_messages`, `context_sections`, `OPENER_RECAP_DEPTH`, `_SECTIONS` |
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

## Import form inside the store (load-bearing)

An acyclic *file* graph is not sufficient. Package `__init__.py` re-exports can
reintroduce the same partial-initialization hazard at package granularity, which
would merely relocate today's load-bearing order from `store/__init__.py` into
each new package's `__init__.py`.

Demonstrated on a prototype of the structure above: with `worlds/lifecycle.py`
written as `from ..campaigns import world_refs`, reordering the three lines of
`campaigns/__init__.py` — changing no other code, leaving the file graph fully
acyclic — produces

```
ImportError: cannot import name 'world_refs' from partially initialized
module 'st.campaigns' (most likely due to a circular import)
```

**Rule: a cross-package import inside the store names the submodule and keeps
it as a module object.**

```python
from ..campaigns import read        # yes — binds a submodule
...
read.world_refs()

from ..campaigns import world_refs  # no — reads a name off a package __init__
from .. import campaigns            # legal — a module object, not a name
```

The precise hazard is *binding a non-module name off a package that may still
be initializing*. `from .. import campaigns` is safe on its own, because the
module object is bound without reading through it; reading an attribute off it
at module scope would close a real cycle and is caught by the cycle rule
instead. Prototyping confirmed both halves of this.

Verified on the prototype under the hostile `__init__` ordering: this form
imports cleanly regardless of order, because binding a submodule does not
require the parent `__init__` to have finished. It also keeps the call site
patchable (see below).

The guard test must check this form directly. A file-level cycle check alone
passes the broken version, since the file graph is acyclic either way.

Given that rule, `store/__init__.py`'s import order stops being load-bearing —
and the guard test is what demonstrates it, rather than a claim in a docstring.

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

## Monkeypatch targets that move

Moving a function to a new submodule changes where tests must patch it. This
is the footgun already documented at `routes/streaming.py:26-31`, and it is
the one way a "pure move" can change behavior — silently, by making a patch
stop intercepting so a test passes for the wrong reason.

Fourteen sites patch a name on a module this refactor splits:

| Test site | Patched | New home |
|---|---|---|
| `test_locks_store.py:865` | `scenes.append_message` | `scenes/write.py` |
| `test_locks_store.py:911`, `:912` | `chronicle.absorb`, `chronicle.append_timeline` | `chronicle` (unsplit; verify) |
| `test_scene_store.py:996` | `scenes.parse_frontmatter` | imported into several `scenes/` files |
| `test_sheets_store.py:799`, `:1134` | `modules.resolve` | `modules/binding.py` |
| `test_audit_store.py:100` | `modules.load_pack` | `modules/pack.py` |
| `test_response_presets.py:601` | `campaigns.read_campaign` | `campaigns/read.py` |
| `test_context.py:1157` | `context._drift_roster` | `context/cast.py` |
| `test_module_edit.py:627`, `:631`, `:914` | `module_edit._run_migration`, `_campaign_locks` | `module_edit/migrate.py` |
| `test_routes.py:2853` | `audit.materialize` | `audit/apply.py` |
| `test_module_display.py:361` | `module_display._load_theme` | `modules/display.py` |

`scenes.parse_frontmatter` is the nastiest: it patches a name `scenes.py`
imported from elsewhere, so after the split each `scenes/` submodule holds its
own binding and patching one has no effect on the others.

The module-object import form required above is what keeps these patchable at
all — a by-value `from ..modules.binding import resolve` in `sheets` would make
`modules.binding.resolve` unpatchable from the caller's perspective.

**Retargeting is decided by the caller, not by the function's new home.** Two
of the sites above must stay exactly where they are:

- `test_locks_store.py:865` patches `scenes.append_message`, and the test
  drives an HTTP route — `routes/mechanics.py:40`,`:202` call
  `store.scenes.append_message(...)`, a call-time lookup on the package.
- `test_routes.py:2853` patches `audit.materialize`, and
  `routes/scenes.py:299` calls `store.audit.materialize(...)` the same way.

A package `__init__` re-export is a by-value binding, so patching
`scenes.write.append_message` or `audit.apply.materialize` would not intercept
either route call — the lock-depth and audit-crash tests would pass while
injecting nothing. Retarget only where the *caller* holds a submodule
reference, in the commit that rewrites that caller's import, and re-run to
confirm the patch still bites. The implementation plan carries the per-site
table.

## Enforcement

A new `backend/tests/test_import_guard.py`, written in the AST-parsing style
of `test_atomic_guard.py` and `test_overlay_guard.py`:

1. Fails on any grimoire-internal import inside a function body.
2. Fails on any third-party or stdlib import inside a function body.
3. Builds the module-level import graph over `backend/src/grimoire/` and fails
   on any cycle, naming the offending path.
4. Fails on any intra-store relative import that binds a **non-module name**
   off a package: `from ..campaigns import world_refs` fails, while
   `from ..campaigns import read` and `from .. import campaigns` pass.
   `__init__.py` files are exempt — re-exporting is their job, and they
   import their own submodules first. Rule 3 alone does not catch this, since
   the file graph is acyclic either way.

The guard's scan scope is `backend/src/grimoire/` only. Tests, `scripts/` and
`evals/` are not covered — a test that imports inside a function to exercise
import behavior is legitimate.
5. Exemptions are function-scoped and must state a reason; a marker inside a
   string literal does not count, matching `test_overlay_guard.py:462`.

The two existing guard tests need no changes: both discover files with
`PACKAGE.rglob("*.py")` (`test_atomic_guard.py:135`, `test_overlay_guard.py:422`),
so new submodules are covered automatically, and their exemptions are
function-scoped and therefore travel with the code that moves.

Resolution must be per-submodule: `from . import scenes` inside the store is
an edge to `grimoire.store.scenes`, not to the package. Attributing it to the
package is exactly the mistake that produced the phantom 63-file SCC.

## Sequencing

Ordered so the graph is strictly less cyclic after every step, and each step
is independently testable.

0. Repair four broken `stat` stubs. Three in `backend/tests/test_atomic.py`
   (lines 150, 379, 402) accept one positional argument, but Python 3.11.15's
   `pathlib` calls `os.stat(path, follow_symlinks=...)`, so a `tmp_path`
   cleanup during teardown crashes pytest with an INTERNALERROR instead of
   reporting results. The fourth, in
   `test_assets_store.py::test_lookup_survives_a_sibling_vanishing_mid_scan`,
   raises for every path except `avatar.png` — including the directory
   `image_path` stats first — so the scan it means to exercise never runs.
   Both are prerequisites for a trustworthy baseline.
1. Land the guard test with a **ratchet baseline** rather than an xfail:
   `backend/tests/import_guard_baseline.txt` lists the violations that exist
   at that moment, one per line. A violation absent from the baseline fails,
   and a baseline line that no longer occurs *also* fails, so the file cannot
   decay into a standing exemption list. Every later task deletes its own
   lines, which makes progress mechanically checked rather than asserted.

   Measured starting baseline: **58 lines** — 57 deferred imports and the one
   `audit → scene_refs → scenes` cycle. (57, not 53, because the four
   third-party lazy imports are violations under rule 2 as well.)
2. Per-kind splits, one kind per commit, in dependency order: `campaigns`,
   `worlds`, `modules`, `appearances`, `sheets`, `audit`, `scenes`.

   No interim scaffolding is needed. While one side of a pair is still a flat
   module, the other side's deferred import simply stays in place and stays on
   the baseline; the task that splits the second side removes both. Each task
   ends with a green suite.
3. The three remaining pure-size splits: `module_edit`, `absorb`, `context`.
   (`sheets` is a size split too, but it carries cycle edges, so it lands in
   step 2 with the other record kinds.)
4. Hoist the deferred imports left in the modules that were never split —
   `chronicle`, `proposals`, `response_presets`, `export`, `entity_schema`,
   `suggest`, `weather`, `checks`, `calendars.base`, `plot`, `relationships`,
   `epub`. That is **26 of the 58 baseline lines**, so skipping it leaves the
   baseline non-empty and step 6 cannot pass. These are not verbatim moves:
   after step 2 the names are packages, so each import targets a submodule and
   its call sites change with it.
5. Hoist the four third-party imports.
6. Delete the now-empty baseline file and the code path that reads it.

## Verification

- `backend/.venv/bin/python -m pytest backend -q` after every step.
  Baseline on this container, after the step-0 test repairs: **2713 passed,
  1 failed**. The single failure,
  `test_atomic.py::test_a_read_only_record_is_not_silently_replaced`, is an
  artifact of running as uid 0, where chmod 0444 does not stop a write. On a
  normal user account the suite is green.

  `test_assets_store.py::test_lookup_survives_a_sibling_vanishing_mid_scan`
  was initially misfiled here as a second root artifact. It is not: its
  `Path.stat` stub raises for every path but `avatar.png`, including the
  directory `image_path` checks first, so the function returns `None` before
  reaching the scan under test — on any uid. Step 0 repairs it.
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
- Behavior changes of any kind. Every step is a move plus an import rewrite,
  plus retargeting the monkeypatch sites listed above; a diff that changes
  logic is a mistake in this refactor.
- Replacing lifecycle cascades with events or a registry. The seed steps in
  `create_campaign` are legible as a sequence and should stay that way.
