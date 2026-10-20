"""Campaign-over-world copy-on-write resolution.

A campaign materializes a record only when it diverges from its world (edit,
version lock, delete, campaign-local create); everything else reads through to
the world live. Rules:

- Flat records (every kind in INHERITED_KINDS that is not an actor -- currently
  locations, lore, items, groups, creatures, greetings -- plus plotmap.json):
  campaign file wins; else a tombstone means absent; else the world file.
- Actors (characters/pcs): whole-dir, keyed on character.md / pc.md existing in
  the campaign — a materialized actor is authoritative for meta + versions, so
  lock-purged versions stay purged. Sidecars (tagline.md, voice_anchor.md) and
  assets still overlay per file -- and what the overlay hides is not lost to
  the campaign: `shadowed_images()` reaches the world's copy under a name the
  campaign replaced, and `base_versions()` passes the world's versions through
  when a lock purged them. Both read-only, both shown beside the campaign's
  own rather than instead of it.
- sync.md holds base hashes for materialized records only. Tombstones live in
  <campaign>/deleted.json (a sorted JSON list of refs); a tombstoned id counts
  as taken for uniquify, so nothing ever resurrects under a reused id.
- <campaign>/detached.json (same shape) holds the third state: a record that
  shares only a slug with whatever the world files under that id. Two ways in,
  and they are the same fact from either end -- the campaign copy whose world
  original was DELETED, and the record the campaign CREATED for itself, whose
  id named nothing in the world when it was allocated. Records, sidecars and
  assets all stop resolving through for it -- see `detached()`.

Which records inherit, and which do not, is the whole content of the rule, so
it is declared below as data (INHERITED_KINDS / INHERITED_FILES) rather than
only in prose: `tests/test_overlay_guard.py` reads those names to check that
nothing outside this module resolves an inheritable record off a raw campaign
root. Everything else under <campaign> is campaign-local by definition and is
read directly: campaign.md, sync.md, deleted.json, detached.json, appearances.json,
calendar.json, changes.json, journal.json, relationship_history.json,
chronicle.json, timeline.md, sheet_baselines.json, the climate default
(store/campaign_climate.py owns that filename), scenes/, sheets/, proposals/,
and the per-actor sidecars filed inside an actor dir
(dossier.md, state.md, voice_drift.md). tagline.md and voice_anchor.md are the
exceptions among sidecars -- both are world-level identity, so both overlay per
file, via `tagline()` / `voice_anchor()` below.

Campaign-local, though, does not mean campaign-lifetime: those sidecars are
filed under a record id, and an id outlives the record it named. Deleting a
record frees its slug, so the next create hands the same id back -- which is
what `forget_world_record` (at the bottom) exists to get in front of.
"""

from __future__ import annotations

# Compatibility facade. State and implementation live in responsibility leaves;
# internal fault injection targets those owners, while existing imports remain.
import filecmp
import hashlib
import json
import logging
import shutil
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel

