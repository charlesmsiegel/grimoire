"""The one planner for the legacy settings layout (inference slice I).

A store written before the roles format keeps `active_connection_id`,
`fallback_connection_id`, `embeddings_*`, one `route_<k>` / `preset_<k>` pair
per legacy route, and -- on each connection -- the model fields (`model`,
`sampler_preset`, `vision`, `prefill`, `post_process`, `reasoning_effort`)
that format 2 keeps elsewhere. This module is what those mean in the new
layout, planned once per scope as a `Plan`:

- `mapped`: the legacy keys in the new vocabulary -- roles, fallbacks, pins
  and route presets -- as the migration persists them (`migrate`, steps 5-7
  and 8), byte for byte what it has always written (N2). The mapping itself
  is `global_mapping` / `campaign_mapping` (once `translate`'s, which slice I
  deleted).
- `facts`: each connection's legacy `vision`, `prefill` and `post_process`
  (where they differ from the defaults) as its model's facts -- the values
  the migration's step 3 copies (`stated`), in memory.
- `repoint` and `presets`: the derived reasoning presets (ruling 4). A
  selection slot on an `openai_compatible` GLM provider whose legacy
  `reasoning_effort` is one a preset can carry (`REPRESENTABLE`) is pointed at
  a preset holding its own preset's params plus that effort, so its wire does
  not change once the legacy field stops being read. The migration never
  persists a repoint; retirement does, after writing the presets.
- `notes`: what no preset can carry (ruling 5) -- a route-level preset that
  sets no reasoning effort over such a provider, which is shared with the
  route's fallback and so is left alone (ratification item 3).

A plan reads through the lookup and the preset reader it is handed, plus the
store's connection listing for the facts and each model's cached catalog and
facts for the Embedding role (`legacy_embeds`). The planner itself writes no
setting. Its connection reads do not promise that: every one of them
(`read_connection_raw`, `read_connection_strict`, `list_connections`) runs
`llm_connections.ensure_migrated()` first, which on a store whose
connections were never seeded writes `llm_connections/` and can write
`active_connection_id` into `config.md` -- the format-1 seeding every
connection read has always done. A scope carrying `RETIRED_KEY` beside the
current format marker plans nothing and reads nothing.

How strictly a plan reads is its lookup's (`lookup(mode=...)`): play reads
fail-soft, as the translation always has; the migration and retirement read
strictly, and an unreadable file raises rather than being planned as absent.

Trim rule, kept from the legacy cascade: `route_*`, `preset_*` and
`embeddings_*` values are stripped; `active_connection_id` and
`fallback_connection_id` are used raw (so a padded id is later rejected by the
existence check exactly as it always was). A route naming a connection that
does not exist is still mapped, to a pin on that id: walking past a dangling
choice is the cascade's job, not this module's.

Play reads the legacy layout through `overlay` alone, in memory: the global
and campaign settings as format 2 sees them (`mapped`, then `repoint`), the
derived presets as virtual presets, the facts overlay and the notes. Nothing
on that path writes; the migration persists `mapped`, and retirement the
rest. `resolve` makes the one call (`resolve._overlay`).

A leaf of `store/inference/`: it imports neither `resolve` nor `migrate`,
which both read the legacy layout through it.
"""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Callable, Collection, Iterator, Mapping
from typing import Literal, NamedTuple

from ... import llm_reasoning, llm_sampling
from .. import config, llm_connections, locks, paths, routing, sampler_presets
from .. import inference_keys as keys
from .. import inference_retired as retired
from . import capabilities, cascade, facts, providers

#: A raw connection by id (legacy fields included), or None. What
#: `lookup(mode=...)` builds; a plain function will do in a test.
#: `llm_connections.Lookup`, re-bound so the planner's callers can annotate
#: with either name.
Lookup = llm_connections.Lookup
#: A sampler preset by id (`sampler_presets.read_preset`'s shape), or None.
PresetRead = Callable[[str], dict | None]

#: The retirement marker (ruling 15): a scope carrying it is planned as empty.
RETIRED_KEY = keys.RETIRED_KEY

#: The legacy GLM efforts a preset can carry: the levels GLM takes that a
#: preset's reasoning effort can say. Computed, so the two vocabularies decide.
REPRESENTABLE: tuple[str, ...] = tuple(
    e for e in llm_reasoning.GLM_EFFORTS if e in llm_sampling.REASONING)

#: The legacy routes' preset keys: spelled the same in both layouts
#: (`inference_keys.GLOBAL_KEYS`), so the migration leaves them as they are.
SHARED_PRESET_KEYS = frozenset(routing.PRESET_CONFIG_KEYS)

#: The global keys the migration writes, every one on every switch: the
#: format-2 layout without the marker (written beside them) and without the
#: preset keys both layouts share.
OWNED_GLOBAL_KEYS: tuple[str, ...] = tuple(
    k for k in keys.GLOBAL_KEYS
    if k != keys.FORMAT_KEY and k not in SHARED_PRESET_KEYS)

#: A note's scope on `config.md`; a campaign's is `campaign_scope(cid)`
#: (`retired`'s, so the record and the view spell them alike).
GLOBAL_SCOPE = retired.GLOBAL_SCOPE
campaign_scope = retired.campaign_scope


