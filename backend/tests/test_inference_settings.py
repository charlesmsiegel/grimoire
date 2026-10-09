"""Reading and writing roles and routes (slice C, Task 4).

`store.inference.settings.view` is what the Models screen and the campaign
Inspector render: each role card and each route row with what this scope
stores, what it `resolves` to (the seam's own resolution) and what it
`inherits` (the same with this scope's choice silenced), and the seam's own
refusal as `problem`. `settings.write` is the one door for a new-layout write,
behind `PUT /api/inference/settings` and `PUT /api/campaigns/{cid}/inference`.

Invented connection ids and the codebase's placeholder names only.
"""

from __future__ import annotations

import threading

import pytest
from fastapi import HTTPException

import grimoire.store as store
from grimoire import routes
from grimoire.store import embed_space, locks, revision, routing
from grimoire.store import inference_keys as keys
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
from grimoire.store.inference import migrate, settings
from grimoire.store.inference import resolve as inference

from . import inference_baseline as base
from . import inference_baseline_c as base_c
from . import inference_fixtures as fx

WAIT = 10
CLEAR = store.sampler_presets.PRESET_CLEAR


@pytest.fixture
def client(tmp_path):
    with base.client_at(tmp_path) as c:
        yield c


def _migrate() -> None:
    got = migrate.ensure()
    assert got.state == "done", got
    assert keys.is_current(store.read_config())


def _fresh(client) -> str:
    """A migrated store: the seeded openrouter connection keyed at
    `vendor/active`, a `spare` connection, two presets and one campaign."""
    cid = base._fresh(client)["cid"]
    base._spare(client)
    base._presets()
    _migrate()
    return cid


def _global(client) -> dict:
    got = client.get("/api/inference/settings")
    assert got.status_code == 200, got.text
    return got.json()


def _campaign(client, cid: str) -> dict:
    got = client.get(f"/api/campaigns/{cid}/inference")
    assert got.status_code == 200, got.text
    return got.json()


def _put(client, body: dict, cid: str = ""):
    url = f"/api/campaigns/{cid}/inference" if cid else "/api/inference/settings"
    return client.put(url, json=body)


def _ok(client, body: dict, cid: str = "") -> dict:
    got = _put(client, body, cid)
    assert got.status_code == 200, got.text
    return got.json()


def _row(view: dict, key: str) -> dict:
    return next(r for r in view["routes"] if r["key"] == key)


def _pair(sel: dict | None) -> tuple[str, str] | None:
    return None if sel is None else (sel["provider"], sel["model"])


def _meta(cid: str) -> dict:
    return store.campaigns.read_campaign(cid)["meta"]


# ---- the view agrees with the seam ----
def _seam(task: str, cid: str) -> tuple[dict | None, str | None]:
    """`(selection, problem)` as `require_inference` answers `task`."""
    try:
        got = routes.common.require_inference(task, cid)
    except HTTPException as exc:
        detail = exc.detail
        return None, detail["detail"] if isinstance(detail, dict) else detail
    first = got.attempts[0]
    return {"provider": first.provider_id, "model": first.model,
            "preset": first.preset_id}, None


def _resolution(task: str, cid: str) -> dict | None:
    """What the resolver serves `task`, refused or not."""
    resolved = inference.resolve(task, cid)
    if not resolved.attempts:
        return None
    first = resolved.attempts[0]
    return {"provider": first.provider_id, "model": first.model, "preset": first.preset_id,
            "via": resolved.via, "scope": resolved.scope}


def _cut(sel: dict | None, parts=("provider", "model", "preset", "via", "scope")):
    return None if sel is None else {k: sel[k] for k in parts}


STATES = {**{f"a:{k}": v for k, v in base.STATES.items()},
          **{f"c:{k}": v for k, v in base_c.STATES.items()}}


@pytest.mark.parametrize("state", sorted(STATES))
def test_settings_view_matches_every_resolution(state, client):
    """Review Focus 1: a migrated legacy store opens `/models` and sees exactly
    what plays. Every task of every row resolves where the row says, and the
    row's `problem` is the seam's own refusal."""
    cid = STATES[state](client)["cid"]
    name = state.split(":", 1)[1]
    if state.startswith("a:") and name in base.UNREADABLE:
        assert base.migrate_state(name).state == "done"
    _migrate()
    for scope_cid, got in (("", _global(client)), (cid, _campaign(client, cid))):
        expected_routes = [r.key for r in routing.ROUTES
                           if not scope_cid or r.campaign_scoped]
        assert [r["key"] for r in got["routes"]] == expected_routes
        for row in got["routes"]:
            for task in row["tasks"]:
                assert _cut(row["resolves"]) == _resolution(task, scope_cid), (state, task)
                seam, problem = _seam(task, scope_cid)
                assert row["problem"] == problem, (state, task)
                if seam is not None:
                    assert _cut(row["resolves"], ("provider", "model", "preset")) == seam
        # The Primary card is what a task no route claims runs on.
        primary = got["roles"]["primary"]
        assert _cut(primary["resolves"]) == _resolution("", scope_cid), state
        assert primary["problem"] == _seam("", scope_cid)[1], state
    embedding = _global(client)["roles"]["embedding"]
    assert embedding["on"] == (embed_space.resolve() is not None), state


def test_the_view_says_where_the_store_stands(client):
    base._fresh(client)
    got = _global(client)
    assert got["format"] == "1" and got["newer"] is False
    assert got["migration"]["state"] == "pending"
    _migrate()
    got = _global(client)
    assert got["format"] == "2" and got["migration"]["state"] == "done"
    assert got["preset_clear"] == CLEAR
    assert {p["id"] for p in got["providers"]} >= {"openrouter", "claude"}
    assert next(p for p in got["providers"] if p["id"] == "openrouter")["usable"] is True


