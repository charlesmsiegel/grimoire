"""The decision contract: what `inference.decide` is asked, and what it answers.

A decision is a closed question about a piece of text -- is this scene over,
which of these characters speaks next, how far has this reply drifted from its
voice -- answered by a value drawn from a set the caller fixed in advance,
never by prose a call site has to pick apart. This module holds that contract
and everything about it that is pure: the request and result types, the
request's validation, the JSON Schema of one batch, the tolerant parser that
reads a reply back, and the chunking of a long batch -- and, for slice H's
native backends, the one mapping both adapters share: how an item is lowered
to the three types both endpoints have and read back (`native_form`,
`native_lift`), the reserved none an
`allow_none` choice adds (`native_choice_keys`) and how a wire key is read
back (`native_key`), what an endpoint cannot carry (`native_gap`), how a
provider's report becomes an `Answer` (`native_answer`) and a reply an
`ItemResult` (`native_result`),
the capture's record of a call (`outcome`) and the structured rendering of a
native result (`render`) -- the one grouping rule for any ordered signal,
`tiers`, which makes a tie explicit rather than breaking it -- and what hands
an answered item to a second opinion: an answer's key (`answer_key`), its
margin over its rival in its own report (`margin`) and the triggers an item
meets (`triggers`, roadmap 01d-C2a) -- and what one did with it
(`Escalation`, `ResolverReply`, 01d-C2b).

It is a gateway leaf on purpose, and imports nothing from the package but
the standard-library leaf `schemas` (spec §7.4, ruling 15; 01f-C3, whose
portable subset, budgets and reader it shares): the operation itself is `grimoire.inference`, and slice H's
native adapters are gateway modules that must normalise into these types
without reaching the store.

Three rules the rest of the module follows:

- **An answer nobody gave is `None` with a reason, never a default** (§12). The
  reasons are a closed vocabulary (`REASONS`); `unreadable` may carry a
  `detail` sub-reason, which is not a new reason. There are three details:
  `NOT_AN_OPTION` (a choice answered with a value naming no option),
  `NO_OBJECT` (the reply held no JSON object at all) and `NO_ITEM` (it held
  one, but nothing in it reads as this item). `was_read` says which side of
  that line an answer is on.
- **The schema stays inside what both providers document** (`schemas.check`,
  which every batch schema passes). OpenAI's strict
  mode and Anthropic's `output_config.format` accept different subsets of JSON
  Schema, so a batch schema uses only `type` (object, array, string, boolean,
  integer, null), `enum`, `anyOf`, `items`, `required`, `properties` and
  `additionalProperties: false`. Anthropic refuses numeric bounds, so a score
  is an integer `enum`; a nullable choice is `anyOf` with a null branch, never
  a type array. Anthropic also refuses array constraints beyond a `minItems`
  of 0 or 1, and OpenAI documents no `uniqueItems`, so a ranking is an array
  of an `enum` with no bounds at all, and the parser enforces its count and
  uniqueness.
- **The parser never raises.** It answers every item it was handed, so one
  unreadable reply costs the answers it held and nothing more.
"""

from __future__ import annotations

import json as json
import logging as logging
import math as math
import re as re
from collections.abc import Callable as Callable
from collections.abc import Collection as Collection
from collections.abc import Mapping as Mapping
from collections.abc import Sequence as Sequence
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from dataclasses import replace as replace
from typing import Any as Any
from typing import ClassVar as ClassVar
from typing import Final as Final

