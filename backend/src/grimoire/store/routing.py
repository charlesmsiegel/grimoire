"""Which connection each kind of generation runs on (#142).

A **route** is a named slot -- "scene turns", "dossier refresh" -- that one or
more of the app's generation *tasks* belong to. `store.usage.meter(task, ...)`
already labels every call with such a task, so a call site names the job it is
doing exactly once and this module answers where that job should be sent.

A route names a CONNECTION, not a model. #142 asked for per-task *models*, but
it was filed when `config.md` held one `model:` field; `store/llm_connections`
has since made the model a property of a named `(kind, base_url, api_key,
model)` profile, and a bare model name no longer says which provider serves it
or which key pays for it. #144's fallback route made the same call for the same
reason -- see `config.DEFAULT_FALLBACK_CONNECTION_ID`.

**This module is a pure leaf and must stay one.** `config.py` imports it for the
key list, so an import back into the store closes `config -> routing -> config`.
Everything impure -- reading `campaign.md`, reading `config.md`, looking a
connection up -- belongs to the caller, which is `routes/common.py`. That split
is `store/response_presets.resolve`'s, whose callers hand it the scope dicts for
the same reason.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import NamedTuple


class Route(NamedTuple):
    """One routing slot: what to call it, and which tasks it covers."""

    key: str
    label: str
    hint: str
    tasks: tuple[str, ...]
    #: Whether a campaign may override this route. False where the route's call
    #: sites have no campaign to read an override from -- a tagline is generated
    #: against a world character, a scenario against an uploaded card. A key
    #: written there anyway (by hand; the PUT refuses it) is ignored, rather
    #: than being a setting that silently never fires.
    campaign_scoped: bool


#: Every route, in the order the pickers render them: the prose ones first,
#: then the per-turn upkeep, then the one-shot utilities.
#:
#: The six routes #142 named are spelled as it spelled them, INCLUDING its
#: granularity: it listed the scene turn's retries, regenerations and director
#: turns as part of one task, not as three. The other four are call sites that
#: did not exist when it was filed.
#:
#: Adding a generation? Add its task here. `test_routing_guard.py` fails on a
#: `_require_connection` call whose task no route claims, so the alternative to
#: this line is a red test, not a silently unroutable call.
ROUTES: tuple[Route, ...] = (
    Route("scene", "Scene turns",
          "Every streamed turn in play: sends, retries, regenerations, director "
          "turns, replayed turns and mechanics continuations.",
          ("chat", "retry", "regenerate", "director", "replay", "continuation"), True),
    Route("opener", "Scene openers",
          "The drafted first post of a new scene.", ("opener",), True),
    Route("absorb", "Absorb & mechanics audit",
          "End-of-scene extraction, and the mechanics audit that runs beside it.",
          ("absorb", "audit"), True),
    Route("dossier", "Dossier refresh",
          "One call per present character at absorb -- the loop where a cheaper "
          "model saves the most.",
          ("dossier",), True),
    Route("summary", "Summaries & scene-break checks",
          "The live rolling summary and the is-this-scene-over question.",
          ("rolling-summary", "scene-break"), True),
    Route("suggestions", "Scene suggestions",
          "Suggested next scenes, and the metadata read out of a scene description.",
          ("suggestions", "intent"), True),
    Route("voice", "Voice anchors & drift",
          "Drafted voice anchors, and the drift check against them.",
          ("voice-anchor", "voice-drift"), True),
    Route("image", "Image descriptions",
          "What a picture shows, drafted for the alt text and the art catalog.",
          ("image-description",), True),
    Route("tagline", "Character taglines",
          "The one-line tagline drafted for a character version.", ("tagline",), False),
    Route("scenario", "Scenario drafts",
          "The scene roster read out of an imported character card.", ("scenario",), False),
)

#: task -> route key. Built here rather than written out, so the two cannot drift.
TASK_ROUTE: dict[str, str] = {task: r.key for r in ROUTES for task in r.tasks}

#: Frontmatter keys, in `ROUTES` order. `config.py` narrows `read_config()` to
#: `_CONFIG_KEYS`, so a key missing from there is silently dropped on read AND
#: on write -- which is why this is one tuple both files share.
CONFIG_KEYS: tuple[str, ...] = tuple(f"route_{r.key}" for r in ROUTES)

_BY_KEY: dict[str, Route] = {r.key: r for r in ROUTES}


def config_key(route_key: str) -> str:
    """The frontmatter key a route's choice is stored under, at either scope."""
    return f"route_{route_key}"


