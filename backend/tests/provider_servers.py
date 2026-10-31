"""Loopback servers for provider transport tests: synthetic, local, never billed.

``sse_server`` speaks HTTP/1.1 and streams with chunked transfer encoding, as
hosted providers do. ``raw_server`` misbehaves at the socket level (never
answers, closes, resets). Both bind ``127.0.0.1`` only and release stalled
handlers at teardown.
"""

from __future__ import annotations

import contextlib
import json
import socket
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def sse_lines(deltas=("Hel", "lo", "!"), *, finish="stop", usage=None, model="synthetic-model",
              done=True) -> list[bytes]:
    """Events as a real OpenAI-compatible server emits them (null finish reasons)."""
    events = [{"model": model, "choices": [{"delta": {"content": d}, "finish_reason": None}]}
              for d in deltas]
    events.append({"model": model, "choices": [{"delta": {}, "finish_reason": finish}]})
    if usage is not None:
        events.append({"model": model, "choices": [], "usage": usage})
    lines = [b"data: " + json.dumps(e).encode() + b"\n\n" for e in events]
    if done:
        lines.append(b"data: [DONE]\n\n")
    return lines


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep test output quiet
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.requests.append((self.path, dict(self.headers), body))
        self.server.behavior(self)

    def start_stream(self, status=200, headers=None, chunked=True):
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        if chunked:
            self.send_header("Transfer-Encoding", "chunked")
        else:
            self.send_header("Connection", "close")
        self.end_headers()

    def chunk(self, data: bytes) -> None:
        self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
        self.wfile.flush()

    def end_chunks(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def respond(self, status: int, body: bytes = b"", headers=None) -> None:
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def stream(lines, *, chunked=True, terminate=True):
    """A behavior that streams ``lines`` and ends cleanly (or not)."""
    def behavior(handler):
        handler.start_stream(chunked=chunked)
        for line in lines:
            if chunked:
                handler.chunk(line)
            else:
                handler.wfile.write(line)
                handler.wfile.flush()
        if chunked and terminate:
            handler.end_chunks()
        handler.close_connection = True
    return behavior


@contextlib.contextmanager
def sse_server(behavior):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.behavior = behavior
    server.requests = []
    server.release = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


def base_url(server, scheme="http") -> str:
    host, port = server.server_address[:2]
    return f"{scheme}://{host}:{port}/v1"


class RawServer:
    """Accepts connections and runs ``behavior(conn, self)`` on each, in a thread."""

    def __init__(self, behavior):
        self.behavior = behavior
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.release = threading.Event()
        self.received = bytearray()
        self.notes: list[str] = []
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._accept = threading.Thread(target=self._loop, daemon=True)
        self._accept.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            thread = threading.Thread(target=self._serve, args=(conn,), daemon=True)
            self._threads.append(thread)
            thread.start()

    def _serve(self, conn):
        try:
            self.behavior(conn, self)
        except OSError:
            pass
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    def read_request(self, conn, timeout=5.0) -> bytes:
        """Read the request headers and Content-Length body."""
        conn.settimeout(timeout)
        data = b""
        while b"\r\n\r\n" not in data:
            piece = conn.recv(65536)
            if not piece:
                return data
            data += piece
        head, _, rest = data.partition(b"\r\n\r\n")
        length = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1])
        while len(rest) < length:
            piece = conn.recv(65536)
            if not piece:
                break
            rest += piece
        self.received += data[:len(head) + 4] + rest
        return data

    def url(self, scheme="http") -> str:
        return f"{scheme}://127.0.0.1:{self.port}/v1"

    def close(self):
        self.release.set()
        self._stop.set()
        self._accept.join(5)
        for thread in self._threads:
            thread.join(5)
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def reset(conn) -> None:
    """Abort the connection with RST (SO_LINGER 0) rather than a clean FIN."""
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("HH" if sys.platform == "win32" else "ii", 1, 0))
    conn.close()


STREAM_HEAD = (b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
               b"Transfer-Encoding: chunked\r\n\r\n")


def raw_chunk(data: bytes) -> bytes:
    return b"%x\r\n%s\r\n" % (len(data), data)
