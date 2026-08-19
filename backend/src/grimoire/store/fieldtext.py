"""One coercion, shared: a stored field as text, or a fallback.

Nine modules had grown their own private copy of this — `plot._field`,
`commitments._field`, `facts._field`, `scene_ideas._field`, `briefing`,
`casefile`, `search`, `timeline`, and `routes.campaigns._ledger_text` — each
with the same three-line body and the same paragraph explaining why it exists.
That is the shape a health report calls duplication, and the reason to fix it
here is not tidiness: nine copies of a rule are nine chances for the tenth
caller to write a subtly different one (an early copy stripped without
falling back, another coerced with `str()`), and the rule is a boundary
guarantee that only works if it is the same everywhere.

The rule, once. A campaign's JSON stores are hand-editable and read by a bare
`json.loads`, so a record with an object-valued `title` reads fine, passes
every `try` around the read — the read *succeeded* — and reaches React, which
refuses an object as a child and blanks the whole panel that was about to show
it. A projection is therefore where the types are made true: anything that is
not a string becomes `fallback`, and a string is stripped.

`str(value)` deliberately not: it would turn `{"a": 1}` into text that reads
like data, renders without complaint, and matches nothing. A field that is not
text is missing, and saying so is what lets the caller substitute an id.
"""

from __future__ import annotations


def text(value, fallback: str = "") -> str:
    """`value` stripped if it is a string, else `fallback`."""
    return value.strip() if isinstance(value, str) else fallback
