"""Image garbage collection: dispose of what nothing anywhere reaches (stage 4).

Spec section 12 and stage-4 rulings M12-M15, amended by the viewer-removal
spec (sections 2 and 3). Deleting a placement deletes only its ref; there is
no reference count, so an object nothing names any more stays until this
collects it. Two calls, both run as the ``maintenance`` run class and both
fail closed:

* **`scan`** is the dry run. It walks every root strictly, lists the store,
  and -- when something is collectable and nothing blocked the walk, or
  something waits in the outbox -- persists a single-use token naming exactly
  that (a blocked walk's names only the outbox: it drains, nothing more).
* **`collect`** takes such a token and disposes of at most what it names,
  intersected with a fresh strict walk, in batches under the ingest/GC lock.

**Roots** (M12) are where a reference is kept that is not itself an image:
every file in every ``image-refs/`` folder under ``worlds/`` and
``campaigns/`` (`image_refs.walk_strict`: sync-conflict copies, both sides of
a promotion journal, readable atomic temps), format-2 collection manifests,
harvest journals (read from the raw directory, so a deleted world's still
count), the migration work map's pending entries, and the same placements and
manifests inside staged work directories (``.world-staging``,
``.module-staging``). Anything among them that does not list, parse or name a
known format -- and any symlink -- BLOCKS: the walk is all or nothing, the
report names each blocking path, and nothing is collected. So does a staged
work directory touched within the last hour (`STAGING_MIN_AGE_SECONDS`): a
fork or an import is still filling it, and what it will place is not on disk
yet.

**Candidates** (M13; viewer-removal spec 2.1). An object is collectable only
when all of these hold:

- no root reaches it;
- its sidecar's and its blob's mtimes are both at least one hour old
  (`GRACE_HOURS`; ingest touches the sidecar, so an object somebody ingested
  and has not placed yet -- a Chub gallery mid-import, a harvested member, a
  staged bundle -- is never collected under them);
- and the collection carries a token from a scan on this device for this
  root, at most `TOKEN_HOURS` old.

The decision is made on mtimes alone: there is no per-device sightings file
(one an older build left under ``.cache/image-store/gc/`` is neither read nor
written). An mtime in the future is never collectable and is reported as
clock skew. The scan agrees with the collection: an otherwise collectable
object whose blob would be held (`_orphan_hold`: an unreadable sidecar, or an
unarrived object) is protected with that reason and is not offered or named
by the token.

**Blobs** (M14). A blob leaves the store only when no surviving readable
sidecar names it: the ``blob -> sidecars`` map is built over every file in
``objects/`` and rebuilt under the ingest/GC lock at each batch. One sidecar
that does not read (or an ``objects/`` folder that does not list) holds EVERY
blob, because it may name any of them. A blob nothing names is a candidate
under the same grace rule as an object -- except while some placement names
an object with no readable sidecar (one that has not arrived yet, mid-sync),
when every orphan blob is held: the unarrived object's blob may be among
them. An ``objects/`` folder that is absent beside a ``blobs/`` one blocks
outright. A blob-index entry is deleted only if it names the object being
collected; a blob's thumbnails go with the blob.

**Disposal** (M15; viewer-removal spec 3). It needs the token of a dry run
made on THIS device for THIS root, at most `TOKEN_HOURS` old, and consumes it
whether or not the collection completes -- unless `recycle_bin.check` finds no
real bin here, which refuses the collection (``no_recycle_bin``) before the
token is touched. A picture is never deleted: its blob goes to the operating
system's recycle bin, through this device's **outbox**
(`outbox_dir`, ``.cache/image-store/recycle/<device key>/``), under a readable
name (`bin_name`). Its sidecar, index entry and thumbnails are deleted.

Each batch -- up to `BATCH_OBJECTS` objects or `BATCH_SECONDS` of candidate
processing, after rebuilding the sidecar listing -- holds
`locks.image_ingest_gc_lock` and then, per object, its stripe, the order
ingest takes them in. Under them every candidate is re-checked (mtime, root,
reachability by the fresh walk, the blob refcount), and then:

1. an object whose blob is held (an unreadable sidecar, an unarrived object)
   is skipped whole, sidecar included -- deleting the sidecar would lose its
   description and bin nothing;
2. an object whose blob another sidecar names loses only its sidecar, and is
   reported ``released`` (``shared``): the picture is still in the store;
3. otherwise the blob is moved into the outbox by a move that never
   replaces a file there (a hard link and an unlink, or ``os.rename`` on
   Windows; `_no_clobber_move`) and THEN the sidecar is deleted. The move is
   the commit: a crash after it
   leaves a sidecar naming a missing blob, which no placement reaches and the
   next collection releases (``blob-missing``) -- the same as a blob already
   gone at this step. This reverses ingest's order (blob, then sidecar) and is
   safe for the same reason: both steps happen under the lock and the stripe,
   so an ingest of the same id waits, finds no sidecar and recreates the
   object with a fresh blob. A placement of that id made through
   ``assets.link_in`` (which needs neither ingest nor this lock) after the
   roots walk and before the batch is a race that predates the bin; its
   outcome is now a recoverable file in the bin instead of a deletion.
   An orphan blob is moved the same way, with no description.

Only after the lock is released are the batch's moved files sent to the bin
(`recycle_bin.send_batch`), the root checked before each, so a slow shell or
Finder call never holds up an upload. A file the bin takes is ``recycled``; a
file it refuses stays in the outbox under its readable name and stops the run
``trash-unavailable``; a batch Windows did not confirm stops it
``recycle-bin-unverified`` (see `recycle_bin`). A cancel or a root change sends
nothing more -- the bin calls ask before each file: what moved and was not sent
waits in the outbox. A collection therefore begins, once
its token is claimed and before its first batch, by sending this device's
outbox to the bin, and a refusal there stops it before anything else leaves
the store. A scan reports what waits (``outbox``) and issues a token for it
alone -- even when the walk is blocked, a token naming no candidate, so the
collection drains and then stops at the same walk -- so waiting files can
always be drained; files in another device's
outbox (or this one's under an earlier store path) are counted in
``foreign_outboxes`` and never touched. The outbox syncs with ``.cache``,
which is accepted traffic.

**Accepted risk.** A placement made on another device that has not synced here
yet does not reach its picture, so this device can bin it after an hour; the
deletion then syncs back and that placement resolves to nothing. Uploading the
binned file again, on the same decoder, lands under the same object id and
heals it -- without its description and tags.

**The root is pinned** (M4). Every path is built from the `root` the caller
captured, and ``paths.home().resolve() == root`` is asserted before every
destructive step -- each deletion, each move and each bin call: on a mismatch
the run stops, disposes of nothing more, and its report says
``root-changed``. Nothing outside ``assets/image-store/`` is ever deleted,
apart from the collected blobs' index entries and thumbnails under ``.cache``
and this module's own tokens; `_delete_file` refuses any other path and any
path reached through a link, and the outbox is not among the trees it deletes
in. A blob is moved only from ``blobs/`` into the outbox, and a file is sent
to the bin only from the outbox, each a regular file reached through no link.

Both calls leave a report (`maintenance_reports.write`) on every ending --
complete, blocked, refused, cancelled, root-changed, trash-unavailable,
recycle-bin-unverified or failed -- and return it. The two share one shape,
and each carries ``recycle_bin`` (the availability answer), ``outbox`` and
``foreign_outboxes``. Nothing here takes a campaign lock or writes campaign
state.
"""

