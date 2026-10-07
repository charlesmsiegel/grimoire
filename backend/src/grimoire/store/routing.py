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
    #: What kind of call this is. Both are `generate` in this slice; `decide`
    #: is reserved for routes that answer a yes/no or a pick.
    operation: str = "generate"
    #: Which connection role serves this route when nothing overrides it.
    default_role: str = "primary"
    #: Capabilities the serving connection must have (e.g. `vision`).
    requires: tuple[str, ...] = ()
    #: For a route split out of a legacy one, the legacy route's key: stored
    #: settings (connection and preset keys) are still read from it. "" means
    #: this route IS a legacy route.
    legacy: str = ""


OPERATIONS: tuple[str, ...] = ("generate", "decide")
DEFAULT_ROLES: tuple[str, ...] = ("primary", "fast", "decision")


def legacy_key(route: Route) -> str:
    """The key this route's stored settings live under."""
    return route.legacy or route.key


#: Every route, in the order the pickers render them: the prose ones first,
#: then the per-turn upkeep, then the one-shot utilities.
#:
#: The six routes #142 named are spelled as it spelled them, INCLUDING its
#: granularity: it listed the scene turn's retries, regenerations and director
#: turns as part of one task, not as three. The other four are call sites that
#: did not exist when it was filed.
#:
#: Adding a generation? Add its task here. `test_routing_guard.py` fails on a
#: `require_inference` call whose task no route claims, so the alternative to
#: this line is a red test, not a silently unroutable call.
ROUTES: tuple[Route, ...] = (
    Route("scene", "Scene turns",
          "Every streamed turn in play: sends, retries, regenerations, kept-writing "
          "replies, director turns, replayed turns and mechanics continuations.",
          ("chat", "retry", "regenerate", "extend", "director", "replay", "continuation"),
          True),
    Route("speaker", "Next speaker",
          "Which character speaks next in group play.",
          ("response-selector",), True, default_role="fast", legacy="scene"),
    Route("opener", "Scene openers",
          "The drafted first post of a new scene.", ("opener",), True),
    Route("absorb", "Absorb & mechanics audit",
          "End-of-scene extraction, and the mechanics audit that runs beside it.",
          ("absorb", "audit"), True, default_role="fast"),
    Route("dossier", "Dossier refresh",
          "One call per present character at absorb -- the loop where a cheaper "
          "model saves the most.",
          ("dossier",), True, default_role="fast"),
    Route("continuity", "Continuity checks",
          "The duplicate check beside absorb and the reconciliation sweep after "
          "End Scene or a refresh.",
          ("continuity-identity", "continuity-reconcile"), True, default_role="fast"),
    Route("summary", "Summaries & scene-break checks",
          "The live rolling summary and the is-this-scene-over question.",
          ("rolling-summary",), True, default_role="fast"),
    Route("scene_break", "Scene-break checks",
          "Whether the scene has reached a natural break.",
          ("scene-break",), True, default_role="fast", legacy="summary"),
    Route("tracker", "Scene state tracker",
          "One small call after every post to keep each character's tracked state current.",
          ("tracker-update",), True, default_role="fast"),
    Route("suggestions", "Scene suggestions",
          "Suggested next scenes, and the metadata read out of a scene description.",
          ("suggestions", "intent", "character-from-passage"), True),
    Route("voice", "Voice anchors & drift",
          "Drafted voice anchors, and the drift check against them. A campaign "
          "override reaches its own cast; a world character's anchor is drafted "
          "outside any campaign and follows the global route.",
          ("voice-anchor",), True),
    Route("voice_drift", "Voice drift checks",
          "Whether a played scene drifted from a character's voice anchor.",
          ("voice-drift",), True, default_role="fast", legacy="voice"),
    Route("image", "Image descriptions",
          "What a picture shows, drafted for the alt text and the art catalog. A "
          "campaign override reaches its own image library; a world record's "
          "picture follows the global route.",
          ("image-description",), True, default_role="fast", requires=("vision",)),
    Route("tagline", "Character taglines",
          "The one-line tagline drafted for a character version.", ("tagline",), False,
          default_role="fast"),
    Route("scenario", "Scenario drafts",
          "The scene roster read out of an imported character card.", ("scenario",), False,
          default_role="fast"),
)


def _legacy_routes() -> tuple[Route, ...]:
    """The routes as they were before three were split out: each legacy route
    with its own tasks followed by its split children's, in registry order.
    Every pre-registry surface (config keys, pickers, the routing bundle, the
    campaign allow-list) reads this view and so sees exactly what it always
    saw."""
    out: list[Route] = []
    for r in ROUTES:
        if r.legacy:
            continue
        kids = tuple(t for c in ROUTES if c.legacy == r.key for t in c.tasks)
        out.append(r._replace(tasks=r.tasks + kids) if kids else r)
    return tuple(out)


#: The twelve routes `ROUTES` held before the split.
LEGACY_ROUTES: tuple[Route, ...] = _legacy_routes()

