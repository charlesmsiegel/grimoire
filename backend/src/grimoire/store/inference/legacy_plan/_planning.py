"""Per-scope plans and one in-memory overlay whose virtual presets/readers are shared by its settings and reroll selections."""

from __future__ import annotations

from collections.abc import Mapping
from typing import NamedTuple

from ... import inference_keys as keys
from ... import inference_retired as retired
from ... import routing, sampler_presets
from .. import cascade
from . import _contracts, _mapping, _reasoning


# ---- the plans ----
def global_plan(cfg: Mapping[str, str], lookup: _contracts.Lookup, presets: _contracts.PresetRead) -> _contracts.Plan:
    """`config.md`'s plan.

    - Below format 2: `mapped` is the whole mapping as the migration persists
      it (`global_mapped`: `OWNED_GLOBAL_KEYS`, "" where unset), `facts` the
      model-facts overlay, and the derivation and notes are over the mapped
      slots.
    - At format 2 and not retired: the derivation and notes only.
    - Retired (`RETIRED_KEY`) at format 2, or a newer build's: nothing, and
      nothing read.
    - Retired below format 2 (`_settled`): `mapped` and `facts`, as the switch
      will persist them, and no derivation -- retirement removes that
      scope's legacy keys and derives nothing in a retired scope -- with a
      note for each GLM slot that loses its effort so (`_stranded`).
    """
    if _contracts._settled(cfg):
        return _contracts.empty()
    if keys.is_current(cfg):
        return _reasoning._planned(cfg, glob={}, mapped={}, model_facts={}, scope=_contracts.GLOBAL_SCOPE,
                        campaign=False, conn=lookup, presets=presets)
    mapped = _mapping.global_mapped(cfg, lookup)
    if _contracts._retired(cfg):
        return _contracts.Plan(mapped, {}, _mapping._facts_overlay(lookup), (),
                    _reasoning._stranded({**cfg, **mapped}, scope=_contracts.GLOBAL_SCOPE, campaign=False,
                              conn=lookup, presets=presets))
    return _reasoning._planned({**cfg, **mapped}, glob={}, mapped=mapped,
                    model_facts=_mapping._facts_overlay(lookup), scope=_contracts.GLOBAL_SCOPE,
                    campaign=False, conn=lookup, presets=presets)


def campaign_plan(meta: Mapping[str, str], *, glob: Mapping[str, str], global_current: bool,
                  lookup: _contracts.Lookup, presets: _contracts.PresetRead, cid: str) -> _contracts.Plan:
    """Campaign `cid`'s plan (`cid` names its notes' scope).

    The campaign is planned as the migration will persist it (planned equals
    persisted, spec 15; ratification item 10): `migrate.campaign` maps an
    unmarked campaign and leaves a marked one -- or a newer build's -- exactly
    as it stands, whatever `config.md`'s format. So a marked campaign's legacy
    `route_<k>` keys, frozen for older builds, are never mapped over its own
    format-2 keys, even under a legacy `config.md`. `glob` is the global
    settings as format 2 sees them (`planned(cfg, global_plan(cfg, ...))`):
    what the campaign inherits, which its notes are judged against.
    `global_current` (whether `config.md` is at format 2) is the caller's to
    say and decides nothing here any more; it is kept for the callers that
    pass it.

    - Unmarked: `mapped` and the derivation.
    - Unmarked but carrying the retirement marker: `mapped` alone. The
      migration maps it, as it maps any unmarked campaign, and retirement
      derives nothing in a scope that says it is retired (N1); a GLM slot
      that loses its effort so is noted (`_stranded`).
    - Marked, not retired: the derivation only.
    - Marked and retired, or a newer build's: nothing, and nothing read.
    """
    del global_current  # the campaign's own marker decides (see above)
    scope = _contracts.campaign_scope(cid)
    if _contracts._settled(meta):
        return _contracts.empty()
    if keys.is_current(meta):
        return _reasoning._planned(meta, glob=glob, mapped={}, model_facts={}, scope=scope,
                        campaign=True, conn=lookup, presets=presets)
    mapped = _mapping.campaign_mapped(meta, lookup)
    if _contracts._retired(meta):
        return _contracts.Plan(mapped, {}, {}, (), _reasoning._stranded({**meta, **mapped}, scope=scope,
                                                  campaign=True, conn=lookup,
                                                  presets=presets))
    return _reasoning._planned({**meta, **mapped}, glob=glob, mapped=mapped, model_facts={},
                    scope=scope, campaign=True, conn=lookup, presets=presets)