from .. import (
    assets,
    atomic,
    cards,
    characters,
    commits,
    entities,
    entity_schema,
    failsoft,
    greetings,
    group_inventory,
    group_proposals,
    group_refs,
    image_descriptions,
    image_refs,
    image_store,
    journal,
    knowledge,
    locks,
    pcs,
    revision,
    statcache,
    taglines,
    voice_anchors,
)
from .. import paths as library_paths
from ..appearances import paths as appearances_paths
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from ..paths import natural_key, safe_id
from ..worlds import paths as worlds_paths
from ._actors import _mark_campaign_owned as _mark_campaign_owned
from ._actors import actor_root as actor_root
from ._actors import char_root as char_root
from ._actors import create_character as create_character
from ._actors import create_pc as create_pc
from ._actors import delete_actor as delete_actor
from ._actors import dematerialize_actor as dematerialize_actor
from ._actors import ensure_actor_writable as ensure_actor_writable
from ._actors import materialize_actor as materialize_actor
from ._actors import pc_root as pc_root
from ._copying import _copy_slots_down as _copy_slots_down
from ._copying import _image_slot_file as _image_slot_file
from ._copying import _materialize_flat as _materialize_flat
from ._copying import _put_base as _put_base
from ._copying import _recorded_base as _recorded_base
from ._copying import _tombstoned_asset as _tombstoned_asset
from ._copying import actor_snapshot as actor_snapshot
from ._copying import copy_record_dir_down as copy_record_dir_down
from ._copying import materialize_entity as materialize_entity
from ._copying import record_text as record_text
from ._copying import recorded_base as recorded_base
from ._copying import require_group_materialization as require_group_materialization
from ._entities import create_entity as create_entity
from ._entities import delete_entity as delete_entity
from ._entities import entity_rev as entity_rev
from ._entities import entity_root as entity_root
from ._entities import entity_source as entity_source
from ._entities import group_capture as group_capture
from ._entities import group_listing as group_listing
from ._entities import group_rows as group_rows
from ._entities import list_entities as list_entities
from ._entities import read_entity as read_entity
from ._entities import read_entity_rev as read_entity_rev
from ._entities import read_group_membership as read_group_membership
from ._entities import require_owned_entity as require_owned_entity
from ._entities import update_entity as update_entity
from ._entities import would_inherit as would_inherit
from ._greetings import _without_detached as _without_detached
from ._greetings import create_greeting as create_greeting
from ._greetings import delete_greeting as delete_greeting
from ._greetings import list_greetings as list_greetings
from ._greetings import materialize_plotmap as materialize_plotmap
from ._greetings import read_greeting as read_greeting
from ._greetings import read_greeting_rev as read_greeting_rev
from ._greetings import read_plotmap as read_plotmap
from ._greetings import set_edges as set_edges
from ._greetings import update_greeting as update_greeting
from ._identity import RecordIdentityRefs as RecordIdentityRefs
from ._identity import _canonical_record_id as _canonical_record_id
from ._identity import _inherits_world as _inherits_world
from ._identity import _require_group_copy as _require_group_copy
from ._identity import _same_actor_slot as _same_actor_slot
from ._identity import _unresolved_group_slots as _unresolved_group_slots
from ._identity import _world_identity_matches_owned as _world_identity_matches_owned
from ._identity import actor_detachments as actor_detachments
from ._identity import capture_record_refs as capture_record_refs
from ._identity import group_unresolved as group_unresolved
from ._identity import has_own_copy as has_own_copy
from ._identity import independent_owned_record as independent_owned_record
from ._identity import record_is_marked as record_is_marked
from ._identity import require_world_identity_scope as require_world_identity_scope
from ._identity import world_identity_matches_owned as world_identity_matches_owned
from ._images import _check_promote_arrived as _check_promote_arrived
from ._images import _held_pictures as _held_pictures
from ._images import _image_halves as _image_halves
from ._images import _novel_bytes as _novel_bytes
from ._images import _row_ids as _row_ids
from ._images import avatar_v as avatar_v
from ._images import base_versions as base_versions
from ._images import delete_image as delete_image
from ._images import image_root as image_root
from ._images import list_images as list_images
from ._images import promote_image as promote_image
from ._images import read_description as read_description
from ._images import read_descriptions as read_descriptions
from ._images import read_descriptions_listed as read_descriptions_listed
from ._images import read_focus as read_focus
from ._images import set_description as set_description
from ._images import shadowed_images as shadowed_images
from ._ledgers import _deleted_path as _deleted_path
from ._ledgers import _detached_path as _detached_path
from ._ledgers import _drop_deleted as _drop_deleted
from ._ledgers import _refs_in as _refs_in
from ._ledgers import _strict_ledger as _strict_ledger
from ._ledgers import _valid_ledger_ref as _valid_ledger_ref
from ._ledgers import add_deleted as add_deleted
from ._ledgers import add_detached as add_detached
from ._ledgers import deleted as deleted
from ._ledgers import deleted_strict as deleted_strict
from ._ledgers import detached as detached
from ._ledgers import detached_strict as detached_strict
from ._ledgers import drop_library_tombstone as drop_library_tombstone
from ._ledgers import require_identity_ledgers as require_identity_ledgers
from ._reclassification import reclassify_entity as reclassify_entity
from ._reclassification import rewrite_owner_refs as rewrite_owner_refs
from ._reclassification import rewrite_ref_fields as rewrite_ref_fields
from ._record_paths import INHERITED_FILES as INHERITED_FILES
from ._record_paths import INHERITED_KINDS as INHERITED_KINDS
from ._record_paths import RESERVED_BASE as RESERVED_BASE
from ._record_paths import _actor_meta as _actor_meta
from ._record_paths import _actor_not_found as _actor_not_found
from ._record_paths import _drop_record_dir as _drop_record_dir
from ._record_paths import _flat_path as _flat_path
from ._record_paths import _identity_path as _identity_path
from ._record_paths import _record_dir as _record_dir
from ._record_paths import _record_present as _record_present
from ._record_paths import asset_ref as asset_ref
from ._record_paths import croot_of as croot_of
from ._record_paths import flat_ref as flat_ref
from ._record_paths import library_ref as library_ref
from ._record_paths import log as log
from ._record_paths import record_dir as record_dir
from ._record_paths import wroot_of as wroot_of
from ._rosters import _anchor_presence as _anchor_presence
from ._rosters import _patch_char_item as _patch_char_item
from ._rosters import _patch_pc_item as _patch_pc_item
from ._rosters import _roster_union as _roster_union
from ._rosters import cast_ids as cast_ids
from ._rosters import character_ids as character_ids
from ._rosters import character_name_and_versions as character_name_and_versions
from ._rosters import character_refs as character_refs
from ._rosters import character_roster as character_roster
from ._rosters import character_sidecars as character_sidecars
from ._rosters import list_characters as list_characters
from ._rosters import list_pcs as list_pcs
from ._rosters import pc_revision as pc_revision
from ._rosters import pc_revisions as pc_revisions
from ._rosters import pc_roster as pc_roster
from ._rosters import read_character as read_character
from ._rosters import read_pc as read_pc
from ._sidecars import set_tagline as set_tagline
from ._sidecars import set_voice_anchor as set_voice_anchor
from ._sidecars import tagline as tagline
from ._sidecars import voice_anchor as voice_anchor
from ._sidecars import voice_anchor_record as voice_anchor_record
from ._transitions import _drop_manifest_ref as _drop_manifest_ref
from ._transitions import _moved_identity_marker as _moved_identity_marker
from ._transitions import _retire_owned_reviews as _retire_owned_reviews
from ._transitions import _undetach as _undetach
from ._transitions import repoint_record as repoint_record
from ._transitions import undetach as undetach
from ._views import View as View
from ._views import _view as _view
from ._views import view as view
from ._world_lifecycle import WorldIdentityScope as WorldIdentityScope
from ._world_lifecycle import _drop_plotmap_edges as _drop_plotmap_edges
from ._world_lifecycle import _forget_in_campaign as _forget_in_campaign
from ._world_lifecycle import bound_dependents as bound_dependents
from ._world_lifecycle import dematerialize as dematerialize
from ._world_lifecycle import dependent_campaigns as dependent_campaigns
from ._world_lifecycle import forget_world_record as forget_world_record
from ._world_lifecycle import retire_world_reviews as retire_world_reviews
from ._world_lifecycle import retire_world_scope_reviews as retire_world_scope_reviews
from ._world_lifecycle import world_identity_change as world_identity_change
from ._world_lifecycle import world_identity_scope as world_identity_scope

