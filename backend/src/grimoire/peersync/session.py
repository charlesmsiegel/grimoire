"""The initiating end of a peer transfer (028): sync this library with a paired device over loopback.

Each pass asks the peer for a snapshot, rebuilds it as a checked mirror (``snapshot``), plans 019's
sync between this library and the mirror, and applies it with every mirror step replayed on the
peer before the next step begins (``replay``). Passes repeat until one writes nothing. The
report's status is ``synced`` (the last pass completed and found no conflict), ``conflicts`` (it
completed with conflicts, kept on both devices) or ``incomplete`` (no pass completed quietly; the
reason says why). Completeness is only ever claimed for a pass whose every write, blobs and
placements included, was re-read on both devices and held the planned bytes.
"""

from __future__ import annotations

import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path

from grimoire.store import LibraryStore, StoreError
from grimoire.sync import Conflict, SyncError, SyncInterrupted, SyncReport, apply_sync, device_of, plan_sync

from .errors import MirrorMismatch, NotPaired, PeerRefused, TransportError
from .pairing import peer_principal, read_pairing
from .replay import send_step
from .snapshot import Limits, receive_mirror
from .wire import Channel, client_handshake, no_delay


#: Refusals after which the server hangs up: there is no next pass on this connection.
_SESSION_ENDING = frozenset({"not_paired", "protocol"})


@dataclass(frozen=True)
class TransferReport:
    """What one transfer did. ``passes`` holds the report of every pass that completed, in order."""

    peer: str
    status: str
    reason: str | None = None
    passes: tuple[SyncReport, ...] = ()

    @property
    def conflicts(self) -> tuple[Conflict, ...]:
        """The conflicts of the last completed pass (both devices keep the other's version)."""
        return self.passes[-1].conflicts if self.passes else ()

    @property
    def copied(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted({entry for report in self.passes for entry in report.copied}))


def transfer(store: LibraryStore, authority, peer: str, address: tuple[str, int], *,  # type: ignore[no-untyped-def]
             attempts: int = 4, timeout: float = 600.0, workdir: str | Path | None = None,
             limits: Limits = Limits()) -> TransferReport:
    """Sync ``store`` with the paired device ``peer`` listening at ``address`` (a loopback address).

    ``timeout`` is how long either end may go quiet: the peer scanning its library for a
    snapshot or committing a large step of pictures takes a while (the server allows as long).

    Raises ``NotPaired`` or ``AuthenticationFailed`` before anything is exchanged; every later
    failure is an ``incomplete`` report, never a half-applied step (docs/DEVICE_SYNC.md).
    """
    pairing = read_pairing(store, peer)
    if pairing is None:
        raise NotPaired(f"this library is not paired with {peer}")
    local = device_of(store)
    local_handle = authority.handle_for(peer_principal(peer), trusted=True)
    try:
        connection = socket.create_connection(address, timeout=min(timeout, 10.0))
    except OSError as error:
        return TransferReport(peer, "incomplete", f"unreachable: {error}")
    # Then wait as long as the peer may take to scan its library or commit a large step.
    connection.settimeout(timeout)
    no_delay(connection)
    channel: Channel | None = None
    try:
        channel = client_handshake(connection, local, peer, pairing.key)
        return _passes(store, channel, local, local_handle, attempts, workdir, limits)
    except TransportError as error:
        return TransferReport(peer, "incomplete", f"disconnected: {error}")
    finally:
        if channel is not None:
            try:
                channel.send({"t": "bye"})
            except TransportError:
                pass
        connection.close()


def _passes(store: LibraryStore, channel: Channel, local: str, local_handle: object, attempts: int,
            workdir: str | Path | None, limits: Limits) -> TransferReport:
    peer = channel.peer
    reports: list[SyncReport] = []
    reason = "no pass ran"
    for _ in range(attempts):
        try:
            report = _one_pass(store, channel, local, local_handle, workdir, limits)
        except PeerRefused as error:
            reason = f"{type(error).__name__}: {error}"
            if error.code in _SESSION_ENDING:
                return TransferReport(peer, "incomplete", error.code, tuple(reports))
            continue  # the next pass plans again from both devices as they now are
        except (SyncInterrupted, MirrorMismatch) as error:
            reason = f"{type(error).__name__}: {error}"
            continue
        except (SyncError, StoreError, OSError) as error:  # ArchiveTooLarge, an unreadable library ...
            # The same next time: say why, rather than raise out of a transfer whose earlier
            # steps may already have landed (each one whole, as 019 leaves them after a crash).
            return TransferReport(peer, "incomplete", f"{type(error).__name__}: {error}", tuple(reports))
        reports.append(report)
        if not report.wrote:
            return TransferReport(peer, "conflicts" if report.conflicts else "synced", None, tuple(reports))
        reason = "every pass still wrote something"
    return TransferReport(peer, "incomplete", reason, tuple(reports))


def _one_pass(store: LibraryStore, channel: Channel, local: str, local_handle: object,
              workdir: str | Path | None, limits: Limits) -> SyncReport:
    peer = channel.peer
    channel.send({"t": "snapshot"})
    with tempfile.TemporaryDirectory(prefix="grimoire-peer-", dir=workdir) as scratch:
        mirror = receive_mirror(channel, Path(scratch) / "peer", local, limits)
        try:
            handles = {local: local_handle, peer: mirror.handle}
            plan = plan_sync(store, mirror.store, handles)

            def replay(step) -> None:  # type: ignore[no-untyped-def]
                if step.device == peer:
                    send_step(channel, step, mirror.store.read_blob_exact)
            return apply_sync(plan, handles, observer=replay)
        finally:
            mirror.store.close()
