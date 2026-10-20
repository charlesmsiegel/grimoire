"""The list of everything the app noticed, and the ignore that silences one.

Every entry is derived on the way out. There is no chore table on disk: a
chore whose count is zero does not appear, and the number in its label is the
number this request computed. Some of a chore's inputs are reused rather than
re-derived -- the overview projections (`store/overview/`), under a key that
covers every file they read -- and the chores whose inputs cannot all be named
in a key stay live (spec 04 section 4.4 classifies every chore). A reused input
describes the bytes as their stamps last showed them; the residual (a rewrite
keeping size, mtime and ctime, possible on Windows and FAT, is believed until
restart in process and for the life of a persisted `sources` row) is in
`docs/store-guarantees.md`. That is what makes the list
worth opening twice -- a to-do list that can go stale teaches the reader to
distrust it, and then the one entry that mattered is the one they scroll past.
One exception, read-only: the continuity chores read the reconciliation cache
(`continuity_candidates.json`) through `continuity.pending`'s live filter in the
same request — suppressed, stale, merged-away and already-closed findings never
count.

The cost rule from `routes/shell.py` applies here for the same reason: a chore
that cannot be counted cheaply is not offered. This page is opened casually and
answers about a whole library, so a count proportional to the library's age
would make it a page nobody opens. Where a useful chore has no cheap source it
waits for one rather than shipping a guess.

`store.chores` owns the ignore set; everything else here is a read of the
stores the rest of the app already reads.

Image gaps describe the artwork the browse surfaces display: a character's
default version and each world's or campaign's own cover. Other versions and
gallery images do not fill that gap. Avatar presence reuses the art facts and
overlay token resolver; adding another image chore should follow its display
surface's resolver too, so the report cannot disagree with what the user sees.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from ... import store
from ...store import overview, readstats
from ...store.continuity import candidates as continuity_candidates
from ...store.continuity import effective as continuity_effective
from ...store.continuity import pending as continuity_pending
from ..common import (
    _dump,  # noqa: F401  (kept for the pydantic-agnostic rule)
    rates_block,
    rates_editable,
)
from ..overview_log import overview_read

# Explicit compatibility aliases retain the original binding and star surface.
from . import _router as _router
from ._campaign import _chore_anchors as _chore_anchors
from ._campaign import _chore_avatars as _chore_avatars
from ._campaign import _chore_cover as _chore_cover
from ._campaign import _chore_open_scenes as _chore_open_scenes
from ._campaign import _chore_sheets as _chore_sheets
from ._campaign import _chore_taglines as _chore_taglines
from ._campaign import _chore_unreviewed as _chore_unreviewed
from ._campaign import _items_anchors as _items_anchors
from ._campaign import _items_open_scenes as _items_open_scenes
from ._campaign import _items_sheets as _items_sheets
from ._campaign import _items_taglines as _items_taglines
from ._campaign import _items_unreviewed as _items_unreviewed
from ._catalog import BUILDERS as BUILDERS
from ._catalog import CAMPAIGN_BUILDERS as CAMPAIGN_BUILDERS
from ._catalog import CAMPAIGN_IDS as CAMPAIGN_IDS
from ._catalog import GROUP_ORDER as GROUP_ORDER
from ._catalog import ITEM_CAP as ITEM_CAP
from ._catalog import ITEMS as ITEMS
from ._catalog import KNOWN as KNOWN
from ._catalog import LIBRARY_BUILDERS as LIBRARY_BUILDERS
from ._catalog import LIBRARY_IDS as LIBRARY_IDS
from ._context import _Ctx as _Ctx
from ._continuity import _CONTINUITY_KIND as _CONTINUITY_KIND
from ._continuity import _chore_continuity_closures as _chore_continuity_closures
from ._continuity import _chore_continuity_overlaps as _chore_continuity_overlaps
from ._continuity import _chore_owed as _chore_owed
from ._continuity import _continuity_items as _continuity_items
from ._continuity import _items_continuity_closures as _items_continuity_closures
from ._continuity import _items_continuity_overlaps as _items_continuity_overlaps
from ._continuity import _items_owed as _items_owed
from ._library import _chore_world_anchors as _chore_world_anchors
from ._library import _chore_world_avatars as _chore_world_avatars
from ._library import _chore_world_covers as _chore_world_covers
from ._library import _chore_world_describe as _chore_world_describe
from ._library import _chore_world_subjects as _chore_world_subjects
from ._library import _chore_world_taglines as _chore_world_taglines
from ._library import _items_world_anchors as _items_world_anchors
from ._library import _items_world_avatars as _items_world_avatars
from ._library import _items_world_covers as _items_world_covers
from ._library import _items_world_describe as _items_world_describe
from ._library import _items_world_subjects as _items_world_subjects
from ._library import _items_world_taglines as _items_world_taglines
from ._library import _world_gap_items as _world_gap_items
from ._pricing import _ROUTE_LABELS as _ROUTE_LABELS
from ._pricing import _UNPRICED_IN_USE_UNREADABLE as _UNPRICED_IN_USE_UNREADABLE
from ._pricing import _URI_COMPONENT_SAFE as _URI_COMPONENT_SAFE
from ._pricing import _campaign_names as _campaign_names
from ._pricing import _chore_embeddings as _chore_embeddings
from ._pricing import _chore_unpriced as _chore_unpriced
from ._pricing import _chore_unpriced_models as _chore_unpriced_models
from ._pricing import _id_part as _id_part
from ._pricing import _items_embeddings as _items_embeddings
from ._pricing import _items_unpriced as _items_unpriced
from ._pricing import _items_unpriced_models as _items_unpriced_models
from ._pricing import _model_rates_href as _model_rates_href
from ._pricing import _provider_ids as _provider_ids
from ._pricing import _unpriced_in_use as _unpriced_in_use
from ._pricing import _use_label as _use_label
from ._report import _campaign_readable as _campaign_readable
from ._report import _campaign_scope as _campaign_scope
from ._report import _groups as _groups
from ._report import _library_chores as _library_chores
from ._report import _run as _run
from ._report import badge_count as badge_count
from ._report import get_todo as get_todo
from ._report import get_todo_items as get_todo_items
from ._report import live as live
from ._report import put_todo_ignored as put_todo_ignored
from ._router import router as router
from ._sources import _DESCRIBE_BASES as _DESCRIBE_BASES
from ._sources import _character_gaps as _character_gaps
from ._sources import _items_avatars as _items_avatars
from ._sources import _items_cover as _items_cover
from ._sources import _ledger_href as _ledger_href
from ._sources import _owed as _owed
from ._sources import _review_sidecars as _review_sidecars
from ._sources import _slugish as _slugish
from ._sources import _world_avatar_gaps as _world_avatar_gaps
from ._sources import _world_char_gaps as _world_char_gaps
from ._sources import _world_cover_gaps as _world_cover_gaps
from ._sources import _world_describe_counts as _world_describe_counts
from ._sources import _world_subject_gaps as _world_subject_gaps

_router.finalize()

# Captured from the original module before any responsibility leaves existed.
__all__ = [
    'BUILDERS',
    'CAMPAIGN_BUILDERS',
    'CAMPAIGN_IDS',
    'GROUP_ORDER',
    'ITEMS',
    'ITEM_CAP',
    'KNOWN',
    'LIBRARY_BUILDERS',
    'LIBRARY_IDS',
    'APIRouter',
    'HTTPException',
    'annotations',
    'badge_count',
    'continuity_candidates',
    'continuity_effective',
    'continuity_pending',
    'get_todo',
    'get_todo_items',
    'json',
    'live',
    'overview',
    'overview_read',
    'put_todo_ignored',
    'quote',
    'rates_block',
    'rates_editable',
    'readstats',
    'router',
    'store',
]
