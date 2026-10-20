"""Assembly: the single gathering pass and the entry points that render it.

`_assemble` collects every section's data once; `build_messages`,
`build_director_messages`, `build_opener_messages` and `context_sections` all
run off that one dict; its keys are keyword arguments to the templates, so
their order is immaterial.

`_prepare` freezes the render once, then packs each available model profile;
the prompt record and outgoing messages are built from that same packed variant.

`SECTIONS` is the prompt's section catalog — the one list, not a mirror of one.
It used to be a mirror: templates/scene/system.j2 re-`include`d each section
itself, so the prompt and the token breakdown were two render paths over the
same data and only the breakdown was allowed to be approximate. That is fine
while every section is all-or-nothing and becomes a lie the moment a packer
drops one, so `_render_sections` now renders the list and system.j2 only joins
what it is handed.
"""

from __future__ import annotations

# Compatibility facade. State and implementation live in responsibility leaves;
# internal fault injection targets those owners, while existing imports remain.
import logging
import random
import re
from copy import deepcopy
from typing import NamedTuple

from .... import content_parts, model_guidance, prompts
from ... import (
    authors_notes,
    birthdays,
    campaign_settings,
    characters,
    config,
    default_prompts,
    entities,
    length_drift,
    locks,
    overlay,
    pcs,
    pins,
    response_presets,
    response_targets,
    styles,
    tokens,
    turn_plan,
    voice_anchors,
)
from ...appearances import cast as appearances_cast
from ...appearances import paths as appearances_paths
from ...appearances import versions as appearances_versions
from ...campaigns import paths as campaigns_paths
from ...campaigns import read as campaigns_read
from ...continuity import effective
from ...history import settings as history_settings
from ...regex import view as regex_view
from ...scenes import focus as scenes_focus
from ...scenes import identity as scenes_identity
from ...scenes import read as scenes_read
from ...scenes import serialize as scenes_serialize
from ...tracker import fields as tracker_fields
from ...tracker import settings as tracker_settings
from ...tracker import view as tracker_view
from ...tracker import walk as tracker_walk
from ...worlds import read as worlds_read
from .. import (
    activation,
    actor,
    archive,
    art,
    authors_note,
    epistemic,
    layout,
    macros,
    mechanics,
    pack,
    source_text,
    speaker,
    story,
    world_state,
)
from .. import cast as cast_data
from .. import history as history_section
from ._catalog import _CAST_SECTIONS as _CAST_SECTIONS
from ._catalog import _GROUP_STATE_SECTION as _GROUP_STATE_SECTION
from ._catalog import _OFF_SCENE_CAST_HEADING as _OFF_SCENE_CAST_HEADING
from ._catalog import _RECALLED_SECTION as _RECALLED_SECTION
from ._catalog import _SETTING_SECTION as _SETTING_SECTION
from ._catalog import _VARIANTS as _VARIANTS
from ._catalog import _VOICE_CAST_SECTIONS as _VOICE_CAST_SECTIONS
from ._catalog import _WORLD_INFO_SECTION as _WORLD_INFO_SECTION
from ._catalog import SECTIONS as SECTIONS
from ._catalog import Section as Section
from ._catalog import _applies as _applies
from ._catalog import _pinned_sections as _pinned_sections
from ._catalog import _section_on as _section_on
from ._catalog import _section_template as _section_template
from ._compose import OPENER_RECAP_DEPTH as OPENER_RECAP_DEPTH
from ._compose import Appended as Appended
from ._compose import _plan_block as _plan_block
from ._compose import _plan_messages as _plan_messages
from ._compose import _plan_rows as _plan_rows
from ._compose import build_director_messages as build_director_messages
from ._compose import build_messages as build_messages
from ._compose import build_opener_messages as build_opener_messages
from ._compose import compose_director_turn as compose_director_turn
from ._compose import compose_opener as compose_opener
from ._compose import compose_turn as compose_turn
from ._compose import context_breakdown as context_breakdown
from ._compose import context_sections as context_sections
from ._history import _parts_of as _parts_of
from ._history import _projected as _projected
from ._history import _split_refs as _split_refs
from ._inputs import _NARRATOR_ONLY_KEYS as _NARRATOR_ONLY_KEYS
from ._inputs import _NO_TRACKER as _NO_TRACKER
from ._inputs import _assigned_npc as _assigned_npc
from ._inputs import _campaign_view as _campaign_view
from ._inputs import _card_text as _card_text
from ._inputs import _cast_blocks as _cast_blocks
from ._inputs import _check_view as _check_view
from ._inputs import _current_setting as _current_setting
from ._inputs import _history_parts as _history_parts
from ._inputs import _lore as _lore
from ._inputs import _npc_cards as _npc_cards
from ._inputs import _players as _players
from ._inputs import _post_history as _post_history
from ._inputs import _prose_style as _prose_style
from ._inputs import _scan_window as _scan_window
from ._inputs import _scope_to_actor as _scope_to_actor
from ._inputs import _tracker_lines as _tracker_lines
from ._inputs import _tracker_read as _tracker_read
from ._inputs import log as log
from ._inspection import _CAST_REASONS as _CAST_REASONS
from ._inspection import HISTORY_UNSELECTED_SHOWN as HISTORY_UNSELECTED_SHOWN
from ._inspection import _breakdown as _breakdown
from ._inspection import _conversation_rows as _conversation_rows
from ._inspection import _history_row as _history_row
from ._inspection import _history_rows as _history_rows
from ._inspection import _lore_row as _lore_row
from ._inspection import _notes_row as _notes_row
from ._inspection import _source_row as _source_row
from ._inspection import _unselected as _unselected
from ._lore_render import _BODY_SLOT as _BODY_SLOT
from ._lore_render import _entry_name as _entry_name
from ._lore_render import _expanded_section as _expanded_section
from ._lore_render import _held_back_only as _held_back_only
from ._lore_render import _is_secret as _is_secret
from ._lore_render import _lore_detail as _lore_detail
from ._lore_render import _lore_source as _lore_source
from ._lore_render import _reason_names as _reason_names
from ._lore_render import _reason_refs as _reason_refs
from ._lore_render import _recall_run as _recall_run
from ._lore_render import _recalled_history as _recalled_history
from ._lore_render import _RunKey as _RunKey
from ._lore_render import _section_body as _section_body
from ._lore_render import _source_data as _source_data
from ._lore_render import _template_order as _template_order
from ._lore_render import _world_info_section as _world_info_section
from ._packing import _merged_carrier as _merged_carrier
from ._packing import _packed as _packed
from ._packing import _prepare as _prepare
from ._packing import _profile_sections as _profile_sections
from ._packing import _token_memo as _token_memo
from ._render import _compose_system as _compose_system
from ._render import _dedupe_runs as _dedupe_runs
from ._render import _render_sections as _render_sections
from ._render import _system_text as _system_text
from ._snapshot import _assemble as _assemble

