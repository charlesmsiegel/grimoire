"""Roles and routes, read for the Models screen and written from it (spec 10).

`view` is what a role card and a route row render, at the global scope or one
campaign's. Each carries what the scope STORES (read as the resolver reads it,
`resolve.current_view`, so a store the migration or retirement has not reached
shows the format-2 layout it will be written as -- which is what plays), what
it `resolves` to and what it `inherits`. The view also carries the planner's
`retirement_notes` (`resolve.retirement_notes`): what could not be carried
over. Nothing renders them yet.

- `resolves` is one `resolve.resolve` per row -- the route's first task, or
  the role itself (`role=`) -- so it is what `require_inference` serves, and
  `problem` is `resolve.refusal` of that same resolution: the seam's own
  decision, never a copy of it (spec 12). `fallback_missing` is that same
  resolution's: what its fallback is known unable to do, so it is never sent
  (spec 5.3) -- the seam refuses nothing over it, so this is the only place
  it shows. `fallback_problem` is the same for a fallback that cannot send at
  all (`resolve.problem`: no key, no base URL), left out of the chain just as
  silently. The Embedding card's `problem` is its own (`embed_space.problem`,
  then a model its provider is known not to embed with): why it embeds
  nothing, None when it embeds.
- `inherits` is the same resolution with this scope's own choice for the row
  silenced (`resolve.Silence`): what "inherit (resolves to ...)" offers. A row
  naming X is therefore never offered "inherit (X)" because of its own X.

`write` is the one door for a new-layout write. It validates the whole body
before anything is written (`RefusedError`, with the HTTP status it means), and a
named selection is replaced WHOLE, so a part it leaves out is cleared;
selections and fields the body does not name are untouched. The caller refuses
a store that is not at the current format first (`routes.common.
refuse_unmigrated`); this re-checks, because a key written into a legacy store
is one the resolver ignores.

A campaign write takes `campaign_lock(cid)` once. Inside that hold an
UNMARKED campaign -- skipped by the migration as busy, forked from one that
was, or created by an older build on a synced device -- is migrated first
(`migrate.campaign`), so the marker the write stamps never lands over legacy
overrides nobody translated (spec 11.1). The write itself is
`campaigns.set_campaign_inference`, which takes the same (reentrant) lock,
stamps the marker and bumps the campaign's write token.

`summary` is the app header's: each role's global selection, named, from the
cascade alone (no resolution), beside whether embedding is on.

The Embedding role is the global scope's alone, takes no preset and no
fallback, and a change of it to a non-empty value needs `confirm_embedding`:
re-embedding a library may cost money, and the server is what enforces that
(rule 1, ruling 6).
"""

from __future__ import annotations

import functools
import unicodedata
from collections.abc import Callable, Mapping

from .. import (
    alternates,
    config,
    embed_space,
    llm_connections,
    locks,
    routing,
    sampler_presets,
)
from .. import inference_keys as keys
from ..campaigns import lifecycle as campaign_lifecycle
from ..campaigns import read as campaign_read
from ..frontmatter import breaks_line
from .. import inference_retired as retired
from . import capabilities, cascade, facts, in_use, migrate, providers, resolve
from .resolved import ResolvedInference

SCOPES: tuple[str, ...] = ("global", "campaign")

#: What a write body may name, at its top level and per role and route.
BODY_FIELDS: tuple[str, ...] = ("roles", "routes", "presets")
ROLE_FIELDS: tuple[str, ...] = ("selection", "fallback")
ROUTE_FIELDS: tuple[str, ...] = ("use", "pin", "preset")

#: What `use_<route>` may say: inherit (""), a generative role, or the route's
#: own pin. Never `embedding` -- a route generates.
USES: tuple[str, ...] = ("", *keys.GENERATIVE_ROLES, keys.PIN)

EMBEDDING_CONFIRM = ("Changing the embedding model re-embeds your library through "
                     "{provider}, which may cost money — confirm to change it.")
#: The same question, for an edit of the provider the Embedding role embeds
#: through that moves its vector space (`embed_space.moved_by`), asked by
#: `PUT /llm-connections/{id}`.
EMBEDDING_MOVE_CONFIRM = ("Changing this provider's address or key re-embeds your "
                          "library through {provider}, which may cost money — "
                          "confirm to change it.")
