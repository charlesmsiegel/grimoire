"""What the stored model settings select, and which of those nothing prices.

`selections` is every stored selection that names a provider: the global
generative roles and their fallbacks, each route's chosen pin, the Embedding
role, and then each campaign's own roles, fallbacks and chosen campaign-scoped
pins, by campaign id -- the order a provider's "used by" list has always
shown (`settings.used_by`, which filters it to one provider). It is read
through the legacy translation, so a store the migration has not reached
reports what plays. A selection with a blank model, or one naming a provider
that no longer exists, is still a stored selection and is kept; a pin its
route does not choose is not one; a campaign that cannot be read names nothing.

`unpriced` is the Housekeeping chore's question (spec 9.2), and does the
filtering `selections` deliberately does not: of the distinct
`(provider, model)` pairs selected, the ones whose provider exists, whose
model is not blank (an unset Claude model runs its default, `facts.model_of`),
whose provider's preset does not report prices (`reports_price`: OpenRouter
and the Claude subscription never count), and which no rate prices
(`pricing.rate_for_call`: the model's own rates, then `pricing.json`). A zero
rate somebody entered is a price. A model served by two providers is two
pairs, because each is priced on its own provider. A generative use of a
model `resolve.native_only` serves natively is not one a rate could price --
its decisions are native (`usage._modellable`), and its generations are
refused before they are sent -- so it is left out (`_native`). It is computed
from configuration (and, for an unpriced pair, the model's cached catalog row
and facts) and never reads the usage ledger, so it costs the same however
long the library has been played.

**What it costs, and what is memoized.** Only a campaign's frontmatter parse
(`campaign_meta`), on the stat signature of its `campaign.md`, in a pool of its
own. The translation, the connection lookup and the format marker come from
other files and are read fresh on every call -- they are in memory and cheap,
and a memo of anything derived from them would go stale when a provider is
deleted or a legacy connection's model is edited, neither of which touches a
`campaign.md`. So a walk is one parse per `campaign.md` that changed since the
last one, plus in-memory work per campaign: the class of cost the library
to-do list (`routes.todo.live("")`) already pays per campaign. That is why the
"detail only" note at `routes.config.get_connection` still holds for
`used_by` (it is asked per provider, so a list would ask it once per row) but
is not a reason to keep this walk off the to-do list, which asks it once.

This module never imports `settings`, which imports it.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import NamedTuple

from .. import config, pricing, routing, statcache
from .. import inference_keys as keys
from ..campaigns import paths as campaign_paths
from ..frontmatter import parse_frontmatter
from . import capabilities, facts, providers, resolve, translate

#: `campaign_meta`'s memo, apart from `statcache`'s shared pool: a walk touches
#: every campaign, and the shared FIFO is what other sweeps rely on staying
#: warm. One small dict per campaign; an older version of a rewritten file
#: stays until the FIFO reaches it, which costs memory inside this budget and
#: never a wrong answer (the key is the file's signature).
_META_POOL: dict = {}
_META_ENTRIES = 1024

#: What makes a campaign name nothing in `selections`, as `used_by` has
#: always treated it.
_UNREADABLE_CAMPAIGN = (campaign_paths.CampaignNotFound, OSError, UnicodeDecodeError,
                        ValueError)


class Use(NamedTuple):
    """One stored selection: `provider` (as stored, trimmed) and `model` (as
    the translation reads it, possibly ""), what kind of slot holds it, the
    role or route key (`embedding` for the Embedding role), and where."""

    provider: str
    model: str
    kind: str       # "role" | "fallback" | "route"
    key: str
    scope: str      # "global" | "campaign"
    cid: str        # "" at global scope


def _parse(path: Path) -> dict:
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return meta


def campaign_meta(cid: str, *, strict: bool = True, memo: bool = True) -> dict:
    """A campaign's frontmatter; `CampaignNotFound` when there is none. Not
    `strict` (the view), one that cannot be read is no campaign choice at all,
    as `resolve.campaign_meta` treats it -- the rows still say what plays.

    The parse is memoized on `campaign.md`'s stat signature (and a file inside
    `statcache`'s racy window is parsed and not cached). The answer is a
    shallow copy, so a caller that edits it cannot edit the memo.

    `memo=False` reads the file fresh, and a write decides on that: a
    signature is only as good as the filesystem's clock (a sync client that
    restores an mtime, or a filesystem that ticks coarser than the racy
    window, leaves a changed file with its old signature), and a write that
    parses one file once gains nothing from the memo to set against that."""
    path = campaign_paths.campaign_meta_path(cid)
    if not path.exists():
        raise campaign_paths.CampaignNotFound(cid)
    try:
        if not memo:
            return _parse(path)
        meta = statcache.memo("inference:campaign_meta", statcache.signature(path),
                              partial(_parse, path),
                              pool=_META_POOL, max_entries=_META_ENTRIES)
    except (OSError, UnicodeDecodeError):
        if strict:
            raise
        return {}
    return dict(meta)


def text(view: dict, key: str) -> str:
    return str(view.get(key, "") or "")


def _uses(own: dict, scope: str, cid: str, routes: list[routing.Route]) -> list[Use]:
    """The roles, fallbacks and chosen pins in one scope's settings (already in
    the current layout) that name a provider. A pin its route does not choose
    (`use_<k>` other than the pin) is not a use."""
    slots = [(kind, role, partial(key, role))
             for role in keys.GENERATIVE_ROLES
             for kind, key in (("role", keys.role_key), ("fallback", keys.fallback_key))]
    slots += [("route", route.key, partial(keys.pin_key, route.key))
              for route in routes
              if text(own, keys.use_key(route.key)).strip() == keys.PIN]
    out: list[Use] = []
    for kind, name, part in slots:
        provider = text(own, part("provider")).strip()
        if provider:
            out.append(Use(provider, text(own, part("model")), kind, name, scope, cid))
    return out


def _selections(cfg: dict, lookup: translate.Lookup) -> list[Use]:
    out = _uses(translate.global_view(cfg, lookup), "global", "", list(routing.ROUTES))
    provider, model = translate.embedding_role(cfg)
    if provider:
        out.append(Use(provider, model, "role", "embedding", "global", ""))
    current = keys.is_current(cfg)
    scoped = [r for r in routing.ROUTES if r.campaign_scoped]
    for cid in campaign_paths.campaign_ids():
        try:
            meta = campaign_meta(cid)
        except _UNREADABLE_CAMPAIGN:
            continue
        own = translate.campaign_view(meta, lookup, current=current)
        out += _uses(own, "campaign", cid, scoped)
    return out


def selections() -> list[Use]:
    """Every stored selection that names a provider, in `used_by`'s order
    (see the module docstring). Raises what `config.read_config` raises."""
    return _selections(config.read_config(), resolve.connection_lookup())


def _where(use: Use) -> dict:
    out = {"kind": use.kind, "key": use.key, "scope": use.scope}
    if use.scope == "campaign":
        out["cid"] = use.cid
    return out


def unpriced() -> list[dict]:
    """`[{provider_id, provider_name, model, uses: [{kind, key, scope, cid?}]}]`:
    each distinct `(provider, model)` in use that nothing would price, sorted
    by provider name, then model. `model` is the one its rates are stated
    under (`facts.model_of`). Raises what `config.read_config` raises.

    A generative use of a native-only model (`_native`) is left out, since
    no rate prices it: a pair used only that way is not listed, and one used
    another way too (the Embedding role) is listed for that use alone."""
    lookup = resolve.connection_lookup()
    table = pricing.read_pricing()
    rates = pricing.provider_rates()
    found: dict[tuple[str, str], dict | None] = {}
    native: dict[tuple[str, str], bool] = {}
    for use in _selections(config.read_config(), lookup):
        raw = lookup(use.provider)
        if raw is None:
            continue
        model = facts.model_of({"kind": raw.get("kind"), "model": use.model})
        if not model.strip():
            continue
        pair = (use.provider, model)
        if pair not in found:
            # The configured model is the name asked for, and all this side
            # knows of what will answer: `rate_for_call` matches the table
            # under the asked-for name too, so a ledger row that answered as
            # a dated snapshot is priced by the same entry that clears this.
            priced = (providers.infer(raw).reports_price
                      or pricing.rate_for_call(table, rates, provider_id=use.provider,
                                               model=model, requested_model=model)
                      is not None)
            found[pair] = None if priced else {
                "provider_id": use.provider,
                "provider_name": str(raw.get("name") or use.provider),
                "model": model, "uses": []}
        entry = found[pair]
        if entry is None or _native(use, raw, model, native):
            # Not a use a rate could price; the pair stays listed only if
            # another of its uses is.
            continue
        entry["uses"].append(_where(use))
    return sorted((e for e in found.values() if e is not None and e["uses"]),
                  key=lambda e: (e["provider_name"], e["model"], e["provider_id"]))


def _native(use: Use, raw: dict, model: str, memo: dict[tuple[str, str], bool]) -> bool:
    """Whether `use` is a generative slot -- any generative role, a fallback,
    or a route pin -- holding a model `resolve.native_only` serves natively,
    the resolver's own rule. No rate prices any such use: every decide route
    that resolves through it, whichever role it walks, is answered on the
    provider's decisions endpoint (never modelled, `usage._modellable`), and
    a generate route on it is refused by the seam before anything is sent
    (its `generate` is known `no`). The Embedding role is the exception: it
    embeds whatever the model generates, and a rate prices that. The model's
    capabilities are read once per pair, and only for a pair nothing prices."""
    if use.kind == "role" and use.key == "embedding":
        return False
    pair = (use.provider, model)
    if pair not in memo:
        memo[pair] = resolve.native_only(capabilities.caps_for(raw, model))
    return memo[pair]
