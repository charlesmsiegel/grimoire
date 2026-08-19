"""HTTP surface for grimoire.

One ``APIRouter`` per domain, composed here into the single ``router`` that
``main.create_app`` mounts under ``/api``:

  ``common``      helpers every domain module reuses (no routes)
  ``models``      request bodies (no routes)
  ``streaming``   SSE framing, persisted-turn strategies, proposal machinery
  ``config``      /config, /llm-connections, /styles, /response-presets,
                  /response, /length-presets, /calendars, /climates
  ``modules``     /modules
  ``worlds``      /worlds
  ``characters``  /worlds/{wid}/characters
  ``greetings``   /worlds/{wid}/greetings and /campaigns/{cid}/greetings
  ``campaigns``   /campaigns
  ``scenes``      /campaigns/{cid}/scenes
  ``weather``     /campaigns/{cid}/weather
  ``mechanics``   rolls, roll proposals, checks, campaign module and sheets
  ``usage``       /usage/summary and /campaigns/{cid}/usage cost rollups
  ``search``      /search, the keyword sweep over content and facts
  ``entities``    the generic /{kind} entity surface for both scopes

ORDERING: FastAPI matches in registration order and never backtracks, so the
include order below is load-bearing in two ways.

1. ``entities`` registers ``/worlds/{wid}/{kind}`` and ``/campaigns/{cid}/{kind}``,
   which capture any third path segment, so it goes **last** — a literal-segment
   route registered after it would never be reached.
2. Seven pairs of patterns *cross* (nine counting per-method): neither is more
   general, but a concrete URL exists that both match — e.g.
   ``POST /campaigns/c/scenes/instantiate/cast/batch`` matches both
   ``/campaigns/{cid}/scenes/{sid}/cast/batch`` and
   ``/campaigns/{cid}/{kind}/instantiate/{mid}/{content_id}``. Which one wins is
   decided purely by this order — hence ``campaigns`` after ``scenes`` and
   ``mechanics``.

``tests/test_route_order.py`` checks both: it fails if any route is shadowed by
an earlier one, and it pins the winner of every crossing pair.

Known, accepted difference from the pre-split single module: for a request whose
method *no* matching route supports, Starlette builds the 405's ``Allow`` header
from the first partially-matching route, so the reordering changes that header's
content for eight method/URL combinations across three crossing shapes (all of
which need a path segment that cannot occur in practice, e.g. a scene literally
named ``instantiate``). The status stays 405 and no dispatch changes; matching
the old header exactly would mean reproducing the old interleaved registration
order, which is the thing this package exists to undo.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import (campaigns, characters, common, config, entities, greetings, mechanics,
               models, modules, scenes, search, streaming, usage, weather, worlds)
from .common import (build_llm, build_openai_compatible_client, get_llm,
                     get_openai_compatible_client)

__all__ = ["router", "build_llm", "build_openai_compatible_client", "get_llm",
           "get_openai_compatible_client"]

router = APIRouter()

for _domain in (config, modules, worlds, characters, greetings,
                scenes, weather, mechanics, usage, campaigns, search):
    router.include_router(_domain.router)

router.include_router(entities.router)  # keep last: generic /{kind} catch-alls
