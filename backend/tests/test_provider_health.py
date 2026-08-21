"""Provider health: the registry, the on-demand check, and the passive half
that keeps the answer current between checks (#146).

The three layers are tested separately on purpose. The registry is a data
structure and needs no app; the observer is a facade concern and needs a real
`LLMClient` (a route test cannot reach it — injecting a fake at
`routes.get_llm` replaces the very code that reports); and the routes need
neither, because by the time they run the verdict is already a dict.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import health, routes
from grimoire.llm import LLMClient
from grimoire.llm_errors import LLMError
from grimoire.main import create_app
from tests.llm_fakes import FakeCatalog


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


def _conn(**fields):
    return {"id": "openrouter", "kind": "openrouter", "name": "OpenRouter",
            "model": "m", "api_key": "k", "base_url": "", **fields}


# ---- the registry ----
def test_an_unseen_connection_is_unknown_not_broken():
    """The distinction the status dot is built on: "nothing has been observed"
    and "the provider refused us" must not draw the same colour."""
    assert health.ProviderHealth().status("openrouter") == {
        "state": "unknown", "kind": "", "detail": "", "at": ""}


def test_a_success_is_recorded_with_a_timestamp():
    registry = health.ProviderHealth()
    status = registry.record(_conn())

    assert (status["state"], status["kind"], status["detail"]) == ("ok", "", "")
    assert status["at"]
    assert registry.status("openrouter") == status


def test_a_failure_records_the_kind_and_detail_the_provider_gave():
    registry = health.ProviderHealth()
    registry.record(_conn(), LLMError("auth", "No auth credentials found"))

    assert registry.status("openrouter") == {
        "state": "error", "kind": "auth", "detail": "No auth credentials found",
        "at": registry.status("openrouter")["at"]}


def test_the_latest_outcome_replaces_the_one_before_it():
    """A status dot answers "how is it now", so a recovered connection has to
    go green again rather than keeping the failure it recovered from."""
    registry = health.ProviderHealth()
    registry.record(_conn(), LLMError("network", "connection refused"))
    registry.record(_conn())

    assert registry.status("openrouter")["state"] == "ok"


def test_connections_are_recorded_independently():
    registry = health.ProviderHealth()
    registry.record(_conn(id="openrouter"), LLMError("auth", "bad key"))
    registry.record(_conn(id="local", kind="openai_compatible"))

    assert registry.status("openrouter")["state"] == "error"
    assert registry.status("local")["state"] == "ok"


def test_a_connection_with_no_id_is_not_filed_under_a_shared_slot():
    """The facade is handed connection dicts, and a hand-built one has no id.
    Filing those under "" would pool every anonymous connection's verdict."""
    registry = health.ProviderHealth()
    registry.record({"kind": "openrouter"}, LLMError("auth", "bad key"))

    assert registry.status("")["state"] == "unknown"


def test_forget_drops_a_verdict():
    registry = health.ProviderHealth()
    registry.record(_conn(), LLMError("auth", "bad key"))
    registry.forget("openrouter")

    assert registry.status("openrouter")["state"] == "unknown"


def test_a_returned_status_cannot_be_edited_from_outside():
    """Both readers hand this straight into a response body, and a caller that
    mutated one would be editing the registry's own record."""
    registry = health.ProviderHealth()
    registry.record(_conn())
    registry.status("openrouter")["state"] = "error"

    assert registry.status("openrouter")["state"] == "ok"


# ---- the passive half: the facade reports what generation actually did ----
class _Provider:
    """Streams a word, or fails, depending on how it was built."""

    def __init__(self, error=None):
        self.error = error

    async def stream(self, messages, *args, **kwargs):
        yield "hi"
        if self.error is not None:
            raise self.error


def _client(provider, observer, **kwargs):
    return LLMClient(openrouter=provider, claude=provider, openai_compatible=provider,
                     observer=observer, retries=0, **kwargs)