from .. import schemas as schemas
from ._models import (
    _PRIORITY as _PRIORITY,
)
from ._models import (
    BACKENDS as BACKENDS,
)
from ._models import (
    ENUM_STRING_CHARS_ABOVE as ENUM_STRING_CHARS_ABOVE,
)
from ._models import (
    ESCALATION_OUTCOMES as ESCALATION_OUTCOMES,
)
from ._models import (
    FAILED_DECLINED as FAILED_DECLINED,
)
from ._models import (
    FAILED_NO_RESULT as FAILED_NO_RESULT,
)
from ._models import (
    HOP_ESCALATION as HOP_ESCALATION,
)
from ._models import (
    JOINT_SEP as JOINT_SEP,
)
from ._models import (
    LEGACY_RATIONALE_KEYS as LEGACY_RATIONALE_KEYS,
)
from ._models import (
    MASS_TIE as MASS_TIE,
)
from ._models import (
    MAX_ENUM_STRING_CHARS as MAX_ENUM_STRING_CHARS,
)
from ._models import (
    MAX_ENUM_VALUES as MAX_ENUM_VALUES,
)
from ._models import (
    MAX_ITEMS_PER_CALL as MAX_ITEMS_PER_CALL,
)
from ._models import (
    MAX_LEVELS as MAX_LEVELS,
)
from ._models import (
    MAX_OPTIONS as MAX_OPTIONS,
)
from ._models import (
    MAX_RANK_CANDIDATES as MAX_RANK_CANDIDATES,
)
from ._models import (
    MAX_SCHEMA_PROPERTIES as MAX_SCHEMA_PROPERTIES,
)
from ._models import (
    MAX_SCHEMA_STRING_CHARS as MAX_SCHEMA_STRING_CHARS,
)
from ._models import (
    MAX_SELECT_OPTIONS as MAX_SELECT_OPTIONS,
)
from ._models import (
    MIN_LEVELS as MIN_LEVELS,
)
from ._models import (
    MIN_OPTIONS as MIN_OPTIONS,
)
from ._models import (
    MIN_OPTIONS_WITH_NONE as MIN_OPTIONS_WITH_NONE,
)
from ._models import (
    MIN_RANK_CANDIDATES as MIN_RANK_CANDIDATES,
)
from ._models import (
    MIN_SELECT_OPTIONS as MIN_SELECT_OPTIONS,
)
from ._models import (
    NATIVE_BACKEND as NATIVE_BACKEND,
)
from ._models import (
    NATIVE_MAX_OPTIONS as NATIVE_MAX_OPTIONS,
)
from ._models import (
    NATIVE_NONE as NATIVE_NONE,
)
from ._models import (
    NATIVE_NONE_TEXT as NATIVE_NONE_TEXT,
)
from ._models import (
    NATIVE_SELECT_TEXT as NATIVE_SELECT_TEXT,
)
from ._models import (
    NO_ITEM as NO_ITEM,
)
from ._models import (
    NO_OBJECT as NO_OBJECT,
)
from ._models import (
    NONE_KEY as NONE_KEY,
)
from ._models import (
    NOT_AN_OPTION as NOT_AN_OPTION,
)
from ._models import (
    REASONS as REASONS,
)
from ._models import (
    RESERVED_IDS as RESERVED_IDS,
)
from ._models import (
    SKIPPED_CAP as SKIPPED_CAP,
)
from ._models import (
    SKIPPED_DEAD_CONNECTION as SKIPPED_DEAD_CONNECTION,
)
from ._models import (
    SKIPPED_INCAPABLE as SKIPPED_INCAPABLE,
)
from ._models import (
    SKIPPED_SAME_MODEL as SKIPPED_SAME_MODEL,
)
from ._models import (
    STRUCTURED_BACKEND as STRUCTURED_BACKEND,
)
from ._models import (
    TRIGGERS as TRIGGERS,
)
from ._models import (
    UNSTATED as UNSTATED,
)
from ._models import (
    Answer as Answer,
)
from ._models import (
    CallRecord as CallRecord,
)
from ._models import (
    Choice as Choice,
)
from ._models import (
    DecideRequestError as DecideRequestError,
)
from ._models import (
    Decision as Decision,
)
from ._models import (
    Escalation as Escalation,
)
from ._models import (
    Item as Item,
)
from ._models import (
    ItemResult as ItemResult,
)
from ._models import (
    Joint as Joint,
)
from ._models import (
    MultiSelect as MultiSelect,
)
from ._models import (
    NativeQuestion as NativeQuestion,
)
from ._models import (
    Option as Option,
)
from ._models import (
    Pair as Pair,
)
from ._models import (
    Predicate as Predicate,
)
from ._models import (
    Question as Question,
)
from ._models import (
    Rank as Rank,
)
from ._models import (
    Ranking as Ranking,
)
from ._models import (
    ResolverReply as ResolverReply,
)
from ._models import (
    Score as Score,
)
from ._models import (
    Trigger as Trigger,
)
from ._models import (
    _finite as _finite,
)
from ._models import (
    _probability as _probability,
)
from ._models import (
    joint_choice as joint_choice,
)
from ._models import (
    joint_key as joint_key,
)
from ._models import (
    kinds as kinds,
)