from __future__ import annotations

# Compatibility facade. State and implementation live in responsibility leaves;
# internal fault injection targets those owners, while existing imports remain.
import contextlib
import errno
import json
import logging
import os
import re
import secrets
import stat
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .. import (
    atomic,
    image_collections,
    image_hash,
    image_migration,
    image_refs,
    image_store,
    image_surfaces,
    locks,
    maintenance_reports,
    paths,
    recycle_bin,
    thumbs,
)
from ._collection import _batches as _batches
from ._collection import _cache_delete as _cache_delete
from ._collection import _collect as _collect
from ._collection import _collect_blob as _collect_blob
from ._collection import _collect_object as _collect_object
from ._collection import _delete_sidecar as _delete_sidecar
from ._collection import _delete_thumbnails as _delete_thumbnails
from ._collection import _drop_index_entry as _drop_index_entry
from ._collection import _flush as _flush
from ._collection import _released as _released
from ._collection import _Run as _Run
from ._collection import _walk_and_collect as _walk_and_collect
from ._collection import _young as _young
from ._collection import collect as collect
from ._contracts import _BLOB_NAME as _BLOB_NAME
from ._contracts import _BLOBS_DIR as _BLOBS_DIR
from ._contracts import _DELETABLE as _DELETABLE
from ._contracts import _GC_DIR as _GC_DIR
from ._contracts import _NOT_IN_A_NAME as _NOT_IN_A_NAME
from ._contracts import _RECYCLE_DIR as _RECYCLE_DIR
from ._contracts import _RENAME_REFUSES_EXISTING as _RENAME_REFUSES_EXISTING
from ._contracts import _THUMBS_DIR as _THUMBS_DIR
from ._contracts import _TOKEN as _TOKEN
from ._contracts import BATCH_OBJECTS as BATCH_OBJECTS
from ._contracts import BATCH_SECONDS as BATCH_SECONDS
from ._contracts import BIN_DESCRIPTION_BYTES as BIN_DESCRIPTION_BYTES
from ._contracts import BIN_DESCRIPTION_CHARS as BIN_DESCRIPTION_CHARS
from ._contracts import BLOCKED as BLOCKED
from ._contracts import CANCELLED as CANCELLED
from ._contracts import COMPLETE as COMPLETE
from ._contracts import FAILED as FAILED
from ._contracts import FORMAT as FORMAT
from ._contracts import GRACE_HOURS as GRACE_HOURS
from ._contracts import KIND as KIND
from ._contracts import MOVE_TRIES as MOVE_TRIES
from ._contracts import RECYCLE_BIN_UNVERIFIED as RECYCLE_BIN_UNVERIFIED
from ._contracts import REFUSED as REFUSED
from ._contracts import ROOT_CHANGED as ROOT_CHANGED
from ._contracts import STAGING_DIRS as STAGING_DIRS
from ._contracts import STAGING_MIN_AGE_SECONDS as STAGING_MIN_AGE_SECONDS
from ._contracts import TOKEN_HOURS as TOKEN_HOURS
from ._contracts import TRASH_UNAVAILABLE as TRASH_UNAVAILABLE
from ._contracts import _as_number as _as_number
from ._contracts import _judge as _judge
from ._contracts import _number as _number
from ._contracts import _RootChangedError as _RootChangedError
from ._contracts import _UnsafePathError as _UnsafePathError
from ._contracts import _Verdict as _Verdict
from ._objects import _list_blobs as _list_blobs
from ._objects import _list_objects as _list_objects
from ._objects import _names_blob as _names_blob
from ._objects import _orphan_hold as _orphan_hold
from ._objects import _shards as _shards
from ._objects import _sidecar_entry as _sidecar_entry
from ._objects import _Sidecars as _Sidecars
from ._objects import _sig as _sig
from ._objects import _unarrived as _unarrived
from ._outbox import _BinGuard as _BinGuard
from ._outbox import _drain as _drain
from ._outbox import _recycled as _recycled
from ._outbox import _send as _send
from ._paths import _blob_key as _blob_key
from ._paths import _check_root as _check_root
from ._paths import _delete_file as _delete_file
from ._paths import _free_name as _free_name
from ._paths import _gc_dir as _gc_dir
from ._paths import _live_root as _live_root
from ._paths import _move_to_outbox as _move_to_outbox
from ._paths import _no_clobber_move as _no_clobber_move
from ._paths import _outbox_files as _outbox_files
from ._paths import _outbox_listing as _outbox_listing
from ._paths import _real_dirs as _real_dirs
from ._paths import _rel as _rel
from ._paths import _split_key as _split_key
from ._paths import _tokens_dir as _tokens_dir
from ._paths import bin_name as bin_name
from ._paths import blob_thumbnails as blob_thumbnails
from ._paths import outbox_dir as outbox_dir
from ._paths import token_path as token_path
from ._reports import _finish as _finish
from ._reports import _log as _log
from ._reports import _new_report as _new_report
from ._reports import _run_id as _run_id
from ._reports import failed_report as failed_report
from ._roots import _journal as _journal
from ._roots import _journals as _journals
from ._roots import _json_root as _json_root
from ._roots import _lstat_dir as _lstat_dir
from ._roots import _manifest as _manifest
from ._roots import _manifest_entries as _manifest_entries
from ._roots import _manifests as _manifests
from ._roots import _refs as _refs
from ._roots import _Roots as _Roots
from ._roots import _spelling_variants as _spelling_variants
from ._roots import _staged as _staged
from ._roots import _staging as _staging
from ._roots import _store_blocks as _store_blocks
from ._roots import _strict_listing as _strict_listing
from ._roots import _strict_path as _strict_path
from ._roots import _walk_roots as _walk_roots
from ._roots import _work_map as _work_map
from ._scanning import _classify as _classify
from ._scanning import _classify_blobs as _classify_blobs
from ._scanning import _classify_objects as _classify_objects
from ._scanning import _clear_candidates as _clear_candidates
from ._scanning import _reclaimable as _reclaimable
from ._scanning import _scan as _scan
from ._scanning import scan as scan
from ._tokens import _claim_token as _claim_token
from ._tokens import _issue_token as _issue_token
from ._tokens import _prune_tokens as _prune_tokens
from ._tokens import _read_token as _read_token

