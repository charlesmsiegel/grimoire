"""The overview projections: what the Worlds, Campaigns, Todo and shell reads
are composed from (roadmap 04, `docs/superpowers/specs/2026-10-09-roadmap-04-
instant-overview-pages-design.md`).

A surface's answer splits into what grows with play -- scenes, transcripts,
ledgers -- and what is bounded by the roster or the world's size. The first
half is projected here, under keys that name every file each projection reads;
the second stays a live read in the routes. That is `routes/todo.py`'s cost
rule turned into a test for what to project.

Only the four overview read routes reach these entry points -- `GET /worlds`,
`GET /campaigns`, `GET /api/shell` and `GET /todo` -- and never a decision site
(`scenes.read.closed_by` on a turn's reservation, `list_campaigns` feeding a
write). The store functions they stand beside (`list_scenes`, `read_scene`,
`effective.*`, `pending.*`) are left exactly as they are, and nothing else in
`store/` imports this package, so the graph stays acyclic.

No module here mutates campaign-scoped state, so the package takes no entry
in `store/locks.py`.
"""

from __future__ import annotations

from . import read  # noqa: F401
from .read import scene_summary  # noqa: F401