def test_each_provider_names_its_own_model_for_a_format_1_reroll(client):
    """At format 1 a reroll naming a provider alone runs that provider's OWN
    model (`resolve._overridden`); the view names it, so the reroll box can
    say which model that is before it is sent."""
    base._fresh(client)
    got = _global(client)
    assert got["format"] == "1"
    mine = {p["id"]: p["own_model"] for p in got["providers"]}
    assert mine["openrouter"] == store.llm_connections.read_connection_raw("openrouter")["model"]
    assert mine["openrouter"] == inference.resolve(
        "regenerate", override=inference.Selection("openrouter", "", "")).attempts[0].model


def test_inherits_silences_this_scopes_own_choice(client):
    cid = _fresh(client)
    got = _ok(client, {"routes": {"scene": {"use": "model", "pin": {
        "provider": "spare", "model": "vendor/spare-pin"}}}})
    scene = _row(got, "scene")
    assert _pair(scene["resolves"]) == ("spare", "vendor/spare-pin")
    assert scene["resolves"]["via"] == "route" and scene["role"] is None
    # Cleared, the row would run on the Primary role.
    assert _pair(scene["inherits"]) == ("openrouter", "vendor/active")
    assert scene["inherits"]["via"] == "role"
    # A row that names nothing inherits what it resolves to.
    opener = _row(got, "opener")
    assert opener["inherits"] == opener["resolves"]
    # At the campaign, its own Primary outranks the global pin (spec 5.1: the
    # campaign role before the global route) -- and is silenced from the
    # Primary card's `inherits` only; the scene row names nothing here.
    got = _ok(client, {"roles": {"primary": {"selection": {
        "provider": "spare", "model": "vendor/campaign"}}}}, cid)
    scene = _row(got, "scene")
    assert _pair(scene["resolves"]) == ("spare", "vendor/campaign")
    assert scene["inherits"] == scene["resolves"]
    assert _pair(got["roles"]["primary"]["resolves"]) == ("spare", "vendor/campaign")
    assert got["roles"]["primary"]["resolves"]["scope"] == "campaign"
    assert _pair(got["roles"]["primary"]["inherits"]) == ("openrouter", "vendor/active")
    # A campaign pin is the campaign row's own choice, and silenced from it.
    got = _ok(client, {"routes": {"opener": {"use": "model", "pin": {
        "provider": "openrouter", "model": "vendor/opener"}}}}, cid)
    assert _pair(_row(got, "opener")["resolves"]) == ("openrouter", "vendor/opener")
    assert _row(got, "opener")["resolves"]["scope"] == "campaign"
    assert _pair(_row(got, "opener")["inherits"]) == ("spare", "vendor/campaign")


def test_a_legacy_route_is_silenced_as_the_pin_it_reads_as(client):
    """Before the migration the view still reads through the translation, and
    a legacy `route_<k>` is a row's own choice like any pin."""
    base._fresh(client)
    base._spare(client)
    base._config(route_dossier="spare")
    dossier = _row(_global(client), "dossier")
    assert dossier["use"] == keys.PIN and dossier["pin"]["provider"] == "spare"
    assert _pair(dossier["resolves"]) == ("spare", "vendor/spare")
    assert _pair(dossier["inherits"]) == ("openrouter", "vendor/active")


def test_unset_fast_and_decision_inherit(client):
    _fresh(client)
    got = _global(client)
    for role in ("fast", "decision"):
        card = got["roles"][role]
        assert card["stored"] == {"provider": "", "model": "", "preset": ""}
        assert _pair(card["resolves"]) == ("openrouter", "vendor/active")
        assert _pair(card["inherits"]) == ("openrouter", "vendor/active")
        assert card["problem"] is None
    got = _ok(client, {"roles": {"fast": {"selection": {"provider": "spare",
                                                        "model": "vendor/fast"}}}})
    assert _pair(got["roles"]["fast"]["resolves"]) == ("spare", "vendor/fast")
    assert _pair(got["roles"]["fast"]["inherits"]) == ("openrouter", "vendor/active")
    assert _pair(got["roles"]["decision"]["resolves"]) == ("spare", "vendor/fast")
    # A Fast-default route follows it.
    assert _pair(_row(got, "absorb")["resolves"]) == ("spare", "vendor/fast")
    assert _row(got, "absorb")["role"] == "fast"


def test_a_route_row_names_the_role_it_walks(client):
    """CODE-M3: `uses` is the role a route walks (spec 5.1) -- what the
    Decision card lists -- where `role` is the one that SUPPLIED the
    selection. With Decision unset the decide routes still use it, while
    Fast (and, unset too, Primary) supplies them; a pin uses none."""
    cid = _fresh(client)
    got = _global(client)
    deciding = {r.key for r in routing.ROUTES if r.default_role == "decision"}
    assert deciding == {"speaker", "scene_break", "voice_drift", "continuity"}
    assert {r["key"] for r in got["routes"] if r["uses"] == "decision"} == deciding
    assert {_row(got, k)["role"] for k in deciding} == {"primary"}
    assert _row(got, "absorb")["uses"] == "fast"
    got = _ok(client, {"routes": {"absorb": {"use": "decision"},
                                  "scene_break": {"use": "model", "pin": {
                                      "provider": "spare", "model": "vendor/pin"}}}})
    assert _row(got, "absorb")["uses"] == "decision"
    assert _row(got, "scene_break")["uses"] is None
    # At the campaign the global pin answers a row that names nothing...
    campaign = client.get(f"/api/campaigns/{cid}/inference").json()
    assert _row(campaign, "scene_break")["uses"] is None
    # ...until the campaign sets the role the route uses, which outranks it
    # (spec 5.1 step 3).
    got = _ok(client, {"roles": {"decision": {"selection": {
        "provider": "spare", "model": "vendor/decide"}}}}, cid)
    assert _row(got, "scene_break")["uses"] == "decision"
    assert _row(got, "scene_break")["role"] == "decision"


