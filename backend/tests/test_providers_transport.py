"""HttpTransport against loopback servers: phases, decoding and failure kinds."""

import socket
import ssl
import sys
import threading
import traceback

import pytest

from provider_servers import (
    STREAM_HEAD, RawServer, base_url, raw_chunk, reset, sse_lines, sse_server, stream,
)
from grimoire.providers import CancelToken, Secret, Timeouts, TransportError
from grimoire.providers.transport import HttpTransport

KEY = "sk-transport-secret-42"
FAST = Timeouts(connect=2.0, read=2.0, total=10.0)


def _url(server_url):
    return server_url + "/chat/completions"


def _open(url, timeouts=FAST, cancel=None, key=KEY, body=b'{"x":1}'):
    connection = HttpTransport().connect(_url(url), timeouts, cancel or CancelToken())
    return connection, connection.send(body, {"Content-Type": "application/json"},
                                       Secret(key) if key else None)


def _lines(response, limit=2**20, timeout=2.0):
    lines = []
    while True:
        line = response.read_line(limit, timeout)
        if not line:
            return lines
        lines.append(line)


@pytest.mark.parametrize("chunked", [True, False])
def test_sse_lines_are_decoded(chunked):
    expected = sse_lines()
    with sse_server(stream(expected, chunked=chunked)) as server:
        connection, response = _open(base_url(server))
        try:
            assert response.status == 200
            assert response.headers["content-type"] == "text/event-stream"
            got = _lines(response)
        finally:
            connection.close()
        path, headers, body = server.requests[0]
    assert b"".join(got) == b"".join(expected)
    assert all(not line.strip().isalnum() for line in got)  # no chunk-size lines leak through
    assert path == "/v1/chat/completions" and body == b'{"x":1}'
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert connection.peer_loopback is True


def test_no_key_sends_no_authorization():
    with sse_server(stream(sse_lines())) as server:
        connection, response = _open(base_url(server), key=None)
        _lines(response)
        connection.close()
        assert "Authorization" not in server.requests[0][1]


def test_oversized_line_is_bad_response():
    with sse_server(stream([b"data: " + b"x" * 5000 + b"\n\n"])) as server:
        connection, response = _open(base_url(server))
        with pytest.raises(TransportError) as caught:
            response.read_line(1000, 2.0)
        connection.close()
    assert (caught.value.kind, caught.value.phase) == ("bad_response", "read")


def test_error_body_is_capped():
    with sse_server(lambda h: h.respond(500, b"e" * 100_000)) as server:
        connection, response = _open(base_url(server))
        assert response.status == 500
        assert len(response.error_body(65536)) == 65536
        connection.close()


def test_refused_port_is_connect_phase():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(TransportError) as caught:
        HttpTransport().connect(f"http://127.0.0.1:{port}/v1/chat/completions",
                                Timeouts(connect=0.5, read=1, total=5), CancelToken())
    assert caught.value.phase == "connect"
    assert caught.value.kind in ("network", "timeout")  # Windows may time out a refusal


def test_silent_server_read_is_timeout():
    def never_answer(conn, server):
        server.read_request(conn)
        server.release.wait(10)
    with RawServer(never_answer) as server:
        connection = HttpTransport().connect(_url(server.url()), Timeouts(1, 0.3, 5), CancelToken())
        with pytest.raises(TransportError) as caught:
            connection.send(b"{}", {}, None)
        connection.close()
    assert (caught.value.kind, caught.value.phase) == ("timeout", "send")


def test_close_after_request_is_network_send_error():
    def close_after_request(conn, server):
        server.read_request(conn)
    with RawServer(close_after_request) as server:
        connection = HttpTransport().connect(_url(server.url()), FAST, CancelToken())
        with pytest.raises(TransportError) as caught:
            connection.send(b"{}", {}, None)
        connection.close()
    assert (caught.value.kind, caught.value.phase) == ("network", "send")


def test_incomplete_chunk_is_truncated_read_error():
    def half_chunk(conn, server):
        server.read_request(conn)
        conn.sendall(STREAM_HEAD + raw_chunk(b"data: {}\n\n") + b"40\r\ndata: {\"cho")
    with RawServer(half_chunk) as server:
        connection, response = _open(server.url())
        assert response.read_line(2**20, 2.0) == b"data: {}\n"
        with pytest.raises(TransportError) as caught:
            _lines(response)
        connection.close()
    assert (caught.value.kind, caught.value.phase) == ("truncated", "read")


