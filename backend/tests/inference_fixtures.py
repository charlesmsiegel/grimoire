"""Model-settings scaffolding the suite shares.

The small builders come first: `primary` (a keyed provider with the Primary
role on it), `embedding` (the confirmed Embedding-role write), `legacy_store`
and the `legacy_client` fixture (a format-1 library, for a test about the
legacy layout; `conftest.py` makes the fixture visible to every suite).

Then the decide conversions' scaffolding. Slice F moves three calls onto `decide()` and the Decision role -- the
scene-break check, the voice-drift judge and the speaker pick -- and each
proves the same three things against a format-2 store: the Decision role is
what serves it, a decide-only Decision model is answered natively (slice H),
falling to a generating role fallback when that fails, and a model that can
neither generate nor decide natively is refused by the seam. The store those
tests stand on is built here once, through the HTTP API, rather than in each
suite.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from grimoire.store import config, inference_keys, locks
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
from grimoire.store.inference import migrate
from grimoire.store.inference import settings as inference_settings
from tests.llm_fakes import FakeOpenRouter

#: What a store born under `GRIMOIRE_TEST_BIRTH=upgraded-default` holds besides
#: its defaults: the format marker, the retirement marker (a born store has no
#: legacy settings to retire, slice I N3) and the default Primary (what
#: migrating a fresh format-1 library yields). Held equal to
#: `config.birth_fields()` by
#: `test_a_test_store_is_born_an_upgraded_default_library`.
UPGRADED_DEFAULT: dict[str, str] = {
    inference_keys.FORMAT_KEY: inference_keys.CURRENT_FORMAT,
    inference_keys.RETIRED_KEY: "1",
    inference_keys.role_key("primary", "provider"): "openrouter",
    inference_keys.role_key("primary", "model"): config.DEFAULT_MODEL,
}


def legacy_store(home: Path | None = None) -> None:
    """Make the store under `home` (default `store.home()`) a format-1 library:
    a `config.md` with no format marker, which is how a store from before the
    new layout looks. Call it BEFORE anything else writes the store's
    settings -- the first read of a missing `config.md` creates one born at
    format 2, and a `config.md` that exists without the marker stays legacy;
    this one replaces whatever is there.

    Written in the `config_lock` hold that birth takes: a test whose client is
    already up calls this while the app's own first read (the backup ticker's,
    on a worker thread) may be materializing a born `config.md`, and without
    the hold that write can land after this one."""
    root = Path(home) if home is not None else store.home()
    root.mkdir(parents=True, exist_ok=True)
    with locks.config_lock():
        (root / "config.md").write_text("---\n---\n", encoding="utf-8")  # atomic-ok: test fixture


def unretired(home: Path | None = None) -> None:
    """Take the retirement marker off `config.md` under `home` (default
    `store.home()`), materializing the born file first: a format-2 store
    retirement has not reached yet -- one a C-H build migrated. The suite is
    born retired (`UPGRADED_DEFAULT`), so a test about the derivation before
    retirement persists it says so with this. In the `config_lock` hold, as
    `legacy_store` writes."""
    root = Path(home) if home is not None else store.home()
    config.read_config()
    path = root / "config.md"
    with locks.config_lock():
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        meta.pop(inference_keys.RETIRED_KEY, None)
        path.write_text(dump_frontmatter(meta, body), encoding="utf-8")  # atomic-ok: test fixture


def migrate_as_c_h() -> migrate.Status:
    """`migrate.ensure()` with retirement held off: the store as a C-H build
    migrated it -- format 2, the legacy keys and fields left in place, nothing
    retired. For a test about the migration's own product, or about a
    format-2 store retirement has not reached yet."""
    with mock.patch.object(migrate, "_retire", lambda *_args: None):
        return migrate.ensure()


@pytest.fixture
def legacy_client(monkeypatch, tmp_path) -> Iterator[TestClient]:
    """`conftest.client` on a format-1 store: `legacy_store` runs before the
    app is built, so nothing the app reads at startup sees a born store. For a
    test about the legacy layout only -- every other suite is born at format
    2."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    legacy_store(tmp_path)
    app = create_app()
    app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["Hel", "lo"])
    with TestClient(app) as c:
        yield c


