"""Bounded real HTTP admission: bursts wait, sustained overload refuses explicitly.

Every handler and admission transition is event-controlled. No private libraries,
provider calls, retry loops or scheduling sleeps are involved.
"""
from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler
import socket
import sys
import threading
import time

from grimoire.app import server as server_module

WAIT = 5.0
POLL_INTERVAL = 0.02


class AdmissionProbe:
    """Observe a real bounded semaphore without replacing its permit accounting."""
    def __init__(self, semaphore):
        self.semaphore = semaphore
        self.condition = threading.Condition()
        self.held = 0
        self.waiting = threading.Event()

    def acquire(self, blocking=True, timeout=None):
        with self.condition:
            saturated = self.held == server_module.MAX_CONNECTIONS
        # This event means the server entered a potentially blocking admission.
        # Release may race ahead of the actual wait; either order must admit the burst.
        if saturated and blocking:
            self.waiting.set()
        acquired = self.semaphore.acquire(blocking=blocking, timeout=timeout)
        if acquired:
            with self.condition:
                self.held += 1
                self.condition.notify_all()
        return acquired

    def release(self):
        with self.condition:
            self.held -= 1
            self.semaphore.release()
            self.condition.notify_all()

    def assert_recovered(self):
        with self.condition:
            assert self.condition.wait_for(lambda: self.held == 0, WAIT), "a handler permit leaked"
        # Exercise the underlying semaphore, rather than trusting the probe's counter.
        held = 0
        try:
            for _ in range(server_module.MAX_CONNECTIONS):
                assert self.semaphore.acquire(blocking=False)
                held += 1
            assert not self.semaphore.acquire(blocking=False)
        finally:
            for _ in range(held):
                self.semaphore.release()


class HandlerState:
    def __init__(self):
        self.condition = threading.Condition()
        self.release = [threading.Event() for _ in range(server_module.MAX_CONNECTIONS)]
        self.paths = []
        self.active = 0
        self.maximum = 0

    def wait_entered(self, number):
        with self.condition:
            assert self.condition.wait_for(lambda: len(self.paths) >= number, WAIT), "handler never entered"


class ControlledHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        state = self.server.state
        with state.condition:
            state.paths.append(self.path)
            state.active += 1
            state.maximum = max(state.maximum, state.active)
            state.condition.notify_all()
        try:
            if self.path.startswith("/block/"):
                index = int(self.path.rsplit("/", 1)[1])
                assert state.release[index].wait(WAIT * 3), "test did not release blocked handler"
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"ok")
        finally:
            with state.condition:
                state.active -= 1
                state.condition.notify_all()


@contextmanager
def running_server():
    server = server_module._Server(("127.0.0.1", 0), ControlledHandler, None)
    server.state = HandlerState()
    server.slots = AdmissionProbe(server.slots)
    errors = []
    server.handle_error = lambda request, address: errors.append(sys.exc_info()[1])
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": POLL_INTERVAL}, daemon=True)
    thread.start()
    clients = []

    def request(path):
        client = socket.create_connection(server.server_address, timeout=WAIT)
        client.settimeout(WAIT)
        clients.append(client)
        client.sendall(f"GET {path} HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n".encode())
        return client

    try:
        yield server, request, errors
    finally:
        for release in server.state.release:
            release.set()
        server.shutdown()
        thread.join(WAIT)
        server.server_close()
        for client in clients:
            client.close()
        assert not thread.is_alive(), "test server did not shut down"
        server.slots.assert_recovered()


def saturated(server, request):
    clients = []
    for index in range(server_module.MAX_CONNECTIONS):
        clients.append(request(f"/block/{index}"))
        server.state.wait_entered(index + 1)
    assert server.state.active == 8
    return clients


def response(client):
    answer = http.client.HTTPResponse(client)
    answer.begin()
    body = answer.read()
    return answer, body


def release_and_recover(server, clients):
    for release in server.state.release:
        release.set()
    for client in clients:
        answer, body = response(client)
        assert answer.status == 200 and body == b"ok"
    server.slots.assert_recovered()
    assert server.state.maximum <= 8


def test_short_burst_waits_for_capacity_and_recovers_all_permits():
    with running_server() as (server, request, errors):
        blocked = saturated(server, request)
        ninth = request("/ninth")
        assert server.slots.waiting.wait(WAIT), "saturated connection was dropped instead of admitted to a bounded wait"
        assert len(server.state.paths) == 8
        server.state.release[0].set()
        answer, body = response(ninth)
        assert answer.status == 200 and body == b"ok"
        server.state.wait_entered(9)
        assert server.state.paths.count("/ninth") == 1
        release_and_recover(server, blocked)
        assert response(request("/after-burst"))[0].status == 200
        assert not errors


