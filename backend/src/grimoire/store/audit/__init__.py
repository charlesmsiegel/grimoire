"""Narrated-event validation (mechanics Phase 5, roadmap #826).

Part 1: scene-start sheet baselines at <campaign>/sheet_baselines.json --
{"<sid>": {"module", "schema": {"hash","mtime"}, "sheets": {"kind--eid":
{"sheet_type","gen","fields"}}}}. Validity = module id + schema stamp +
per-sheet gen + type; no cross-store invalidation hooks (gen self-invalidates).
Lock ordering: campaign lock (locks.campaign_lock) -> baseline lock,
never reversed.
Spec: docs/superpowers/specs/2026-07-12-mechanics-phase5-absorb-validation-design.md
"""

from __future__ import annotations

# Submodules before names, and `baselines` before `prompt`: `prompt` imports
# `scenes/read.py`, and initializing that package runs `scenes/lifecycle.py`,
# which imports `audit.baselines` -- both directly and through `scene_refs`.
# The order is a reading aid, not a requirement: that re-entry binds a
# submodule, which imports on demand even with this package half-initialized.
# What does matter is that `baselines` itself imports no scene state, which is
# the whole point of the cut (it is what removed the audit/scene_refs/scenes
# cycle). `apply` reads both siblings.
from . import apply, baselines, prompt  # noqa: F401
from .apply import AuditParseError, apply_delta, materialize, parse_output  # noqa: F401
from .baselines import (  # noqa: F401
    _LOCKS,
    _LOCKS_GUARD,
    _lock,
    _path,
    _write,
    baseline_entry_valid,
    baseline_field,
    capture_baseline,
    clear_baselines,
    read_baselines,
    repoint_scenes,
    schema_stamp,
)
from .prompt import (  # noqa: F401
    _field_label,
    build_prompt,
    render_value,
    roll_lines,
    sheet_blocks,
    sheet_scope,
)
