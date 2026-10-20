"""Migrating legacy images into the image store (stage 4, spec §11).

A maintenance operation, never a startup step. `run(root, dry_run=...,
cancel=...)` is the whole of it and always returns its report; the plan below
is the half a dry run and a real run share (M7): `plan(root, cancel=...)` walks the
inventory (`image_surfaces.occurrences`), hashes every legacy file the way
ingest would, groups the files by picture and previews what folding their
metadata would do; `report(plan)` is the §11 report. The dry run is exactly
`report(plan(...))`, and it writes nothing at all: the caller persists the
report (M2).

**Pinned root (M4).** Every path is built from the `root` the caller captured
-- the placements read in each directory under it, the objects and blobs
through `image_store`'s `root=` keyword -- never from `paths.home()` asked
again, so a data-dir move mid-plan cannot splice two trees into one plan.

**What is planned.** One file per logical name per directory (the one
`assets.legacy_path` serves today); everything else is left untouched and
reported with its path and the reason:

- the inventory's own refusals (`Occurrence.untouched`): other siblings of a
  name, an extension nothing accepts, a name nothing can store;
- ``case-alias``: names in one directory that differ only by case, across
  legacy files and existing placements alike. Placing either would leave two
  placements that alias as soon as the tree syncs to a case-insensitive
  device, so neither is placed (M7, which overrides §11.B's "first wins");
- ``unreadable``, and the identities that are not pictures: bytes that do not
  sniff (``unsniffable``), that sniff and do not parse (``unsanitizable``), or
  that do not decode (``undecodable``);
- ``legacy-differs`` / ``placement-not-available``: a legacy file beside an
  image-bearing placement of its name that is another picture (or whose
  picture is not under `root` to compare). The placement is never overwritten
  (M8), so neither is touched.

**Grouping.** Files are hashed after sanitizing (so a card avatar and the same
pixels without the card's JSON are one stream) and decoded once per distinct
stream. A group is one image id. A NEW object keeps the stream most
placements use, then the smaller, then the lowest hash (`choose_retained`);
an object already under `root` keeps the blob it has. `items` is ordered so
each group's retained stream comes first: ingest keeps the first arrival.

**Metadata preview (§11.D, M9).** A group's description is previewed from the
object's own text, its existing conflicts, and every legacy key that would
fold into it -- its files' keys and the metadata-only occurrences (M6 a and
c) whose target places it. Several distinct texts are a conflict, never a
choice; past `MAX_CONFLICTS` the cap is reported. A key that is not a string,
or is over `image_store.MAX_DESCRIPTION`, stays where it is and is reported.
Subjects are unioned per scope and a disagreement is reported. These are
previews: a real run re-reads every value at fold time (M9).

**The real run (M8-M10)** -- see the section that starts at `_RootChangedError`:
per legacy file, re-read and re-hash, ingest outside every lock, then under
the campaign lock (a campaign occurrence), `image_collection_lock` (a world
library) and the name lock: never overwrite an image-bearing placement, write
one when there is none, verify it under the pinned root, drop its legacy
thumbnails, unlink exactly the file that was hashed, fold its keys. Then the
metadata-only occurrences, format-1 manifests and harvest journals. Every
campaign written is revision-bumped under its lock.

**Lock domain.** Every campaign write here happens under
`locks.campaign_lock(cid)` (`_campaign_held`), but no public function takes a
`cid` -- `run` takes a store root -- so `test_lock_domain_guard` does not
survey this module, and listing it in `DOMAIN_MODULES` would be the phantom
entry that guard refuses.
"""

from __future__ import annotations

