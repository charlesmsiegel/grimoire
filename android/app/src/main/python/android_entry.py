"""Android process entrypoint: configure the environment, run the grimoire server.

Called from Kotlin (ServerRuntime.bootstrap) on a dedicated thread; start_server
blocks in uvicorn for the life of the process.

Ordering matters: the socket is bound and *listening* before the port is
reported, so the WebView may connect immediately — connections queue in the
backlog while the interpreter finishes importing the app.

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
nothing. `_open_store_to_usb` relaxes the umask so new files are group
read/write, and walks the existing tree once to fix what the old umask already
wrote. Nothing else on the device gains access: Android 11+ keeps every other
app out of ``Android/data/<pkg>`` regardless of mode bits, so the grant is to
USB (and the user holding the cable) alone.
"""

import os
import socket


# Records which store path the one-time chmod walk has already covered. Kept
# beside the store rather than inside it so it never syncs to another device,
# and holding the path rather than being a bare flag so pointing the
# Storage-location page at a fresh directory migrates that one too.
_USB_SENTINEL = ".usb_readable"


def _open_store_to_usb(home_dir: str) -> None:
    """Make the store group-readable so a USB sync can see it (see module doc).

    Fail-soft throughout: a store the app cannot chmod is a store the user
    cannot sync over the cable, which is a worse outcome than an unsyncable
    store only if it also stops the app from starting. It must not.
    """
    os.umask(0o007)
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
                os.chmod(dirpath, 0o770)
            except OSError:
                continue
            for name in filenames:
                try:
                    os.chmod(os.path.join(dirpath, name), 0o660)
                except OSError:
                    pass
        with open(sentinel, "w", encoding="utf-8") as fh:
            fh.write(str(root))
    except Exception:  # noqa: BLE001 - never block startup on a permissions tweak
        pass


def start_server(home_dir: str, dist_dir: str, templates_dir: str, callback) -> None:
    os.environ["HOME"] = home_dir
    os.environ["GRIMOIRE_DIST"] = dist_dir
    os.environ["GRIMOIRE_TEMPLATES"] = templates_dir
    os.environ.pop("GRIMOIRE_HOME", None)

    _open_store_to_usb(home_dir)

    import uvicorn

    from grimoire.main import create_app

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    callback.onPort(sock.getsockname()[1])

    config = uvicorn.Config(
        create_app(),
        # explicit pure-python implementations: uvicorn[standard]'s compiled
        # extras live in the pyproject `desktop` extra and aren't installed here
        http="h11",
        loop="asyncio",
        log_level="info",
    )
    uvicorn.Server(config).run(sockets=[sock])