class Derived(NamedTuple):
    """A derived reasoning preset: its id, name and params."""

    id: str
    name: str
    params: dict


class Plan(NamedTuple):
    #: The migration's fields: today's translation, byte for byte (N2).
    mapped: dict[str, str]
    #: The derived presets' ids, by the preset key of each slot they replace,
    #: applied over `mapped`.
    repoint: dict[str, str]
    #: provider -> model -> the facts its legacy fields state, `{}` for none:
    #: every connection the store lists, below format 2 only.
    facts: dict[str, dict[str, dict]]
    #: The presets `repoint` names, each once.
    presets: tuple[Derived, ...]
    notes: tuple[retired.Note, ...]


def empty() -> Plan:
    """What a retired scope (or one this build does not own) plans: nothing.
    A fresh plan on every call, so no caller can mutate one another holds."""
    return Plan({}, {}, {}, (), ())


def planned(meta: Mapping[str, str], plan: Plan) -> dict[str, str]:
    """A scope's settings as format 2 sees them under `plan`: `meta`, then
    `mapped`, then `repoint` over both. For a campaign plan's `glob`, pass
    `config.md`'s raw settings and its `global_plan`."""
    return {**meta, **plan.mapped, **plan.repoint}


def _retired(meta: Mapping) -> bool:
    return str(meta.get(RETIRED_KEY, "") or "").strip() == "1"


def is_retired(meta: Mapping) -> bool:
    """Whether a `config.md` or `campaign.md` carries the retirement marker
    (whatever its format marker says)."""
    return _retired(meta)


def _settled(meta: Mapping) -> bool:
    """Whether a scope plans nothing at all: a newer build's, or retired AND
    marked current. A retirement marker on a scope with no format marker (a
    copy restored beside an older `config.md`, a hand edit) does not settle
    it: the migration still maps that scope -- `migrate.campaign` and the
    switch go by the format marker alone -- so the planner maps it too
    (planned equals persisted; slice I, N1). Retirement then derives
    nothing there, because the scope says it is retired."""
    return keys.is_newer(meta) or (_retired(meta) and keys.is_current(meta))


# ---- the lookups ----
#: The ways a fail-soft read can fail, every one of which reads as "no such
#: connection" -- a dangling reference is walked past, never raised.
_SOFT_UNREADABLE = (llm_connections.ConnectionNotFound, locks.StoreBusy,
                    OSError, UnicodeDecodeError)


def _soft_read(conn_id: str) -> dict | None:
    try:
        return llm_connections.read_connection_raw(conn_id)
    except _SOFT_UNREADABLE:
        return None


def _holds_model_fields(raw: Mapping) -> bool:
    """Whether a raw connection holds any non-empty legacy model field."""
    for field in llm_connections.MODEL_FIELDS:
        value = raw.get(field)
        if value is True or (isinstance(value, str) and value.strip()):
            return True
    return False


class _Reader:
    """A memoised connection lookup that knows how strictly it reads: the
    plan around it reads what else it needs (a model's facts, for the
    Embedding role; the retirement record) as strictly as the lookup does.

    A connection whose file is there and holds no non-empty legacy model
    field -- stripped by retirement, or written that way by a C-H edit after
    the strip -- answers with the fields the retirement record holds for it
    (`retired.read()['fields']`), so a campaign that arrives unmarked after
    the strip still maps to the model it named (C3, N6, R2-2). Never for an
    absent file: a deleted provider does not come back (N20). A strict lookup
    reads the record strictly, and an unreadable record raises
    `retired.RecordUnreadableError` rather than answering with an empty
    pin.

    A file with no legacy field and no entry is either one created at format
    2 with none -- answered as it is -- or one the strip took them off whose
    entry has not arrived (a partial sync, a record restored apart from the
    connections). The strip's marker (`llm_connections.STRIPPED_KEY`) tells
    the two apart: a strict lookup raises `retired.EntryMissingError` for a
    marked file with no entry, so nothing is persisted from a connection read
    as having no model and no effort, and the scope is retried on the next
    start. A soft lookup -- play, which persists nothing -- answers it as it
    stands."""

    def __init__(self, read: Callable[[str], dict | None], *, strict: bool):
        self._read = read
        self.strict = strict
        self._seen: dict[str, dict | None] = {}
        self._record: dict[str, dict[str, str]] | None = None

    def _fields(self, conn_id: str) -> dict[str, str]:
        if self._record is None:
            self._record = retired.read(strict=self.strict)["fields"]
        return self._record.get(conn_id, {})

    def _answer(self, conn_id: str) -> dict | None:
        raw = self._read(conn_id)
        if raw is None or _holds_model_fields(raw):
            return raw
        recorded = self._fields(conn_id)
        if not recorded:
            if self.strict and llm_connections.stripped(conn_id):
                raise retired.EntryMissingError(conn_id)
            return raw
        out = dict(raw)
        for field, value in recorded.items():
            if field in llm_connections.MODEL_FIELDS:
                out[field] = value == "true" if field == "prefill" else value
        return out

    def __call__(self, conn_id: str) -> dict | None:
        if conn_id not in self._seen:
            self._seen[conn_id] = self._answer(conn_id)
        return self._seen[conn_id]


