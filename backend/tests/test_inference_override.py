"""A reroll can name a provider, a model and a preset (spec 5.6).

`RegenerateBody` carries `provider` and `preset` beside `model`, with the legacy
`connection_id` read as `provider`. The meanings that moved belong to the
CURRENT layout (`inference_format` 2) and only to it:

- a provider alone keeps the STANDING model on that provider (the legacy
  meaning, "that connection's own model", ended with the legacy field), and
  with no standing selection to keep it is a 400;
- a preset replaces the route-level preset too, for the primary attempt only
  (on a format-1 store as well: the final review's S-M9);
- `routed` compares the effective provider, model and preset.

A format-1 store keeps today's meanings exactly; the frozen fixture pins that
(`test_inference_equivalence.py`), and the format-1 tests here pin the two edges
this change touches (a provider-only body, and a preset the old body never had).

Stores are built directly in the new layout, by writing the role and route keys
and the marker through `store.write_config`, so nothing here depends on the
migration. The last test is the one that does, and runs once it exists.

Invented connection ids and fake keys only.
"""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import grimoire.store as store
from grimoire import routes
from grimoire.llm import effective_model
from grimoire.routes.models import RegenerateBody
from grimoire.store.inference import resolve as inf

from . import inference_baseline as base

PRESET_CLEAR = store.sampler_presets.PRESET_CLEAR

#: What "no preset" looks like on an attempt that was handed none.
NO_PRESET = {"preset_id": "", "preset_name": "", "scope": "none", "params": {}}


@pytest.fixture
def at(tmp_path):
    """`at()` builds a store and returns its context (`cid`).

    The baseline's `fresh` state (the seeded OpenRouter connection `openrouter`,
    key and model `vendor/active`), plus `spare` (OpenRouter, `vendor/spare`),
    `local` (OpenAI-compatible, `local-model`) and `nokey` (OpenRouter, no key);
    the presets warm, cold and hot. The store is still format 1: each test
    switches it, so the legacy layout is what a test starts from."""
    cms = []

    def build() -> dict:
        cm = base.client_at(tmp_path / "store")
        client = cm.__enter__()
        cms.append(cm)
        ctx = base._fresh(client)
        base._presets()
        store.sampler_presets.create_preset("hot", {"temperature": 1.4})
        base._spare(client)
        base._local(client)
        base._connection(client, "nokey")
        # The catalog says what each model takes, so `model_params` is a real
        # part of a cell rather than an absent one.
        rev = store.llm_connections.read_connection_raw("spare")["rev"]
        store.llm_connections.set_cached_models("spare", [
            {"id": "vendor/active", "params": ["temperature", "top_p"]},
            {"id": "vendor/spare", "params": ["temperature"]},
        ], rev)
        return ctx

    yield build
    for cm in reversed(cms):
        cm.__exit__(None, None, None)


def _format2(**fields: str) -> None:
    """Switch the store to the current layout with `fields` as its keys."""
    store.write_config(inference_format="2", **fields)
    assert store.inference_keys.is_current(store.read_config())


def _standing(model: str = "vendor/active", preset: str = "") -> dict:
    return {"role_primary_provider": "openrouter", "role_primary_model": model,
            "role_primary_preset": preset}


def _run(body, cid: str = ""):
    """`(resolved, routed)` for a reroll carrying `body` (a dict or a model)."""
    if isinstance(body, dict):
        body = SimpleNamespace(**body)
    return routes.common.override_inference(body, "regenerate", cid)


def _refused(body, cid: str = "") -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        _run(body, cid)
    return caught.value


# ---- the body ----
def test_the_body_carries_provider_and_preset():
    body = RegenerateBody(provider="spare", model="vendor/x", preset="warm")
    assert (body.provider, body.model, body.preset) == ("spare", "vendor/x", "warm")
    empty = RegenerateBody()
    assert empty.provider is None and empty.preset is None
    assert RegenerateBody(connection_id="spare").connection_id == "spare"


def test_connection_id_is_read_as_provider(at):
    ctx = at()
    _format2(**_standing())
    legacy, legacy_routed = _run(RegenerateBody(connection_id="spare"), ctx["cid"])
    named, named_routed = _run(RegenerateBody(provider="spare"), ctx["cid"])
    assert base._resolved(legacy.conn) == base._resolved(named.conn)
    assert legacy_routed is named_routed is True
    # Both present: `provider` is the field, `connection_id` only stands in for
    # its absence.
    both, _ = _run(RegenerateBody(provider="spare", connection_id="local"), ctx["cid"])
    assert both.conn["id"] == "spare"
    # An empty `provider` is absent.
    blank, _ = _run(RegenerateBody(provider="  ", connection_id="local"), ctx["cid"])
    assert blank.conn["id"] == "local"