def test_a_role_without_a_provider_reports_the_seams_problem(client):
    _fresh(client)
    _ok(client, {"roles": {"primary": {"selection": {"provider": ""}}}})
    card = _global(client)["roles"]["primary"]
    assert card["resolves"] is None
    assert card["problem"] == "No LLM connection selected"


def test_a_role_write_replaces_only_the_named_role(client):
    """Review Focus 4: two tabs, each writing one role."""
    _fresh(client)
    _ok(client, {"roles": {
        "primary": {"selection": {"provider": "openrouter", "model": "vendor/a",
                                  "preset": "warm"},
                    "fallback": {"provider": "spare", "model": "vendor/fb"}},
        "fast": {"selection": {"provider": "spare", "model": "vendor/b", "preset": "cold"}}}})
    # The second tab names Fast's selection only, without a preset: the
    # selection is replaced whole, so the blank clears.
    got = _ok(client, {"roles": {"fast": {"selection": {"provider": "openrouter",
                                                        "model": "vendor/c"}}}})
    assert got["roles"]["fast"]["stored"] == {"provider": "openrouter", "model": "vendor/c",
                                              "preset": ""}
    assert got["roles"]["primary"]["stored"] == {"provider": "openrouter",
                                                 "model": "vendor/a", "preset": "warm"}
    assert got["roles"]["primary"]["fallback"] == {"provider": "spare", "model": "vendor/fb",
                                                   "preset": ""}
    # A fallback alone leaves its selection where it was.
    got = _ok(client, {"roles": {"primary": {"fallback": {"provider": ""}}}})
    assert got["roles"]["primary"]["fallback"]["provider"] == ""
    assert got["roles"]["primary"]["stored"]["model"] == "vendor/a"


def test_a_generative_selection_names_a_model_with_its_provider(client):
    """A provider has no model of its own: a selection, fallback or pin naming
    one alone would send a blank model id, so it is refused before anything
    is written -- at either scope. Embedding's provider alone is "off"."""
    cid = _fresh(client)
    for body in ({"roles": {"fast": {"selection": {"provider": "spare"}}}},
                 {"roles": {"primary": {"fallback": {"provider": "spare", "preset": "warm"}}}},
                 {"routes": {"absorb": {"use": "model", "pin": {"provider": "spare"}}}}):
        for where in ("", cid):
            got = _put(client, body, where)
            assert got.status_code == 400, (body, where, got.text)
            assert "choose a model for spare" in got.json()["detail"]
    assert keys.role_key("fast", "provider") not in _meta(cid)
    assert store.read_config().get(keys.role_key("fast", "provider"), "") == ""
    # Clearing the selection (no provider, no model) is not this refusal.
    _ok(client, {"roles": {"fast": {"selection": {"provider": "", "model": ""}}}})


@pytest.mark.parametrize("brk", ["\u2028", "\u2029", "\x85", "\x0b", "\x1c", "\n"],
                         ids=lambda c: f"U+{ord(c):04X}")
def test_a_value_holding_a_line_break_is_refused(client, brk):
    """Every value here is one frontmatter line, and the parser splits on any
    boundary `str.splitlines` knows -- U+2028 and U+2029 are not controls, so
    a control-character check alone let them through, and the rest of the
    line came back as a key. Refused before anything is written."""
    cid = _fresh(client)
    sneaky = f"vendor/m{brk}inference_format: 3"
    for where in ("", cid):
        got = _put(client, {"roles": {"fast": {"selection": {
            "provider": "spare", "model": sneaky}}}}, where)
        assert got.status_code == 400, got.text
        assert "one line of text" in got.json()["detail"]
    assert keys.is_current(store.read_config())
    assert store.read_config().get(keys.role_key("fast", "model"), "") == ""
    assert keys.role_key("fast", "model") not in _meta(cid)


def test_route_writes_name_their_parts(client):
    _fresh(client)
    got = _ok(client, {"routes": {"absorb": {"use": "primary", "preset": "warm"}},
                       "presets": {"dossier": CLEAR}})
    absorb = _row(got, "absorb")
    assert absorb["use"] == "primary" and absorb["preset"] == "warm"
    assert absorb["resolves"]["preset"] == "warm"
    assert absorb["role"] == "primary"
    assert _row(got, "dossier")["preset"] == CLEAR
    assert _row(got, "dossier")["resolves"]["preset"] == ""
    got = _ok(client, {"routes": {"absorb": {"use": ""}}})
    assert _row(got, "absorb")["use"] == "" and _row(got, "absorb")["preset"] == "warm"


@pytest.mark.parametrize("body", [
    {"roles": {"primary": {"selection": {"provider": "nosuch"}}}},
    {"roles": {"primary": {"selection": {"provider": "spare", "preset": "nosuch"}}}},
    {"roles": {"sidekick": {"selection": {"provider": "spare"}}}},
    {"roles": {"primary": {"selection": {"provider": "spare", "colour": "red"}}}},
    {"roles": {"primary": {"selection": {"provider": 3}}}},
    {"roles": {"embedding": {"fallback": {"provider": "spare"}}}},
    {"routes": {"nosuch": {"use": "fast"}}},
    {"routes": {"scene": {"use": "sideways"}}},
    {"routes": {"scene": {"pin": {"provider": "nosuch"}}}},
    {"presets": {"scene": "nosuch"}},
    {"routes": {"scene": {"preset": "warm"}}, "presets": {"scene": "cold"}},
    {"active_connection_id": "spare"},
    {"route_scene": "spare"},
    {"role_primary_provider": "spare"},
    {"roles": ["primary"]},
])
def test_a_bad_write_is_refused_and_writes_nothing(client, body):
    _fresh(client)
    before = (store.home() / "config.md").read_text(encoding="utf-8")
    got = _put(client, body)
    assert got.status_code == 400, got.text
    assert (store.home() / "config.md").read_text(encoding="utf-8") == before