def put_settings(client, body: dict) -> None:
    """`PUT /api/inference/settings` with `body`, which must be accepted."""
    got = client.put("/api/inference/settings", json=body)
    assert got.status_code == 200, got.text


def primary(client, model: str = "primary", *, provider: str = "openrouter",
            api_key: str = "sk-or-x", preset: str = "") -> None:
    """`provider` keyed with `api_key` (left as it is when `api_key` is
    empty), and the Primary role on it at `model`, wearing `preset`. Format 2:
    a provider names no model of its own, so the role's selection does."""
    if api_key:
        got = client.put(f"/api/llm-connections/{provider}", json={"api_key": api_key})
        assert got.status_code == 200, got.text
    selection = {"provider": provider, "model": model}
    if preset:
        selection["preset"] = preset
    put_settings(client, {"roles": {"primary": {"selection": selection}}})


def endpoint(client, name: str, *, base_url: str = "https://example.test/v1",
             api_key: str = "sk-test-endpoint") -> str:
    """An OpenAI-compatible provider called `name` at `base_url`, keyed; its id."""
    got = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": name, "base_url": base_url, "api_key": api_key})
    assert got.status_code == 200, got.text
    return got.json()["id"]


def primary_falling_back(client, selection: tuple[str, str], fallback: tuple[str, str]) -> None:
    """The Primary role on `selection` (provider, model), falling back to
    `fallback` (provider, model): the fallback a call carries, riding the
    resolution's chain (`wire.Chain.fallback`)."""
    put_settings(client, {"roles": {"primary": {
        "selection": {"provider": selection[0], "model": selection[1]},
        "fallback": {"provider": fallback[0], "model": fallback[1]}}}})


def embedding(provider: str, model: str) -> None:
    """The Embedding role on `provider` serving `model`, written through the
    store with the confirmation re-embedding asks for (nothing here sends
    what it would)."""
    inference_settings.write("global", "", {"roles": {"embedding": {
        "selection": {"provider": provider, "model": model}}}}, confirm_embedding=True)


def format2(client) -> None:
    """A format-2 store: the Primary role on the seeded `openrouter` provider,
    keyed, at `vendor/active`, and a keyed `spare` provider (a test that uses
    it names its model, `vendor/spare`). A store born legacy is migrated
    first; one born at format 2 is already current. Calling it again creates
    nothing new: `spare` is created once, and the key and the Primary are
    written again with the values they already hold."""
    got = client.put("/api/llm-connections/openrouter", json={"api_key": "sk-test-active"})
    assert got.status_code == 200, got.text
    if client.get(f"/api/llm-connections/{SPARE[0]}").status_code == 404:
        got = client.post("/api/llm-connections", json={"kind": "openrouter", "name": "spare",
                                                        "api_key": "sk-spare"})
        assert got.status_code == 200, got.text
        assert got.json()["id"] == SPARE[0]
    assert migrate.ensure().state == "done"
    put_settings(client, {"roles": {"primary": {
        "selection": {"provider": "openrouter", "model": "vendor/active"}}}})


#: Where `decide_only`'s generating fallback is: on another provider, or on
#: the decide-only model's own (one OpenRouter account, two of its models).
SPARE = ("spare", "vendor/spare")
SAME_PROVIDER = ("openrouter", "vendor/active")


