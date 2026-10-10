"""Providers, model facts, health and presets on the API (slice C, Task 5).

A provider is created from a preset (`store/inference/providers.py`): the
preset fixes the adapter, the address when it is locked, and the billing
unless the body says otherwise. A model's facts are written and read beside
the provider. A health check that generates (the Claude subscription) is sent
only on `confirm: true`. Deleting a provider or a preset clears the global
roles, fallbacks and pins that name it. At format 2 the legacy settings --
`config.md`'s legacy inference keys, a campaign's `route_*` keys and a
connection's model fields -- are refused on write, checked inside the hold
that writes them; on a store a newer build wrote, every model-settings write
is a 409 `newer_format`.

Invented connection ids and the codebase's placeholder names only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import grimoire.store as store
from grimoire import routes
from grimoire.store import inference_keys as keys
from grimoire.store import routing
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
from grimoire.store.inference import capabilities, facts, providers
from tests.llm_fakes import FakeCatalog

from . import inference_baseline as base

HEALTH_UNCONFIRMED = ("This check sends one short message and may use your "
                      "subscription — confirm to run it.")
ROUTE = "summary"


@pytest.fixture
def client(tmp_path):
    with base.client_at(tmp_path) as c:
        yield c


def _format(value: str) -> None:
    store.write_config(**{keys.FORMAT_KEY: value})


def _create(client, **body):
    return client.post("/api/llm-connections", json={"name": "Saltmarch", **body})


def _raw(conn_id: str) -> dict:
    return store.llm_connections.read_connection_raw(conn_id)


def _spare(client) -> str:
    got = _create(client, kind="openrouter", name="spare", api_key="sk-spare")
    assert got.status_code == 200, got.text
    return got.json()["id"]


def _campaign_meta(cid: str) -> dict:
    meta, _ = parse_frontmatter(store.campaigns.paths.campaign_meta_path(cid)
                                .read_text(encoding="utf-8"))
    return meta


def _set_campaign_meta(cid: str, **fields: str) -> None:
    path = store.campaigns.paths.campaign_meta_path(cid)
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    meta.update(fields)
    path.write_text(dump_frontmatter(meta, body), encoding="utf-8")


# ---- creating a provider from a preset ----
def test_a_locked_preset_fixes_the_address(client):
    url = providers.PRESETS["zai"].base_url
    made = _create(client, kind="openai_compatible", preset="zai", api_key="sk-z")
    assert made.status_code == 200, made.text
    raw = _raw(made.json()["id"])
    assert (raw["base_url"], raw["preset"], raw["billing"]) == (url, "zai", "metered")

    same = _create(client, kind="openai_compatible", preset="zai", base_url=url)
    assert same.status_code == 200, same.text

    moved = _create(client, kind="openai_compatible", preset="zai",
                    base_url="http://localhost:9999/v1")
    assert moved.status_code == 400
    assert moved.json()["detail"] == "this provider's address is fixed"

    # An editable preset keeps the address it was given.
    local = _create(client, kind="openai_compatible", preset="ollama",
                    base_url="http://saltmarch.lan:11434/v1")
    assert _raw(local.json()["id"])["base_url"] == "http://saltmarch.lan:11434/v1"

    # And a locked one cannot be repointed afterwards either.
    zid = made.json()["id"]
    assert client.put(f"/api/llm-connections/{zid}",
                      json={"base_url": "http://localhost:9999/v1"}).status_code == 400
    assert _raw(zid)["base_url"] == url
    # Not even by clearing the preset in the same body.
    assert client.put(f"/api/llm-connections/{zid}",
                      json={"preset": "", "base_url": "http://localhost:9999/v1"}
                      ).status_code == 400
    assert _raw(zid)["base_url"] == url
    # Clearing it alone, or with its own address, is still taken -- and the
    # connection, now on the preset it infers, is still locked after it.
    assert client.put(f"/api/llm-connections/{zid}",
                      json={"preset": "", "base_url": url}).status_code == 200
    assert client.put(f"/api/llm-connections/{zid}",
                      json={"base_url": "http://localhost:9999/v1"}).status_code == 400
    assert _raw(zid)["base_url"] == url


def test_kind_must_match_the_preset(client):
    wrong = _create(client, kind="openrouter", preset="zai")
    assert wrong.status_code == 400
    assert "z.ai" in wrong.json()["detail"]
    assert _create(client, kind="openrouter", preset="no-such").status_code == 400
    assert [c["id"] for c in client.get("/api/llm-connections").json()] == [
        "claude", "openrouter"]

    zid = _create(client, kind="openai_compatible", preset="zai").json()["id"]
    assert client.put(f"/api/llm-connections/{zid}",
                      json={"preset": "openrouter"}).status_code == 400
    assert client.put(f"/api/llm-connections/{zid}",
                      json={"preset": "zai_coding"}).status_code == 200
    raw = _raw(zid)
    assert (raw["preset"], raw["base_url"]) == (
        "zai_coding", providers.PRESETS["zai_coding"].base_url)


def test_an_anthropic_provider_can_be_created(client):
    made = _create(client, kind="anthropic", preset="anthropic", api_key="sk-ant")
    assert made.status_code == 200, made.text
    raw = _raw(made.json()["id"])
    assert (raw["kind"], raw["base_url"], raw["billing"], raw["preset"]) == (
        "anthropic", "https://api.anthropic.com", "metered", "anthropic")

    # Billing defaults to the preset's, and the body may say otherwise.
    coding = _create(client, kind="openai_compatible", preset="zai_coding")
    assert _raw(coding.json()["id"])["billing"] == "subscription"
    custom = _create(client, kind="openai_compatible", preset="custom",
                     base_url="http://saltmarch.lan/v1", billing="subscription")
    assert _raw(custom.json()["id"])["billing"] == "subscription"
    assert _create(client, kind="openai_compatible", preset="custom",
                   billing="free").status_code == 400


@pytest.mark.parametrize("brk", ["\u2028", "\u2029", "\x85", "\n"],
                         ids=lambda c: f"U+{ord(c):04X}")
def test_a_provider_text_field_holding_a_line_break_is_refused(client, brk):
    """A provider is frontmatter, and a line boundary the parser splits on
    would read the rest of the field back as a key of its own -- `kind`,
    `api_key`. Refused on create and on edit, before anything is written."""
    before = sorted(p.name for p in (store.home() / "llm_connections").glob("*"))
    for field in ("name", "base_url", "api_key"):
        got = _create(client, kind="openai_compatible", preset="custom",
                      **{"base_url": "http://saltmarch.lan/v1",
                         field: f"Saltmarch{brk}kind: anthropic"})
        assert got.status_code == 400, (field, got.text)
        assert f"{field} must be one line" in got.json()["detail"]
    assert sorted(p.name for p in (store.home() / "llm_connections").glob("*")) == before

    pid = _spare(client)
    raw = (store.home() / "llm_connections" / f"{pid}.md").read_bytes()
    got = client.put(f"/api/llm-connections/{pid}",
                     json={"name": f"Saltmarch{brk}api_key: sk-mara"})
    assert got.status_code == 400, got.text
    assert (store.home() / "llm_connections" / f"{pid}.md").read_bytes() == raw


def test_a_create_naming_no_preset_is_stamped_with_the_inferred_one(client):
    """The preset is written at birth, so the connection carries its URL lock
    and is never judged against a blank stored preset later."""
    def stamped(**body):
        made = _create(client, **body)
        assert made.status_code == 200, made.text
        raw = _raw(made.json()["id"])
        return raw["preset"], raw["billing"]

    coding = providers.PRESETS["zai_coding"].base_url
    assert stamped(kind="openrouter") == ("openrouter", "metered")
    assert stamped(kind="claude") == ("claude", "subscription")
    assert stamped(kind="openai_compatible", base_url=coding) == (
        "zai_coding", "subscription")
    assert stamped(kind="openai_compatible", base_url="http://saltmarch.lan/v1") == (
        "custom", "metered")
    # A billing the body names is kept over the inferred preset's.
    assert stamped(kind="openai_compatible", base_url=coding, billing="metered") == (
        "zai_coding", "metered")

    # The inferred preset's lock holds afterwards: its address is fixed.
    zid = _create(client, kind="openai_compatible", name="Coding",
                  base_url=coding).json()["id"]
    moved = client.put(f"/api/llm-connections/{zid}",
                       json={"base_url": "http://localhost:9999/v1"})
    assert (moved.status_code, moved.json()["detail"]) == (
        400, "this provider's address is fixed")
    assert _raw(zid)["base_url"] == coding


def test_an_inferred_lock_still_saves_the_address_it_was_created_with(client):
    """A create stamped with a locked preset whose address it does not quite
    match (the host is the preset's, the path is not) is accepted as before --
    and the editor resending that stored address is no change, not a 400."""
    odd = "https://api.openai.com/v2"
    made = _create(client, kind="openai_compatible", base_url=odd, api_key="sk-o")
    assert made.status_code == 200, made.text
    oid = made.json()["id"]
    assert (_raw(oid)["preset"], _raw(oid)["base_url"]) == ("openai", odd)
    resent = client.put(f"/api/llm-connections/{oid}",
                        json={"name": "Renamed", "base_url": odd, "api_key": ""})
    assert resent.status_code == 200, resent.text
    assert (_raw(oid)["name"], _raw(oid)["api_key"]) == ("Renamed", "sk-o")
    assert client.put(f"/api/llm-connections/{oid}",
                      json={"base_url": "https://api.openai.com/v3"}).status_code == 400


@pytest.mark.parametrize("fmt", ["1", "2"])
@pytest.mark.parametrize("kind", ["openrouter", "anthropic"])
def test_naming_an_unstamped_connections_own_preset_keeps_its_key_and_rev(
        client, fmt, kind):
    """A connection no build has stamped -- the seeded OpenRouter one on a
    store born at format 2, or one an older build created with no preset --
    is already on the preset it would infer. Naming that preset repoints
    nothing: its blank address is not "moved" to the preset's, which would
    drop its key."""
    if kind == "openrouter":
        pid = "openrouter"
        store.llm_connections.update_connection(pid, api_key="sk-seeded")
    else:
        pid = store.llm_connections.create_connection(
            kind, "Saltmarch", base_url="", api_key="sk-seeded")
    _format(fmt)
    before = _raw(pid)
    assert (before["preset"], before["base_url"], before["api_key"]) == (
        "", "", "sk-seeded")
    r = client.put(f"/api/llm-connections/{pid}",
                   json={"name": before["name"], "base_url": "", "api_key": "",
                         "preset": kind})
    assert r.status_code == 200, r.text
    after = _raw(pid)
    assert (after["api_key"], after["base_url"], after["rev"]) == (
        "sk-seeded", "", before["rev"])
    assert after["preset"] == kind


def test_a_preset_move_on_the_same_host_keeps_the_key(client):
    zid = _create(client, kind="openai_compatible", preset="zai", api_key="sk-z").json()["id"]
    # The editor's save: every field, the key blank for "keep it".
    r = client.put(f"/api/llm-connections/{zid}",
                   json={"preset": "zai_coding", "base_url": "", "api_key": ""})
    assert r.status_code == 200, r.text
    raw = _raw(zid)
    assert (raw["preset"], raw["base_url"], raw["api_key"]) == (
        "zai_coding", providers.PRESETS["zai_coding"].base_url, "sk-z")


def test_a_preset_move_to_another_host_drops_the_key(client):
    oid = _create(client, kind="openai_compatible", preset="openai",
                  api_key="sk-o").json()["id"]
    r = client.put(f"/api/llm-connections/{oid}",
                   json={"preset": "zai", "base_url": "", "api_key": ""})
    assert r.status_code == 200, r.text
    raw = _raw(oid)
    assert (raw["preset"], raw["base_url"], raw["api_key"]) == (
        "zai", providers.PRESETS["zai"].base_url, "")
    # A key supplied with the move is the one kept.
    r = client.put(f"/api/llm-connections/{oid}",
                   json={"preset": "openai", "api_key": "sk-new"})
    assert r.status_code == 200, r.text
    assert (_raw(oid)["base_url"], _raw(oid)["api_key"]) == (
        providers.PRESETS["openai"].base_url, "sk-new")


def test_presets_list_names_the_generating_check(client):
    got = client.get("/api/providers/presets")
    assert got.status_code == 200
    body = got.json()
    assert [p["id"] for p in body] == list(providers.PRESETS)
    for row in body:
        preset = providers.PRESETS[row["id"]]
        assert row == {**capabilities.preset_body(preset),
                       "generating_check": preset.kind == "claude"}
    assert [p["id"] for p in body if p["generating_check"]] == ["claude"]


# ---- model facts ----
def test_a_facts_write_before_the_switch_is_409_not_migrated(client):
    """At format 1 the lowering reads a connection's legacy fields, not its
    model's facts: a write would answer 200 and change nothing, and the
    migration would later merge the legacy values over it. Refused like every
    other new-layout settings write -- and as `newer_format` past 2."""
    pid = _spare(client)
    assert not keys.is_current(store.read_config())
    for body in ({"model": "m", "prefill": True}, {"model": "m", "vision": "off"},
                 {"model": "m", "overrides": {"vision": "yes"}}):
        r = client.put(f"/api/llm-connections/{pid}/facts", json=body)
        assert r.status_code == 409, (body, r.text)
        assert r.json()["kind"] == "not_migrated"
        assert r.json()["status"]["state"] in ("pending", "failed", "running")
    assert not store.llm_connections.facts_path(pid).exists()

    _format("3")
    r = client.put(f"/api/llm-connections/{pid}/facts", json={"model": "m", "prefill": True})
    assert r.status_code == 409 and r.json()["kind"] == "newer_format"
    assert not store.llm_connections.facts_path(pid).exists()


def test_facts_round_trip_and_validate(client):
    _format("2")
    pid = _spare(client)
    put = client.put(f"/api/llm-connections/{pid}/facts", json={
        "model": "vendor/mara-7b", "vision": "on", "prefill": True,
        "post_process": "strict", "overrides": {"structured_output": "yes", "tools": "no"}})
    assert put.status_code == 200, put.text

    got = client.get(f"/api/llm-connections/{pid}/facts",
                     params={"model": "vendor/mara-7b"}).json()
    assert got["model"] == "vendor/mara-7b"
    assert (got["vision"], got["prefill"], got["post_process"]) == ("on", True, "strict")
    assert got["overrides"] == {"structured_output": "yes", "tools": "no"}
    assert got["capabilities"]["tools"] == {"value": "no", "source": "user"}
    assert got["capabilities"]["vision"] == {"value": "yes", "source": "user"}
    assert got["capabilities"]["structured_output"] == {"value": "yes", "source": "user"}
    assert set(got["capabilities"]) == set(capabilities.NAMES)
    assert put.json() == got

    before = store.llm_connections.facts_path(pid).read_text(encoding="utf-8")
    for bad in ({"vision": "maybe"}, {"prefill": "true"}, {"post_process": "loose"},
                {"overrides": {"telepathy": "yes"}}, {"overrides": {"vision": "maybe"}},
                {"overrides": ["vision"]}):
        r = client.put(f"/api/llm-connections/{pid}/facts",
                       json={"model": "vendor/mara-7b", **bad})
        assert r.status_code == 400, (bad, r.text)
    assert client.put(f"/api/llm-connections/{pid}/facts",
                      json={"model": " ", "vision": "on"}).status_code == 400
    assert store.llm_connections.facts_path(pid).read_text(encoding="utf-8") == before

    assert client.get("/api/llm-connections/nope/facts").status_code == 404
    assert client.put("/api/llm-connections/nope/facts",
                      json={"model": "m", "vision": "on"}).status_code == 404
    assert not store.llm_connections.facts_path("nope").exists()


# ---- 01i: the model's size, stated and resolved ----
def _limit(value, source, catalog=None) -> dict:
    return {"value": value, "source": source, "catalog": catalog}


def test_facts_state_and_return_the_models_limits(client):
    _format("2")
    pid = _spare(client)
    store.llm_connections.set_cached_models(
        pid, [{"id": "vendor/m", "context": 131072, "max_output": 16000}], _raw(pid)["rev"])
    url = f"/api/llm-connections/{pid}/facts"
    got = client.get(url, params={"model": "vendor/m"}).json()
    assert got["limits"] == {"window": _limit(131072, "catalog", 131072),
                             "max_output": _limit(16000, "catalog", 16000)}
    assert (got["context_window"], got["max_output"]) == (None, None)

    put = client.put(url, json={"model": "vendor/m", "context_window": 32768})
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["context_window"] == 32768 and body["max_output"] is None
    # The user's word wins, and the listing's figure stays beside it.
    assert body["limits"]["window"] == _limit(32768, "user", 131072)
    assert body["limits"]["max_output"] == _limit(16000, "catalog", 16000)
    assert client.get(url, params={"model": "vendor/m"}).json() == body

    cleared = client.put(url, json={"model": "vendor/m", "context_window": 0}).json()
    assert cleared["limits"]["window"] == _limit(131072, "catalog", 131072)
    assert cleared["context_window"] is None

    # A model nothing lists or states.
    other = client.get(url, params={"model": "vendor/unlisted"}).json()
    assert other["limits"] == {"window": _limit(None, "unknown"),
                               "max_output": _limit(None, "unknown")}


def test_a_limit_write_is_refused_as_every_facts_write_is(client):
    _format("2")
    pid = _spare(client)
    url = f"/api/llm-connections/{pid}/facts"
    assert client.put(url, json={"model": "m", "context_window": 8192}).status_code == 200
    before = store.llm_connections.facts_path(pid).read_bytes()
    for bad in ({"context_window": "8192"}, {"context_window": True}, {"max_output": -1},
                {"context_window": 2**31}, {"max_output": 1.5}, {"max_output": 16000},
                {"context_window": 4096, "max_output": 8000}):
        r = client.put(url, json={"model": "m", **bad})
        assert r.status_code == 400, (bad, r.text)
    assert store.llm_connections.facts_path(pid).read_bytes() == before
    # The reverse order: an output stated first, then a window below it.
    assert client.put(url, json={"model": "n", "max_output": 16000}).status_code == 200
    before = store.llm_connections.facts_path(pid).read_bytes()
    r = client.put(url, json={"model": "n", "context_window": 8192})
    assert r.status_code == 400 and "window" in r.json()["detail"]
    assert store.llm_connections.facts_path(pid).read_bytes() == before


def test_a_limit_write_before_the_switch_is_409_not_migrated(client):
    pid = _spare(client)
    r = client.put(f"/api/llm-connections/{pid}/facts",
                   json={"model": "m", "context_window": 8192})
    assert r.status_code == 409 and r.json()["kind"] == "not_migrated"
    assert not store.llm_connections.facts_path(pid).exists()


def test_a_limit_write_asks_no_confirm_embedding(client):
    """Stating a size spends nothing and moves no vector space -- not even on
    the model the Embedding role embeds with."""
    pid = _embedding_on(client)
    space = store.embed_space.resolve()
    assert space is not None
    got = client.put(f"/api/llm-connections/{pid}/facts",
                     json={"model": "vendor/embed-small", "context_window": 8192,
                           "max_output": 1})
    assert got.status_code == 200, got.text
    assert store.embed_space.resolve() == space


def test_facts_default_to_the_providers_own_model(client):
    pid = _spare(client)
    store.llm_connections.update_connection(pid, model="vendor/spare")
    _format("2")
    client.put(f"/api/llm-connections/{pid}/facts",
               json={"model": "vendor/spare", "prefill": True})
    got = client.get(f"/api/llm-connections/{pid}/facts").json()
    assert (got["model"], got["prefill"]) == ("vendor/spare", True)


def test_an_empty_override_clears_it(client):
    _format("2")
    pid = _spare(client)
    url = f"/api/llm-connections/{pid}/facts"
    client.put(url, json={"model": "m", "overrides": {"vision": "yes", "prefill": "no"}})
    got = client.put(url, json={"model": "m", "overrides": {"vision": ""}}).json()
    assert got["overrides"] == {"prefill": "no"}
    got = client.put(url, json={"model": "m", "overrides": {"prefill": ""}}).json()
    assert got["overrides"] == {}
    assert facts.read(pid) == {}


def test_a_facts_write_never_recreates_a_deleted_providers_file(client, monkeypatch):
    """The facts writers compare-and-write under the connection lock, as
    `record_verified` does: a write for a provider that is gone -- including
    one deleted after the route checked it -- files nothing rather than an
    orphan beside a connection that no longer exists."""
    for write in (lambda pid: facts.set_overrides(pid, "m", {"vision": "yes"}),
                  lambda pid: facts.set_stated(pid, "m", prefill=True),
                  lambda pid: facts.state(pid, "m", vision="on")):
        pid = _spare(client)
        store.llm_connections.delete_connection(pid)
        with pytest.raises(store.llm_connections.ConnectionNotFound):
            write(pid)
        assert not store.llm_connections.facts_path(pid).exists()

    _format("2")
    pid = _spare(client)
    real = store.llm_connections.read_connection_raw

    def deleted_after_the_first_read(conn_id):
        got = real(conn_id)
        monkeypatch.setattr(store.llm_connections, "read_connection_raw", real)
        store.llm_connections.delete_connection(conn_id)
        return got

    monkeypatch.setattr(store.llm_connections, "read_connection_raw",
                        deleted_after_the_first_read)
    r = client.put(f"/api/llm-connections/{pid}/facts", json={"model": "m", "vision": "on"})
    assert r.status_code == 404
    assert not store.llm_connections.facts_path(pid).exists()


def _embedding_off_by_the_catalog(client) -> str:
    """Format 2, the Embedding role on a provider whose catalog says the
    chosen model makes text only: off, by a known `no`."""
    pid = _embedding_on(client)
    store.llm_connections.set_cached_models(
        pid, [{"id": "vendor/embed-small", "outputs": ["text"]}], _raw(pid)["rev"])
    assert store.embed_space.resolve() is None
    return pid


def test_a_facts_override_that_turns_embedding_on_needs_confirm(client):
    """The user's `embed: yes` outranks the catalog's `no`, so the role starts
    embedding the library from scratch: a settings write that moves the
    space, refused without `confirm_embedding` (rule 1) and nothing written."""
    pid = _embedding_off_by_the_catalog(client)
    url = f"/api/llm-connections/{pid}/facts"
    body = {"model": "vendor/embed-small", "overrides": {"embed": "yes"}}

    for extra in ({}, {"confirm_embedding": False}, {"confirm_embedding": "true"}):
        got = client.put(url, json={**body, **extra})
        assert got.status_code == 400, (extra, got.text)
        assert got.json()["kind"] == "confirm_embedding"
    assert facts.read(pid) == {}
    assert store.embed_space.resolve() is None

    got = client.put(url, json={**body, "confirm_embedding": True})
    assert got.status_code == 200, got.text
    assert store.embed_space.resolve() is not None


def test_a_facts_write_that_moves_no_space_asks_nothing(client):
    """Another model, a fact that leaves the role off, a role turned off, and
    a role already on: none starts a new space."""
    pid = _embedding_off_by_the_catalog(client)
    url = f"/api/llm-connections/{pid}/facts"
    assert client.put(url, json={"model": "vendor/other",
                                 "overrides": {"embed": "yes"}}).status_code == 200
    assert client.put(url, json={"model": "vendor/embed-small",
                                 "prefill": True}).status_code == 200
    assert client.put(url, json={"model": "vendor/embed-small",
                                 "overrides": {"embed": "no"}}).status_code == 200
    on = _spare(client)
    store.write_config(**{keys.role_key("embedding", "provider"): on})
    assert store.embed_space.resolve() is not None
    got = client.put(f"/api/llm-connections/{on}/facts",
                     json={"model": "vendor/embed-small", "overrides": {"embed": "no"}})
    assert got.status_code == 200, got.text
    assert store.embed_space.resolve() is None


def test_the_facts_confirm_is_compared_in_the_hold_that_writes(client, monkeypatch):
    """The Embedding role moves onto the model between the route's read and
    the facts write: the check made inside the hold still sees it."""
    _format("2")
    pid = _spare(client)
    store.llm_connections.set_cached_models(
        pid, [{"id": "vendor/embed-small", "outputs": ["text"]}], _raw(pid)["rev"])
    real = facts._load_for_write

    def role_moves_first(provider_id, **kw):
        store.write_config(**{keys.role_key("embedding", "provider"): pid,
                              keys.role_key("embedding", "model"): "vendor/embed-small"})
        return real(provider_id, **kw)

    monkeypatch.setattr(facts, "_load_for_write", role_moves_first)
    got = client.put(f"/api/llm-connections/{pid}/facts",
                     json={"model": "vendor/embed-small", "overrides": {"embed": "yes"}})
    assert got.status_code == 400, got.text
    assert got.json()["kind"] == "confirm_embedding"


def _facts_held(monkeypatch, pid: str) -> bytes:
    """`pid`'s facts file held by a sync client: every read of it raises."""
    path = store.llm_connections.facts_path(pid)
    before = path.read_bytes()
    real = Path.read_text

    def held(self, *a, **kw):
        if self == path:
            raise OSError("held by a sync client")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", held)
    return before


def test_unreadable_facts_are_flagged_and_never_written(client, monkeypatch):
    """A facts file that cannot be read is not one with nothing stated: the
    GET says so (`unreadable`), and a save is refused as C refuses it (503,
    try again) with its fixed detail -- never a 500, never the file
    replaced, never the error's text."""
    _format("2")
    pid = _spare(client)
    facts.set_overrides(pid, "m", {"vision": "yes"})
    url = f"/api/llm-connections/{pid}/facts"
    assert client.get(url, params={"model": "m"}).json()["unreadable"] is False
    before = _facts_held(monkeypatch, pid)

    got = client.get(url, params={"model": "m"})
    assert got.status_code == 200, got.text
    assert (got.json()["unreadable"], got.json()["unreadable_reason"]) == (True, "held")

    got = client.put(url, json={"model": "m", "prefill": True})
    assert got.status_code == 503, got.text
    assert got.json() == {"detail": routes.config.FACTS_UNREADABLE}
    assert "sync client" not in got.text
    assert store.llm_connections.facts_path(pid).read_bytes() == before


@pytest.mark.parametrize("raw", ["{not json", "[]", '{"m": "oops"}', ""])
def test_a_mangled_facts_file_is_flagged_and_refused_with_409(client, raw):
    """A file that was read and does not parse is not the transient 503: it
    will not clear on its own, so the write is refused as 409
    `facts_unreadable` with a fixed detail saying what a person must do, and
    the file is never replaced. The GET flags it too, so no save is offered."""
    _format("2")
    pid = _spare(client)
    path = store.llm_connections.facts_path(pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    url = f"/api/llm-connections/{pid}/facts"

    got = client.get(url, params={"model": "m"})
    assert got.status_code == 200, got.text
    assert (got.json()["unreadable"], got.json()["unreadable_reason"]) == (True, "mangled")

    got = client.put(url, json={"model": "m", "prefill": True})
    assert got.status_code == 409, got.text
    assert got.json() == {"kind": "facts_unreadable",
                          "detail": routes.config.FACTS_MANGLED}
    assert path.read_text(encoding="utf-8") == raw


def test_a_readable_facts_file_carries_no_unreadable_reason(client):
    _format("2")
    pid = _spare(client)
    got = client.get(f"/api/llm-connections/{pid}/facts", params={"model": "m"}).json()
    assert got["unreadable"] is False
    assert "unreadable_reason" not in got


# ---- health ----
def test_a_generating_health_check_needs_confirm(client):
    fake = FakeCatalog()
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    for body in (None, {}, {"confirm": False}, {"confirm": "true"}, {"confirm": 1}):
        r = client.post("/api/llm-connections/claude/health", json=body)
        assert r.status_code == 400, body
        assert r.json()["detail"] == HEALTH_UNCONFIRMED
    assert fake.checked == []

    r = client.post("/api/llm-connections/claude/health", json={"confirm": True})
    assert (r.status_code, r.json()["ok"]) == (200, True)
    assert [c.provider_id for c in fake.checked] == ["claude"]


def test_a_free_health_check_needs_no_confirm(client):
    fake = FakeCatalog()
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    r = client.post("/api/llm-connections/openrouter/health")
    assert (r.status_code, r.json()["ok"]) == (200, True)
    assert [c.provider_id for c in fake.checked] == ["openrouter"]


# ---- delete sweeps ----
def _selection_keys(provider: str, model: str, preset: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for role in keys.GENERATIVE_ROLES:
        out.update({keys.role_key(role, "provider"): provider,
                    keys.role_key(role, "model"): model,
                    keys.role_key(role, "preset"): preset,
                    keys.fallback_key(role, "provider"): provider,
                    keys.fallback_key(role, "model"): model,
                    keys.fallback_key(role, "preset"): preset})
    for route in routing.ROUTES:
        out.update({keys.use_key(route.key): keys.PIN,
                    keys.pin_key(route.key, "provider"): provider,
                    keys.pin_key(route.key, "model"): model,
                    keys.pin_key(route.key, "preset"): preset})
    return out


def test_deleting_a_provider_clears_global_roles_and_pins(client):
    cid = base._world_and_campaign(client)
    pid = _spare(client)
    other = _create(client, kind="openrouter", name="keeper").json()["id"]
    store.sampler_presets.create_preset("warm", {"temperature": 0.9})
    named = {**_selection_keys(pid, "vendor/spare", "warm"),
             keys.role_key("embedding", "provider"): pid,
             keys.role_key("embedding", "model"): "vendor/embed"}
    # Another provider's selection on one role, and a campaign naming the dead one.
    kept = {keys.fallback_key("decision", "provider"): other,
            keys.fallback_key("decision", "model"): "vendor/keep",
            keys.fallback_key("decision", "preset"): "warm"}
    store.write_config(**{**named, **kept})
    _set_campaign_meta(cid, **{keys.role_key("fast", "provider"): pid,
                               keys.role_key("fast", "model"): "vendor/spare"})

    assert client.delete(f"/api/llm-connections/{pid}").status_code == 200

    cfg = store.read_config()
    uses = {keys.use_key(r.key) for r in routing.ROUTES}
    for key in named:
        if key in kept:
            assert cfg[key] == kept[key], key
        elif key in uses:
            # A route's choice of its pin stays; the pin it chose is what went.
            assert cfg[key] == keys.PIN, key
        else:
            assert cfg.get(key, "") == "", key
    # Campaign references dangle; resolution walks past them.
    assert _campaign_meta(cid)[keys.role_key("fast", "provider")] == pid


def test_deleting_a_preset_clears_global_role_and_pin_presets(client):
    cid = base._world_and_campaign(client)
    pid = _spare(client)
    store.sampler_presets.create_preset("warm", {"temperature": 0.9})
    store.sampler_presets.create_preset("cold", {"temperature": 0.2})
    named = _selection_keys(pid, "vendor/spare", "warm")
    store.write_config(**named, **{keys.preset_key(r.key): "warm" for r in routing.ROUTES})
    store.write_config(**{keys.role_key("primary", "preset"): "cold"})
    _set_campaign_meta(cid, **{keys.role_key("fast", "preset"): "warm"})

    assert client.delete("/api/sampler-presets/warm").status_code == 200

    cfg = store.read_config()
    for key, value in named.items():
        if key == keys.role_key("primary", "preset"):
            assert cfg[key] == "cold"
        elif key.endswith("_preset"):
            assert cfg.get(key, "") == "", key
        else:
            assert cfg[key] == value, key        # providers and models are kept
    for route in routing.ROUTES:
        assert cfg.get(keys.preset_key(route.key), "") == "", route.key
    assert _campaign_meta(cid)[keys.role_key("fast", "preset")] == "warm"


# ---- used by ----
def test_used_by_names_roles_routes_fallbacks_and_campaigns(client):
    cid = base._world_and_campaign(client)
    pid = _spare(client)
    _format("2")
    store.write_config(**{
        keys.role_key("primary", "provider"): pid,
        keys.fallback_key("decision", "provider"): pid,
        keys.use_key(ROUTE): keys.PIN, keys.pin_key(ROUTE, "provider"): pid,
        # A pin nothing chooses is not a use.
        keys.pin_key("tracker", "provider"): pid,
        keys.role_key("embedding", "provider"): pid,
        keys.role_key("embedding", "model"): "vendor/embed",
    })
    _set_campaign_meta(cid, **{keys.FORMAT_KEY: keys.CURRENT_FORMAT,
                               keys.role_key("fast", "provider"): pid,
                               keys.use_key("voice"): keys.PIN,
                               keys.pin_key("voice", "provider"): pid})

    got = client.get(f"/api/llm-connections/{pid}").json()["used_by"]
    assert sorted(got, key=lambda u: (u["scope"], u["kind"], u["key"])) == [
        {"kind": "role", "key": "fast", "scope": "campaign", "cid": cid},
        {"kind": "route", "key": "voice", "scope": "campaign", "cid": cid},
        {"kind": "fallback", "key": "decision", "scope": "global"},
        {"kind": "role", "key": "embedding", "scope": "global"},
        {"kind": "role", "key": "primary", "scope": "global"},
        {"kind": "route", "key": ROUTE, "scope": "global"},
    ]
    assert client.get("/api/llm-connections/openrouter").json()["used_by"] == []
    assert all("used_by" not in c for c in client.get("/api/llm-connections").json())


def test_used_by_reads_a_legacy_store_as_it_plays(client):
    base._world_and_campaign(client)
    pid = _spare(client)
    store.write_config(active_connection_id=pid, route_tagline=pid)
    got = client.get(f"/api/llm-connections/{pid}").json()["used_by"]
    assert {(u["kind"], u["key"], u["scope"]) for u in got} == {
        ("role", "primary", "global"), ("route", "tagline", "global")}


# ---- format 2 refuses the legacy settings ----
def test_model_fields_on_a_provider_are_refused_on_format_2_only(client):
    made = _create(client, kind="openrouter", model="vendor/one", vision="on")
    assert made.status_code == 200, made.text
    pid = made.json()["id"]
    assert client.put(f"/api/llm-connections/{pid}",
                      json={"model": "vendor/two", "prefill": True}).status_code == 200

    _format("2")
    for field, value in (("model", "vendor/three"), ("vision", "off"),
                         ("prefill", True), ("post_process", "strict"),
                         ("reasoning_effort", "high")):
        r = _create(client, kind="openrouter", **{field: value})
        assert r.status_code == 400, field
        assert r.json()["detail"] == "set this on the model, not the provider"
    for field, value in (("model", "vendor/three"), ("vision", "off"),
                         ("prefill", False), ("post_process", "strict"),
                         ("reasoning_effort", "high")):
        r = client.put(f"/api/llm-connections/{pid}", json={field: value})
        assert r.status_code == 400, field
        assert r.json()["detail"] == "set this on the model, not the provider"
    raw = _raw(pid)
    assert (raw["model"], raw["vision"], raw["prefill"]) == ("vendor/two", "on", True)

    # Resending what is stored writes no model field, and a rename still saves.
    ok = client.put(f"/api/llm-connections/{pid}",
                    json={"name": "Renamed", "model": "vendor/two", "prefill": True})
    assert ok.status_code == 200, ok.text
    assert _raw(pid)["name"] == "Renamed"
    assert _create(client, kind="openrouter", name="Plain").status_code == 200


def test_a_model_field_write_racing_the_switch_is_refused(client, monkeypatch):
    pid = _spare(client)
    real = routes.config._check_preset_field

    def switch_meanwhile(*args, **kwargs):
        real(*args, **kwargs)
        _format("2")

    monkeypatch.setattr(routes.config, "_check_preset_field", switch_meanwhile)
    r = client.put(f"/api/llm-connections/{pid}", json={"model": "vendor/late"})
    assert r.status_code == 400
    assert _raw(pid)["model"] == ""


def test_put_config_refuses_legacy_inference_keys_on_format_2(client):
    pid = _spare(client)
    assert client.put("/api/config", json={"fallback_connection_id": pid}).status_code == 200
    _format("2")
    for key in ("active_connection_id", "fallback_connection_id",
                "embeddings_connection_id", "embeddings_model"):
        r = client.put("/api/config", json={key: pid, "user_label": "Seraphine"})
        assert r.status_code == 400, key
        assert r.json()["detail"] == "this setting moved to Models"
    cfg = store.read_config()
    assert (cfg["active_connection_id"], cfg["fallback_connection_id"]) == ("openrouter", pid)
    assert cfg.get("user_label") != "Seraphine"
    assert client.put("/api/config", json={"user_label": "Seraphine"}).status_code == 200


def test_a_legacy_config_write_racing_the_switch_is_refused(client, monkeypatch):
    """The format is read inside the `config_lock` hold that writes: the
    switch landing after the route began and before its write is seen."""
    pid = _spare(client)
    real = routes.config._recursion_depth_ok

    def switch_meanwhile(value):
        _format("2")
        return real(value)

    monkeypatch.setattr(routes.config, "_recursion_depth_ok", switch_meanwhile)
    r = client.put("/api/config", json={"fallback_connection_id": pid})
    assert (r.status_code, r.json()["detail"]) == (400, "this setting moved to Models")
    assert store.read_config().get("fallback_connection_id", "") == ""


# ---- a newer store refuses every model-settings write ----
def _newer_writes(client) -> dict:
    pid = _spare(client)
    store.sampler_presets.create_preset("warm", {"temperature": 0.9})
    base._world_and_campaign(client)    # its campaign.md is in the snapshot too
    return {
        "create provider": lambda: _create(client, kind="openrouter", name="late"),
        "update provider": lambda: client.put(f"/api/llm-connections/{pid}",
                                              json={"name": "Late"}),
        "delete provider": lambda: client.delete(f"/api/llm-connections/{pid}"),
        "facts": lambda: client.put(f"/api/llm-connections/{pid}/facts",
                                    json={"model": "m", "vision": "on"}),
        "create preset": lambda: client.post("/api/sampler-presets",
                                             json={"name": "late", "params": {}}),
        "import preset": lambda: client.post("/api/sampler-presets/import",
                                             json={"data": {"temperature": 1.0}}),
        "update preset": lambda: client.put("/api/sampler-presets/warm",
                                            json={"name": "warm", "params": {}}),
        "delete preset": lambda: client.delete("/api/sampler-presets/warm"),
        "test run": lambda: client.post(f"/api/llm-connections/{pid}/test", json={
            "model": "m", "capabilities": ["generate"], "confirm": True}),
        "config inference key": lambda: client.put(
            "/api/config", json={"fallback_connection_id": pid}),
    }


WRITES = ("create provider", "update provider", "delete provider", "facts",
          "create preset", "import preset", "update preset", "delete preset",
          "test run", "config inference key")


def _settings_snapshot() -> dict[str, str]:
    """Every file a model-settings write could touch, with its contents."""
    home = store.home()
    files = [home / "config.md", *sorted((home / "llm_connections").glob("*")),
             *sorted((home / "sampler_presets").glob("*")),
             *sorted((home / "campaigns").glob("*/campaign.md"))]
    return {str(p): p.read_text(encoding="utf-8") for p in files if p.is_file()}


@pytest.mark.parametrize("write", WRITES)
def test_every_settings_write_refuses_a_newer_store(client, write):
    writes = _newer_writes(client)
    assert set(writes) == set(WRITES)
    _format("3")
    before = _settings_snapshot()
    r = writes[write]()
    assert r.status_code == 409, r.text
    assert r.json()["kind"] == "newer_format"
    assert _settings_snapshot() == before
    # A setting that is not a model setting is still written.
    assert client.put("/api/config", json={"user_label": "Seraphine"}).status_code == 200


# ---- an edit that moves the Embedding role's vector space ----
EMBEDDING_MOVE = ("Changing this provider's address or key re-embeds your library "
                  "through spare, which may cost money — confirm to change it.")


def _embedding_on(client) -> str:
    """Format 2, with the Embedding role on an OpenRouter provider that embeds."""
    _format("2")
    pid = _spare(client)
    store.write_config(**{keys.role_key("embedding", "provider"): pid,
                          keys.role_key("embedding", "model"): "vendor/embed-small"})
    assert store.embed_space.resolve() is not None
    return pid


def test_rekeying_the_embedding_provider_needs_confirm(client):
    """A new key restamps the provider's rev, and the rev is part of the
    vector space: every cached vector is orphaned and the library re-embedded
    on paid calls. The server asks first, as it does for a role change."""
    pid = _embedding_on(client)
    before = (_raw(pid)["rev"], store.embed_space.resolve()["space"])

    got = client.put(f"/api/llm-connections/{pid}", json={"api_key": "sk-new"})

    assert got.status_code == 400, got.text
    assert got.json() == {"kind": "confirm_embedding", "detail": EMBEDDING_MOVE}
    assert (_raw(pid)["rev"], store.embed_space.resolve()["space"]) == before
    assert _raw(pid)["api_key"] == "sk-spare"
    # `false`, or anything but `true`, is the same as leaving it out.
    for value in (False, "true", 1):
        got = client.put(f"/api/llm-connections/{pid}",
                         json={"api_key": "sk-new", "confirm_embedding": value})
        assert got.status_code == 400, (value, got.text)
    assert _raw(pid)["api_key"] == "sk-spare"

    got = client.put(f"/api/llm-connections/{pid}",
                     json={"api_key": "sk-new", "confirm_embedding": True})
    assert got.status_code == 200, got.text
    assert _raw(pid)["api_key"] == "sk-new"
    assert store.embed_space.resolve()["space"] != before[1]


def test_a_rev_neutral_edit_of_the_embedding_provider_asks_nothing(client):
    pid = _embedding_on(client)
    space = store.embed_space.resolve()["space"]
    got = client.put(f"/api/llm-connections/{pid}",
                     json={"name": "spare", "billing": "metered", "api_key": ""})
    assert got.status_code == 200, got.text
    assert store.embed_space.resolve()["space"] == space


def test_an_edit_that_turns_embedding_off_asks_nothing(client):
    """A local endpoint repointed nowhere embeds nothing afterwards: nothing is
    re-embedded, so there is nothing to confirm."""
    _format("2")
    made = _create(client, kind="openai_compatible", name="vectors",
                   base_url="http://localhost:1234/v1")
    assert made.status_code == 200, made.text
    pid = made.json()["id"]
    store.write_config(**{keys.role_key("embedding", "provider"): pid,
                          keys.role_key("embedding", "model"): "embed-small"})
    assert store.embed_space.resolve() is not None
    got = client.put(f"/api/llm-connections/{pid}", json={"api_key": "sk-local"})
    assert got.status_code == 400, got.text
    got = client.put(f"/api/llm-connections/{pid}", json={"base_url": ""})
    assert got.status_code == 200, got.text
    assert store.embed_space.resolve() is None


def test_rekeying_another_provider_asks_nothing(client):
    _embedding_on(client)
    got = client.put("/api/llm-connections/openrouter", json={"api_key": "sk-other"})
    assert got.status_code == 200, got.text


def test_a_key_that_turns_embedding_on_needs_confirm(client):
    """The Embedding role names a keyless OpenRouter provider: it embeds
    nothing yet, and the key is what starts embedding the library."""
    _format("2")
    store.write_config(**{keys.role_key("embedding", "provider"): "openrouter",
                          keys.role_key("embedding", "model"): "vendor/embed-small"})
    assert store.embed_space.resolve() is None
    got = client.put("/api/llm-connections/openrouter", json={"api_key": "sk-first"})
    assert got.status_code == 400, got.text
    assert got.json()["kind"] == "confirm_embedding"


def test_a_key_that_outlives_a_catalogs_no_needs_confirm(client):
    """The cached catalog says the chosen model makes text only, so the role
    is off. A new key restamps the rev, which leaves that row stale: once
    saved the provider embeds, so the edit is a move and asks first."""
    pid = _embedding_on(client)
    store.llm_connections.set_cached_models(
        pid, [{"id": "vendor/embed-small", "outputs": ["text"]}], _raw(pid)["rev"])
    assert store.embed_space.resolve() is None
    got = client.put(f"/api/llm-connections/{pid}", json={"api_key": "sk-new"})
    assert got.status_code == 400, got.text
    assert got.json()["kind"] == "confirm_embedding"
    assert _raw(pid)["api_key"] == "sk-spare"


def test_the_embedding_confirm_is_compared_in_the_hold_that_writes(client, monkeypatch):
    """The Embedding role moves onto the provider between the route's read and
    its write: the check made inside the hold still sees it."""
    _format("2")
    pid = _spare(client)
    real = store.llm_connections._update

    def role_moves_first(conn_id, fields, **kw):
        store.write_config(**{keys.role_key("embedding", "provider"): pid,
                              keys.role_key("embedding", "model"): "vendor/embed-small"})
        return real(conn_id, fields, **kw)

    monkeypatch.setattr(store.llm_connections, "_update", role_moves_first)
    got = client.put(f"/api/llm-connections/{pid}", json={"api_key": "sk-new"})
    assert got.status_code == 400, got.text
    assert _raw(pid)["api_key"] == "sk-spare"


# ---- the store ----
def test_a_no_op_update_keeps_the_rev(client):
    pid = _spare(client)
    rev = _raw(pid)["rev"]
    store.llm_connections.update_connection(pid)
    store.llm_connections.update_connection(pid, name="spare", api_key="")
    assert _raw(pid)["rev"] == rev


def test_a_facts_write_that_cannot_read_the_file_is_refused_and_writes_nothing(
        client, monkeypatch):
    """A facts file another program holds is not an empty one: the write is
    refused (503, try again) rather than replacing every other model's facts."""
    from pathlib import Path

    _format("2")
    pid = _spare(client)
    assert facts.record_verified(pid, "vendor/other", _raw(pid)["rev"],
                                 {"vision": {"ok": True}})
    path = store.llm_connections.facts_path(pid)
    before = path.read_bytes()
    real = Path.read_text

    def held(self, *a, **kw):
        if self == path:
            raise PermissionError("held by a sync client")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", held)
    got = client.put(f"/api/llm-connections/{pid}/facts",
                     json={"model": "vendor/m", "vision": "off"})
    monkeypatch.setattr(Path, "read_text", real)
    assert got.status_code == 503, got.text
    assert path.read_bytes() == before


# ---- PUT /config at format 1: the legacy embedding keys ----

def _legacy_embedding(client) -> str:
    """Format 1, with the legacy embedding keys on a local provider that embeds."""
    made = _create(client, kind="openai_compatible", name="vectors",
                   base_url="http://localhost:1234/v1")
    assert made.status_code == 200, made.text
    pid = made.json()["id"]
    store.write_config(embeddings_connection_id=pid, embeddings_model="nomic-embed")
    assert not keys.is_current(store.read_config())
    assert store.embed_space.resolve() is not None
    return pid


@pytest.mark.parametrize("change", [{"embeddings_model": "other-embed"},
                                    {"embeddings_connection_id": "spare"}],
                         ids=["model", "provider"])
def test_moving_the_legacy_embedding_keys_needs_confirm(client, change):
    """At format 1, `PUT /config` is a door to the same re-embed the provider
    edit asks about: the rule is the server's, whatever the format."""
    _legacy_embedding(client)
    spare = _create(client, kind="openai_compatible", name="spare",
                    base_url="http://localhost:4321/v1")
    assert spare.status_code == 200, spare.text
    before = store.embed_space.resolve()["space"]

    for extra in ({}, {"confirm_embedding": False}, {"confirm_embedding": "true"}):
        got = client.put("/api/config", json={**change, **extra})
        assert got.status_code == 400, (extra, got.text)
        assert got.json()["kind"] == "confirm_embedding"
        assert store.embed_space.resolve()["space"] == before

    got = client.put("/api/config", json={**change, "confirm_embedding": True})
    assert got.status_code == 200, got.text
    assert store.embed_space.resolve()["space"] != before


def test_switching_the_legacy_embedding_off_or_leaving_it_asks_nothing(client):
    pid = _legacy_embedding(client)
    assert client.put("/api/config", json={"embeddings_model": "nomic-embed",
                                           "embeddings_connection_id": pid,
                                           "user_label": "Mara"}).status_code == 200
    assert client.put("/api/config", json={"embeddings_model": ""}).status_code == 200
    assert store.embed_space.resolve() is None



# ---- embedding options (01h-S2) ----

NOMIC = {"input": "prefix", "query_prefix": "search_query: ",
         "document_prefix": "search_document: "}
NOMIC_SUFFIX = "\0embopt1:b61a0b1188d1b0bd3a7cf05b26a81c2a"
BGE = {"input": "prefix",
       "query_prefix": "Represent this sentence for searching relevant passages: "}


def _facts_url(pid: str) -> str:
    return f"/api/llm-connections/{pid}/facts"


def test_an_options_write_on_the_roles_model_needs_confirm(client):
    pid = _embedding_on(client)
    space = store.embed_space.resolve()["space"]
    body = {"model": "vendor/embed-small", "embedding": NOMIC}
    for extra in ({}, {"confirm_embedding": False}, {"confirm_embedding": "true"}):
        got = client.put(_facts_url(pid), json={**body, **extra})
        assert got.status_code == 400, (extra, got.text)
        assert got.json()["kind"] == "confirm_embedding"
    assert facts.read(pid) == {}
    assert store.embed_space.resolve()["space"] == space

    got = client.put(_facts_url(pid), json={**body, "confirm_embedding": True})
    assert got.status_code == 200, got.text
    assert store.embed_space.resolve()["space"] == space + NOMIC_SUFFIX
    read = client.get(_facts_url(pid), params={"model": "vendor/embed-small"}).json()
    assert read["embedding"] == NOMIC and read["embedding_invalid"] is False
    assert "embedding_invalid_reason" not in read


def test_moving_back_to_no_options_still_asks(client):
    pid = _embedding_on(client)
    space = store.embed_space.resolve()["space"]
    assert client.put(_facts_url(pid), json={"model": "vendor/embed-small", "embedding": NOMIC,
                                             "confirm_embedding": True}).status_code == 200
    got = client.put(_facts_url(pid), json={"model": "vendor/embed-small", "embedding": {}})
    assert got.status_code == 400 and got.json()["kind"] == "confirm_embedding"
    got = client.put(_facts_url(pid), json={"model": "vendor/embed-small", "embedding": {},
                                            "confirm_embedding": True})
    assert got.status_code == 200, got.text
    assert store.embed_space.resolve()["space"] == space


def test_a_query_side_only_options_write_asks_nothing(client):
    pid = _embedding_on(client)
    space = store.embed_space.resolve()["space"]
    got = client.put(_facts_url(pid), json={"model": "vendor/embed-small", "embedding": BGE})
    assert got.status_code == 200, got.text
    assert store.embed_space.resolve()["space"] == space
    client.put(_facts_url(pid), json={"model": "vendor/embed-small", "embedding": NOMIC,
                                      "confirm_embedding": True})
    got = client.put(_facts_url(pid), json={
        "model": "vendor/embed-small", "embedding": {**NOMIC, "query_prefix": "query: "}})
    assert got.status_code == 200, got.text
    assert store.embed_space.resolve()["space"] == space + NOMIC_SUFFIX


def test_an_options_write_on_another_model_asks_nothing(client):
    pid = _embedding_on(client)
    got = client.put(_facts_url(pid), json={"model": "vendor/other", "embedding": NOMIC})
    assert got.status_code == 200, got.text


@pytest.mark.parametrize(("block", "message"), [
    ({"input": "param", "param_field": "input_type", "query_value": "query",
      "document_value": "document"}, facts.PARAM_NOT_YET),
    ({"dimensions": 512}, facts.DIMENSIONS_NOT_YET),
    ({"input": "prefix", "document_prefix": "a\u0000b"}, facts.EMBED_CONTROL),
    ({"dimensions_field": "model"}, facts.EMBED_RESERVED),
    ({"document_prefix": "passage: "}, facts.EMBED_WRONG_MODE),
])
def test_invalid_options_are_400_and_write_nothing(client, block, message):
    pid = _embedding_on(client)
    got = client.put(_facts_url(pid), json={"model": "vendor/embed-small", "embedding": block,
                                            "confirm_embedding": True})
    assert got.status_code == 400, got.text
    assert got.json()["detail"] == message
    assert facts.read(pid) == {}


def test_a_qwen_style_prefix_with_a_newline_saves(client):
    pid = _embedding_on(client)
    block = {"input": "prefix",
             "query_prefix": "Instruct: Given a passage of a story, retrieve the lore, "
                             "records or images it concerns\nQuery: "}
    got = client.put(_facts_url(pid), json={"model": "vendor/embed-small", "embedding": block})
    assert got.status_code == 200, got.text
    assert facts.read(pid)["vendor/embed-small"]["embedding"] == block


def test_an_invalid_block_on_disk_is_flagged(client):
    pid = _embedding_on(client)
    path = store.llm_connections.facts_path(pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"vendor/embed-small": {"embedding": {
        "input": "param", "param_field": "input_type", "query_value": "query",
        "document_value": "document"}}}), encoding="utf-8")
    read = client.get(_facts_url(pid), params={"model": "vendor/embed-small"}).json()
    assert read["embedding_invalid"] is True
    assert read["embedding_invalid_reason"] == facts.PARAM_NOT_YET
    assert store.embed_space.resolve() is None


def test_an_options_write_before_the_switch_is_409_not_migrated(client):
    pid = _spare(client)
    r = client.put(_facts_url(pid), json={"model": "m", "embedding": NOMIC})
    assert r.status_code == 409 and r.json()["kind"] == "not_migrated"
    assert not store.llm_connections.facts_path(pid).exists()


@pytest.mark.parametrize("state", ["held", "mangled"])
def test_a_key_edit_with_unreadable_facts_still_asks(client, monkeypatch, state):
    """Gate finding 1: while the role's facts cannot be read it names no
    space -- which is not "embeds nothing". A key edit then would land the
    role on the new rev's space, unasked, once the file is readable again,
    so it asks; a rename moves no space and still asks nothing."""
    pid = _embedding_on(client)
    client.put(_facts_url(pid), json={"model": "vendor/embed-small", "embedding": NOMIC,
                                      "confirm_embedding": True})
    if state == "held":
        _facts_held(monkeypatch, pid)
    else:
        store.llm_connections.facts_path(pid).write_text("{", encoding="utf-8")
    assert store.embed_space.resolve() is None
    got = client.put(f"/api/llm-connections/{pid}", json={"api_key": "sk-new"})
    assert got.status_code == 400, got.text
    assert got.json()["kind"] == "confirm_embedding"
    assert _raw(pid)["api_key"] == "sk-spare"
    got = client.put(f"/api/llm-connections/{pid}", json={"name": "Spare vectors"})
    assert got.status_code == 200, got.text


def test_moving_the_legacy_embedding_keys_onto_unreadable_facts_still_asks(client, monkeypatch):
    """Gate finding 1, the format-1 door: a legacy key change onto a provider
    whose facts file is held names no space -- which is not "embeds nothing",
    so `PUT /config` asks rather than letting the role land there unasked."""
    _legacy_embedding(client)
    spare = _create(client, kind="openai_compatible", name="spare",
                    base_url="http://localhost:4321/v1").json()["id"]
    path = store.llm_connections.facts_path(spare)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    _facts_held(monkeypatch, spare)
    got = client.put("/api/config", json={"embeddings_connection_id": spare})
    assert got.status_code == 400, got.text
    assert got.json()["kind"] == "confirm_embedding"