def lookup(*, mode: Literal["soft", "migrate", "retire"]) -> Lookup:
    """A raw connection by id, memoised for one plan.

    - `soft`: play's read. A file that cannot be read is no connection, as
      the translation has always read it.
    - `migrate`: the migration's read, C's (A12): `read_connection_strict`, so
      a file that is there and empty, unfenced, undecodable or holds no `kind`
      RAISES `llm_connections.ConnectionUnreadableError`, and an absent one
      answers None. What is read here is written down beside the marker.
    - `retire`: the same strict read, for retirement and every write it makes
      (N1): an unreadable file stops that scope rather than being planned as
      absent.

    Every mode falls back to the retirement record for a stripped connection
    (`_Reader`): `soft` reads the record fail-soft; `migrate` and `retire`
    read it strictly whenever they consult it (R2-2, R3-2) -- an entry there
    proves a strip happened, whatever `config.md` says -- and refuse a file
    the strip marked whose entry has not arrived (`retired.EntryMissingError`)."""
    if mode == "soft":
        return _Reader(_soft_read, strict=False)
    if mode in ("migrate", "retire"):
        return _Reader(llm_connections.read_connection_strict, strict=True)
    raise ValueError(f"unknown lookup mode: {mode!r}")


def _strict(conn: Lookup) -> bool:
    return getattr(conn, "strict", False) is True


# ---- the mapping ----
def _selection(conn_id: str, conn: Lookup) -> dict[str, str]:
    """`{provider, model, preset}` for a connection id (model and preset are
    empty when the connection is unknown or sets none)."""
    raw = conn(conn_id) or {}
    return {
        "provider": conn_id,
        "model": str(raw.get("model") or ""),
        "preset": str(raw.get("sampler_preset") or "").strip(),
    }


def _pins_and_presets(meta: Mapping, conn: Lookup, scoped_only: bool,
                      only: Collection[str] | None) -> dict:
    out: dict = {}
    for route in routing.ROUTES:
        if scoped_only and not route.campaign_scoped:
            continue
        if only is not None and route.key not in only:
            continue
        legacy = routing.legacy_key(route)
        chosen = str(meta.get(routing.config_key(legacy), "") or "").strip()
        if chosen:
            out[keys.use_key(route.key)] = keys.PIN
            sel = _selection(chosen, conn)
            for part in keys.PARTS:
                out[keys.pin_key(route.key, part)] = sel[part]
        preset = str(meta.get(routing.preset_key(legacy), "") or "").strip()
        if preset:
            out[keys.preset_key(route.key)] = preset
    return out


def embedding_view(cfg: Mapping) -> dict[str, str]:
    """The Embedding role's slot in the current layout's keys, both values
    stripped: `{role_embedding_provider, role_embedding_model}`. A current
    config reads them from those keys, a legacy one from `embeddings_*`. No
    lookup: the embedding model is read from config alone.

    What `resolve.embedding` hands the cascade. Unlike `global_mapping`, it
    always strips (the `embeddings_*` trim rule, kept at both formats) and
    always carries both keys."""
    if keys.is_current(cfg):
        provider = cfg.get(keys.role_key("embedding", "provider"), "")
        model = cfg.get(keys.role_key("embedding", "model"), "")
    else:
        provider = cfg.get("embeddings_connection_id", "")
        model = cfg.get("embeddings_model", "")
    return {keys.role_key("embedding", "provider"): str(provider or "").strip(),
            keys.role_key("embedding", "model"): str(model or "").strip()}


def embedding_role(cfg: Mapping) -> tuple[str, str]:
    """`(provider, model)` as stored for the Embedding role (`embedding_view`'s
    two values): the stored pair, not the resolution (`resolve.embedding`)."""
    view = embedding_view(cfg)
    return (view[keys.role_key("embedding", "provider")],
            view[keys.role_key("embedding", "model")])


def global_mapping(cfg: Mapping, conn: Lookup, *,
                   only: Collection[str] | None = None) -> dict:
    """A legacy `config.md`'s settings in the current layout (`only`: the
    route keys whose pins and presets to map; None for all). The format is
    the caller's to decide: this maps whatever it is given as legacy."""
    out: dict = {}
    active = str(cfg.get("active_connection_id", "") or "")
    if active:
        for part, value in _selection(active, conn).items():
            out[keys.role_key("primary", part)] = value
    fallback = str(cfg.get("fallback_connection_id", "") or "")
    if fallback:
        sel = _selection(fallback, conn)
        for role in keys.GENERATIVE_ROLES:
            for part, value in sel.items():
                out[keys.fallback_key(role, part)] = value
    provider, model = embedding_role(cfg)
    if provider:
        out[keys.role_key("embedding", "provider")] = provider
        out[keys.role_key("embedding", "model")] = model
    out.update(_pins_and_presets(cfg, conn, scoped_only=False, only=only))
    return out


