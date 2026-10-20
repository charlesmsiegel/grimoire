"""Per-version image store: <base>/<cid>/assets/<vid>/<name>.<ext>.

**Placements.** Every write now lands in the content-addressed store
(`store.image_store`) and leaves a small placement record in the directory,
``<d>/image-refs/<name>.json`` (`store.image_refs`), instead of the bytes. The
legacy ``<name>.<ext>`` layout is still READ, as the fallback of every lookup
(spec section 6): a placement that resolves wins; one that does not (object or
blob not synced yet) falls through to a legacy file of that name when there is
one. Nothing here ever writes a legacy file again, and a write removes the
name's legacy siblings once its placement is published.

The default base is "characters"; entity kinds (locations, lore) pass base=kind
with vid="default" so records without versions get the same folder layout. The
avatar/primary image is the image named AVATAR. Other image kinds (gallery,
emotions, backgrounds, …) drop into the same per-version folder with no schema
change. Images are never hashed into the card, so character sync is untouched
by image edits.
"""

from __future__ import annotations

# Compatibility facade. State and implementation live in responsibility leaves;
# internal fault injection targets those owners, while existing imports remain.
import json
import os
import shutil
from collections.abc import Callable, Iterable
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from typing import Literal, overload

from .. import atomic, image_hash, image_refs, image_store, locks, statcache
from ..paths import safe_id
from ._contracts import _EXTS as _EXTS
from ._contracts import _PROMOTE_TMP as _PROMOTE_TMP
from ._contracts import AVATAR as AVATAR
from ._contracts import DESCRIPTIONS_FILE as DESCRIPTIONS_FILE
from ._contracts import FOCUS_FILE as FOCUS_FILE
from ._contracts import LOCALIZED_PREFIX as LOCALIZED_PREFIX
from ._contracts import _addressable_name as _addressable_name
from ._contracts import _dir as _dir
from ._contracts import _safe_name as _safe_name
from ._contracts import gallery_count as gallery_count
from ._contracts import localized_count as localized_count
from ._contracts import norm_ext as norm_ext
from ._contracts import storable as storable
from ._contracts import version_dir as version_dir
from ._legacy_rescue import _HEAL_RETRIES as _HEAL_RETRIES
from ._legacy_rescue import _free_gallery as _free_gallery
from ._legacy_rescue import _heal_slot as _heal_slot
from ._legacy_rescue import _heal_stranded_promotion as _heal_stranded_promotion
from ._legacy_rescue import _newest_stranded as _newest_stranded
from ._locking import _heal_locks as _heal_locks
from ._locking import _image_lock as _image_lock
from ._locking import _image_locks_held as _image_locks_held
from ._locking import _locks_if_free as _locks_if_free
from ._locking import name_locks_held as name_locks_held
from ._locking import sidecar_lock as sidecar_lock
from ._locking import sidecar_locks_held as sidecar_locks_held
from ._lookup import _candidates as _candidates
from ._lookup import _Files as _Files
from ._lookup import _listing as _listing
from ._lookup import _resolve_refs as _resolve_refs
from ._lookup import _stranded as _stranded
from ._lookup import _token as _token
from ._lookup import file_in as file_in
from ._lookup import has_legacy as has_legacy
from ._lookup import image_version as image_version
from ._lookup import is_slot_file as is_slot_file
from ._lookup import legacy_path as legacy_path
from ._lookup import list_in as list_in
from ._lookup import mtime_ns as mtime_ns
from ._lookup import name_rows_in as name_rows_in
from ._lookup import names_in as names_in
from ._lookup import path_in as path_in
from ._lookup import resolve as resolve
from ._lookup import siblings as siblings
from ._lookup import slot_taken as slot_taken
from ._lookup import taken_slots as taken_slots
from ._metadata import _avatar_ref as _avatar_ref
from ._metadata import _clamp_focus as _clamp_focus
from ._metadata import _clear_focus_in as _clear_focus_in
from ._metadata import _edit_sidecar_locked as _edit_sidecar_locked
from ._metadata import _read_focus_file as _read_focus_file
from ._metadata import _read_sidecar as _read_sidecar
from ._metadata import _unlink_focus_file as _unlink_focus_file
from ._metadata import clear_focus as clear_focus
from ._metadata import drop_sidecar_entry as drop_sidecar_entry
from ._metadata import edit_sidecar as edit_sidecar
from ._metadata import owns_focus as owns_focus
from ._metadata import read_focus as read_focus
from ._placements import _drop_if_placed as _drop_if_placed
from ._placements import _drop_snapshotted as _drop_snapshotted
from ._placements import _place as _place
from ._placements import _set_placement as _set_placement
from ._placements import _sheds_caption as _sheds_caption
from ._placements import _snapshot_siblings as _snapshot_siblings
from ._placements import adopt_legacy as adopt_legacy
from ._recovery import ImageNotYetAvailableError as ImageNotYetAvailableError
from ._recovery import _finish_promotion as _finish_promotion
from ._recovery import _journal_ok as _journal_ok
from ._recovery import _recover_before_write as _recover_before_write
from ._recovery import _recover_promotion as _recover_promotion
from ._recovery import check_arrived as check_arrived
from ._recovery import recover_promotion as recover_promotion
from ._recovery import recover_promotions_in as recover_promotions_in
from ._version_views import _restamp_avatar as _restamp_avatar
from ._version_views import _stamp_avatar_blob as _stamp_avatar_blob
from ._version_views import _stamp_avatar_object as _stamp_avatar_object
from ._version_views import image_file as image_file
from ._version_views import image_id as image_id
from ._version_views import image_path as image_path
from ._version_views import list_images as list_images
from ._version_views import version_art as version_art
from ._writes import _check_promotable as _check_promotable
from ._writes import _copy_slot as _copy_slot
from ._writes import copy_slots as copy_slots
from ._writes import delete_image as delete_image
from ._writes import delete_in as delete_in
from ._writes import delete_version_images as delete_version_images
from ._writes import link_if_vacant as link_if_vacant
from ._writes import link_in as link_in
from ._writes import promote_image as promote_image
from ._writes import put_image as put_image
from ._writes import put_in as put_in
from ._writes import unlink_if_holds as unlink_if_holds
from ._writes import write_focus as write_focus

