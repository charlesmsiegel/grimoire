"""todo: pricing responsibilities."""

from __future__ import annotations

from urllib.parse import quote

from ... import store
from ..common import rates_block, rates_editable
from . import _context
from ._router import router as router


def _chore_unpriced(ctx: _context._Ctx) -> dict | None:
    """Models the ledger holds that no rate prices.

    The chore exists because the failure is silent: a table is matched by model
    string, so an entry whose key does not match what was actually recorded
    prices nothing and says nothing. Naming the strings is the whole fix -- the
    reader can then see that their entry reads `z.ai/...` where the ledger says
    `z-ai/...`, which no amount of staring at a rollup would reveal.

    `unpriced_models` answers per provider, so one model served by two
    providers is two entries there (and two items below, each opening its own
    provider's rates); the names here list each recorded string once (M6).
    """
    try:
        models = store.usage.unpriced_models()
    except (OSError, ValueError):
        return None
    if not models:
        return None
    calls = sum(m["calls"] for m in models)
    distinct = list(dict.fromkeys(m["model"] for m in models))
    names = ", ".join(distinct[:3])
    more = "" if len(distinct) <= 3 else f", and {len(distinct) - 3} more"
    block = rates_block()
    how = ("Either the model's own rates, on its provider, or an entry in your "
           "pricing table can price them; a table entry has to match the model "
           "string exactly." if block is None else
           "An entry in your pricing table can price them; a table entry has to "
           "match the model string exactly. " + block)
    return {
        "id": "unpriced", "scope": "library", "group": "Costs & pricing", "severity": "warn", "n": calls,
        "what": f"{calls} call{'s' if calls != 1 else ''} no rate prices",
        "why": f"Nobody reported a price for these and no rate you have entered "
               f"prices them, so they are counted rather than costed: {names}{more}. "
               + how,
        "fix": "/models#rates", "fix_label": "Token rates",
    }


#: What stops the models chore and its items from answering: `config.md`, the
#: connection lookup or a campaign that cannot be read, or a store lock held
#: too long (M11). Each costs this chore, never the page.
_UNPRICED_IN_USE_UNREADABLE = (OSError, UnicodeDecodeError, ValueError, store.locks.StoreBusy)


def _unpriced_in_use() -> list[dict] | None:
    """`in_use.unpriced()`, or None when it could not be read."""
    try:
        return store.inference.in_use.unpriced()
    except _UNPRICED_IN_USE_UNREADABLE:
        return None


def _chore_unpriced_models(ctx: _context._Ctx) -> dict | None:
    """Models the settings select that nothing would price (spec 9.2).

    The configuration-side twin of `unpriced`: that one asks which strings the
    ledger RECORDED that no rate matches, this one which models the roles,
    fallbacks, chosen pins and campaign overrides SELECT that no rate would
    price -- so a local model is named before its first call is counted
    rather than costed. Computed from settings alone, never the ledger, so it
    costs the same however long the library has been played."""
    models = _unpriced_in_use()
    if not models:
        return None
    n = len(models)
    first = models[0]
    why = ("Their provider reports no price, and neither the model's own rates nor "
           "your pricing table covers them, so their calls are counted rather than "
           "costed. A local model is free only once you enter zero rates for it.")
    block = rates_block()
    if block is not None:
        return {
            "id": "unpriced-models", "scope": "library", "group": "Housekeeping",
            "severity": "note", "n": n,
            "what": f"{n} model{'s' if n != 1 else ''} in use with no price",
            "why": why + " " + block,
            "fix": "/models#rates", "fix_label": "Token rates",
        }
    return {
        "id": "unpriced-models", "scope": "library", "group": "Housekeeping",
        "severity": "note", "n": n,
        "what": f"{n} model{'s' if n != 1 else ''} in use with no price",
        "why": why,
        "fix": _model_rates_href(first["provider_id"], first["model"]),
        "fix_label": "Set rates",
    }


def _chore_embeddings(ctx: _context._Ctx) -> dict | None:
    """The one library note §18.1 asks for: no embeddings connection and model.

    It asks `embed_space.resolve` -- whether a sweep COULD embed -- and not the
    recall depth, which governs prompt recall rather than matching. Shown only
    once a campaign exists, since before then there is nothing to match. A
    resolve that raises is "could not tell", never "not configured": an outage
    is not a setup task."""
    try:
        if store.embed_space.resolve() is not None:
            return None
    except Exception:  # noqa: BLE001 -- an outage is not "not configured"
        return None
    try:
        if not store.campaigns.read.list_campaigns():
            return None
    except OSError:
        return None
    return {
        "id": "embeddings", "scope": "library", "group": "Housekeeping", "severity": "note",
        "n": 1,
        "what": "Semantic matching is not configured",
        "why": "Grimoire still uses basic matching to find possible overlaps in your ledgers. "
               "Semantic matching improves detection when the same story business is phrased "
               "differently.",
        "fix": "/models/edit", "fix_label": "Embeddings",
    }