# ---- a provider alone ----
def test_provider_only_keeps_the_standing_model_on_format_2(at):
    ctx = at()
    _format2(**_standing())
    resolved, routed = _run({"provider": "spare"}, ctx["cid"])
    # The standing model, not spare's own (`vendor/spare`); the catalog of the
    # provider it now runs on says what that model takes.
    assert {**base._resolved(resolved.conn), "routed": routed} == {
        "conn": "spare", "model": "vendor/active", "sampling": NO_PRESET,
        "model_params": ["temperature", "top_p"], "routed": True}


def test_provider_only_keeps_the_standing_preset_on_format_2(at):
    ctx = at()
    _format2(**_standing(preset="cold"))
    resolved, routed = _run({"provider": "spare"}, ctx["cid"])
    sampling = resolved.conn["sampling"]
    assert (sampling["preset_id"], sampling["scope"]) == ("cold", "connection")
    assert routed is True


def test_provider_only_on_the_standing_provider_is_not_routed(at):
    ctx = at()
    _format2(**_standing())
    resolved, routed = _run({"provider": "openrouter"}, ctx["cid"])
    assert resolved.conn["id"] == "openrouter"
    assert effective_model(resolved.conn) == "vendor/active"
    assert routed is False


def test_provider_only_without_a_standing_selection_is_400(at):
    ctx = at()
    _format2()    # a current store that names no Primary
    exc = _refused({"provider": "spare"}, ctx["cid"])
    assert exc.status_code == 400
    assert "name a model for this provider" in str(exc.detail).lower()
    # The body's refusal comes before the connection's: a provider with no key
    # is asked for a model first, because that is the fix that is the caller's.
    assert _refused({"provider": "nokey"}, ctx["cid"]).status_code == 400


def test_a_provider_and_a_model_need_no_standing_selection(at):
    ctx = at()
    _format2()
    resolved, routed = _run({"provider": "spare", "model": "vendor/spare"}, ctx["cid"])
    assert resolved.conn["id"] == "spare" and routed is True
    assert effective_model(resolved.conn) == "vendor/spare"


def test_provider_only_with_an_empty_standing_model(at):
    """A standing selection that names a provider and no model (a `claude`
    connection on its default, say) has a provider but nothing to carry to
    another one."""
    ctx = at()
    _format2(**_standing(model=""))
    exc = _refused({"provider": "spare"}, ctx["cid"])
    assert exc.status_code == 400
    assert "name a model for this provider" in str(exc.detail).lower()
    # The standing provider itself is no move: nothing to keep, nothing changed.
    resolved, routed = _run({"provider": "openrouter"}, ctx["cid"])
    assert resolved.conn["id"] == "openrouter"
    assert routed is False
    # Naming the model as well is how the caller answers the 400.
    resolved, routed = _run({"provider": "spare", "model": "vendor/spare"}, ctx["cid"])
    assert resolved.conn["id"] == "spare" and routed is True


def test_a_model_alone_still_needs_the_standing_selection(at):
    ctx = at()
    _format2()
    exc = _refused({"model": "vendor/bigger"}, ctx["cid"])
    assert exc.status_code == 409 and exc.detail["kind"] == "missing_key"


def test_an_unknown_provider_is_400_on_format_2(at):
    ctx = at()
    _format2(**_standing())
    exc = _refused({"provider": "nope"}, ctx["cid"])
    assert exc.status_code == 400 and "no longer exists" in str(exc.detail)


def test_a_provider_that_cannot_send_is_the_same_409(at):
    ctx = at()
    _format2(**_standing())
    exc = _refused({"provider": "nokey"}, ctx["cid"])
    assert exc.status_code == 409
    assert exc.detail["kind"] == "missing_key"
    assert "nokey" in exc.detail["detail"]


def test_provider_only_keeps_the_standing_model_on_format_1_too(at):
    """Since slice I a format-1 store plays as format 2, in memory, so a
    provider named alone means what it means there (spec 5.6): that provider
    at the STANDING model -- never at a model of its own."""
    ctx = at()
    assert not store.inference_keys.is_current(store.read_config())
    standing, _ = _run({}, ctx["cid"])
    resolved, routed = _run({"provider": "spare"}, ctx["cid"])
    assert resolved.conn["id"] == "spare"
    assert effective_model(resolved.conn) == effective_model(standing.conn) != "vendor/spare"
    assert routed is True
    # ... and `connection_id` says the same thing it always did.
    legacy, _ = _run({"connection_id": "spare"}, ctx["cid"])
    assert base._resolved(legacy.conn) == base._resolved(resolved.conn)


