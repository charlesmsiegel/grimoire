"""The context builder: assemble a scene's cast + world-info into the OpenRouter
messages list, SillyTavern-faithful. World-info selection goes through activate(),
the single swap point for smarter retrieval; `semantic.py` is the strategy that
took that swap, scoring what the keywords missed by embedding similarity, and
`archive.py` sits beside both, recalling absorbed scenes that have fallen out of
the recap window by the same keyword rule.

This package gathers DATA; the prompt text lives in templates/scene/ (see
templates/README.md for the variable contract). The section ORDER lives here,
in `assemble.SECTIONS`: `_render_sections` renders it, `pack.pack` decides
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
# `tokens` is the package ABOVE this one's (#51): the per-record badge measures
# an entity with the same counter the breakdown does, and `entities` cannot
# import this package — `world_state` reads entities through `overlay`, so that
# would be a cycle. It is still bound as `context.tokens`, which is where every
# caller and every test that patches `_encoder` already looks.
from .. import tokens  # noqa: F401

# `_encoder` is re-exported by value for compatibility with callers that read
# `context._encoder`. Patching it here no longer intercepts: `count_tokens`
# lives in tokens.py and calls that module's global, so a test that wants a
# stub encoder has to patch `context.tokens._encoder` instead.
from ..tokens import _encoder, count_tokens  # noqa: F401
from . import (  # noqa: F401
               archive,
               art,
               assemble,
               cast,
               compare,
               layout,
               macros,
               mechanics,
               pack,
               semantic,
               speaker,
               story,
               world_state,
)
from .archive import _archive_entries, archive_depth  # noqa: F401
from .art import catalogue as art_catalogue  # noqa: F401
from .art import resolve_handles as resolve_art_handles  # noqa: F401
from .assemble import (  # noqa: F401
               OPENER_RECAP_DEPTH,
               SECTIONS,
               Appended,
               Section,
               _assemble,
               _breakdown,
               _compose_system,
               _packed,
               _render_sections,
               _section_template,
               _system_text,
               build_director_messages,
               build_messages,
               build_opener_messages,
               compose_director_turn,
               compose_opener,
               compose_turn,
               context_breakdown,
               context_sections,
)
from .cast import (  # noqa: F401
               _campaign_player_refs,
               _cast_directory_data,
               _char_name,
               _drift_roster,
               _voice_notes,
               cast_datetime_facts,
)
from .compare import compare_breakdowns  # noqa: F401
from .macros import (  # noqa: F401
               _LITERAL_MACROS,
               _MACRO_TOKEN,
               _RANDOM_MACRO,
               _ROLL_MACRO,
               _datetime_subs,
               _expand_random,
               _expand_rolls,
               _strip_unknown_macros,
               _substitute,
               expand_macros,
               scene_substitutions,
)
from .mechanics import (  # noqa: F401
               _mechanics,
               _rule_keys_match,
               _sheet_summary_lines,
               _sheet_type_label,
)

# The tier constants and the budget reader, by value; `pack.pack` itself is
# deliberately NOT re-exported — `from . import pack` above binds the module
# under that name, and a same-named function would silently replace it, leaving
# `context.pack.LOCK_IN` an AttributeError.
from .pack import (  # noqa: F401
               ARCHIVE,
               BACKGROUND,
               DROP_ORDER,
               HISTORY,
               HISTORY_FLOOR,
               LOCK_IN,
               MESSAGE_OVERHEAD,
               RECALLED,
               SEPARATOR,
               SPOTLIGHT,
               budget_tokens,
               message_cost,
)
from .story import (  # noqa: F401
               _project_history,
               _recap_depth,
               _recap_ids,
               _relationship_lines,
               _story_entries,
)
from .world_state import (  # noqa: F401
               _character_states,
               _group_states,
               _today_data,
               _transient_states,
               _weather_data,
               _world_info,
               activate,
               keyword_hit,
               secrecy_split,
)
