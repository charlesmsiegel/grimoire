"""``python -m grimoire.app``: set a library up for the app, pair it with another device, and serve it (044).

    init LIBRARY --device NAME [--owner human:owner]
    identify LIBRARY --device NAME
    pair LIBRARY --peer NAME [--key HEX] [--owner human:owner]
    serve LIBRARY [--owner human:owner] [--port N] [--peer NAME=HOST:PORT ...] [--sync-port N] [--model FILE]
          [--setup]

``serve`` with no LIBRARY, where the V2 library location has no library yet, serves the first-run page, which
makes or opens a library in the browser (108). A LIBRARY that is named and holds nothing is refused, unless
``--setup`` is given: a mistyped path or an unmounted drive must not quietly become a second library.

Every address is loopback: 028's peer transport is authenticated, not encrypted. A phone reaches the
desktop through a forwarded port (docs/MOBILE.md). LIBRARY defaults to the V2 library location
(``GRIMOIRE_LIBRARY`` or the platform default).
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

from grimoire.ledgers import DurableAuthority
from grimoire.library import resolve_library
from grimoire.peersync import PairingError, PeerServer, new_pairing_key, pair
from grimoire.peersync.wire import is_loopback
from grimoire.security import is_human
from grimoire.store import LibraryStore
from grimoire.sync import NoDeviceIdentity, SyncError, device_of
from grimoire.textapi import TextApi

from .adapters import providers
from .appapi import AppApi
from .campaign_config import CampaignConfigError, load_campaign_config
from .campaign_services import configure as configure_campaign, validate_credentials
from .launcher import Launcher
from .libraries import (
    DEFAULT_OWNER,
    SetupError,
    check_device,
    create_library,
    inspect,
    name_device,
    open_authority,
    open_store,
)
from .server import AppServer


class CliError(Exception):
    """A message for the person at the console; the command exits 2."""


def _library(value: str | None) -> Path:
    # A path typed at the console is relative to where it was typed, as `init` and the store take it; the
    # browser's first-run page takes full paths only, since it has no such place.
    return Path(os.path.abspath(os.path.expanduser(value))) if value else resolve_library().path


def _console(error: SetupError) -> CliError:
    return CliError(str(error))


def _open(path: Path) -> LibraryStore:
    try:
        return open_store(path)
    except SetupError as error:
        raise _console(error) from None


def _authority(store: LibraryStore, owner: str) -> DurableAuthority:
    try:
        return open_authority(store, owner)
    except SetupError as error:
        raise _console(error) from None


def _check_owner(owner: str) -> None:
    if not is_human(owner):
        raise CliError(f"--owner must be a human principal such as {DEFAULT_OWNER}, not {owner!r}")


def _check_device(device: str) -> None:
    try:
        check_device(device)
    except SetupError as error:
        raise CliError(f"--{error}") from None


def init(args: argparse.Namespace) -> None:
    path = _library(args.library)
    _check_owner(args.owner)  # both before anything is written, so a refused init leaves nothing behind
    _check_device(args.device)
    try:
        create_library(path, device=args.device, owner=args.owner)
    except SetupError as error:
        raise _console(error) from None
    print(f"{path}: a library for {args.owner} on device {args.device}")


def _identify(store: LibraryStore, device: str) -> None:
    try:
        name_device(store, device)
    except SetupError as error:
        raise _console(error) from None


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
    """Serve the library at LIBRARY. Where there is none yet (a new place, or an empty folder) and LIBRARY was
    left to the default location or ``--setup`` was given, serve the first-run page instead, which sets one up
    there or elsewhere in the browser (108)."""
    path = _library(args.library)
    _check_owner(args.owner)
    try:
        place = inspect(path)
    except SetupError as error:
        raise _console(error) from None
    peers = dict(args.peer)
    try:
        campaign_config = load_campaign_config(args.campaign_config)
        validate_credentials(campaign_config)
    except CampaignConfigError as error:
        raise CliError(str(error)) from None

    def build(store: LibraryStore, authority: DurableAuthority) -> TextApi:
        api = TextApi(store, authority, owner=args.owner, peers=peers,
                      stages=campaign_config.stages, agendas=campaign_config.agendas)
        try:
            configure_campaign(api, campaign_config)
        except CampaignConfigError as error:
            raise CliError(str(error)) from None
        # 112: the model is the settings file's; one that is missing or unusable starts the app without a
        # model, and its Models and costs page says why and can set one up
        settings = providers.configure(api, args.model)
        if settings.problem is not None and args.model is not None:
            print(f"no model: {settings.problem['message']}", file=sys.stderr)
        return api

    if place.state == "library":
        _serve_library(args, place.path, build)
        return
    if place.state == "invalid":
        raise CliError(f"{place.path} has a library marker this version cannot open (another version, or damaged)")
    if place.state != "new" and place.state != "empty":
        raise CliError(f"{place.path} holds something other than a library: pick an empty or new folder")
    if args.library and not args.setup:
        raise CliError(f"{place.path} holds no library: check the path, run `init` there, or add --setup to make "
                       "one in the browser")
    if args.peer or args.sync_port is not None:
        raise CliError(f"{place.path} has no library yet: run `init` (or open the app without --peer and "
                       "--sync-port) first")
    launcher = Launcher(owner=args.owner, build=build, suggested=place.path,
                        generation=providers.usable(args.model, args.owner))
    try:
        app = _listen(launcher, args)
        print(f"no library at {place.path} yet: open the address to set one up")
        _run(app, None)
    finally:
        launcher.close()


def _serve_library(args: argparse.Namespace, path: Path, build) -> None:  # type: ignore[no-untyped-def]
    store = _open(path)
    try:
        authority = _authority(store, args.owner)
        try:
            if args.peer or args.sync_port is not None:
                try:
                    device = device_of(store)
                except NoDeviceIdentity:
                    raise CliError("this library has no device name: run `init` (or 019's identify) first") from None
            launcher = Launcher(owner=args.owner, build=build, suggested=path,
                                opened=(path, AppApi(build(store, authority))))
            app = _listen(launcher, args)
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
            _run(app, peer_server)
        finally:
            authority.close()
    finally:
        store.close()


def _listen(launcher: Launcher, args: argparse.Namespace) -> AppServer:
    try:
        return AppServer(launcher, host=args.host, port=args.port)
    except OSError as error:
        raise CliError(f"cannot listen on {args.host}:{args.port} ({error.strerror or error}): "
                       "is another serve running? Pick another --port") from None


def _run(app: AppServer, peer_server: PeerServer | None) -> None:
    print(f"open {app.url}", flush=True)
    try:
        app.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        if peer_server is not None:
            peer_server.close()


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
    three = commands.add_parser("serve", help="serve the library (or, with none yet, its first-run page) on loopback")
    three.add_argument("library", nargs="?")
    three.add_argument("--owner", default=DEFAULT_OWNER)
    three.add_argument("--host", default="127.0.0.1")
    three.add_argument("--port", type=_port, default=8080)
    three.add_argument("--peer", type=_peer, action="append", default=[], help="NAME=HOST:PORT of a paired device")
    three.add_argument("--sync-port", type=_port, help="also serve this library to paired devices on this port")
    three.add_argument("--campaign-config", type=Path,
                       help="read-only authored stages, NPC agendas and optional labeling model (JSON)")
    three.add_argument("--model", type=Path, help="the model settings file (JSON): created by the app when missing")
    three.add_argument("--setup", action="store_true",
                       help="where LIBRARY holds no library yet, serve the first-run page to make one")
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