# Explicit aliases preserve the former module surface; implementation lives in its owners.
from ._models import (
    log as log,
)
from ._models import (
    none_text as none_text,
)
from ._models import (
    split_joint as split_joint,
)
from ._native import (
    _UNOFFERED as _UNOFFERED,
)
from ._native import (
    Lift as Lift,
)
from ._native import (
    _argmax as _argmax,
)
from ._native import (
    _asked_as as _asked_as,
)
from ._native import (
    _distribution as _distribution,
)
from ._native import (
    _expected as _expected,
)
from ._native import (
    _from_distribution as _from_distribution,
)
from ._native import (
    _legal_keys as _legal_keys,
)
from ._native import (
    _lift_rank as _lift_rank,
)
from ._native import (
    _lift_select as _lift_select,
)
from ._native import (
    _lowered_ids as _lowered_ids,
)
from ._native import (
    _native_choice as _native_choice,
)
from ._native import (
    _native_predicate as _native_predicate,
)
from ._native import (
    _native_score as _native_score,
)
from ._native import (
    _pointwise_read as _pointwise_read,
)
from ._native import (
    _Read as _Read,
)
from ._native import (
    native_answer as native_answer,
)
from ._native import (
    native_choice_keys as native_choice_keys,
)
from ._native import (
    native_form as native_form,
)
from ._native import (
    native_gap as native_gap,
)
from ._native import (
    native_key as native_key,
)
from ._native import (
    native_lift as native_lift,
)
from ._native import (
    native_questions as native_questions,
)
from ._native import (
    native_result as native_result,
)
from ._reports import (
    _filtered as _filtered,
)
from ._reports import (
    _present as _present,
)
from ._reports import (
    _recorded as _recorded,
)
from ._reports import (
    _rendered as _rendered,
)
from ._reports import (
    _threshold as _threshold,
)
from ._reports import (
    answer_key as answer_key,
)
from ._reports import (
    head_first as head_first,
)
from ._reports import (
    head_marginal as head_marginal,
)
from ._reports import (
    margin as margin,
)
from ._reports import (
    outcome as outcome,
)
from ._reports import (
    regrouped as regrouped,
)
from ._reports import (
    render as render,
)
from ._reports import (
    tiers as tiers,
)
from ._reports import (
    triggers as triggers,
)
from ._schemas import (
    _SEPARATORS as _SEPARATORS,
)
from ._schemas import (
    _bound as _bound,
)
from ._schemas import (
    _check_choice as _check_choice,
)
from ._schemas import (
    _check_joint as _check_joint,
)
from ._schemas import (
    _check_joint_option as _check_joint_option,
)
from ._schemas import (
    _check_offers as _check_offers,
)
from ._schemas import (
    _check_question as _check_question,
)
from ._schemas import (
    _check_rank as _check_rank,
)
from ._schemas import (
    _check_select as _check_select,
)
from ._schemas import (
    _enum_count as _enum_count,
)
from ._schemas import (
    _nullable as _nullable,
)
from ._schemas import (
    _obj as _obj,
)
from ._schemas import (
    _question_schema as _question_schema,
)
from ._schemas import (
    chunks as chunks,
)
from ._schemas import (
    enum_values as enum_values,
)
from ._schemas import (
    normalise as normalise,
)
from ._schemas import (
    offerable as offerable,
)
from ._schemas import (
    schema as schema,
)
from ._schemas import (
    schema_chars as schema_chars,
)
from ._schemas import (
    schema_properties as schema_properties,
)
from ._schemas import (
    validate as validate,
)
from ._structured import (
    _NO_ITEM as _NO_ITEM,
)
from ._structured import (
    _NO_OBJECT as _NO_OBJECT,
)
from ._structured import (
    _NOT_AN_OPTION as _NOT_AN_OPTION,
)
from ._structured import (
    _UNREADABLE as _UNREADABLE,
)
from ._structured import (
    _flattened as _flattened,
)
from ._structured import (
    _foreign_index as _foreign_index,
)
from ._structured import (
    _item_object as _item_object,
)
from ._structured import (
    _lift_joint as _lift_joint,
)
from ._structured import (
    _option_named as _option_named,
)
from ._structured import (
    _read as _read,
)
from ._structured import (
    _read_choice as _read_choice,
)
from ._structured import (
    _read_listed as _read_listed,
)
from ._structured import (
    _read_rank as _read_rank,
)
from ._structured import (
    _read_select as _read_select,
)
from ._structured import (
    find_object as find_object,
)
from ._structured import (
    held_no_object as held_no_object,
)
from ._structured import (
    parse as parse,
)
from ._structured import (
    unanswered as unanswered,
)
from ._structured import (
    was_read as was_read,
)

