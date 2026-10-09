"""The resolver: a task (and a campaign's settings) in, the attempts it runs out.

`store.inference.resolve` assembles the pure pieces (`cascade`, and the
planner's in-memory overlay of a legacy layout) around the store reads, and must answer exactly what the route layer answered
before the refactor. The functions it replaced are gone from `routes.common`
now, so these tests hold the resolver itself -- not the seam built on it -- to
the frozen baseline (`fixtures/inference_baseline.json`), over every store state
that baseline builds (`inference_baseline.STATES`), for every task, with and
without the campaign. `test_inference_equivalence.py` holds the seam to the same
file.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import grimoire.store as store
from grimoire import llm, llm_sampling, routes, wire
from grimoire.store import inference_keys as keys
from grimoire.store import routing
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
from grimoire.store.inference import capabilities, migrate
from grimoire.store.inference import facts as inference_facts
from grimoire.store.inference import resolve as inf
from grimoire.store.inference.capabilities import Cap
from grimoire.store.inference.cascade import Selection
from grimoire.store.inference.resolved import Attempt, ResolvedInference

from . import inference_baseline as baseline
from . import inference_fixtures
from .test_inference_equivalence import (
    NEW_TASKS,
    PROVIDER_ONLY_OVERRIDES,
    migrated_expectation,
)

TASKS = [*sorted(routing.TASK_ROUTE), ""]


def _recorded(state: str, task: str, scope: str) -> dict:
    """The baseline's cell for `task`, or, for a task claimed since it was
    recorded (`test_inference_equivalence.NEW_TASKS`), its route sibling's --
    which that task must resolve exactly as."""
    return BASELINE[state]["tasks"][NEW_TASKS.get(task, task)][scope]

#: The frozen answers (never regenerated -- see `inference_baseline`).
BASELINE = json.loads(baseline.FIXTURE.read_text(encoding="utf-8"))


def _normalised(value):
    """`value` as the baseline spells it: connection revs replaced."""
    return baseline._normalise(value, baseline._revs())


def _state(name: str, tmp_path):
    """A client on a store built as `name`, and that state's context."""
    cm = baseline.client_at(tmp_path)
    client = cm.__enter__()
    try:
        ctx = baseline.STATES[name](client)
    except BaseException:
        cm.__exit__(None, None, None)
        raise
    return cm, client, ctx


@pytest.fixture
def at_state(tmp_path):
    """`at_state(name)` builds the store and returns its context; torn down after."""
    opened = []

    def build(name: str) -> dict:
        cm, _client, ctx = _state(name, tmp_path / name)
        opened.append(cm)
        return ctx

    yield build
    for cm in reversed(opened):
        cm.__exit__(None, None, None)


def _primary(resolved: ResolvedInference) -> wire.Target | None:
    """The primary attempt's target, None when nothing resolved."""
    return resolved.attempts[0].target if resolved.attempts else None


def _second(resolved: ResolvedInference) -> wire.Target | None:
    """The fallback attempt's target -- riding the chain or not -- or None."""
    return resolved.attempts[1].target if len(resolved.attempts) > 1 else None


def _refusal(resolved: ResolvedInference) -> dict | None:
    """The 409 detail `routes.common.require_inference` raises for this
    resolution, or None.

    Spelled out here rather than read off the seam, so the comparison below
    proves the resolver carries everything that refusal needs (the primary's
    target, `via`, `route`, `role`) independently of the code that builds it.
    Every store resolves as format 2 since slice I, so this is that wording
    (spec 12)."""
    primary = _primary(resolved)
    if primary is None:
        return {"detail": "No LLM connection selected", "kind": "missing_key"}
    problem = inf.target_problem(primary)
    if problem is None:
        return None
    name = primary.provider_name or primary.provider_id
    if resolved.via == "route":
        label = routing.label_for(resolved.route).lower()
        return {"detail": f"{problem} ({name}, routed for {label})", "kind": "missing_key"}
    if resolved.role:
        return {"detail": f"{problem} ({name}, the {resolved.role.capitalize()} role)",
                "kind": "missing_key"}
    return {"detail": f"{problem} ({name})", "kind": "missing_key"}


# ---- the equivalence sweep: every state, task and scope against the baseline ----
@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_every_task_resolves_as_the_baseline_recorded(state, at_state):
    ctx = at_state(state)
    cid = ctx["cid"]
    for task in TASKS:
        for scope, scope_cid in (("global", ""), ("campaign", cid)):
            where = (state, task, scope)
            recorded = _recorded(state, task, scope)
            resolved = inf.resolve(task, scope_cid)
            assert resolved.task == task and resolved.operation == "generate", where

            # The refusal, where the baseline refused, and only there.
            if "status" in recorded:
                assert recorded["status"] == 409, where
                assert _refusal(resolved) == recorded["detail"], where
            else:
                assert _refusal(resolved) is None, where
                # The primary: the same connection, model, sampling and
                # catalog parameters the seam handed the facade.
                got = _normalised(baseline._resolved(_primary(resolved)))
                assert got == {k: v for k, v in recorded.items() if k != "fallback"}, where
                # The fallback, exactly as the facade sent it (the next test
                # says why the two now agree without any adjustment here).
                assert _fallback(resolved) == recorded["fallback"], where

            if not resolved.attempts:
                assert resolved.chain is None, where
                continue
            first = resolved.attempts[0]
            assert first.provider_id == first.target.provider_id, where
            assert first.preset_id == first.target.sampling.preset_id, where


def _fallback(resolved: ResolvedInference) -> dict | None:
    """The resolved fallback as the baseline spells a facade fallback:
    `{id, sampling}`, revs normalised, or None."""
    got = _second(resolved)
    if got is None:
        return None
    return _normalised({"id": got.provider_id, "sampling": asdict(got.sampling)})


def _facade_fallback(resolved: ResolvedInference) -> wire.Target | None:
    """What the facade would send as the fallback for a generation on
    `resolved`'s chain: `LLMClient._routes`, on a client built as the app
    builds one -- which holds no fallback, so this is the one the chain
    carries. Building one opens nothing."""
    attempts = routes.common.build_llm()._routes(resolved.chain)
    return attempts[1].target if len(attempts) > 1 else None


