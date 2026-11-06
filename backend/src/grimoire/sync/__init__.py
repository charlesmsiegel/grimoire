"""V2 device synchronization core (019): transport-free, file-granular, keep-both on conflict.

An adapter over ``grimoire.store``, not part of the pure core. Two open libraries are
synchronized by bytes: non-overlapping edits merge deterministically, anything else becomes a
named conflict that keeps both versions on both devices. Timestamps never decide. See
docs/SYNC.md for the contract.
"""

from .decide import decide, derive_base
from .dismiss import Dismissal, dismiss
from .errors import (
    ArchiveTooLarge, DismissRefused, IdentityExists, NoDeviceIdentity, ResolveRefused, SameDevice, SyncError,
    SyncInterrupted, SyncNotQuiet,
)
from .model import CONFLICT_CODES, Base, Decision, Side
from .resolution import resolve
from .session import (
    Conflict, Copy, Keep, Step, SyncPlan, SyncReport, apply_sync, device_of, identify, plan_sync, sync,
    sync_until_quiet, syncable,
)

__all__ = [
    "ArchiveTooLarge", "Base", "CONFLICT_CODES", "Conflict", "Copy", "Decision", "DismissRefused", "Dismissal",
    "IdentityExists", "Keep", "NoDeviceIdentity", "ResolveRefused", "SameDevice", "Side", "Step", "SyncError",
    "SyncInterrupted", "SyncNotQuiet", "SyncPlan", "SyncReport", "apply_sync", "decide", "derive_base", "device_of",
    "dismiss", "identify", "plan_sync", "resolve", "sync", "sync_until_quiet", "syncable",
]