# Original star-import surface, captured before extraction.
__all__ = [
    'BATCH_OBJECTS',
    'BATCH_SECONDS',
    'BIN_DESCRIPTION_BYTES',
    'BIN_DESCRIPTION_CHARS',
    'BLOCKED',
    'CANCELLED',
    'COMPLETE',
    'FAILED',
    'FORMAT',
    'GRACE_HOURS',
    'KIND',
    'MOVE_TRIES',
    'RECYCLE_BIN_UNVERIFIED',
    'REFUSED',
    'ROOT_CHANGED',
    'STAGING_DIRS',
    'STAGING_MIN_AGE_SECONDS',
    'TOKEN_HOURS',
    'TRASH_UNAVAILABLE',
    'Callable',
    'Iterator',
    'Path',
    'annotations',
    'atomic',
    'bin_name',
    'blob_thumbnails',
    'collect',
    'contextlib',
    'dataclass',
    'errno',
    'failed_report',
    'field',
    'image_collections',
    'image_hash',
    'image_migration',
    'image_refs',
    'image_store',
    'image_surfaces',
    'json',
    'locks',
    'logging',
    'maintenance_reports',
    'os',
    'outbox_dir',
    'paths',
    're',
    'recycle_bin',
    'scan',
    'secrets',
    'stat',
    'sys',
    'thumbs',
    'time',
    'token_path',
    'uuid',
]