def campaign_mapping(meta: Mapping, conn: Lookup, *,
                     only: Collection[str] | None = None) -> dict:
    """A legacy campaign's overrides in the current layout. A campaign carried
    only route and preset choices, for the campaign-scoped routes, so that is
    all there is to map."""
    return _pins_and_presets(meta, conn, scoped_only=True, only=only)


# ---- the mapping as the migration persists it ----
def _selection_keys() -> Iterator[Callable[[str], str]]:
    """The key builder of every selection a scope can hold: each generative
    role and its fallback, and each route's pin."""
    for role in keys.GENERATIVE_ROLES:
        yield functools.partial(keys.role_key, role)
        yield functools.partial(keys.fallback_key, role)
    for route in routing.ROUTES:
        yield functools.partial(keys.pin_key, route.key)


def enriched(fields: dict[str, str], conn: Lookup) -> dict[str, str]:
    """`fields` with an unset Claude model written as `opus` wherever it is a
    selection's model -- what the Claude adapter runs an unset model as, so
    nothing that resolves changes; what a later edit starts from does."""
    out = dict(fields)
    for key in _selection_keys():
        provider = out.get(key("provider"), "")
        if not provider or out.get(key("model")):
            continue
        raw = conn(provider)
        if raw is not None and raw.get("kind") == "claude":
            out[key("model")] = config.DEFAULT_CLAUDE_MODEL
    return out


def _persistable(view: dict) -> dict[str, str]:
    """A mapped campaign view as the keys to write: the shared preset keys
    left alone, and an empty value dropped -- an absent key and "" read the
    same, a campaign's frontmatter is a file people read by hand, and a
    campaign is migrated and marked in one write, so nothing a partial run
    wrote is left for a later one to clear."""
    return {k: str(v) for k, v in view.items()
            if k not in SHARED_PRESET_KEYS and str(v) != ""}


def global_mapped(cfg: Mapping, conn: Lookup) -> dict[str, str]:
    """The format-2 keys a legacy `config.md` migrates to (steps 5-7): every
    one of `OWNED_GLOBAL_KEYS`, "" where it is unset. `global_plan`'s
    `mapped` below format 2, and what `migrate.global_fields` persists."""
    view = global_mapping(cfg, conn)
    fields = dict.fromkeys(OWNED_GLOBAL_KEYS, "")
    fields.update((k, str(view[k])) for k in OWNED_GLOBAL_KEYS if k in view)
    if not legacy_embeds(cfg, conn):
        # A legacy choice that never embedded stays off (ruling 5).
        for part in keys.EMBEDDING_PARTS:
            fields[keys.role_key("embedding", part)] = ""
    return enriched(fields, conn)


def campaign_mapped(meta: Mapping, conn: Lookup) -> dict[str, str]:
    """The format-2 keys a legacy `campaign.md` migrates to (steps 6-7).
    `campaign_plan`'s `mapped` for an unmarked campaign, and what
    `migrate.campaign_fields` persists."""
    return enriched(_persistable(campaign_mapping(meta, conn)), conn)


# ---- the Embedding role ----
#: The sources whose `no` is a guess rather than knowledge (`resolve._GUESSES`).
_GUESSES = frozenset({"name"})


def _rev(raw: Mapping) -> str:
    rev = raw.get("rev", "")
    return rev if isinstance(rev, str) else ""


def _embed_endpoint(raw: Mapping, current: bool) -> str:
    """`resolve.embed_endpoint`'s rule: an `openai_compatible` record's own
    URL; OpenRouter's only in the current layout and with a key; else ""."""
    if raw["kind"] == "openai_compatible":
        return raw["base_url"] or ""
    if raw["kind"] == "openrouter" and current and raw["api_key"]:
        return providers.PRESETS["openrouter"].base_url
    return ""


def _cannot_embed(provider: str, model: str, raw: dict, model_facts: dict) -> bool:
    """Whether `model` on record `raw` is KNOWN not to embed: its catalog row,
    its facts or its adapter say `no` (a name-rule guess never counts)."""
    row = llm_connections.cached_row(str(raw.get("id", "") or provider), model)
    caps = capabilities.resolve_caps(providers.infer(raw), model, catalog_row=row,
                                     facts=model_facts)
    return all(_known_no(caps.get(cap)) for cap in capabilities.NEEDS["embed"])


def _known_no(found: capabilities.Cap | None) -> bool:
    return (found is not None and found.value == capabilities.NO
            and found.source not in _GUESSES)


def _soft_facts(provider: str, model: str, rev: str) -> dict:
    try:
        return facts.of(provider, model, rev)
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def legacy_embeds(cfg: Mapping, conn: Lookup) -> bool:
    """Whether `cfg`'s Embedding choice embeds: what `embed_space.resolve`
    answers (`resolve.embedding`'s rule -- a provider that exists, an
    endpoint, a model, and no known `no` for `embed`), read through `conn`
    rather than the resolver, which reads the legacy layout through this
    module.

    A record that reads but is malformed is off, as `embed_space.resolve` has
    always said. Under a strict lookup a provider file that cannot be read
    raises, so the migration fails and retries rather than reading it as
    "never embedded" and clearing the choice for good -- and a known `no` is
    judged again with the facts read strictly (`facts.of(strict=True)`): a
    facts file a sync client holds reads as nothing stated, and a user's
    `embed: yes` over a catalog's `no` would vanish into that `no`."""
    provider, model = embedding_role(cfg)
    try:
        raw = conn(provider) if provider else None
        if raw is None:
            return False
        if not model or not _embed_endpoint(raw, keys.is_current(cfg)):
            return False
        rev = _rev(raw)
        if not _cannot_embed(provider, model, raw, _soft_facts(provider, model, rev)):
            return True
        if not _strict(conn):
            return False
        known = facts.of(provider, model, rev, strict=True)
        return not _cannot_embed(provider, model, raw, known)
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, UnicodeDecodeError):   # unreadable, not malformed
            raise
        return False


