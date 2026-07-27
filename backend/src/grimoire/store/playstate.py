"""Per-character campaign play-state stored beside the character copy at
<root>/characters/<cid>/state.md: a standing snapshot of `current_state` plus what the
character `knows` / `suspects`, as optional `## `-headed prose sections. A body with no
recognized header is read wholesale as `current_state` (Phase-2 back-compat). Snapshot
only — rewritten each absorb (discrete events live in the chronicle timeline). Shares
its parse/compose/read/write machinery with groupstate.py via sectioned_state.py.
"""

from __future__ import annotations

from .sectioned_state import Sections

LABELS: dict[str, str] = {
    "current_state": "Current state", "knows": "Knows", "suspects": "Suspects",
}
FIELDS: tuple[str, ...] = tuple(LABELS)

_SECTIONS = Sections("characters", LABELS, fallback="current_state")

state_path = _SECTIONS.state_path
read_state = _SECTIONS.read_state
write_state = _SECTIONS.write_state


def compose_body(current_state: str, knows: str, suspects: str) -> str:
    """Positional for the three known sections — absorb.py builds them one by one."""
    return _SECTIONS.compose_body(
        {"current_state": current_state, "knows": knows, "suspects": suspects})
