import shutil
import socket
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from grimoire.store import fetch

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
PUBLIC_IP = "93.184.216.34"


def _addrinfo(ip: str, port):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]


def test_sniff_ext_detects_png_and_rejects_text():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    assert fetch.sniff_ext(png) == "png"
    assert fetch.sniff_ext(b"not an image") is None


def test_decode_data_uri_returns_bytes_and_ext():
    # 1x1 gif, base64
    uri = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="
    got = fetch.decode_data_uri(uri)
    assert got is not None
    raw, ext = got
    assert ext == "gif"
    assert raw[:6] in (b"GIF87a", b"GIF89a")


def test_decode_data_uri_rejects_non_data_uri():
    assert fetch.decode_data_uri("https://example.com/a.png") is None


def test_host_is_blocked_blocks_loopback():
    assert fetch.host_is_blocked("127.0.0.1") is True
    assert fetch.host_is_blocked("localhost") is True


def test_download_url_rejects_non_image(monkeypatch):
    # bytes that aren't an image and a non-image content-type -> None
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (b"<html>nope", "text/html"))
    assert fetch.download_url("https://h/page") is None


def test_download_url_accepts_sniffed_image_despite_bad_content_type(monkeypatch):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "application/octet-stream"))
    got = fetch.download_url("https://h/x")
    assert got is not None and got[1] == "png"


def test_download_url_swallows_errors(monkeypatch):
    def boom(url):
        raise RuntimeError("network down")

    monkeypatch.setattr(fetch, "_http_get_bytes", boom)
    assert fetch.download_url("https://h/x") is None


def test_download_bytes_returns_raw_content_regardless_of_type(monkeypatch):
    # unlike download_url, no image-type requirement -- a JSON card must pass through
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (b'{"a": 1}', "application/json"))
    assert fetch.download_bytes("https://h/card.json") == b'{"a": 1}'


def test_download_bytes_rejects_oversize(monkeypatch):
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (b"x" * (fetch.MAX_BYTES + 1), None))
    assert fetch.download_bytes("https://h/x") is None


def test_download_bytes_swallows_errors(monkeypatch):
    def boom(url):
        raise RuntimeError("network down")

    monkeypatch.setattr(fetch, "_http_get_bytes", boom)
    assert fetch.download_bytes("https://h/x") is None


# --- SSRF guard: the connection is pinned to the address that was validated ---


@pytest.fixture(autouse=True)
def _no_ambient_proxy(monkeypatch):
    """Pinning stands down behind a proxy, so no test may inherit one.

    Patched at urllib rather than at `fetch._proxied` so these tests still run
    the real decision, and so a dev box with a system-wide proxy configured
    tests the same thing CI does.
    """
    monkeypatch.setattr(fetch.urllib.request, "getproxies", dict)


def test_resolve_allowed_returns_the_validated_addresses(monkeypatch):
    monkeypatch.setattr(fetch.socket, "getaddrinfo",
                        lambda host, port=None, *a, **kw: _addrinfo(PUBLIC_IP, port))
    assert fetch.resolve_allowed("img.example.test") == [PUBLIC_IP]


def test_resolve_allowed_blocks_private_and_unresolvable(monkeypatch):
    assert fetch.resolve_allowed("127.0.0.1") is None
    assert fetch.host_is_blocked("127.0.0.1") is True

    def unresolvable(host, port=None, *a, **kw):
        raise OSError("NXDOMAIN")

    monkeypatch.setattr(fetch.socket, "getaddrinfo", unresolvable)
    assert fetch.resolve_allowed("nope.example.test") is None


def _split_resolution(monkeypatch, hostname, rebound="127.0.0.1"):
    """Answer the first lookup of `hostname` publicly and every later one privately.

    That is the rebinding attacker: the guard's resolution says one thing, the
    resolution the socket layer would do on connect says another.
    """
    seen: list[str] = []

    def fake(host, port=None, *a, **kw):
        seen.append(host)
        if host == hostname:
            first = seen.count(hostname) == 1
            return _addrinfo(PUBLIC_IP if first else rebound, port)
        return _addrinfo(host, port)  # a literal resolves to itself

    monkeypatch.setattr(fetch.socket, "getaddrinfo", fake)
    return seen


