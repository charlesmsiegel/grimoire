"""The app before and after a library is open (108): what the server calls when ``serve`` starts on a place with
no library yet, so the first-run page can set one up in the browser.

A ``Launcher`` serves the ``setup.*`` ops at any time and, once a library is open, every op of that library's
``AppApi`` too. Until then the library's ops answer ``no_library``. Making and opening go through
``libraries`` exactly as ``init`` does, so a library is never made inside, around or beside a V1 library, and a
refused place has nothing written to it. One library per serve: once one is open, ``setup.create`` and
``setup.open`` answer ``library_open`` (stop ``serve`` to switch). Once closed, every op answers ``closed``, so a
request still in flight as the serve stops never reaches a store that is shut.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from grimoire.ledgers import DurableAuthority
from grimoire.store import LibraryStore, StoreError
from grimoire.sync import NoDeviceIdentity, SyncError, device_of
from grimoire.textapi import ApiRefused, TextApi, field

from .adapter import Adapter
from .appapi import AppApi
from .libraries import SetupError, check_device, create_library, inspect, name_device, open_authority, open_store

#: The ops a launcher serves whatever its state.
SETUP_OPS = ("setup.check", "setup.create", "setup.open", "setup.status")
#: A refusal about the place the person typed is their input; any other is the state of things.
_BAD_INPUT = frozenset({"bad_path", "invalid_device", "invalid_owner"})

#: Builds the text API over a library the launcher has just opened (the serve's runtime, policy and peers).
BuildApi = Callable[[LibraryStore, DurableAuthority], TextApi]


class Launcher:
    """The app's ops through first run. ``owner`` is the serve's owner; ``suggested`` the place ``serve`` was
    pointed at (or the platform default), offered first. ``build`` makes the text API once a library is open;
    ``generation`` says whether the serve has a model configured (``--model``), shown before a library exists.
    ``opened`` is a library the serve already opened itself: its path (``None`` where only its API was given) and
    API, which the launcher does not close."""

    def __init__(self, *, owner: str, build: BuildApi, suggested: Path | None = None, generation: bool = False,
                 adapters: tuple[Adapter, ...] | None = None, opened: tuple[Path | None, AppApi] | None = None) -> None:
        self.owner = owner
        self.suggested = suggested
        self.generation = generation
        self._build = build
        self._adapters = adapters
        self._lock = threading.Lock()
        self._closed = False
        self._owned: tuple[LibraryStore, DurableAuthority] | None = None
        self._path, self._app = opened if opened is not None else (None, None)
        self._handlers: Mapping[str, Callable[[Mapping[str, object]], dict]] = {
            "setup.status": self._status, "setup.check": self._check, "setup.create": self._create,
            "setup.open": self._open,
        }

    @property
    def is_open(self) -> bool:
        return self._app is not None

    @property
    def ops(self) -> tuple[str, ...]:
        app = self._app
        return tuple(sorted((*SETUP_OPS, *(app.ops if app is not None else ()))))

    def call(self, op: str, args: Mapping[str, object] | None = None) -> dict:
        handler = self._handlers.get(op) if isinstance(op, str) else None
        if handler is not None:
            if args is None:
                args = {}
            if not isinstance(args, Mapping):
                raise ApiRefused("invalid_field", "the arguments must be a JSON object")
            with self._lock:  # one setup at a time: two tabs never make or open two libraries
                self._not_closed()
                return handler(args)
        self._not_closed()
        app = self._app
        if app is None:
            raise ApiRefused("no_library", "no library is open yet: set one up first")
        result = app.call(op, args)
        if op == "library":  # the client learns the setup ops from the same answer as the rest
            result = {**result, "ops": list(self.ops)}
        return result

    def image(self, ref: str) -> tuple[str, bytes]:
        self._not_closed()
        app = self._app
        if app is None:
            raise ApiRefused("no_library", "no library is open yet: set one up first")
        return app.image(ref)

    def close(self) -> None:
        """Stop serving, and close the library the launcher opened itself (one the serve opened, it closes).
        Under the setup lock, so a ``setup.create`` waiting on it finds the launcher closed instead of opening a
        library nothing would close."""
        with self._lock:
            self._closed = True
            self._app = None
            owned, self._owned = self._owned, None
        if owned is not None:
            store, authority = owned
            try:
                authority.close()
            finally:
                store.close()

    # -- ops ---------------------------------------------------------------------------------------------
    def _status(self, args: Mapping[str, object]) -> dict:
        app = self._app
        device = None
        if app is not None:
            try:
                device = device_of(app.api.store)
            except (SyncError, StoreError, OSError, ValueError):
                device = None
        return {"open": app is not None, "library": str(self._path) if self._path is not None else None,
                "suggested": str(self.suggested) if self.suggested is not None else None,
                "owner": self.owner, "device": device,
                "generation": app.api.runtime is not None if app is not None else self.generation}

    def _check(self, args: Mapping[str, object]) -> dict:
        place = _inspect(field(args, "path", str))
        return {"path": str(place.path), "state": place.state}

    def _create(self, args: Mapping[str, object]) -> dict:
        self._not_open()
        place = _inspect(field(args, "path", str))
        device = field(args, "device", str)
        if place.state not in ("new", "empty"):
            raise ApiRefused("occupied" if place.state == "occupied" else "already_a_library",
                             f"{place.path} is not an empty place for a new library ({place.state})")
        try:
            create_library(place.path, device=device, owner=self.owner)
        except SetupError as error:
            raise _refusal(error) from None
        return self._open_at(place.path, None)

    def _open(self, args: Mapping[str, object]) -> dict:
        self._not_open()
        place = _inspect(field(args, "path", str))
        device = field(args, "device", str, required=False, default=None)
        if device is not None:
            try:
                check_device(device)
            except SetupError as error:
                raise _refusal(error) from None
        if place.state != "library":
            raise ApiRefused("not_a_library", f"{place.path} holds no V2 library ({place.state})")
        return self._open_at(place.path, device)

    def _not_closed(self) -> None:
        if self._closed:
            raise ApiRefused("closed", "the app is stopping")

    def _not_open(self) -> None:
        if self._app is not None:
            raise ApiRefused("library_open", f"{self._path} is open: stop the app to set up another library")

    def _open_at(self, path: Path, device: str | None) -> dict:
        try:
            store = open_store(path)
        except SetupError as error:
            raise _refusal(error) from None
        try:
            authority = open_authority(store, self.owner)
            try:
                if device is not None:
                    _name_once(store, device)
            except BaseException:
                authority.close()
                raise
        except SetupError as error:
            store.close()
            raise _refusal(error) from None
        except BaseException:
            store.close()
            raise
        try:
            app = AppApi(self._build(store, authority), self._adapters)
        except BaseException:
            authority.close()
            store.close()
            raise
        self._owned = (store, authority)
        self._path, self._app = path, app
        return self._status({})


def _name_once(store: LibraryStore, device: str) -> None:
    """Name an unnamed library, once, as ``identify`` would; a named one keeps its name, and a different name
    typed for it is refused rather than dropped, so the person never believes the device was renamed."""
    try:
        named = device_of(store)
    except NoDeviceIdentity:
        name_device(store, device)
        return
    if named != device:
        raise ApiRefused("device_differs", f"this library already calls this device {named!r}: leave the name "
                         f"empty, or type {named!r}")


def _inspect(path: str):  # type: ignore[no-untyped-def]
    try:
        return inspect(path)
    except SetupError as error:
        raise _refusal(error) from None


def _refusal(error: SetupError) -> ApiRefused:
    return ApiRefused(error.reason, str(error), 400 if error.reason in _BAD_INPUT else None)


__all__ = ["SETUP_OPS", "BuildApi", "Launcher"]