def _items_unpriced(cid: str) -> list[dict]:
    """One item per call shape -- provider, the model asked for, the model that
    answered -- as `unpriced_models` keeps them apart, so the id carries all
    three and no two items share one: two providers serving one model (M6), or
    two names asked of one provider that answered as one snapshot. The label
    says which name it was asked for as when that differs, as the Costs page
    does. An item whose provider still exists opens that model's rates, under
    the model the rates are stated for; one with no provider, or a deleted
    one, opens the pricing table, the only rate that can reach it -- and so
    does every item while a model's own rates cannot be written
    (`rates_editable`)."""
    try:
        models = store.usage.unpriced_models()
    except (OSError, ValueError):
        return []
    named = any(m.get("provider_id") for m in models)
    providers = _provider_ids() if named and rates_editable() else set()
    out = []
    for m in models:
        provider_id = m.get("provider_id") or ""
        asked = m.get("facts_model") or m["model"]
        fix = "/models#rates"
        if provider_id in providers:
            fix = _model_rates_href(provider_id, asked)
        label = m["model"] if asked == m["model"] else f"{m['model']} asked for as {asked}"
        out.append({"id": ":".join(_id_part(x) for x in (provider_id, m["model"], asked)),
                    "label": label,
                    "detail": f"{m['calls']} calls that a rate would price",
                    "fix": fix})
    return out


def _id_part(part: str) -> str:
    """One part of a `:`-joined item id, with `%` and `:` escaped so the join
    cannot collide: an Ollama-style model name holds a colon (`llama3:8b`),
    and `(p, "a:b", asked "c")` and `(p, "a", asked "b:c")` would otherwise
    be one id. A name holding neither reads as it is."""
    return part.replace("%", "%25").replace(":", "%3A")


def _provider_ids() -> set[str]:
    """The providers that exist now, or none when they cannot be listed --
    every item then opens the pricing table, which can price any of them."""
    try:
        return {c["id"] for c in store.llm_connections.list_connections()}
    except (OSError, ValueError):
        return set()


#: What `encodeURIComponent` leaves unescaped beyond what `quote` always does.
_URI_COMPONENT_SAFE = "!*'()"


def _model_rates_href(provider_id: str, model: str) -> str:
    """`/providers/<id>/models/<model>?edit=rates`, encoded as the frontend's
    `ProvidersView.modelPath` encodes it: the id whole, the model segment by
    segment, because a model id's own `/` is part of the path the `models/*`
    splat reads back."""
    segments = "/".join(quote(seg, safe=_URI_COMPONENT_SAFE) for seg in model.split("/"))
    return (f"/providers/{quote(provider_id, safe=_URI_COMPONENT_SAFE)}"
            f"/models/{segments}?edit=rates")


#: Each route's label, for the models chore's detail.
_ROUTE_LABELS = {route.key: route.label for route in store.routing.ROUTES}


def _use_label(use: dict, names: dict[str, str]) -> str:
    """One use as the models chore's detail names it: the role (or its
    fallback) or the route, and the campaign's name where it is a campaign's."""
    if use["kind"] == "route":
        label = _ROUTE_LABELS.get(use["key"], use["key"])
    else:
        label = use["key"].capitalize()
        if use["kind"] == "fallback":
            label += " fallback"
    if use["scope"] == "campaign":
        cid = use.get("cid", "")
        label += f" ({names.get(cid, cid)})"
    return label


def _campaign_names(models: list[dict]) -> dict[str, str]:
    """The name of each campaign a use names, by id; an id where it cannot be read."""
    names: dict[str, str] = {}
    for m in models:
        for use in m["uses"]:
            cid = use.get("cid", "")
            if cid and cid not in names:
                try:
                    meta = store.inference.in_use.campaign_meta(cid)
                except (store.CampaignNotFound, *_UNPRICED_IN_USE_UNREADABLE):
                    meta = {}
                names[cid] = str(meta.get("name") or cid)
    return names


def _items_unpriced_models(cid: str) -> list[dict]:
    """One item per (provider, model) in use with no price; a model on two
    providers is two items, each opening its own provider's rates (M6) -- or
    the pricing table, while those cannot be written (`rates_editable`)."""
    models = _unpriced_in_use() or []
    names = _campaign_names(models)
    editable = rates_editable()
    # A provider id is a `safe_id` slug, which never holds `:`, so the first
    # colon always ends it and this id cannot collide.
    return [{"id": f"{m['provider_id']}:{m['model']}",
             "label": f"{m['model']} on {m['provider_name']}",
             "detail": "Used by " + ", ".join(_use_label(u, names) for u in m["uses"]),
             "fix": (_model_rates_href(m["provider_id"], m["model"]) if editable
                     else "/models#rates")}
            for m in models]


def _items_embeddings(cid: str) -> list[dict]:
    return [{"id": "embeddings", "label": "Semantic matching",
             "detail": "No embeddings connection and model are set",
             "fix": "/models/edit"}]
