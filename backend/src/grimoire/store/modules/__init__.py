"""Mechanics module packs (#160): loading, validation, registry, binding.

A module is a declarative data pack -- JSON + markdown, no code plugins
(deliberately unlike calendars' Python-plugin model: sharing a module never
runs untrusted code). Built-ins ship in ``builtin_modules/`` inside this
package; user modules live in ``<GRIMOIRE_HOME>/modules/``.
Spec: docs/superpowers/specs/2026-07-12-mechanics-phase1-modules-design.md.
"""

from __future__ import annotations

# Submodules before names, and every leaf before `binding`: `binding` is the
# only part that reaches sideways, importing `campaigns`/`worlds`, whose
# package initializers pull in `sheets`, `appearances` and `scenes` -- each of
# which does `from . import modules` and so binds this package while it is
# still initializing. They read `modules.<name>` at call time only, which is
# safe precisely because the leaves are already bound by the time `binding`'s
# import runs.
from . import fields, validate, display, pack, content, binding, admin  # noqa: F401
from .fields import (  # noqa: F401
    CONTENT_KINDS, _pool_group_fields, assembled_fields, numeric_names,
)
from .validate import (  # noqa: F401
    FIELD_TYPES, RESERVED_NAMES, ROLL_SCOPE_NAMES, SHEET_KINDS, _PLACEHOLDER,
    _RAISABLE_TYPES, _as_dict, _as_list, _validate_advancement, _validate_checks,
    _validate_creation, _validate_derived, _validate_field, _validate_manifest,
    _validate_outcomes, _validate_sheets, validate_sheet_values,
)
from .pack import (  # noqa: F401
    DEFAULT_BUILTIN_DIR, _MID_RE, ContentNotFound, ModuleError, ModuleNotFound,
    _load_content, _load_rules, _safe_mid, _scan, _split_csv, builtin_dir,
    list_modules, load_pack, load_pack_at, pack_root, user_dir,
)
from .content import read_content, read_rule  # noqa: F401
from .binding import (  # noqa: F401
    _write_key, resolve, set_campaign_module, set_world_module,
)
from .admin import create_module, delete_module  # noqa: F401
