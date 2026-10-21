"""LLM failures preserve their HTTP status, retry advice and error envelope."""

from __future__ import annotations

import json

import pytest

from grimoire import routes
from grimoire.llm_errors import LLMError
from grimoire.routes import common
from tests import draft_runs as drafts
from tests.llm_fakes import FailingOpenRouter, FakeCatalog

# ---- the map itself ----


#: The taxonomy, written out a second time by hand. Deliberate duplication:
#: derived from `_LLM_STATUS` this would assert only that a dict equals itself.
#: Someone changing a status has to change it here too, which is the point.
TAXONOMY = [
    ("rate_limit", 429),
    ("timeout", 504),
    ("missing_key", 409),
    ("missing_dependency", 409),
    ("auth", 502),
    ("network", 502),
    ("bad_response", 502),
]


@pytest.mark.parametrize("kind,status", TAXONOMY)
def test_each_kind_maps_to_its_status(kind, status):
    assert common._llm_http_error(LLMError(kind, "x")).status_code == status


def test_an_unknown_kind_still_answers_rather_than_raising():
    """A KeyError on the failure path would replace the provider's error with
    our own, and the caller would never learn what actually went wrong."""
    assert common._llm_http_error(LLMError("something_new", "x")).status_code == 502


def test_the_body_envelope_is_unchanged():
    """The frontend reads `kind` out of the body and always has. A status
    change that also reshaped the body would break every consumer at once."""
    assert common._llm_http_error(LLMError("rate_limit", "slow down")).detail == \
        {"detail": "slow down", "kind": "rate_limit"}


def test_an_upstream_auth_failure_is_a_gateway_failure_not_a_401():
    """Deliberate, and the one place the issue's suggestion was not taken.

    This API authenticates nobody, so 401 would be a claim about the *caller's*
    credentials that is false, and RFC 9110 requires a `WWW-Authenticate` on one
    that there is no honest value for. A provider that refused our key is a
    gateway that could not serve the request. The `kind` still says which
    gateway failure it was, which is what the frontend reads.
    """
    assert common._llm_http_error(LLMError("auth", "bad key")).status_code == 502


@pytest.mark.parametrize("seconds,header", [
    (12.0, "12"),
    (0.2, "1"),          # rounded UP: a whole second the provider will accept
    (11.4, "12"),
    (None, None),
    (0.0, None),
    (-5.0, None),
    (float("inf"), None),
    (float("nan"), None),
])
def test_a_provider_window_becomes_a_retry_after(seconds, header):
    assert common._retry_after_header(seconds) == header


def test_only_a_rate_limit_carries_a_retry_after():
    """A window on a `network` failure is not advice about when to retry this
    request — nothing rate-limited it — and a `Retry-After` on a 502 would read
    as one."""
    assert common._llm_http_error(LLMError("network", "reset", 30.0)).headers is None
    assert common._llm_http_error(LLMError("timeout", "gave up", 30.0)).headers is None
    assert common._llm_http_error(LLMError("rate_limit", "slow", 30.0)).headers == \
        {"Retry-After": "30"}
    assert common._llm_http_error(LLMError("rate_limit", "slow")).headers is None


# ---- no route decides for itself ----


# ---- and the wire agrees ----
def _fixtures(client):
    """A world with a character and a campaign, plus a usable connection —
    between them enough to reach one route per module that catches an LLM
    failure."""
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara", "version_name": "main"})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    return wid, cid


def _paths(wid, cid):
    return [
        (f"/api/campaigns/{cid}/scene-suggestions", None),
        (f"/api/campaigns/{cid}/scene-intent", {"text": "a storm", "offscreen": False}),
        (f"/api/worlds/{wid}/characters/mara/tagline/generate", None),
        (f"/api/worlds/{wid}/characters/mara/voice-anchor/generate", None),
        (f"/api/campaigns/{cid}/characters/mara/voice-anchor/generate", None),
    ]


@pytest.mark.parametrize("kind,status", TAXONOMY)
def test_every_one_shot_generation_route_reports_the_kind_it_got(client, kind, status):
    """One assertion per route per kind: the status is applied at the call site,
    so a route that forgets the helper is only visible if it is asked."""
    wid, cid = _fixtures(client)
    for path, body in _paths(wid, cid):
        client.app.dependency_overrides[routes.get_llm] = \
            lambda: FailingOpenRouter([], kind, "upstream said no")
        r = (drafts.post(client, path, json=body) if body
             else drafts.post(client, path))
        assert (r.status_code, r.json()["kind"]) == (status, kind), path
        assert r.json()["detail"] == "upstream said no", path


