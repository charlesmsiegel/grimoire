"""Turning the parsed sections into StagedEdits — the diff the reviewer sees.

The file is named for the role, not the function, because `materialize` is a
public function this package re-exports: a submodule spelled the same way
would be overwritten by that export, and a later `from ..absorb import
materialize` would bind the function rather than the module.
"""

from __future__ import annotations

# Compatibility facade. State and implementation live in responsibility leaves;
# internal fault injection targets those owners, while existing imports remain.
from collections.abc import Callable
from typing import NamedTuple

from ... import commitments, entities, errors, facts, fieldtext, overlay, plot
from ...campaigns import paths as campaigns_paths
from ...continuity import effective as continuity_effective
from ...continuity import retarget
from ...paths import slugify
from .. import conflicts, membership, parse, routing, staging, weather
from ._continuity_rows import _accepted_existing as _accepted_existing
from ._continuity_rows import _as_new_alternative as _as_new_alternative
from ._continuity_rows import _attach_alternatives as _attach_alternatives
from ._continuity_rows import _candidate_alternatives as _candidate_alternatives
from ._continuity_rows import _commitment_edit as _commitment_edit
from ._continuity_rows import _continuity_edits as _continuity_edits
from ._continuity_rows import _continuity_records as _continuity_records
from ._continuity_rows import _current_candidates as _current_candidates
from ._continuity_rows import _fresh_id as _fresh_id
from ._continuity_rows import _latest_beat as _latest_beat
from ._continuity_rows import _merged_head as _merged_head
from ._continuity_rows import _merged_tail as _merged_tail
from ._continuity_rows import _moved_elsewhere as _moved_elsewhere
from ._continuity_rows import _plot_edit as _plot_edit
from ._continuity_rows import _recheck_accepted as _recheck_accepted
from ._continuity_rows import _recheck_section as _recheck_section
from ._continuity_rows import _siblings as _siblings
from ._contracts import _CARD_FIELDS as _CARD_FIELDS
from ._contracts import _IDENTITY_SECTIONS as _IDENTITY_SECTIONS
from ._contracts import _LOW_DECISIONS as _LOW_DECISIONS
from ._contracts import _NEW_RECORD_NOUNS as _NEW_RECORD_NOUNS
from ._contracts import APPEND_KINDS as APPEND_KINDS
from ._contracts import AS_NEW_KEY as AS_NEW_KEY
from ._contracts import CLOSED_SINCE as CLOSED_SINCE
from ._contracts import MOVED_SINCE as MOVED_SINCE
from ._contracts import _actor_exists as _actor_exists
from ._contracts import _char_name as _char_name
from ._contracts import _entity_target as _entity_target
from ._edits import _fact_edits as _fact_edits
from ._edits import _fact_label as _fact_label
from ._edits import _fact_predecessor as _fact_predecessor
from ._edits import _new_character_dossier as _new_character_dossier
from ._edits import _new_character_provenance as _new_character_provenance
from ._edits import _recorded_here as _recorded_here
from ._edits import materialize as materialize
from ._edits import record_taken as record_taken
from ._record_ids import Assigned as Assigned
from ._record_ids import _aliases as _aliases
from ._record_ids import _assign_section as _assign_section
from ._record_ids import _commitment_settled as _commitment_settled
from ._record_ids import _live_canon as _live_canon
from ._record_ids import _merge_honoured as _merge_honoured
from ._record_ids import _new_commitment_id as _new_commitment_id
from ._record_ids import _new_record_id as _new_record_id
from ._record_ids import _new_thread_id as _new_thread_id
from ._record_ids import _redirect as _redirect
from ._record_ids import _thread_settled as _thread_settled
from ._record_ids import assign_ids as assign_ids

# Original star-import surface, captured before extraction.
__all__ = [
    'APPEND_KINDS',
    'AS_NEW_KEY',
    'CLOSED_SINCE',
    'MOVED_SINCE',
    'Assigned',
    'Callable',
    'NamedTuple',
    'annotations',
    'assign_ids',
    'campaigns_paths',
    'commitments',
    'conflicts',
    'continuity_effective',
    'entities',
    'errors',
    'facts',
    'fieldtext',
    'materialize',
    'membership',
    'overlay',
    'parse',
    'plot',
    'record_taken',
    'retarget',
    'routing',
    'slugify',
    'staging',
    'weather',
]
