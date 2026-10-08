"""At format 2 a model's own facts drive the wire (slice C, Task 3).

A migrated store keeps every legacy connection field where it was (frozen, not
deleted), and writes the connection's `vision`, `prefill` and `post_process`
into its model's facts (`llm_connections/<id>.facts.json`). From then on the
facts are the user's word -- so the lowering overlays them onto the connection
dict the facade reads, and every consumer (`llm.prefill_capable`, the strict
post-processing, `post_images.capability`) answers per model without changing.

Facts are written here with `facts.set_stated`, which is what the Models
screen's writer will call; the screen itself is a later task. Invented
connection ids and the codebase's placeholder names only.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import grimoire.store as store
from grimoire import llm, llm_sampling, routes
from grimoire.store import post_images
from grimoire.store.inference import facts, migrate
from grimoire.store.inference import resolve as inference
from grimoire.store.inference.capabilities import Cap
from grimoire.store.inference.resolved import Attempt, ResolvedInference

from . import inference_baseline as base
from . import inference_baseline_c as base_c

MODEL = "vendor/active"


@pytest.fixture
def client(tmp_path):
    with base.client_at(tmp_path) as c:
        yield c


def _migrate() -> None:
    got = migrate.ensure()
    assert got.state == "done", got
    assert store.inference_keys.is_current(store.read_config())


def _catalog(vision: bool) -> None:
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    store.llm_connections.set_cached_models(
        "openrouter", [{"id": MODEL, "vision": vision, "params": ["temperature"]}], rev)


def _primary(task: str = "chat", cid: str = "") -> dict:
    conn = inference.resolve(task, cid).conn
    assert conn is not None
    return conn


def test_format_2_prefill_and_post_process_come_from_facts(client):
    base._fresh(client)
    _migrate()
    # The legacy connection says neither; the model's facts now say both.
    raw = store.llm_connections.read_connection_raw("openrouter")
    assert raw["prefill"] is False and raw["post_process"] in ("", "none")
    facts.set_stated("openrouter", MODEL, prefill=True, post_process="strict")

    conn = _primary()
    assert conn["prefill"] is True and conn["post_process"] == "strict"
    assert llm.prefill_capable(conn)
    # The connection's own lowering (the standing fallback, the list) agrees.
    own = inference.own_sampling(raw)
    assert own["prefill"] is True and own["post_process"] == "strict"

    # And the other way: the facts' False outranks a legacy True.
    store.llm_connections.update_connection("openrouter", prefill=True,
                                            post_process="strict")
    facts.set_stated("openrouter", MODEL, prefill=False, post_process="none")
    conn = _primary()
    assert conn["prefill"] is False and conn["post_process"] == "none"
    assert not llm.prefill_capable(conn)


def test_format_2_unstated_facts_map_to_the_defaults(client):
    """A model nothing was stated about runs with prefill off, no post
    processing and post images on auto -- not the connection's flags. That is
    what a reroll onto another model gets (per-model facts, spec 4.2)."""
    base._fresh(client)
    store.llm_connections.update_connection("openrouter", prefill=True,
                                            post_process="strict", vision="on")
    _migrate()
    assert _primary()["prefill"] is True  # migrated onto the connection's model
    resolved, _ = routes.common.override_inference(
        SimpleNamespace(model="vendor/bigger"), "regenerate", "")
    conn = resolved.conn
    assert conn["model"] == "vendor/bigger"
    assert (conn["prefill"], conn["post_process"], conn["vision"]) == (False, "none", "")


def test_format_1_ignores_the_facts(client):
    base._fresh(client)
    facts.set_stated("openrouter", MODEL, prefill=True, post_process="strict",
                     vision="off")
    assert not store.inference_keys.is_current(store.read_config())
    conn = _primary()
    assert (conn["prefill"], conn["post_process"], conn["vision"]) == (False, "none", "")


def test_format_2_post_images_read_the_model_facts(client):
    base._fresh(client)
    base._config(send_images="on")
    _catalog(vision=False)
    _migrate()
    assert post_images.capability(_primary()) == "no"
    facts.set_stated("openrouter", MODEL, vision="on")
    conn = _primary()
    assert conn["vision"] == "on"
    assert post_images.capability(conn) == "yes"
    assert post_images.images_for(conn) > 0
    assert client.get("/api/config").json()["send_images_reach"] == "yes"


def test_vision_off_stops_post_images_but_not_image_descriptions(client):
    base._fresh(client)
    base._config(send_images="on")
    _catalog(vision=True)
    _migrate()
    assert post_images.capability(_primary()) == "yes"

    facts.set_stated("openrouter", MODEL, vision="off")
    conn = _primary()
    assert conn["vision"] == "off"
    assert post_images.capability(conn) == "no"
    assert post_images.images_for(conn) == 0
    assert client.get("/api/config").json()["send_images_reach"] == "no"

    # "off" is the post-image preference, not a capability `no` (ruling 2):
    # the catalog's yes still serves image descriptions.
    usable = routes.common.require_inference("image-description")
    assert usable.attempts[0].capabilities["vision"] == Cap("yes", "catalog")
    assert usable.missing == ()


def test_vision_on_is_a_user_yes(client):
    base._fresh(client)
    base._config(send_images="on")
    _catalog(vision=False)
    _migrate()
    with pytest.raises(HTTPException) as refused:
        routes.common.require_inference("image-description")
    assert refused.value.status_code == 409
    assert refused.value.detail["kind"] == "incapable"

    facts.set_stated("openrouter", MODEL, vision="on")
    usable = routes.common.require_inference("image-description")
    assert usable.attempts[0].capabilities["vision"] == Cap("yes", "user")
    assert post_images.capability(usable.conn) == "yes"


def test_the_images_on_bridge_is_format_1_only(client):
    """A legacy "Images: on" waives a catalog's vision `no` at format 1. At
    format 2 the connection's flag is frozen legacy and says nothing: the
    model's facts do, and a cleared `vision` there is refused like any `no`."""
    base._fresh(client)
    store.llm_connections.update_connection("openrouter", vision="on")
    _catalog(vision=False)
    routes.common.require_inference("image-description")  # format 1: the bridge

    _migrate()
    routes.common.require_inference("image-description")  # migrated: a user yes
    facts.set_stated("openrouter", MODEL, vision="")
    assert store.llm_connections.read_connection_raw("openrouter")["vision"] == "on"
    with pytest.raises(HTTPException) as refused:
        routes.common.require_inference("image-description")
    assert refused.value.status_code == 409
    assert refused.value.detail["kind"] == "incapable"