def _resolver_fallback(resolved: ResolvedInference) -> wire.Target | None:
    """The resolver's fallback attempt, as its target, or None."""
    return _second(resolved)


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_the_resolved_fallback_is_what_the_facade_sent(state, at_state):
    """The fallback attempt is the one the facade sends -- not the fallback
    connection as it stands alone (spec 5.2, 5.4, 5.5).

    The baseline recorded each fallback off `LLMClient._routes`, which then did
    two things to the global fallback connection: it dropped a fallback whose id
    was the primary's own (`llm._same_route`), and it carried a preset the
    primary took from a ROUTE scope -- campaign or global, a `PRESET_CLEAR`
    included -- onto the fallback (`llm.fallback_sampling`). The resolver does
    both, so every recorded fallback is matched as recorded: identity and
    sampling. And the facade now sends the resolver's own (`Chain.fallback`).
    A refused resolution has none recorded (the facade was never reached), and
    the resolver then has nothing to fall back from or a primary the seam
    refuses before any fallback could matter."""
    ctx = at_state(state)
    for task in TASKS:
        for scope, scope_cid in (("global", ""), ("campaign", ctx["cid"])):
            where = (state, task, scope)
            recorded = _recorded(state, task, scope)
            resolved = inf.resolve(task, scope_cid)
            if not resolved.attempts:
                assert resolved.chain is None and "status" in recorded, where
                continue
            if "status" in recorded:
                continue
            assert _fallback(resolved) == recorded["fallback"], where
            # And the live facade agrees, on the chain the resolver built.
            assert _facade_fallback(resolved) == _resolver_fallback(resolved), where
            got = _second(resolved)
            if got is not None:
                fb = resolved.attempts[1]
                assert (fb.provider_id, fb.model, fb.preset_id) == (
                    got.provider_id, got.model, got.sampling.preset_id), where


def test_a_route_preset_follows_the_route_onto_the_fallback(at_state):
    """The two shapes the recorded fallbacks hold that the fallback connection
    alone does not."""
    ctx = at_state("routed")
    # Campaign `preset_scene: warm` on a campaign pin to `local`: the fallback
    # (`spare`, whose own preset is none) is sent with the route's preset.
    chat = inf.resolve("chat", ctx["cid"])
    assert _primary(chat).provider_id == "local" and _primary(chat).sampling.scope == "campaign"
    assert _second(chat).provider_id == "spare"
    assert _second(chat).sampling == _primary(chat).sampling
    assert chat.attempts[1].preset_id == "warm"
    # The model's catalog parameters stay the fallback's own.
    assert _second(chat).model == "vendor/spare"
    # Global `preset_summary` is PRESET_CLEAR: "no preset" follows the route too.
    summary = inf.resolve("rolling-summary")
    assert _primary(summary).sampling.scope == "global"
    assert _primary(summary).sampling.preset_id == ""
    assert _second(summary).sampling == _primary(summary).sampling
    # A connection-level answer stays with its connection.
    dossier = inf.resolve("dossier")
    assert _primary(dossier).sampling.scope == "connection"
    assert _second(dossier).sampling.scope in ("connection", "none")
    assert _second(dossier).sampling != _primary(dossier).sampling


def test_a_fallback_naming_the_primary_is_no_fallback(at_state):
    """Campaign `route_tracker: spare` while `spare` is also the fallback: a
    second attempt on the connection that just failed is not a fallback."""
    ctx = at_state("routed")
    tracker = inf.resolve("tracker-update", ctx["cid"])
    assert _primary(tracker).provider_id == "spare"
    assert _second(tracker) is None and len(tracker.attempts) == 1
    assert _second(inf.resolve("tracker-update")).provider_id == "spare"


def test_the_route_scopes_are_the_facades():
    assert inf.ROUTE_SCOPES == llm.ROUTE_SCOPES


#: Override bodies the resolver answers. `long_model` is a 400 on the body's
#: own length, which the seam checks before resolving anything.
OVERRIDES = {name: body for name, body in baseline.OVERRIDE_BODIES.items()
             if name != "long_model"}


def _selection(body: dict) -> Selection:
    return Selection(body.get("connection_id", ""), body.get("model", ""), "")


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_every_override_resolves_as_the_baseline_recorded(state, at_state):
    """The resolver's half of an override, against what the seam answered
    (recorded at the campaign scope). A 400 or a 409 is the seam's to raise;
    the resolver's part is to have nothing, or something that cannot send.

    Every store resolves as format 2 since slice I, so the cells are the
    migration's (`migrated_expectation`), and a connection named alone means
    what it means at format 2 -- `test_inference_override.py` holds those."""
    ctx = at_state(state)
    expected = migrated_expectation(BASELINE[state])["overrides"]
    for name, body in OVERRIDES.items():
        if name in PROVIDER_ONLY_OVERRIDES:
            continue
        where = (state, name)
        recorded = expected[name]
        resolved = inf.resolve("regenerate", ctx["cid"],
                               override=_selection(body))
        if recorded.get("status") == 400:
            # A named connection that does not exist.
            assert not resolved.attempts, where
        elif recorded.get("status") == 409:
            assert not resolved.attempts or inf.target_problem(_primary(resolved)), where
        else:
            got = _normalised(baseline._resolved(_primary(resolved)))
            assert got == {k: v for k, v in recorded.items() if k != "routed"}, where
            # An override picks the primary; the fallback stays standing
            # policy, sent as the facade sends it for that primary.
            assert _facade_fallback(resolved) == _resolver_fallback(resolved), where


# ---- the brief's named cases ----
def test_a_fresh_store_resolves_every_task_to_the_active_connection(at_state):
    ctx = at_state("fresh")
    for task in TASKS:
        for scope_cid in ("", ctx["cid"]):
            resolved = inf.resolve(task, scope_cid)
            assert _primary(resolved).provider_id == "openrouter"
            assert _primary(resolved).model == "vendor/active"
            assert resolved.via == "role"
            assert resolved.role == "primary" and resolved.scope == "global"
            assert _second(resolved) is None and len(resolved.attempts) == 1


def test_a_route_pin_targets_that_connection(at_state):
    at_state("routed")
    resolved = inf.resolve("dossier")
    raw = store.llm_connections.read_connection_raw("local")
    target = _primary(resolved)
    assert (target.provider_id, target.kind, target.base_url, target.api_key, target.rev) == (
        "local", raw["kind"], "http://localhost:1234/v1", raw["api_key"], raw["rev"])
    assert target.provider_name == raw["name"] and target.model == raw["model"]
    # The fallback the call carries (`spare`) is the facade's, not the record's.
    assert resolved.chain.fallback.provider_id == "spare"
    # The account is the ledger's (slice E): the provider's billing and the
    # operation, with no role, because a pin is no role's slot.
    assert target.account == wire.Account(operation="generate", billing="metered")
    assert target.sampling == wire.Sampling(preset_id="warm", preset_name="warm",
                                            scope="connection", params={"temperature": 0.9})
    assert target.model_params is None          # not an OpenRouter connection
    assert resolved.route == "dossier"
    assert (resolved.via, resolved.scope, resolved.role) == ("route", "global", "")
    first = resolved.attempts[0]
    assert (first.provider_id, first.model, first.preset_id, first.target) == (
        "local", "local-model", "warm", target)


