"""The scene-absorption extraction: one deterministic-primed LLM call producing the
chronicle summary plus proposed state/lore/authored edits, their diff materialization,
and their application. Prompt/parse only here + pure materialize/apply; the LLM call
lives in the route layer and the prompt text in templates/absorb/.
"""

from __future__ import annotations

# Submodules first, then names. The submodule line is listed in dependency
# order (`prompt`/`parse`/`snapshots`/`routing`/`weather`/`conflicts` ->
# `materializer` -> `apply`): each file imports only files named before it.
# Python would resolve any other order too -- a submodule already in
# `sys.modules` is bound whatever this line says -- so the order is a deliberate
# reading aid, not a requirement: it states the package's internal layering in
# one line.
#
# `weather.py` holds both halves of the narrated-weather path rather than
# splitting them across `materializer` and `apply`, so the span rule that
# stages a row and the one that writes it stay side by side.
#
# `conflicts.py` sits below both for the same reason from the other direction:
# `materializer` stages a `before` and `apply` checks the store against it, so
# the one definition of "what the record says now" has to be visible to each.
#
# `routing.py` is a leaf rather than part of `materializer`: it decides how much
# weight a proposal has earned, `materializer` decides what a proposal IS, and
# keeping the review policy in one file is what makes "none of this is
# permission" checkable by reading `apply.py`'s imports -- it does not import
# `routing`, and a change that made it do so would be visible on that line.
from . import (  # noqa: F401
               apply,
               conflicts,
               materializer,
               parse,
               prompt,
               routing,
               snapshots,
               weather,
)
from .apply import _BROWSABLE_KINDS, UNCONFIRMED, apply_edits  # noqa: F401
from .conflicts import (  # noqa: F401
               MERGEABLE,
               RESOLUTIONS,
               batch_verdicts,
               check_conflicts,
               commitment_line,
               conflict_row,
               current_value,
               fact_line,
               merge_text,
               plot_line,
               resolved,
)
from .materializer import (  # noqa: F401
               _CARD_FIELDS,
               APPEND_KINDS,
               _actor_exists,
               _char_name,
               _entity_target,
               _new_character_dossier,
               _new_character_provenance,
               materialize,
)
from .parse import (  # noqa: F401
               CITATION_FIELDS,
               CITATION_TEXT,
               _certainty,
               _cite,
               _confidence,
               _int05,
               _truthy,
               extract_object,
               parse_output,
)
from .prompt import build_prompt  # noqa: F401

# `routing` is bound as a MODULE only, unlike every other submodule here. Its
# public names are `HIGH`, `LOW`, `WEIGHTS`, `band`, `review`, `authority` --
# each of which says what it means read as `routing.HIGH` and nothing at all
# read as `absorb.HIGH`, in a package whose namespace already holds conflict
# `RESOLUTIONS` and a `review`-shaped word in half its docstrings.
from .snapshots import (  # noqa: F401
               FACT_SNAPSHOT_LIMIT,
               _snapshot_line,
               commitment_snapshot,
               fact_snapshot,
               group_snapshot,
               plot_snapshot,
               relationships_snapshot,
               state_snapshot,
               steering_snapshot,
)
from .weather import _apply_weather, _weather_edits  # noqa: F401
