"""The push/sync engine: per-campaign incoming changes + accept/reject, and
the three explicit moves that run the other way.

Compares three content hashes per ref (kind, id):
  world = world entity's current hash (or None)
  base  = campaign sync.md[ref]        (or None)
  mine  = campaign entity's current hash (or None)
An incoming change exists iff world is not None and world != base.

`incoming`/`accept`/`reject` only ever advance the campaign. `promote`, `push`
and `demote` (#52, #53, #60) move a record the other way, and each of them is
something a user asked for by name -- never automatic, because all three relax
the rule that library drift becomes a campaign-local override and never a
library edit (#113).

Design: docs/superpowers/specs/2026-08-21-library-promote-demote-design.md
"""

from __future__ import annotations

import json
import logging
import shutil
from contextlib import contextmanager, suppress
from pathlib import Path

from .. import (
    actor_names,
    assets,
    atomic,
    characters,
    entities,
    entity_schema,
    greetings,
    group_refs,
    image_refs,
    journal,
    locks,
    overlay,
    pcs,
    revision,
)
from ..appearances import paths as appearances_paths
from ..appearances import versions as appearances_versions
from ..campaigns import lifecycle as campaigns_lifecycle
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from ..frontmatter import parse_frontmatter
from ..paths import safe_id
from ..worlds import lifecycle as worlds_lifecycle
from ..worlds import paths as worlds_paths
from ._composition import _composition_row as _composition_row
from ._composition import _composition_state as _composition_state
from ._composition import _locked_rows as _locked_rows
from ._composition import _manifest_row as _manifest_row
from ._composition import _manifest_rows as _manifest_rows
from ._composition import composition as composition
from ._composition import is_composition_ref as is_composition_ref
from ._demote import _demote_scope as _demote_scope
from ._demote import _prepare_demote as _prepare_demote
from ._demote import _require_world_record as _require_world_record
from ._demote import demote as demote
from ._demote import dependents as dependents
from ._display_refs import _incoming_identity as _incoming_identity
from ._display_refs import _incoming_names as _incoming_names
from ._display_refs import _incoming_native_names as _incoming_native_names
from ._display_refs import _incoming_ref_rows as _incoming_ref_rows
from ._display_refs import _show_incoming_fields as _show_incoming_fields
from ._incoming import _actor_incoming as _actor_incoming
from ._incoming import _plotmap_incoming as _plotmap_incoming
from ._incoming import _unpicked_incoming as _unpicked_incoming
from ._incoming import campaigns_for_world as campaigns_for_world
from ._incoming import incoming as incoming
from ._move_policy import DanglingReferenceError as DanglingReferenceError
from ._move_policy import LibraryMoveError as LibraryMoveError
from ._move_policy import NotDemotableError as NotDemotableError
from ._move_policy import NotDivergedError as NotDivergedError
from ._move_policy import NotInLibraryError as NotInLibraryError
from ._move_policy import NotPushableError as NotPushableError
from ._move_policy import PromoteConflictError as PromoteConflictError
from ._move_policy import PushConflictError as PushConflictError
from ._move_policy import UnknownTargetError as UnknownTargetError
from ._move_policy import _actor_meta_name as _actor_meta_name
from ._move_policy import _actor_missing as _actor_missing
from ._move_policy import _flat_kind as _flat_kind
from ._move_policy import _publishes_membership as _publishes_membership
from ._move_policy import _require_world as _require_world
from ._move_policy import _require_world_actor as _require_world_actor
from ._move_policy import _require_world_character as _require_world_character
from ._move_policy import _require_world_members as _require_world_members
from ._move_policy import _safe_ref as _safe_ref
from ._move_policy import _world_file as _world_file
from ._move_policy import _world_holds_actor as _world_holds_actor
from ._move_policy import _world_holds_flat as _world_holds_flat
from ._pins import _pins_path as _pins_path
from ._pins import pinned_refs as pinned_refs
from ._pins import set_pin as set_pin
from ._promote import _promote_actor as _promote_actor
from ._promote import _promote_flat as _promote_flat
from ._promote import promote as promote
from ._promote import promoted_intact as promoted_intact
from ._publication import ACTOR_WORLD_SIDECARS as ACTOR_WORLD_SIDECARS
from ._publication import _advance_appearance_base as _advance_appearance_base
from ._publication import _copy_extras as _copy_extras
from ._publication import _copy_sidecars as _copy_sidecars
from ._publication import _copy_tree as _copy_tree
from ._publication import _put_base as _put_base
from ._publication import _reattaching_on_failure as _reattaching_on_failure
from ._publication import _touch_world as _touch_world
from ._publication import log as log
from ._pull import _advance as _advance
from ._pull import _advance_actor as _advance_actor
from ._pull import accept as accept
from ._pull import reject as reject
from ._push import _push_locked as _push_locked
from ._push import diverged as diverged
from ._push import library_status as library_status
from ._push import push as push
from ._read_shapes import _actor_blob as _actor_blob
from ._read_shapes import _actor_name_or_id as _actor_name_or_id
from ._read_shapes import _actor_summary_blob as _actor_summary_blob
from ._read_shapes import _align_entity_fields as _align_entity_fields
from ._read_shapes import _blob_name as _blob_name
from ._read_shapes import _dir_hash as _dir_hash
from ._read_shapes import _entity_blob as _entity_blob
from ._read_shapes import _plotmap_blob as _plotmap_blob

# Explicit compatibility aliases retain the original binding and star surface.
from ._read_shapes import _ref_str as _ref_str

# Preserve the original deferred module annotations and their resolved types.
__annotations__ = {'ACTOR_WORLD_SIDECARS': 'tuple[str, ...]'}

# Captured from the original module before responsibility leaves existed.
__all__ = [
    'ACTOR_WORLD_SIDECARS',
    'DanglingReferenceError',
    'LibraryMoveError',
    'NotDemotableError',
    'NotDivergedError',
    'NotInLibraryError',
    'NotPushableError',
    'Path',
    'PromoteConflictError',
    'PushConflictError',
    'UnknownTargetError',
    'accept',
    'actor_names',
    'annotations',
    'appearances_paths',
    'appearances_versions',
    'assets',
    'atomic',
    'campaigns_for_world',
    'campaigns_lifecycle',
    'campaigns_paths',
    'campaigns_read',
    'characters',
    'composition',
    'contextmanager',
    'demote',
    'dependents',
    'diverged',
    'entities',
    'entity_schema',
    'greetings',
    'group_refs',
    'image_refs',
    'incoming',
    'is_composition_ref',
    'journal',
    'json',
    'library_status',
    'locks',
    'log',
    'logging',
    'overlay',
    'parse_frontmatter',
    'pcs',
    'pinned_refs',
    'promote',
    'promoted_intact',
    'push',
    'reject',
    'revision',
    'safe_id',
    'set_pin',
    'shutil',
    'suppress',
    'worlds_lifecycle',
    'worlds_paths',
]