def _by_hand(*, current: bool, vision_flag: str, source: str) -> ResolvedInference:
    conn = {"id": "openrouter", "kind": "openrouter", "name": "OpenRouter",
            "api_key": "sk-test", "model": MODEL, "vision": vision_flag}
    attempt = Attempt("openrouter", MODEL, "", conn, provider_preset="openrouter",
                      capabilities={"vision": Cap("no", source)})
    return ResolvedInference(
        task="image-description", operation="generate", route="image",
        legacy_route="image", role="fast", via="role", scope="global",
        attempts=(attempt,), current=current, missing=("vision",))


def test_refusal_is_one_pure_decision():
    # Format 1: "Images: on" outranks a catalog `no`; nothing to refuse.
    assert inference.refusal(_by_hand(current=False, vision_flag="on",
                                      source="catalog")) is None
    # Format 2: the bridge is gone.
    status, detail = inference.refusal(_by_hand(current=True, vision_flag="on",
                                                source="catalog"))
    assert status == 409 and detail["kind"] == "incapable"
    # The wire protocol's `no` keeps its old body at either format.
    for current in (False, True):
        assert inference.refusal(_by_hand(current=current, vision_flag="on",
                                          source="adapter")) == (
            409, store.image_drafts.UNSUPPORTED)
    # No connection at all is the missing-key refusal.
    empty = ResolvedInference(task="chat", operation="generate", route="scene",
                              legacy_route="scene", role="", via="", scope="none",
                              attempts=())
    assert inference.refusal(empty) == (
        409, {"detail": "No LLM connection selected", "kind": "missing_key"})


def test_glm_legacy_effort_still_applies_under_a_route_preset(client):
    """Ruling 3: until derived reasoning presets (slice I), the legacy GLM
    effort keeps riding when the effective preset sets none -- a route preset
    included, at format 2 as at format 1."""
    ctx = base_c.STATES["glm_max_under_route_preset"](client)
    for migrated in (False, True):
        if migrated:
            _migrate()
        for cid in ("", ctx["cid"]):
            conn = _primary("rolling-summary", cid)
            assert conn["sampling"]["preset_id"] == "cold", migrated
            assert llm_sampling.effective(conn)["effective"]["reasoning_effort"] == "max", \
                migrated


def test_an_unreadable_facts_sidecar_lowers_to_the_defaults(client):
    """A corrupt `<id>.facts.json` is no facts: resolve and lower keep working,
    every behaviour unstated, and the store's own dict is never mutated."""
    base._fresh(client)
    _migrate()
    store.llm_connections.facts_path("openrouter").write_text("{not json", encoding="utf-8")
    conn = _primary()
    assert (conn["prefill"], conn["post_process"], conn["vision"]) == (False, "none", "")
    raw = store.llm_connections.read_connection_raw("openrouter")
    before = dict(raw)
    lowered = inference.lower(raw, inference.preset_sampling(""), current=True)
    assert lowered["post_process"] == "none" and raw == before


def test_an_unknown_post_process_value_lowers_to_none():
    conn = inference.with_facts({"id": "x"}, {"post_process": "shouting"})
    assert conn["post_process"] == "none"


def test_the_facts_model_key_is_the_effective_model():
    """The overlay keys facts the way the migration wrote them: by the model a
    connection actually runs (`llm.effective_model`, restated because the
    store never imports `llm`)."""
    for conn in ({"kind": "claude", "model": ""}, {"kind": "claude", "model": "sonnet"},
                 {"kind": "openrouter", "model": MODEL}, {"kind": "openrouter", "model": ""}):
        assert facts.model_of(conn) == llm.effective_model(conn)
