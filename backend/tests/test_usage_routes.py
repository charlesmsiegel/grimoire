"""Every generation route files a ledger row, and the rollup endpoints read it."""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from tests.llm_fakes import FailingOpenRouter, FakeOpenRouter, FakeOpenRouterComplete

USAGE = {"prompt_tokens": 900, "completion_tokens": 40, "cost_usd": 0.0042,
         "cost_basis": "billed", "model": "realm/opus", "connection": "Main",
         "provider": "openrouter"}


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return tmp_path


@pytest.fixture
def client(home):
    app = create_app()
    app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["Hel", "lo"])
    c = TestClient(app)
    c.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    return c


def _rows(home):
    return [json.loads(line)
            for path in sorted((home / "usage").glob("*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _campaign(client, name="Run"):
    wid = client.post("/api/worlds", json={"name": name}).json()["id"]
    return wid, client.post("/api/campaigns",
                            json={"name": name, "world": wid}).json()["id"]


def _scene(client, cid, title="S"):
    return client.post(f"/api/campaigns/{cid}/scenes", json={"title": title}).json()["id"]


def _use(app, fake):
    app.dependency_overrides[routes.get_llm] = lambda: fake
    return fake


# ---- the streamed turn ----
def test_a_chat_turn_files_what_the_provider_reported(client, home):
    _use(client.app, FakeOpenRouter(["Hel", "lo"], usage=USAGE))
    _, cid = _campaign(client)
    sid = _scene(client, cid)

    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hi"})
    assert r.status_code == 200

    row, = _rows(home)
    assert row["kind"] == "llm"
    assert row["task"] == "chat"
    assert row["campaign"] == cid
    assert row["scene"] == sid
    assert row["model"] == "realm/opus"
    assert row["prompt_tokens"] == 900
    assert row["completion_tokens"] == 40
    assert row["cost_usd"] == 0.0042
    assert row["status"] == "ok"
    assert row["duration_ms"] >= 0


def test_a_turn_the_provider_failed_is_still_a_row(client, home):
    _use(client.app, FailingOpenRouter(kind="rate_limit", message="slow down"))
    _, cid = _campaign(client)
    sid = _scene(client, cid)

    client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hi"})

    row, = _rows(home)
    assert row["status"] == "error"
    assert row["error"] == "rate_limit"


def test_the_opener_is_recorded_under_its_own_task(client, home):
    _use(client.app, FakeOpenRouter(["Once"], usage=USAGE))
    _, cid = _campaign(client)
    sid = _scene(client, cid)

    client.post(f"/api/campaigns/{cid}/scenes/{sid}/opener", json={"prompt": "go"})

    row, = _rows(home)
    assert row["task"] == "opener"
    assert row["scene"] == sid


def test_a_world_level_call_records_no_campaign_at_all(client, home):
    _use(client.app, FakeOpenRouterComplete("A quiet sort of menace."))
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters",
                json={"name": "Mara", "version": "default"})

    client.post(f"/api/worlds/{wid}/characters/mara/tagline/generate")

    row, = _rows(home)
    assert row["task"] == "tagline"
    assert "campaign" not in row, "a tagline belongs to a world, not a campaign"


# ---- the rollup endpoints ----
def test_the_summary_endpoint_rolls_the_ledger_up(client, home):
    _use(client.app, FakeOpenRouter(["Hel", "lo"], usage=USAGE))
    _, cid = _campaign(client)
    sid = _scene(client, cid)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hi"})

    body = client.get("/api/usage/summary").json()
    assert body["days"] == 30
    assert body["totals"]["calls"] == 1
    assert body["totals"]["total_tokens"] == 940
    assert body["totals"]["cost_usd"] == 0.0042
    assert [b["key"] for b in body["by_task"]] == ["chat"]
    assert body["session"]["calls"] == 1
    assert body["session_started"]


def test_the_summary_window_is_a_query_parameter(client):
    assert client.get("/api/usage/summary?days=7").json()["days"] == 7
    assert client.get("/api/usage/summary?days=0").json()["days"] == 1


def test_the_campaign_endpoint_reports_only_that_campaigns_calls(client, home):
    _use(client.app, FakeOpenRouter(["ok"], usage=USAGE))
    _, one = _campaign(client, "One")
    _, two = _campaign(client, "Two")
    client.post(f"/api/campaigns/{one}/scenes/{_scene(client, one)}/chat",
                json={"content": "hi"})
    client.post(f"/api/campaigns/{two}/scenes/{_scene(client, two)}/chat",
                json={"content": "hi"})

    body = client.get(f"/api/campaigns/{one}/usage").json()
    assert body["campaign"] == one
    assert body["totals"]["calls"] == 1
    assert client.get("/api/usage/summary").json()["totals"]["calls"] == 2


def test_the_campaign_endpoint_404s_for_a_campaign_that_does_not_exist(client):
    assert client.get("/api/campaigns/nope/usage").status_code == 404


def test_a_library_that_has_never_generated_summarizes_to_zero(client):
    body = client.get("/api/usage/summary").json()
    assert body["totals"]["calls"] == 0
    assert body["by_day"] == []


async def test_a_cancelled_turn_is_recorded_as_aborted_not_as_a_failure(client, home):
    """The disconnect path: the socket is gone, so no frame can be sent — but
    the provider generated, and on a metered connection it was billed.

    Driven through `_chat_stream` directly, the way the other cancellation tests
    are: a `TestClient` that stops reading still drains the response, so it
    cannot produce the close this path exists for. One empty frame first, so the
    generator is suspended on a heartbeat yield when `aclose` arrives — closing
    one that has never yielded runs none of its body."""
    from tests.llm_fakes import StallingOpenRouter

    _, cid = _campaign(client)
    sid = _scene(client, cid)
    resp = routes.streaming._chat_stream(
        cid, sid, [{"role": "user", "content": "hi"}], {"kind": "openrouter", "model": "m"},
        StallingOpenRouter([""]))
    frames = resp.body_iterator
    assert await frames.__anext__() == ": heartbeat\n\n"
    await frames.aclose()

    row, = _rows(home)
    assert row["status"] == "aborted"
    assert row["task"] == "chat"
    assert client.get("/api/usage/summary").json()["totals"]["errors"] == 0


def test_an_absorb_step_the_budget_refuses_files_no_row(client, home, monkeypatch):
    """`_Budget.run` closes the coroutine unawaited when the clock is spent, so
    the dossier and audit calls never leave the process. A row for each would
    make an absorb that ran out of time look like one that made three free
    requests."""
    from grimoire import routes as routes_pkg

    class ClockEatingFake(FakeOpenRouterComplete):
        """Answers instantly but advances the (faked) absorb clock, so the
        budget is exhausted after the extraction without any real waiting."""

        def __init__(self, clock, cost, text, usage=None):
            super().__init__(text, usage=usage)
            self.clock, self.cost = clock, cost

        def _next(self, messages, conn):
            self.clock[0] += self.cost
            return super()._next(messages, conn)

    wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara", "version_name": "main"})
    store.sheets.write(cid, "characters", "mara", "medium", {"health": 3}, expected=None)
    sid = _scene(client, cid)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": "mara", "version": "main", "role": "npc"})
    store.scenes.append_message(cid, sid, "user", "Mara took a hit.")
    client.put("/api/config", json={"absorb_budget": "60"})

    clock = [0.0]
    monkeypatch.setattr(routes_pkg.scenes, "_clock", lambda: clock[0])
    _use(client.app, ClockEatingFake(
        clock, 90.0,
        '{"one_line": "o", "summary": "s", "keywords": [], "timeline_events": []}',
        usage=USAGE))

    client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")

    rows = _rows(home)
    assert [r["task"] for r in rows] == ["absorb"], (
        "only the extraction was ever sent")


def test_the_whole_chain_from_the_wire_to_the_ledger(client, home):
    """The one test with nothing faked between the SSE body and the row.

    Every other test here swaps in `FakeLLM` at the `get_llm` seam, so the
    adapter, `llm_usage`, the facade's stamping and the meter are each proved
    against a stand-in for the next one. This drives a real `OpenRouterClient`
    over a mock transport instead, which is the only way the request field, the
    chunk the block rides on, and the row it becomes are checked as one thing.
    """
    import httpx
    from grimoire.llm import LLMClient
    from grimoire.openrouter import OpenRouterClient

    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, text=(
            'data: {"model":"realm/opus-2026-08","choices":[{"delta":{"content":"Hi"}}]}\n\n'
            'data: {"model":"realm/opus-2026-08","choices":[],'
            '"usage":{"prompt_tokens":1204,"completion_tokens":37,"cost":0.00915}}\n\n'
            "data: [DONE]\n\n"))

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    real = LLMClient(openrouter=OpenRouterClient(http=http), timeout=0, retries=0)
    client.app.dependency_overrides[routes.get_llm] = lambda: real

    _, cid = _campaign(client)
    sid = _scene(client, cid)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hi"})
    assert r.status_code == 200

    assert sent["usage"] == {"include": True}, "the block has to be asked for"
    row, = _rows(home)
    assert row["prompt_tokens"] == 1204
    assert row["completion_tokens"] == 37
    assert row["cost_usd"] == 0.00915
    assert row["cost_basis"] == "billed"
    # The dated snapshot the provider answered with, not the alias the scene
    # asked for -- a ledger that names the wrong one cannot be reconciled.
    assert row["model"] == "realm/opus-2026-08"
    assert row["provider"] == "openrouter"

    body = client.get(f"/api/campaigns/{cid}/usage").json()
    assert body["totals"]["cost_usd"] == 0.00915
    assert body["totals"]["total_tokens"] == 1241
    assert body["totals"]["unpriced_calls"] == 0


def test_the_scenario_extraction_files_a_world_level_row(client, home):
    """Main's scenario import (#217) landed while this branch was open, and the
    usage guard caught it unmetered. This pins the row itself, since the guard
    only proves the holder was passed, not that anything files it."""
    import io

    _use(client.app, FakeOpenRouterComplete("{}", usage=USAGE))
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]

    r = client.post(f"/api/worlds/{wid}/scenario/parse",
                    files={"file": ("card.json", io.BytesIO(b'{"name": "Saltmarch"}'),
                                    "application/json")},
                    data={"format": "json"})
    assert r.status_code == 200

    row, = _rows(home)
    assert row["task"] == "scenario"
    assert "campaign" not in row, "a scenario card is imported into a world"
    assert row["prompt_tokens"] == 900