# ---- the model facts ----
def stated(conn: Mapping) -> dict[str, object]:
    """A connection's legacy model fields that differ from the defaults, as
    `{field: value}` -- what `facts.adopt_legacy` states of its model."""
    out: dict[str, object] = {}
    vision = str(conn.get("vision") or "")
    if vision:
        out["vision"] = vision
    if conn.get("prefill") is True:
        out["prefill"] = True
    post_process = str(conn.get("post_process") or "")
    if post_process not in ("", "none"):
        out["post_process"] = post_process
    return out


def _facts_overlay(conn: Lookup) -> dict[str, dict[str, dict]]:
    """`stated` of every connection the store lists, by provider and then by
    the model it runs (`facts.model_of`) -- one that states nothing too, as
    `{}`, because the migration's step 3 visits every connection and takes
    back what an earlier, interrupted run copied whatever it states now
    (`facts.adopt_legacy`; read so by `facts.adopted`). In memory only: step 3
    makes the writes."""
    out: dict[str, dict[str, dict]] = {}
    for listed in llm_connections.list_connections():
        raw = conn(listed["id"])
        if raw is None:
            continue
        out.setdefault(listed["id"], {})[facts.model_of(raw)] = stated(raw)
    return out


# ---- the derived presets ----
def derived_name(base_name: str, effort: str) -> str:
    """`"<base> · reasoning <effort>"`, or `"Reasoning <effort>"` with no base."""
    return f"{base_name} · reasoning {effort}" if base_name else f"Reasoning {effort}"


def _canonical(params: Mapping) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def derive(base: dict | None, effort: str, existing: PresetRead) -> Derived:
    """The preset a slot whose own preset is `base` (None: no readable one)
    is repointed at to carry `effort`: `base`'s params plus that effort.

    Its id is the name's slug. When a preset by that id already holds a
    different name or different params, the slug takes a suffix: the first
    eight hex digits of a digest over the derived NAME and params. So
    identical derivations (one name, one set of params) collapse to one file
    whatever base they came from, two devices pick the same id, and two
    different derivations whose names slug alike ("Warm", "Warm!") never
    share one."""
    name = derived_name(str(base.get("name") or "") if base else "", effort)
    own = base.get("params") if base else None
    merged = {**(own if isinstance(own, dict) else {}), "reasoning_effort": effort}
    params = {k: merged[k] for k in llm_sampling.CONTROLS if k in merged}
    pid = paths.slugify(name)
    taken = existing(pid)
    if taken is not None and (taken.get("name") != name or taken.get("params") != params):
        digest = hashlib.sha256(_canonical({"name": name, "params": params})
                                .encode("utf-8")).hexdigest()
        pid = f"{pid}-{digest[:8]}"
    return Derived(pid, name, params)


def _legacy_effort(selection: cascade.Selection, conn: Lookup,
                   levels: tuple[str, ...]) -> str:
    """The legacy GLM reasoning effort the legacy wire sent for `selection`,
    when it is one of `levels`; "" otherwise. Sent only by an
    `openai_compatible` provider, to a GLM model (`llm_reasoning.is_glm`)."""
    raw = conn(selection.provider)
    if raw is None or raw.get("kind") != "openai_compatible":
        return ""
    effort = str(raw.get("reasoning_effort") or "")
    if effort not in levels or not llm_reasoning.is_glm({"model": selection.model}):
        return ""
    return effort


def _slots(view: Mapping, *, campaign: bool) -> Iterator[tuple[str, cascade.Selection]]:
    """`(preset key, selection)` for every selection slot `view` sets: each
    generative role and its fallback, then each route pinned to a model (at a
    campaign, only the routes a campaign may override)."""
    def slot(prefix: Callable[[str], str]) -> cascade.Selection | None:
        provider = str(view.get(prefix("provider"), "") or "")
        if not provider:
            return None
        return cascade.Selection(provider, str(view.get(prefix("model"), "") or ""),
                                 str(view.get(prefix("preset"), "") or ""))

    for role in keys.GENERATIVE_ROLES:
        for prefix in (functools.partial(keys.role_key, role),
                       functools.partial(keys.fallback_key, role)):
            got = slot(prefix)
            if got is not None:
                yield prefix("preset"), got
    for route in routing.ROUTES:
        if campaign and not route.campaign_scoped:
            continue
        if str(view.get(keys.use_key(route.key), "") or "") != keys.PIN:
            continue
        prefix = functools.partial(keys.pin_key, route.key)
        got = slot(prefix)
        if got is not None:
            yield prefix("preset"), got