def test_a_split_route_reports_its_own_route(at_state):
    at_state("fresh")
    assert inf.resolve("scene-break").route == "scene_break"
    assert inf.resolve("").route == ""


def test_a_keyless_fallback_is_no_fallback(at_state):
    at_state("keyless")
    for task in ("chat", "absorb", ""):
        assert _second(inf.resolve(task)) is None
        assert inf.resolve(task).chain.fallback is None
    # The ROUTED keyless connection is still the primary: the seam reports it.
    absorb = inf.resolve("absorb")
    assert _primary(absorb).provider_id == "nokey"
    assert inf.target_problem(_primary(absorb)) is not None
    assert absorb.via == "route"


def test_no_active_connection_still_resolves_a_pinned_route(at_state):
    at_state("no_active")
    pinned = inf.resolve("dossier")
    assert _primary(pinned).provider_id == "local"
    # Review Focus 1: the pin keeps the global fallback.
    assert _second(pinned) is not None and _second(pinned).provider_id == "spare"
    unpinned = inf.resolve("chat")
    assert unpinned.chain is None and unpinned.attempts == ()


def test_a_legacy_flat_config_is_migrated_before_it_is_read(at_state, monkeypatch):
    at_state("pre_connections")
    order: list[str] = []
    real_migrate = store.llm_connections.ensure_migrated
    real_read = store.config.read_config

    def migrate():
        order.append("migrate")
        return real_migrate()

    def read():
        order.append("read")
        return real_read()

    monkeypatch.setattr(store.llm_connections, "ensure_migrated", migrate)
    monkeypatch.setattr(store.config, "read_config", read)
    resolved = inf.resolve("chat")
    assert order[0] == "migrate" and "read" in order
    assert _primary(resolved).provider_id == "openrouter"
    assert _primary(resolved).model == "vendor/legacy"
    assert _primary(resolved).api_key == "sk-test-legacy"


def test_an_unreadable_connection_file_reads_as_missing(at_state, monkeypatch):
    at_state("embed_with_dangling")
    # `spare` is invalid UTF-8 on disk and pinned for dossier: walked past.
    resolved = inf.resolve("dossier")
    assert _primary(resolved).provider_id == "claude" and resolved.via == "role"

    # Every way a read can fail reads as "no such connection", never raises.
    store.write_config(route_dossier="local")
    assert _primary(inf.resolve("dossier")).provider_id == "local"
    real = store.llm_connections.read_connection_raw
    for exc in (store.locks.StoreBusy("busy"), OSError("gone"),
                UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"),
                store.llm_connections.ConnectionNotFound("local")):
        def fail(conn_id, exc=exc):
            if conn_id == "local":
                raise exc
            return real(conn_id)
        monkeypatch.setattr(store.llm_connections, "read_connection_raw", fail)
        resolved = inf.resolve("dossier")
        assert _primary(resolved).provider_id == "claude", exc


def _uncounted_planner(monkeypatch) -> None:
    """The planner's own reads (`legacy_plan.lookup("soft")`) through the real
    reader, so a test counting `read_connection_raw` counts the resolver's."""
    from grimoire.store.inference import legacy_plan

    real = store.llm_connections.read_connection_raw

    def soft(conn_id: str):
        try:
            return real(conn_id)
        except store.llm_connections.ConnectionNotFound:
            return None

    monkeypatch.setattr(legacy_plan, "_soft_read", soft)


def test_a_connection_is_read_once_per_resolve(at_state, monkeypatch):
    """By the resolver, once (`connection_lookup`). The planner reads a legacy
    layout through a lookup of its own (`_overlay`), once more at most."""
    at_state("routed")
    calls: list[str] = []
    real = store.llm_connections.read_connection_raw

    def counting(conn_id):
        calls.append(conn_id)
        return real(conn_id)

    _uncounted_planner(monkeypatch)
    monkeypatch.setattr(store.llm_connections, "read_connection_raw", counting)
    inf.resolve("dossier")
    assert calls and len(calls) == len(set(calls))


def test_a_provider_only_override_keeps_the_standing_model(at_state):
    """Spec 5.6, on a legacy store too since slice I: a provider named alone
    runs at the STANDING selection's model and preset (the active
    connection's, mapped into the Primary role in memory), never at a model
    of its own."""
    at_state("routed")
    resolved = inf.resolve("regenerate",
                           override=Selection("local", "", ""))
    assert _primary(resolved).provider_id == "local"
    assert _primary(resolved).model == "vendor/active"
    # The route's preset follows the route (global `preset_scene: cold`); a
    # route with none falls to the standing selection's own (`warm`).
    assert _primary(resolved).sampling.preset_id == "cold"
    assert _primary(resolved).sampling.scope == "global"
    tagline = inf.resolve("tagline", override=Selection("local", "", ""))
    assert _primary(tagline).model == "vendor/active"
    assert _primary(tagline).sampling.preset_id == "warm"
    assert _primary(tagline).sampling.scope == "connection"


def test_a_model_only_override_keeps_the_standing_provider(at_state):
    ctx = at_state("routed")
    standing = inf.resolve("regenerate", ctx["cid"])
    assert _primary(standing).provider_id == "local"
    resolved = inf.resolve("regenerate", ctx["cid"],
                           override=Selection("", "bigger-local", ""))
    assert _primary(resolved).provider_id == "local"
    assert _primary(resolved).model == "bigger-local"
    # Global: the active OpenRouter connection, driven at a model whose catalog
    # entry says what it takes.
    glob = inf.resolve("regenerate",
                       override=Selection("", "vendor/bigger", ""))
    assert _primary(glob).provider_id == "openrouter"
    assert _primary(glob).model == "vendor/bigger"
    assert _primary(glob).model_params == ("temperature",)