def test_a_provider_missing_key_matches_the_pre_flight_refusal(client):
    """`require_inference` refuses an unconfigured connection with a 409
    before any call goes out. A key the *provider* then reports missing is the
    same condition found later, and answering it differently would make one
    setup mistake look like two problems."""
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]

    early = drafts.post(client, f"/api/campaigns/{cid}/scene-suggestions")
    assert (early.status_code, early.json()["kind"]) == (409, "missing_key")

    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FailingOpenRouter([], "missing_key", "OpenRouter API key is not set")
    late = drafts.post(client, f"/api/campaigns/{cid}/scene-suggestions")

    assert (late.status_code, late.json()["kind"]) == (409, "missing_key")


def test_the_model_listing_route_reports_the_kind_it_got(client):
    """Model listing is not a generation, so it does not reach the taxonomy by
    the same path the streaming and one-shot routes do; without this the static
    guard is the only thing holding that route to it."""
    r = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Endpoint",
        "base_url": "https://x", "api_key": "sk-x"})
    conn = r.json()["id"]
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeCatalog(error=LLMError("rate_limit", "slow down", 30.0))

    r = drafts.post(client, f"/api/llm-connections/{conn}/models/refresh")

    assert (r.status_code, r.json()["kind"]) == (429, "rate_limit")
    # In the BODY, not a header: the refresh is detached (#398) and its 202 went
    # out before the provider was called, so there is no response left to hang a
    # `Retry-After` on. See `common.run_error`.
    assert r.json()["retry_after"] == "30"


def test_a_rate_limited_route_passes_the_providers_window_on(client):
    _wid, cid = _fixtures(client)
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FailingOpenRouter([], "rate_limit", "slow down", retry_after=12.4)

    r = drafts.post(client, f"/api/campaigns/{cid}/scene-suggestions")

    assert r.status_code == 429
    assert r.json()["retry_after"] == "13"


def test_a_cross_origin_caller_can_read_the_retry_after(client):
    """CORS hides every response header but a safelisted handful, and
    `Retry-After` is not one of them. The app's own frontend is same-origin via
    vite's proxy, so nothing here would have noticed.

    The one-shot routes carry their window in the body now that they are
    detached, so this pins the exposure for the routes that still answer with a
    header -- the pre-flight refusals every one of them can still raise while
    the request is there."""
    _wid, cid = _fixtures(client)
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FailingOpenRouter([], "rate_limit", "slow down", retry_after=12.4)

    started = client.post(f"/api/campaigns/{cid}/scene-suggestions",
                          headers={"Origin": "http://localhost:5173"})

    assert started.status_code == 202
    assert "Retry-After" in started.headers["access-control-expose-headers"]
    assert drafts.settle(client, started).json()["retry_after"] == "13"


def test_a_rate_limit_with_no_window_sends_no_retry_after(client):
    _wid, cid = _fixtures(client)
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FailingOpenRouter([], "rate_limit", "slow down")

    r = drafts.post(client, f"/api/campaigns/{cid}/scene-suggestions")

    assert r.status_code == 429 and "retry_after" not in r.json()


def test_a_streamed_failure_still_answers_200_with_an_in_band_error(client):
    """The boundary of this change. A stream's headers are long gone by the time
    a provider fails, so its errors stay SSE events carrying the same `kind` —
    and a status taxonomy that reached in here would be inventing a second one.
    """
    _wid, cid = _fixtures(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FailingOpenRouter([], "rate_limit", "slow down")

    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hi"})

    assert r.status_code == 200
    assert "retry-after" not in r.headers
    frames = [json.loads(line[len("data:"):]) for line in r.text.splitlines()
              if line.startswith("data:")]
    errors = [f["error"] for f in frames if "error" in f]
    # The frame carries more than this (`post_returned`, which is the streaming
    # path's own business); what matters here is that the taxonomy travels in it.
    assert [(e["detail"], e["kind"]) for e in errors] == [("slow down", "rate_limit")]


# ---- and the browser reads the same taxonomy (#146) ----