def _recording_transport(handler):
    seen: list[httpx.Request] = []

    def respond(request):
        seen.append(request)
        return handler(request)

    return httpx.MockTransport(respond), seen


def test_connect_is_pinned_to_the_validated_ip_not_a_rebound_one(monkeypatch):
    _split_resolution(monkeypatch, "img.example.test")
    transport, seen = _recording_transport(
        lambda req: httpx.Response(200, content=PNG, headers={"content-type": "image/png"}))

    content, ctype = fetch._http_get_bytes("https://img.example.test/a.png", transport=transport)

    assert content == PNG and ctype == "image/png"
    req, = seen
    # The address the guard validated — not the loopback address a second
    # resolution would have handed the socket layer.
    assert req.url.host == PUBLIC_IP
    assert req.headers["Host"] == "img.example.test"
    assert req.extensions["sni_hostname"] == "img.example.test"


def test_pinned_url_keeps_the_port_and_path(monkeypatch):
    _split_resolution(monkeypatch, "img.example.test")
    transport, seen = _recording_transport(
        lambda req: httpx.Response(200, content=PNG, headers={"content-type": "image/png"}))

    fetch._http_get_bytes("https://img.example.test:8443/deep/a.png?v=2", transport=transport)

    req, = seen
    assert str(req.url) == f"https://{PUBLIC_IP}:8443/deep/a.png?v=2"
    assert req.headers["Host"] == "img.example.test:8443"


def test_ipv6_literal_host_is_bracketed(monkeypatch):
    monkeypatch.setattr(fetch.socket, "getaddrinfo",
                        lambda host, port=None, *a, **kw: [
                            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800:220:1::1", port or 0, 0, 0))])
    transport, seen = _recording_transport(
        lambda req: httpx.Response(200, content=PNG, headers={"content-type": "image/png"}))

    fetch._http_get_bytes("https://img.example.test/a.png", transport=transport)

    req, = seen
    assert str(req.url) == "https://[2606:2800:220:1::1]/a.png"
    assert req.headers["Host"] == "img.example.test"


def test_each_redirect_hop_is_pinned_to_its_own_validated_ip(monkeypatch):
    hops = {"one.example.test": PUBLIC_IP, "two.example.test": "151.101.1.140"}
    monkeypatch.setattr(fetch.socket, "getaddrinfo",
                        lambda host, port=None, *a, **kw: _addrinfo(hops.get(host, host), port))

    def handler(request):
        if request.headers["Host"] == "one.example.test":
            return httpx.Response(302, headers={"location": "https://two.example.test/b.png"})
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    transport, seen = _recording_transport(handler)
    content, _ = fetch._http_get_bytes("https://one.example.test/a.png", transport=transport)

    assert content == PNG
    assert [(r.url.host, r.headers["Host"]) for r in seen] == [
        (PUBLIC_IP, "one.example.test"),
        ("151.101.1.140", "two.example.test"),
    ]


def test_relative_redirect_keeps_the_hostname_rather_than_the_pinned_ip(monkeypatch):
    monkeypatch.setattr(fetch.socket, "getaddrinfo",
                        lambda host, port=None, *a, **kw: _addrinfo(PUBLIC_IP, port))

    def handler(request):
        if request.url.path == "/a.png":
            return httpx.Response(302, headers={"location": "/b.png"})
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    transport, seen = _recording_transport(handler)
    content, _ = fetch._http_get_bytes("https://img.example.test/a.png", transport=transport)

    assert content == PNG
    # Second hop still addressed to the hostname (so TLS still verifies it),
    # and still pinned to the address validated for that hop.
    assert seen[1].headers["Host"] == "img.example.test"
    assert seen[1].url.host == PUBLIC_IP
    assert seen[1].extensions["sni_hostname"] == "img.example.test"


def test_redirect_to_a_blocked_host_is_rejected_before_connecting(monkeypatch):
    def fake(host, port=None, *a, **kw):
        return _addrinfo(PUBLIC_IP if host == "img.example.test" else "169.254.169.254", port)

    monkeypatch.setattr(fetch.socket, "getaddrinfo", fake)
    transport, seen = _recording_transport(
        lambda req: httpx.Response(302, headers={"location": "http://metadata.example.test/latest"}))

    with pytest.raises(ValueError, match="blocked host"):
        fetch._http_get_bytes("https://img.example.test/a.png", transport=transport)
    assert len(seen) == 1  # the blocked hop never got sent


def test_size_cap_still_applies_to_a_pinned_response(monkeypatch):
    _split_resolution(monkeypatch, "img.example.test")
    monkeypatch.setattr(fetch, "MAX_BYTES", 32)
    transport, _ = _recording_transport(lambda req: httpx.Response(200, content=PNG + b"\x00" * 64))

    with pytest.raises(ValueError, match="too large"):
        fetch._http_get_bytes("https://img.example.test/a.png", transport=transport)


def test_content_length_over_the_cap_is_refused_before_reading(monkeypatch):
    _split_resolution(monkeypatch, "img.example.test")
    monkeypatch.setattr(fetch, "MAX_BYTES", 32)
    headers = {"content-length": "99999", "content-type": "image/png"}
    transport, _ = _recording_transport(
        lambda req: httpx.Response(200, content=PNG, headers=headers))

    with pytest.raises(ValueError, match="too large"):
        fetch._http_get_bytes("https://img.example.test/a.png", transport=transport)


def test_falls_back_to_the_next_validated_address_when_one_refuses(monkeypatch):
    monkeypatch.setattr(fetch.socket, "getaddrinfo",
                        lambda host, port=None, *a, **kw: [
                            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port or 0)),
                            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("151.101.1.140", port or 0))])

    def handler(request):
        if request.url.host == PUBLIC_IP:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    transport, seen = _recording_transport(handler)
    content, _ = fetch._http_get_bytes("https://img.example.test/a.png", transport=transport)

    assert content == PNG
    assert [r.url.host for r in seen] == [PUBLIC_IP, "151.101.1.140"]


