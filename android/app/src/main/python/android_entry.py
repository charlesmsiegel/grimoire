"""Android process entrypoint: configure the environment, bind, run the grimoire server.

Called from Kotlin (ServerRuntime.bootstrap) on a dedicated thread; start_server
blocks in uvicorn for the life of the process.

Ordering matters: the socket is bound and *listening*, and its port reported,
before anything of uvicorn or grimoire is imported. Those imports -- FastAPI,
pydantic, every route module, and the app `grimoire.main` builds as it loads --
are the longest thing this process does before it can answer, and reporting
first lets the WebView send its first request during them: the connection
waits in the listen backlog and is answered the moment uvicorn starts
accepting. If anything after the report fails, the socket is closed, so that
queued connection is reset rather than left hanging, and the exception still
propagates to Kotlin.

The port is the same on every launch of an install (`_bind_preferred`), which
makes the WebView's origin the same, which is what lets it keep anything at
all between launches; and the listener is created so that Nagle is off on
every connection it accepts (`_listen`).

HOME (not GRIMOIRE_HOME) is pointed at app storage so store/paths.py resolves
its default root and bootstrap pointer under the app dir while the
Storage-location settings page keeps working — an env GRIMOIRE_HOME would
permanently override the user's choice there.

The store also has to be *readable over USB*, which is why Kotlin hands us the
external files dir rather than the private one (ServerRuntime.bootstrap). That
intent needs one thing from this side: Android runs app processes with
``umask 0077``, so every file the store wrote landed 0600 and `adb pull`
answered "Permission denied" on all of them — the directory is setgid with
group ``ext_data_rw``, which adb belongs to, but the files granted that group
nothing.

Group bits alone are not enough, which cost a sync to discover. The store root
is setgid with group ``ext_data_rw``, so files the app created there did land in
a group adb could read -- but directories *adb itself* creates while pushing are
owned by ``shell`` with no setgid bit, and app-created subdirectories inside
those inherit the app's own group instead. adb is not in that group and cannot
chmod files it does not own, so the tree turns unreadable from the cable exactly
where a sync last wrote. `_open_store_to_usb` therefore opens the "other" bits
too, and walks the existing tree once to fix whatever a stricter umask already
wrote. Nothing else on the device gains access: Android 11+ keeps every other
app out of ``Android/data/<pkg>`` whatever the mode bits say, so the grant is to
USB (and the person holding the cable) alone.
"""

import contextlib
import os
import random
import socket

# Records which store path the one-time chmod walk has already covered. Kept
# beside the store rather than inside it so it never syncs to another device,
# and holding the path rather than being a bare flag so pointing the
# Storage-location page at a fresh directory migrates that one too.
_USB_SENTINEL = ".usb_readable"

# The port this install serves on, as decimal text. Beside the store for the
# sentinel's reason -- a port is this device's business, and a synced store
# must not carry one to another -- in the app's own files directory, which an
# app update leaves alone and AssetExtractor's re-extraction (it replaces
# `files/web` only) never touches.
_PORT_FILE = ".grimoire-port"

# Where an install's first port is drawn from. Below Linux's ephemeral range
# (32768-60999), which is where the kernel hands out bind(0) and outgoing
# connections, so the draw is not a port the kernel may already have given
# someone else; above the low registered ports, where other apps' fixed-port
# servers sit. Wide, so another app's fixed port rarely lands on the draw --
# and when one does, `_bind_preferred` falls back.
_PORT_RANGE = range(10000, 32768)


def _open_store_to_usb(home_dir: str) -> None:
    """Make the store group-readable so a USB sync can see it (see module doc).

    Fail-soft throughout: a store the app cannot chmod is a store the user
    cannot sync over the cable, which is a worse outcome than an unsyncable
    store only if it also stops the app from starting. It must not.
    """
    os.umask(0o000)
    try:
        from grimoire.store import paths

        root = paths.home()
        sentinel = os.path.join(home_dir, _USB_SENTINEL)
        try:
            with open(sentinel, encoding="utf-8") as fh:
                if fh.read().strip() == str(root):
                    return
        except OSError:
            pass
        if not root.is_dir():
            return  # nothing written yet; the umask covers everything from here
        for dirpath, _dirnames, filenames in os.walk(root):
            try:
                os.chmod(dirpath, 0o777)
            except OSError:
                # Not `continue`: a directory this process does not own is
                # exactly where the unreadable files are. A sync pushes into
                # the store as the `shell` user, so the directories it creates
                # belong to shell and refuse our chmod -- while the files the
                # app later writes inside them are ours to fix and are the only
                # copies of that work. Skipping them here is how the whole of a
                # campaign written since the last sync stayed unreadable.
                pass
            for name in filenames:
                try:
                    os.chmod(os.path.join(dirpath, name), 0o666)
                except OSError:
                    pass
        with open(sentinel, "w", encoding="utf-8") as fh:
            fh.write(str(root))
    except Exception:  # noqa: BLE001 - never block startup on a permissions tweak
        pass