def test_a_preset_is_honoured_on_format_1_too(at):
    """Spec 5.6 settles the override preset without restricting it to the new
    layout, and a store whose migration keeps failing can stay at format 1:
    a named preset is the primary's there as well, and one that names nothing
    is the same 400."""
    ctx = at()
    base._config(preset_scene="cold", fallback_connection_id="local")
    assert not store.inference_keys.is_current(store.read_config())
    plain, plain_routed = _run({}, ctx["cid"])
    assert plain.conn["sampling"]["preset_id"] == "cold"
    assert plain_routed is False

    named, named_routed = _run({"preset": "hot"}, ctx["cid"])
    assert named.conn["id"] == plain.conn["id"]
    assert effective_model(named.conn) == effective_model(plain.conn)
    assert named.conn["sampling"]["preset_id"] == "hot"
    assert named.conn["sampling"]["scope"] == "override"
    assert named_routed is True
    # The primary's alone: the fallback keeps the route's preset.
    assert named.fallback["id"] == "local"
    assert named.fallback["sampling"]["preset_id"] == "cold"
    # Naming what the route already runs is no override.
    assert _run({"preset": "cold"}, ctx["cid"])[1] is False
    assert _refused({"preset": "nosuch"}, ctx["cid"]).status_code == 400


# ---- a preset ----
def _route_preset_with_fallback(preset: str = "cold") -> None:
    """Standing Primary on `openrouter`, a route preset on the scene route, and
    a fallback on `local` whose own preset is `warm`."""
    _format2(**_standing(), preset_scene=preset,
             role_primary_fallback_provider="local",
             role_primary_fallback_model="local-model",
             role_primary_fallback_preset="warm")


def test_an_override_preset_outranks_the_route_preset_on_the_primary_only(at):
    ctx = at()
    _route_preset_with_fallback()
    standing, _ = _run({}, ctx["cid"])
    assert standing.conn["sampling"]["preset_id"] == "cold"
    assert standing.conn["sampling"]["scope"] == "global"
    # Unrouted, the route's preset follows the route onto the fallback.
    assert standing.fallback["id"] == "local"
    assert standing.fallback["sampling"]["preset_id"] == "cold"

    resolved, routed = _run({"preset": "hot"}, ctx["cid"])
    assert resolved.conn["id"] == "openrouter"
    assert resolved.conn["sampling"]["preset_id"] == "hot"
    assert resolved.conn["sampling"]["scope"] == "override"
    assert resolved.conn["sampling"]["params"] == {"temperature": 1.4}
    assert routed is True
    # The override is for the primary alone: the fallback gets what it would
    # have had without it -- the route's preset, as on the standing call.
    assert resolved.fallback["id"] == "local"
    assert resolved.fallback["sampling"]["preset_id"] == "cold"
    assert resolved.fallback["sampling"]["scope"] == "global"
    # What the facade will send is what the resolver says: the fallback this
    # call carries.
    assert resolved.conn[routes.common.llm.FALLBACK_KEY] is resolved.fallback
    sent = routes.common.build_llm()._routes(resolved.chain)
    assert [route.target.provider_id for route in sent] == ["openrouter", "local"]
    assert sent[1].target.sampling.preset_id == "cold"


def test_an_override_preset_composes_with_a_provider_and_a_model(at):
    ctx = at()
    _route_preset_with_fallback()
    resolved, routed = _run({"provider": "spare", "model": "vendor/spare",
                             "preset": "hot"}, ctx["cid"])
    assert resolved.conn["id"] == "spare"
    assert effective_model(resolved.conn) == "vendor/spare"
    assert resolved.conn["sampling"]["preset_id"] == "hot"
    assert routed is True


def test_preset_clear_in_an_override(at):
    ctx = at()
    _route_preset_with_fallback()
    resolved, routed = _run({"preset": PRESET_CLEAR}, ctx["cid"])
    sampling = resolved.conn["sampling"]
    assert sampling["preset_id"] == "" and sampling["params"] == {}
    assert sampling["scope"] == "override"
    assert routed is True    # the standing route runs `cold`
    # Still the primary alone: the fallback keeps the route's preset.
    assert resolved.fallback["sampling"]["preset_id"] == "cold"
    sent = routes.common.build_llm()._routes(resolved.chain)
    assert sent[1].target.sampling.preset_id == "cold"


def test_an_unknown_override_preset_is_400(at):
    ctx = at()
    _route_preset_with_fallback()
    exc = _refused({"preset": "nosuch"}, ctx["cid"])
    assert exc.status_code == 400
    assert "preset" in str(exc.detail).lower()
    # Body before connection: with a provider that cannot send, the preset is
    # still what is wrong with the request.
    assert _refused({"provider": "nokey", "model": "m", "preset": "nosuch"},
                    ctx["cid"]).status_code == 400
    # An id that is no safe path segment names nothing either.
    assert _refused({"preset": "../x"}, ctx["cid"]).status_code == 400


