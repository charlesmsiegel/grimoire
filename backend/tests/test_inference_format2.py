"""At format 2 a model's own facts drive the wire (slice C, Task 3).

A migrated store keeps every legacy connection field where it was (frozen, not
deleted), and writes the connection's `vision`, `prefill` and `post_process`
into its model's facts (`llm_connections/<id>.facts.json`). From then on the
facts are the user's word -- so the lowering overlays them onto the connection
dict the facade reads, and every consumer (`llm.prefill_capable`, the strict
post-processing, `post_images.capability`) answers per model without changing.

Facts are written here with `facts.set_stated`, which is what the Models
screen's writer will call; the screen itself is a later task.

Since slice I every store plays as format 2: a store the migration has not
reached -- format 1, an unmarked campaign, a scope not yet retired -- is read
through the planner, in memory (`legacy_plan.overlay`), including a legacy
connection's model fields as its model's facts and its GLM effort as a derived
reasoning preset. Nothing on that path writes. The second half of this file
holds that.

Invented connection ids and the codebase's placeholder names only.
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import grimoire.store as store
from grimoire import llm, llm_sampling, routes
from grimoire.store import post_images
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
from grimoire.store.inference import facts, legacy_plan, migrate
from grimoire.store.inference import resolve as inference
from grimoire.store.inference.capabilities import Cap
from grimoire.store.inference.resolved import Attempt, ResolvedInference

from . import inference_baseline as base
from . import inference_baseline_c as base_c
from . import inference_fixtures
from .llm_fakes import FakeLLM

MODEL = "vendor/active"


@pytest.fixture
def legacy(tmp_path):
    """A client on a format-1 store (`inference_baseline.client_at`): what the
    frozen baselines build on. A test of a store born at format 2 takes
    `conftest.client` instead."""
    with base.client_at(tmp_path) as c:
        yield c


def _migrate() -> None:
    got = migrate.ensure()
    assert got.state == "done", got
    assert store.inference_keys.is_current(store.read_config())


def _migrate_unretired() -> None:
    """`_migrate` as a C-H build migrated: retirement held off, so the store
    is at format 2 with its legacy GLM effort still planned in memory."""
    got = inference_fixtures.migrate_as_c_h()
    assert got.state == "done", got
    assert store.inference_keys.is_current(store.read_config())
    assert not store.read_config()[store.inference_keys.RETIRED_KEY]


def _catalog(vision: bool) -> None:
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    store.llm_connections.set_cached_models(
        "openrouter", [{"id": MODEL, "vision": vision, "params": ["temperature"]}], rev)


def _primary(task: str = "chat", cid: str = "") -> dict:
    conn = inference.resolve(task, cid).conn
    assert conn is not None
    return conn


def test_format_2_prefill_and_post_process_come_from_facts(legacy):
    base._fresh(legacy)
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


def test_format_2_unstated_facts_map_to_the_defaults(legacy):
    """A model nothing was stated about runs with prefill off, no post
    processing and post images on auto -- not the connection's flags. That is
    what a reroll onto another model gets (per-model facts, spec 4.2)."""
    base._fresh(legacy)
    store.llm_connections.update_connection("openrouter", prefill=True,
                                            post_process="strict", vision="on")
    _migrate()
    assert _primary()["prefill"] is True  # migrated onto the connection's model
    resolved, _ = routes.common.override_inference(
        SimpleNamespace(model="vendor/bigger"), "regenerate", "")
    conn = resolved.conn
    assert conn["model"] == "vendor/bigger"
    assert (conn["prefill"], conn["post_process"], conn["vision"]) == (False, "none", "")


def test_format_1_reads_the_facts_as_format_2_does(legacy):
    """A legacy store resolves as format 2 in memory (slice I): its model's
    facts drive the wire, as they will once it is migrated -- the connection
    states nothing here, so the migration copies nothing over them."""
    base._fresh(legacy)
    facts.set_stated("openrouter", MODEL, prefill=True, post_process="strict",
                     vision="off")
    assert not store.inference_keys.is_current(store.read_config())
    conn = _primary()
    assert (conn["prefill"], conn["post_process"], conn["vision"]) == (True, "strict", "off")


def test_format_2_post_images_read_the_model_facts(legacy):
    base._fresh(legacy)
    base._config(send_images="on")
    _catalog(vision=False)
    _migrate()
    assert post_images.capability(_primary()) == "no"
    facts.set_stated("openrouter", MODEL, vision="on")
    conn = _primary()
    assert conn["vision"] == "on"
    assert post_images.capability(conn) == "yes"
    assert post_images.images_for(conn) > 0
    assert legacy.get("/api/config").json()["send_images_reach"] == "yes"


def test_vision_off_stops_post_images_but_not_image_descriptions(legacy):
    base._fresh(legacy)
    base._config(send_images="on")
    _catalog(vision=True)
    _migrate()
    assert post_images.capability(_primary()) == "yes"

    facts.set_stated("openrouter", MODEL, vision="off")
    conn = _primary()
    assert conn["vision"] == "off"
    assert post_images.capability(conn) == "no"
    assert post_images.images_for(conn) == 0
    assert legacy.get("/api/config").json()["send_images_reach"] == "no"

    # "off" is the post-image preference, not a capability `no` (ruling 2):
    # the catalog's yes still serves image descriptions.
    usable = routes.common.require_inference("image-description")
    assert usable.attempts[0].capabilities["vision"] == Cap("yes", "catalog")
    assert usable.missing == ()


def test_vision_on_is_a_user_yes(legacy):
    base._fresh(legacy)
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


def test_a_legacy_images_on_is_a_user_yes_until_the_facts_say_otherwise(legacy):
    """A legacy "Images: on" is read into the model's facts in memory at
    format 1 (the planner's facts overlay), where it is the user's `yes` over
    a catalog's vision `no`. Once migrated the connection's flag is frozen
    legacy and says nothing: the model's facts do, and a cleared `vision`
    there is refused like any `no`."""
    base._fresh(legacy)
    store.llm_connections.update_connection("openrouter", vision="on")
    _catalog(vision=False)
    routes.common.require_inference("image-description")  # format 1: the facts, in memory

    _migrate()
    routes.common.require_inference("image-description")  # migrated: a user yes
    facts.set_stated("openrouter", MODEL, vision="")
    assert store.llm_connections.read_connection_raw("openrouter")["vision"] == "on"
    with pytest.raises(HTTPException) as refused:
        routes.common.require_inference("image-description")
    assert refused.value.status_code == 409
    assert refused.value.detail["kind"] == "incapable"


def _by_hand(*, vision_flag: str, source: str) -> ResolvedInference:
    conn = {"id": "openrouter", "kind": "openrouter", "name": "OpenRouter",
            "api_key": "sk-test", "model": MODEL, "vision": vision_flag}
    attempt = Attempt("openrouter", MODEL, "", conn, provider_preset="openrouter",
                      capabilities={"vision": Cap("no", source)})
    return ResolvedInference(
        task="image-description", operation="generate", route="image",
        role="fast", via="role", scope="global",
        attempts=(attempt,), missing=("vision",))


def test_refusal_is_one_pure_decision():
    # A connection's "Images: on" bridges nothing: only the model's facts can
    # outrank the catalog, and they did it before the resolution was built.
    status, detail = inference.refusal(_by_hand(vision_flag="on", source="catalog"))
    assert status == 409 and detail["kind"] == "incapable"
    # The wire protocol's `no` keeps its old body.
    assert inference.refusal(_by_hand(vision_flag="on", source="adapter")) == (
        409, store.image_drafts.UNSUPPORTED)
    # No connection at all is the missing-key refusal.
    empty = ResolvedInference(task="chat", operation="generate", route="scene",
                              role="", via="", scope="none", attempts=())
    assert inference.refusal(empty) == (
        409, {"detail": "No LLM connection selected", "kind": "missing_key"})


def test_a_route_preset_over_a_glm_effort_sends_none_and_is_noted(legacy):
    """Ratification item 3: a route preset that sets no reasoning effort is
    shared with the route's fallback, so it is not derived -- the GLM
    connection's legacy effort stops riding there, at format 1 (in memory) and
    once migrated alike, and the planner notes it rather than dropping it
    silently. The Primary's own slot still carries it, on its derived preset.
    Migrated and not yet retired: once retired the notes are the retirement
    record's (Task 6b), and the planner plans nothing there."""
    ctx = base_c.STATES["glm_max_under_route_preset"](legacy)
    for migrated in (False, True):
        if migrated:
            _migrate_unretired()
        for cid in ("", ctx["cid"]):
            conn = _primary("rolling-summary", cid)
            assert conn["sampling"]["preset_id"] == "cold", migrated
            assert "reasoning_effort" not in llm_sampling.effective(conn)["effective"], \
                migrated
            chat = _primary("chat", cid)
            assert chat["sampling"]["preset_id"] == "warm-reasoning-max", migrated
            assert llm_sampling.effective(chat)["effective"]["reasoning_effort"] == "max"
            noted = {(n.subject, n.provider_id, n.effort, n.kind)
                     for n in inference.retirement_notes(cid)}
            assert noted == {("summary", "glm", "max", "route_preset"),
                             ("scene_break", "glm", "max", "route_preset")}, migrated


def test_an_unreadable_facts_sidecar_lowers_to_the_defaults(legacy):
    """A corrupt `<id>.facts.json` is no facts: resolve and lower keep working,
    every behaviour unstated, and the store's own dict is never mutated."""
    base._fresh(legacy)
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


# ---- slice I: a layout the migration has not reached plays in memory ----
REPLY = 'The tide turns.\n```handoff\n{"next":null}\n```'


def _settings_digest(home: Path) -> dict[str, object]:
    """What a settings write would change: `config.md`, every file under
    `llm_connections/` (the records, their facts and catalogs) and
    `sampler_presets/`, and each campaign's settings -- its frontmatter but
    `updated`, which play stamps on a campaign whatever its settings."""
    out: dict[str, object] = {"config.md": (home / "config.md").read_bytes()}
    for folder in ("llm_connections", "sampler_presets"):
        for path in sorted((home / folder).rglob("*")) if (home / folder).exists() else ():
            if path.is_file():
                out[path.relative_to(home).as_posix()] = path.read_bytes()
    for path in sorted((home / "campaigns").glob("*/campaign.md")):
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        out[path.relative_to(home).as_posix()] = {k: v for k, v in meta.items()
                                                  if k != "updated"}
    return out


def _cast_scene(client) -> tuple[str, str]:
    """Realm, Saltmarch and a scene with Mara in it, through the store and the
    API as `test_format2_play` builds one."""
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    actor = client.post(f"/api/campaigns/{cid}/characters", json={"name": "Mara"})
    assert actor.status_code == 200, actor.text
    cast = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"id": actor.json()["character"]})
    assert cast.status_code == 200, cast.text
    return cid, sid


