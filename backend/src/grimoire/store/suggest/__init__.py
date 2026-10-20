"""Ephemeral scene-suggestion and scene-intent prompt builder/parser: assemble
deterministic campaign signals (a story-so-far anchor, open plot threads with
dormancy, a status-annotated cast, calendar facts at the current moment, seedable
ids), build the one-shot prompt, and parse the model's proposed openings (or, for
scene-intent, the metadata implied by the user's own typed description).
Assembly + prompt/parse only; the LLM call lives in the route (mirrors
absorb/prompt.py) and the prompt text in templates/scene_suggestions/ and
templates/scene_intent/.

The suggestion snapshot consumes the continuity projections (capstone spec
§15): the commitments, a dated timeline (`pressure.build`'s items), the driver
index (`drivers.snapshot`'s drivers), its anchors and the reviewed links
between drivers -- one pressure computation at one `now`, shared by the
timeline and the index. `build_snapshot(drivers=False)` is the intent prompt's
legacy shape: exactly the keys it always had, and no pressure or driver work.

The prompt renders that capture through `driver_view`, which caps the index
and the timeline at `DRIVER_PROMPT_CAP` while always keeping what the reader's
`Controls` pin. The instruction section of the system message never changes;
the driver contract and the controls are addenda after it, so a campaign with
nothing to drive and no control set gets the message it always had.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ... import prompts, schemas
from .. import _idea_provenance as idea_shapes
from .. import (
    birthdays,
    calendars,
    characters,
    chronicle,
    clock,
    default_prompts,
    events,
    fieldtext,
    greetings,
    overlay,
    pcs,
    playing,
)
from ..appearances import cast as appearances_cast
from ..appearances import paths as appearances_paths
from ..campaigns import paths as campaigns_paths
from ..context import layout, macros
from ..continuity import drivers as continuity_drivers
from ..continuity import effective, pressure
from ..scenes import focus as scenes_focus
from ._claims import _anchor_of as _anchor_of
from ._claims import _canon_map as _canon_map
from ._claims import _claimed_refs as _claimed_refs
from ._claims import _misses as _misses
from ._claims import _pair as _pair
from ._claims import _valid_pairs as _valid_pairs
from ._claims import claim as claim
from ._claims import order_cards as order_cards
from ._contracts import _SOONER_KINDS as _SOONER_KINDS
from ._contracts import ANCHOR_RELATIONS as ANCHOR_RELATIONS
from ._contracts import MUST_CAP as MUST_CAP
from ._contracts import MUST_KINDS as MUST_KINDS

# Explicit compatibility aliases retain the original binding and star surface.
from ._contracts import NEAR_MIN_DAYS as NEAR_MIN_DAYS
from ._contracts import NO_CONTROLS as NO_CONTROLS
from ._contracts import TEMPORAL_KINDS as TEMPORAL_KINDS
from ._contracts import TIME_MODES as TIME_MODES
from ._contracts import Controls as Controls
from ._contracts import ControlsError as ControlsError
from ._controls import _bad as _bad
from ._controls import _check_anchor as _check_anchor
from ._controls import _check_must as _check_must
from ._controls import _distinct as _distinct
from ._controls import _live_canon_or_identity as _live_canon_or_identity
from ._controls import _stale as _stale
from ._controls import resolve_controls as resolve_controls
from ._dates import _MONTH_KEY as _MONTH_KEY
from ._dates import _RELATION_RULES as _RELATION_RULES
from ._dates import _against_anchor as _against_anchor
from ._dates import _against_mode as _against_mode
from ._dates import _anchor_day as _anchor_day
from ._dates import _fixed as _fixed
from ._dates import _friendly_of as _friendly_of
from ._dates import _in_month as _in_month
from ._dates import _lower_ok as _lower_ok
from ._dates import _month_key as _month_key
from ._dates import _month_of as _month_of
from ._dates import _now_fixed as _now_fixed
from ._dates import _plugin_soft as _plugin_soft
from ._dates import _soft_provider as _soft_provider
from ._dates import check_date as check_date
from ._dates import date_normalizer as date_normalizer
from ._dates import judge_date as judge_date
from ._dates import normalize_date as normalize_date
from ._ideas import _BIRTHDAY_DAY_REF as _BIRTHDAY_DAY_REF
from ._ideas import _BOUNDED_DIGEST as _BOUNDED_DIGEST
from ._ideas import _HOLIDAY_REF as _HOLIDAY_REF
from ._ideas import _NO_ANNOTATION as _NO_ANNOTATION
from ._ideas import _PASSING_RELATIONS as _PASSING_RELATIONS
from ._ideas import _STORED_KINDS as _STORED_KINDS
from ._ideas import _actor_name as _actor_name
from ._ideas import _anchor_fields as _anchor_fields
from ._ideas import _anchor_native as _anchor_native
from ._ideas import _anchor_reading as _anchor_reading
from ._ideas import _annotate as _annotate
from ._ideas import _annotated as _annotated
from ._ideas import _classify as _classify
from ._ideas import _event_reading as _event_reading
from ._ideas import _event_record as _event_record
from ._ideas import _exists as _exists
from ._ideas import _IdeaContext as _IdeaContext
from ._ideas import _kind_of as _kind_of
from ._ideas import _month_reading as _month_reading
from ._ideas import _month_ref as _month_ref
from ._ideas import _occurrence_day as _occurrence_day
from ._ideas import _occurrence_label as _occurrence_label
from ._ideas import _occurrence_reading as _occurrence_reading
from ._ideas import _provenance_anchor as _provenance_anchor
from ._ideas import _provenance_pair as _provenance_pair
from ._ideas import _reading as _reading
from ._ideas import _record_reading as _record_reading
from ._ideas import _soft_canon as _soft_canon
from ._ideas import _soft_ledgers as _soft_ledgers
from ._ideas import _stale_reason as _stale_reason
from ._ideas import _unannotated as _unannotated
from ._ideas import _well_formed_occurrence as _well_formed_occurrence
from ._ideas import idea_provenance as idea_provenance
from ._ideas import validate_ideas as validate_ideas
from ._parsing import _anchor_card as _anchor_card
from ._parsing import _card as _card
from ._parsing import _cast_of as _cast_of
from ._parsing import _extract_json as _extract_json
from ._parsing import is_card as is_card
from ._parsing import parse_greeting_picks as parse_greeting_picks
from ._parsing import parse_intent as parse_intent
from ._parsing import parse_next_date as parse_next_date
from ._parsing import parse_output as parse_output
from ._parsing import raw_suggestions as raw_suggestions
from ._prompts import DIRECTION_LIMIT as DIRECTION_LIMIT
from ._prompts import INTENT_LIMIT as INTENT_LIMIT
from ._prompts import _intent_shape as _intent_shape
from ._prompts import build_intent_prompt as build_intent_prompt
from ._prompts import build_intent_request as build_intent_request
from ._prompts import build_prompt as build_prompt
from ._prompts import creation_defaults as creation_defaults
from ._prompts import intent_schema as intent_schema
from ._references import ref_validator as ref_validator
from ._references import token_ok as token_ok
from ._references import valid_ids as valid_ids
from ._references import valid_refs as valid_refs
from ._snapshot import _NO_READING as _NO_READING
from ._snapshot import GREETING_EXCERPT as GREETING_EXCERPT
from ._snapshot import NOTATION_MONTH_LIMIT as NOTATION_MONTH_LIMIT
from ._snapshot import _blank_calendar_facts as _blank_calendar_facts
from ._snapshot import _calendar_facts as _calendar_facts
from ._snapshot import _calendar_reads as _calendar_reads
from ._snapshot import _char_name as _char_name
from ._snapshot import _commitment_rows as _commitment_rows
from ._snapshot import _dormancy_of as _dormancy_of
from ._snapshot import _driver_capture as _driver_capture
from ._snapshot import _links_among as _links_among
from ._snapshot import _near_days as _near_days
from ._snapshot import _notation as _notation
from ._snapshot import _pressure_of as _pressure_of
from ._snapshot import _sooner_ref as _sooner_ref
from ._snapshot import _tok as _tok
from ._snapshot import build_snapshot as build_snapshot
from ._snapshot import greeting_candidates as greeting_candidates
from ._view import DRIVER_PROMPT_CAP as DRIVER_PROMPT_CAP
from ._view import _anchor_view as _anchor_view
from ._view import _index_rank as _index_rank
from ._view import _index_rows as _index_rows
from ._view import _rendered_links as _rendered_links
from ._view import _select as _select
from ._view import _timeline_rank as _timeline_rank
from ._view import _timeline_rows as _timeline_rows
from ._view import _when as _when
from ._view import driver_view as driver_view

# Preserve the original deferred module annotations and their resolved types.
__annotations__ = {'_NO_ANNOTATION': 'dict[str, Any]', '_RELATION_RULES': 'dict[str, Callable[[int, int, int | None], bool]]'}

# Captured from the original module before responsibility leaves existed.
__all__ = [
    'ANCHOR_RELATIONS',
    'DIRECTION_LIMIT',
    'DRIVER_PROMPT_CAP',
    'GREETING_EXCERPT',
    'INTENT_LIMIT',
    'MUST_CAP',
    'MUST_KINDS',
    'NEAR_MIN_DAYS',
    'NOTATION_MONTH_LIMIT',
    'NO_CONTROLS',
    'TEMPORAL_KINDS',
    'TIME_MODES',
    'Any',
    'Callable',
    'Controls',
    'ControlsError',
    'annotations',
    'appearances_cast',
    'appearances_paths',
    'birthdays',
    'build_intent_prompt',
    'build_intent_request',
    'build_prompt',
    'build_snapshot',
    'calendars',
    'campaigns_paths',
    'characters',
    'check_date',
    'chronicle',
    'claim',
    'clock',
    'continuity_drivers',
    'creation_defaults',
    'dataclass',
    'date_normalizer',
    'default_prompts',
    'driver_view',
    'effective',
    'events',
    'field',
    'fieldtext',
    'greeting_candidates',
    'greetings',
    'idea_provenance',
    'idea_shapes',
    'intent_schema',
    'is_card',
    'json',
    'judge_date',
    'layout',
    'macros',
    'normalize_date',
    'order_cards',
    'overlay',
    'parse_greeting_picks',
    'parse_intent',
    'parse_next_date',
    'parse_output',
    'pcs',
    'playing',
    'pressure',
    'prompts',
    'raw_suggestions',
    're',
    'ref_validator',
    'resolve_controls',
    'scenes_focus',
    'schemas',
    'token_ok',
    'valid_ids',
    'valid_refs',
    'validate_ideas',
]
