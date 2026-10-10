"""Cross-campaign attribution (roadmap 01h-C5, slice 01h-S6).

`attribute(claims)` groups texts by who pays for them, and
`embed_groups_sync` embeds each group as its own call: one row per group
with its campaign, no request body that mixes groups, one deadline for the
whole run, the unattributed group first, a `bad_response` group that the run
carries on past, and any other failure stopping the run with the rest
`not_sent` and filed nowhere. Every name below is invented.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from grimoire import embeddings
from grimoire.store import config, embed_space, llm_connections, logs, usage
from grimoire.store import inference_keys as keys
from grimoire.store.inference import embed
from grimoire.store.inference.embed import EmbedGroup, GroupResult


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    logs.forget_file_sizes()
    logs.apply_level("debug")
    yield tmp_path
    logs.forget_file_sizes()
    logs.apply_level("info")


@pytest.fixture
def space() -> dict:
    conn = llm_connections.create_connection(
        "openai_compatible", "Saltmarch Vectors", base_url="https://vectors.example/v1",
        api_key="sk-fake-0001", model="", post_process="none")
    config.write_config(**{keys.FORMAT_KEY: "2",
                           keys.role_key("embedding", "provider"): conn,
                           keys.role_key("embedding", "model"): "embed-1"})
    got = embed_space.endpoint()
    assert got is not None
    return got


def _client(fail: dict[str, int] | None = None):
    """A recording endpoint; a request carrying a text in `fail` answers that
    status."""
    seen: list[list[str]] = []
    fail = fail or {}

    def handler(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["input"]
        seen.append(inputs)
        for text, status in fail.items():
            if text in inputs:
                return httpx.Response(status, json={"error": "no"})
        return httpx.Response(200, json={
            "data": [{"index": i, "embedding": [float(len(t)), 1.0]}
                     for i, t in enumerate(inputs)],
            "usage": {"prompt_tokens": len(inputs)}})

    return embeddings.EmbeddingsClient(httpx.Client(transport=httpx.MockTransport(handler))), seen


def _rows() -> list[dict]:
    return list(usage.calls(days=1))


# ---- attribute -------------------------------------------------------------------

def test_a_text_one_campaign_claims_is_charged_to_it():
    got = embed.attribute([("saltmarch", "Mara"), ("realm", "Winifred"),
                           ("saltmarch", "Seraphine"), ("saltmarch", "Mara")])
    assert got == [EmbedGroup("realm", texts=("Winifred",)),
                   EmbedGroup("saltmarch", texts=("Mara", "Seraphine"))]


def test_a_shared_or_unclaimed_text_is_unattributed_and_goes_first():
    got = embed.attribute([("saltmarch", "Mara"), ("realm", "the harbour"),
                           ("saltmarch", "the harbour"), ("", "the Realm's charter"),
                           ("realm", "Winifred")])
    assert [g.campaign for g in got] == ["", "realm", "saltmarch"]
    assert got[0].texts == ("the harbour", "the Realm's charter")
    assert got[1].texts == ("Winifred",) and got[2].texts == ("Mara",)


def test_attribution_is_deterministic():
    claims = [("b", "x"), ("a", "y"), ("c", "x"), ("a", "z")]
    assert embed.attribute(claims) == embed.attribute(list(claims))
    assert embed.attribute([]) == []


# ---- embed_groups_sync -----------------------------------------------------------

GROUPS = [EmbedGroup("", texts=("the harbour",)),
          EmbedGroup("realm", scene="dusk", texts=("Winifred", "the gate")),
          EmbedGroup("saltmarch", texts=("Mara",))]


def test_one_row_per_group_with_its_campaign_and_no_mixed_request(space):
    client, seen = _client()
    got = embed.embed_groups_sync("semantic-search", GROUPS, space=space, client=client,
                                  run_id="run-sync-1", cached=5, uncached=4)
    assert [r.error for r in got] == ["", "", ""]
    assert got[1].vectors == [[8.0, 1.0], [8.0, 1.0]]
    assert seen == [["the harbour"], ["Winifred", "the gate"], ["Mara"]]
    rows = _rows()
    assert [(r.get("campaign", ""), r.get("scene", ""), r["prompt_tokens"]) for r in rows] == [
        ("", "", 1), ("realm", "dusk", 2), ("saltmarch", "", 1)]
    assert all(r["run_id"] == "run-sync-1" for r in rows)
    lines = [r for r in logs.read(level="debug")["rows"] if r.get("module") == "embed"]
    assert len(lines) == 3
    # The run's hits and misses ride the first call's line alone (the log
    # reads newest first).
    assert [(ln.get("cached"), ln.get("uncached")) for ln in reversed(lines)] == [
        (5, 4), (None, None), (None, None)]


def test_a_bad_response_group_fails_alone(space):
    client, seen = _client({"Winifred": 400})
    got = embed.embed_groups_sync("semantic-search", GROUPS, space=space, client=client)
    assert [r.error for r in got] == ["", "bad_response", ""]
    assert got[1].vectors is None and got[2].vectors == [[4.0, 1.0]]
    assert len(seen) == 3
    assert [r["status"] for r in _rows()] == ["ok", "error", "ok"]


@pytest.mark.parametrize(("status", "kind"), [(429, "rate_limit"), (401, "auth"),
                                              (503, "network")])
def test_any_other_failure_stops_the_run(space, status, kind):
    client, seen = _client({"Winifred": status})
    got = embed.embed_groups_sync("semantic-search", GROUPS, space=space, client=client)
    assert got[1] == GroupResult(None, kind)
    assert got[2] == GroupResult(None, "not_sent")
    assert len(seen) == 2
    assert len(_rows()) == 2              # nothing filed for the group never sent


def test_a_width_ignoring_endpoint_stops_the_run(space):
    def narrow(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["input"]
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [1.0, 0.0, 0.0]}
                                                  for i in range(len(inputs))]})
    client = embeddings.EmbeddingsClient(httpx.Client(transport=httpx.MockTransport(narrow)))
    wide = {**space, "options": embeddings.wire.EmbedOptions(dimensions=2)}
    wide["space"] = space["space"] + "\0embopt1:" + wide["options"].digest()
    got = embed.embed_groups_sync("semantic-search", GROUPS, space=wide, client=client)
    assert [r.error for r in got] == ["missing_key", "not_sent", "not_sent"]


def test_one_deadline_covers_every_group(space, monkeypatch):
    handed: list[float] = []
    real = embed.embed_sync

    def spy(*args, **kwargs):
        handed.append(kwargs["deadline"])
        return real(*args, **kwargs)

    monkeypatch.setattr(embed, "embed_sync", spy)
    client, _ = _client()
    before = time.monotonic()
    embed.embed_groups_sync("semantic-search", GROUPS, space=space, client=client)
    assert len(handed) == 3 and len(set(handed)) == 1
    assert before + embeddings.TIMEOUT <= handed[0] <= time.monotonic() + embeddings.TIMEOUT


def test_a_spent_deadline_sends_and_files_nothing(space):
    client, seen = _client()
    got = embed.embed_groups_sync("semantic-search", GROUPS, space=space, client=client,
                                  deadline=time.monotonic() - 1)
    assert [r.error for r in got] == ["not_sent"] * 3
    assert seen == [] and _rows() == []


def test_on_group_is_called_in_order_as_each_lands(space):
    client, seen = _client()
    calls: list[tuple[str, int]] = []

    def save(group: EmbedGroup, result: GroupResult) -> None:
        calls.append((group.campaign, len(seen)))

    embed.embed_groups_sync("semantic-search", GROUPS, space=space, client=client,
                            on_group=save)
    # Each before the next group's request.
    assert calls == [("", 1), ("realm", 2), ("saltmarch", 3)]


def test_on_group_raising_stops_the_run(space):
    client, seen = _client()

    def boom(group: EmbedGroup, result: GroupResult) -> None:
        if group.campaign == "realm":
            raise OSError("the cache is held")

    got = embed.embed_groups_sync("semantic-search", GROUPS, space=space, client=client,
                                  on_group=boom)
    assert [r.error for r in got] == ["", "", "not_sent"]
    assert len(seen) == 2


def test_an_unregistered_task_is_refused_before_anything(space):
    client, seen = _client()
    with pytest.raises(ValueError):
        embed.embed_groups_sync("chat", GROUPS, space=space, client=client)
    assert seen == []
