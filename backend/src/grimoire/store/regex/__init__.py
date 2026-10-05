"""Output-processing regex rules: the schema and the engine that applies it.

A leaf package -- nothing in it writes a transcript, and nothing it imports
imports it back. `rules` is the schema and the replacement expander, `apply`
runs a layered list of rules over text, `layers` is where the per-level rule
files live and how they stack, and `view` is what callers outside the package
use: messages as the prompt or display phase sees them, and the store phase.
`stream` is the display phase over a reply still arriving, and `rewrites` the
record of what the store phase changed, for Restore original. `translate`
turns a SillyTavern (JavaScript) pattern into Python with a verdict, and
`st_import` maps SillyTavern regex scripts onto rules through it.
"""

from __future__ import annotations

from . import apply, layers, rewrites, rules, st_import, stream, translate, view  # noqa: F401
