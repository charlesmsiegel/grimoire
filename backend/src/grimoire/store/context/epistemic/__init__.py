"""Per-actor access to retrieved history: the deterministic stage (11-C1).

09 retrieves scene evidence for a turn; this module says, for the actor the
turn is written for, which of it that actor has and how. Each excerpt post is
classed by the first rule that settles it (spec 11 §4.2): a user override
(`store/knowledge.py`), authorship, a presence interval, the scene lists, or
nothing. A summary or title is never shown to an actor on presence alone (S1):
absorb writes it from the narrator's chair, so only a whole-scene grant whose
fingerprint still holds can vouch for it.

Pure and read-only. `classify` takes one best-effort hold
(`locks.best_effort_campaign_lock`, `assemble._assemble`'s policy), reads the
presence, override and chronicle files once and each source scene once, calls
no model and writes nothing, so it is declared in no lock list. (The cast's
names and each scene's player label re-read `appearances.json` through their
own helpers; they feed R2' and re-matching, and a stale read only narrows.) Every post 09 handed over is re-matched against this read before a
rule sees it, because a cut between retrieval and classification renumbers
posts; whatever no longer matches is dropped for everyone (`moved`).

The rule this module exists to keep: every failure narrows. An unreadable
file, an override that cannot be checked, a lock not acquired, a group that
cannot be expanded -- each makes less visible to an actor, never more. The
narrator is never narrowed by any of it: the narrator sees what 09 returned.

11-S3 calls it on what each compose step's retrieval returned
(`routes/history_recall.gather_view`), and compose renders only what the view
allows (`context/history.parts`). The basis codes are inspector data; they
never reach a prompt.
"""

from __future__ import annotations

# Compatibility facade. State and implementation live in responsibility leaves;
# internal fault injection targets those owners, while existing imports remain.
import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace

from ... import chronicle, export, knowledge, knowledge_scene, locks, pins, responses
from ...appearances import cast as appearances_cast
from ...appearances import paths as appearances_paths
from ...history import budget as history_budget
from ...history import model
from ...regex import view as regex_view
from ...scenes import identity as scenes_identity
from ...scenes import paths as scenes_paths
from ...scenes import read as scenes_read
from ...scenes import serialize as scenes_serialize
from ...searchdocs import expand as searchdocs_expand
from ._classification import _GONE as _GONE
from ._classification import _READ as _READ
from ._classification import _UNREADABLE as _UNREADABLE
from ._classification import _UNVERIFIABLE as _UNVERIFIABLE
from ._classification import _actor_header as _actor_header
from ._classification import _aggregate as _aggregate
from ._classification import _answered as _answered
from ._classification import _Classes as _Classes
from ._classification import _classes as _classes
from ._classification import _classify_item as _classify_item
from ._classification import _current_sid as _current_sid
from ._classification import _Env as _Env
from ._classification import _group_withholds as _group_withholds
from ._classification import _guarded as _guarded
from ._classification import _intervals as _intervals
from ._classification import _keyed as _keyed
from ._classification import _local as _local
from ._classification import _Overrides as _Overrides
from ._classification import _overrides as _overrides
from ._classification import _own_classes as _own_classes
from ._classification import _pc_spoke as _pc_spoke
from ._classification import _post_class as _post_class
from ._classification import _presence as _presence
from ._classification import _r1 as _r1
from ._classification import _reaches as _reaches
from ._classification import _read_all as _read_all
from ._classification import _read_scene as _read_scene
from ._classification import _read_source as _read_source
from ._classification import _redacted as _redacted
from ._classification import _rematch as _rematch
from ._classification import _runs as _runs
from ._classification import _Scene as _Scene
from ._classification import _scene_class as _scene_class
from ._classification import _slices as _slices
from ._classification import _Source as _Source
from ._classification import _stood as _stood
from ._classification import _summary_slice as _summary_slice
from ._classification import _unclassified as _unclassified
from ._classification import _withheld_within as _withheld_within
from ._classification import classify as classify
from ._classification import classify_evidence as classify_evidence
from ._classification import eligible as eligible
from ._classification import narrator_view as narrator_view
from ._classification import present_roster as present_roster
from ._contracts import _ACTOR_PREFIX as _ACTOR_PREFIX
from ._contracts import _ELLIPSIS as _ELLIPSIS
from ._contracts import _NO_DIGEST as _NO_DIGEST
from ._contracts import _STATUS as _STATUS
from ._contracts import ACCESS as ACCESS
from ._contracts import BASES as BASES
from ._contracts import DECIDED_ACCESS as DECIDED_ACCESS
from ._contracts import DECIDED_BASES as DECIDED_BASES
from ._contracts import ELIGIBLE_BASES as ELIGIBLE_BASES
from ._contracts import NARRATOR as NARRATOR
from ._contracts import STATES as STATES
from ._contracts import VISIBLE as VISIBLE
from ._contracts import Classified as Classified
from ._contracts import EpistemicView as EpistemicView
from ._contracts import Perspective as Perspective
from ._contracts import Slice as Slice
from ._contracts import log as log
from ._records import RECORD_VERSION as RECORD_VERSION
from ._records import _header_of as _header_of
from ._records import _ints as _ints
from ._records import _item_of as _item_of
from ._records import _post_of as _post_of
from ._records import _post_record as _post_record
from ._records import _scene_ref_of as _scene_ref_of
from ._records import _slice_of as _slice_of
from ._records import _strings as _strings
from ._records import from_record as from_record
from ._records import to_record as to_record

# Original star-import surface, captured before extraction.
__all__ = [
    'ACCESS',
    'BASES',
    'DECIDED_ACCESS',
    'DECIDED_BASES',
    'ELIGIBLE_BASES',
    'NARRATOR',
    'RECORD_VERSION',
    'STATES',
    'VISIBLE',
    'Classified',
    'EpistemicView',
    'Mapping',
    'Perspective',
    'Sequence',
    'Slice',
    'annotations',
    'appearances_cast',
    'appearances_paths',
    'asdict',
    'chronicle',
    'classify',
    'classify_evidence',
    'dataclass',
    'eligible',
    'export',
    'field',
    'from_record',
    'history_budget',
    'knowledge',
    'knowledge_scene',
    'locks',
    'log',
    'logging',
    'model',
    'narrator_view',
    'pins',
    'present_roster',
    'regex_view',
    'replace',
    'responses',
    'scenes_identity',
    'scenes_paths',
    'scenes_read',
    'scenes_serialize',
    'searchdocs_expand',
    'to_record',
]
