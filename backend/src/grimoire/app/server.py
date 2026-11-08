"""The app's loopback HTTP server (044): the text API as ``POST /api/<op>``, pictures as ``GET /api/image``,
and the phone page as ``GET /``.

It listens on a loopback address only. Every request must name that address in its ``Host`` header, which
stops a web page from reaching it by DNS rebinding. Every ``/api`` request must carry the serve's random token
in ``X-Grimoire-Token``. A custom header forces a CORS preflight, which is never answered, so no other origin's
page can drive the library. The page is served with a content security policy that allows only its own script
and style, the page's own address for requests, and ``blob:`` pictures.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import secrets
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from grimoire.ledgers import AuthorityUnavailable
from grimoire.peersync.wire import is_loopback
from grimoire.textapi import ApiRefused, TextApi

#: The largest request body read: a 32 MiB picture is about 43 MiB as base64, plus its JSON.
MAX_BODY = 48 * 1024 * 1024
#: Connections handled at once; more are closed at once, so no number of clients can hold the process.
MAX_CONNECTIONS = 8
#: Seconds a connection may sit idle mid-request before it is dropped.
SOCKET_TIMEOUT = 30.0
#: Seconds a request may take to send its line and headers, however it trickles them; then it is dropped.
HEADER_DEADLINE = 10.0
#: A body gets this long, plus a second for each BODY_RATE bytes it declares, to arrive.
BODY_DEADLINE = 10.0
BODY_RATE = 1024 * 1024
TOKEN_HEADER = "X-Grimoire-Token"
PAGE = Path(__file__).with_name("static") / "index.html"
COMMON_HEADERS = (("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                  ("Referrer-Policy", "no-referrer"), ("X-Frame-Options", "DENY"),
                  ("Cross-Origin-Resource-Policy", "same-origin"))


def _inline_hash(page: str, tag: str) -> str:
    """The CSP source for the page's one inline ``<script>`` or ``<style>`` block."""
    start = page.index(f"<{tag}>") + len(tag) + 2
    end = page.index(f"</{tag}>", start)
    digest = hashlib.sha256(page[start:end].encode("utf-8")).digest()
    return f"'sha256-{base64.b64encode(digest).decode('ascii')}'"