def test_routed_compares_the_preset_too(at):
    ctx = at()
    _format2(**_standing(), preset_scene="cold")
    # The same provider and model, a different preset: a different route.
    assert _run({"preset": "hot"}, ctx["cid"])[1] is True
    # Naming the preset the standing route already runs is no override.
    assert _run({"preset": "cold"}, ctx["cid"])[1] is False
    # Naming the model as well changes nothing about that.
    assert _run({"model": "vendor/active", "preset": "cold"}, ctx["cid"])[1] is False
    # "No preset" against a route that runs one is a different route ...
    assert _run({"preset": PRESET_CLEAR}, ctx["cid"])[1] is True
    # ... and against one that runs none it is the same.
    _format2(preset_scene="")
    assert _run({"preset": PRESET_CLEAR}, ctx["cid"])[1] is False
    assert _run({"preset": "hot"}, ctx["cid"])[1] is True


def test_an_override_preset_does_not_move_the_standing_resolution(at):
    ctx = at()
    _format2(**_standing(), preset_scene="cold")
    plain = inf.resolve("regenerate", ctx["cid"])
    assert plain.standing_preset is None
    named = inf.resolve("regenerate", ctx["cid"],
                        override=inf.Selection("", "", "hot"))
    assert named.standing_preset == "cold"
    assert named.standing == plain.standing
    assert named.attempts[0].preset_id == "hot"


# ---- after the migration ----
MIGRATE = importlib.util.find_spec("grimoire.store.inference.migrate")


@pytest.mark.skipif(MIGRATE is None,
                    reason="store.inference.migrate does not exist on this branch; "
                           "Task 2 adds it and this runs once both are merged")
@pytest.mark.parametrize("state", sorted(base.STATES))
def test_provider_only_override_cells_after_migration(state, tmp_path):
    """Every provider-only body in the baseline, on every baseline state, once
    the store is migrated: the named provider at the STANDING model, with the
    standing route's sampling. That is the one meaning that moves (the
    baseline's own cells record the legacy "that connection's own model"), so
    the expectation is built from the migrated store's own standing selection
    rather than read back from the fixture."""

    provider_only = {name: body for name, body in base.OVERRIDE_BODIES.items()
                     if set(body) == {"connection_id"}}
    assert provider_only    # the roster has them
    with base.client_at(tmp_path) as client:
        cid = base.STATES[state](client)["cid"]
        # The anchor: what the standing route was BEFORE the migration. The
        # expectations below are built from the migrated store's own standing
        # selection, so without this a migration that lost the selection (or
        # moved it) would make them agree with themselves.
        legacy = base._normalise(base._override({}, cid), base._revs())
        base.migrate_state(state)
        assert store.inference_keys.is_current(store.read_config())
        standing = inf.resolve("regenerate", cid)
        if "status" in legacy:
            assert standing.standing is None, state
        else:
            assert standing.standing is not None, state
            assert standing.standing.provider == legacy["conn"], state
            assert effective_model(standing.conn) == legacy["model"], state
            migrated = standing.conn["sampling"]
            assert migrated["preset_id"] == legacy["sampling"]["preset_id"], state
            assert migrated["params"] == legacy["sampling"]["params"], state
        for name, body in provider_only.items():
            observed = base._normalise(base._override(body, cid), base._revs())
            raw = store.llm_connections.read_connection_raw(body["connection_id"]) \
                if _exists(body["connection_id"]) else None
            where = (state, name)
            if raw is None:
                assert observed["status"] == 400, where
            elif standing.standing is None or (
                    not standing.standing.model
                    and raw["id"] != standing.standing.provider):
                # Nothing to keep: no standing selection, or one that runs its
                # provider's default model (a `claude` connection with none set),
                # which is not a model to carry to another provider.
                assert observed["status"] == 400, where
                assert "name a model" in str(observed["detail"]).lower(), where
            elif inf.problem(raw) is not None:
                assert observed["status"] == 409, where
            else:
                model = standing.standing.model
                conn = {**raw, "model": model}
                expected = base._normalise({
                    "conn": raw["id"], "model": effective_model(conn),
                    "sampling": standing.conn["sampling"],
                    "model_params": inf.model_params(conn),
                    "routed": raw["id"] != standing.standing.provider,
                }, base._revs())
                assert observed == expected, where


def _exists(conn_id: str) -> bool:
    try:
        store.llm_connections.read_connection_raw(conn_id)
    except Exception:    # noqa: BLE001 -- any unreadable id is "no connection"
        return False
    return True
