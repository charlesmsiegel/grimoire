"""The context builder: assemble a scene's cast + world-info into the OpenRouter
messages list, SillyTavern-faithful. World-info selection goes through activate(),
the single swap point for smarter retrieval later; `archive.py` sits beside it,
recalling absorbed scenes that have fallen out of the recap window by the same
keyword rule.

This package gathers DATA; the prompt text lives in templates/scene/ (see
templates/README.md for the variable contract). The section ORDER lives here,
in `assemble._SECTIONS`: `_render_sections` renders it, `pack.pack` decides
what fits, and templates/scene/system.j2 joins what survives. One list, one
render — so `context_sections` (the inspector's breakdown) and `build_messages`
(the prompt) cannot disagree about what was sent.
"""

from __future__ import annotations

# Submodules first, then names. The submodule line is listed in dependency
# order (`cast` -> `macros`/`world_state`, and everything -> `assemble`): each
# file imports only files named before it. Python would resolve any other order
# too -- a submodule already in `sys.modules` is bound whatever this line says
# -- so the order is a deliberate reading aid, not a requirement: it states the
# package's internal layering in one line.
#
# `tokens.py` stands alone: `count_tokens` is what the breakdown route calls on
# the strings `context_sections` hands back, and it imports nothing from here.
from . import (cast, macros, world_state, mechanics, story, archive, pack,  # noqa: F401
               tokens, assemble)
from .cast import (  # noqa: F401
    _campaign_player_refs, _cast_directory_data, _char_name, _drift_roster,
    _voice_notes, cast_datetime_facts,
)
from .macros import (  # noqa: F401
    _LITERAL_MACROS, _MACRO_TOKEN, _RANDOM_MACRO, _ROLL_MACRO, _datetime_subs,
    _expand_random, _expand_rolls, _strip_unknown_macros, _substitute,
    expand_macros, scene_substitutions,
)
from .world_state import (  # noqa: F401
    _character_states, _group_states, _today_data, _weather_data, _world_info,
    activate, keyword_hit,
)
from .archive import _archive_entries, archive_depth  # noqa: F401
# The tier constants and the budget reader, by value; `pack.pack` itself is
# deliberately NOT re-exported — `from . import pack` above binds the module
# under that name, and a same-named function would silently replace it, leaving
# `context.pack.LOCK_IN` an AttributeError.
from .pack import (  # noqa: F401
    ARCHIVE, BACKGROUND, DROP_ORDER, HISTORY, HISTORY_FLOOR, LOCK_IN,
    MESSAGE_OVERHEAD, SEPARATOR, SPOTLIGHT, budget_tokens, message_cost,
)
from .mechanics import (  # noqa: F401
    _mechanics, _rule_keys_match, _sheet_summary_lines, _sheet_type_label,
)
from .story import (  # noqa: F401
    _project_history, _recap_depth, _recap_ids, _relationship_lines, _story_entries,
)
from .assemble import (  # noqa: F401
    OPENER_RECAP_DEPTH, _SECTIONS, _assemble, _compose_system, _packed,
    _render_sections, _section_template, _system_text, Section,
    build_director_messages, build_messages, build_opener_messages,
    context_breakdown, context_sections,
)
# `_encoder` is re-exported by value for compatibility with callers that read
# `context._encoder`. Patching it here no longer intercepts: `count_tokens`
# lives in tokens.py and calls that module's global, so a test that wants a
# stub encoder has to patch `context.tokens._encoder` instead.
from .tokens import _encoder, count_tokens  # noqa: F401