# Original star-import surface, captured before extraction.
__all__ = [
    'HISTORY_UNSELECTED_SHOWN',
    'OPENER_RECAP_DEPTH',
    'SECTIONS',
    'Appended',
    'NamedTuple',
    'Section',
    'activation',
    'actor',
    'annotations',
    'appearances_cast',
    'appearances_paths',
    'appearances_versions',
    'archive',
    'art',
    'authors_note',
    'authors_notes',
    'birthdays',
    'build_director_messages',
    'build_messages',
    'build_opener_messages',
    'campaign_settings',
    'campaigns_paths',
    'campaigns_read',
    'cast_data',
    'characters',
    'compose_director_turn',
    'compose_opener',
    'compose_turn',
    'config',
    'content_parts',
    'context_breakdown',
    'context_sections',
    'deepcopy',
    'default_prompts',
    'effective',
    'entities',
    'epistemic',
    'history_section',
    'history_settings',
    'layout',
    'length_drift',
    'locks',
    'log',
    'logging',
    'macros',
    'mechanics',
    'model_guidance',
    'overlay',
    'pack',
    'pcs',
    'pins',
    'prompts',
    'random',
    're',
    'regex_view',
    'response_presets',
    'response_targets',
    'scenes_focus',
    'scenes_identity',
    'scenes_read',
    'scenes_serialize',
    'source_text',
    'speaker',
    'story',
    'styles',
    'tokens',
    'tracker_fields',
    'tracker_settings',
    'tracker_view',
    'tracker_walk',
    'turn_plan',
    'voice_anchors',
    'world_state',
    'worlds_read',
]

# Preserve deferred module annotations without copying live values.
__annotations__ = {'_NO_TRACKER': 'tuple[dict, list, dict]'}