#: The same question, for a model-facts write that turns the Embedding role on
#: (`embed_space.facts_moved`), asked by `PUT /llm-connections/{id}/facts`.
EMBEDDING_FACTS_CONFIRM = ("This turns embedding on through {provider}: your library "
                           "will be embedded, which may cost money — confirm to save it.")

NEWER_CAMPAIGN = {
    "kind": "newer_format",
    "detail": "A newer version of grimoire has changed this campaign's model settings. "
              "Update grimoire to change them here."}


class RefusedError(Exception):
    """A write this module will not make: the HTTP status it means and the
    body (a sentence, or a `{detail, kind}` dict) to answer with."""

    def __init__(self, status: int, detail: str | dict):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _bad(detail: str) -> RefusedError:
    return RefusedError(400, detail)


# ---- reading ----
def _stored(view: dict, key: Callable[[str], str],
            parts: tuple[str, ...] = keys.PARTS) -> dict[str, str]:
    return {part: in_use.text(view, key(part)) for part in parts}


def _sel(resolved: ResolvedInference) -> dict | None:
    """The `Sel` of a resolution's primary attempt; None when nothing resolved."""
    if not resolved.attempts:
        return None
    first = resolved.attempts[0]
    sampling = first.conn.get("sampling") or {}
    return {"provider": first.provider_id,
            "provider_name": str(first.conn.get("name") or first.provider_id),
            "model": first.model,
            "preset": first.preset_id,
            "preset_name": str(sampling.get("preset_name") or ""),
            "via": resolved.via, "scope": resolved.scope}


def _problem(resolved: ResolvedInference) -> str | None:
    """The seam's refusal of `resolved`, as the sentence it would answer with;
    None when the seam would serve it."""
    refused = resolve.refusal(resolved)
    if refused is None:
        return None
    body = refused[1]
    return str(body["detail"]) if isinstance(body, dict) else body


def _decides_natively(resolved: ResolvedInference) -> str:
    """What the resolved primary's `decide_native` is -- `yes`, `no` or
    `unknown`, read off the capabilities the resolver itself decided on -- so
    the Models page can word a structured decision without a second read that
    could disagree with the mode (I9). `no` only when it is KNOWN
    (`resolve.decides_natively`: a name-rule guess is no knowledge), so "No
    native decision API" is never said of a model nobody has checked."""
    if not resolved.attempts:
        return capabilities.UNKNOWN
    first = resolved.attempts[0]
    cap = first.capabilities.get("decide_native")
    if cap is not None and cap.value == capabilities.YES:
        return capabilities.YES
    return capabilities.UNKNOWN if resolve.decides_natively(first) else capabilities.NO


def _role_card(role: str, own: dict, scope: str, cid: str) -> dict:
    silence = resolve.Silence(scope, frozenset(keys.role_key(role, p) for p in keys.PARTS))
    # The Decision card reads its role as the decide routes it serves do
    # (spec 12, one decision): a model that cannot generate is answered
    # natively there, and a same-provider fallback behind it is a stage of
    # its own rather than a retry -- so the card says what those routes do.
    operation = "decide" if role == "decision" else "generate"
    resolved = resolve.resolve("", cid, role=role, operation=operation)
    return {"stored": _stored(own, functools.partial(keys.role_key, role)),
            "fallback": _stored(own, functools.partial(keys.fallback_key, role)),
            "resolves": _sel(resolved),
            "inherits": _sel(resolve.resolve("", cid, role=role, operation=operation,
                                             silence=silence)),
            "problem": _problem(resolved),
            # What the fallback is KNOWN unable to do, so it is never sent
            # (spec 5.3) -- the seam does not refuse over it, so without this
            # nothing on screen would say the fallback is off.
            "fallback_missing": list(resolved.fallback_missing),
            # Why the fallback cannot send at all (no key, no base URL), so it
            # is left out: as silent at the seam as a dropped one.
            "fallback_problem": resolved.fallback_problem,
            # The backend the Decision role's model is answered by -- "native",
            # "structured", or "" when it can do neither (`decision_mode`);
            # "" on every other role.
            "decision_mode": resolved.decision_mode or "",
            "decides_natively": _decides_natively(resolved)}


