"""What the legacy settings layout could not carry over, as notes.

The planner (`legacy_plan`) computes these, and retirement will persist them
in the retirement record (`<home>/inference-retired.json`, slice I, Task 6b),
which this module grows into. For now it holds only the note itself: a leaf,
so the planner and the record never import each other (N10).

A note's id is a digest of what it is ABOUT -- its scope, subject, provider,
effort and kind -- and never of its wording (N14): a later build that rewords
a note must not bring back one the user dismissed.
"""

from __future__ import annotations

import hashlib
import json
from typing import NamedTuple

#: What a note can say was lost:
#: - `route_preset`: a route-level preset that sets no reasoning effort, over a
#:   GLM provider whose legacy effort rode under it (ruling 5);
#: - `unrepresentable`: a legacy effort no preset can carry;
#: - `fact_not_carried`: a connection's model field its model's facts do not
#:   state, found before the strip (N9).
KINDS: tuple[str, ...] = ("route_preset", "unrepresentable", "fact_not_carried")


class Note(NamedTuple):
    """One thing not carried over. `subject` names what it is about at that
    scope (a route key, a selection slot, a model field); `effort` is the
    legacy reasoning effort involved, "" when none is."""

    id: str
    scope: str
    subject: str
    provider_id: str
    effort: str
    kind: str
    text: str


def note_id(scope: str, subject: str, provider_id: str, effort: str, kind: str) -> str:
    """The stable id of the note with these fields: the same on every device
    and in every build, whatever the note's text says."""
    canonical = json.dumps([scope, subject, provider_id, effort, kind], ensure_ascii=False,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