# Original star-import surface, captured before extraction.
__all__ = [
    'INHERITED_FILES',
    'INHERITED_KINDS',
    'RESERVED_BASE',
    'BaseModel',
    'Callable',
    'Iterable',
    'Iterator',
    'Mapping',
    'Path',
    'RecordIdentityRefs',
    'View',
    'WorldIdentityScope',
    'actor_detachments',
    'actor_root',
    'actor_snapshot',
    'add_deleted',
    'add_detached',
    'annotations',
    'appearances_paths',
    'asset_ref',
    'assets',
    'atomic',
    'avatar_v',
    'base_versions',
    'bound_dependents',
    'campaigns_paths',
    'campaigns_read',
    'capture_record_refs',
    'cards',
    'cast_ids',
    'char_root',
    'character_ids',
    'character_name_and_versions',
    'character_refs',
    'character_roster',
    'character_sidecars',
    'characters',
    'commits',
    'contextmanager',
    'copy_record_dir_down',
    'create_character',
    'create_entity',
    'create_greeting',
    'create_pc',
    'croot_of',
    'delete_actor',
    'delete_entity',
    'delete_greeting',
    'delete_image',
    'deleted',
    'deleted_strict',
    'dematerialize',
    'dematerialize_actor',
    'dependent_campaigns',
    'detached',
    'detached_strict',
    'drop_library_tombstone',
    'ensure_actor_writable',
    'entities',
    'entity_rev',
    'entity_root',
    'entity_schema',
    'entity_source',
    'failsoft',
    'filecmp',
    'flat_ref',
    'forget_world_record',
    'greetings',
    'group_capture',
    'group_inventory',
    'group_listing',
    'group_proposals',
    'group_refs',
    'group_rows',
    'group_unresolved',
    'has_own_copy',
    'hashlib',
    'image_descriptions',
    'image_refs',
    'image_root',
    'image_store',
    'independent_owned_record',
    'journal',
    'json',
    'knowledge',
    'library_paths',
    'library_ref',
    'list_characters',
    'list_entities',
    'list_greetings',
    'list_images',
    'list_pcs',
    'locks',
    'log',
    'logging',
    'materialize_actor',
    'materialize_entity',
    'materialize_plotmap',
    'natural_key',
    'pc_revision',
    'pc_revisions',
    'pc_root',
    'pc_roster',
    'pcs',
    'promote_image',
    'read_character',
    'read_description',
    'read_descriptions',
    'read_descriptions_listed',
    'read_entity',
    'read_entity_rev',
    'read_focus',
    'read_greeting',
    'read_greeting_rev',
    'read_group_membership',
    'read_pc',
    'read_plotmap',
    'reclassify_entity',
    'record_dir',
    'record_is_marked',
    'record_text',
    'recorded_base',
    'repoint_record',
    'require_group_materialization',
    'require_identity_ledgers',
    'require_owned_entity',
    'require_world_identity_scope',
    'retire_world_reviews',
    'retire_world_scope_reviews',
    'revision',
    'rewrite_owner_refs',
    'rewrite_ref_fields',
    'safe_id',
    'set_description',
    'set_edges',
    'set_tagline',
    'set_voice_anchor',
    'shadowed_images',
    'shutil',
    'statcache',
    'tagline',
    'taglines',
    'undetach',
    'update_entity',
    'update_greeting',
    'view',
    'voice_anchor',
    'voice_anchor_record',
    'voice_anchors',
    'world_identity_change',
    'world_identity_matches_owned',
    'world_identity_scope',
    'worlds_paths',
    'would_inherit',
    'wroot_of',
]

# Preserve deferred module annotations without copying live values.
__annotations__ = {'INHERITED_KINDS': 'tuple[str, ...]', 'INHERITED_FILES': 'tuple[str, ...]'}