def test_a_provider_override_with_no_standing_selection_names_no_model(at_state):
    """With nothing standing, a provider named alone has no model to keep
    (spec 5.6): it resolves with none, which the seam asks the caller for."""
    at_state("no_active")
    assert inf.resolve("regenerate").chain is None
    named = inf.resolve("regenerate", override=Selection("spare", "", ""))
    assert _primary(named).provider_id == "spare" and named.attempts[0].model == ""
    # A model alone overrides the standing selection, and there is none.
    model_only = inf.resolve("regenerate",
                             override=Selection("", "vendor/bigger", ""))
    assert model_only.chain is None


def test_an_override_naming_no_connection_resolves_nothing(at_state):
    at_state("fresh")
    resolved = inf.resolve("regenerate", override=Selection("nope", "", ""))
    assert resolved.chain is None and resolved.attempts == ()


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_own_target_reports_what_the_baseline_recorded(state, at_state):
    """A connection's own sampling, as the connection editor's sidebar reports
    it, is what the baseline's `display.connection_sampling` recorded."""
    at_state(state)
    recorded = BASELINE[state]["display"]["connection_sampling"]
    ids = {c["id"] for c in store.llm_connections.list_connections()}
    assert ids
    for conn_id in sorted(ids):
        raw = store.llm_connections.read_connection_raw(conn_id)
        got = _normalised(llm_sampling.report(inf.own_target(raw)))
        assert got == recorded[conn_id], conn_id


def test_own_target_drops_a_stale_model_params(at_state):
    at_state("routed")
    raw = store.llm_connections.read_connection_raw("local")
    got = inf.own_target({**raw, "model_params": ["stale"]})
    assert got.model_params is None


def test_facts_for_reads_the_model_at_format_2_and_the_record_below_it(at_state):
    """`facts_for` is what a target outside any route sends its model's
    behaviour from: below format 2 the record's own legacy fields (what it
    sent before the migration), at format 2 its model's facts -- never the
    record's frozen fields there."""
    at_state("fresh")
    assert not keys.is_current(store.read_config())
    store.llm_connections.update_connection("openrouter", prefill=True,
                                            post_process="strict", vision="off")
    raw = store.llm_connections.read_connection_raw("openrouter")
    model = raw["model"]
    assert inf.facts_for(raw, model) == {"vision": "off", "prefill": True,
                                         "post_process": "strict"}
    legacy = inf.own_target(raw)
    assert (legacy.prefill, legacy.post_process, legacy.reads_images) == (True, "strict", "no")

    store.write_config(**{keys.FORMAT_KEY: "2"})
    assert keys.is_current(store.read_config())
    inference_facts.set_stated("openrouter", model, prefill=False, post_process="none",
                               vision="on")
    got = inf.facts_for(raw, model)
    assert (got["prefill"], got["post_process"], got["vision"]) == (False, "none", "on")
    current = inf.own_target(raw)
    assert (current.prefill, current.post_process, current.reads_images) == (
        False, "none", "yes")


def test_the_operation_is_carried(at_state):
    at_state("fresh")
    assert inf.resolve("chat", operation="decide").operation == "decide"


def test_an_unreadable_campaign_resolves_globally(at_state):
    """A `campaign.md` that cannot be decoded has no routing opinion: the seam
    answers what the global scope says, rather than failing the generation."""
    ctx = at_state("routed")
    cid = ctx["cid"]
    path = store.campaigns.campaign_root(cid) / "campaign.md"

    def answer(task: str, scope_cid: str):
        try:
            return routes.common.require_inference(task, scope_cid)
        except HTTPException as exc:
            return exc.status_code, exc.detail

    # The state is chosen so the question has teeth: this campaign routes
    # `scene` elsewhere while its file can be read.
    assert answer("chat", cid) != answer("chat", "")
    path.write_bytes(b"\xff\xfe\x00 not text")
    assert inf.campaign_meta(cid) == {}
    for task in TASKS:
        assert answer(task, cid) == answer(task, ""), task


def _key_cleared_after_first_read(monkeypatch, conn_id: str) -> list[str]:
    """Patch the connection reader so `conn_id` loses its key on every read
    after the first -- a key cleared in another tab mid-request. Returns the
    log of ids read."""
    real = store.llm_connections.read_connection_raw
    reads: list[str] = []

    def reader(cid: str):
        reads.append(cid)
        got = real(cid)
        if cid == conn_id and reads.count(cid) > 1:
            return {**got, "api_key": ""}
        return got

    monkeypatch.setattr(store.llm_connections, "read_connection_raw", reader)
    return reads


@pytest.mark.parametrize("body", [{"model": "vendor/bigger"},
                                  {"connection_id": "openrouter", "model": "vendor/bigger"}])
def test_an_override_is_refused_on_the_copy_that_serves(at_state, monkeypatch, body):
    """Review probe: an override that read the standing connection twice
    checked one copy and served the other, so a key cleared between the two
    reads went out keyless with no 409. One read per call by the resolver,
    and what serves is what was checked. (The planner's own read of a legacy
    layout is not the resolver's, and decides no key: `_uncounted_planner`.)"""
    ctx = at_state("fresh")
    for scope_cid in ("", ctx["cid"]):
        _uncounted_planner(monkeypatch)
        reads = _key_cleared_after_first_read(monkeypatch, "openrouter")
        resolved, routed = routes.common.override_inference(
            SimpleNamespace(**body), "regenerate", scope_cid)
        assert reads.count("openrouter") == 1, (scope_cid, reads)
        assert resolved.chain.primary.api_key == "sk-test-active"
        assert resolved.chain.primary.model == "vendor/bigger" and routed
        monkeypatch.undo()


@pytest.mark.parametrize("body", [{"model": "vendor/bigger"}, {}])
def test_a_model_only_override_on_a_keyless_standing_route_is_a_409(at_state, monkeypatch,
                                                                    body):
    """The other half of the probe: when the one read IS keyless, the reroll
    is refused with the seam's own wording -- never served."""
    at_state("fresh")
    real = store.llm_connections.read_connection_raw
    name = real("openrouter")["name"]
    monkeypatch.setattr(store.llm_connections, "read_connection_raw",
                        lambda cid: {**real(cid), "api_key": ""})
    with pytest.raises(HTTPException) as exc:
        routes.common.override_inference(SimpleNamespace(**body), "regenerate", "")
    assert exc.value.status_code == 409
    assert exc.value.detail == {"detail": f"OpenRouter key not set ({name}, the Primary role)",
                                "kind": "missing_key"}


