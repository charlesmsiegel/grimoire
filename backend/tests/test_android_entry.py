"""The Android entry point's socket, port and startup order, without a device.

`android_entry.py` is packaged verbatim into the APK and nothing in the
ordinary gate runs it: `make check-apk` needs the Android toolchain, and even
that only proves it compiles. What it decides is all visible from here, though
-- which socket the WebView talks to, which origin it gets, and what the
process does before it can answer -- so it is imported by file path (it is not
a module of the `grimoire` package, and putting its directory on `sys.path`
for the whole session would let any test import it by accident) and driven
with the server itself faked out.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import socket
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ENTRY = ROOT / "android" / "app" / "src" / "main" / "python" / "android_entry.py"

_spec = importlib.util.spec_from_file_location("android_entry", ENTRY)
assert _spec is not None and _spec.loader is not None
android_entry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(android_entry)

PORT_FILE = android_entry._PORT_FILE


def _free_port() -> int:
    """A port nothing is listening on right now (the usual bind-0-and-close)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _recorded(home: pathlib.Path) -> str:
    return (home / PORT_FILE).read_text(encoding="utf-8").strip()


# ---- the listener ----------------------------------------------------------


def test_the_listener_is_a_tcp_socket_with_nagle_off():
    """`socket(AF_INET, SOCK_STREAM)` reports proto 0, and asyncio sets
    TCP_NODELAY on an accepted connection only when the listener's proto is
    IPPROTO_TCP -- uvicorn sets nothing itself. Without it every response on a
    reused keep-alive connection waited out the client's delayed ACK."""
    with android_entry._listen(0) as sock:
        assert sock.proto == socket.IPPROTO_TCP
        assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
        host, port = sock.getsockname()
        assert host == "127.0.0.1"
        assert port > 0


def test_a_connection_accepted_the_way_uvicorn_accepts_it_has_nagle_off():
    """What the WebView actually talks to is the ACCEPTED socket, not the
    listener -- so check that end, through asyncio's own accept path."""
    seen: dict[str, int] = {}

    async def main() -> None:
        async def handle(reader, writer):
            seen["nodelay"] = writer.get_extra_info("socket").getsockopt(
                socket.IPPROTO_TCP, socket.TCP_NODELAY)
            writer.close()

        listener = android_entry._listen(0)
        server = await asyncio.start_server(handle, sock=listener)
        async with server:
            reader, writer = await asyncio.open_connection(*listener.getsockname())
            await reader.read()  # EOF once the handler has looked and closed
            writer.close()

    asyncio.run(main())
    assert seen["nodelay"]


# ---- the per-install port --------------------------------------------------


def test_a_free_recorded_port_is_bound_again(tmp_path):
    """The whole point: the same port, so the same origin, so the WebView keeps
    its HTTP cache, its code cache and its localStorage across launches."""
    port = _free_port()
    (tmp_path / PORT_FILE).write_text(str(port), encoding="utf-8")
    with android_entry._bind_preferred(str(tmp_path)) as sock:
        assert sock.getsockname()[1] == port
        assert sock.proto == socket.IPPROTO_TCP
    assert _recorded(tmp_path) == str(port)


@pytest.mark.skipif(sys.platform == "win32",
                    reason="SO_REUSEADDR on Windows lets a second socket bind a port "
                           "that is already listening, so the occupied case cannot be "
                           "staged there; the code this covers only runs on Android")
def test_an_occupied_recorded_port_falls_back_and_records_the_port_it_got(tmp_path):
    """Loopback is device-wide, so another app can hold the port. That launch
    takes whatever the OS gives it, and records THAT: it is the origin now
    holding this session's localStorage, so it is the one to come back to."""
    with android_entry._listen(0) as squatter:
        taken = squatter.getsockname()[1]
        (tmp_path / PORT_FILE).write_text(str(taken), encoding="utf-8")
        with android_entry._bind_preferred(str(tmp_path)) as sock:
            got = sock.getsockname()[1]
            assert got != taken
            assert sock.proto == socket.IPPROTO_TCP
            assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
    assert _recorded(tmp_path) == str(got)


def test_a_first_launch_draws_from_its_range_and_records_the_draw(tmp_path, monkeypatch):
    port = _free_port()
    monkeypatch.setattr(android_entry, "_PORT_RANGE", range(port, port + 1))
    with android_entry._bind_preferred(str(tmp_path)) as sock:
        assert sock.getsockname()[1] == port
    assert _recorded(tmp_path) == str(port)


def test_the_first_launch_range_sits_below_the_kernels_ephemeral_ports():
    """Linux hands out 32768-60999 for bind(0) and outgoing connections, so a
    port drawn from there is one the kernel may already have given someone
    else; and the low registered ports are where other apps' servers sit."""
    rng = android_entry._PORT_RANGE
    assert rng.start >= 1024
    assert rng.stop <= 32768
    assert len(rng) > 1000  # wide enough that two installs rarely collide


@pytest.mark.parametrize("junk", ["", "not a port", "0", "80", "70000", "-1"])
def test_a_record_that_is_not_a_usable_port_is_treated_as_absent(tmp_path, monkeypatch, junk):
    """Fail-soft: a damaged file costs one fresh draw, never a failed start
    (and never a bind to a privileged or impossible port)."""
    (tmp_path / PORT_FILE).write_text(junk, encoding="utf-8")
    port = _free_port()
    monkeypatch.setattr(android_entry, "_PORT_RANGE", range(port, port + 1))
    with android_entry._bind_preferred(str(tmp_path)) as sock:
        assert sock.getsockname()[1] == port
    assert _recorded(tmp_path) == str(port)