#: task -> route key. Built here rather than written out, so the two cannot drift.
TASK_ROUTE: dict[str, str] = {task: r.key for r in ROUTES for task in r.tasks}

#: Frontmatter keys, in `LEGACY_ROUTES` order. `config.py` narrows `read_config()` to
#: `_CONFIG_KEYS`, so a key missing from there is silently dropped on read AND
#: on write -- which is why this is one tuple both files share.
CONFIG_KEYS: tuple[str, ...] = tuple(f"route_{r.key}" for r in LEGACY_ROUTES)

#: The sampler-preset choice per route, stored beside the connection choice at
#: both scopes (`store/sampler_presets.py` resolves it). A separate tuple rather
#: than folded into `CONFIG_KEYS` because that one is also the list of keys
#: naming a CONNECTION -- `llm_connections.delete_connection` clears every key
#: in it that names the deleted id, and a preset that happened to share a
#: connection's slug would be cleared by the wrong delete.
PRESET_CONFIG_KEYS: tuple[str, ...] = tuple(f"preset_{r.key}" for r in LEGACY_ROUTES)

_BY_KEY: dict[str, Route] = {r.key: r for r in ROUTES}


def config_key(route_key: str) -> str:
    """The frontmatter key a route's choice is stored under, at either scope."""
    return f"route_{route_key}"


def preset_key(route_key: str) -> str:
    """The frontmatter key a route's sampler preset is stored under, at either
    scope."""
    return f"preset_{route_key}"


def route_by_key(route_key: str) -> Route:
    return _BY_KEY[route_key]


def label_for(route_key: str) -> str:
    """A route's name for a message, or the key itself for one nothing claims.

    The lookup a *message* wants. `route_by_key` raises, which is right for code
    that has already established the route exists and wrong for an error path --
    a 409 explaining why a connection cannot send must not become a KeyError 500
    on the way out.
    """
    got = _BY_KEY.get(route_key)
    return got.label if got is not None else route_key


def route(task: str) -> Route | None:
    """The route a task belongs to, or None for a task no route claims."""
    key = TASK_ROUTE.get(task, "")
    return _BY_KEY.get(key)


def routes_for(scope: str) -> tuple[Route, ...]:
    """The routes a scope may set: all of them globally, the campaign-scoped
    ones for a campaign."""
    if scope == "campaign":
        return tuple(r for r in LEGACY_ROUTES if r.campaign_scoped)
    return LEGACY_ROUTES


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
    lkey = legacy_key(got)
    key = config_key(lkey)
    if got.campaign_scoped:
        chosen = _opinion(campaign_meta, key, exists)
        if chosen:
            return {"route": lkey, "connection_id": chosen, "scope": "campaign"}
    chosen = _opinion(cfg, key, exists)
    if chosen:
        return {"route": lkey, "connection_id": chosen, "scope": "global"}
    return {"route": lkey, "connection_id": "", "scope": "active"}


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
    inherited: dict[str, str] = {}
    inherited_from: dict[str, dict] = {}
    # The same routes in all four maps, not every route in some of them: a
    # campaign bundle that reported an effective value for a route the campaign
    # cannot set would be answering a question its picker never asks, and a
    # reader diffing the keys would have to work out which is which.
    for r in mine:
        # Any of the route's tasks answers for all of them -- they resolve
        # through the same key by construction -- so the first one stands in.
        got = resolve(r.tasks[0], campaign_meta=campaign_meta, cfg=cfg, exists=exists)
        effective[r.key] = got["connection_id"]
        provenance[r.key] = {"scope": got["scope"]}
        # What this route would resolve to if THIS scope said nothing, which is
        # the only honest label for an "inherit" option. `effective` includes
        # the scope's own opinion, so a row that already names a connection
        # would otherwise offer "inherit (that same connection)" and change the
        # answer when you picked it.
        without = resolve(r.tasks[0], exists=exists,
                          campaign_meta=_silenced(campaign_meta, r) if scope == "campaign"
                          else campaign_meta,
                          cfg=_silenced(cfg, r) if scope == "global" else cfg)
        inherited[r.key] = without["connection_id"]
        inherited_from[r.key] = {"scope": without["scope"]}
    return {"routes": routes, "effective": effective, "provenance": provenance,
            "inherited": inherited, "inherited_from": inherited_from}


def _silenced(meta: dict, r: Route) -> dict:
    """`meta` with this route's key removed -- the scope having no opinion."""
    return {k: v for k, v in meta.items() if k != config_key(r.key)}


def refused(scope: str, fields: Iterable[str]) -> list[str]:
    """The field names in `fields` this scope may NOT set.

    A campaign PUT naming `route_tagline` is a 400 rather than a stored key that
    never fires -- the setting would look applied and never route anything.
    """
    allowed = {config_key(r.key) for r in routes_for(scope)}
    return [f for f in fields if f not in allowed]