def test_use_embedding_is_refused(client):
    cid = _fresh(client)
    for where in ("", cid):
        got = _put(client, {"routes": {"scene": {"use": "embedding"}}}, where)
        assert got.status_code == 400, got.text
        assert "Embedding" in got.json()["detail"]
    assert store.read_config().get(keys.use_key("scene"), "") == ""
    assert keys.use_key("scene") not in _meta(cid)


def test_campaign_cannot_set_embedding_or_global_routes(client):
    cid = _fresh(client)
    before = _meta(cid)
    for body in ({"roles": {"embedding": {"selection": {"provider": "spare",
                                                        "model": "embed"}}}},
                 {"routes": {"tagline": {"use": "primary"}}},
                 {"presets": {"scenario": "warm"}}):
        got = _put(client, body, cid)
        assert got.status_code == 400, (body, got.text)
    assert _meta(cid) == before
    view = _campaign(client, cid)
    assert "embedding" not in view["roles"]
    assert {r["key"] for r in view["routes"]} == {r.key for r in routing.ROUTES
                                                   if r.campaign_scoped}


def test_an_embedding_preset_is_refused(client):
    _fresh(client)
    got = _put(client, {"roles": {"embedding": {"selection": {
        "provider": "spare", "model": "embed", "preset": "warm"}}}})
    assert got.status_code == 400, got.text
    assert store.read_config().get(keys.role_key("embedding", "provider"), "") == ""


EMBED = {"roles": {"embedding": {"selection": {"provider": "spare",
                                               "model": "vendor/embed-small"}}}}


def test_an_embedding_change_needs_confirm(client):
    """Review Focus 5: a change that may cost money is never made unasked."""
    _fresh(client)
    got = _put(client, EMBED)
    assert got.status_code == 400, got.text
    assert got.json() == {
        "kind": "confirm_embedding",
        "detail": "Changing the embedding model re-embeds your library through spare, "
                  "which may cost money — confirm to change it."}
    assert store.read_config().get(keys.role_key("embedding", "provider"), "") == ""
    # `confirm_embedding: false` is the same as leaving it out.
    assert _put(client, {**EMBED, "confirm_embedding": False}).status_code == 400
    got = _ok(client, {**EMBED, "confirm_embedding": True})
    assert got["roles"]["embedding"]["stored"] == {"provider": "spare",
                                                   "model": "vendor/embed-small"}
    assert _pair(got["roles"]["embedding"]["resolves"]) == ("spare", "vendor/embed-small")
    # Rewriting the same value changes nothing, and asks nothing.
    _ok(client, EMBED)
    # A different model is a change again.
    other = {"roles": {"embedding": {"selection": {"provider": "spare",
                                                   "model": "vendor/embed-large"}}}}
    assert _put(client, other).status_code == 400
    assert _put(client, {**other, "confirm_embedding": "yes"}).status_code == 400


def test_clearing_embedding_needs_no_confirm(client):
    _fresh(client)
    _ok(client, {**EMBED, "confirm_embedding": True})
    got = _ok(client, {"roles": {"embedding": {"selection": {}}}})
    assert got["roles"]["embedding"]["stored"] == {"provider": "", "model": ""}
    assert got["roles"]["embedding"]["resolves"] is None
    assert got["roles"]["embedding"]["on"] is False


def test_clearing_one_part_of_embedding_needs_no_confirm(client):
    """Clearing the model alone switches embeddings off; it re-embeds nothing,
    so it asks nothing -- and the card does not claim a resolution it lacks."""
    _fresh(client)
    _ok(client, {**EMBED, "confirm_embedding": True})
    got = _ok(client, {"roles": {"embedding": {"selection": {"provider": "spare"}}}})
    card = got["roles"]["embedding"]
    assert card["stored"] == {"provider": "spare", "model": ""}
    assert card["on"] is False and card["resolves"] is None


def test_a_dropped_fallback_is_reported_on_its_rows(client):
    """Spec 5.3: a fallback known unable to do what the route needs is never
    sent. The view says so where it happens -- the role card (for a task no
    route claims) and each route row -- so the screen can show it."""
    _fresh(client)
    _ok(client, {"roles": {"primary": {"fallback": {"provider": "spare",
                                                    "model": "vendor/spare"}}}})
    rev = store.llm_connections.read_connection_raw("spare")["rev"]
    store.llm_connections.set_cached_models(
        "spare", [{"id": "vendor/spare", "vision": False}], rev)
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    store.llm_connections.set_cached_models(
        "openrouter", [{"id": "vendor/active", "vision": True}], rev)

    got = _global(client)

    assert _row(got, "image")["fallback_missing"] == ["vision"]
    assert _row(got, "summary")["fallback_missing"] == []
    assert got["roles"]["primary"]["fallback_missing"] == []
    assert inference.resolve("image-description").fallback_missing == ("vision",)
    # A campaign's view carries it too.
    cid = store.campaigns.list_campaigns()[0]["id"]
    assert _row(_campaign(client, cid), "image")["fallback_missing"] == ["vision"]


