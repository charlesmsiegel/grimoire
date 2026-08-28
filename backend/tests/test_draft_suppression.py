"""The suppression invariant for auxiliary generation (#189): an ephemeral
draft may compute anything and mutate nothing.

`test_draft_runs.py` pins the class contract from the outside (the opener
writes nothing to the transcript, results are held on the run and reaped).
This suite pins the same invariant from the inside, the way #189 asked: the
canonical campaign-state writers are replaced with functions that fail the
test, and the draft paths are driven over them. What #189 called "mechanics,
drift, extraction and state writes" all reach disk through this list, so a
future draft kind that grows a side effect fails here by name.

**Extend the list when a new writer lands.** A mechanics module write, a new
drift record, a new extraction target -- if a store module gains a function
that persists campaign state, it belongs in `_WRITERS`, or this suite stops
guarding it.

What is deliberately NOT in the list: the prompt capture
(`routes/common._record_prompt`, an observability write that is on by
default), the usage ledger and the log file. None of them is campaign state,
and the first is the one write the opener path is documented to make.
"""

import pytest

import grimoire.store as store
from grimoire import routes
from tests.llm_fakes import FakeOpenRouter, FakeOpenRouterComplete

#: (module, function) pairs a draft must never reach: the extraction pipeline,
#: the transcript, play state, relationships, plot, dossiers and voice drift.
_WRITERS = [
    ("absorb", "apply_edits"),
    ("chronicle", "absorb"),
    ("playstate", "write_state"),
    ("scenes", "append_message"),
    ("scenes", "append_messages"),
    ("scenes", "append_reply"),
    ("scenes", "mark_absorbed"),
    ("relationships", "set_feeling"),
    ("relationships", "set_bond"),
    ("plot", "set_movement"),
    ("dossiers", "write"),
    ("dossiers", "stage_edit"),
    ("voice_drift", "write"),
    ("voice_drift", "stage_edit"),
]


@pytest.fixture
def instrumented(client, monkeypatch):
    """The route-test app, with every canonical campaign-state writer armed."""
    def _boom(name):
        def fail(*a, **k):  # pragma: no cover - reaching this IS the failure
            raise AssertionError(f"ephemeral draft path called {name}")
        return fail

    for mod, fn in _WRITERS:
        monkeypatch.setattr(getattr(store, mod), fn, _boom(f"{mod}.{fn}"))
    return client


@pytest.fixture
def world(instrumented):
    instrumented.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = instrumented.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    instrumented.post(f"/api/worlds/{wid}/characters",
                      json={"name": "Mara", "version_name": "main"})
    return wid


@pytest.fixture
def campaign(instrumented, world):
    cid = instrumented.post("/api/campaigns",
                            json={"name": "Saltmarch", "world": world}).json()["id"]
    return world, cid


def _wait_landed(client, base, run_id, timeout=10.0):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"{base}/{run_id}").json()["run"]
        if run["state"] == "landed":
            return run
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} never landed")


def _first_run_id(body: str) -> str:
    import json
    for line in body.splitlines():
        if line.startswith("data: "):
            payload = json.loads(line[6:])
            if "run" in payload:
                return payload["run"]["id"]
    raise AssertionError("no leading run frame in the response")


def test_the_opener_computes_over_an_armed_store(instrumented, campaign):
    _world, cid = campaign
    sid = instrumented.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    instrumented.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouter(["The quay."])

    body = instrumented.post(f"/api/campaigns/{cid}/scenes/{sid}/opener",
                             json={"prompt": "begin"}).text

    # On the RUN, not the response: an armed writer firing inside the detached
    # generator still answers 200 with an error frame, so "did not raise" and
    # "status 200" both cover the very regression this suite exists to catch.
    run = instrumented.get(f"/api/campaigns/{cid}/scenes/{sid}/runs/"
                           f"{_first_run_id(body)}").json()["run"]
    assert run["state"] == "landed"
    assert run["error"] is None
    assert instrumented.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"] == []


def test_a_202_draft_computes_over_an_armed_store(instrumented, world):
    instrumented.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete("Keeps the tide-gate.")

    started = instrumented.post(f"/api/worlds/{world}/characters/mara/tagline/generate")

    assert started.status_code == 202
    landed = _wait_landed(instrumented, f"/api/worlds/{world}/runs",
                          started.json()["run"]["id"])
    assert landed["result"] == {"tagline": "Keeps the tide-gate."}


def test_a_campaign_scoped_draft_computes_over_an_armed_store(instrumented, campaign):
    _world, cid = campaign
    instrumented.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeOpenRouterComplete("Clipped. Never uses contractions.")

    started = instrumented.post(
        f"/api/campaigns/{cid}/characters/mara/voice-anchor/generate")

    assert started.status_code == 202
    landed = _wait_landed(instrumented, f"/api/campaigns/{cid}/runs",
                          started.json()["run"]["id"])
    assert "voice_anchor" in landed["result"]


def test_the_armed_writers_exist(client):
    """The list stays honest: a renamed writer must rename here too, not fall
    out of the guard as a silent getattr miss."""
    for mod, fn in _WRITERS:
        assert callable(getattr(getattr(store, mod), fn)), f"{mod}.{fn}"
