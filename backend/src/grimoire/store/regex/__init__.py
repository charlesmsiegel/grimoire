"""Output-processing regex rules: the schema and the engine that applies it.

A leaf package -- nothing in it writes a transcript, and nothing it imports
imports it back. `rules` is the schema and the replacement expander, `apply`
runs a layered list of rules over text, and `layers` is where the per-level rule
files live and how they stack.
"""

from __future__ import annotations

from . import apply, layers, rules  # noqa: F401