def test_a_fallback_that_cannot_send_says_why_on_its_rows(client):
    """A fallback with no key is left out of the chain as silently as one known
    incapable: the view carries the seam's own reason, so the screen can say
    the stored fallback is never tried."""
    cid = _fresh(client)
    _ok(client, {"roles": {"primary": {"fallback": {"provider": "spare",
                                                    "model": "vendor/spare"}}}})
    assert _global(client)["roles"]["primary"]["fallback_problem"] is None
    bare = store.llm_connections.create_connection("openrouter", "Saltmarch Bare")
    _ok(client, {"roles": {"primary": {"fallback": {"provider": bare,
                                                    "model": "vendor/spare"}}}})

    got = _global(client)

    assert got["roles"]["primary"]["fallback_problem"] == "OpenRouter key not set"
    assert got["roles"]["primary"]["fallback_missing"] == []
    assert _row(got, "scene")["fallback_problem"] == "OpenRouter key not set"
    assert inference.resolve("chat").fallback_problem == "OpenRouter key not set"
    assert inference.resolve("chat").fallback is None
    assert _row(_campaign(client, cid), "scene")["fallback_problem"] == "OpenRouter key not set"


def test_a_fallback_on_the_primarys_own_provider_says_why_on_its_rows(client):
    """A fallback on the primary's own provider -- another model there -- is
    left out of the chain (a second try on the connection that just failed is
    not a fallback). It was left out silently, so the view showed it as one
    that works; it carries the reason now, as one that cannot send does."""
    cid = _fresh(client)
    primary = _global(client)["roles"]["primary"]["resolves"]["provider"]
    _ok(client, {"roles": {"primary": {"fallback": {"provider": primary,
                                                    "model": "vendor/other"}}}})

    got = _global(client)

    assert got["roles"]["primary"]["fallback_problem"] == inference.SAME_PROVIDER
    assert _row(got, "scene")["fallback_problem"] == inference.SAME_PROVIDER
    assert inference.resolve("chat").fallback is None
    assert _row(_campaign(client, cid), "scene")["fallback_problem"] == inference.SAME_PROVIDER


# ---- the Decision card reads its role as a decision (slice H) ----
def _resolution_of(resolved) -> dict:
    first = resolved.attempts[0]
    return {"provider": first.provider_id, "model": first.model, "preset": first.preset_id,
            "via": resolved.via, "scope": resolved.scope}


def test_the_decision_card_resolves_as_a_decision(client):
    """Spec 12, one decision: the Decision card resolves its role as the
    decide routes it serves do, so a decide-only model with a same-provider
    fallback reads on the card exactly as on those rows -- no problem
    (ruling 2), and the fallback a stage of its own rather than a retry. Read
    as a generation it would be refused ("cannot generate text") and its
    fallback dropped as one on the primary's own provider."""
    fx.decide_only(client, fallback=True, on=fx.SAME_PROVIDER)
    got = _global(client)
    card, row = got["roles"]["decision"], _row(got, "scene_break")
    decided = inference.resolve("", role="decision", operation="decide")
    assert _cut(card["resolves"]) == _resolution_of(decided)
    assert card["problem"] is None and row["problem"] is None
    assert card["fallback_problem"] is None and row["fallback_problem"] is None
    assert card["fallback_missing"] == row["fallback_missing"] == []
    # The generate reading the card used to make says otherwise.
    generated = inference.resolve("", role="decision")
    assert generated.fallback_problem == inference.SAME_PROVIDER
    assert inference.refusal(generated)[1]["kind"] == "incapable"
    # Every other role still reads as a generation.
    assert got["roles"]["primary"]["fallback_problem"] is None
    assert got["roles"]["primary"]["problem"] is None


def test_the_settings_view_carries_decision_mode(client):
    """I9: the Decision card and each decide route row say which backend
    answers -- `native`, `structured`, or `""` on a model that can do neither
    (refused) -- read off the resolution, so the Models page keeps no
    capability rule of its own. Every other card and row says `""`."""
    deciding = {r.key for r in routing.ROUTES if r.operation == "decide"}
    fx.format2(client)
    got = _global(client)
    assert got["roles"]["decision"]["decision_mode"] == "structured"
    assert {_row(got, k)["decision_mode"] for k in deciding} == {"structured"}
    assert {r["decision_mode"] for r in got["routes"] if r["key"] not in deciding} == {""}
    assert {got["roles"][r]["decision_mode"] for r in ("primary", "fast")} == {""}

    fx.decide_only(client, fallback=False)
    got = _global(client)
    assert got["roles"]["decision"]["decision_mode"] == "native"
    assert {_row(got, k)["decision_mode"] for k in deciding} == {"native"}

    fx.neither(client)
    got = _global(client)
    card = got["roles"]["decision"]
    assert card["decision_mode"] == ""
    assert "cannot generate text or make native decisions" in card["problem"]
    assert {_row(got, k)["decision_mode"] for k in deciding} == {""}


def test_the_settings_view_says_whether_the_model_decides_natively(client):
    """I9: `decides_natively` is the resolved primary's own `decide_native`
    -- the capabilities the resolver decided on -- so the page words a
    structured decision without a second read that could disagree."""
    deciding = {r.key for r in routing.ROUTES if r.operation == "decide"}
    fx.format2(client)
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    # Generates, and its catalog says it decides natively too: structured
    # (ruling 1), on a model that could also decide natively.
    store.llm_connections.set_cached_models(
        "openrouter", [{"id": "vendor/active", "outputs": ["text", "decisions"]}], rev)
    got = _global(client)
    card = got["roles"]["decision"]
    assert (card["decision_mode"], card["decides_natively"]) == ("structured", True)
    assert {(_row(got, k)["decision_mode"], _row(got, k)["decides_natively"])
            for k in deciding} == {("structured", True)}

    # The same model with no native API: cleared on the card and every row.
    store.llm_connections.set_cached_models(
        "openrouter", [{"id": "vendor/active", "outputs": ["text"]}], rev)
    got = _global(client)
    assert got["roles"]["decision"]["decides_natively"] is False
    assert {_row(got, k)["decides_natively"] for k in deciding} == {False}

    # A model the user says cannot decide natively (and that cannot generate).
    fx.neither(client)
    got = _global(client)
    assert got["roles"]["decision"]["decides_natively"] is False