# ---- the campaign is read store-side, and never fails a resolution ----
def test_resolve_reads_the_campaign_itself(at_state):
    """`resolve(task, cid)` (spec 5.4): the campaign's frontmatter is read here,
    so a caller hands over an id rather than a dict it read itself."""
    ctx = at_state("routed")
    assert _primary(inf.resolve("chat", ctx["cid"])).provider_id == "local"
    assert _primary(inf.resolve("chat")).provider_id == "openrouter"
    assert inf.campaign_meta(ctx["cid"])["route_scene"] == "local"
    assert inf.campaign_meta("") == {}


@pytest.mark.parametrize("exc", [store.campaigns.CampaignNotFound("x"),
                                 store.locks.StoreBusy("busy"), OSError("gone"),
                                 UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")])
def test_a_campaign_that_cannot_be_read_resolves_globally(at_state, monkeypatch, exc):
    ctx = at_state("routed")

    def fail(cid):
        raise exc

    monkeypatch.setattr(store.campaigns, "read_campaign", fail)
    assert inf.campaign_meta(ctx["cid"]) == {}
    assert _primary(inf.resolve("chat", ctx["cid"])).provider_id == "openrouter"


def test_a_missing_campaign_resolves_globally(at_state):
    at_state("routed")
    assert inf.campaign_meta("no-such-campaign") == {}
    assert inf.resolve("chat", "no-such-campaign").chain == inf.resolve("chat").chain


# ---- the layout is decided once, globally (spec 11.1) ----
def _write_campaign_meta(cid: str, fields: dict) -> None:
    path = store.campaigns.campaign_root(cid) / "campaign.md"
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    path.write_text(dump_frontmatter({**meta, **fields}, body), encoding="utf-8")
    got = store.campaigns.read_campaign(cid)["meta"]
    assert all(str(got.get(k)) == str(v) for k, v in fields.items()), got


#: A marked campaign in a legacy store, and where its scene turns run. Since
#: slice I a campaign plays as the migration will persist it (planned equals
#: persisted, spec 15): `migrate.campaign` leaves a marked campaign exactly
#: as it stands, so its own format-2 keys route it and its legacy `route_<k>`
#: keys -- frozen for older builds -- route nothing, whatever `config.md`'s
#: format.
MARKED_CAMPAIGNS = [
    # A marker beside LEGACY keys only: they route nothing; the global role does.
    ({keys.FORMAT_KEY: 2, "route_scene": "local"}, "openrouter"),
    # A marker beside NEW-style keys: those route it.
    ({keys.FORMAT_KEY: 2, keys.use_key("scene"): keys.PIN,
      keys.pin_key("scene", "provider"): "local",
      keys.pin_key("scene", "model"): "local-model"}, "local"),
    # Both, disagreeing: the format-2 pin, never the legacy key mapped over it.
    ({keys.FORMAT_KEY: 2, "route_scene": "local", keys.use_key("scene"): keys.PIN,
      keys.pin_key("scene", "provider"): "spare",
      keys.pin_key("scene", "model"): "vendor/spare"}, "spare"),
]


@pytest.mark.parametrize(("fields", "expected"), MARKED_CAMPAIGNS)
def test_a_campaign_marker_alone_never_switches_the_layout(at_state, fields, expected):
    ctx = at_state("whitespace")
    cid = ctx["cid"]
    _write_campaign_meta(cid, fields)
    assert not keys.is_current(store.config.read_config())
    resolved = inf.resolve("chat", cid)
    assert _primary(resolved).provider_id == expected


@pytest.mark.parametrize(("fields", "expected"), MARKED_CAMPAIGNS)
def test_a_marked_campaign_plays_as_the_migration_persists_it(at_state, fields, expected):
    """Fix round 1, I5 (the review's probe): what a marked campaign in a
    legacy store plays in memory is what it plays once the store is
    migrated."""
    ctx = at_state("whitespace")
    cid = ctx["cid"]
    _write_campaign_meta(cid, fields)
    before = _primary(inf.resolve("chat", cid)).provider_id
    assert migrate.ensure().state == "done"
    assert keys.is_current(store.config.read_config())
    assert _primary(inf.resolve("chat", cid)).provider_id == before == expected


# ---- a resolution reads only what its task can reach ----
@pytest.mark.parametrize(("task", "scoped", "expected"), [
    # The global dossier pin, and the fallback: the pin answers before the
    # Primary role is walked.
    ("dossier", False, {"spare", "local"}),
    # The campaign's scene pin, and the fallback.
    ("chat", True, {"spare", "local"}),
    # A global-only route nothing pins, and a task no route claims.
    ("tagline", False, {"openrouter", "spare"}),
    ("", True, {"openrouter", "spare"}),
])
def test_a_resolution_reads_only_the_connections_its_task_can_touch(
        at_state, monkeypatch, task, scoped, expected):
    """`routed` pins summary to `claude`, voice to a dangling id, tracker to
    `local` (and `spare` in the campaign) and absorb, in the campaign, to a
    deleted connection: the resolver, resolving one task, looks none of those
    up. (The planner maps the whole legacy layout through its own lookup,
    `_uncounted_planner`.)"""
    ctx = at_state("routed")
    calls: list[str] = []
    real = store.llm_connections.read_connection_raw

    def counting(conn_id):
        calls.append(conn_id)
        return real(conn_id)

    _uncounted_planner(monkeypatch)
    monkeypatch.setattr(store.llm_connections, "read_connection_raw", counting)
    inf.resolve(task, ctx["cid"] if scoped else "")
    assert set(calls) == expected


# ---- slice B: what each attempt carries, and what it cannot do ----
def _catalog(conn_id: str, rows: list[dict]) -> None:
    """A catalog sidecar for `conn_id`, tagged with its CURRENT rev."""
    rev = store.llm_connections.read_connection_raw(conn_id)["rev"]
    store.llm_connections.set_cached_models(conn_id, rows, rev)


def test_each_attempt_carries_its_provider_facts_and_capabilities(at_state):
    ctx = at_state("routed")
    chat = inf.resolve("chat", ctx["cid"])     # campaign pin to `local`, fallback `spare`
    local, spare = chat.attempts
    raw = store.llm_connections.read_connection_raw("local")
    assert (local.provider_kind, local.base_url, local.rev) == (
        "openai_compatible", "http://localhost:1234/v1", raw["rev"])
    assert (local.billing, local.provider_preset) == ("metered", "lmstudio")
    assert local.facts == inference_facts.of("local", "local-model", raw["rev"])
    # The same answer the standalone resolver gives, read once per attempt.
    assert local.capabilities == capabilities.caps_for(raw)
    assert tuple(local.capabilities) == capabilities.NAMES
    # Its controls are the gateway's decision for the target it sends.
    assert local.controls == llm_sampling.effective(local.target)
    assert list(local.controls["controls"]) == list(llm_sampling.CONTROLS)
    spare_raw = store.llm_connections.read_connection_raw("spare")
    # OpenRouter's URL is its preset's: the connection carries none of its own.
    assert (spare.provider_kind, spare.base_url, spare.rev, spare.billing,
            spare.provider_preset) == ("openrouter", "https://openrouter.ai/api/v1",
                                       spare_raw["rev"], "metered", "openrouter")
    assert spare.capabilities == capabilities.caps_for(spare_raw, "vendor/spare")
    assert spare.controls == llm_sampling.effective(spare.target)


def test_a_subscription_connection_reports_its_billing(at_state):
    at_state("claude_active")
    first = inf.resolve("chat").attempts[0]
    assert (first.provider_kind, first.provider_preset, first.billing) == (
        "claude", "claude", "subscription")


def test_the_target_carries_the_catalog_rows_features(at_state):
    at_state("fresh")
    _catalog("openrouter", [{"id": "vendor/active", "params": ["temperature"],
                             "features": {"structured_output": True}}])
    resolved = inf.resolve("chat")
    assert _primary(resolved).model_features == {"structured_output": True}
    assert _primary(resolved).model_params == ("temperature",)
    assert resolved.attempts[0].capabilities["structured_output"] == Cap("yes", "catalog")


def test_a_row_without_features_attaches_none(at_state):
    at_state("routed")
    resolved = inf.resolve("chat")
    assert _primary(resolved).provider_id == "openrouter"
    assert _primary(resolved).model_params is not None
    assert _primary(resolved).model_features is None


def test_the_catalog_is_read_once_per_attempt(at_state, monkeypatch):
    ctx = at_state("routed")
    calls: list[str] = []
    real = store.llm_connections.cached_models

    def counting(conn_id):
        calls.append(conn_id)
        return real(conn_id)

    monkeypatch.setattr(store.llm_connections, "cached_models", counting)
    resolved = inf.resolve("chat", ctx["cid"])
    assert sorted(calls) == sorted(a.provider_id for a in resolved.attempts)


def test_a_known_no_on_a_required_capability_is_missing(at_state):
    at_state("fresh")
    _catalog("openrouter", [{"id": "vendor/active", "vision": False}])
    image = inf.resolve("image-description")
    assert image.attempts[0].capabilities["vision"] == Cap("no", "catalog")
    assert image.missing == ("vision",)
    # A route that requires nothing is not held to it.
    assert inf.resolve("chat").missing == ()


def test_a_failed_vision_test_never_lifts_a_catalogs_vision_no(at_state):
    """The refusal is the catalog's: a failed test is `unknown`, which never
    overrides a lower source's known `no` (spec 5.3, 12)."""
    at_state("fresh")
    _catalog("openrouter", [{"id": "vendor/active", "vision": False}])
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    inference_facts.record_verified("openrouter", "vendor/active", rev,
                                    {"vision": {"ok": False, "error": "refused"}})
    image = inf.resolve("image-description")
    assert image.attempts[0].capabilities["vision"] == Cap("no", "catalog")
    assert image.missing == ("vision",)


def test_unknown_is_never_missing(at_state):
    at_state("fresh")
    image = inf.resolve("image-description")
    assert image.attempts[0].capabilities["vision"] == Cap("unknown", "unknown")
    assert image.missing == () and image.fallback_missing == ()


def test_the_operations_own_capability_is_checked_in_capability_order(at_state):
    at_state("fresh")
    _catalog("openrouter", [{"id": "vendor/active", "outputs": ["embeddings"],
                             "vision": False}])
    assert inf.resolve("chat").missing == ("generate",)
    assert inf.resolve("image-description").missing == ("generate", "vision")


def test_nothing_resolved_is_missing_nothing(at_state):
    at_state("no_active")
    resolved = inf.resolve("chat")
    assert resolved.attempts == ()
    assert resolved.missing == () and resolved.fallback_missing == ()


def test_an_incapable_fallback_is_reported_and_the_facade_does_not_send_it(at_state):
    """Slice B reported it; from slice C the facade drops it (spec 5.3) -- on a
    legacy store too. `test_inference_fallback.py` holds the current layout."""
    at_state("routed")
    _catalog("openrouter", [{"id": "vendor/active", "vision": True}])
    _catalog("spare", [{"id": "vendor/spare", "vision": False}])
    image = inf.resolve("image-description")
    assert _primary(image).provider_id == "openrouter"
    assert _second(image).provider_id == "spare"
    assert image.missing == () and image.fallback_missing == ("vision",)
    sent = routes.common.build_llm()._routes(image.chain)
    assert [route.target.provider_id for route in sent] == ["openrouter"]
    # And the seam does not refuse over a fallback.
    assert routes.common.require_inference(
        "image-description").chain.primary.provider_id == "openrouter"


# ---- the seam's capability refusal ----
def _refused(call) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        call()
    assert exc.value.status_code == 409
    return exc.value


def test_the_missing_key_refusal_comes_before_the_capability_one(at_state):
    at_state("fresh")
    blind = store.llm_connections.create_connection("openrouter", "Saltmarch Router",
                                                    model="vendor/blind")
    store.write_config(route_image=blind)
    _catalog(blind, [{"id": "vendor/blind", "vision": False}])
    assert inf.resolve("image-description").missing == ("vision",)
    exc = _refused(lambda: routes.common.require_inference("image-description"))
    assert exc.detail == {
        "detail": "OpenRouter key not set (Saltmarch Router, routed for image descriptions)",
        "kind": "missing_key"}
    store.llm_connections.update_connection(blind, api_key="sk-test-blind")
    _catalog(blind, [{"id": "vendor/blind", "vision": False}])
    exc = _refused(lambda: routes.common.require_inference("image-description"))
    # A pin names no role, and its remedy is another model for the route.
    assert exc.detail == {
        "detail": "The Image descriptions route is pinned to vendor/blind on "
                  "Saltmarch Router, which cannot read images — choose another "
                  "model for this route.",
        "kind": "incapable"}


def test_an_adapter_that_cannot_read_images_keeps_todays_refusal(at_state):
    at_state("claude_active")
    image = inf.resolve("image-description")
    assert image.attempts[0].capabilities["vision"] == Cap("no", "adapter")
    exc = _refused(lambda: routes.common.require_inference("image-description"))
    assert exc.detail == store.image_drafts.UNSUPPORTED
    # Everything else on the same connection still runs.
    assert routes.common.require_inference("chat").chain.primary.provider_id == "claude"


def _embedder_only(model: str = "vendor/embedder") -> None:
    """Put `model` in the seeded OpenRouter catalog as a model that serves
    embeddings and no text (`outputs`; the name rule would lose to the
    preset's `always` generate)."""
    _catalog("openrouter", [{"id": model, "outputs": ["embeddings"]}])


def test_an_unrouted_task_is_named_as_a_generation(at_state):
    at_state("fresh")
    store.llm_connections.update_connection("openrouter", model="vendor/embedder")
    _embedder_only()
    exc = _refused(lambda: routes.common.require_inference(""))
    # No route, so nothing to pin: the remedy is the role's model alone.
    assert exc.detail == {
        "detail": "This generation runs on the Primary role (vendor/embedder on "
                  "OpenRouter), which cannot generate text — choose another "
                  "Primary model.",
        "kind": "incapable"}


@pytest.mark.parametrize("body", [{"model": "vendor/embedder"},
                                  {"connection_id": "openrouter",
                                   "model": "vendor/embedder"}])
def test_an_override_onto_an_incapable_model_is_refused_the_same_way(at_state, body):
    ctx = at_state("fresh")
    _embedder_only()
    exc = _refused(lambda: routes.common.override_inference(
        SimpleNamespace(**body), "regenerate", ctx["cid"]))
    # The override moved the call off the role's model, so no role is named.
    assert exc.detail["kind"] == "incapable"
    assert exc.detail["detail"].startswith(
        "The Scene turns route runs on vendor/embedder on OpenRouter, "
        "which cannot generate text — choose another model")
    assert "role" not in exc.detail["detail"]


def test_an_override_onto_a_keyless_connection_is_refused_for_the_key_first(at_state):
    ctx = at_state("fresh")
    keyless = store.llm_connections.create_connection(
        "openrouter", "Mara", model="vendor/embedder")
    _catalog(keyless, [{"id": "vendor/embedder", "outputs": ["embeddings"]}])
    assert inf.resolve("regenerate", ctx["cid"],
                       override=Selection(keyless, "vendor/embedder", "")).missing == ("generate",)
    exc = _refused(lambda: routes.common.override_inference(
        SimpleNamespace(connection_id=keyless, model="vendor/embedder"), "regenerate",
        ctx["cid"]))
    assert exc.detail == {"detail": "Mara: OpenRouter key not set", "kind": "missing_key"}


def test_a_soft_phase_reports_the_capability_refusal(at_state):
    ctx = at_state("fresh")
    store.llm_connections.update_connection("openrouter", model="vendor/embedder")
    _embedder_only()
    conn, reason = routes.common._soft_inference(
        lambda: routes.common.require_inference("dossier", ctx["cid"]))
    assert conn is None
    assert reason == ("The Dossier refresh route runs on the Primary role (vendor/embedder "
                      "on OpenRouter), which cannot generate text — choose another "
                      "Primary model or pin this route.")


def test_an_attempt_built_from_slice_a_fields_defaults_the_rest():
    a = Attempt("p", "m", "")
    assert (a.provider_kind, a.base_url, a.rev, a.billing, a.provider_preset) == ("",) * 5
    assert (a.facts, a.capabilities, a.controls) == ({}, {}, {})


#: The recorded states where the seam's capability check is meant to refuse:
#: a `claude` primary on the image route, which only `image_draft_prompt`
#: reaches and which it refused itself before the seam took the check over.
NEWLY_REFUSED = {("claude_active", "image-description"),
                 ("embed_with_dangling", "image-description")}


@pytest.mark.parametrize("state", sorted(baseline.STATES))
def test_the_capability_refusal_is_the_seams_only_change(state, at_state):
    """`require_inference` against the frozen baseline, refusal and success
    alike: every recorded answer stands except the intended new one, which
    answers exactly as the image route always did."""
    ctx = at_state(state)
    for task in TASKS:
        for scope, scope_cid in (("global", ""), ("campaign", ctx["cid"])):
            where = (state, task, scope)
            recorded = _recorded(state, task, scope)
            try:
                served = routes.common.require_inference(task, scope_cid)
            except HTTPException as exc:
                failure = baseline._failure(exc)
                if (state, task) in NEWLY_REFUSED:
                    assert "status" not in recorded, where
                    assert failure == {"status": 409,
                                       "detail": store.image_drafts.UNSUPPORTED}, where
                else:
                    assert failure == recorded, where
                continue
            assert "status" not in recorded and (state, task) not in NEWLY_REFUSED, where
            # And what it served is what was recorded: the connection, model,
            # sampling and catalog parameters, and the fallback the facade sends.
            fallback = _facade_fallback(served)
            assert _normalised({
                **baseline._resolved(served.chain.primary),
                "fallback": None if fallback is None
                else {"id": fallback.provider_id, "sampling": asdict(fallback.sampling)},
            }) == recorded, where


# ---- "Images: on" on a legacy connection: the user's word, through the facts ----
def test_images_on_outranks_a_catalogs_vision_no_at_the_seam(at_state):
    """A legacy connection's "Images: on" is read into its model's facts in
    memory (`Overlay.facts`), where it is the user's `yes` and outranks the
    catalog's `no` -- the resolver's answer, so nothing is missing."""
    at_state("fresh")
    store.llm_connections.update_connection("openrouter", vision="on")
    _catalog("openrouter", [{"id": "vendor/active", "vision": False}])
    image = inf.resolve("image-description")
    assert image.attempts[0].capabilities["vision"] == Cap("yes", "user")
    assert image.missing == ()
    assert routes.common.require_inference(
        "image-description").chain.primary.provider_id == "openrouter"


def test_images_on_outranks_a_catalogs_vision_no_on_the_fallback_too(at_state):
    """The same bridge, for the fallback: a format-1 fallback set to "Images:
    on" was sent before slice C whatever its catalog said, and the same
    connection as primary is served -- so it is attached, not dropped."""
    at_state("fresh")
    spare = store.llm_connections.create_connection(
        "openrouter", "Mara Spare", api_key="sk-spare", model="vendor/spare", vision="on")
    _catalog(spare, [{"id": "vendor/spare", "vision": False}])
    _catalog("openrouter", [{"id": "vendor/active", "vision": True}])
    store.write_config(fallback_connection_id=spare)
    assert not store.inference_keys.is_current(store.read_config())

    image = inf.resolve("image-description")

    assert image.fallback_missing == ()
    assert image.chain.fallback.provider_id == spare

    # The wire protocol's own `no` is never waived, on the fallback either.
    store.llm_connections.update_connection("claude", vision="on")
    store.write_config(fallback_connection_id="claude")
    image = inf.resolve("image-description")
    assert image.fallback_missing == ("vision",)
    assert image.chain.fallback is None


def test_images_on_does_not_outrank_the_adapter(at_state):
    at_state("claude_active")
    store.llm_connections.update_connection("claude", vision="on")
    assert store.llm_connections.read_connection_raw("claude")["vision"] == "on"
    exc = _refused(lambda: routes.common.require_inference("image-description"))
    assert exc.detail == store.image_drafts.UNSUPPORTED


def test_images_on_does_not_outrank_the_models_own_facts(at_state):
    at_state("fresh")
    store.llm_connections.update_connection("openrouter", vision="on")
    inference_facts.set_overrides("openrouter", "vendor/active", {"vision": "no"})
    exc = _refused(lambda: routes.common.require_inference("image-description"))
    assert exc.detail["kind"] == "incapable"


def test_a_failed_test_never_refuses_and_the_users_word_outranks_it(at_state):
    """Spec 12: a failed test leaves the model unverified, so it cannot make
    the seam refuse -- and the user's own override beats it."""
    at_state("fresh")
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    inference_facts.record_verified("openrouter", "vendor/active", rev, {
        "vision": {"ok": False, "error": "image input is not supported"}})
    # The failure alone: unverified, carrying the error, and not refused.
    image = inf.resolve("image-description")
    assert image.attempts[0].capabilities["vision"] == Cap(
        "unknown", "test", "image input is not supported")
    assert image.missing == ()
    assert routes.common.require_inference(
        "image-description").chain.primary.provider_id == "openrouter"
    # The user's override outranks the failed test.
    inference_facts.set_overrides("openrouter", "vendor/active", {"vision": "yes"})
    conn = store.llm_connections.read_connection_raw("openrouter")
    assert capabilities.caps_for(conn)["vision"] == Cap("yes", "user")
    assert routes.common.require_inference(
        "image-description").chain.primary.provider_id == "openrouter"
    assert store.post_images.capability(inf.own_target(conn)) == "yes"
    # And so does a legacy connection's "Images: on" (the planner's facts).
    inference_facts.state("openrouter", "vendor/active", overrides={"vision": ""})
    store.llm_connections.update_connection("openrouter", vision="on")
    image = inf.resolve("image-description")
    assert image.attempts[0].capabilities["vision"] == Cap("yes", "user")


def test_the_name_rule_hides_a_model_but_never_refuses_it(at_state):
    """A chat model whose id says "embed" is a guess the name rule gets wrong:
    the picker hides it, the seam lets it run."""
    at_state("fresh")
    chat = store.llm_connections.create_connection(
        "openai_compatible", "Saltmarch Local", base_url="https://llm.saltmarch.test/v1",
        model="mara-embedded-chat")
    raw = store.llm_connections.read_connection_raw(chat)
    assert store.inference.providers.infer(raw).id == "custom"
    store.write_config(route_dossier=chat)
    dossier = inf.resolve("dossier")
    assert dossier.attempts[0].capabilities["generate"] == Cap("no", "name")
    assert dossier.missing == ()
    assert routes.common.require_inference("dossier").chain.primary.provider_id == chat
    group, reason = capabilities.group_for(
        dossier.attempts[0].capabilities, "generate", store.inference.providers.PRESETS["custom"])
    assert group == "hidden" and "name" in reason


def test_an_anthropic_connection_needs_a_key_to_send():
    """The same credential rule as OpenRouter's: the Anthropic API answers a
    keyless request with a 401, so the seam refuses it first with a 409."""
    conn = {"id": "a", "kind": "anthropic", "name": "Anthropic API",
            "base_url": "", "api_key": "", "model": "claude-test-1"}
    assert inf.problem(conn) == "Anthropic API key not set"
    assert inf.problem({**conn, "api_key": "test-key-anthropic"}) is None


# ---- a native attempt's controls are n/a (slice H, Task 9; spec 8) ----
def _decision_on(tmp_path, rows: list[dict]):
    """A format-2 store whose Decision role is `vendor/decider` on the seeded
    OpenRouter provider, served under the `Warm` preset, with `spare` as its
    fallback, the catalog rows `rows` cached; the scene-break route's decide
    resolution."""
    with baseline.client_at(tmp_path) as client:
        inference_fixtures.decide_only(client, fallback=True)
        rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
        store.llm_connections.set_cached_models("openrouter", rows, rev)
        pid = store.sampler_presets.create_preset("Warm", {"temperature": 0.8})
        inference_fixtures.put_settings(client, {"roles": {"decision": {"selection": {
            "provider": "openrouter", "model": "vendor/decider", "preset": pid}}}})
        return inf.resolve("scene-break", operation="decide")


def test_a_native_attempt_reports_na_controls(tmp_path):
    resolved = _decision_on(tmp_path, [{"id": "vendor/decider", "outputs": ["decisions"]}])
    native, fallback = resolved.attempts
    assert (native.decision_mode, fallback.decision_mode) == ("native", "structured")
    assert native.controls == llm_sampling.not_applicable(native.target,
                                                          llm_sampling.WHY_NATIVE)
    assert native.controls["requested"] == {"temperature": 0.8}
    assert native.controls["effective"] == {}
    # The structured fallback keeps what it sends.
    assert fallback.controls == llm_sampling.effective(fallback.target)


def test_a_dual_capable_attempt_keeps_its_controls(tmp_path):
    """Ruling 1: a model that can generate is structured, so its controls are
    slice F's -- what `effective` says of the target it is sent."""
    resolved = _decision_on(tmp_path, [{"id": "vendor/decider",
                                        "outputs": ["text", "decisions"]}])
    primary = resolved.attempts[0]
    assert primary.decision_mode == "structured"
    assert primary.controls == llm_sampling.effective(primary.target)
    assert primary.controls["effective"] == {"temperature": 0.8}