async def test_a_finished_generation_reports_success():
    seen = []
    client = _client(_Provider(), lambda conn, err: seen.append((conn["id"], err)))

    assert [c async for c in client.stream([], _conn())] == ["hi"]
    assert seen == [("openrouter", None)]


async def test_a_failed_generation_reports_the_error_it_failed_with():
    seen = []
    boom = LLMError("auth", "bad key")
    client = _client(_Provider(boom), lambda conn, err: seen.append((conn["id"], err)))

    with pytest.raises(LLMError):
        [c async for c in client.stream([], _conn())]
    assert seen == [("openrouter", boom)]


async def test_a_fallback_is_reported_under_its_own_connection():
    """Two connections, two verdicts: a primary that failed and a fallback that
    served are different facts about different providers, and the Connections
    page shows each beside the connection it belongs to."""
    seen = []
    fallback = _conn(id="local", kind="openai_compatible")
    calls = {"n": 0}

    class _FirstFails:
        async def stream(self, messages, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise LLMError("network", "connection refused")
                yield  # pragma: no cover - unreachable, makes this a generator
            yield "hi"

    client = LLMClient(openrouter=_FirstFails(), claude=_FirstFails(),
                       openai_compatible=_FirstFails(), retries=0,
                       fallback=lambda: fallback,
                       observer=lambda conn, err: seen.append(
                           (conn["id"], err.kind if err else None)))

    assert [c async for c in client.stream([], _conn())] == ["hi"]
    assert seen == [("openrouter", "network"), ("local", None)]


async def test_an_observer_that_raises_cannot_fail_a_working_generation():
    """The one outcome a status feature must never be able to produce."""
    def explode(conn, err):
        raise RuntimeError("registry is on fire")

    client = _client(_Provider(), explode)
    assert [c async for c in client.stream([], _conn())] == ["hi"]


async def test_an_observer_that_raises_cannot_replace_a_providers_error():
    def explode(conn, err):
        raise RuntimeError("registry is on fire")

    client = _client(_Provider(LLMError("rate_limit", "slow down")), explode)
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], _conn())]
    assert exc.value.kind == "rate_limit"


async def test_a_generation_the_caller_walks_away_from_reports_nothing():
    """Neither a success nor a failure: nobody learned anything about the
    provider, and recording "ok" for a turn that was abandoned mid-stream
    would be a claim the stream never made."""
    seen = []
    client = _client(_Provider(), lambda conn, err: seen.append(err))

    agen = client.stream([], _conn())
    assert await agen.__anext__() == "hi"
    await agen.aclose()

    assert seen == []


# ---- the on-demand half: the check route ----
def test_check_reports_a_healthy_connection(client):
    fake = FakeCatalog()
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})

    r = client.post("/api/llm-connections/openrouter/health")

    assert r.status_code == 200
    body = r.json()
    assert (body["ok"], body["kind"], body["detail"]) == (True, "", "")
    assert body["checked_at"]


def test_check_reports_a_refused_key_without_failing_the_request(client):
    """200 with `ok: false`, not 502. The provider failed; the question "tell
    me about that connection" did not, and a 502 would put the frontend's
    error banner in front of the answer the reader asked for."""
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeCatalog(health_error=LLMError("auth", "No auth credentials found"))
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-dead"})

    r = client.post("/api/llm-connections/openrouter/health")

    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert (r.json()["kind"], r.json()["detail"]) == ("auth", "No auth credentials found")


def test_check_short_circuits_a_connection_with_no_credential(client):
    """`_require_connection` already refuses to generate on one of these
    without a network call; a check that made the doomed request anyway would
    teach the reader nothing the missing key does not."""
    fake = FakeCatalog()
    client.app.dependency_overrides[routes.get_llm] = lambda: fake

    r = client.post("/api/llm-connections/openrouter/health")

    assert (r.status_code, r.json()["ok"], r.json()["kind"]) == (200, False, "missing_key")
    assert fake.checked == []