def _deleted_after_validation(monkeypatch, delete) -> None:
    """`settings._validated` as it is, then `delete()` -- another tab's delete
    landing between the body's check and the write."""
    real = settings._validated

    def validated_then_deleted(scope, body):
        plan = real(scope, body)
        delete()
        return plan

    monkeypatch.setattr(settings, "_validated", validated_then_deleted)


def test_a_provider_deleted_during_the_write_is_not_written(client, monkeypatch):
    _fresh(client)
    doomed = store.llm_connections.create_connection(
        "openrouter", "Winifred Router", api_key="sk-w")
    _deleted_after_validation(
        monkeypatch, lambda: store.llm_connections.delete_connection(doomed))

    got = _put(client, {"roles": {"fast": {"selection": {"provider": doomed,
                                                         "model": "vendor/small"}}}})

    assert got.status_code == 400, got.text
    assert doomed in got.text
    assert not store.read_config().get(keys.role_key("fast", "provider"))


def test_a_preset_deleted_during_the_write_is_not_written(client, monkeypatch):
    _fresh(client)
    _deleted_after_validation(
        monkeypatch, lambda: store.sampler_presets.delete_preset("warm"))

    got = _put(client, {"presets": {"summary": "warm"}})

    assert got.status_code == 400, got.text
    assert "warm" in got.text
    assert not store.read_config().get(keys.preset_key("summary"))


def test_a_provider_or_preset_deleted_during_a_campaign_write_is_not_written(
        client, monkeypatch):
    """The campaign write rechecks what it names in its hold, as the global
    write does: a delete landing after the body was checked would otherwise
    leave the campaign naming nothing, answered 200."""
    cid = _fresh(client)
    doomed = store.llm_connections.create_connection(
        "openrouter", "Winifred Router", api_key="sk-w")
    _deleted_after_validation(
        monkeypatch, lambda: store.llm_connections.delete_connection(doomed))
    got = _put(client, {"roles": {"fast": {"selection": {"provider": doomed,
                                                         "model": "vendor/small"}}}}, cid)
    assert got.status_code == 400, got.text
    assert doomed in got.text
    assert keys.role_key("fast", "provider") not in _meta(cid)

    monkeypatch.undo()
    _deleted_after_validation(
        monkeypatch, lambda: store.sampler_presets.delete_preset("warm"))
    got = _put(client, {"presets": {"summary": "warm"}}, cid)
    assert got.status_code == 400, got.text
    assert "warm" in got.text
    assert keys.preset_key("summary") not in _meta(cid)


def test_a_model_id_is_bounded_and_single_line(client):
    """A model id lands in every turn's usage row and prompt-log index, as the
    reroll override's does, so it takes the same bound; and a control
    character in any id would be written into `config.md` unescaped."""
    _fresh(client)
    long = "x" * (store.alternates.MAX_MODEL_CHARS + 1)
    for body in (
        {"roles": {"primary": {"selection": {"provider": "spare", "model": long}}}},
        {"roles": {"primary": {"fallback": {"provider": "spare", "model": long}}}},
        {"routes": {"summary": {"use": "model", "pin": {"provider": "spare",
                                                         "model": long}}}},
        {"roles": {"primary": {"selection": {"provider": "spare",
                                             "model": "vendor/m\nrole_fast_provider: x"}}}},
        {"roles": {"fast": {"selection": {"provider": "spare\x00"}}}},
        {"roles": {"embedding": {"selection": {"provider": "spare",
                                               "model": "embed\rsmall"}}},
         "confirm_embedding": True},
    ):
        got = _put(client, body)
        assert got.status_code == 400, (body, got.text)
    cfg = store.read_config()
    assert cfg[keys.role_key("primary", "model")] != long
    assert not cfg.get(keys.role_key("fast", "provider"))
    # The bound itself is accepted.
    _ok(client, {"roles": {"primary": {"selection": {
        "provider": "spare", "model": "x" * store.alternates.MAX_MODEL_CHARS}}}})


def test_a_missing_key_names_the_provider_and_the_role(client):
    """Spec 12: `missing_key` names the provider and the route or role. Two
    providers of one kind would otherwise both read "OpenRouter key not set"."""
    _fresh(client)
    mara = store.llm_connections.create_connection("openrouter", "Mara Router")
    winifred = store.llm_connections.create_connection("openrouter", "Winifred Router")
    _ok(client, {"roles": {"fast": {"selection": {"provider": winifred,
                                                  "model": "vendor/small"}}}})

    _, problem = _seam("rolling-summary", "")
    assert problem == "OpenRouter key not set (Winifred Router, the Fast role)"
    got = _global(client)
    assert got["roles"]["fast"]["problem"] == problem
    assert _row(got, "summary")["problem"] == problem

    # Fast cleared: it is Primary's selection, so Primary is the role named.
    _ok(client, {"roles": {"fast": {"selection": {}},
                           "primary": {"selection": {"provider": mara,
                                                     "model": "vendor/big"}}}})
    assert _seam("rolling-summary", "")[1] == (
        "OpenRouter key not set (Mara Router, the Primary role)")
    assert _seam("chat", "")[1] == "OpenRouter key not set (Mara Router, the Primary role)"

    # A pinned split route is named for itself, not its legacy parent.
    _ok(client, {"routes": {"speaker": {"use": "model", "pin": {
        "provider": winifred, "model": "vendor/small"}}}})
    assert _seam("response-selector", "")[1] == (
        "OpenRouter key not set (Winifred Router, routed for next speaker)")