def _legacy_active(client) -> None:
    """The seeded OpenRouter connection, keyed and at a model, as the active
    connection of a format-1 store."""
    got = client.put("/api/llm-connections/openrouter",
                     json={"api_key": "sk-test-active", "model": MODEL})
    assert got.status_code == 200, got.text
    store.write_config(active_connection_id="openrouter")


def _sent(fake: FakeLLM) -> dict:
    turns = [r for r in fake.requests
             if "You maintain the scene state tracker" not in r["messages"][0]["content"]]
    assert turns, fake.requests
    return turns[-1]["conn"]


def test_a_format_1_store_plays_in_memory_and_writes_nothing(legacy, tmp_path):
    home = store.home()
    _legacy_active(legacy)
    cid, sid = _cast_scene(legacy)
    assert not store.inference_keys.is_current(store.read_config())
    before = _settings_digest(home)

    turn = FakeLLM([[REPLY]])
    legacy.app.dependency_overrides[routes.get_llm] = lambda: turn
    r = legacy.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                    json={"content": "Hi", "speaker_ref": "characters:mara"})

    assert r.status_code == 200, r.text
    assert "error" not in r.text, r.text
    assert (_sent(turn)["id"], _sent(turn)["model"]) == ("openrouter", MODEL)
    assert _settings_digest(home) == before
    assert not store.inference_keys.is_current(store.read_config())


