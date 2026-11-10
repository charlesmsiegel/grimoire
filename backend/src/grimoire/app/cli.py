"""``python -m grimoire.app``: set a library up for the app, pair it with another device, and serve it (044).

    init LIBRARY --device NAME [--owner human:owner]
    identify LIBRARY --device NAME
    pair LIBRARY --peer NAME [--key HEX] [--owner human:owner]
    serve LIBRARY [--owner human:owner] [--port N] [--peer NAME=HOST:PORT ...] [--sync-port N] [--model FILE]

Every address is loopback: 028's peer transport is authenticated, not encrypted. A phone reaches the
desktop through a forwarded port (docs/MOBILE.md). LIBRARY defaults to the V2 library location
(``GRIMOIRE_LIBRARY`` or the platform default).
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from grimoire.ledgers import DurableAuthority
from grimoire.library import resolve_library
from grimoire.peersync import PairingError, PeerServer, new_pairing_key, pair
from grimoire.peersync.wire import is_loopback
from grimoire.security import Grant, Permission, ResourcePath, is_human
from grimoire.store import LibraryLocked, LibraryStore, StoreError
from grimoire.sync import NoDeviceIdentity, SyncError, device_of, identify
from grimoire.sync.documents import check_device_id
from grimoire.textapi import TextApi

from .adapters import providers
from .server import AppServer

#: The one owner principal the app uses unless told otherwise. Both paired devices must use the same one:
#: who owns the table, and which player sits at it, is per library.
DEFAULT_OWNER = "human:owner"
#: The synchronized roots the owner writes (028).
SCOPES = ("sources", "campaigns", "assets", "kept")


class CliError(Exception):
    """A message for the person at the console; the command exits 2."""


def _library(value: str | None) -> Path:
    return Path(value) if value else resolve_library().path


def _open(path: Path) -> LibraryStore:
    try:
        return LibraryStore.open(path)
    except LibraryLocked:
        raise CliError(f"{path} is open in another process: stop `serve` first") from None
    except (StoreError, OSError, ValueError) as error:
        raise CliError(f"{path}: cannot open the library ({error})") from None


def _authority(store: LibraryStore, owner: str) -> DurableAuthority:
    try:
        return DurableAuthority.open(store, [owner])
    except Exception as error:  # noqa: BLE001 -- shown to the person, who chose the owner
        raise CliError(f"cannot open the library's authority as {owner} ({error})") from None


def _check_owner(owner: str) -> None:
    if not is_human(owner):
        raise CliError(f"--owner must be a human principal such as {DEFAULT_OWNER}, not {owner!r}")


def _check_device(device: str) -> None:
    try:
        check_device_id(device)
    except ValueError as error:
        raise CliError(f"--device {device!r}: {error}") from None


def init(args: argparse.Namespace) -> None:
    path = _library(args.library)
    _check_owner(args.owner)  # both before anything is written, so a refused init leaves nothing behind
    _check_device(args.device)
    try:
        store = LibraryStore.create(path)
    except (StoreError, OSError, ValueError) as error:
        raise CliError(f"{path}: cannot create a library ({error})") from None
    try:
        authority = _authority(store, args.owner)
        try:
            for scope in SCOPES:
                authority.add_grant(Grant(f"owner-{scope}", args.owner, frozenset({Permission.APPLY}),
                                          ResourcePath.parse(scope), args.owner))
        finally:
            authority.close()
        _identify(store, args.device)
    finally:
        store.close()
    print(f"{path}: a library for {args.owner} on device {args.device}")


def _identify(store: LibraryStore, device: str) -> None:
    try:
        identify(store, device)
    except (SyncError, StoreError, ValueError) as error:
        raise CliError(f"cannot name this device {device!r} ({error})") from None


def identify_command(args: argparse.Namespace) -> None:
    """Name an existing library's device once (019), so its new turns and pictures carry its tag."""
    path = _library(args.library)
    _check_device(args.device)
    store = _open(path)
    try:
        _identify(store, args.device)
    finally:
        store.close()
    print(f"{path}: device {args.device}")


def pair_command(args: argparse.Namespace) -> None:
    path = _library(args.library)
    if args.key is None:
        key = new_pairing_key()
    else:
        try:
            key = bytes.fromhex(args.key)
        except ValueError:
            raise CliError("--key must be the hex key the other device printed") from None
    _check_owner(args.owner)
    _check_device(args.peer)
    store = _open(path)
    try:
        try:
            device = device_of(store)
        except SyncError:  # unnamed or damaged: the hint names no device
            device = None
        authority = _authority(store, args.owner)
        try:
            pair(store, authority, args.peer, key, granted_by=args.owner)
        except PairingError as error:
            raise CliError(str(error)) from None
        finally:
            authority.close()
    finally:
        store.close()
    print(f"{path}: paired with {args.peer}")
    if args.key is None:
        name = device if device is not None else "<this device's name>"
        print(f"on {args.peer}, run: python -m grimoire.app pair --peer {name} --key {key.hex()}")