def _embedding_problem(client, provider: str, model: str) -> dict:
    store.write_config(**{keys.role_key("embedding", "provider"): provider,
                          keys.role_key("embedding", "model"): model})
    return _global(client)["roles"]["embedding"]


def test_the_embedding_card_says_why_it_is_off(client):
    """Each other card carries the seam's problem; the Embedding card says why
    it embeds nothing, and nothing when it embeds."""
    _fresh(client)
    store.llm_connections.create_connection("claude", "Mara Claude")
    store.llm_connections.create_connection("openai_compatible", "Winifred Local",
                                            base_url="")
    store.llm_connections.create_connection("openrouter", "Seraphine Router")
    cases = {
        ("", ""): "No provider chosen",
        ("", "embed-small"): "No provider chosen",
        ("spare", ""): "No model chosen",
        ("seraphine-router", "vendor/embed-small"): "Seraphine Router has no key set",
        ("mara-claude", "embed-small"): "Mara Claude cannot embed",
        ("winifred-local", "embed-small"): "Winifred Local has no address set",
        ("gone", "embed-small"): "The chosen provider no longer exists",
    }
    for (provider, model), why in cases.items():
        card = _embedding_problem(client, provider, model)
        assert card["on"] is False, (provider, model)
        assert card["problem"] == why, (provider, model)

    card = _embedding_problem(client, "spare", "vendor/embed-small")
    assert card["on"] is True and card["problem"] is None


def test_a_provider_known_not_to_embed_turns_the_card_off(client):
    """A z.ai provider's preset NEVER embeds: an endpoint is not a space, so
    the role resolves to none and the card says why."""
    _fresh(client)
    zai = store.llm_connections.create_connection(
        "openai_compatible", "Winifred Zai", base_url="https://api.z.ai/api/paas/v4",
        api_key="sk-fake", model="", post_process="none")
    card = _embedding_problem(client, zai, "embed-small")
    assert embed_space.resolve(store.read_config()) is None
    assert card["on"] is False and card["resolves"] is None
    assert card["problem"] == ("embed-small on Winifred Zai cannot make embeddings, "
                               "so embedding is off — choose another Embedding model.")


def _choose_embedding(client, name: str, base_url: str) -> dict:
    provider = store.llm_connections.create_connection(
        "openai_compatible", name, base_url=base_url, api_key="sk-fake")
    body = {"roles": {"embedding": {"selection": {"provider": provider,
                                                  "model": "vec-small"}}},
            "confirm_embedding": True}
    return _ok(client, body)["roles"]["embedding"]


def test_the_settings_write_answers_with_the_embedding_cards_problem(client):
    """Slice D (ruling 4): the write's own answer carries the Embedding card,
    and its `problem` comes from the same `missing` that switched the role off.
    The card tests above read it back after a config write; this is the answer
    to the `PUT` that chose the role, off and then on."""
    _fresh(client)
    off = _choose_embedding(client, "Winifred Zai", "https://api.z.ai/api/paas/v4")
    assert off["on"] is False
    assert off["problem"] == ("vec-small on Winifred Zai cannot make embeddings, so "
                              "embedding is off — choose another Embedding model.")
    assert _global(client)["roles"]["embedding"]["problem"] == off["problem"]

    on = _choose_embedding(client, "Saltmarch Vectors", "https://vectors.example/v1")
    assert on["on"] is True
    assert on["problem"] is None


def test_an_unreadable_campaign_still_has_a_view(client):
    cid = _fresh(client)
    path = store.campaigns.paths.campaign_meta_path(cid)
    path.write_bytes(b"\xff\xfe not text")
    got = client.get(f"/api/campaigns/{cid}/inference")
    assert got.status_code == 200, got.text


def test_the_store_write_enforces_the_confirmation_too(client):
    _fresh(client)
    with pytest.raises(settings.RefusedError) as refused:
        settings.write("global", "", EMBED)
    assert refused.value.status == 400
    settings.write("global", "", EMBED, confirm_embedding=True)
    assert store.read_config()[keys.role_key("embedding", "model")] == "vendor/embed-small"


def test_a_write_on_a_newer_store_is_409(client):
    cid = _fresh(client)
    store.write_config(**{keys.FORMAT_KEY: "3"})
    for where in ("", cid):
        got = _put(client, {"roles": {"fast": {"selection": {"provider": "spare",
                                                             "model": "vendor/spare"}}}},
                   where)
        assert got.status_code == 409, got.text
        assert got.json()["kind"] == "newer_format"
    assert store.read_config().get(keys.role_key("fast", "provider"), "") == ""
    assert keys.role_key("fast", "provider") not in _meta(cid)
    assert _global(client)["newer"] is True


def test_a_store_a_newer_build_switches_after_the_check_is_409_in_the_hold(
        client, monkeypatch):
    """Another process -- a newer build -- can switch the format after the
    request's first check and before the write takes its lock. The format is
    read again in the hold that writes, so the write is refused rather than
    landing a key the newer layout does not read."""
    cid = _fresh(client)
    for where in ("", cid):
        real = settings._require_current

        def then_newer(real=real):
            real()
            store.write_config(**{keys.FORMAT_KEY: "3"})   # the other process

        monkeypatch.setattr(settings, "_require_current", then_newer)
        got = _put(client, {"roles": {"fast": {"selection": {"provider": "spare",
                                                             "model": "vendor/spare"}}}},
                   where)
        assert got.status_code == 409, got.text
        assert got.json() == routes.common.NEWER_FORMAT
        monkeypatch.setattr(settings, "_require_current", real)
        store.write_config(**{keys.FORMAT_KEY: keys.CURRENT_FORMAT})
    assert store.read_config().get(keys.role_key("fast", "provider"), "") == ""
    assert keys.role_key("fast", "provider") not in _meta(cid)


