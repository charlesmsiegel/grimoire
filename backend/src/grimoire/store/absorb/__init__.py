"""The scene-absorption extraction: one deterministic-primed LLM call producing the
chronicle summary plus proposed state/lore/authored edits, their diff materialization,
and their application. Prompt/parse only here + pure materialize/apply; the LLM call
lives in the route layer and the prompt text in templates/absorb/.
"""

from __future__ import annotations

# Submodules first, then names. The submodule line is listed in dependency
# order (`prompt`/`parse`/`snapshots`/`weather`/`conflicts` -> `materializer` ->
# `apply`): each file imports only files named before it. Python would resolve
# any other order too -- a submodule already in `sys.modules` is bound whatever
# this line says -- so the order is a deliberate reading aid, not a requirement:
# it states the package's internal layering in one line.
#
# `weather.py` holds both halves of the narrated-weather path rather than
# splitting them across `materializer` and `apply`, so the span rule that
# stages a row and the one that writes it stay side by side.
#
# `conflicts.py` sits below both for the same reason from the other direction:
# `materializer` stages a `before` and `apply` checks the store against it, so
# the one definition of "what the record says now" has to be visible to each.
from . import (prompt, parse, snapshots, weather, conflicts,  # noqa: F401
               materializer, apply)
from .prompt import build_prompt  # noqa: F401
from .parse import (  # noqa: F401
    _confidence, _int05, _truthy, extract_object, parse_output,
)
from .snapshots import (  # noqa: F401
    _snapshot_line, commitment_snapshot, group_snapshot, plot_snapshot,
    relationships_snapshot, state_snapshot,
)
from .weather import _apply_weather, _weather_edits  # noqa: F401
from .conflicts import (  # noqa: F401
    MERGEABLE, RESOLUTIONS, batch_verdicts, check_conflicts, commitment_line,
    conflict_row, current_value, merge_text, plot_line, resolved,
)
from .materializer import (  # noqa: F401
    _CARD_FIELDS, _actor_exists, _char_name, _entity_kind,
    _new_character_dossier, _new_character_provenance, materialize,
)
from .apply import UNCONFIRMED, _BROWSABLE_KINDS, apply_edits  # noqa: F401