# Original star-import surface, captured before extraction.
__all__ = [
    'AVATAR',
    'DESCRIPTIONS_FILE',
    'FOCUS_FILE',
    'LOCALIZED_PREFIX',
    'Callable',
    'ExitStack',
    'ImageNotYetAvailableError',
    'Iterable',
    'Literal',
    'Path',
    'adopt_legacy',
    'annotations',
    'atomic',
    'check_arrived',
    'clear_focus',
    'contextmanager',
    'copy_slots',
    'delete_image',
    'delete_in',
    'delete_version_images',
    'drop_sidecar_entry',
    'edit_sidecar',
    'file_in',
    'gallery_count',
    'has_legacy',
    'image_file',
    'image_hash',
    'image_id',
    'image_path',
    'image_refs',
    'image_store',
    'image_version',
    'is_slot_file',
    'json',
    'legacy_path',
    'link_if_vacant',
    'link_in',
    'list_images',
    'list_in',
    'localized_count',
    'locks',
    'mtime_ns',
    'name_locks_held',
    'name_rows_in',
    'names_in',
    'norm_ext',
    'os',
    'overload',
    'owns_focus',
    'path_in',
    'promote_image',
    'put_image',
    'put_in',
    'read_focus',
    'recover_promotion',
    'recover_promotions_in',
    'resolve',
    'safe_id',
    'shutil',
    'siblings',
    'sidecar_lock',
    'sidecar_locks_held',
    'slot_taken',
    'statcache',
    'storable',
    'suppress',
    'taken_slots',
    'unlink_if_holds',
    'version_art',
    'version_dir',
    'write_focus',
]
