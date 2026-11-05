"""Serve one library to its paired devices: ``python -m grimoire.peersync serve LIBRARY --owner human:you``.

Prints ``listening <host> <port>`` once ready, then serves (``--once``: a single connection).
"""

from __future__ import annotations

import argparse
import sys

from grimoire.ledgers import DurableAuthority
from grimoire.store import LibraryStore

from .server import PeerServer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m grimoire.peersync")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve a library to its paired devices on loopback")
    serve.add_argument("library")
    serve.add_argument("--owner", action="append", required=True, help="the library's owner principal(s)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--once", action="store_true", help="serve one connection, then exit")
    arguments = parser.parse_args(argv)
    with LibraryStore.open(arguments.library) as store:
        authority = DurableAuthority.open(store, arguments.owner)
        try:
            server = PeerServer(store, authority, host=arguments.host, port=arguments.port)
            try:
                host, port = server.address
                print(f"listening {host} {port}", flush=True)
                if arguments.once:
                    server.serve_one()
                else:
                    server.serve_forever()
            finally:
                server.close()
        finally:
            authority.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
