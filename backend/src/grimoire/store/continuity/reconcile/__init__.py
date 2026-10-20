"""The reconciliation sweep (capstone spec §11): what the whole ledger may hold
twice, and what may be finished, found after End Scene or on a refresh.

**Scores rank; they never write (§3.2).** Discovery answers "which records are
worth asking about" and nothing more. A pair clearing every floor merges
nothing, a thread past its staleness threshold is not closed, and a deadline
behind the campaign's present resolves nothing: each becomes a *finding* in
the candidate cache, and only a reader's apply writes the ledgers. `discover`
itself writes nothing to the campaign -- the persists write the cache, under
the lock; the only thing a sweep warms is the global vector cache.

**What discovery finds** (Decisions 10 and 11):

- *Pairs.* Every ref to score is paired once against every other thread and
  commitment (closed and resolved included, §11.2), scored by Slice C's
  `similarity` rules so a sweep and absorb's identity check agree about what
  "close" means. A plausible pair is kept when it is in the top
  `RECONCILE_TOP_K` of either endpoint, per type, counting only an endpoint
  whose every pair this sweep scored -- so an incremental sweep keeps what a
  full one would for the changed ref, not every plausible pair it has, and
  defers the other direction to the next full sweep. Same-type pairs are
  ``possible_duplicate``; a thread and a commitment are ``possible_relation``,
  never a duplicate (§3.6).
- *Incremental vs full.* A full sweep scores every ref; an incremental one only
  the refs whose identity hash moved since the cache's basis (or that have no
  entry). The hash is salted with the embedding space, so a space change makes
  every ref changed until it is rescored under the new one. Scoring order is
  missing, then changed, then least recently scored, then ref: a sweep cut off
  at `RECONCILE_MAX_PAIRS` leaves the refs it did not finish for the next one,
  and successive sweeps rotate through the whole ledger rather than rescoring
  the same alphabetical prefix. A pool that could not be read leaves every ref
  unrescored, since none was paired against it.
- *Touched.* An incremental sweep re-checks the refs a scene names plus those
  whose identity text moved since the basis -- compared without the space
  salt (`basis.text_hashes`), so a space change moves nothing.
- *Lifecycle.* A stale thread is nominated for closure, an overdue commitment
  (a passed due, or a reached before/by/on event) for resolution. Both are
  persisted deterministically, with or without a model.

**Embeddings** (§9.4, Decision 12) are an enhancement with a bounded cost. One
`vectors.load` over every scored text is the sweep's only read of the vector
cache; it finds the uncached texts. An incremental sweep requires the changed
refs' uncached texts, and the rest of `RECONCILE_WARM_LIMIT` goes to a window
of the other uncached texts that `embed_space.warm_window` rotates on the
campaign id plus the run's stamp -- the stamp is what makes the rotation real,
since a seed of the campaign alone hands two runs the same offset and a text
stuck there would be retried forever. `similarity.semantic` embeds in
`embeddings.BATCH` chunks under one `embeddings.TIMEOUT` deadline, saving each
chunk as it lands and forgetting off-width vectors. The cap and that window are
the run's (`EmbedBudget`): a follow-on pass embeds only what the first left.
The required texts and the window go in separate calls, and a failed required
call is retried once over a rotating proper subset of what it did not save: a
changed ref left without a vector stays changed and so stays required, so a
text the provider refuses would otherwise fail the window and every changed ref
beside it on every sweep, which is the stuck head the rotation exists to
remove. A provider failure is
mode ``failure`` and one counts-only error row; the lexical candidates stand.
While a space is configured, a ref left without a vector (a failure, or outside
this run's window) is scored lexically but not reported rescored: its basis
entry stays, so its prior pairs are kept and it is scored semantically once its
vector exists.

**Model-only nominations** (Decision 9). Two kinds of nomination carry no
deterministic signal worth showing on its own: a *temporal pair* (a commitment
whose due the calendar cannot read, beside a near event) and a *touched*
lifecycle re-check (a record the scene just moved, which cannot be stale).
Persisted without a proposal, either would flood the review after every
scene, and entirely so with no connection. So they live in `Sweep.model_only`,
go to the model, and land only in the second persist carrying its proposal;
one the model declines is cached as ``settled`` and shown to nobody. They are
filtered by verdict here, against the same `pending.Current` discovery read, so
a pair the reader dismissed or linked, or a re-check under a Keep open that
still holds, is never re-asked.

**Adjudication** (§11.2-§11.4, Decision 13) is one `decide()` per sweep, never
one per finding: one item per finding, chunked (at most
`decisions.MAX_ITEMS_PER_CALL` items to a metered call), so a sweep is a few
calls and each is bounded where one prompt over every finding grew with the
sweep. `select` chooses what it asks -- overdue resolutions, then
duplicates strongest first, then the rest, capped at `RECONCILE_MAX_CANDIDATES`
-- from the findings with no proposal, re-checking each model-only nomination
so nothing the reader dismissed or linked is sent. `build_payload` sends the
records those findings name and nothing else of the ledger: each record's line,
its last beats with their scenes, its pressure, links and people, and the
chronicle lines around them -- never a transcript. The scenes it shows are the
only evidence `proposals_of` accepts. It trusts no field of the reply:
a word outside the candidate's vocabulary, a direction the link rules refuse,
or a closure whose evidence is not a scene its item showed is ``uncertain``,
and a reply with no decodable object is None -- a failed run, not "no
proposals". A status word stands on its known evidence scene alone, with no
rationale (I4): the rationale is display text, and nothing is invented in its
place.

**A partial sweep lands.** A candidate no chunk reached (a chunk that failed,
was refused or came back with no object beside one that answered) gets no
proposal, never ``uncertain``: the sweep counts it `unanswered`, its cached
record keeps no proposal and the next sweep's `select` asks it again (I1). The
exception is a model-only nomination, which is cached only with its proposal,
so one the reply never reached is not stored at all and is nominated afresh
next time (M7). A candidate the reply reached and answered badly was read, and
is ``uncertain`` (N8).

**Two persists** (§11.1 steps 2 and 4, Decisions 7 and 8) share one campaign
lock hold. Persist 1 writes what the sweep found plus every cached record it
did not ask about again; persist 2 sets the model's proposals. Each re-judges
every record against continuity.json as it stands under that hold, so a
dismissal, merge or link that landed after discovery is never undone, and each
is fenced on the run's generation, so a newer run's cache is never overwritten.
One that would leave the cache as it was writes nothing and moves no token.

**Soft all the way down.** Pools, aging, the clock and pressure can each run
user calendar-plugin code or read a garbled ledger, so each is read through
`tolerance.call`; a source that fell back, or whose ledger or chronicle could not be
read, is reported unchecked in `Sweep.lifecycle_checked`, so a persist never
retracts a finding on the word of a source it could not read. A malformed continuity.json reads as no
decisions at all, so while it lasts discovery finds nothing (Decision 2).

The constants below are candidate-generation parameters, not truth thresholds.
Each is justified structurally, and every one is to be tuned against real
prompts later.
"""

