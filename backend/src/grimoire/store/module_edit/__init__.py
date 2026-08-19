"""Module authoring (#829, mechanics Phase 8): staged, validated, journaled
whole-directory publication of user-library pack edits.

Concurrency threat model: the original spec assumed exactly two actors — the
User (UI) and the LLM (play flows) — both inside one process. A synced store
adds a third, a second grimoire process, which that design did not account for
(#234). One global re-entrant module-edit lock serializes all module mutation
+ recovery; every publishing writer also holds every campaign's
locks.campaign_lock(cid) across its swap, so LLM flows (which hold their
single campaign lock across resolve/load/compute) never observe a
half-published pack. Both locks are now OS file locks as well as in-process
ones, so all three actors are excluded **on one machine, under one OS user**;
two *devices* sharing a synced folder are still not, and cannot be by any
filesystem lock, and neither are two OS accounts sharing one store.
No machinery for two User actions racing.
Specs: docs/superpowers/specs/2026-07-13-mechanics-phase8-authoring-ui-design.md,
docs/superpowers/specs/2026-07-28-cross-process-campaign-locks-design.md.
"""

from __future__ import annotations

# Submodules first, then names. The submodule line is listed in dependency
# order (`packfile`/`scope` -> `staging` -> `layout` -> `migrate` -> `packs` ->
# `renaming` -> `edits`): each file imports only files named before it. Python
# would resolve any other order too -- a submodule already in `sys.modules` is
# bound whatever this line says -- so the order is a deliberate reading aid,
# not a requirement: it states the package's internal layering in one line.
#
# `packfile` and `scope` are shared leaves rather than a home for behaviour.
# Without them `edits`, `layout`, `migrate` and `renaming` are one cycle:
# `edits` reads `_field_keys`/`_group_scope`, `layout` reads `_fragment_users`,
# and `migrate._apply` catches `_RenameCollision`.
from . import edits, layout, migrate, packfile, packs, renaming, scope, staging  # noqa: F401
from .edits import (  # noqa: F401
               _rule_meta,
               delete_check,
               delete_content,
               delete_group,
               delete_rule,
               delete_sheet_type,
               set_check_defaults,
               set_layout,
               set_manifest,
               set_theme,
               upsert_check,
               upsert_content,
               upsert_group,
               upsert_rule,
               upsert_sheet_type,
)
from .layout import (  # noqa: F401
               _edit_tree,
               _layout_name_edit,
               _prune_layout,
               _prune_node,
               _specialize_layout,
)
from .migrate import (  # noqa: F401
               _apply,
               _campaign_locks,
               _content_ids,
               _file_kind,
               _impact,
               _iter_ref_values,
               _migrate_file,
               _migrate_preview,
               _replay_journal,
               _require_user_root,
               _result,
               _run_migration,
               _sample,
               _sheet_files,
               _sidecar_stats_at,
               _would_migrate,
               recover,
)
from .packfile import _read_json, _read_sheets, _write_json  # noqa: F401
from .packs import (  # noqa: F401
               MAX_MEMBERS,
               MAX_UNCOMPRESSED,
               _check_archive,
               _member_parts,
               create_module,
               delete_module,
               duplicate_module,
               export_module,
               import_module,
)
from .renaming import (  # noqa: F401
               _RENAME_KINDS,
               _SAFE_KEY,
               _composing_tids,
               _rename_map_key,
               _rewrite_expr,
               _rewrite_exprs,
               _rewrite_placeholders,
               check_proposal_guard,
               rename,
)
from .scope import (  # noqa: F401
               _field_keys,
               _fragment_users,
               _group_scope,
               _RenameCollision,
)
from .staging import _M, _publish, _staging_root, locked, new_mid  # noqa: F401