# ---- the in-memory overlay (play's read) ----
class Overlay(NamedTuple):
    """The stored settings as format 2 sees them, for one global scope and
    (optionally) one campaign: what `resolve` resolves, in memory."""

    #: `config.md`'s settings as format 2 sees them: `mapped`, then `repoint`.
    cfg: dict[str, str]
    #: The campaign's, likewise ({} with no campaign).
    meta: dict[str, str]
    #: The derived presets, by id, in `sampler_presets.read_preset`'s shape:
    #: virtual until retirement writes them.
    presets: Mapping[str, dict]
    #: provider -> model -> the facts its legacy fields state, `{}` for none
    #: (below format 2): what the migration's step 3 will adopt, which a
    #: reader lays over the facts file as `facts.adopted` does.
    facts: Mapping[str, Mapping[str, dict]]
    #: What no preset can carry, global first, then the campaign's.
    notes: tuple[retired.Note, ...]
    #: `config.md`'s settings as the migration will persist them: `mapped`
    #: without `repoint`, so every preset id in it names a preset file (N2).
    #: What a settings view shows as stored, and so what a write can name back.
    stored: dict[str, str]
    #: The campaign's, likewise ({} with no campaign).
    stored_meta: dict[str, str]
    #: The Embedding role's `(provider, model)` as `config.md` holds it, both
    #: stripped and never judged (`embedding_role`): a legacy pair below
    #: format 2, even one the mapping turns off. What a guard that asks
    #: whether an edit moves the role's space compares (`embed_space.moved_by`).
    embedding: tuple[str, str]
    #: Whether `config.md` is below format 2 and was mapped here (not
    #: settled): a format-1 store. It still plays as format 2 everywhere but
    #: two places, where a format-1 store has always answered differently
    #: and still does (spec 5.6; user ruling 2026-10-09): a reroll naming a
    #: provider runs that provider's OWN model and preset (`selection`), and
    #: a `missing_key` refusal keeps its format-1 sentence
    #: (`resolve.unusable`, naming a pin by its `legacy_route`).
    legacy: bool = False
    #: The soft lookup this overlay planned through (None when nothing was
    #: planned): what `selection` reads, so a format-1 reroll reads the
    #: connection exactly as the mapping did, with no second plan.
    lookup: _contracts.Lookup | None = None
    #: The memoised preset reader the plan used, answering with the derived
    #: presets (`presets`) first; None when nothing was planned.
    read_preset: _contracts.PresetRead | None = None

    def selection(self, conn_id: str, model: str = "") -> cascade.Selection:
        """Connection `conn_id` as the planner plans a slot that names it --
        what a format-1 reroll naming the provider runs on (spec 5.6): its own
        model (or `model`, when the reroll names one) and its own preset, then
        `derived`, so a GLM connection's legacy reasoning effort rides a
        derived preset exactly as it does on a stored slot. Read through this
        overlay's lookup, so the record fallback for a stripped connection
        applies as it does to the mapping. Empty model and preset for an
        unknown id or one that sets none."""
        sel = _mapping._selection(conn_id, self.lookup or _no_connection)
        return self.derived(
            cascade.Selection(sel["provider"], model or sel["model"], sel["preset"]))

    def derived(self, chosen: cascade.Selection) -> cascade.Selection:
        """`chosen` with the planner's derivation of that slot applied
        (`_derived_for`), judged on the model it sends: a format-1 reroll --
        naming a provider, or a model alone on the standing provider -- sends
        a GLM connection's legacy effort wherever the legacy wire did. A
        preset that already sets an effort (a derived one included) is left
        as it is. A derived preset no stored slot planned is added to
        `presets`, so the resolution that asked can read it."""
        if self.read_preset is None or not isinstance(self.presets, dict):
            return chosen
        made = _reasoning._derived_for(chosen, self.lookup or _no_connection, self.read_preset,
                            self.read_preset)
        if made is None:
            return chosen
        got = _virtual(made)
        held = self.presets.setdefault(made.id, got)
        if (held["name"], held["params"]) != (got["name"], got["params"]):
            raise _contracts.OverlayError(f"derived preset {made.id!r} planned with two bodies")
        return chosen._replace(preset=made.id)

    def legacy_route(self, route: routing.Route | None) -> str:
        """The legacy route `route`'s settings were stored under at format 1
        (its own key, or the parent a split route came out of); "" for none:
        what a format-1 refusal names a pin by."""
        return routing.legacy_key(route) if route is not None else ""