from __future__ import annotations

# Compatibility facade. State and implementation live in responsibility leaves;
# internal fault injection targets those owners, while existing imports remain.
import hashlib
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .... import decisions, embeddings, prompts
from ... import (
    aging,
    calendars,
    chronicle,
    clock,
    embed_space,
    errors,
    fieldtext,
    locks,
    paths,
    relationships,
    revision,
    scene_ids,
    vectors,
)
from ...campaigns import paths as campaigns_paths
from ...scenes import read as scenes_read
from .. import candidates, canon, effective, involvement, pending, pressure, similarity, tolerance
from ._answers import _DIRECTED as _DIRECTED
from ._answers import _JOINS as _JOINS
from ._answers import _RELATION_OF as _RELATION_OF
from ._answers import _STATUS_OF as _STATUS_OF
from ._answers import _UNFOLDED as _UNFOLDED
from ._answers import _WAYS as _WAYS
from ._answers import DECISION_ID as DECISION_ID
from ._answers import DECISIONS as DECISIONS
from ._answers import EVIDENCE_IDS as EVIDENCE_IDS
from ._answers import EVIDENCE_SCENES as EVIDENCE_SCENES
from ._answers import LABELS as LABELS
from ._answers import PAIR_VOCABULARIES as PAIR_VOCABULARIES
from ._answers import RECONCILE_REASON_CHARS as RECONCILE_REASON_CHARS
from ._answers import _allowed as _allowed
from ._answers import _answered as _answered
from ._answers import _chosen as _chosen
from ._answers import _decide as _decide
from ._answers import _decision_item as _decision_item
from ._answers import _decision_options as _decision_options
from ._answers import _evidence as _evidence
from ._answers import _lifecycle_word as _lifecycle_word
from ._answers import _offered as _offered
from ._answers import _option_id as _option_id
from ._answers import _pair as _pair
from ._answers import _ref_of as _ref_of
from ._answers import _runs as _runs
from ._answers import _temporal_word as _temporal_word
from ._answers import build_items as build_items
from ._answers import explain as explain
from ._answers import folded as folded
from ._answers import item_scenes as item_scenes
from ._answers import proposals_of as proposals_of
from ._answers import rebuild as rebuild
from ._answers import unfolded as unfolded
from ._answers import vocabulary as vocabulary
from ._discovery import _DEADLINE_LEDGERS as _DEADLINE_LEDGERS
from ._discovery import _LIFECYCLE_OF as _LIFECYCLE_OF
from ._discovery import _OVERDUE_SOURCES as _OVERDUE_SOURCES
from ._discovery import _POOL_LEDGERS as _POOL_LEDGERS
from ._discovery import _SEMANTIC_UNAVAILABLE as _SEMANTIC_UNAVAILABLE
from ._discovery import _STALE_LEDGERS as _STALE_LEDGERS
from ._discovery import _TASK as _TASK
from ._discovery import RECONCILE_MAX_PAIRS as RECONCILE_MAX_PAIRS
from ._discovery import RECONCILE_TEMPORAL_EVENTS as RECONCILE_TEMPORAL_EVENTS
from ._discovery import RECONCILE_TOP_K as RECONCILE_TOP_K
from ._discovery import RECONCILE_WARM_LIMIT as RECONCILE_WARM_LIMIT
from ._discovery import SWEEPS as SWEEPS
from ._discovery import EmbedBudget as EmbedBudget
from ._discovery import Sweep as Sweep
from ._discovery import _chronicle_readable as _chronicle_readable
from ._discovery import _embed as _embed
from ._discovery import _identity_hash as _identity_hash
from ._discovery import _keep_basis_space as _keep_basis_space
from ._discovery import _lifecycle as _lifecycle
from ._discovery import _live_only as _live_only
from ._discovery import _near_events as _near_events
from ._discovery import _nominate as _nominate
from ._discovery import _order as _order
from ._discovery import _overdue as _overdue
from ._discovery import _pair_signals as _pair_signals
from ._discovery import _PairPool as _PairPool
from ._discovery import _pairs as _pairs
from ._discovery import _record as _record
from ._discovery import _rescored as _rescored
from ._discovery import _score_pairs as _score_pairs
from ._discovery import _semantic as _semantic
from ._discovery import _signals as _signals
from ._discovery import _stale as _stale
from ._discovery import _subjects as _subjects
from ._discovery import _temporal as _temporal
from ._discovery import _text_hash as _text_hash
from ._discovery import _top_k as _top_k
from ._discovery import _touched as _touched
from ._discovery import _touched_refs as _touched_refs
from ._discovery import _undatable as _undatable
from ._discovery import _unscored_partners as _unscored_partners
from ._discovery import discover as discover
from ._discovery import generation as generation
from ._payloads import _LETTERS as _LETTERS
from ._payloads import _TYPE_OF as _TYPE_OF
from ._payloads import RECONCILE_ACTORS as RECONCILE_ACTORS
from ._payloads import RECONCILE_BEATS as RECONCILE_BEATS
from ._payloads import RECONCILE_MAX_CANDIDATES as RECONCILE_MAX_CANDIDATES
from ._payloads import RECONCILE_RECENT_SCENES as RECONCILE_RECENT_SCENES
from ._payloads import RECONCILE_RECORD_BYTES as RECONCILE_RECORD_BYTES
from ._payloads import RECONCILE_SCENE_LINE_BYTES as RECONCILE_SCENE_LINE_BYTES
from ._payloads import _beats as _beats
from ._payloads import _campaign_date as _campaign_date
from ._payloads import _Context as _Context
from ._payloads import _count as _count
from ._payloads import _days as _days
from ._payloads import _item as _item
from ._payloads import _lifecycle_text as _lifecycle_text
from ._payloads import _number as _number
from ._payloads import _pair_text as _pair_text
from ._payloads import _pressure_text as _pressure_text
from ._payloads import _priority as _priority
from ._payloads import _recent as _recent
from ._payloads import _record_view as _record_view
from ._payloads import _scene_lines as _scene_lines
from ._payloads import _signal_text as _signal_text
from ._payloads import _when as _when
from ._payloads import build_payload as build_payload
from ._payloads import select as select
from ._payloads import snippet_line as snippet_line
from ._persistence import _GENERATION as _GENERATION
from ._persistence import _KEPT as _KEPT
from ._persistence import _RETRACTABLE as _RETRACTABLE
from ._persistence import Build as Build
from ._persistence import _carried as _carried
from ._persistence import _commit as _commit
from ._persistence import _found_basis as _found_basis
from ._persistence import _keep as _keep
from ._persistence import _rank as _rank
from ._persistence import _reasked as _reasked
from ._persistence import _result as _result
from ._persistence import _retracted as _retracted
from ._persistence import _same_basis as _same_basis
from ._persistence import _superseded as _superseded
from ._persistence import _unvoided as _unvoided
from ._persistence import persist_found as persist_found
from ._persistence import persist_investigation as persist_investigation
from ._persistence import persist_proposals as persist_proposals
from ._readouts import _DEADLINE_SOURCES as _DEADLINE_SOURCES
from ._readouts import _NO_DEADLINE as _NO_DEADLINE
from ._readouts import _live_scenes as _live_scenes
from ._readouts import _play_key as _play_key
from ._readouts import _pressure_by_ref as _pressure_by_ref
from ._readouts import _reading as _reading
from ._readouts import play_order as play_order
from ._readouts import pressure_by_ref as pressure_by_ref

