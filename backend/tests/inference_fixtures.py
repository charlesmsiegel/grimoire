"""Model-settings scaffolding the decide conversions' route tests share.

Slice F moves three calls onto `decide()` and the Decision role -- the
scene-break check, the voice-drift judge and the speaker pick -- and each
proves the same three things against a format-2 store: the Decision role is
what serves it, a decide-only Decision model is answered natively (slice H),
falling to a generating role fallback when that fails, and a model that can
neither generate nor decide natively is refused by the seam. The store those
tests stand on is built here once, through the HTTP API, rather than in each
suite.
"""

from __future__ import annotations

from pathlib import Path

import grimoire.store as store
from grimoire.store import config, inference_keys, locks
from grimoire.store.inference import migrate

#: What a store born under `GRIMOIRE_TEST_BIRTH=upgraded-default` holds besides
#: its defaults: the format marker and the default Primary (what migrating a
#: fresh format-1 library yields). Held equal to `config.birth_fields()` by
#: `test_a_test_store_is_born_an_upgraded_default_library`.
UPGRADED_DEFAULT: dict[str, str] = {
    inference_keys.FORMAT_KEY: inference_keys.CURRENT_FORMAT,
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


def put_settings(client, body: dict) -> None:
    """`PUT /api/inference/settings` with `body`, which must be accepted."""
    got = client.put("/api/inference/settings", json=body)
    assert got.status_code == 200, got.text


def format2(client) -> None:
    """A format-2 store: the Primary role on the seeded `openrouter` provider,
    keyed, at `vendor/active`, and a keyed `spare` provider (a test that uses
    it names its model, `vendor/spare`). A store born legacy is migrated
    first; one born at format 2 is already current. Calling it again changes
    nothing: `spare` is created once."""
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