def _listen(port: int) -> socket.socket:
    """A loopback listener on ``port`` (0 for any), with Nagle off.

    ``IPPROTO_TCP`` is passed explicitly because nothing else turns Nagle off.
    ``socket(AF_INET, SOCK_STREAM)`` reports proto 0; asyncio sets TCP_NODELAY
    on an accepted connection only when the listener's proto *is*
    ``IPPROTO_TCP`` (accepted sockets inherit it), and uvicorn sets nothing
    itself. So every response on a reused keep-alive connection -- headers and
    body go out as separate writes -- held its last small segment until the
    WebView's delayed ACK released it, tens of milliseconds per request on
    what should be a loopback round trip. TCP_NODELAY on the listener as well,
    which Linux copies onto each accepted socket, so the guarantee does not
    rest on one asyncio detail.

    ``SO_REUSEADDR`` is what lets a relaunch rebind the port it had a moment
    ago while the old process's connections sit in TIME_WAIT -- without it a
    stable port would move after every force-stop. It does not let a second
    live listener share the port (Linux refuses that, which is exactly the
    signal `_bind_preferred` falls back on).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(64)
    except OSError:
        sock.close()
        raise
    return sock


def _recorded_port(home_dir: str) -> int | None:
    """The port `_bind_preferred` recorded, or None for anything unusable.

    A missing, unreadable or damaged record costs one fresh draw, never a
    failed start -- and a value outside the unprivileged range is damage, not
    an instruction to bind port 80.
    """
    try:
        with open(os.path.join(home_dir, _PORT_FILE), encoding="utf-8") as fh:
            port = int(fh.read().strip())
    except (OSError, ValueError):
        return None
    return port if 1024 <= port <= 65535 else None


def _bind_preferred(home_dir: str) -> socket.socket:
    """Listen on this install's port: the recorded one, else a fresh draw.

    The WebView keys everything it keeps on the origin, and the port is part
    of the origin: its HTTP cache (the content-hashed bundles and the
    ``?v=``-stamped images, both served as immutable), V8's code cache for
    that bundle, and localStorage -- which holds ``grimoire.runs.pending``, the
    unsent text the frontend rescues for exactly the relaunch after Android
    killed the process. Binding ``127.0.0.1:0`` gave every launch a new origin
    and threw all of it away. Keeping the HTTP cache is safe only because
    `main.SPAStaticFiles` marks the document ``no-cache``: an index.html kept
    past an APK update would name bundles the update deleted.

    Loopback is device-wide, so another app may hold the port. That launch
    takes whatever the OS gives it and records *that*, because it is the
    origin now holding this session's localStorage, so it is the one to come
    back to. Only the port this process bound is ever reported, so a squatter
    on the old one is never what the WebView loads. The record is written
    only when it changes, and fail-soft: losing it costs a stable origin,
    while failing to bind would cost the app.
    """
    recorded = _recorded_port(home_dir)
    try:
        sock = _listen(recorded or random.choice(_PORT_RANGE))
    except OSError:
        sock = _listen(0)
    port = sock.getsockname()[1]
    if port != recorded:
        with (contextlib.suppress(OSError),
              open(os.path.join(home_dir, _PORT_FILE), "w", encoding="utf-8") as fh):
            fh.write(str(port))
    return sock


def start_server(home_dir: str, dist_dir: str, templates_dir: str, callback,
                 runs=None) -> None:
    """Serve the app, and -- on Android -- let the shell know about live runs.

    `runs` is the Kotlin `RunCallback`, or `None` on any host that has no
    shell to tell (the desktop entry point, and every test). It is what makes a
    detached turn survive a locked phone: without a foreground service the OS
    may reclaim this process mid-generation, and without the terminal callback
    nothing tells the player their reply arrived.

    Stashed on `app.state` rather than passed down, because the two things that
    need it -- the run registry and the runner -- are reached from request
    handlers and from the lifespan loop respectively, and neither takes a
    parameter from here.
    """
    os.environ["HOME"] = home_dir
    os.environ["GRIMOIRE_DIST"] = dist_dir
    os.environ["GRIMOIRE_TEMPLATES"] = templates_dir
    os.environ.pop("GRIMOIRE_HOME", None)

    # Nothing above this line imports anything heavier than the stdlib: see the
    # module docstring for why the port goes out first.
    sock = _bind_preferred(home_dir)
    try:
        callback.onPort(sock.getsockname()[1])

        _open_store_to_usb(home_dir)

        import uvicorn

        # Importing `grimoire.main` builds the app, and that is the one served.
        # This used to call `create_app()` again, which analysed every route a
        # second time to build an object that replaced one nobody had used.
        from grimoire.main import app

        if runs is not None:
            # Fail-soft at the boundary, and again at each call site: a shell that
            # cannot promote itself is a degraded install, not a broken turn.
            app.state.runs.set_live_sink(runs.onRunsChanged)
            app.state.on_run_terminal = runs.onRunTerminal

        config = uvicorn.Config(
            app,
            # explicit pure-python implementations: uvicorn[standard]'s compiled
            # extras live in the pyproject `desktop` extra and aren't installed here
            http="h11",
            loop="asyncio",
            log_level="info",
            # One formatted line per request on stdout, which Chaquopy forwards
            # to logcat: a write on the event loop for every image in a portrait
            # grid, read by nobody. The app's own log is `store/logs.py`, which
            # never carried access lines; uvicorn's startup and error lines stay.
            access_log=False,
        )
        uvicorn.Server(config).run(sockets=[sock])
    finally:
        # uvicorn closes it on a clean shutdown. Every other way out -- a failed
        # import, a lifespan that refused to start -- would otherwise leave a
        # listener the WebView's queued connection waits on forever.
        sock.close()