def test_a_home_that_cannot_hold_the_record_still_serves(tmp_path):
    """The record is a convenience. Losing it costs a stable origin, which is a
    cache miss; failing to bind would cost the app."""
    missing = tmp_path / "no" / "such" / "dir"
    with android_entry._bind_preferred(str(missing)) as sock:
        assert sock.getsockname()[1] > 0
    assert not missing.exists()


# ---- start_server's order --------------------------------------------------


class _Callback:
    def __init__(self, events: list[str], ports: list[int]):
        self._events, self._ports = events, ports

    def onPort(self, port: int) -> None:  # noqa: N802 - the Kotlin interface's name
        self._events.append("port")
        self._ports.append(port)


@pytest.fixture
def entry_env(monkeypatch, tmp_path):
    """start_server writes the process environment and, through the USB step,
    the process umask. Record the first so monkeypatch restores it; replace the
    second, which would otherwise leak into every test that runs after."""
    for var in ("HOME", "GRIMOIRE_DIST", "GRIMOIRE_TEMPLATES"):
        monkeypatch.setenv(var, str(tmp_path / "unused"))
    monkeypatch.delenv("GRIMOIRE_HOME", raising=False)
    events: list[str] = []
    monkeypatch.setattr(android_entry, "_open_store_to_usb",
                        lambda home: events.append("usb"))
    home = tmp_path / "home"
    home.mkdir()
    return types.SimpleNamespace(home=home, events=events, ports=[])


@pytest.mark.parametrize("module", ["uvicorn", "grimoire.main"])
def test_the_port_is_reported_before_the_app_is_imported_and_a_failure_closes_it(
        entry_env, monkeypatch, module):
    """Report first, import second: the WebView's first request waits in the
    listen backlog while the interpreter imports, instead of the socket not
    existing yet. And if the import then fails, the socket is closed -- the
    queued connection is reset rather than hung forever -- and the error
    still reaches Kotlin, whose `callAttr` is waiting on it."""
    monkeypatch.setitem(sys.modules, module, None)  # makes `import module` raise
    cb = _Callback(entry_env.events, entry_env.ports)
    with pytest.raises(ImportError) as raised:
        android_entry.start_server(str(entry_env.home), "dist", "templates", cb)
    assert entry_env.events[:2] == ["port", "usb"]
    [port] = entry_env.ports
    assert _recorded(entry_env.home) == str(port)
    # `raised` holds the traceback, and through it start_server's frame and
    # its socket -- as the PyException Kotlin receives does. So the close has
    # to be explicit: refcounting would only get to it once that is dropped.
    assert raised.traceback
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection(("127.0.0.1", port), timeout=5).close()


def test_the_server_runs_the_module_level_app_on_the_reported_socket(
        entry_env, monkeypatch):
    """One app per process. `grimoire.main` builds one when it is imported,
    and building a second meant analysing every route again for nothing."""
    import uvicorn

    import grimoire.main

    class _Registry:
        sink = None

        def set_live_sink(self, sink):
            self.sink = sink

    the_app = types.SimpleNamespace(state=types.SimpleNamespace(runs=_Registry()))
    monkeypatch.setattr(grimoire.main, "app", the_app)

    def _second_app():
        raise AssertionError("start_server built a second app")

    monkeypatch.setattr(grimoire.main, "create_app", _second_app)

    captured: dict = {}

    class _Config:
        def __init__(self, app, **kwargs):
            captured["app"] = app
            captured["kwargs"] = kwargs

    class _Server:
        def __init__(self, config):
            pass

        def run(self, sockets):
            captured["sockets"] = sockets
            captured["port"] = sockets[0].getsockname()[1]

    # Config's own __init__ reconfigures `logging` for the whole process.
    monkeypatch.setattr(uvicorn, "Config", _Config)
    monkeypatch.setattr(uvicorn, "Server", _Server)

    runs = types.SimpleNamespace(onRunsChanged=lambda live: None,
                                 onRunTerminal=lambda *args: None)
    cb = _Callback(entry_env.events, entry_env.ports)
    android_entry.start_server(str(entry_env.home), "dist", "templates", cb, runs)

    assert captured["app"] is the_app
    assert the_app.state.runs.sink is runs.onRunsChanged
    assert the_app.state.on_run_terminal is runs.onRunTerminal
    kwargs = captured["kwargs"]
    assert kwargs["access_log"] is False
    assert kwargs["http"] == "h11"
    assert kwargs["loop"] == "asyncio"
    assert entry_env.ports == [captured["port"]]
    assert entry_env.events[:2] == ["port", "usb"]
    # uvicorn closes it on a clean shutdown; start_server closes it however
    # the server stopped, so nothing is left accepting connections nobody
    # will answer.
    [sock] = captured["sockets"]
    assert sock.fileno() == -1, "the listener outlived the server"


def test_what_the_entry_writes_beside_the_store_is_never_synced():
    """Both files describe one device. They sit beside the store rather than
    in it, but the Storage-location page can point the store at HOME itself,
    and then only the sync script's skip list keeps one phone's port (or its
    chmod bookkeeping) off another. Held to the names this module writes, so
    renaming one here cannot quietly start syncing it."""
    spec = importlib.util.spec_from_file_location(
        "grimoire_sync", ROOT / "scripts" / "grimoire_sync.py")
    assert spec is not None and spec.loader is not None
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    assert {android_entry._PORT_FILE, android_entry._USB_SENTINEL} <= sync.SKIP_NAMES