def test_check_404s_for_a_connection_that_does_not_exist(client):
    assert client.post("/api/llm-connections/nope/health").status_code == 404


def test_check_asks_about_the_connection_named_in_the_path(client):
    fake = FakeCatalog()
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    cid = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Local", "base_url": "http://127.0.0.1:8080/v1",
    }).json()["id"]

    client.post(f"/api/llm-connections/{cid}/health")

    assert [(c["id"], c["base_url"]) for c in fake.checked] == [
        (cid, "http://127.0.0.1:8080/v1")]


def test_a_claude_connection_is_checkable_though_it_has_no_catalog(client):
    """The kind with no `/models` endpoint still has a health answer — #146 and
    #149 are about different capabilities and the route split has to agree."""
    fake = FakeCatalog()
    client.app.dependency_overrides[routes.get_llm] = lambda: fake

    assert client.post("/api/llm-connections/claude/health").json()["ok"] is True
    assert client.post("/api/llm-connections/claude/models/refresh").status_code == 400


# ---- what the rest of the app sees ----
def test_a_check_shows_up_in_the_config_the_status_bar_reads(client):
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeCatalog(health_error=LLMError("rate_limit", "slow down"))
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    client.post("/api/llm-connections/openrouter/health")

    reported = client.get("/api/config").json()["health"]

    assert (reported["state"], reported["kind"], reported["detail"]) == (
        "error", "rate_limit", "slow down")


def test_config_reports_unknown_before_anything_has_been_observed(client):
    """A fresh app has no verdict, and must not invent one — in either
    direction. The dot says "not checked", the Connections page says so in
    words, and neither performs a network call to find out."""
    assert client.get("/api/config").json()["health"]["state"] == "unknown"


def test_config_reports_no_health_when_there_is_no_active_connection(client):
    # The read first: on a store this new, it is what runs the connections
    # migration, and the migration seeds an active connection when it finds
    # none. Clearing before that has run is undone by it.
    assert client.get("/api/config").json()["health"] is not None
    client.put("/api/config", json={"active_connection_id": ""})

    assert client.get("/api/config").json()["health"] is None


def test_a_connections_detail_carries_its_own_verdict(client):
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeCatalog(health_error=LLMError("network", "connection refused"))
    cid = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Local", "base_url": "http://127.0.0.1:8080/v1",
    }).json()["id"]
    client.post(f"/api/llm-connections/{cid}/health")

    assert client.get(f"/api/llm-connections/{cid}").json()["health"]["kind"] == "network"
    # ...and the connection nobody checked is still unknown, not inheriting it
    assert client.get("/api/llm-connections/openrouter").json()["health"]["state"] == "unknown"


def test_editing_a_connection_clears_the_verdict_its_old_settings_earned(client):
    """The reader who just fixed a key is watching to see whether the fix took;
    reporting the old failure back at them is the one answer that cannot be
    right."""
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeCatalog(health_error=LLMError("auth", "bad key"))
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-dead"})
    client.post("/api/llm-connections/openrouter/health")
    assert client.get("/api/llm-connections/openrouter").json()["health"]["state"] == "error"

    body = client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-fresh"}).json()

    assert body["health"]["state"] == "unknown"
    assert client.get("/api/config").json()["health"]["state"] == "unknown"


def test_a_recreated_connection_does_not_inherit_the_dead_ones_failure(client):
    """Ids are slugs and slugs are reusable, so "delete and make another one
    with the same name" lands on the same key in the registry."""
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeCatalog(health_error=LLMError("network", "connection refused"))
    cid = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Local", "base_url": "http://old/v1",
    }).json()["id"]
    client.post(f"/api/llm-connections/{cid}/health")
    client.delete(f"/api/llm-connections/{cid}")

    again = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Local", "base_url": "http://new/v1",
    }).json()["id"]

    assert again == cid  # the freed slug — the whole point of this test
    assert client.get(f"/api/llm-connections/{again}").json()["health"]["state"] == "unknown"