def route_by_key(route_key: str) -> Route:
    return _BY_KEY[route_key]


def route(task: str) -> Route | None:
    """The route a task belongs to, or None for a task no route claims."""
    key = TASK_ROUTE.get(task, "")
    return _BY_KEY.get(key)


def routes_for(scope: str) -> tuple[Route, ...]:
    """The routes a scope may set: all of them globally, the campaign-scoped
    ones for a campaign."""
    if scope == "campaign":
        return tuple(r for r in ROUTES if r.campaign_scoped)
    return ROUTES


def _opinion(meta: dict, key: str, exists: Callable[[str], bool]) -> str:
    """What a scope says about a route, or "" for "no opinion".

    Three things read as no opinion and the walk continues past all of them: an
    absent key, an empty (or whitespace-only) value, and an id naming a
    connection that no longer exists. The third is the one worth spelling out:
    `llm_connections.delete_connection` clears every *config* key that named the
    deleted connection, but it cannot reach into every campaign's frontmatter,
    so a dangling campaign override has to degrade to the next scope rather than
    fail a turn. `response_presets.resolve` treats a missing style the same way,
    for the same reason.
    """
    value = str(meta.get(key, "") or "").strip()
    return value if value and exists(value) else ""


def resolve(task: str, *, campaign_meta: dict, cfg: dict,
            exists: Callable[[str], bool]) -> dict:
    """Which connection id `task` runs on: campaign -> global -> active.

    `connection_id` is "" for the active connection, which is both the default
    and the only base this cascade has -- there is no "clear" sentinel, because
    "no connection at all" is not a state a generation can run in.

    An unknown task answers the active connection rather than raising: the guard
    test is what keeps a new call site from getting here, and a registry entry
    someone forgot must not 500 a scene mid-turn.
    """
    got = route(task)
    if got is None:
        return {"route": "", "connection_id": "", "scope": "active"}
    key = config_key(got.key)
    if got.campaign_scoped:
        chosen = _opinion(campaign_meta, key, exists)
        if chosen:
            return {"route": got.key, "connection_id": chosen, "scope": "campaign"}
    chosen = _opinion(cfg, key, exists)
    if chosen:
        return {"route": got.key, "connection_id": chosen, "scope": "global"}
    return {"route": got.key, "connection_id": "", "scope": "active"}


def bundle(*, campaign_meta: dict, cfg: dict, exists: Callable[[str], bool],
           scope: str) -> dict:
    """What a routing surface renders: what this scope says, what actually
    resolves, and where each resolved value came from.

    `/response`'s shape, and for its reason: a picker offering "inherit" has to
    be able to say what inheriting currently gets you.
    """
    own = campaign_meta if scope == "campaign" else cfg
    mine = routes_for(scope)
    routes = {r.key: str(own.get(config_key(r.key), "") or "") for r in mine}
    effective: dict[str, str] = {}
    provenance: dict[str, dict] = {}
    for r in ROUTES:
        # Any of the route's tasks answers for all of them -- they resolve
        # through the same key by construction -- so the first one stands in.
        got = resolve(r.tasks[0], campaign_meta=campaign_meta, cfg=cfg, exists=exists)
        effective[r.key] = got["connection_id"]
        provenance[r.key] = {"scope": got["scope"]}
    return {"routes": routes, "effective": effective, "provenance": provenance}


def writable(scope: str, fields: Iterable[str]) -> list[str]:
    """The field names in `fields` this scope may not set.

    A campaign PUT naming `route_tagline` is a 400 rather than a stored key that
    never fires -- the setting would look applied and never route anything.
    """
    allowed = {config_key(r.key) for r in routes_for(scope)}
    return [f for f in fields if f not in allowed]