# Original star-import surface, captured before extraction.
__all__ = [
    'DECISIONS',
    'DECISION_ID',
    'EVIDENCE_IDS',
    'EVIDENCE_SCENES',
    'LABELS',
    'PAIR_VOCABULARIES',
    'RECONCILE_ACTORS',
    'RECONCILE_BEATS',
    'RECONCILE_MAX_CANDIDATES',
    'RECONCILE_MAX_PAIRS',
    'RECONCILE_REASON_CHARS',
    'RECONCILE_RECENT_SCENES',
    'RECONCILE_RECORD_BYTES',
    'RECONCILE_SCENE_LINE_BYTES',
    'RECONCILE_TEMPORAL_EVENTS',
    'RECONCILE_TOP_K',
    'RECONCILE_WARM_LIMIT',
    'SWEEPS',
    'Any',
    'Build',
    'Callable',
    'Counter',
    'EmbedBudget',
    'Iterable',
    'Iterator',
    'Literal',
    'Sequence',
    'Sweep',
    'aging',
    'annotations',
    'build_items',
    'build_payload',
    'calendars',
    'campaigns_paths',
    'candidates',
    'canon',
    'chronicle',
    'clock',
    'dataclass',
    'decisions',
    'discover',
    'effective',
    'embed_space',
    'embeddings',
    'errors',
    'explain',
    'fieldtext',
    'folded',
    'generation',
    'hashlib',
    'involvement',
    'item_scenes',
    'locks',
    'paths',
    'pending',
    'persist_found',
    'persist_investigation',
    'persist_proposals',
    'play_order',
    'pressure',
    'pressure_by_ref',
    'prompts',
    'proposals_of',
    're',
    'rebuild',
    'relationships',
    'revision',
    'scene_ids',
    'scenes_read',
    'select',
    'similarity',
    'snippet_line',
    'time',
    'tolerance',
    'unfolded',
    'vectors',
    'vocabulary',
]

# Preserve the original deferred module metadata without redeclaring exports.
__annotations__ = {'DECISIONS': 'dict[str, tuple[str, ...]]', 'LABELS': 'dict[str, str]'}