def decide_only(client, *, fallback: bool, on: tuple[str, str] = SPARE) -> None:
    """The Decision role on an OpenRouter model whose catalog row says it only
    decides -- served natively (`decision_mode == "native"`) -- with a
    generating fallback (`on`: `spare` at `vendor/spare`, or `SAME_PROVIDER`)
    or none."""
    format2(client)
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    store.llm_connections.set_cached_models(
        "openrouter", [{"id": "vendor/decider", "outputs": ["decisions"]},
                       {"id": "vendor/active", "outputs": ["text"]}], rev)
    put_settings(client, {"roles": {"decision": {
        "selection": {"provider": "openrouter", "model": "vendor/decider"},
        "fallback": ({"provider": on[0], "model": on[1]} if fallback
                     else {"provider": ""})}}})


#: A model that both generates and decides natively (invented id): what a
#: native-first task asks its decisions endpoint first (spec 01c §4.2).
BOTH = ("openrouter", "vendor/both")


def generates_and_decides(client, *, fallback: bool, on: tuple[str, str] = SPARE) -> None:
    """The Decision role on an OpenRouter model whose catalog row says it
    generates AND decides natively (`generate` and `decide_native` both a
    catalog `yes`, and structured outputs listed), with a generating fallback
    (`on`) or none. Served structured, as every generating model is: only a
    task's `native_first` policy puts a native stage in front (spec 01c)."""
    format2(client)
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    store.llm_connections.set_cached_models(
        "openrouter", [{"id": BOTH[1], "outputs": ["text", "decisions"],
                        "params": ["temperature", "structured_outputs"]},
                       {"id": "vendor/active", "outputs": ["text"]}], rev)
    put_settings(client, {"roles": {"decision": {
        "selection": {"provider": BOTH[0], "model": BOTH[1]},
        "fallback": ({"provider": on[0], "model": on[1]} if fallback
                     else {"provider": ""})}}})


#: The model `neither` puts on the Decision role.
NEITHER = ("openrouter", "vendor/neither")


def neither(client) -> None:
    """The Decision role, with no fallback, on an OpenRouter model known
    unable to do either thing a decision needs (I8): its catalog `outputs`
    lacks `text` (`generate: no`), and the user has overridden
    `decide_native` to `no`. Both are known `no`, so a decide resolution is
    refused with 409 `incapable`.

    The override is what makes this model: the catalog alone cannot. An
    OpenRouter catalog row only ever says `decide_native: yes` (`decisions`
    in its outputs); a list without it says nothing (`capabilities.py`:
    "absence says nothing"), so a non-text row leaves `decide_native`
    unknown, and that model is served natively."""
    format2(client)
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    store.llm_connections.set_cached_models(
        "openrouter", [{"id": NEITHER[1], "outputs": ["image"]},
                       {"id": "vendor/active", "outputs": ["text"]}], rev)
    got = client.put("/api/llm-connections/openrouter/facts",
                     json={"model": NEITHER[1], "overrides": {"decide_native": "no"}})
    assert got.status_code == 200, got.text
    put_settings(client, {"roles": {"decision": {
        "selection": {"provider": NEITHER[0], "model": NEITHER[1]},
        "fallback": {"provider": ""}}}})


#: A decisions-only model on the OpenAI preset (invented id).
OPENAI_DECIDER = "gpt-test-decider"


def openai_decides_only(client) -> str:
    """The Decision role on an OpenAI-preset provider's decisions-only model,
    returning the provider's id. The preset's `always` says every model on it
    generates, and an OpenAI `/models` row states no outputs, so nothing but
    the user can say this model cannot: until they mark it -- the capability
    override `generate: no` (`PUT /llm-connections/{id}/facts`), which
    outranks the preset (`capabilities.resolve_caps`) -- it resolves
    structured, as spec 5.3 says. The callers mark it themselves."""
    format2(client)
    got = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Realm OpenAI",
        "base_url": "https://api.openai.com/v1", "api_key": "sk-test-openai"})
    assert got.status_code == 200, got.text
    conn_id = got.json()["id"]
    put_settings(client, {"roles": {"decision": {
        "selection": {"provider": conn_id, "model": OPENAI_DECIDER},
        "fallback": {"provider": ""}}}})
    return conn_id
