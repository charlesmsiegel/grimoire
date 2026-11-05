"""Listing a library's ``.sync/peers`` folders (019, 028): names only, never their contents.

Kept beside ``LibraryStore`` rather than in it: 019 reads its pairs through ``read_sync_state``, and
only two callers list a folder: ``identify`` (which pairs to retire) and 028's bookkeeping (whether
shard files stand where a pair's root document is missing). Nothing here reads a file, so a large
or hostile file under ``.sync`` costs a listing entry, never a read.
"""

from __future__ import annotations

import errno
import stat

from .diskops import is_link, lstat_or_none
from .errors import NameCollision
from .layout import SYNC, SYNC_PEERS, sync_state_parts


def sync_peers(store) -> tuple[str, ...]:  # type: ignore[no-untyped-def]
    """The names under ``.sync/peers/`` (sync, 019), sorted; empty when there is none.

    Only names are returned; ``read_sync_state`` decides whether each holds valid state.
    """
    with store.lock:
        store._require_consistent()
        presence = store._access.presence((SYNC, SYNC_PEERS))
        if presence == "mismatch":  # on a case-insensitive filesystem it *is* the folder
            raise NameCollision(f"{SYNC}/{SYNC_PEERS} exists only under another spelling")
        if presence != "present":
            return ()
        return tuple(sorted(store._access.list_dir((SYNC, SYNC_PEERS))))


def sync_state_names(store, peer: str) -> tuple[str, ...]:  # type: ignore[no-untyped-def]
    """The ``.sync`` state names a library holds for one pair: every entry of ``.sync/peers/<peer>/``
    that ``read_sync_state`` accepts (028's archive and marker shards included), sorted; empty when the
    folder does not exist. Other entries are not state and are left out.

    ``ValueError`` if ``peer`` is not a device name. A look-alike spelling of the folder or of one
    above it is ``NameCollision``, and a file where the folder belongs is ``NotADirectoryError``
    (unreadable is never absent), as for ``read_sync_state``.
    """
    folder = sync_state_parts(f"{SYNC_PEERS}/{peer}/archive.json")[:-1]  # validates the device name
    with store.lock:
        store._require_consistent()
        for depth in range(1, len(folder) + 1):
            presence = store._access.presence(folder[:depth])
            if presence == "mismatch":
                raise NameCollision(f"{'/'.join(folder[:depth])} exists only under another spelling")
            if presence == "absent":
                return ()
        info = lstat_or_none(store._access.path(folder))
        if info is not None and not stat.S_ISDIR(info.st_mode) and not is_link(store._access.path(folder)):
            raise NotADirectoryError(errno.ENOTDIR, f"{'/'.join(folder)} is not a folder")
        names = []
        for entry in store._access.list_dir(folder):
            name = f"{SYNC_PEERS}/{peer}/{entry}"
            try:
                sync_state_parts(name)
            except ValueError:
                continue
            names.append(name)
        return tuple(sorted(names))