def _route_row(route: routing.Route, own: dict, scope: str, cid: str,
               uses: str) -> dict:
    task = route.tasks[0]
    resolved = resolve.resolve(task, cid, operation=route.operation)
    inherited = resolve.resolve(task, cid, operation=route.operation,
                                silence=resolve.Silence(scope, frozenset(keys.route_keys(route))))
    return {"key": route.key, "label": route.label, "hint": route.hint,
            "tasks": list(route.tasks), "operation": route.operation,
            "default_role": route.default_role, "requires": list(route.requires),
            "campaign_scoped": route.campaign_scoped,
            "use": in_use.text(own, keys.use_key(route.key)),
            "pin": _stored(own, functools.partial(keys.pin_key, route.key)),
            "preset": in_use.text(own, keys.preset_key(route.key)),
            "resolves": _sel(resolved), "inherits": _sel(inherited),
            "problem": _problem(resolved),
            "fallback_missing": list(resolved.fallback_missing),
            "fallback_problem": resolved.fallback_problem,
            # On a decide route, the backend its model is answered by
            # (`decision_mode`): "native", "structured", or "" when it can do
            # neither; "" on every other route.
            "decision_mode": resolved.decision_mode or "",
            "decides_natively": _decides_natively(resolved),
            # The role that supplied the selection; None for a pin, or nothing.
            "role": resolved.role or None,
            # The role the route walks to get there (`cascade.walked_role`):
            # what the Decision card lists, inheriting or not. None for a pin.
            "uses": uses or None}


def _embedding_card(cfg: dict, lookup: llm_connections.Lookup) -> dict:
    provider, model = resolve.embedding_role(cfg)
    # One resolution per card: whether it is on, and why not, are read from
    # the same answer (spec 12, one decision).
    got = embed_space.resolution(cfg)
    on = embed_space.endpoint_of(got) is not None
    # What embeds, or nothing: a provider with no model, or one that cannot
    # embed, resolves to no embedding at all, and the card must not say both.
    raw = lookup(provider) if provider and on else None
    resolves = None if raw is None else {
        "provider": provider, "provider_name": str(raw.get("name") or provider),
        "model": model, "preset": "", "preset_name": "", "via": "role", "scope": "global"}
    # Why it is off, in the card's own words: the Embedding role has no seam
    # refusal to borrow (nothing is refused; recall degrades), so this is the
    # embedding resolver's account of itself rather than `_problem`'s.
    return {"stored": {"provider": provider, "model": model}, "resolves": resolves,
            "on": on, "problem": None if on else _embedding_problem(cfg, got)}


def _embedding_problem(cfg: dict, got: ResolvedInference | None) -> str | None:
    """Why the Embedding role is off, given its resolution (`got`; None where
    reading it raised). `embed_space.problem`'s reasons first -- a role that
    cannot send at all says that before anything else -- then a model its
    provider is known not to embed with (slice D, ruling 4): the same
    `missing` that switched the role off, so the card and the resolution are
    one decision (spec 12). Never raises."""
    why = embed_space.problem(cfg, embeds=False)
    if why != embed_space.OFF or got is None or not got.missing or not got.attempts:
        return why
    attempt = got.attempts[0]
    name = str(attempt.conn.get("name") or attempt.provider_id)
    return (f"{attempt.model} on {name} cannot {capabilities.CANNOT['embed']}, "
            "so embedding is off — choose another Embedding model.")


def _providers() -> list[dict]:
    """Every provider, with whether it can send at all -- `resolve.problem`,
    the seam's credential rule, asked of a masked record (`key_set` stands in
    for the key it deliberately does not carry) -- and `own_model`, the model
    its record names (`facts.model_of`; "" when it names none). A reroll
    naming the provider alone runs it at the STANDING model whatever the
    store's format (`resolve._overridden`, spec 5.6), so nothing resolves
    from this: it is what the record says."""
    return [{"id": c["id"], "name": str(c.get("name") or c["id"]),
             "kind": str(c.get("kind") or ""),
             "preset": str(c.get("preset") or "") or providers.infer(c).id,
             "usable": resolve.problem({**c, "api_key": "x" if c.get("key_set") else ""})
             is None,
             "own_model": facts.model_of(c)}
            for c in llm_connections.list_connections()]


