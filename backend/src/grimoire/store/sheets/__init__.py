"""Sheet instances for sheetable entities (#161, mechanics Phase 3).

Campaign sheets live at ``<campaign>/sheets/<kind>--<id>.json``; world
starting sheets at ``<world>/sheets/<mid>/<kind>--<id>.json``. File shape:
``{"sheet_type": ..., "fields": {...}}``. Derived values are computed on
read, never stored. Sheets are campaign-owned mutable state: copied at
create (``seed``), never overlay-read. ``read``/``coverage`` never raise on
malformed sheet content; writes validate strictly and raise ``SheetError``.
Spec: docs/superpowers/specs/2026-07-12-mechanics-phase3-sheets-design.md.
"""

from __future__ import annotations

# Submodules first, then names, and the submodules in dependency order
# (`paths` -> `schema` -> ... -> `advancement`): every file imports only files
# named before it, so nothing here observes a half-initialized sibling.
#
# The file names deliberately differ from the public function names --
# `reader`/`writer`/`tally`/`advancement`, not `read`/`write`/`coverage`/
# `advance`. Same-named files would be overwritten by the `from .reader import
# read` line below, and a later `from ..sheets import read` would silently bind
# the *function*: an AttributeError at call time, past both the import and the
# cycle guard. test_import_guard.py's
# `test_no_submodule_is_shadowed_by_a_facade_export` enforces this.
from . import (paths, schema, pools, reader, writer, creation, tally,  # noqa: F401
               advancement)
from .paths import (  # noqa: F401
    FILE_KINDS, SheetConflict, SheetError, _atomic_write_json, _campaign_dir,
    _campaign_path, _next_gen, _world_dir, _world_path, sheet_kind,
)
from .schema import (  # noqa: F401
    _MUTABLE_TYPES, _compute_derived, _int_or, _numeric_scope,
    _validate_instance, canonical_field_value, default_fields,
    expression_scope, instance_errors,
)
from .pools import _pool_budget, _pool_floor, _pool_group_fields  # noqa: F401
from .reader import (  # noqa: F401
    _read_path, list_refs, read, read_world, world_list_refs,
    world_sheet_modules,
)
from .writer import (  # noqa: F401
    _check_expected, _checked_write, _set_field_locked, _stored_snapshot,
    _validate_write_target, delete, set_field, write, write_world,
)
from .creation import (  # noqa: F401
    _assert_campaign_entity_exists, _assert_world_entity_exists,
    _checked_creation_write, delete_world, write_creation, write_world_creation,
)
from .tally import _tally, _type_kinds, coverage, seed, world_coverage  # noqa: F401
from .advancement import _advancement_cost, advance  # noqa: F401
