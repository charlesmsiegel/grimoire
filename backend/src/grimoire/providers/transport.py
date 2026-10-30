"""The transport seam and the standard-library HTTP transport.

Each failure is reported with the phase it happened in, because that decides
billing: a failure in ``connect`` sent nothing (a known zero); anything after
``connect()`` returned may have reached the provider (unknown usage).

Exceptions from ``http.client`` and ``ssl`` are converted *outside* their
``except`` block, so neither their messages nor their frames (whose locals can
hold the Authorization header) survive in the error the caller sees.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import threading
import time
from collections.abc import Mapping
from typing import Protocol
from urllib.parse import urlsplit

from .cancel import CancelToken
from .config import Secret, Timeouts
from .errors import TransportError


class Response(Protocol):
    status: int
    headers: Mapping[str, str]

    def read_line(self, max_bytes: int, timeout: float) -> bytes:
        """One line (``b""`` at EOF). Raises ``TransportError(kind, "read")``."""

    def error_body(self, limit: int) -> bytes:
        """Up to ``limit`` bytes of the remaining body; ``b""`` if it cannot be read."""


class Connection(Protocol):
    peer_loopback: bool

    def send(self, body: bytes, headers: Mapping[str, str], key: Secret | None) -> Response:
        """Write the request and read the status. Raises ``TransportError(kind, "send")``."""

    def close(self) -> None: ...


class Transport(Protocol):
    def connect(self, url: str, timeouts: Timeouts, cancel: CancelToken) -> Connection:
        """Open a connection. Raises ``TransportError(kind, "connect")``: nothing was sent."""


def _kind(error: BaseException) -> str:
    """Classify a socket/HTTP failure. Order matters: several classes overlap."""
    if isinstance(error, ssl.SSLCertVerificationError):
        return "tls"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ConnectionError):  # includes RemoteDisconnected
        return "network"
    if isinstance(error, http.client.IncompleteRead):
        return "truncated"
    if isinstance(error, http.client.HTTPException):
        return "bad_response"
    if isinstance(error, ssl.SSLError) and not isinstance(
            error, (ssl.SSLEOFError, ssl.SSLZeroReturnError, ssl.SSLSyscallError)):
        return "tls"  # a protocol failure (say, plaintext on a TLS port): retrying won't help
    return "network"


def _failure(error: BaseException) -> object:
    """What to raise once outside the ``except`` block.

    An ordinary error becomes a transport kind. Anything else (an interrupt, a
    cancellation from another framework) is re-raised as a fresh instance of
    its own class, without the message or frames that could hold a header.
    """
    if isinstance(error, Exception):
        return _kind(error)
    if isinstance(error, SystemExit):
        code = error.code
        return SystemExit(code if code is None or isinstance(code, int) else 1)
    return type(error)


def _raise(failure: object, phase: str):
    if isinstance(failure, str):
        raise TransportError(failure, phase) from None
    if isinstance(failure, BaseException):
        raise failure from None
    try:
        fresh = failure()
    except Exception:
        fresh = failure.__new__(failure)
    raise fresh from None


def _is_loopback(address: object) -> bool:
    try:
        return ipaddress.ip_address(str(address).split("%")[0]).is_loopback
    except ValueError:
        return False


class _HttpResponse:
    def __init__(self, response: http.client.HTTPResponse, sock: socket.socket) -> None:
        self._response = response
        self._sock = sock
        self.status = response.status
        self.headers = {name.lower(): value for name, value in response.getheaders()}

    def read_line(self, max_bytes: int, timeout: float) -> bytes:
        failure = None
        try:
            self._sock.settimeout(max(timeout, 0.001))
            line = self._response.readline(max_bytes + 1)
        except BaseException as error:  # converted below, outside this block
            failure = _failure(error)
        if failure is not None:
            _raise(failure, "read")
        if len(line) > max_bytes:
            raise TransportError("bad_response", "read")
        return line

    def error_body(self, limit: int) -> bytes:
        try:
            return self._response.read(limit)
        except Exception:
            return b""

    def __repr__(self) -> str:
        return f"<response {self.status}>"


class _HttpConnection:
    def __init__(self, connection: http.client.HTTPConnection, path: str, host: str,
                 peer_loopback: bool, cancel: CancelToken, timeouts: Timeouts,
                 started: float) -> None:
        self._connection = connection
        self._sock = connection.sock
        self._sock.settimeout(timeouts.read)  # waiting for the status is a read, not a connect
        self._path = path
        self._host = host
        self.peer_loopback = peer_loopback
        self._response: http.client.HTTPResponse | None = None
        self._hook = cancel._on_cancel(self._shutdown)
        self._cancel = cancel
        # The attempt's hard deadline: however slowly bytes trickle in, the
        # socket is shut down when ``total`` runs out.
        left = timeouts.total - (time.monotonic() - started)  # connecting counts too
        self._deadline = threading.Timer(max(left, 0.001), self._shutdown)
        self._deadline.daemon = True
        self._deadline.start()

    def _shutdown(self) -> None:
        """Wake a read blocked in another thread; closing stays with the owner."""
        try:
            socket.socket.shutdown(self._sock, socket.SHUT_RDWR)
        except (OSError, TypeError, AttributeError):
            pass

    def send(self, body: bytes, headers: Mapping[str, str], key: Secret | None) -> Response:
        failure = _write(self._connection, self._path, self._host, body, headers, key)
        if failure is not None:
            self.close()
            _raise(failure, "send")
        try:
            self._response = self._connection.getresponse()
        except BaseException as error:  # converted below, outside this block
            failure = _failure(error)
        if failure is not None:
            self.close()
            _raise(failure, "send")
        return _HttpResponse(self._response, self._sock)

    def close(self) -> None:
        self._deadline.cancel()
        self._cancel._clear(self._hook)
        for closeable in (self._response, self._connection):
            if closeable is not None:
                try:
                    closeable.close()
                except Exception:
                    pass

    def __repr__(self) -> str:
        return f"<connection {self._host}>"


def _write(connection: http.client.HTTPConnection, path: str, host: str, body: bytes,
           headers: Mapping[str, str], key: Secret | None):
    """Send one request. The only place a key becomes header text.

    Returns ``None``, or what to raise; the caller raises it outside any
    ``except`` block, so this frame (and http.client's) is not on its traceback.
    """
    failure = None
    try:
        connection.putrequest("POST", path, skip_host=True, skip_accept_encoding=True)
        connection.putheader("Host", host)
        for name, value in headers.items():
            connection.putheader(name, value)
        if key is not None:
            connection.putheader("Authorization", "Bearer " + key.reveal())
        connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
    except BaseException as error:  # converted by the caller, outside this block
        failure = _failure(error)
        try:
            connection._buffer.clear()  # a buffered Authorization line must not linger
        except Exception:
            pass
        try:
            connection.close()
        except Exception:
            pass
    return failure


class HttpTransport:
    """``http.client`` over TLS (or plain loopback HTTP). No proxy support, and no
    injection seams: the adapter labels its usage ``provider_reported``."""

    def connect(self, url: str, timeouts: Timeouts, cancel: CancelToken) -> Connection:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
        if parts.scheme == "https":
            connection: http.client.HTTPConnection = http.client.HTTPSConnection(
                host, port, timeout=timeouts.connect, context=ssl.create_default_context())
        else:
            connection = http.client.HTTPConnection(host, port, timeout=timeouts.connect)
        started = time.monotonic()
        failure = None
        try:
            connection.connect()
            peer = connection.sock.getpeername()[0]
        except BaseException as error:  # converted below, outside this block
            failure = _failure(error)
        if failure is not None:
            connection.close()
            _raise(failure, "connect")
        loopback = _is_loopback(peer)
        if parts.scheme == "http" and not loopback:
            connection.close()
            raise TransportError("peer_not_loopback", "connect")
        host_header = f"[{host}]" if ":" in host else host
        if port is not None:
            host_header = f"{host_header}:{port}"
        return _HttpConnection(connection, parts.path or "/", host_header, loopback, cancel,
                               timeouts, started)

    def __repr__(self) -> str:
        return "HttpTransport()"
