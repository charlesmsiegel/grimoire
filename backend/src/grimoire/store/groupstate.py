"""Per-group campaign state stored beside the campaign's group records at
<root>/groups/<gid>/state.md (a sibling directory of the flat groups/<gid>.md,
like <kind>/<eid>/assets/): a standing snapshot in optional `## `-headed prose
sections. A body whose first non-empty line is not a recognized header is read
wholesale as `goals`. Snapshot only — rewritten each absorb. Shares its
parse/compose/read/write machinery with playstate.py via sectioned_state.py;
state is campaign-local by definition (never world-side).
"""

from __future__ import annotations

from .sectioned_state import Sections

LABELS: dict[str, str] = {
    "goals": "Goals", "resources": "Resources", "focus": "Focus",
    "public_perception": "Public perception", "secrets": "Secrets",
}
FIELDS: tuple[str, ...] = tuple(LABELS)

_SECTIONS = Sections("groups", LABELS, fallback="goals")

state_path = _SECTIONS.state_path
read_state = _SECTIONS.read_state
write_state = _SECTIONS.write_state
compose_body = _SECTIONS.compose_body
