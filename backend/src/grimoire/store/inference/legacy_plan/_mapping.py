"""Soft/strict memoized connection reading and exact legacy-to-current settings/model-facts mapping, including embedding eligibility."""

from __future__ import annotations

import functools
from collections.abc import Callable, Collection, Iterator, Mapping
from typing import Literal

from ... import config, llm_connections, locks, routing
from ... import inference_keys as keys
from ... import inference_retired as retired
from .. import capabilities, facts, providers
from . import _contracts

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
                raise retired.EntryMissingError(conn_id, str(raw.get("name") or ""))
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


def lookup(*, mode: Literal["soft", "migrate", "retire"]) -> _contracts.Lookup:
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


def _strict(conn: _contracts.Lookup) -> bool:
    return getattr(conn, "strict", False) is True


# ---- the mapping ----
def _selection(conn_id: str, conn: _contracts.Lookup) -> dict[str, str]:
    """`{provider, model, preset}` for a connection id (model and preset are
    empty when the connection is unknown or sets none)."""
    raw = conn(conn_id) or {}
    return {
        "provider": conn_id,
        "model": str(raw.get("model") or ""),
        "preset": str(raw.get("sampler_preset") or "").strip(),
    }


def _pins_and_presets(meta: Mapping, conn: _contracts.Lookup, scoped_only: bool,
                      only: Collection[str] | None) -> dict:
    out: dict = {}
    for route in routing.ROUTES:
        if scoped_only and not route.campaign_scoped:
            continue
        if only is not None and route.key not in only:
            continue
        legacy = routing.legacy_key(route)
        if not legacy:
            continue   # born at format 2 (`routing.NO_LEGACY`): nothing stored for it
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


def global_mapping(cfg: Mapping, conn: _contracts.Lookup, *,
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


def campaign_mapping(meta: Mapping, conn: _contracts.Lookup, *,
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


def enriched(fields: dict[str, str], conn: _contracts.Lookup) -> dict[str, str]:
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
            if k not in _contracts.SHARED_PRESET_KEYS and str(v) != ""}


def global_mapped(cfg: Mapping, conn: _contracts.Lookup) -> dict[str, str]:
    """The format-2 keys a legacy `config.md` migrates to (steps 5-7): every
    one of `OWNED_GLOBAL_KEYS`, "" where it is unset. `global_plan`'s
    `mapped` below format 2, and what `migrate.global_fields` persists."""
    view = global_mapping(cfg, conn)
    fields = dict.fromkeys(_contracts.OWNED_GLOBAL_KEYS, "")
    fields.update((k, str(view[k])) for k in _contracts.OWNED_GLOBAL_KEYS if k in view)
    if not legacy_embeds(cfg, conn):
        # A legacy choice that never embedded stays off (ruling 5).
        for part in keys.EMBEDDING_PARTS:
            fields[keys.role_key("embedding", part)] = ""
    return enriched(fields, conn)


def campaign_mapped(meta: Mapping, conn: _contracts.Lookup) -> dict[str, str]:
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


def legacy_embeds(cfg: Mapping, conn: _contracts.Lookup) -> bool:
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


def _facts_overlay(conn: _contracts.Lookup) -> dict[str, dict[str, dict]]:
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
