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
  ``entities``    the generic /{kind} entity surface for both scopes

ORDERING: ``entities`` registers ``/worlds/{wid}/{kind}`` and
``/campaigns/{cid}/{kind}``, which capture any third path segment, so it is
included **last** — a literal-segment route registered after it would never be
reached. Nothing else here is order-sensitive, and
``tests/test_route_order.py`` fails if any route ends up shadowed by an earlier
one, so the rule is checked rather than remembered.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import (campaigns, characters, common, config, entities, greetings, mechanics,
               models, modules, scenes, streaming, weather, worlds)
from .common import get_llm, get_openai_compatible_client

__all__ = ["router", "get_llm", "get_openai_compatible_client"]

router = APIRouter()

for _domain in (config, modules, worlds, characters, greetings,
                campaigns, scenes, weather, mechanics):
    router.include_router(_domain.router)

router.include_router(entities.router)  # keep last: generic /{kind} catch-alls