def _unmark(cid: str, **fields: str) -> None:
    """Campaign `cid`'s frontmatter without the format marker, plus `fields`:
    a campaign the migration skipped, or one an older build created."""
    path = store.campaigns.campaign_root(cid) / "campaign.md"
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    meta.pop(store.inference_keys.FORMAT_KEY, None)
    path.write_text(dump_frontmatter({**meta, **fields}, body),  # atomic-ok: test fixture
                    encoding="utf-8")
    assert not store.inference_keys.is_current(store.campaigns.read_campaign(cid)["meta"])


def _spare(client) -> None:
    """A keyed `spare` provider, and the seeded `openrouter` keyed too."""
    got = client.put("/api/llm-connections/openrouter", json={"api_key": "sk-test-active"})
    assert got.status_code == 200, got.text
    got = client.post("/api/llm-connections", json={"kind": "openrouter", "name": "spare",
                                                     "api_key": "sk-test-spare"})
    assert got.status_code == 200 and got.json()["id"] == "spare", got.text


def test_an_unmarked_campaign_resolves_its_overrides_in_memory(client):
    assert store.inference_keys.is_current(store.read_config())
    _spare(client)
    cid, _sid = _cast_scene(client)
    _unmark(cid, route_scene="spare")
    path = store.campaigns.campaign_root(cid) / "campaign.md"
    before = path.read_bytes()

    assert routes.common.require_inference("chat", cid).conn["id"] == "spare"
    assert routes.common.require_inference("chat").conn["id"] == "openrouter"
    # Still unmarked: nothing was written.
    assert not store.inference_keys.is_current(store.campaigns.read_campaign(cid)["meta"])
    assert path.read_bytes() == before


