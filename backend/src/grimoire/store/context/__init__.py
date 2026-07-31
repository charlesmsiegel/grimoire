"""The context builder: assemble a scene's cast + world-info into the OpenRouter
messages list, SillyTavern-faithful. World-info selection goes through activate(),
the single swap point for smarter retrieval later.

This package gathers DATA; the prompt text and section layout live in
templates/scene/ (see templates/README.md for the variable contract).
build_messages & co. render templates/scene/system.j2 from that data.
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
from . import cast, macros, world_state, mechanics, story, tokens, assemble  # noqa: F401
from .cast import (  # noqa: F401
    _campaign_player_refs, _cast_directory_data, _char_name, _drift_roster,
    cast_datetime_facts,
)
from .macros import (  # noqa: F401
    _LITERAL_MACROS, _MACRO_TOKEN, _RANDOM_MACRO, _ROLL_MACRO, _datetime_subs,
    _expand_random, _expand_rolls, _strip_unknown_macros, _substitute,
    expand_macros, scene_substitutions,
)
from .world_state import (  # noqa: F401
    _character_states, _group_states, _today_data, _weather_data, _world_info,
    activate,
)
from .mechanics import (  # noqa: F401
    _mechanics, _rule_keys_match, _sheet_summary_lines, _sheet_type_label,
)
from .story import _project_history, _relationship_lines, _story_entries  # noqa: F401
from .assemble import (  # noqa: F401
    OPENER_RECAP_DEPTH, _SECTIONS, _assemble, _system_text,
    build_director_messages, build_messages, build_opener_messages,
    context_sections,
)
# `_encoder` is re-exported by value for compatibility with callers that read
# `context._encoder`. Patching it here no longer intercepts: `count_tokens`
# lives in tokens.py and calls that module's global, so a test that wants a
# stub encoder has to patch `context.tokens._encoder` instead.
from .tokens import _encoder, count_tokens  # noqa: F401