def view(scope: str, cid: str = "") -> dict:
    """What the Models screen (`global`) or a campaign's Inspector
    (`campaign`) renders. Raises `CampaignNotFound` for a campaign that does
    not exist. Never carries a key."""
    if scope not in SCOPES:
        raise ValueError(f"no such scope: {scope!r}")
    cfg = config.read_config()
    lookup = resolve.connection_lookup()
    # What each row STORES is the layout as the migration persists it
    # (`stored`): every preset id in it names a preset file, so a save that
    # sends a row back unchanged names what a write accepts. The derived
    # reasoning preset a GLM slot plays on (in memory, until retirement
    # writes it) is what the row `resolves` to.
    if scope == "campaign":
        seen = resolve.current_view(cfg, in_use.campaign_meta(cid, strict=False), cid=cid)
        glob, own = seen.stored, seen.stored_meta
    else:
        cid = ""
        seen = resolve.current_view(cfg)
        glob = own = seen.stored

    def uses(route: routing.Route) -> str:
        return cascade.walked_role(route, campaign=own if scope == "campaign" else {},
                                   glob=glob, exists=lambda c: lookup(c) is not None)
    roles: dict[str, dict] = {role: _role_card(role, own, scope, cid)
                              for role in keys.GENERATIVE_ROLES}
    if scope == "global":
        roles["embedding"] = _embedding_card(cfg, lookup)
    return {
        "format": in_use.text(cfg, keys.FORMAT_KEY).strip() or "1",
        "newer": keys.is_newer(cfg),
        "migration": migrate.status().as_dict(),
        "roles": roles,
        "routes": [_route_row(r, own, scope, cid, uses(r)) for r in routing.ROUTES
                   if scope == "global" or r.campaign_scoped],
        "providers": _providers(),
        "presets": [{"id": p["id"], "name": p["name"]}
                    for p in sampler_presets.list_presets()],
        "preset_clear": sampler_presets.PRESET_CLEAR,
        # What could not be carried over (ruling 5, N9), shown on /models
        # until dismissed: see `retirement_notes`.
        "retirement_notes": retirement_notes(scope, cid),
    }


#: Why a write that reaches the retirement record is refused when the record
#: cannot be read (409 `retirement_unreadable`, R3-3).
RETIREMENT_UNREADABLE = ("The record of retired model settings could not be read; "
                         "try again once it has synced.")


def _campaign_names() -> dict[str, str]:
    try:
        return {cid: name or cid for cid, name, _world in campaign_read.world_refs()}
    except (OSError, UnicodeDecodeError, ValueError):
        return {}


def retirement_notes(scope: str, cid: str = "") -> list[dict]:
    """What could not be carried over, as the view shows it (N11): the
    retirement record's notes not yet dismissed, then the planner's
    (`resolve.retirement_notes`) that the record does not hold yet -- one
    list, ids collapsing, so a note reads the same before and after
    retirement records it, and one dismissed before retirement stays
    dismissed. On the global view (`/models`) every scope's -- the planner's
    included, planned for every campaign (`_planner_notes`), so a campaign's
    loss shows there from this build's first start (spec 11.4); on a
    campaign's, the global ones and that campaign's. Each row carries `scope_name`: ""
    for the global scope, else the campaign's name, so a note on `/models`
    says where it applies. Read fail-soft: never raises."""
    record = retired.read()
    known = {row["id"] for row in record["notes"]}
    names = _campaign_names()

    def shown(row_scope: str) -> bool:
        return scope == "global" or row_scope in (retired.GLOBAL_SCOPE,
                                                  retired.campaign_scope(cid))

    def row(fields: Mapping[str, str]) -> dict:
        target = retired.scope_campaign(fields["scope"])
        return {**{k: fields[k] for k in retired.Note._fields},
                "scope_name": names.get(target, target) if target else ""}

    planned = (_planner_notes(names) if scope == "global"
               else resolve.retirement_notes(cid))
    out = [row(r) for r in record["notes"] if not r["dismissed"] and shown(r["scope"])]
    out += [row(n._asdict()) for n in planned if n.id not in known and shown(n.scope)]
    return out


