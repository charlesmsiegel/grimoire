"""Character containers: one folder per character, one JSON V3 card per version.

Unlike generic entities (one markdown file each), a character is a directory:
  <root>/characters/<cid>/character.md   # frontmatter: name, default_version
  <root>/characters/<cid>/<vid>.json     # a SillyTavern V3 card
  <root>/characters/<cid>/assets/        # optional images (from PNG/CHARX import)
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from PIL import Image

from .. import (
    _actor_hash as actor_hash,
)
from .. import (
    assets,
    atomic,
    cards,
    chub,
    fetch,
    image_descriptions,
    image_hash,
    image_refs,
    image_store,
    lorebook,
    statcache,
    taglines,
    voice_anchors,
    ziputil,
)
from ..frontmatter import dump_frontmatter, parse_frontmatter
from ..paths import safe_id, slugify, uniquify
from ._avatar import _avatar_candidates as _avatar_candidates
from ._avatar import _carried_uri as _carried_uri
from ._avatar import _drop_avatar_uri as _drop_avatar_uri
from ._avatar import _plane_is_the_carried_avatar as _plane_is_the_carried_avatar
from ._avatar import _png_plane_of as _png_plane_of
from ._avatar import _resolve_avatar as _resolve_avatar
from ._avatar import _shown as _shown
from ._bundled import _AVATAR_TYPES as _AVATAR_TYPES
from ._bundled import _MAX_BUNDLED_CANDIDATES as _MAX_BUNDLED_CANDIDATES
from ._bundled import _MAX_BUNDLED_IMAGES as _MAX_BUNDLED_IMAGES
from ._bundled import _MAX_BUNDLED_READS as _MAX_BUNDLED_READS
from ._bundled import _SIDECAR_STEMS as _SIDECAR_STEMS
from ._bundled import _bundled_image_name as _bundled_image_name
from ._bundled import _BundledImages as _BundledImages
from ._bundled import _stage_bundled as _stage_bundled
from ._bundled import _stage_entry as _stage_entry
from ._card_fields import DEFAULT_VERSION_NAME as DEFAULT_VERSION_NAME
from ._card_fields import _apply_label as _apply_label
from ._card_fields import _carry_label as _carry_label
from ._card_fields import _dumps as _dumps
from ._card_fields import _extensions as _extensions
from ._card_fields import _importable_lore as _importable_lore
from ._card_fields import _version_chub_source as _version_chub_source
from ._card_fields import _version_label as _version_label
from ._card_fields import _writable_extensions as _writable_extensions
from ._card_fields import blank_card as blank_card
from ._catalog import _NO_FACTS as _NO_FACTS
from ._catalog import _build_roster_row as _build_roster_row
from ._catalog import _build_row as _build_row
from ._catalog import _build_version_facts as _build_version_facts
from ._catalog import _facts_entry as _facts_entry
from ._catalog import _listed_dirs as _listed_dirs
from ._catalog import _listed_names as _listed_names
from ._catalog import _row as _row
from ._catalog import character_count as character_count
from ._catalog import character_exists as character_exists
from ._catalog import character_refs as character_refs
from ._catalog import find_unlinked_versions as find_unlinked_versions
from ._catalog import list_characters as list_characters
from ._catalog import listed_ids as listed_ids
from ._catalog import roster as roster
from ._catalog import version_facts as version_facts
from ._chub import _CHUB_DEF_FIELDS as _CHUB_DEF_FIELDS
from ._chub import _download_gallery as _download_gallery
from ._chub import _download_lorebooks as _download_lorebooks
from ._chub import _gallery_names as _gallery_names
from ._chub import _legacy_identity as _legacy_identity
from ._chub import _replace_gallery as _replace_gallery
from ._chub import _replace_held_gallery as _replace_held_gallery
from ._chub import _sniff_card_format as _sniff_card_format
from ._chub import download_card as download_card
from ._chub import download_chub_gallery as download_chub_gallery
from ._chub import download_chub_gallery_stream as download_chub_gallery_stream
from ._chub import download_chub_lorebooks as download_chub_lorebooks
from ._chub import import_from_chub as import_from_chub
from ._chub import merge_chub_definition as merge_chub_definition
from ._chub import resolve_chub_node as resolve_chub_node
from ._exporting import _export_filename as _export_filename
from ._exporting import _stored_avatar as _stored_avatar
from ._exporting import export_card as export_card
from ._hashes import _dir_hash_compute as _dir_hash_compute
from ._hashes import card_hash as card_hash
from ._hashes import dir_content_hash as dir_content_hash
from ._hashes import dir_hash as dir_hash
from ._hashes import snapshot as snapshot
from ._importing import _card_text as _card_text
from ._importing import _check_destination as _check_destination
from ._importing import _occupied_in as _occupied_in
from ._importing import _place_import as _place_import
from ._importing import _unwind_import as _unwind_import
from ._importing import _write_record as _write_record
from ._importing import _Written as _Written
from ._importing import import_card as import_card
from ._memo import _FACTS_POOL as _FACTS_POOL
from ._memo import _IDENTITY_POOL as _IDENTITY_POOL
from ._memo import _ROW_POOL as _ROW_POOL
from ._memo import _SUMMARY_POOL as _SUMMARY_POOL

# Explicit compatibility aliases retain the original binding and star surface.
from ._memo import POOL_ENTRIES as POOL_ENTRIES
from ._paths import CharacterNotFound as CharacterNotFound
from ._paths import VersionNotFound as VersionNotFound
from ._paths import _addressable_default as _addressable_default
from ._paths import _card_path as _card_path
from ._paths import _char_dir as _char_dir
from ._paths import _chars_dir as _chars_dir
from ._paths import _meta_path as _meta_path
from ._paths import _require_char as _require_char
from ._paths import _version_ids as _version_ids
from ._paths import _version_ids_in as _version_ids_in
from ._paths import require_version as require_version
from ._reads import _build_identity as _build_identity
from ._reads import _card_summary as _card_summary
from ._reads import _greeting_count as _greeting_count
from ._reads import birthdate_meta as birthdate_meta
from ._reads import default_version as default_version
from ._reads import name_and_versions as name_and_versions
from ._reads import read_card as read_card
from ._reads import read_character as read_character
from ._reads import read_tags as read_tags
from ._reads import version_ids as version_ids
from ._reads import version_label as version_label
from ._records import clear_chub_source as clear_chub_source
from ._records import create_character as create_character
from ._records import create_version as create_version
from ._records import delete_character as delete_character
from ._records import delete_version as delete_version
from ._records import set_birthdate as set_birthdate
from ._records import set_chub_source as set_chub_source
from ._records import set_default_version as set_default_version
from ._records import set_name as set_name
from ._records import set_tags as set_tags
from ._records import update_version as update_version

# Preserve the original deferred module annotations and their resolved types.
__annotations__ = {'_ROW_POOL': 'dict', '_SUMMARY_POOL': 'dict', '_FACTS_POOL': 'dict', '_IDENTITY_POOL': 'dict'}

# Captured from the original module before responsibility leaves existed.
__all__ = [
    'DEFAULT_VERSION_NAME',
    'POOL_ENTRIES',
    'BytesIO',
    'Callable',
    'CharacterNotFound',
    'Image',
    'Path',
    'VersionNotFound',
    'actor_hash',
    'annotations',
    'assets',
    'atomic',
    'birthdate_meta',
    'blank_card',
    'card_hash',
    'cards',
    'character_count',
    'character_exists',
    'character_refs',
    'chub',
    'clear_chub_source',
    'create_character',
    'create_version',
    'dataclass',
    'default_version',
    'delete_character',
    'delete_version',
    'dir_content_hash',
    'dir_hash',
    'download_card',
    'download_chub_gallery',
    'download_chub_gallery_stream',
    'download_chub_lorebooks',
    'dump_frontmatter',
    'export_card',
    'fetch',
    'field',
    'find_unlinked_versions',
    'functools',
    'hashlib',
    'image_descriptions',
    'image_hash',
    'image_refs',
    'image_store',
    'import_card',
    'import_from_chub',
    'json',
    'list_characters',
    'listed_ids',
    'lorebook',
    'merge_chub_definition',
    'name_and_versions',
    'os',
    'parse_frontmatter',
    'read_card',
    'read_character',
    'read_tags',
    'require_version',
    'resolve_chub_node',
    'roster',
    'safe_id',
    'set_birthdate',
    'set_chub_source',
    'set_default_version',
    'set_name',
    'set_tags',
    'shutil',
    'slugify',
    'snapshot',
    'statcache',
    'taglines',
    'uniquify',
    'update_version',
    'version_facts',
    'version_ids',
    'version_label',
    'voice_anchors',
    'zipfile',
    'ziputil',
]
