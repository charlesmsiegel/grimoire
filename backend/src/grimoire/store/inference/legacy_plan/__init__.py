"""The one planner for the legacy settings layout (inference slice I).

A store written before the roles format keeps `active_connection_id`,
`fallback_connection_id`, `embeddings_*`, one `route_<k>` / `preset_<k>` pair
per legacy route, and -- on each connection -- the model fields (`model`,
`sampler_preset`, `vision`, `prefill`, `post_process`, `reasoning_effort`)
that format 2 keeps elsewhere. This module is what those mean in the new
layout, planned once per scope as a `Plan`:

- `mapped`: the legacy keys in the new vocabulary -- roles, fallbacks, pins
  and route presets -- as the migration persists them (`migrate`, steps 5-7
  and 8), byte for byte what it has always written (N2). The mapping itself
  is `global_mapping` / `campaign_mapping` (once `translate`'s, which slice I
  deleted).
- `facts`: each connection's legacy `vision`, `prefill` and `post_process`
  (where they differ from the defaults) as its model's facts -- the values
  the migration's step 3 copies (`stated`), in memory.
- `repoint` and `presets`: the derived reasoning presets (ruling 4). A
  selection slot on an `openai_compatible` GLM provider whose legacy
  `reasoning_effort` is one a preset can carry (`REPRESENTABLE`) is pointed at
  a preset holding its own preset's params plus that effort, so its wire does
  not change once the legacy field stops being read. The migration never
  persists a repoint; retirement does, after writing the presets.
- `notes`: what no preset can carry (ruling 5) -- a route-level preset that
  sets no reasoning effort over such a provider, which is shared with the
  route's fallback and so is left alone (ratification item 3).

A plan reads through the lookup and the preset reader it is handed, plus the
store's connection listing for the facts and each model's cached catalog and
facts for the Embedding role (`legacy_embeds`). The planner itself writes no
setting. Its connection reads do not promise that: every one of them
(`read_connection_raw`, `read_connection_strict`, `list_connections`) runs
`llm_connections.ensure_migrated()` first, which on a store whose
connections were never seeded writes `llm_connections/` and can write
`active_connection_id` into `config.md` -- the format-1 seeding every
connection read has always done. A scope carrying `RETIRED_KEY` beside the
current format marker plans nothing and reads nothing.

How strictly a plan reads is its lookup's (`lookup(mode=...)`): play reads
fail-soft, as the translation always has; the migration and retirement read
strictly, and an unreadable file raises rather than being planned as absent.

Trim rule, kept from the legacy cascade: `route_*`, `preset_*` and
`embeddings_*` values are stripped; `active_connection_id` and
`fallback_connection_id` are used raw (so a padded id is later rejected by the
existence check exactly as it always was). A route naming a connection that
does not exist is still mapped, to a pin on that id: walking past a dangling
choice is the cascade's job, not this module's.

Play reads the legacy layout through `overlay` alone, in memory: the global
and campaign settings as format 2 sees them (`mapped`, then `repoint`), the
derived presets as virtual presets, the facts overlay and the notes. Nothing
on that path writes; the migration persists `mapped`, and retirement the
rest. `resolve` makes the one call (`resolve._overlay`).

A leaf of `store/inference/`: it imports neither `resolve` nor `migrate`,
which both read the legacy layout through it.
"""

from __future__ import annotations

# Compatibility facade. State and implementation live in responsibility leaves;
# internal fault injection targets those owners, while existing imports remain.
import functools
import hashlib
import json
from collections.abc import Callable, Collection, Iterator, Mapping
from typing import Literal, NamedTuple