def test_a_busy_unmarked_campaign_still_resolves_and_nothing_is_written(client):
    """I3, inverted: another writer holds the campaign's lock, and the turn
    still resolves its legacy override -- the planner reads, and takes no
    lock, so nothing waits and nothing is written."""
    _spare(client)
    cid, _sid = _cast_scene(client)
    _unmark(cid, route_scene="spare")
    path = store.campaigns.campaign_root(cid) / "campaign.md"
    before = path.read_bytes()
    held, release = threading.Event(), threading.Event()

    def holder() -> None:
        with store.locks.campaign_lock(cid):
            held.set()
            release.wait(10)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert held.wait(10)
        assert routes.common.require_inference("chat", cid).conn["id"] == "spare"
    finally:
        release.set()
        thread.join(10)
    assert path.read_bytes() == before


def test_a_newer_store_still_plays_and_refuses_settings_writes(client):
    """I1: a store a newer build switched resolves best effort, as format 2,
    and its model-settings writes stay refused as `newer_format`."""
    got = client.put("/api/llm-connections/openrouter", json={"api_key": "sk-test-active"})
    assert got.status_code == 200, got.text
    path = store.home() / "config.md"
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    path.write_text(dump_frontmatter({**meta, store.inference_keys.FORMAT_KEY: "3"}, body),
                    encoding="utf-8")  # atomic-ok: test fixture
    assert store.inference_keys.is_newer(store.read_config())

    served = routes.common.require_inference("chat")
    assert (served.conn["id"], served.conn["model"]) == ("openrouter", store.config.DEFAULT_MODEL)
    refused = client.put("/api/inference/settings", json={"roles": {"primary": {
        "selection": {"provider": "openrouter", "model": "vendor/bigger"}}}})
    assert refused.status_code == 409, refused.text
    assert refused.json()["kind"] == "newer_format"


def test_a_format_1_glm_store_still_sends_its_effort(legacy):
    """The legacy GLM effort rides on a derived preset, virtual until
    retirement writes it: the wire is what the legacy connection sent."""
    ctx = base_c.STATES["glm_reasoning"](legacy)
    for cid in ("", ctx["cid"]):
        conn = _primary("chat", cid)
        assert conn["sampling"]["preset_id"] == "warm-reasoning-low"
        assert conn["sampling"]["params"] == {"temperature": 0.9, "reasoning_effort": "low"}
        assert llm_sampling.effective(conn)["effective"] == {
            "temperature": 0.9, "reasoning_effort": "low"}
    assert not (store.home() / "sampler_presets" / "warm-reasoning-low.json").exists()


def _retire_by_hand(cid: str) -> None:
    """The retirement marker on `config.md` and campaign `cid`, as Task 6's
    retirement will write it -- here without its writes, so the connection
    still carries its legacy effort and no derived preset exists."""
    store.write_config(**{store.inference_keys.RETIRED_KEY: "1"})
    path = store.campaigns.campaign_root(cid) / "campaign.md"
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    path.write_text(dump_frontmatter({**meta, store.inference_keys.RETIRED_KEY: "1"}, body),
                    encoding="utf-8")  # atomic-ok: test fixture