def test_persistent_overload_is_fixed_503_closed_and_never_dispatched(monkeypatch):
    monkeypatch.setattr(server_module, "ADMISSION_TIMEOUT", 0.1, raising=False)
    with running_server() as (server, request, errors):
        blocked = saturated(server, request)
        started = time.monotonic()
        overflow = request("/untrusted-secret-never-dispatched")
        answer, body = response(overflow)
        assert answer.status == 503
        assert time.monotonic() - started < 2.0
        assert answer.getheader("Connection") == "close"
        assert answer.getheader("Content-Length") == str(len(body))
        assert answer.getheader("Content-Type", "").startswith("text/plain")
        assert answer.getheader("Cache-Control") == "no-store"
        assert answer.getheader("X-Content-Type-Options") == "nosniff"
        assert "default-src 'none'" in answer.getheader("Content-Security-Policy", "")
        assert answer.getheader("X-Frame-Options") == "DENY"
        assert body and b"untrusted-secret" not in body
        try:
            assert overflow.recv(1) == b"", "503 must close the connection"
        except ConnectionResetError:
            # macOS can reset a closed socket with unread request bytes. Only the
            # terminal probe permits this; the complete 503 above must arrive.
            pass
        assert len(server.state.paths) == 8
        release_and_recover(server, blocked)
        assert response(request("/after-overload"))[0].status == 200
        assert not errors


class FailedWriteSocket:
    def __init__(self, socket_, attempted, closed):
        self.socket = socket_
        self.attempted = attempted
        self.closed = closed
        self.timeout = None

    def __getattr__(self, name):
        return getattr(self.socket, name)

    def settimeout(self, value):
        self.timeout = value
        self.socket.settimeout(value)

    def sendall(self, body):
        self.attempted.set()
        raise OSError("synthetic refusal write failure")

    def close(self):
        try:
            self.socket.close()
        finally:
            self.closed.set()


def test_failed_overload_write_closes_socket_and_preserves_capacity(monkeypatch):
    monkeypatch.setattr(server_module, "ADMISSION_TIMEOUT", 0.1, raising=False)
    with running_server() as (server, request, errors):
        blocked = saturated(server, request)
        get_request = server.get_request
        attempted, closed = threading.Event(), threading.Event()
        wrappers = []

        def fail_next_write():
            socket_, address = get_request()
            wrapper = FailedWriteSocket(socket_, attempted, closed)
            wrappers.append(wrapper)
            server.get_request = get_request
            return wrapper, address

        server.get_request = fail_next_write
        request("/failed-refusal")
        assert attempted.wait(WAIT), "overload did not attempt an explicit refusal"
        assert closed.wait(WAIT), "failed refusal did not close its socket"
        assert 0 < wrappers[0].timeout <= 0.1
        assert len(server.state.paths) == 8
        release_and_recover(server, blocked)
        assert response(request("/after-write-failure"))[0].status == 200
        assert not errors


def test_worker_start_failure_returns_its_permit(monkeypatch):
    with running_server() as (server, request, errors):
        start = threading.Thread.start
        failed = threading.Event()

        def fail_worker(thread):
            if getattr(thread, "_target", None) == server.process_request_thread:
                failed.set()
                raise RuntimeError("synthetic worker start failure")
            return start(thread)

        with monkeypatch.context() as patch:
            patch.setattr(threading.Thread, "start", fail_worker)
            request("/not-dispatched")
            assert failed.wait(WAIT)
            server.slots.assert_recovered()
        assert not server.state.paths
        assert response(request("/after-start-failure"))[0].status == 200
        assert len(errors) == 1 and str(errors[0]) == "synthetic worker start failure"


def test_shutdown_is_bounded_while_admission_waits(monkeypatch):
    timeout = 0.1
    monkeypatch.setattr(server_module, "ADMISSION_TIMEOUT", timeout, raising=False)
    with running_server() as (server, request, errors):
        blocked = saturated(server, request)
        request("/waiting-at-shutdown")
        assert server.slots.waiting.wait(WAIT)
        done = threading.Event()

        def shutdown():
            server.shutdown()
            done.set()

        stopping = threading.Thread(target=shutdown, daemon=True)
        started = time.monotonic()
        stopping.start()
        # Admission + refusal write + serve poll, with scheduler headroom; never wait for handlers.
        bound = timeout + 0.1 + POLL_INTERVAL + 1.0
        assert done.wait(bound), "shutdown waited indefinitely for saturated handlers"
        stopping.join(WAIT)
        assert time.monotonic() - started < bound
        release_and_recover(server, blocked)
        assert not errors