from .... import llm_reasoning, llm_sampling
from ... import config, llm_connections, locks, paths, routing, sampler_presets
from ... import inference_keys as keys
from ... import inference_retired as retired
from .. import capabilities, cascade, facts, providers
from ._contracts import GLOBAL_SCOPE as GLOBAL_SCOPE
from ._contracts import OWNED_GLOBAL_KEYS as OWNED_GLOBAL_KEYS
from ._contracts import REPRESENTABLE as REPRESENTABLE
from ._contracts import RETIRED_KEY as RETIRED_KEY
from ._contracts import SHARED_PRESET_KEYS as SHARED_PRESET_KEYS
from ._contracts import Derived as Derived
from ._contracts import Lookup as Lookup
from ._contracts import OverlayError as OverlayError
from ._contracts import Plan as Plan
from ._contracts import PresetRead as PresetRead
from ._contracts import _retired as _retired
from ._contracts import _settled as _settled
from ._contracts import campaign_scope as campaign_scope
from ._contracts import empty as empty
from ._contracts import is_retired as is_retired
from ._contracts import planned as planned
from ._mapping import _GUESSES as _GUESSES
from ._mapping import _SOFT_UNREADABLE as _SOFT_UNREADABLE
from ._mapping import _cannot_embed as _cannot_embed
from ._mapping import _embed_endpoint as _embed_endpoint
from ._mapping import _facts_overlay as _facts_overlay
from ._mapping import _holds_model_fields as _holds_model_fields
from ._mapping import _known_no as _known_no
from ._mapping import _persistable as _persistable
from ._mapping import _pins_and_presets as _pins_and_presets
from ._mapping import _Reader as _Reader
from ._mapping import _rev as _rev
from ._mapping import _selection as _selection
from ._mapping import _selection_keys as _selection_keys
from ._mapping import _soft_facts as _soft_facts
from ._mapping import _soft_read as _soft_read
from ._mapping import _strict as _strict
from ._mapping import campaign_mapped as campaign_mapped
from ._mapping import campaign_mapping as campaign_mapping
from ._mapping import embedding_role as embedding_role
from ._mapping import embedding_view as embedding_view
from ._mapping import enriched as enriched
from ._mapping import global_mapped as global_mapped
from ._mapping import global_mapping as global_mapping
from ._mapping import legacy_embeds as legacy_embeds
from ._mapping import lookup as lookup
from ._mapping import stated as stated
from ._planning import Overlay as Overlay
from ._planning import _as_current as _as_current
from ._planning import _no_connection as _no_connection
from ._planning import _persisted as _persisted
from ._planning import _virtual as _virtual
from ._planning import campaign_plan as campaign_plan
from ._planning import global_plan as global_plan
from ._planning import overlay as overlay
from ._reasoning import _canonical as _canonical
from ._reasoning import _derivation as _derivation
from ._reasoning import _derived_for as _derived_for
from ._reasoning import _legacy_effort as _legacy_effort
from ._reasoning import _planned as _planned
from ._reasoning import _route_note as _route_note
from ._reasoning import _route_notes as _route_notes
from ._reasoning import _route_preset as _route_preset
from ._reasoning import _RoutePreset as _RoutePreset
from ._reasoning import _slot_label as _slot_label
from ._reasoning import _slots as _slots
from ._reasoning import _stranded as _stranded
from ._reasoning import _under_route_preset as _under_route_preset
from ._reasoning import derive as derive
from ._reasoning import derived_name as derived_name

# Original star-import surface, captured before extraction.
__all__ = [
    'GLOBAL_SCOPE',
    'OWNED_GLOBAL_KEYS',
    'REPRESENTABLE',
    'RETIRED_KEY',
    'SHARED_PRESET_KEYS',
    'Callable',
    'Collection',
    'Derived',
    'Iterator',
    'Literal',
    'Lookup',
    'Mapping',
    'NamedTuple',
    'Overlay',
    'OverlayError',
    'Plan',
    'PresetRead',
    'annotations',
    'campaign_mapped',
    'campaign_mapping',
    'campaign_plan',
    'campaign_scope',
    'capabilities',
    'cascade',
    'config',
    'derive',
    'derived_name',
    'embedding_role',
    'embedding_view',
    'empty',
    'enriched',
    'facts',
    'functools',
    'global_mapped',
    'global_mapping',
    'global_plan',
    'hashlib',
    'is_retired',
    'json',
    'keys',
    'legacy_embeds',
    'llm_connections',
    'llm_reasoning',
    'llm_sampling',
    'locks',
    'lookup',
    'overlay',
    'paths',
    'planned',
    'providers',
    'retired',
    'routing',
    'sampler_presets',
    'stated',
]

# Preserve the original deferred module metadata without redeclaring exports.
__annotations__ = {'REPRESENTABLE': 'tuple[str, ...]', 'OWNED_GLOBAL_KEYS': 'tuple[str, ...]'}