def test_proxied_reads_the_environment_httpx_reads(monkeypatch):
    monkeypatch.setattr(fetch.urllib.request, "getproxies",
                        lambda: {"https": "http://proxy.example.test:3128"})
    monkeypatch.setattr(fetch.urllib.request, "proxy_bypass", lambda host: False)
    assert fetch._proxied("https", "img.example.test") is True
    assert fetch._proxied("http", "img.example.test") is False  # https_proxy only

    monkeypatch.setattr(fetch.urllib.request, "proxy_bypass", lambda host: True)
    assert fetch._proxied("https", "img.example.test") is False  # in no_proxy


def test_no_proxy_configured_means_no_proxy(monkeypatch):
    monkeypatch.setattr(fetch.urllib.request, "getproxies", dict)
    assert fetch._proxied("https", "img.example.test") is False


def test_a_proxied_request_keeps_the_hostname_and_still_checks_the_host(monkeypatch):
    """The proxy resolves the name and connects, so pinning would only break it.

    Rewriting the URL to an IP makes a hostname-allowlisting proxy refuse the
    CONNECT outright, and buys nothing: the lookup we would be pinning is not
    the one that decides where the bytes come from.
    """
    monkeypatch.setattr(fetch, "_proxied", lambda scheme, host: True)
    _split_resolution(monkeypatch, "img.example.test")
    transport, seen = _recording_transport(
        lambda req: httpx.Response(200, content=PNG, headers={"content-type": "image/png"}))

    content, _ = fetch._http_get_bytes("https://img.example.test/a.png", transport=transport)

    assert content == PNG
    req, = seen
    assert req.url.host == "img.example.test"

    # ...and the guard still runs: a host that resolves private is still refused.
    monkeypatch.setattr(fetch.socket, "getaddrinfo",
                        lambda host, port=None, *a, **kw: _addrinfo("169.254.169.254", port))
    with pytest.raises(ValueError, match="blocked host"):
        fetch._http_get_bytes("https://metadata.example.test/latest", transport=transport)


