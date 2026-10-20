"""Hand edits to the continuity ledger.

`GET /campaigns/{cid}/ledger` (in `campaigns/_ledger.py`) has always been able to *show*
what a campaign owes; until this module nothing could change any of it by hand.
The only writer of plot.json, commitments.json, facts.json and relationships.json
was `absorb/apply.py`, reached through `PUT /chronicle` — so a thread the model
never noticed had closed stayed open forever, a commitment it invented could not
be removed, and a mistyped fact stood as a dated truth.

Three rules hold across every route here.

**One lock, the absorb pass's own.** Every write takes
`locks.campaign_lock(cid)`, which is the lock `PUT /chronicle` holds across its
whole record-then-apply sequence. So a hand edit cannot interleave with a save
landing and read a ledger that is half updated. The lock is reentrant, so the
store mutators that take it themselves are safe to call inside.

**Every write is journalled as `manual`, and journalled writes are
reversible.** `store/undo.py`'s `journalled()` was written for exactly this —
"a hand edit made outside the absorb pipeline" — and snapshots the record
before the write and seals it after. So a hand edit is accountable rather than
being the one kind of change a campaign cannot explain: it lands in
`journal.json` and shows up in the play view's Changes panel under History,
which already labels a `manual` row "edited by hand" and offers the same Undo
an absorb's edits get.

Best-effort, and deliberately so — `journalled` swallows its own failure and
that policy is older than this module: the write has already happened by the
time the journal is appended, and losing the history of an edit is a smaller
harm than 500-ing an edit that landed. So the guarantee is "a write that was
journalled can be reversed", not "a write cannot land unjournalled". The fact
routes below hold to the same rule rather than inventing a stricter one they
could not keep.

Not in the ledger's own Recent-changes section, which is a different log:
`store/changes.py` is the ROLLING view — one entry per record, replaced by the
next write-back, answering "what does this record's latest absorb say". A hand
edit deliberately does not write it, because that log is about what the pass
extracted and a correction is not an extraction.

**Only records, never logs.** The relationship history and the change log record
what happened; there is nothing here that edits either, because editing a log
falsifies history rather than correcting state. Undo is how something in them is
reversed, and it already exists.

The fact routes carry the rule `store/facts.py` argues and
`test_absorb_writer_guard.py` enforces: grimoire never edits a fact, the user
may. `PUT .../facts/{fid}` is that edit, and it is the only caller of
`facts.set_text` in the app.
"""

from __future__ import annotations

import json
import logging
from typing import NamedTuple

from fastapi import APIRouter, HTTPException

from ... import store
from ...store.continuity import doc as continuity_doc
from ...store.continuity import effective as continuity_effective
from ...store.continuity import review as continuity_review
from ...store.regex import view as regex_view
from ...store.scenes import identity as scenes_identity
from ..models import (
    ChronicleLineSave,
    CommitmentSave,
    FactRecord,
    FactRetire,
    FactSave,
    KnowledgeEdit,
    KnowledgeFingerprint,
    KnowledgePost,
    KnowledgeSave,
    RelationshipSave,
    ThreadSave,
)

# Explicit compatibility aliases retain the original binding and star surface.
from . import _router as _router
from ._knowledge import _edited as _edited
from ._knowledge import _listed_posts as _listed_posts
from ._knowledge import _restamped as _restamped
from ._knowledge import _scene_view as _scene_view
from ._knowledge import _SceneView as _SceneView
from ._knowledge import _stale_reason as _stale_reason
from ._knowledge import delete_knowledge as delete_knowledge
from ._knowledge import get_knowledge as get_knowledge
from ._knowledge import get_knowledge_scene as get_knowledge_scene
from ._knowledge import post_knowledge as post_knowledge
from ._knowledge import put_knowledge as put_knowledge
from ._knowledge_sources import _audience_now as _audience_now
from ._knowledge_sources import _bad as _bad
from ._knowledge_sources import _busy as _busy
from ._knowledge_sources import _fingerprinted as _fingerprinted
from ._knowledge_sources import _knowledge_audience as _knowledge_audience
from ._knowledge_sources import _knowledge_subject as _knowledge_subject
from ._knowledge_sources import _knowledge_write as _knowledge_write
from ._knowledge_sources import _malformed as _malformed
from ._knowledge_sources import _scene_posts as _scene_posts
from ._knowledge_sources import _ScenePosts as _ScenePosts
from ._knowledge_sources import _subject_form as _subject_form
from ._knowledge_sources import _visible as _visible
from ._knowledge_sources import _whole_shown as _whole_shown
from ._movement import _closure_scene as _closure_scene
from ._movement import _stored_scene as _stored_scene
from ._movement import move_commitment as move_commitment
from ._movement import move_thread as move_thread
from ._policy import _PARTIAL_DELETE as _PARTIAL_DELETE
from ._policy import _campaign_or_404 as _campaign_or_404
from ._policy import _label as _label
from ._policy import _live_target as _live_target
from ._policy import _refuse_unsafe_delete as _refuse_unsafe_delete
from ._policy import forget_or_partial as forget_or_partial
from ._records import delete_commitment as delete_commitment
from ._records import delete_fact as delete_fact
from ._records import delete_relationship as delete_relationship
from ._records import delete_thread as delete_thread
from ._records import post_commitment as post_commitment
from ._records import post_fact as post_fact
from ._records import post_fact_retire as post_fact_retire
from ._records import post_thread as post_thread
from ._records import put_chronicle_line as put_chronicle_line
from ._records import put_commitment as put_commitment
from ._records import put_fact as put_fact
from ._records import put_relationship as put_relationship
from ._records import put_thread as put_thread
from ._router import router as router

log = logging.getLogger('grimoire.routes.ledger')

_router.finalize()

# Captured from the original module before any responsibility leaves existed.
__all__ = [
    'APIRouter',
    'ChronicleLineSave',
    'CommitmentSave',
    'FactRecord',
    'FactRetire',
    'FactSave',
    'HTTPException',
    'KnowledgeEdit',
    'KnowledgeFingerprint',
    'KnowledgePost',
    'KnowledgeSave',
    'NamedTuple',
    'RelationshipSave',
    'ThreadSave',
    'annotations',
    'continuity_doc',
    'continuity_effective',
    'continuity_review',
    'delete_commitment',
    'delete_fact',
    'delete_knowledge',
    'delete_relationship',
    'delete_thread',
    'forget_or_partial',
    'get_knowledge',
    'get_knowledge_scene',
    'json',
    'log',
    'logging',
    'move_commitment',
    'move_thread',
    'post_commitment',
    'post_fact',
    'post_fact_retire',
    'post_knowledge',
    'post_thread',
    'put_chronicle_line',
    'put_commitment',
    'put_fact',
    'put_knowledge',
    'put_relationship',
    'put_thread',
    'regex_view',
    'router',
    'scenes_identity',
    'store',
]
