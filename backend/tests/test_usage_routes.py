"""Every generation route files a ledger row, and the rollup endpoints read it."""

from __future__ import annotations

import importlib
import json
from datetime import date, timedelta

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


def _restamp(text: str, stamp: str) -> str:
    """A scene file with its `created` frontmatter moved to `stamp`."""
    return "\n".join(f"created: {stamp}" if line.startswith("created:") else line
                      for line in text.splitlines()) + "\n"


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
    a refused call never leaves the process. A row for it would make an absorb
    that ran out of time look like one that made a free request.

    Refused is not cancelled: a phase the clock stops mid-flight WAS sent and
    does file a row. Only the never-sent case is covered here."""
    from grimoire import routes as routes_pkg

    class ClockEatingFake(FakeOpenRouterComplete):
        """Answers instantly, optionally advancing the (faked) absorb clock, so
        budget arithmetic is exercised without any real waiting."""

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
        clock, 0.0,
        '{"one_line": "o", "summary": "s", "keywords": [], "timeline_events": []}',
        usage=USAGE))

    # The budget is eaten by the dossier phase's OWN reads, not by an earlier
    # phase. The phases run concurrently, so "the extraction spent the clock
    # first" is no longer a thing that can be arranged -- and this is the more
    # direct setup anyway: the gap between the loop's budget check and the call
    # is exactly where a refusal happens.
    real_read = store.dossiers.read

    def slow_read(*a, **kw):
        clock[0] += 70.0
        return real_read(*a, **kw)

    monkeypatch.setattr(store.dossiers, "read", slow_read)

    client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")

    tasks = [r["task"] for r in _rows(home)]
    assert "dossier" not in tasks, "a call the budget refused must file no row"
    assert "absorb" in tasks, "the extraction was sent and must file one"


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


# ---- the cache split reaches the ledger and the rollup (#148) ----

def test_a_cached_turn_files_its_split_and_the_rollup_keeps_it_out_of_the_total(client, home):
    """End to end: what the provider said about caching survives the adapter,
    the meter and the row, and a rollup reports it as a breakdown rather than
    folding it into a total that already counts it once."""
    _use(client.app, FakeOpenRouter(["Hel", "lo"],
                                    usage={**USAGE, "prompt_tokens": 5000,
                                           "cache_read_tokens": 4096,
                                           "cache_write_tokens": 512}))
    _, cid = _campaign(client)
    sid = _scene(client, cid)

    client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hi"})

    row, = _rows(home)
    assert row["prompt_tokens"] == 5000
    assert row["cache_read_tokens"] == 4096
    assert row["cache_write_tokens"] == 512

    totals = client.get("/api/usage/summary").json()["totals"]
    assert totals["cache_read_tokens"] == 4096
    assert totals["cache_write_tokens"] == 512
    # 5000 prompt + 40 completion. The 4096 already sits inside the 5000.
    assert totals["total_tokens"] == 5040


def test_a_turn_with_no_caching_leaves_the_rollups_cache_columns_at_zero(client, home):
    _use(client.app, FakeOpenRouter(["Hel", "lo"], usage=USAGE))
    _, cid = _campaign(client)
    sid = _scene(client, cid)

    client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hi"})

    row, = _rows(home)
    assert "cache_read_tokens" not in row      # absent per row: nobody said
    totals = client.get("/api/usage/summary").json()["totals"]
    assert totals["cache_read_tokens"] == 0    # present per bucket: nothing cached


# ---- the per-scene breakdown and the budget (#153) ----
def test_the_scene_endpoint_reports_that_scenes_turns(client, home):
    _use(client.app, FakeOpenRouter(["ok"], usage=USAGE))
    _, cid = _campaign(client)
    one, two = _scene(client, cid, "One"), _scene(client, cid, "Two")
    client.post(f"/api/campaigns/{cid}/scenes/{one}/chat", json={"content": "hi"})
    client.post(f"/api/campaigns/{cid}/scenes/{two}/chat", json={"content": "hi"})

    body = client.get(f"/api/campaigns/{cid}/scenes/{one}/usage").json()
    assert body["scene"] == one
    assert body["totals"]["calls"] == 1
    assert body["totals"]["cost_usd"] == 0.0042
    turn, = body["turns"]
    assert turn["task"] == "chat"
    assert turn["model"] == "realm/opus"
    assert turn["total_tokens"] == 940
    assert [b["key"] for b in body["by_task"]] == ["chat"]


def test_the_scene_window_is_the_scenes_own_lifetime(client, home):
    """A scene played months ago must not report $0.00 because the default
    rollup window only reaches back thirty days."""
    _, cid = _campaign(client)
    sid = _scene(client, cid)
    meta = home / "campaigns" / cid / "scenes" / f"{sid}.md"
    # Sixty days back: outside the 30-day window every other rollup defaults
    # to, and well inside the ledger's own MAX_DAYS bound.
    old = (date.fromisoformat(store.usage._today()) - timedelta(days=60)).isoformat()
    meta.write_text(_restamp(meta.read_text(encoding="utf-8"), f"{old}T09:00:00Z"),
                    encoding="utf-8")
    store.usage.record(task="chat", campaign=cid, scene=sid, cost_usd=1.25,
                       ts=f"{old}T10:00:00Z")

    body = client.get(f"/api/campaigns/{cid}/scenes/{sid}/usage").json()
    assert body["since"] == old
    assert body["totals"]["cost_usd"] == 1.25


def test_the_scene_endpoint_404s_for_a_scene_that_does_not_exist(client):
    _, cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/scenes/nope/usage").status_code == 404
    assert client.get("/api/campaigns/nope/scenes/nope/usage").status_code == 404


def test_a_campaign_starts_with_no_budget(client):
    _, cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/budget").json()["level"] == "off"


def test_setting_a_budget_answers_with_where_it_leaves_the_campaign(client, home):
    _use(client.app, FakeOpenRouter(["ok"], usage=USAGE))
    _, cid = _campaign(client)
    client.post(f"/api/campaigns/{cid}/scenes/{_scene(client, cid)}/chat",
                json={"content": "hi"})

    body = client.put(f"/api/campaigns/{cid}/budget",
                      json={"budget_usd": 10, "budget_period": "monthly"}).json()
    assert body["limit_usd"] == 10.0
    assert body["spent_usd"] == 0.0042
    assert body["level"] == "ok"
    assert client.get(f"/api/campaigns/{cid}/budget").json()["limit_usd"] == 10.0


def test_a_budget_is_stored_on_the_campaign_not_in_the_ledger(client, home):
    _, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/budget", json={"budget_usd": 12.5})

    meta = (home / "campaigns" / cid / "campaign.md").read_text(encoding="utf-8")
    assert "budget_usd: 12.50" in meta
    assert "budget_period: monthly" in meta


def test_clearing_a_budget_removes_it_rather_than_zeroing_it(client, home):
    _, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/budget", json={"budget_usd": 12.5})

    assert client.put(f"/api/campaigns/{cid}/budget",
                      json={"budget_usd": None}).json()["level"] == "off"
    meta = (home / "campaigns" / cid / "campaign.md").read_text(encoding="utf-8")
    assert "budget_usd" not in meta
    assert "budget_period" not in meta


def test_a_campaign_over_its_budget_says_so(client, home):
    _, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/budget", json={"budget_usd": 1})
    store.usage.record(task="chat", campaign=cid, cost_usd=1.4)

    body = client.get(f"/api/campaigns/{cid}/budget").json()
    assert body["level"] == "over"
    assert body["fraction"] > 1


def test_a_budget_smaller_than_a_cent_is_no_budget_at_all(client, home):
    """The file holds dollars to the cent, so a third of one would be written
    as 0.00 and read straight back as "none" -- a campaign whose settings say
    it is capped and whose behaviour says it is not."""
    _, cid = _campaign(client)
    assert client.put(f"/api/campaigns/{cid}/budget",
                      json={"budget_usd": 0.001}).json()["level"] == "off"
    assert "budget_usd" not in (home / "campaigns" / cid / "campaign.md").read_text(
        encoding="utf-8")


def test_the_budget_endpoints_404_for_a_campaign_that_does_not_exist(client):
    assert client.get("/api/campaigns/nope/budget").status_code == 404
    assert client.put("/api/campaigns/nope/budget",
                      json={"budget_usd": 5}).status_code == 404


def test_another_campaigns_spend_is_not_charged_to_this_budget(client, home):
    _use(client.app, FakeOpenRouter(["ok"], usage=USAGE))
    _, one = _campaign(client, "One")
    _, two = _campaign(client, "Two")
    client.put(f"/api/campaigns/{one}/budget", json={"budget_usd": 0.05})
    client.post(f"/api/campaigns/{two}/scenes/{_scene(client, two)}/chat",
                json={"content": "hi"})

    assert client.get(f"/api/campaigns/{one}/budget").json()["level"] == "ok"


def test_a_renamed_scene_keeps_the_cost_it_ran_up_under_its_old_id(client, home):
    """The end-to-end shape of the ledger's place in `scene_refs.repoint`: a
    real rename through the real route, and the scene's spend still there."""
    _use(client.app, FakeOpenRouter(["ok"], usage=USAGE))
    _, cid = _campaign(client)
    sid = _scene(client, cid, "Untitled")
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hi"})

    renamed = client.put(f"/api/campaigns/{cid}/scenes/{sid}",
                         json={"title": "The Tideflats"}).json()["id"]
    assert renamed != sid, "the rename has to actually move the file"

    body = client.get(f"/api/campaigns/{cid}/scenes/{renamed}/usage").json()
    assert body["totals"]["calls"] == 1
    assert body["totals"]["cost_usd"] == 0.0042
