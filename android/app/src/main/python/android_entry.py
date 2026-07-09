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
"""

import os
import socket


def start_server(home_dir: str, dist_dir: str, templates_dir: str, callback) -> None:
    os.environ["HOME"] = home_dir
    os.environ["GRIMOIRE_DIST"] = dist_dir
    os.environ["GRIMOIRE_TEMPLATES"] = templates_dir
    os.environ.pop("GRIMOIRE_HOME", None)

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