def _planner_notes(names: Mapping[str, str]) -> list[retired.Note]:
    """What the planner could not carry over, in every scope: the global
    plan's and each campaign's (`resolve.retirement_notes`, which gives a
    campaign's own beside the global ones), ids collapsing. What `/models`
    shows before retirement has recorded them (spec 11.4), and what a
    dismissal looks a planner-only note up in."""
    out: dict[str, retired.Note] = {}
    for cid in ["", *names]:
        for note in resolve.retirement_notes(cid):
            out.setdefault(note.id, note)
    return list(out.values())


def dismiss_note(note_id: str) -> bool:
    """Dismiss note `note_id` for good, on every device: returns whether any
    note by that id was known. One the record does not hold yet -- the
    planner's, before retirement recorded it -- is recorded already dismissed
    (N14), so neither the planner nor a later retirement brings it back. A
    settings write that spends nothing and touches no campaign: in
    `config.format_hold` (a newer store raises `config.NewerFormatError`),
    which is also the cross-process hold the record's writers share.
    `retired.RecordUnreadableError` when the record cannot be read."""
    with config.format_hold():
        if retired.dismiss(note_id):
            return True
    # Planned outside the hold (it reads connections and presets); recorded
    # inside it, where `dismiss` reads the record again.
    planned = next((note for note in _planner_notes(_campaign_names())
                    if note.id == note_id), None)
    if planned is None:
        return False
    with config.format_hold():
        return retired.dismiss(planned)


def _named(selection: cascade.Selection, lookup: llm_connections.Lookup,
           virtual: Mapping[str, dict]) -> dict:
    """A role's selection as the header names it: the provider's name, the
    model and the preset's name ("" when it names none, or one that is gone;
    a derived preset not yet written is `virtual`'s)."""
    raw = lookup(selection.provider) or {}
    preset = (virtual.get(selection.preset) or sampler_presets.read_preset(selection.preset)
              if selection.preset else None)
    return {"provider_name": str(raw.get("name") or selection.provider),
            # The model it actually runs: a Claude provider with none set
            # runs its default, which is what `active_connection` names too.
            "model": facts.model_of({"kind": raw.get("kind"), "model": selection.model}),
            "preset_name": preset["name"] if preset else ""}


def summary() -> dict:
    """What `GET /config` says of the roles beside the header's chat target:
    each generative role's global selection, named (None when nothing selects
    one), and whether embedding is on.

    One `resolve.current_view` and `cascade.role_selection` per role -- the
    cascade, not the resolver: no `resolve.resolve`, so this refuses nothing
    and is no resolver call site (the header's own target is the one display
    resolve the config route makes). A legacy store is read as the planner
    reads it, so it names what plays there too."""
    llm_connections.ensure_migrated()
    cfg = config.read_config()
    lookup = resolve.connection_lookup()
    seen = resolve.current_view(cfg)
    glob = seen.cfg

    def exists(provider_id: str) -> bool:
        return lookup(provider_id) is not None

    roles: dict[str, dict | None] = {}
    for role in keys.GENERATIVE_ROLES:
        selection = cascade.role_selection(role, campaign={}, glob=glob, exists=exists)[0]
        roles[role] = None if selection is None else _named(selection, lookup, seen.presets)
    return {"roles": roles, "embedding_on": embed_space.resolve(cfg) is not None}


# ---- what names a provider ----
def used_by(provider_id: str) -> list[dict]:
    """Where the stored settings name `provider_id`: `[{kind: "role" |
    "fallback" | "route", key, scope: "global" | "campaign", cid?}]` -- a
    role, a role's fallback, a route's chosen pin, or the Embedding role.

    `in_use.selections`, filtered to this provider: read as `view` reads them,
    through the planner, so a store the migration has not reached reports
    what plays. Global first, then each campaign by id; a campaign
    that cannot be read names nothing. Walks every campaign (each one's parse
    memoized on its file), so it belongs on a provider's detail, never on the
    list, which would ask it once per provider."""
    out: list[dict] = []
    for use in in_use.selections():
        if use.provider != provider_id:
            continue
        row = {"kind": use.kind, "key": use.key, "scope": use.scope}
        if use.scope == "campaign":
            row["cid"] = use.cid
        out.append(row)
    return out