def _derivation(view: Mapping, *, campaign: bool, conn: Lookup,
                presets: PresetRead) -> tuple[dict[str, str], tuple[Derived, ...]]:
    """`(repoint, presets)` for every slot of `view` the legacy effort rode on
    (ruling 4): its provider `openai_compatible`, its legacy effort one of
    `REPRESENTABLE`, its model GLM, and its own preset, read, setting no
    reasoning effort."""
    repoint: dict[str, str] = {}
    derived: dict[str, Derived] = {}

    def existing(pid: str) -> dict | None:
        # A preset this plan derived already answers first, so two different
        # bases that derive one name never share an id.
        got = derived.get(pid)
        return {"name": got.name, "params": got.params} if got is not None else presets(pid)

    for preset_key, selection in _slots(view, campaign=campaign):
        made = _derived_for(selection, conn, presets, existing)
        if made is None:
            continue
        derived.setdefault(made.id, made)
        repoint[preset_key] = made.id
    return repoint, tuple(derived.values())


def _derived_for(selection: cascade.Selection, conn: Lookup, presets: PresetRead,
                 existing: PresetRead) -> Derived | None:
    """The derived preset one selection slot is repointed at (ruling 4), or
    None: its provider `openai_compatible` with a legacy effort one of
    `REPRESENTABLE`, its model GLM, and its own preset, read, setting no
    reasoning effort. `existing` is what `derive` checks an id against."""
    effort = _legacy_effort(selection, conn, REPRESENTABLE)
    if not effort:
        return None
    own = selection.preset.strip()
    base = presets(own) if own else None
    params = base.get("params") if base is not None else None
    if isinstance(params, dict) and "reasoning_effort" in params:
        return None
    return derive(base, effort, existing)


class _RoutePreset(NamedTuple):
    """The route-level preset a route runs under: its name ("" for
    `PRESET_CLEAR`, no preset at all) and whether it sets a reasoning effort."""

    name: str
    sets_effort: bool


def _route_preset(view: Mapping, route: routing.Route,
                  presets: PresetRead) -> _RoutePreset | None:
    """`view`'s opinion on `route`'s preset, by `cascade.preset_for`'s rule:
    None for none (absent, blank, or an id that names no preset, which the
    walk passes)."""
    chosen = str(view.get(keys.preset_key(route.key), "") or "").strip()
    if chosen == sampler_presets.PRESET_CLEAR:
        return _RoutePreset("", False)
    preset = presets(chosen) if chosen else None
    if preset is None:
        return None
    params = preset.get("params")
    return _RoutePreset(str(preset.get("name") or chosen),
                        isinstance(params, dict) and "reasoning_effort" in params)


def _under_route_preset(route: routing.Route, view: Mapping, glob: Mapping, *,
                        campaign: bool, from_global: bool,
                        conn: Lookup) -> list[cascade.Selection]:
    """The selection and fallback `route` runs on at this scope, both under
    its route preset: the cascade over the scope (a campaign's over it and
    `glob`). When a campaign runs under the GLOBAL route preset, a provider
    the global plan NOTES on that route is left out -- the global note (one
    per route and provider, and a provider's legacy effort is the provider's)
    already says it. Only a noted one: whether a selection is noted turns on
    its model too, so a provider the global scope runs on a model that is not
    GLM is noted for the campaign that runs it on one that is."""
    def exists(provider: str) -> bool:
        return conn(provider) is not None

    if campaign:
        choice = cascade.choose(route, campaign=dict(view), glob=dict(glob), exists=exists)
    else:
        choice = cascade.choose(route, campaign={}, glob=dict(view), exists=exists)
    noted_globally: set[str] = set()
    if from_global:
        alone = cascade.choose(route, campaign={}, glob=dict(glob), exists=exists)
        noted_globally = {s.provider for s in (alone.selection, alone.fallback)
                          if s is not None
                          and _legacy_effort(s, conn, llm_reasoning.GLM_EFFORTS)}
    return [s for s in (choice.selection, choice.fallback)
            if s is not None and s.provider not in noted_globally]


def _route_notes(view: Mapping, *, glob: Mapping, scope: str, campaign: bool,
                 conn: Lookup, presets: PresetRead) -> tuple[retired.Note, ...]:
    """Ruling 5: a route whose route-level preset (`PRESET_CLEAR` included)
    sets no reasoning effort, and whose selection -- or whose fallback, which
    the route's preset follows -- is a GLM provider with a legacy effort. Not
    derived (ratification item 3), so noted, once per (scope, route,
    provider).

    At a campaign, the route is judged as the campaign runs it: its preset
    by the campaign-then-global walk, its selection by the cascade over the
    campaign and `glob` (the global settings as format 2 sees them). So a
    campaign preset over a selection the campaign inherits is noted, and so
    is a campaign's own selection under a global preset. A global preset
    over a selection the global scope chooses as well is the global plan's
    note, and is not noted again per campaign."""
    out: dict[str, retired.Note] = {}
    for route in routing.ROUTES:
        if campaign and not route.campaign_scoped:
            continue
        found = _route_preset(view, route, presets)
        from_global = False
        if found is None and campaign:
            found, from_global = _route_preset(glob, route, presets), True
        if found is None or found.sets_effort:
            continue
        for selection in _under_route_preset(route, view, glob, campaign=campaign,
                                             from_global=from_global, conn=conn):
            effort = _legacy_effort(selection, conn, llm_reasoning.GLM_EFFORTS)
            if not effort:
                continue
            raw = conn(selection.provider) or {}
            note = _route_note(scope, route, selection.provider,
                               str(raw.get("name") or selection.provider), effort,
                               found.name)
            out.setdefault(note.id, note)
    return tuple(out.values())