# Compatibility facade. State and implementation live in responsibility leaves;
# internal fault injection targets those owners, while existing imports remain.
import errno
import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Iterable
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from .. import (
    assets,
    atomic,
    greeting_images,
    image_collection_imports,
    image_collections,
    image_hash,
    image_refs,
    image_scopes,
    image_store,
    image_subjects,
    image_surfaces,
    locks,
    paths,
    revision,
    thumbs,
)
from ._collection_conversion import _carry_member_key as _carry_member_key
from ._collection_conversion import _collections as _collections
from ._collection_conversion import _convert as _convert
from ._collection_conversion import _converted_members as _converted_members
from ._collection_conversion import _format2_manifests as _format2_manifests
from ._collection_conversion import _greeting_dirs as _greeting_dirs
from ._collection_conversion import _journal as _journal
from ._collection_conversion import _journals as _journals
from ._collection_conversion import _Member as _Member
from ._collection_conversion import _member_keys as _member_keys
from ._collection_conversion import _rekey as _rekey
from ._collection_conversion import _rekey_one as _rekey_one
from ._collection_conversion import _settle_converted as _settle_converted
from ._collection_conversion import _url_keys_kept as _url_keys_kept
from ._collection_conversion import _world_roots as _world_roots
from ._contracts import _FOLD_TRIES as _FOLD_TRIES
from ._contracts import _KIND as _KIND
from ._contracts import _MEMBER_NAME as _MEMBER_NAME
from ._contracts import _NOT_PICTURES as _NOT_PICTURES
from ._contracts import _OBJECT as _OBJECT
from ._contracts import _RELATION as _RELATION
from ._contracts import _SUBJECTS as _SUBJECTS
from ._contracts import CANCELLED as CANCELLED
from ._contracts import DONE as DONE
from ._contracts import FAILED as FAILED
from ._contracts import KIND as KIND
from ._contracts import MAX_CONFLICTS as MAX_CONFLICTS
from ._contracts import ROOT_CHANGED as ROOT_CHANGED
from ._contracts import Group as Group
from ._contracts import Item as Item
from ._contracts import MigrationPlan as MigrationPlan
from ._contracts import Occurrence as Occurrence
from ._contracts import _RootChangedError as _RootChangedError
from ._contracts import choose_retained as choose_retained
from ._execution_state import _attempt as _attempt
from ._execution_state import _campaign_held as _campaign_held
from ._execution_state import _campaign_of as _campaign_of
from ._execution_state import _campaign_write as _campaign_write
from ._execution_state import _collection_held as _collection_held
from ._execution_state import _error_text as _error_text
from ._execution_state import _Exec as _Exec
from ._execution_state import _linked_slot as _linked_slot
from ._execution_state import _load_map as _load_map
from ._execution_state import _lstat as _lstat
from ._execution_state import _pend as _pend
from ._execution_state import _safe_map as _safe_map
from ._execution_state import _same_file as _same_file
from ._execution_state import _same_root as _same_root
from ._execution_state import _save_map as _save_map
from ._execution_state import _snap as _snap
from ._execution_state import _stopped as _stopped
from ._execution_state import _unpend as _unpend
from ._execution_state import pending_ids as pending_ids
from ._execution_state import work_map_path as work_map_path
from ._folding import _delete_key_if as _delete_key_if
from ._folding import _fold_description as _fold_description
from ._folding import _fold_keys as _fold_keys
from ._folding import _fold_subjects as _fold_subjects
from ._folding import _fold_subjects_once as _fold_subjects_once
from ._folding import _fold_text_once as _fold_text_once
from ._folding import _ours as _ours
from ._folding import _restore_key as _restore_key
from ._folding import _says as _says
from ._folding import _settled_delete as _settled_delete
from ._folding import _sidecar_now as _sidecar_now
from ._folding import _still_target as _still_target
from ._folding import _subjects_folded_here as _subjects_folded_here
from ._folding import _tags as _tags
from ._folding import _unfoldable as _unfoldable
from ._folding import _with_subjects as _with_subjects
from ._folding import _with_text as _with_text
from ._placements import _count_added as _count_added
from ._placements import _drop_focus_file as _drop_focus_file
from ._placements import _drop_thumbs as _drop_thumbs
from ._placements import _dropped_focus as _dropped_focus
from ._placements import _focus_file as _focus_file
from ._placements import _hashes_to as _hashes_to
from ._placements import _item as _item
from ._placements import _metadata_one as _metadata_one
from ._placements import _move_focus as _move_focus
from ._placements import _object_under as _object_under
from ._placements import _override as _override
from ._placements import _place_and_clean as _place_and_clean
from ._placements import _placement_for as _placement_for
from ._placements import _reread as _reread
from ._placements import _still_shown as _still_shown
from ._placements import _target_verified as _target_verified
from ._placements import _unlink_same as _unlink_same
from ._placements import _verify as _verify
from ._placements import _write_placement as _write_placement
from ._planning import _aliases as _aliases
from ._planning import _cancelled as _cancelled
from ._planning import _count as _count
from ._planning import _group as _group
from ._planning import _groups as _groups
from ._planning import _hash as _hash
from ._planning import _inventory as _inventory
from ._planning import _manifest as _manifest
from ._planning import _member_differs as _member_differs
from ._planning import _metadata as _metadata
from ._planning import _read as _read
from ._planning import _resolves as _resolves
from ._planning import _run_fields as _run_fields
from ._planning import _slot_ids as _slot_ids
from ._planning import _Stream as _Stream
from ._planning import _stream as _stream
from ._planning import _target_id as _target_id
from ._planning import _untouch as _untouch
from ._planning import failed_report as failed_report
from ._planning import plan as plan
from ._planning import report as report
from ._preview import _decide as _decide
from ._preview import _describe as _describe
from ._preview import _key_text as _key_text
from ._preview import _label as _label
from ._preview import _list_of_str as _list_of_str
from ._preview import _object_subjects as _object_subjects
from ._preview import _object_texts as _object_texts
from ._preview import _raw_texts as _raw_texts
from ._preview import _sidecar as _sidecar
from ._preview import _Sources as _Sources
from ._preview import _sources as _sources
from ._preview import _subjects as _subjects
from ._runner import _execute as _execute
from ._runner import run as run

# Original star-import surface, captured before extraction.
__all__ = [
    'CANCELLED',
    'DONE',
    'FAILED',
    'KIND',
    'MAX_CONFLICTS',
    'ROOT_CHANGED',
    'Callable',
    'Group',
    'Item',
    'Iterable',
    'MigrationPlan',
    'Occurrence',
    'Path',
    'annotations',
    'assets',
    'atomic',
    'choose_retained',
    'contextmanager',
    'dataclass',
    'errno',
    'failed_report',
    'field',
    'greeting_images',
    'hashlib',
    'image_collection_imports',
    'image_collections',
    'image_hash',
    'image_refs',
    'image_scopes',
    'image_store',
    'image_subjects',
    'image_surfaces',
    'json',
    'locks',
    'nullcontext',
    'os',
    'partial',
    'paths',
    'pending_ids',
    'plan',
    're',
    'report',
    'revision',
    'run',
    'stat',
    'thumbs',
    'work_map_path',
]