# ---- validating a write ----
def _object(value: object, where: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _bad(f"{where} must be an object")
    return value


def _only(entry: dict, allowed: tuple[str, ...], where: str) -> None:
    unknown = sorted(str(k) for k in entry if k not in allowed)
    if unknown:
        raise _bad(f"{where}: unknown field(s) {', '.join(unknown)}")


def _string(value: object, where: str) -> str:
    """An id or a model as text, stripped; "" for None. Every value this
    module reads is one line of `config.md` or `campaign.md` frontmatter, so a
    control character (a pasted newline, a NUL) or any other line boundary the
    parser splits on (`frontmatter.breaks_line`: U+2028 and U+2029 are not
    controls) is refused rather than written -- the rest of the line would be
    read back as a key of its own."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _bad(f"{where} must be text")
    if breaks_line(value) or any(unicodedata.category(ch) == "Cc" for ch in value):
        raise _bad(f"{where} must be one line of text, without control characters")
    return value.strip()


def _model(value: object, where: str) -> str:
    """A model id: `_string`, bounded as the reroll override bounds one
    (`alternates.MAX_MODEL_CHARS`) -- the same id reaches every turn's usage
    row and prompt-log index, read on every listing."""
    model = _string(value, where)
    if len(model) > alternates.MAX_MODEL_CHARS:
        raise _bad(f"{where} is too long to be a model id — check it and try again")
    return model


def _provider(value: object, where: str) -> str:
    """A provider id that exists, or ""."""
    pid = _string(value, where)
    if pid and resolve.connection_lookup()(pid) is None:
        raise _bad(f"no such provider: {pid}")
    return pid


def _preset(value: object, where: str) -> str:
    """A sampler preset id that exists, `PRESET_CLEAR`, or ""."""
    pid = _string(value, where)
    if pid and pid != sampler_presets.PRESET_CLEAR and sampler_presets.read_preset(pid) is None:
        raise _bad(f"no such preset: {pid}")
    return pid


def _selection(value: object, where: str, *, embedding: bool = False) -> dict[str, str]:
    """A whole selection: every part, "" for one the body left out. A
    generative one names a model whenever it names a provider."""
    entry = _object(value, where)
    _only(entry, keys.PARTS, where)
    if embedding and _string(entry.get("preset"), f"{where}.preset"):
        raise _bad("The Embedding role takes no preset: an embedding call samples nothing.")
    sel = {"provider": _provider(entry.get("provider"), f"{where}.provider"),
           "model": _model(entry.get("model"), f"{where}.model"),
           "preset": _preset(entry.get("preset"), f"{where}.preset")}
    if not embedding and sel["provider"] and not sel["model"]:
        # A provider has no model of its own (spec 5.6): written alone, the
        # selection would send a blank model id. The Embedding role differs --
        # a provider alone there is embedding off, which re-embeds nothing.
        raise _bad(f"{where}: choose a model for {sel['provider']} -- a provider "
                   "alone sends no model")
    return {p: sel[p] for p in (keys.EMBEDDING_PARTS if embedding else keys.PARTS)}


def _use(value: object, where: str) -> str:
    use = _string(value, where)
    if use == "embedding":
        raise _bad("A route cannot use the Embedding role: a route generates, and "
                   "Embedding only embeds.")
    if use not in USES:
        raise _bad(f"{where} must be one of {', '.join(repr(u) for u in USES)}")
    return use


def _route(key: object, scope: str) -> routing.Route:
    route = next((r for r in routing.ROUTES if r.key == key), None)
    if route is None:
        raise _bad(f"no such route: {key}")
    if scope == "campaign" and not route.campaign_scoped:
        raise _bad(f"The {route.label} route is set for the whole library, not per campaign.")
    return route


class _Plan:
    """A validated body: the keys to write, the Embedding role it names
    (`(provider, model)`, None when it names none) and that provider's name,
    read here so nothing is looked up under the `config_lock` hold."""

    def __init__(self) -> None:
        self.fields: dict[str, str] = {}
        self.embedding: tuple[str, str] | None = None
        self.embedding_name = ""


def _roles(plan: _Plan, roles: dict, scope: str) -> None:
    for role, value in roles.items():
        if role not in keys.ROLES:
            raise _bad(f"no such role: {role}")
        where = f"roles.{role}"
        entry = _object(value, where)
        _only(entry, ROLE_FIELDS, where)
        if role == "embedding":
            if scope == "campaign":
                raise _bad("The Embedding role is set for the whole library, "
                           "not per campaign.")
            if "fallback" in entry:
                raise _bad("The Embedding role has no fallback.")
            if "selection" in entry:
                sel = _selection(entry["selection"], f"{where}.selection", embedding=True)
                plan.fields.update((keys.role_key(role, p), sel[p]) for p in keys.EMBEDDING_PARTS)
                plan.embedding = (sel["provider"], sel["model"])
                raw = resolve.connection_lookup()(sel["provider"]) if sel["provider"] else None
                plan.embedding_name = (str(raw.get("name") or sel["provider"])
                                       if raw is not None else "the embedding provider")
            continue
        for name, key in (("selection", keys.role_key), ("fallback", keys.fallback_key)):
            if name in entry:
                sel = _selection(entry[name], f"{where}.{name}")
                plan.fields.update((key(role, p), sel[p]) for p in keys.PARTS)


def _routes(plan: _Plan, routes: dict, presets: dict, scope: str) -> None:
    for key, value in routes.items():
        route = _route(key, scope)
        where = f"routes.{route.key}"
        entry = _object(value, where)
        _only(entry, ROUTE_FIELDS, where)
        if "use" in entry:
            plan.fields[keys.use_key(route.key)] = _use(entry["use"], f"{where}.use")
        if "pin" in entry:
            sel = _selection(entry["pin"], f"{where}.pin")
            plan.fields.update((keys.pin_key(route.key, p), sel[p]) for p in keys.PARTS)
        if "preset" in entry:
            plan.fields[keys.preset_key(route.key)] = _preset(entry["preset"],
                                                              f"{where}.preset")
    for key, value in presets.items():
        route = _route(key, scope)
        field = keys.preset_key(route.key)
        preset = _preset(value, f"presets.{route.key}")
        if plan.fields.get(field, preset) != preset:
            raise _bad(f"routes.{route.key}.preset and presets.{route.key} disagree")
        plan.fields[field] = preset


def _validated(scope: str, body: object) -> _Plan:
    """The whole body checked, before anything is written."""
    if not isinstance(body, dict):
        raise _bad("the body must be an object")
    legacy = sorted(str(k) for k in body if k in keys.LEGACY_GLOBAL_KEYS)
    if legacy:
        raise _bad("These settings moved to roles and routes and are no longer "
                   f"written: {', '.join(legacy)}")
    _only(body, BODY_FIELDS, "the body")
    plan = _Plan()
    _roles(plan, _object(body.get("roles"), "roles"), scope)
    _routes(plan, _object(body.get("routes"), "routes"),
            _object(body.get("presets"), "presets"), scope)
    return plan


# ---- writing ----
def _require_current() -> None:
    """The caller refuses a legacy store with its 409 first
    (`routes.common.refuse_unmigrated`); reaching here without that is a bug,
    and a key written now would be ignored by the resolver. A newer store is
    not one: the write's own hold refuses it (`config.format_hold`), where a
    switch cannot land after the check."""
    cfg = config.read_config()
    if not keys.is_current(cfg) and not keys.is_newer(cfg):
        raise RuntimeError("inference settings are written only at the current format")


def _confirmed(plan: _Plan, confirm: bool) -> None:
    """Refuse an unconfirmed change of the Embedding role to a selection that
    embeds (a provider AND a model): only that re-embeds the library. Clearing
    either part switches embeddings off and re-embeds nothing. Read under the
    caller's `config_lock` hold, so the value compared against is the one the
    write replaces."""
    new = plan.embedding
    if new is None or confirm or not all(new):
        return
    cfg = config.read_config()
    before = (in_use.text(cfg, keys.role_key("embedding", "provider")).strip(),
              in_use.text(cfg, keys.role_key("embedding", "model")).strip())
    if new != before:
        raise RefusedError(400, {
            "kind": "confirm_embedding",
            "detail": EMBEDDING_CONFIRM.format(provider=plan.embedding_name)})


def _named_refs(fields: dict[str, str]) -> tuple[list[str], list[str]]:
    """`(provider ids, preset ids)` the global keys in `fields` name."""
    provider_keys = [keys.role_key(role, "provider") for role in keys.ROLES]
    provider_keys += [keys.fallback_key(role, "provider") for role in keys.GENERATIVE_ROLES]
    preset_keys = [keys.role_key(role, "preset") for role in keys.GENERATIVE_ROLES]
    preset_keys += [keys.fallback_key(role, "preset") for role in keys.GENERATIVE_ROLES]
    for route in routing.ROUTES:
        provider_keys.append(keys.pin_key(route.key, "provider"))
        preset_keys += [keys.pin_key(route.key, "preset"), keys.preset_key(route.key)]
    providers_named = [fields[k] for k in provider_keys if fields.get(k)]
    presets_named = [fields[k] for k in preset_keys
                     if fields.get(k) and fields[k] != sampler_presets.PRESET_CLEAR]
    return providers_named, presets_named


def _still_there(plan: _Plan) -> None:
    """Every provider and preset the write names, checked again in the hold
    that writes: one deleted in another tab after `_validated` checked it
    would otherwise be written as a reference to nothing -- and answered 200,
    the save that leaves every turn refused. A delete sweeps the global keys
    under these same locks, so once this passes, no delete lands between it
    and the write."""
    providers_named, presets_named = _named_refs(plan.fields)
    for pid in providers_named:
        if resolve.connection_lookup()(pid) is None:
            raise _bad(f"no such provider: {pid}")
    for preset in presets_named:
        if sampler_presets.read_preset(preset) is None:
            raise _bad(f"no such preset: {preset}")


def _write_global(plan: _Plan, confirm_embedding: bool) -> None:
    if not plan.fields:
        return
    # `llm_connections.LOCK`, then `config_lock` -- the order a provider
    # delete's sweep takes them in -- so the existence check and the write are
    # one step against a delete (`_still_there`).
    # `config.format_hold` refuses a store a newer build switched meanwhile,
    # inside the same hold (`config.NewerFormatError`, 409 `newer_format`).
    with llm_connections.LOCK, config.format_hold():
        _still_there(plan)
        _confirmed(plan, confirm_embedding)
        config.write_config(**plan.fields)


def _write_campaign(cid: str, plan: _Plan) -> None:
    # The campaign lock, then `config.format_hold` held through the write --
    # with `llm_connections.LOCK` between them, the order the migration's
    # switch takes the last two in, because migrating the campaign below reads
    # connections. A global switch cannot land between the format check and
    # this campaign's write, whichever process makes it.
    with locks.campaign_lock(cid), llm_connections.LOCK, config.format_hold():
        # Fresh, not through the memo: what this decides (refuse a newer
        # campaign, migrate an unmarked one) must be the file as it is now.
        meta = in_use.campaign_meta(cid, memo=False)
        if not plan.fields:
            return
        if keys.is_newer(meta):
            raise RefusedError(409, NEWER_CAMPAIGN)
        # What the body names, checked again in this hold, as the global write
        # does: a delete never sweeps campaign keys, so one landing after
        # `_validated` would leave this campaign naming nothing.
        _still_there(plan)
        if not keys.is_current(meta):
            # In this hold, before the write: the marker the write stamps would
            # switch off every legacy override nobody translated.
            migrate.campaign(cid)
        campaign_lifecycle.set_campaign_inference(cid, plan.fields)


def write(scope: str, cid: str, body: dict, *, confirm_embedding: bool = False) -> None:
    """Write `body` -- `{roles?: {role: {selection?, fallback?}}, routes?:
    {route: {use?, pin?, preset?}}, presets?: {route: id}}` -- at `scope`.

    Raises `RefusedError` (400 for a body this will not write, 409 for a campaign
    a newer build marked) before writing anything, and `CampaignNotFound` for
    a campaign that does not exist."""
    if scope not in SCOPES:
        raise ValueError(f"no such scope: {scope!r}")
    plan = _validated(scope, body)
    _require_current()
    if scope == "global":
        _write_global(plan, confirm_embedding)
    else:
        _write_campaign(cid, plan)