def _no_connection(_conn_id: str) -> dict | None:
    return None


def _virtual(made: _contracts.Derived) -> dict:
    """A derived preset in `sampler_presets.read_preset`'s shape."""
    return {"id": made.id, "name": made.name, "params": dict(made.params),
            "notes": "", "source": ""}


def _as_current(meta: Mapping[str, str], plan: _contracts.Plan) -> dict[str, str]:
    """`planned(meta, plan)`, marked current when the plan mapped it: the
    scope as the migration will write it, which a reader asking
    `inference_keys.is_current` of it sees as such."""
    out = _contracts.planned(meta, plan)
    if plan.mapped and not keys.is_current(meta):
        out[keys.FORMAT_KEY] = keys.CURRENT_FORMAT
    return out


def _persisted(meta: Mapping[str, str], plan: _contracts.Plan) -> dict[str, str]:
    """`_as_current` without the repoint: the scope as the migration persists
    it, before retirement writes the derived presets."""
    return _as_current(meta, plan._replace(repoint={}))


def overlay(cfg: Mapping[str, str], meta: Mapping[str, str], *, cid: str = "") -> Overlay:
    """`cfg` and `meta` (a campaign's frontmatter, {} for none; `cid` names
    its notes' scope) as format 2 sees them, planned in memory.

    Identity, with nothing read, when `config.md` is settled (retired and
    current, or a newer build's) and the campaign is absent or settled too. Otherwise each scope is `planned`
    (`mapped | repoint`, N2) through one fail-soft lookup (`lookup("soft")`:
    an unreadable connection is no connection, as the translation always
    read it) and `sampler_presets.read_preset`, memoised for the call.

    The campaign is planned against the global settings as planned here,
    and reads the global derived presets before the store's: a campaign
    derivation whose slug a global one already holds with another body takes
    the suffix (`derive`), so neither scope's derived preset ever shadows or
    aliases the other's under one id. A store a newer build switched is read
    as format 2, best effort: its `config.md` plans nothing, and its
    campaigns are planned as they would be under a current one."""
    if _contracts._settled(cfg) and (not meta or _contracts._settled(meta)):
        return Overlay(dict(cfg), dict(meta), {}, {}, (), dict(cfg), dict(meta),
                       _mapping.embedding_role(cfg))
    conn = _mapping.lookup(mode="soft")
    virtual: dict[str, dict] = {}
    seen: dict[str, dict | None] = {}

    def presets(pid: str) -> dict | None:
        if pid in virtual:
            return virtual[pid]
        if pid not in seen:
            seen[pid] = sampler_presets.read_preset(pid)
        return seen[pid]

    def keep(plan: _contracts.Plan) -> None:
        for made in plan.presets:
            got = _virtual(made)
            held = virtual.setdefault(made.id, got)
            # `derive` reads `presets`, which answers with `virtual` first, so
            # an id it hands back is free or holds this very preset.
            if (held["name"], held["params"]) != (got["name"], got["params"]):
                raise _contracts.OverlayError(f"derived preset {made.id!r} planned with two bodies")

    gplan = global_plan(cfg, conn, presets)
    keep(gplan)
    glob = _as_current(cfg, gplan)
    stored = _persisted(cfg, gplan)
    legacy = not _contracts._settled(cfg) and not keys.is_current(cfg)
    if not meta:
        return Overlay(glob, {}, virtual, gplan.facts, gplan.notes, stored, {},
                       _mapping.embedding_role(cfg), legacy, conn, presets)
    cplan = campaign_plan(meta, glob=glob,
                          global_current=keys.is_current(cfg) or keys.is_newer(cfg),
                          lookup=conn, presets=presets, cid=cid)
    keep(cplan)
    return Overlay(glob, _as_current(meta, cplan), virtual, gplan.facts,
                   gplan.notes + cplan.notes, stored, _persisted(meta, cplan),
                   _mapping.embedding_role(cfg), legacy, conn, presets)