def test_reset_mid_stream_is_a_read_error():
    first_read = threading.Event()

    def reset_mid_stream(conn, server):
        server.read_request(conn)
        conn.sendall(STREAM_HEAD + raw_chunk(b"data: {}\n\n"))
        first_read.wait(5)  # reset only after the client consumed the line (Windows drops it)
        reset(conn)
    with RawServer(reset_mid_stream) as server:
        connection, response = _open(server.url())
        assert response.read_line(2**20, 2.0) == b"data: {}\n"
        first_read.set()
        with pytest.raises(TransportError) as caught:
            _lines(response)
        connection.close()
    assert caught.value.phase == "read"
    assert caught.value.kind in ("network", "truncated")


def test_bad_status_line_is_bad_response():
    def nonsense(conn, server):
        server.read_request(conn)
        conn.sendall(b"NOT-HTTP garbage\r\n\r\n")
    with RawServer(nonsense) as server:
        connection = HttpTransport().connect(_url(server.url()), FAST, CancelToken())
        with pytest.raises(TransportError) as caught:
            connection.send(b"{}", {}, None)
        connection.close()
    assert (caught.value.kind, caught.value.phase) == ("bad_response", "send")


def test_tls_handshake_reset_is_network():
    def hang_up(conn, server):
        conn.recv(10)
    with RawServer(hang_up) as server:
        with pytest.raises(TransportError) as caught:
            HttpTransport().connect(_url(server.url("https")), FAST, CancelToken())
    assert (caught.value.kind, caught.value.phase) == ("network", "connect")


def test_cert_verification_error_maps_to_tls(monkeypatch):
    def refuse(self, *args, **kwargs):
        raise ssl.SSLCertVerificationError("certificate verify failed")
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", refuse)
    with RawServer(lambda conn, server: server.release.wait(5)) as server:
        with pytest.raises(TransportError) as caught:
            HttpTransport().connect(_url(server.url("https")), FAST, CancelToken())
    assert (caught.value.kind, caught.value.phase) == ("tls", "connect")


def test_non_loopback_peer_is_refused_before_write(monkeypatch):
    monkeypatch.setattr(socket.socket, "getpeername", lambda self: ("10.1.2.3", 80))
    with RawServer(lambda conn, server: server.read_request(conn, timeout=1.0)) as server:
        with pytest.raises(TransportError) as caught:
            HttpTransport().connect(_url(server.url()), FAST, CancelToken())
    assert (caught.value.kind, caught.value.phase) == ("peer_not_loopback", "connect")
    assert bytes(server.received) == b""


def test_keyboardinterrupt_inside_send_leaks_no_key(monkeypatch):
    import http.client

    def interrupted(self, data):
        raise KeyboardInterrupt
    with sse_server(stream(sse_lines())) as server:
        connection = HttpTransport().connect(_url(base_url(server)), FAST, CancelToken())
        monkeypatch.setattr(http.client.HTTPConnection, "send", interrupted)
        with pytest.raises(KeyboardInterrupt) as caught:
            connection.send(b"{}", {}, Secret(KEY))
        connection.close()
    formatted = "".join(traceback.TracebackException.from_exception(
        caught.value, capture_locals=True).format())
    assert KEY not in formatted
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.xfail(sys.platform == "win32", reason="shutdown() may not wake a blocked recv on Windows")
def test_cross_thread_cancel_wakes_a_blocked_read():
    released_by = []

    def stall(conn, server):
        server.read_request(conn)
        conn.sendall(STREAM_HEAD + raw_chunk(b"data: {}\n\n"))
        released_by.append("client" if server.release.wait(20) else "timer")
    token = CancelToken()
    with RawServer(stall) as server:
        connection, response = _open(server.url(), timeouts=Timeouts(2.0, 30.0, 60.0), cancel=token)
        assert response.read_line(2**20, 30.0) == b"data: {}\n"
        timer = threading.Timer(0.1, token.cancel)
        timer.start()
        try:
            while response.read_line(2**20, 30.0):
                pass
        except TransportError:
            pass
        server.release.set()  # the client returned: release the server
        timer.join()
        connection.close()
    assert released_by == ["client"]


def test_system_exit_keeps_its_code_and_foreign_interrupts_are_not_network_errors(monkeypatch):
    import http.client

    class Cancelled(BaseException):
        pass
    for raised, expected in ((SystemExit(3), SystemExit), (Cancelled(), Cancelled)):
        def boom(self, data, raised=raised):
            raise raised
        with sse_server(stream(sse_lines())) as server:
            connection = HttpTransport().connect(_url(base_url(server)), FAST, CancelToken())
            monkeypatch.setattr(http.client.HTTPConnection, "send", boom)
            with pytest.raises(expected) as caught:
                connection.send(b"{}", {}, Secret(KEY))
            monkeypatch.undo()
            connection.close()
        if expected is SystemExit:
            assert caught.value.code == 3