def test_a_retired_scope_never_reads_the_legacy_effort(legacy):
    ctx = base_c.STATES["glm_reasoning"](legacy)
    _migrate_unretired()
    _retire_by_hand(ctx["cid"])
    raw = store.llm_connections.read_connection_raw("glm")
    assert raw["reasoning_effort"] == "low"
    for cid in ("", ctx["cid"]):
        conn = _primary("chat", cid)
        assert conn["sampling"]["preset_id"] == "warm"
        assert "reasoning_effort" not in llm_sampling.effective(conn)["effective"]
    assert inference.retirement_notes(ctx["cid"]) == ()


def test_glm_with_no_preset_effort_reports_supported_and_sends_nothing():
    for effort in ("", "high"):
        eff = llm_sampling.effective({"id": "glm", "kind": "openai_compatible",
                                      "model": "glm-5.3", "base_url": base_c.GLM_URL,
                                      "reasoning_effort": effort, "sampling": {"params": {}}})
        assert eff["controls"]["reasoning_effort"]["state"] == "supported"
        assert "reasoning_effort" not in eff["effective"]


def test_overlay_is_free_on_a_retired_store(monkeypatch):
    """A retired `config.md`, with no campaign or a retired one: the overlay is
    the settings as given, and nothing is read -- no connection, no preset."""
    def forbid(*_args, **_kwargs):
        raise AssertionError("a retired store reads nothing")

    monkeypatch.setattr(legacy_plan, "lookup", forbid)
    monkeypatch.setattr(store.sampler_presets, "read_preset", forbid)
    monkeypatch.setattr(store.llm_connections, "list_connections", forbid)
    retired = store.inference_keys.RETIRED_KEY
    cfg = {store.inference_keys.FORMAT_KEY: "2", retired: "1", "active_connection_id": "glm"}
    meta = {store.inference_keys.FORMAT_KEY: "2", retired: "1", "route_scene": "glm"}
    for campaign in ({}, meta):
        seen = legacy_plan.overlay(cfg, campaign, cid="saltmarch")
        assert (seen.cfg, seen.meta) == (cfg, campaign)
        assert (dict(seen.presets), dict(seen.facts), seen.notes) == ({}, {}, ())


@pytest.mark.parametrize(("field", "copied", "default"), [
    ("prefill", True, False), ("vision", "off", ""), ("post_process", "strict", "none")])
def test_a_format_1_store_reads_its_facts_as_the_migration_will_leave_them(
        legacy, field, copied, default):
    """Fix round 1, I1: an interrupted migration copied a legacy field into
    the model's facts, then the user set the field back to its default at
    format 1. The migration's step 3 takes that copy back
    (`facts.adopt_legacy`); play reads it the same way in memory, so the stale
    copy is never sent -- and what plays now is what plays once migrated."""
    base._fresh(legacy)
    store.llm_connections.update_connection("openrouter", **{field: copied})
    raw = store.llm_connections.read_connection_raw("openrouter")
    facts.adopt_legacy("openrouter", facts.model_of(raw), legacy_plan.stated(raw))
    assert facts.of("openrouter", MODEL, raw["rev"])[field] == copied   # the copy, on disk
    store.llm_connections.update_connection("openrouter", **{field: default})
    assert not store.inference_keys.is_current(store.read_config())

    in_memory = _primary()[field]
    _migrate()
    assert in_memory == _primary()[field] == default


def test_a_glm_role_saved_back_unchanged_is_accepted_and_sends_the_same(legacy):
    """Fix round 1, I2: on a migrated, unretired GLM store the Models page
    shows each role as STORED -- the base preset the migration persisted, a
    preset file -- and the derived reasoning preset only as what it resolves
    to. Sending the stored selection back unchanged is a 200, and the wire
    does not move."""
    base_c.STATES["glm_reasoning"](legacy)
    _migrate_unretired()
    before = llm_sampling.effective(_primary())["effective"]
    assert before == {"temperature": 0.9, "reasoning_effort": "low"}

    view = legacy.get("/api/inference/settings").json()
    card = view["roles"]["primary"]
    assert card["stored"]["preset"] == "warm"
    assert card["stored"]["preset"] in {p["id"] for p in view["presets"]}
    assert card["resolves"]["preset"] == "warm-reasoning-low"
    saved = legacy.put("/api/inference/settings",
                       json={"roles": {"primary": {"selection": card["stored"]}}})
    assert saved.status_code == 200, saved.text
    assert llm_sampling.effective(_primary())["effective"] == before
    assert not (store.home() / "sampler_presets" / "warm-reasoning-low.json").exists()