# Preserve deferred module annotations; values and their types have one owner.
__annotations__ = {
    'UNSTATED': 'Final',
    '_UNOFFERED': 'Final',
    'TRIGGERS': 'tuple[str, ...]',
    'ESCALATION_OUTCOMES': 'tuple[str, ...]',
}


__all__ = [
    'BACKENDS',
    'ENUM_STRING_CHARS_ABOVE',
    'ESCALATION_OUTCOMES',
    'FAILED_DECLINED',
    'FAILED_NO_RESULT',
    'HOP_ESCALATION',
    'JOINT_SEP',
    'LEGACY_RATIONALE_KEYS',
    'MASS_TIE',
    'MAX_ENUM_STRING_CHARS',
    'MAX_ENUM_VALUES',
    'MAX_ITEMS_PER_CALL',
    'MAX_LEVELS',
    'MAX_OPTIONS',
    'MAX_RANK_CANDIDATES',
    'MAX_SCHEMA_PROPERTIES',
    'MAX_SCHEMA_STRING_CHARS',
    'MAX_SELECT_OPTIONS',
    'MIN_LEVELS',
    'MIN_OPTIONS',
    'MIN_OPTIONS_WITH_NONE',
    'MIN_RANK_CANDIDATES',
    'MIN_SELECT_OPTIONS',
    'NATIVE_BACKEND',
    'NATIVE_MAX_OPTIONS',
    'NATIVE_NONE',
    'NATIVE_NONE_TEXT',
    'NATIVE_SELECT_TEXT',
    'NONE_KEY',
    'NOT_AN_OPTION',
    'NO_ITEM',
    'NO_OBJECT',
    'REASONS',
    'RESERVED_IDS',
    'SKIPPED_CAP',
    'SKIPPED_DEAD_CONNECTION',
    'SKIPPED_INCAPABLE',
    'SKIPPED_SAME_MODEL',
    'STRUCTURED_BACKEND',
    'TRIGGERS',
    'UNSTATED',
    'Answer',
    'Any',
    'CallRecord',
    'Callable',
    'Choice',
    'ClassVar',
    'Collection',
    'DecideRequestError',
    'Decision',
    'Escalation',
    'Final',
    'Item',
    'ItemResult',
    'Joint',
    'Lift',
    'Mapping',
    'MultiSelect',
    'NativeQuestion',
    'Option',
    'Pair',
    'Predicate',
    'Question',
    'Rank',
    'Ranking',
    'ResolverReply',
    'Score',
    'Sequence',
    'Trigger',
    'annotations',
    'answer_key',
    'chunks',
    'dataclass',
    'enum_values',
    'field',
    'find_object',
    'head_first',
    'head_marginal',
    'held_no_object',
    'joint_choice',
    'joint_key',
    'json',
    'kinds',
    'log',
    'logging',
    'margin',
    'math',
    'native_answer',
    'native_choice_keys',
    'native_form',
    'native_gap',
    'native_key',
    'native_lift',
    'native_questions',
    'native_result',
    'none_text',
    'normalise',
    'offerable',
    'outcome',
    'parse',
    're',
    'regrouped',
    'render',
    'replace',
    'schema',
    'schema_chars',
    'schema_properties',
    'schemas',
    'split_joint',
    'tiers',
    'triggers',
    'unanswered',
    'validate',
    'was_read',
]