def test_a_campaign_a_newer_build_marked_is_409(client):
    cid = _fresh(client)
    path = store.home() / "campaigns" / cid / "campaign.md"
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    path.write_text(dump_frontmatter({**meta, keys.FORMAT_KEY: "3"}, body), encoding="utf-8")
    assert keys.is_newer(_meta(cid)), _meta(cid)
    got = _put(client, {"roles": {"fast": {"selection": {"provider": "spare",
                                                         "model": "vendor/spare"}}}}, cid)
    assert got.status_code == 409, got.text
    assert got.json()["kind"] == "newer_format"
    assert keys.role_key("fast", "provider") not in _meta(cid)


def test_a_write_before_migration_is_409_not_migrated(client):
    cid = base._fresh(client)["cid"]
    base._spare(client)
    for where in ("", cid):
        got = _put(client, {"roles": {"fast": {"selection": {"provider": "spare"}}}}, where)
        assert got.status_code == 409, got.text
        detail = got.json()
        assert detail["kind"] == "not_migrated"
        assert detail["status"]["state"] == "pending"
    assert keys.FORMAT_KEY not in _meta(cid)
    assert store.read_config().get(keys.role_key("fast", "provider"), "") == ""


def test_a_missing_campaign_is_404(client):
    _fresh(client)
    assert client.get("/api/campaigns/nosuch/inference").status_code == 404
    assert _put(client, {"routes": {"scene": {"use": "fast"}}}, "nosuch").status_code == 404
    assert _put(client, {"routes": {"tagline": {"use": "fast"}}}, "nosuch").status_code == 404


def _skipped_campaign(client) -> str:
    """A campaign whose legacy `route_scene` names `spare`, left unmarked by a
    migration that found it busy."""
    cid = base._fresh(client)["cid"]
    base._spare(client)
    base._campaign_routing(cid, {"route_scene": "spare"})
    held, release = threading.Event(), threading.Event()

    def holder():
        with locks.campaign_lock(cid):
            held.set()
            release.wait(WAIT)

    t = threading.Thread(target=holder)
    t.start()
    try:
        assert held.wait(WAIT)
        assert migrate.ensure().state == "pending"
    finally:
        release.set()
        t.join(WAIT)
    assert keys.is_current(store.read_config())
    assert keys.FORMAT_KEY not in _meta(cid)
    return cid


def _fork(client, cid: str) -> str:
    got = client.post(f"/api/campaigns/{cid}/fork", json={"name": "Saltmarch Fork"})
    assert got.status_code == 200, got.text
    fork = got.json()["id"]
    # A fork is born through the birth seam: translated, then marked, so its
    # source's legacy `route_scene` arrives as the pin it meant.
    meta = _meta(fork)
    assert meta[keys.FORMAT_KEY] == keys.CURRENT_FORMAT
    assert meta[keys.use_key("scene")] == keys.PIN
    assert meta[keys.pin_key("scene", "provider")] == "spare"
    return fork


@pytest.mark.parametrize("forked", [False, True], ids=["skipped", "fork_of_skipped"])
def test_a_campaign_write_migrates_that_campaign_first(client, forked):
    """Review Focus 2: a write to an unmarked campaign migrates it in the same
    hold, so its legacy `route_scene` survives the marker the write stamps."""
    cid = _skipped_campaign(client)
    if forked:
        cid = _fork(client, cid)
    assert _resolution("chat", cid)["provider"] == "spare"
    got = _ok(client, {"roles": {"fast": {"selection": {"provider": "openrouter",
                                                        "model": "vendor/fast"}}}}, cid)
    # What played before the write still plays.
    assert _resolution("chat", cid)["provider"] == "spare"
    meta = _meta(cid)
    assert meta[keys.FORMAT_KEY] == keys.CURRENT_FORMAT
    assert meta[keys.use_key("scene")] == keys.PIN
    assert meta[keys.pin_key("scene", "provider")] == "spare"
    assert meta[keys.role_key("fast", "provider")] == "openrouter"
    assert _pair(_row(got, "scene")["resolves"]) == ("spare", "vendor/spare")
    assert _pair(_row(got, "absorb")["resolves"]) == ("openrouter", "vendor/fast")


def test_the_campaign_write_holds_one_lock_across_migrate_and_write(client, monkeypatch):
    """`migrate.campaign` refuses unless its caller holds the lock; the write
    reaches it inside its own hold."""
    cid = _skipped_campaign(client)
    seen = []
    real = migrate.campaign

    def watching(c: str) -> bool:
        seen.append(locks.holds_campaign(c))
        return real(c)

    monkeypatch.setattr(migrate, "campaign", watching)
    settings.write("campaign", cid, {"routes": {"opener": {"use": "fast"}}})
    assert seen == [True]


def test_campaign_write_bumps_the_revision(client):
    cid = _fresh(client)
    before = revision.current(cid)
    settings.write("campaign", cid, {"routes": {"scene": {"use": "fast"}}})
    after = revision.current(cid)
    assert after != before
    # A write that changes nothing moves nothing.
    settings.write("campaign", cid, {"routes": {"scene": {"use": "fast"}}})
    assert revision.current(cid) == after


def test_the_view_never_carries_a_key(client):
    cid = _fresh(client)
    _ok(client, {**EMBED, "confirm_embedding": True})
    for text in (client.get("/api/inference/settings").text,
                 client.get(f"/api/campaigns/{cid}/inference").text):
        assert "sk-test-active" not in text and "sk-spare" not in text
        assert "api_key" not in text
