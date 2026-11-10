"""Making and opening a library for the app (044, 108): what ``init`` does at the console and the first-run page
does in the browser, so the two can never set a library up differently.

A library is made only at a place V2 may write (``check_write_destination``): never inside, around or beside a V1
library, and refused before anything is written. It gets the owner's grants on the synchronized roots (028)
and its device name (019).
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from grimoire.ledgers import DurableAuthority
from grimoire.library import LibraryPathError, V1IsolationError, check_write_destination
from grimoire.security import Grant, Permission, ResourcePath, is_human
from grimoire.store import LibraryLocked, LibraryStore, NotALibrary, StoreError
from grimoire.store.layout import MARKER
from grimoire.sync import SyncError, identify
from grimoire.sync.documents import check_device_id

#: The one owner principal the app uses unless told otherwise. Both paired devices must use the same one:
#: who owns the table, and which player sits at it, is per library.
DEFAULT_OWNER = "human:owner"
#: The synchronized roots the owner writes (028).
SCOPES = ("sources", "campaigns", "assets", "kept")


class SetupError(Exception):
    """A library could not be made or opened; ``reason`` is machine-readable. Every refusal of the place, the
    owner or the device comes before anything is written. Two reasons say something was: ``create_incomplete``
    (the library was made but its owner's grants or device name were not, so it must be moved aside before
    trying again) and ``identify_failed`` (the library opened but could not be named)."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Place:
    """What is at a path the person typed: ``new`` (nothing yet: a library can be made there), ``empty`` (an
    empty folder: likewise), ``library`` (a V2 library: it can be opened), ``invalid`` (a library marker this
    version cannot open: neither) or ``occupied`` (a folder with other things in it, or a file: neither)."""

    path: Path
    state: str


def check_owner(owner: str) -> None:
    if not is_human(owner):
        raise SetupError("invalid_owner", f"the owner must be a human principal such as {DEFAULT_OWNER}, not {owner!r}")


def check_device(device: str) -> None:
    try:
        check_device_id(device)
    except ValueError as error:
        raise SetupError("invalid_device", f"device {device!r}: {error}") from None


def inspect(path: str | os.PathLike[str]) -> Place:
    """What is at ``path``, read-only. A place overlapping a V1 library is refused (``v1_library``), as is one V2
    may not write at all (``bad_path``): neither is ever offered."""
    try:
        resolved = check_write_destination(path)
    except V1IsolationError as error:
        raise SetupError("v1_library", str(error)) from None
    except (LibraryPathError, OSError, ValueError) as error:
        raise SetupError("bad_path", str(error)) from None
    try:
        info = os.lstat(resolved)
    except FileNotFoundError:
        if not resolved.parent.is_dir():
            raise SetupError("bad_path", f"{resolved.parent} must be an existing folder") from None
        return Place(resolved, "new")
    except OSError as error:
        raise SetupError("bad_path", f"{resolved}: {error.strerror or error}") from None
    if not stat.S_ISDIR(info.st_mode):
        return Place(resolved, "occupied")
    if (resolved / MARKER).is_file():
        # Read the marker as the store would (read-only: no lock, no recovery), so a folder is offered as a
        # library only when opening it can succeed.
        try:
            LibraryStore.open(resolved, writable=False).close()
        except NotALibrary:
            return Place(resolved, "invalid")
        except (StoreError, OSError, ValueError) as error:
            raise SetupError("bad_path", f"{resolved}: cannot read it ({error})") from None
        return Place(resolved, "library")
    try:
        if not any(resolved.iterdir()) or LibraryStore.interrupted_create(resolved):
            return Place(resolved, "empty")  # a create stopped before its marker is finished by the next one
        return Place(resolved, "occupied")
    except OSError as error:
        raise SetupError("bad_path", f"{resolved}: {error.strerror or error}") from None


def create_library(path: str | os.PathLike[str], *, device: str, owner: str = DEFAULT_OWNER) -> Path:
    """Make a library at ``path`` for ``owner`` on ``device`` and close it again. Owner and device are checked
    before anything is written, so a refused setup leaves nothing behind. The store publishes its marker before
    the grants and the device name can be written, so a failure after that is ``create_incomplete``: a library
    is there, but not one this owner can write, and the person is told so rather than that nothing happened."""
    check_owner(owner)
    check_device(device)
    try:
        store = LibraryStore.create(path)
    except V1IsolationError as error:
        raise SetupError("v1_library", str(error)) from None
    except (StoreError, OSError, ValueError) as error:
        raise SetupError("create_failed", f"{path}: cannot create a library ({error})") from None
    try:
        authority = open_authority(store, owner)
        try:
            for scope in SCOPES:
                authority.add_grant(Grant(f"owner-{scope}", owner, frozenset({Permission.APPLY}),
                                          ResourcePath.parse(scope), owner))
        finally:
            authority.close()
        name_device(store, device)
        return store.root
    except Exception as error:  # noqa: BLE001 -- told to the person, with what is now on disk
        raise SetupError("create_incomplete", f"{store.root}: a library was made there but not finished ({error}): "
                         "move that folder aside and make the library again") from None
    finally:
        store.close()


def name_device(store: LibraryStore, device: str) -> None:
    try:
        identify(store, device)
    except (SyncError, StoreError, ValueError) as error:
        raise SetupError("identify_failed", f"cannot name this device {device!r} ({error})") from None


def open_store(path: str | os.PathLike[str]) -> LibraryStore:
    try:
        return LibraryStore.open(path)
    except LibraryLocked:
        raise SetupError("library_locked", f"{path} is open in another process: stop `serve` first") from None
    except V1IsolationError as error:
        raise SetupError("v1_library", str(error)) from None
    except (StoreError, OSError, ValueError) as error:
        raise SetupError("open_failed", f"{path}: cannot open the library ({error})") from None


def open_authority(store: LibraryStore, owner: str) -> DurableAuthority:
    try:
        return DurableAuthority.open(store, [owner])
    except Exception as error:  # noqa: BLE001 -- shown to the person, who chose the owner
        raise SetupError("authority_failed", f"cannot open the library's authority as {owner} ({error})") from None


__all__ = ["DEFAULT_OWNER", "SCOPES", "Place", "SetupError", "check_device", "check_owner", "create_library",
           "inspect", "name_device", "open_authority", "open_store"]