def _port(value: str) -> int:
    if not value.isascii() or not value.isdigit() or not 0 <= int(value) <= 65535:
        raise argparse.ArgumentTypeError("a port is a whole number from 0 to 65535")
    return int(value)


def _peer(value: str) -> tuple[str, tuple[str, int]]:
    name, sep, address = value.partition("=")
    host, colon, port = address.rpartition(":")
    host = host.strip("[]")
    if not sep or not colon or not name or not port.isascii() or not port.isdigit() \
            or not 0 < int(port) <= 65535 or not is_loopback(host):
        raise argparse.ArgumentTypeError("a peer is NAME=HOST:PORT on a loopback address")
    return name, (host, int(port))


def serve(args: argparse.Namespace) -> None:
    path = _library(args.library)
    store = _open(path)
    try:
        authority = _authority(store, args.owner)
        try:
            if args.peer or args.sync_port is not None:
                try:
                    device = device_of(store)
                except NoDeviceIdentity:
                    raise CliError("this library has no device name: run `init` (or 019's identify) first") from None
            api = TextApi(store, authority, owner=args.owner, peers=dict(args.peer))
            # 112: the model is the settings file's; one that is missing or unusable starts the app without a
            # model, and its Models and costs page says why and can set one up
            settings = providers.configure(api, args.model)
            if settings.problem is not None and args.model is not None:
                print(f"no model: {settings.problem['message']}", file=sys.stderr)
            try:
                app = AppServer(api, host=args.host, port=args.port)
            except OSError as error:
                raise CliError(f"cannot listen on {args.host}:{args.port} ({error.strerror or error}): "
                               "is another serve running? Pick another --port") from None
            peer_server = None
            if args.sync_port is not None:
                try:
                    peer_server = PeerServer(store, authority, host=args.host, port=args.sync_port)
                except OSError as error:
                    app.close()
                    raise CliError(f"cannot listen on {args.host}:{args.sync_port} for sync "
                                   f"({error.strerror or error}): pick another --sync-port") from None
                threading.Thread(target=peer_server.serve_forever, daemon=True).start()
                print(f"device {device}: paired devices sync on {args.host}:{peer_server.address[1]}")
            print(f"open {app.url}", flush=True)
            try:
                app.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                app.close()
                if peer_server is not None:
                    peer_server.close()
        finally:
            authority.close()
    finally:
        store.close()


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="python -m grimoire.app", description="Play a V2 library on this device.")
    commands = top.add_subparsers(dest="command", required=True)
    one = commands.add_parser("init", help="create an empty library for this device")
    one.add_argument("library", nargs="?")
    one.add_argument("--device", required=True, help="this device's name, such as phone or desktop")
    one.add_argument("--owner", default=DEFAULT_OWNER)
    one.set_defaults(run=init)
    named = commands.add_parser("identify", help="name an existing library's device, once")
    named.add_argument("library", nargs="?")
    named.add_argument("--device", required=True)
    named.set_defaults(run=identify_command)
    two = commands.add_parser("pair", help="pair this library with another device")
    two.add_argument("library", nargs="?")
    two.add_argument("--peer", required=True, help="the other device's name")
    two.add_argument("--key", help="the key the other device printed (none: print a new one)")
    two.add_argument("--owner", default=DEFAULT_OWNER)
    two.set_defaults(run=pair_command)
    three = commands.add_parser("serve", help="serve the library and the phone page on loopback")
    three.add_argument("library", nargs="?")
    three.add_argument("--owner", default=DEFAULT_OWNER)
    three.add_argument("--host", default="127.0.0.1")
    three.add_argument("--port", type=_port, default=8080)
    three.add_argument("--peer", type=_peer, action="append", default=[], help="NAME=HOST:PORT of a paired device")
    three.add_argument("--sync-port", type=_port, help="also serve this library to paired devices on this port")
    three.add_argument("--model", type=Path, help="the model settings file (JSON): created by the app when missing")
    three.set_defaults(run=serve)
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if getattr(args, "host", "127.0.0.1") and not is_loopback(getattr(args, "host", "127.0.0.1")):
            raise CliError("--host must be a loopback address")
        args.run(args)
    except CliError as error:
        print(f"grimoire app: {error}", file=sys.stderr)
        return 2
    return 0


__all__ = ["DEFAULT_OWNER", "main", "parser"]