class _PngHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self):
        self.server.seen_hosts.append(self.headers.get("Host"))
        self.send_response(200)
        self.send_header("content-type", "image/png")
        self.send_header("content-length", str(len(PNG)))
        self.end_headers()
        self.wfile.write(PNG)

    def log_message(self, *a):  # keep the test output quiet
        pass


@pytest.fixture()
def png_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PngHandler)
    server.seen_hosts = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_pinned_request_reaches_a_real_server_addressed_to_the_hostname(monkeypatch, png_server):
    """The rewrite has to survive a real socket, not just a mock transport."""
    port = png_server.server_address[1]
    monkeypatch.setattr(fetch, "resolve_allowed", lambda host: ["127.0.0.1"])

    content, ctype = fetch._http_get_bytes(f"http://img.example.test:{port}/a.png")

    assert content == PNG and ctype == "image/png"
    assert png_server.seen_hosts == [f"img.example.test:{port}"]


def _self_signed(tmp_path, name):
    """A cert/key pair for `name`, or None where openssl isn't installed."""
    if not shutil.which("openssl"):
        return None
    cert, key = tmp_path / f"{name}.pem", tmp_path / f"{name}.key"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "1",
         "-subj", f"/CN={name}", "-addext", f"subjectAltName=DNS:{name}"],
        check=True, capture_output=True)
    return cert, key


@pytest.fixture()
def tls_server(tmp_path):
    """Serves a PNG over TLS on loopback; yields (port, cert) per certificate name."""
    servers = []

    def start(name):
        pair = _self_signed(tmp_path, name)
        if pair is None:
            pytest.skip("openssl not installed")
        cert, key = pair
        server = ThreadingHTTPServer(("127.0.0.1", 0), _PngHandler)
        server.seen_hosts = []
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return server.server_address[1], cert

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_tls_verifies_the_hostname_even_though_it_connected_to_an_ip(monkeypatch, tls_server):
    """The whole point of the SNI extension: pinning must not weaken TLS."""
    port, cert = tls_server("img.example.test")
    monkeypatch.setattr(fetch, "_SSL_CTX", ssl.create_default_context(cafile=str(cert)))
    monkeypatch.setattr(fetch, "resolve_allowed", lambda host: ["127.0.0.1"])

    content, ctype = fetch._http_get_bytes(f"https://img.example.test:{port}/a.png")

    assert content == PNG and ctype == "image/png"


def test_tls_rejects_a_certificate_issued_to_another_name(monkeypatch, tls_server):
    """The counterpart: verification is really happening, not merely configured."""
    port, _ = tls_server("other.example.test")
    _, ours = tls_server("img.example.test")
    monkeypatch.setattr(fetch, "_SSL_CTX", ssl.create_default_context(cafile=str(ours)))
    monkeypatch.setattr(fetch, "resolve_allowed", lambda host: ["127.0.0.1"])

    assert fetch.download_url(f"https://img.example.test:{port}/a.png") is None


def test_rebinding_host_is_resolved_once_and_connected_to_by_literal(monkeypatch, png_server):
    """The end-to-end rebinding case, over a real connection.

    The guard's lookup answers with a public address; any later lookup of the
    same name answers with loopback. The fetch must never make that later
    lookup — it connects to the literal it validated.
    """
    port = png_server.server_address[1]
    seen: list[str] = []

    def fake(host, port=None, *a, **kw):
        seen.append(host)
        if host == "img.example.test":
            # public for the guard's lookup, loopback for every later one
            return _addrinfo(PUBLIC_IP if seen.count(host) == 1 else "127.0.0.1", port)
        if host == PUBLIC_IP:
            # Stand the validated public address in for the loopback server, so
            # the pinned connect stays inside the test instead of leaving the
            # machine — the point under test is *which name gets looked up*.
            return _addrinfo("127.0.0.1", port)
        return _addrinfo(host, port)

    monkeypatch.setattr(socket, "getaddrinfo", fake)

    content, _ = fetch._http_get_bytes(f"http://img.example.test:{port}/a.png")

    assert content == PNG
    assert seen == ["img.example.test", PUBLIC_IP]  # the name is never looked up twice
    assert png_server.seen_hosts == [f"img.example.test:{port}"]