def page_policy(page: str) -> str:
    return ("default-src 'none'; "
            f"script-src {_inline_hash(page, 'script')}; style-src {_inline_hash(page, 'style')}; "
            "img-src blob:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, handler, app: AppServer) -> None:  # type: ignore[no-untyped-def]
        self.app = app
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(address, handler)

    def process_request(self, request, client_address) -> None:  # type: ignore[no-untyped-def]
        if not self.slots.acquire(blocking=False):  # too many at once: drop this one, not the process
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:  # type: ignore[no-untyped-def]
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


    def handle_error(self, request, client_address) -> None:  # type: ignore[no-untyped-def]
        if isinstance(sys.exc_info()[1], OSError):  # a dropped or expired connection: nothing to report
            return
        super().handle_error(request, client_address)


class _DeadlineReader(io.RawIOBase):
    """The socket as a raw file whose every read waits no longer than the request's deadline.

    The deadline is kept by the reading thread itself. A timer that shuts the socket down from another thread
    frees the client, but on Windows it leaves the blocked read, and so the connection's slot, held until the
    socket's own timeout."""

    def __init__(self, connection: socket.socket) -> None:
        super().__init__()
        self._connection = connection
        self.until: float | None = None

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:  # type: ignore[no-untyped-def,override]
        wait = SOCKET_TIMEOUT
        if self.until is not None:
            wait = min(wait, self.until - time.monotonic())
            if wait <= 0:
                raise TimeoutError("the request's deadline passed")
        self._connection.settimeout(wait)
        return self._connection.recv_into(buffer)


class _V6Server(_Server):
    address_family = socket.AF_INET6


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    protocol_version = "HTTP/1.0"
    timeout = SOCKET_TIMEOUT
    server_version = "grimoire"
    sys_version = ""

    # -- deadlines -----------------------------------------------------------------------------------------
    def setup(self) -> None:
        super().setup()
        self.rfile.close()  # read through the deadline instead of the plain socket file
        self._reader = _DeadlineReader(self.connection)
        self.rfile = io.BufferedReader(self._reader)
        self._deadline(HEADER_DEADLINE)

    def _deadline(self, seconds: float | None) -> None:
        """Drop the connection unless what it is sending arrives within ``seconds`` (``None``: stop the clock).
        A per-recv timeout alone lets a client hold a slot forever by trickling a byte at a time."""
        self._reader.until = None if seconds is None else time.monotonic() + seconds
        if seconds is None:
            self.connection.settimeout(SOCKET_TIMEOUT)

    # -- responses ---------------------------------------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str, extra: tuple[tuple[str, str], ...] = ()) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in COMMON_HEADERS + extra:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, value: object) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8",
                   (("Content-Security-Policy", "default-src 'none'; sandbox"),))

    def _error(self, status: int, reason: str, message: str = "") -> None:
        self._json(status, {"error": reason, "message": message or reason})

    def log_message(self, format: str, *args) -> None:  # type: ignore[no-untyped-def]  # noqa: A002
        return  # a request line can carry a picture ref; nothing is written to the console per request

    # -- checks ------------------------------------------------------------------------------------------
    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self.server.app.hosts

    def _token_ok(self) -> bool:
        given = self.headers.get(TOKEN_HEADER, "")
        return hmac.compare_digest(given.encode("utf-8"), self.server.app.token.encode("utf-8"))

    def _guard(self) -> bool:
        """Host first, then the token: a refused request is answered before anything is read or done. The
        request target must be a plain path: an absolute-form target (``http://host/api/...``) would route as
        its path while dodging a check on the raw string."""
        if not self._host_ok():
            self._error(403, "bad_host", "this server answers only its own loopback address")
            return False
        if not self.path.startswith("/") or self.path.startswith("//"):
            self._error(400, "invalid_field", "the request target must be a path")
            return False
        if urlsplit(self.path).path.startswith("/api") and not self._token_ok():
            self._error(401, "unauthorized", "the token is missing or wrong")
            return False
        return True

    # -- routes ------------------------------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._deadline(None)  # the headers are in; a GET has no body
        if not self._guard():
            return
        parts = urlsplit(self.path)
        if parts.path == "/":
            app = self.server.app
            self._send(200, app.page, "text/html; charset=utf-8", (("Content-Security-Policy", app.policy),))
        elif parts.path == "/api/image":
            refs = parse_qs(parts.query).get("ref", [])
            if len(refs) != 1:
                self._error(400, "invalid_field", "ref is required, once")
                return
            try:
                media_type, data = self.server.app.image(refs[0])
            except ApiRefused as error:
                self._json(error.status, error.to_json())
                return
            except Exception:  # noqa: BLE001 -- a bug: logged here, never shown to the client
                self._failure()
                return
            if not media_type.startswith("image/"):
                media_type = "application/octet-stream"
            self._send(200, data, media_type, (("Content-Security-Policy", "default-src 'none'; sandbox"),))
        else:
            self._error(404, "not_found", "no such page")

    do_HEAD = do_GET  # noqa: N815

    def do_POST(self) -> None:  # noqa: N802
        self._deadline(None)
        if not self._guard():
            return
        parts = urlsplit(self.path)
        if not parts.path.startswith("/api/") or parts.query:
            self._error(404, "not_found", "no such page")
            return
        op = parts.path[len("/api/"):]
        if self.headers.get("Transfer-Encoding") is not None:
            self._error(411, "length_required", "send the body with a Content-Length")
            return
        length = self.headers.get("Content-Length")
        if length is None or not length.isascii() or not length.isdigit():
            self._error(411, "length_required", "send the body with a Content-Length")
            return
        if int(length) > MAX_BODY:
            self._error(413, "too_large", f"a request is at most {MAX_BODY} bytes")
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._error(415, "unsupported_media", "the body must be application/json")
            return
        self._deadline(BODY_DEADLINE + int(length) / BODY_RATE)
        try:
            body = self.rfile.read(int(length))
        except TimeoutError:  # the deadline passed: drop the connection, as for headers
            self.close_connection = True
            return
        self._deadline(None)  # read: an op may take as long as it takes (a model's answer)
        if len(body) != int(length):
            self._error(400, "invalid_field", "the body ended early")
            return
        try:
            args = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, ValueError, RecursionError):
            self._error(400, "invalid_field", "the body must be a JSON object")
            return
        if not isinstance(args, dict):
            self._error(400, "invalid_field", "the body must be a JSON object")
            return
        try:
            result = self.server.app.call(op, args)
        except ApiRefused as error:
            self._json(error.status, error.to_json())
        except AuthorityUnavailable as error:
            self._error(503, "authority_unavailable", f"the library's authority stopped; restart the app ({error})")
        except Exception:  # noqa: BLE001 -- a bug: logged here, never shown to the client
            self._failure()
        else:
            self._json(200, result)

    def _failure(self) -> None:
        print("grimoire app: an op failed:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        self._error(500, "internal", "the app failed; see its console")


class AppServer:
    """Serve ``api`` on a loopback ``host``. ``token`` (default: 32 random bytes, drawn per serve) is what
    every ``/api`` request must carry. Calls reach ``api`` one at a time (``TextApi`` serializes them)."""

    def __init__(self, api: TextApi, *, host: str = "127.0.0.1", port: int = 0, token: str | None = None) -> None:
        if not is_loopback(host):
            raise ValueError("the app serves loopback addresses only")
        self.api = api
        self.token = token if token is not None else secrets.token_urlsafe(32)
        page = PAGE.read_text(encoding="utf-8")
        self.page = page.encode("utf-8")
        self.policy = page_policy(page)
        server_class = _V6Server if ":" in host else _Server
        self._server = server_class((host, port), _Handler, self)
        bound_host, bound_port = self._server.server_address[:2]
        names = [f"[{bound_host}]" if ":" in bound_host else bound_host]
        if bound_host in ("127.0.0.1", "::1"):
            names.append("localhost")
        self.hosts = frozenset(f"{name}:{bound_port}" for name in names)
        self._state = threading.Lock()
        self._serving = self._closed = False

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return host, port

    @property
    def url(self) -> str:
        host, port = self.address
        shown = f"[{host}]" if ":" in host else host
        return f"http://{shown}:{port}/#token={self.token}"

    def call(self, op: str, args: dict) -> dict:
        return self.api.call(op, args)

    def image(self, ref: str) -> tuple[str, bytes]:
        return self.api.image(ref)

    def serve_forever(self) -> None:
        with self._state:
            if self._closed:
                return
            self._serving = True
        try:
            self._server.serve_forever(poll_interval=0.2)
        finally:
            with self._state:
                self._serving = False

    def close(self) -> None:
        """Stop serving (from another thread) and release the port; a server never started just closes."""
        with self._state:
            self._closed = True
            serving = self._serving
        if serving:
            self._server.shutdown()
        self._server.server_close()


__all__ = ["BODY_DEADLINE", "HEADER_DEADLINE", "MAX_BODY", "MAX_CONNECTIONS", "TOKEN_HEADER", "AppServer", "page_policy"]