def _route_note(scope: str, route: routing.Route, provider: str, provider_name: str,
                effort: str, preset_name: str) -> retired.Note:
    where = "" if scope == GLOBAL_SCOPE else " in this campaign"
    what = (f"the preset “{preset_name}” sets no reasoning effort" if preset_name
            else "no preset is set")
    text = (f"On the {route.label} route{where}, {what}, so the GLM provider "
            f"“{provider_name}” no longer sends its reasoning effort ({effort}) there "
            "— this was not carried over.")
    kind = "route_preset"
    return retired.Note(retired.note_id(scope, route.key, provider, effort, kind),
                        scope, route.key, provider, effort, kind, text)


def _slot_label(preset_key: str) -> str:
    """How a note names the selection slot whose preset key is `preset_key`."""
    for role in keys.GENERATIVE_ROLES:
        if preset_key == keys.role_key(role, "preset"):
            return f"the {role.capitalize()} role"
        if preset_key == keys.fallback_key(role, "preset"):
            return f"the {role.capitalize()} role's fallback"
    for route in routing.ROUTES:
        if preset_key == keys.pin_key(route.key, "preset"):
            return f"the {route.label} route"
    return preset_key


def _stranded(view: Mapping, *, scope: str, campaign: bool, conn: Lookup,
              presets: PresetRead) -> tuple[retired.Note, ...]:
    """A scope carrying the retirement marker but no format marker (N1, review
    M-2): mapped as the migration maps it, and derived nothing, because it
    says it is retired -- so a GLM slot whose legacy effort rode on the
    legacy wire loses it. Each such slot is noted (guarantee 7), once per
    (scope, slot, provider): kind `unrepresentable`, a legacy effort this
    scope's presets cannot carry."""
    out: dict[str, retired.Note] = {}
    for preset_key, selection in _slots(view, campaign=campaign):
        effort = _legacy_effort(selection, conn, llm_reasoning.GLM_EFFORTS)
        if not effort:
            continue
        own = selection.preset.strip()
        base = presets(own) if own else None
        params = base.get("params") if base is not None else None
        if isinstance(params, dict) and "reasoning_effort" in params:
            continue
        raw = conn(selection.provider) or {}
        name = str(raw.get("name") or selection.provider)
        where = "" if scope == GLOBAL_SCOPE else " in this campaign"
        kind = "unrepresentable"
        text = (f"On {_slot_label(preset_key)}{where}, the GLM provider “{name}” no longer "
                f"sends its reasoning effort ({effort}), because no preset there sets one "
                "— this was not carried over.")
        note = retired.Note(retired.note_id(scope, preset_key, selection.provider, effort, kind),
                            scope, preset_key, selection.provider, effort, kind, text)
        out.setdefault(note.id, note)
    return tuple(out.values())


def _planned(view: Mapping, *, glob: Mapping, mapped: dict[str, str], model_facts: dict,
             scope: str, campaign: bool, conn: Lookup, presets: PresetRead) -> Plan:
    repoint, made = _derivation(view, campaign=campaign, conn=conn, presets=presets)
    notes = _route_notes(view, glob=glob, scope=scope, campaign=campaign, conn=conn,
                         presets=presets)
    return Plan(mapped, repoint, model_facts, made, notes)


# ---- the plans ----
def global_plan(cfg: Mapping[str, str], lookup: Lookup, presets: PresetRead) -> Plan:
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
    if _settled(cfg):
        return empty()
    if keys.is_current(cfg):
        return _planned(cfg, glob={}, mapped={}, model_facts={}, scope=GLOBAL_SCOPE,
                        campaign=False, conn=lookup, presets=presets)
    mapped = global_mapped(cfg, lookup)
    if _retired(cfg):
        return Plan(mapped, {}, _facts_overlay(lookup), (),
                    _stranded({**cfg, **mapped}, scope=GLOBAL_SCOPE, campaign=False,
                              conn=lookup, presets=presets))
    return _planned({**cfg, **mapped}, glob={}, mapped=mapped,
                    model_facts=_facts_overlay(lookup), scope=GLOBAL_SCOPE,
                    campaign=False, conn=lookup, presets=presets)


