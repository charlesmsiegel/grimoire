"""Compression is for a network, and loopback is not one.

Both servers this app ships are loopback-only: the Android entry point binds
`127.0.0.1`, and the desktop scripts run uvicorn on its default host, which is
loopback too. Gzipping a response for a client on the same machine saves no
transfer time and costs CPU on the event loop that also carries every live
turn's stream -- and on the Android dependency set's Starlette it compresses
images and fonts as well, inline, for no byte saving at all.

So `_RemoteOnlyGZip` skips compression for a loopback peer and keeps it for
anyone else. A reverse proxy on the same host connects from loopback but
relays over a real network, so a forwarding header makes a loopback peer count
as remote.

Driven as raw ASGI rather than through `TestClient`: the peer address is the
whole question, and `TestClient`'s `client=` keyword does not exist on the
Starlette that `make check-pydantic1` (the Android set) installs, while its
default peer, `"testclient"`, is not an address at all. That default is what
keeps `test_routes.py::test_large_json_responses_are_gzipped` on the remote
path, and it stays green beside these.
"""

import asyncio

import pytest
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from grimoire.main import _RemoteOnlyGZip

GZIP = [(b"accept-encoding", b"gzip, deflate")]
#: Comfortably past the 1 KiB floor, and compressible, so a remote client
#: that is NOT compressed is a failure rather than a size decision.
BIG = {"body": "Seraphine walks the Saltmarch road. " * 200}


def _asgi(app, path, *, client, headers=(), method="GET"):
    """One request through `app`, as the ASGI server would hand it over."""
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "headers": [(b"host", b"127.0.0.1:8173"), *headers],
        "client": client, "server": ("127.0.0.1", 8173),
    }
    sent: list[dict] = []
    requested = False

    async def receive():
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        # Nothing here streams, so nothing should wait for a disconnect; if
        # something does, it gets one rather than hanging the suite.
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], Headers(raw=start["headers"]), body


def _wrapped():
    async def inner(scope, receive, send):
        await JSONResponse(BIG)(scope, receive, send)
    return _RemoteOnlyGZip(inner)


@pytest.mark.parametrize("client", [
    ("127.0.0.1", 50123),
    ("::1", 50123),
    # The whole of 127.0.0.0/8 is loopback; Debian-family hosts resolve their
    # own hostname to 127.0.1.1.
    ("127.0.1.1", 50123),
    # An IPv4 peer on a dual-stack IPv6 listener (`--host ::`).
    ("::ffff:127.0.0.1", 50123),
    # Not an address, but a server that reports a peer by name would say so.
    ("localhost", 50123),
])
def test_a_loopback_peer_is_served_uncompressed(client):
    status, headers, body = _asgi(_wrapped(), "/", client=client, headers=GZIP)
    assert status == 200
    assert "content-encoding" not in headers
    assert b"Saltmarch road" in body


@pytest.mark.parametrize("client", [
    ("10.0.0.2", 50123),
    ("192.168.1.20", 50123),
    ("fd00::2", 50123),
    # TestClient's peer. Not loopback, so every TestClient-driven test keeps
    # exercising the compressing path.
    ("testclient", 50000),
    # No peer at all -- a unix-domain socket, which is how a reverse proxy
    # usually reaches an app server. Whoever is behind it is not on loopback.
    None,
])
def test_a_remote_peer_is_still_gzipped(client):
    status, headers, _ = _asgi(_wrapped(), "/", client=client, headers=GZIP)
    assert status == 200
    assert headers.get("content-encoding") == "gzip"


@pytest.mark.parametrize("forwarded", [
    (b"x-forwarded-for", b"203.0.113.9"),
    (b"forwarded", b"for=203.0.113.9;proto=https"),
    (b"x-real-ip", b"203.0.113.9"),
    # Some proxies name only the scheme or host they were reached on.
    (b"x-forwarded-proto", b"https"),
    (b"x-forwarded-host", b"grimoire.example"),
])
def test_a_same_host_reverse_proxy_keeps_compression(forwarded):
    """A reverse proxy on the same machine connects from loopback but relays
    the response over a real network, where compression still pays. Caddy
    sends these headers by default and nginx when configured to; one that
    sends none looks local, the trade `_RemoteOnlyGZip` states."""
    status, headers, _ = _asgi(_wrapped(), "/", client=("127.0.0.1", 50123),
                               headers=[*GZIP, forwarded])
    assert status == 200
    assert headers.get("content-encoding") == "gzip"


def test_a_remote_peer_that_does_not_accept_gzip_gets_identity():
    # The wrapper chooses whether to OFFER compression; negotiation is still
    # the client's, exactly as it was with the bare middleware.
    _, headers, _ = _asgi(_wrapped(), "/", client=("10.0.0.2", 50123))
    assert "content-encoding" not in headers


# ---- through the real app ----

@pytest.fixture
def dist(tmp_path, monkeypatch):
    """A built frontend with a bundle big enough to be worth compressing."""
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        '<html><script src="/assets/index-3f9a1c.js"></script></html>', encoding="utf-8")
    (root / "assets" / "index-3f9a1c.js").write_text(
        "console.log('Winifred reads the ledger');\n" * 200, encoding="utf-8")
    monkeypatch.setenv("GRIMOIRE_DIST", str(root))
    return root


@pytest.fixture
def client(dist, client):
    # `dist` first, so `GRIMOIRE_DIST` is set before conftest's `client`
    # builds the app -- `create_app` only mounts the frontend if it exists.
    return client


@pytest.fixture
def lore_path(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    r = client.post(f"/api/worlds/{wid}/lore",
                    json={"name": "Saltmarch", "body": BIG["body"]})
    assert r.status_code == 200
    return f"/api/worlds/{wid}/lore/saltmarch"


def test_the_app_serves_loopback_json_uncompressed(client, lore_path):
    status, headers, body = _asgi(client.app, lore_path, client=("127.0.0.1", 50123),
                                  headers=GZIP)
    assert status == 200
    assert "content-encoding" not in headers
    assert b"Saltmarch road" in body


def test_the_app_still_gzips_json_for_a_remote_peer(client, lore_path):
    status, headers, _ = _asgi(client.app, lore_path, client=("10.0.0.2", 50123),
                               headers=GZIP)
    assert status == 200
    assert headers.get("content-encoding") == "gzip"


def test_the_app_serves_the_bundle_uncompressed_to_loopback(client):
    # The bundle is the largest thing a cold start fetches, and it was
    # recompressed on every uncached request.
    status, headers, body = _asgi(client.app, "/assets/index-3f9a1c.js",
                                  client=("127.0.0.1", 50123), headers=GZIP)
    assert status == 200
    assert "content-encoding" not in headers
    assert body.startswith(b"console.log('Winifred")


def test_the_app_still_gzips_the_bundle_for_a_remote_peer(client):
    status, headers, _ = _asgi(client.app, "/assets/index-3f9a1c.js",
                               client=("10.0.0.2", 50123), headers=GZIP)
    assert status == 200
    assert headers.get("content-encoding") == "gzip"
