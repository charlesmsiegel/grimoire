"""The LLM error-status taxonomy (#213).

Every non-stream route that catches an `LLMError` used to answer 502, whatever
had actually gone wrong. The `kind` was in the body all along, so the frontend
could tell a rate limit from a broken provider — but nothing else could, and a
status code is the part a proxy, a retry helper and a log reader read without
knowing this app's vocabulary.

What is asserted here, in three layers, because each catches a different way of
getting it wrong:

- **The map is total.** `_LLM_STATUS` covers exactly `llm_errors.KINDS`, so a
  new kind cannot arrive with no status decided for it.
- **The routes use it.** A static guard over `routes/` proves no handler still
  spells a status out for itself, which is the mistake that produced this issue
  and is invisible to any test of the routes that happen to be covered.
- **The wire agrees.** Real requests against real routes, because a map nothing
  reaches is just a dict.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from grimoire import llm_errors, routes
from grimoire.llm_errors import LLMError
from grimoire.routes import common
from tests.llm_fakes import FailingOpenRouter, FakeModelsClient

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "grimoire" / "routes"


# ---- the map itself ----
def test_every_kind_has_a_status():
    """The guard that keeps the taxonomy total. A kind added to `llm_errors`
    without a decision here would otherwise reach the fallback silently, which
    is exactly the blanket 502 this issue removed."""
    assert set(common._LLM_STATUS) == set(llm_errors.KINDS)


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
def scan(src: str) -> list[int]:
    """Line numbers where an `except LLMError` block builds an HTTPException.

    The whole failure this issue names is a call site answering for itself, and
    ten of them did. Testing only the routes that are easy to reach would leave
    the hard ones (absorb, scene-break, rolling-summary) free to drift back, so
    the rule is checked where it is actually written.

    Deliberately narrow: it looks only inside an `except` clause naming
    `LLMError`, and only for an `HTTPException(...)` constructed there. A
    handler that calls a helper of its own escapes, as does one that re-raises
    something built elsewhere. Both are shapes worth reading anyway, and a guard
    claiming to catch them would be claiming more than it can see.
    """
    found = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.ExceptHandler) or not _names_llm_error(node.type):
            continue
        found += [inner.lineno for inner in ast.walk(node)
                  if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                  and inner.func.id == "HTTPException"]
    return sorted(found)


def _names_llm_error(node: ast.AST | None) -> bool:
    """Whether an `except` clause catches `LLMError`, alone or in a tuple."""
    if node is None:
        return False
    parts = node.elts if isinstance(node, ast.Tuple) else [node]
    return any(isinstance(p, ast.Name) and p.id == "LLMError" for p in parts)


def test_the_guard_catches_the_shape_it_bans():
    """A guard that never fires proves nothing. Both forms of the ban, and the
    two neighbours it must NOT flag."""
    assert scan("try:\n    pass\nexcept LLMError as exc:\n"
                "    raise HTTPException(status_code=502, detail={})\n") == [4]
    assert scan("try:\n    pass\nexcept (OSError, LLMError) as exc:\n"
                "    raise HTTPException(status_code=502, detail={})\n") == [4]
    assert scan("try:\n    pass\nexcept LLMError as exc:\n"
                "    raise _llm_http_error(exc) from exc\n") == []
    assert scan("try:\n    pass\nexcept ValueError:\n"
                "    raise HTTPException(status_code=400, detail={})\n") == []


def test_the_guard_reads_the_modules_that_catch_llm_failures():
    """An empty glob would leave the parametrized guard below vacuously green,
    passing hardest exactly when it had stopped reading anything."""
    names = {p.name for p in SRC.glob("*.py")}
    assert {"campaigns.py", "characters.py", "config.py", "scenes.py",
            "streaming.py", "worlds.py"} <= names


@pytest.mark.parametrize("path", sorted(SRC.glob("*.py")), ids=lambda p: p.name)
def test_no_route_spells_out_its_own_llm_status(path):
    assert scan(path.read_text(encoding="utf-8")) == [], \
        f"{path.name} decides its own status instead of calling _llm_http_error()"


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
        r = client.post(path, json=body) if body else client.post(path)
        assert (r.status_code, r.json()["kind"]) == (status, kind), path
        assert r.json()["detail"] == "upstream said no", path


def test_a_provider_missing_key_matches_the_pre_flight_refusal(client):
    """`_require_connection` refuses an unconfigured connection with a 409
    before any call goes out. A key the *provider* then reports missing is the
    same condition found later, and answering it differently would make one
    setup mistake look like two problems."""
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]

    early = client.post(f"/api/campaigns/{cid}/scene-suggestions")
    assert (early.status_code, early.json()["kind"]) == (409, "missing_key")

    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FailingOpenRouter([], "missing_key", "OpenRouter API key is not set")
    late = client.post(f"/api/campaigns/{cid}/scene-suggestions")

    assert (late.status_code, late.json()["kind"]) == (409, "missing_key")


def test_the_model_listing_route_reports_the_kind_it_got(client):
    """Model listing hangs off a DIFFERENT dependency
    (`get_openai_compatible_client`, not `get_llm`), so without this the static
    guard is the only thing holding that route to the taxonomy."""
    r = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Endpoint",
        "base_url": "https://x", "api_key": "sk-x"})
    conn = r.json()["id"]
    client.app.dependency_overrides[routes.get_openai_compatible_client] = \
        lambda: FakeModelsClient(error=LLMError("rate_limit", "slow down", 30.0))

    r = client.post(f"/api/llm-connections/{conn}/models/refresh")

    assert (r.status_code, r.json()["kind"]) == (429, "rate_limit")
    assert r.headers["retry-after"] == "30"


def test_a_rate_limited_route_passes_the_providers_window_on(client):
    _wid, cid = _fixtures(client)
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FailingOpenRouter([], "rate_limit", "slow down", retry_after=12.4)

    r = client.post(f"/api/campaigns/{cid}/scene-suggestions")

    assert r.status_code == 429
    assert r.headers["retry-after"] == "13"


def test_a_cross_origin_caller_can_read_the_retry_after(client):
    """CORS hides every response header but a safelisted handful, and
    `Retry-After` is not one of them. The app's own frontend is same-origin via
    vite's proxy, so nothing here would have noticed."""
    _wid, cid = _fixtures(client)
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FailingOpenRouter([], "rate_limit", "slow down", retry_after=12.4)

    r = client.post(f"/api/campaigns/{cid}/scene-suggestions",
                    headers={"Origin": "http://localhost:5173"})

    assert r.status_code == 429 and r.headers["retry-after"] == "13"
    assert "Retry-After" in r.headers["access-control-expose-headers"]


def test_a_rate_limit_with_no_window_sends_no_retry_after(client):
    _wid, cid = _fixtures(client)
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FailingOpenRouter([], "rate_limit", "slow down")

    r = client.post(f"/api/campaigns/{cid}/scene-suggestions")

    assert r.status_code == 429 and "retry-after" not in r.headers


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
