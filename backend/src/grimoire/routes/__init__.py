"""HTTP surface for grimoire.

One ``APIRouter`` per domain, composed here into the single ``router`` that
``main.create_app`` mounts under ``/api``:

  ``common``      helpers every domain module reuses (no routes)
  ``models``      request bodies (no routes)
  ``streaming``   SSE framing, persisted-turn strategies, proposal machinery
  ``config``      /config, /llm-connections, /styles, /response-presets,
                  /response, /length-presets, /entity-kinds, /calendars, /climates
  ``modules``     /modules
  ``worlds``      /worlds
  ``characters``  /worlds/{wid}/characters
  ``greetings``   /worlds/{wid}/greetings and /campaigns/{cid}/greetings
  ``campaigns``   /campaigns
  ``scenes``      /campaigns/{cid}/scenes
  ``weather``     /campaigns/{cid}/weather
  ``mechanics``   rolls, roll proposals, checks, campaign module and sheets
  ``usage``       /usage/summary, /campaigns/{cid}/usage cost rollups, /pricing
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

from . import (
    campaigns,
    character_turns,
    characters,
    common,
    config,
    entities,
    greetings,
    ledger,
    mechanics,
    models,
    modules,
    observability,
    passage_characters,
    runs,
    scenes,
    search,
    shell,
    streaming,
    todo,
    usage,
    weather,
    world_images,
    worlds,
)
from .common import (
    build_llm,
    build_openai_compatible_client,
    get_health,
    get_llm,
    get_openai_compatible_client,
)

__all__ = [
    "build_llm",
    "build_openai_compatible_client",
    "get_health",
    "get_llm",
    "get_openai_compatible_client",
    "router",
]

router = APIRouter()

# The lifespan an APIRouter carries when it was given none -- compared by type,
# since each router holds its own instance. Read off a fresh router rather than
# `router`: an include into the aggregate replaces its lifespan with a merged
# one, after which every plain domain router would look like one with its own.
_NO_LIFESPAN = type(APIRouter().lifespan_context)


def _includes_nest() -> bool:
    """Does ``include_router`` nest a router rather than copy its routes?

    Newer FastAPI -- the desktop's -- includes a router as one branch whose
    routes are analysed on the first request that walks into it; older FastAPI
    -- the Android pin, < 0.116 -- copies every route at include time and
    re-analyses each copy. Asked of FastAPI rather than of a version number:
    an empty router leaves a branch behind only where includes nest, and costs
    no analysis on either kind."""
    probe = APIRouter()
    probe.include_router(APIRouter())
    return bool(probe.routes)


_NESTED = _includes_nest()


def _compose(domain: APIRouter) -> None:
    """Add ``domain``'s routes to ``router``: included where includes nest, and
    appended by reference -- the route objects, not copies -- where they copy.

    Where an include copies (FastAPI < 0.116, the Android pin) it re-analyses
    every route it copies (signature, dependencies, response model), and
    ``main.create_app`` includes this aggregate into the app, which analyses
    them all again anyway. Including here as well cost one extra analysis of
    the whole API at every cold start, for an aggregate nothing serves
    directly. Appending the objects costs none, and loses nothing an include
    would have applied: a router's own prefix, tags, dependencies and default
    response class are baked into each route when ``@router.get`` declares it,
    and the include's arguments would come from this call, which passes none.

    Where an include nests (the desktop's FastAPI), each domain has to stay a
    branch of its own. A branch's routes are analysed on the first request
    that walks into it; a flat aggregate is one branch, so whichever request
    came first -- a world's character list, from a tab left open across a
    restart -- paid for all ~450 routes before it was answered, where one
    branch per domain has it pay for the few domains it walks past.

    What an include copies off the router *itself* is its startup and shutdown
    handlers and its lifespan, which appending would drop. A domain router
    that grows any of those is refused on either kind of FastAPI, so a router
    that would only work on the desktop fails the gate there too.
    """
    own_lifespan = type(domain.lifespan_context) is not _NO_LIFESPAN
    if domain.on_startup or domain.on_shutdown or own_lifespan:
        raise RuntimeError("a domain router with event handlers or a lifespan "
                           "must be composed with include_router")
    if _NESTED:
        router.include_router(domain)
    else:
        router.routes.extend(domain.routes)


# `world_images` AFTER `characters`: `/worlds/{wid}/images/{name}` generalizes
# `/worlds/{wid}/images/undescribed`, which `characters` owns, so any earlier
# and the `{name}` route swallows the describe backlog.
for _domain in (config, modules, worlds, characters, world_images, greetings,
                runs, scenes, character_turns, passage_characters, weather, mechanics, usage, observability,
                campaigns, ledger, search, shell, todo):
    _compose(_domain.router)

_compose(entities.router)  # keep last: generic /{kind} catch-alls