def campaign_plan(meta: Mapping[str, str], *, glob: Mapping[str, str], global_current: bool,
                  lookup: Lookup, presets: PresetRead, cid: str) -> Plan:
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
    scope = campaign_scope(cid)
    if _settled(meta):
        return empty()
    if keys.is_current(meta):
        return _planned(meta, glob=glob, mapped={}, model_facts={}, scope=scope,
                        campaign=True, conn=lookup, presets=presets)
    mapped = campaign_mapped(meta, lookup)
    if _retired(meta):
        return Plan(mapped, {}, {}, (), _stranded({**meta, **mapped}, scope=scope,
                                                  campaign=True, conn=lookup,
                                                  presets=presets))
    return _planned({**meta, **mapped}, glob=glob, mapped=mapped, model_facts={},
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
    lookup: Lookup | None = None
    #: The memoised preset reader the plan used, answering with the derived
    #: presets (`presets`) first; None when nothing was planned.
    read_preset: PresetRead | None = None

    def selection(self, conn_id: str, model: str = "") -> cascade.Selection:
        """Connection `conn_id` as the planner plans a slot that names it --
        what a format-1 reroll naming the provider runs on (spec 5.6): its own
        model (or `model`, when the reroll names one) and its own preset, then
        the planner's derivation of that slot (`_derived_for`), so a GLM
        connection's legacy reasoning effort rides a derived preset exactly as
        it does on a stored slot, and the reroll sends what the legacy wire
        sent. A derived preset no stored slot planned is added to `presets`
        here, so the resolution that asked can read it. Read through this
        overlay's lookup, so the record fallback for a stripped connection
        applies as it does to the mapping. Empty model and preset for an
        unknown id or one that sets none."""
        conn = self.lookup or _no_connection
        sel = _selection(conn_id, conn)
        chosen = cascade.Selection(sel["provider"], model or sel["model"], sel["preset"])
        if self.read_preset is None or not isinstance(self.presets, dict):
            return chosen
        made = _derived_for(chosen, conn, self.read_preset, self.read_preset)
        if made is None:
            return chosen
        got = _virtual(made)
        held = self.presets.setdefault(made.id, got)
        if (held["name"], held["params"]) != (got["name"], got["params"]):
            raise OverlayError(f"derived preset {made.id!r} planned with two bodies")
        return chosen._replace(preset=made.id)

    def legacy_route(self, route: routing.Route | None) -> str:
        """The legacy route `route`'s settings were stored under at format 1
        (its own key, or the parent a split route came out of); "" for none:
        what a format-1 refusal names a pin by."""
        return routing.legacy_key(route) if route is not None else ""


def _no_connection(_conn_id: str) -> dict | None:
    return None


class OverlayError(ValueError):
    """The overlay planned one derived preset id with two bodies. `derive`
    reads the global derived presets before the store's, so this cannot
    happen; raised rather than asserted, so a regression fails loudly under
    `-O` too, and as a ValueError rather than an AssertionError."""


def _virtual(made: Derived) -> dict:
    """A derived preset in `sampler_presets.read_preset`'s shape."""
    return {"id": made.id, "name": made.name, "params": dict(made.params),
            "notes": "", "source": ""}


def _as_current(meta: Mapping[str, str], plan: Plan) -> dict[str, str]:
    """`planned(meta, plan)`, marked current when the plan mapped it: the
    scope as the migration will write it, which a reader asking
    `inference_keys.is_current` of it sees as such."""
    out = planned(meta, plan)
    if plan.mapped and not keys.is_current(meta):
        out[keys.FORMAT_KEY] = keys.CURRENT_FORMAT
    return out


def _persisted(meta: Mapping[str, str], plan: Plan) -> dict[str, str]:
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
    if _settled(cfg) and (not meta or _settled(meta)):
        return Overlay(dict(cfg), dict(meta), {}, {}, (), dict(cfg), dict(meta),
                       embedding_role(cfg))
    conn = lookup(mode="soft")
    virtual: dict[str, dict] = {}
    seen: dict[str, dict | None] = {}

    def presets(pid: str) -> dict | None:
        if pid in virtual:
            return virtual[pid]
        if pid not in seen:
            seen[pid] = sampler_presets.read_preset(pid)
        return seen[pid]

    def keep(plan: Plan) -> None:
        for made in plan.presets:
            got = _virtual(made)
            held = virtual.setdefault(made.id, got)
            # `derive` reads `presets`, which answers with `virtual` first, so
            # an id it hands back is free or holds this very preset.
            if (held["name"], held["params"]) != (got["name"], got["params"]):
                raise OverlayError(f"derived preset {made.id!r} planned with two bodies")

    gplan = global_plan(cfg, conn, presets)
    keep(gplan)
    glob = _as_current(cfg, gplan)
    stored = _persisted(cfg, gplan)
    legacy = not _settled(cfg) and not keys.is_current(cfg)
    if not meta:
        return Overlay(glob, {}, virtual, gplan.facts, gplan.notes, stored, {},
                       embedding_role(cfg), legacy, conn, presets)
    cplan = campaign_plan(meta, glob=glob,
                          global_current=keys.is_current(cfg) or keys.is_newer(cfg),
                          lookup=conn, presets=presets, cid=cid)
    keep(cplan)
    return Overlay(glob, _as_current(meta, cplan), virtual, gplan.facts,
                   gplan.notes + cplan.notes, stored, _persisted(meta, cplan),
                   embedding_role(cfg), legacy, conn, presets)
