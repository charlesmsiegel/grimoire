"""Campaign-owned preferences for optional obstacle offers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from grimoire.chats import scope_from_payload
from grimoire.chats.model import OBSTACLE_PACING_KEY as PACE_KEY
from grimoire.core.identity import ScopeRef
from grimoire.ledgers import DurableAuthority
from grimoire.obstacles import ObstacleState
from grimoire.peersync.pairing import pairing_ended
from grimoire.play.obstacles import check_label_ids
from grimoire.play.scene import Scene, check_ready
from grimoire.scene import SceneRefused, TurnRecord
from grimoire.store import LibraryStore, StaleWrite
from grimoire.store.jsonfmt import is_etag, thaw
from grimoire.store.layout import address_parts
from grimoire.store.loader import StoredScopeRecord
from grimoire.sync.turns import open_conflicts

MANUAL = "manual"
INTERVALS = {"every_action": 1, "every_third_action": 3}


class PacingError(ValueError):
    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class Pacing:
    mode: str | None
    etag: str


def _read(store: LibraryStore, scope: ScopeRef) -> tuple[StoredScopeRecord, Pacing]:
    path = "/".join(address_parts(scope))
    kept, complete = open_conflicts(store, path, (path,), ended=lambda peer: pairing_ended(store, peer))
    if kept or not complete:
        raise PacingError("scope_conflict", "Resolve the campaign scope's sync conflict before editing pacing.")
    stored = store.read(scope)
    record = stored.record
    try:
        scope_from_payload(scope, record.schema, record.payload)
    except ValueError:
        raise PacingError("pacing_unreadable", "The campaign scope cannot be read.") from None
    if PACE_KEY not in record.payload:
        return stored, Pacing(MANUAL, stored.etag)
    value = record.payload[PACE_KEY]
    mode = value if type(value) is str and value in INTERVALS else None
    return stored, Pacing(mode, stored.etag)


def read_pacing(store: LibraryStore, scope: ScopeRef) -> Pacing:
    """Read the current preference without applying any pending play changes."""
    with store.lock:
        return _read(store, scope)[1]


def save_pacing(store: LibraryStore, scope: ScopeRef, handle: object, *, mode: str, expected: str) -> Pacing:
    """Change only the native scope and return its committed mode and etag."""
    if type(mode) is not str or (mode != MANUAL and mode not in INTERVALS):
        raise PacingError("invalid_field", "Choose Manual, Every action turn or Every third action turn.")
    if not is_etag(expected):
        raise PacingError("invalid_field", "Expected must be the reviewed campaign scope revision.")
    with store.lock:
        authority = store.authority
        if (not isinstance(authority, DurableAuthority) or not authority.usable or not authority.issued(handle)
                or not handle.trusted or handle.principal not in authority.owners):
            raise PacingError("not_authorized", "The library owner must save campaign pacing.")
        stored, pacing = _read(store, scope)
        if pacing.etag != expected:
            raise StaleWrite([("/".join(address_parts(scope)), "changed")])
        if pacing.mode == mode:
            return pacing
        if pacing.mode is None and mode != MANUAL:
            raise PacingError("pacing_malformed", "Save Manual to repair the campaign's pacing setting.")
        payload = thaw(stored.record.payload)
        if mode == MANUAL:
            del payload[PACE_KEY]
        else:
            payload[PACE_KEY] = mode
        batch = store.transaction()
        key = batch.put(replace(stored.record, payload=payload), expected)
        committed = batch.commit(handle)
        return Pacing(mode, committed.etags[key])


def _public_action(record: TurnRecord) -> bool:
    return record.post is not None and record.post.kind == "action" and not record.post.private and not record.also


def suggested_obstacle(mode: str | None, scene: Scene, owner: str, states: Iterable[ObstacleState]) -> str | None:
    """Pick a fresh table-visible obstacle on the cadence of public solo actions."""
    interval = INTERVALS.get(mode)
    pending = scene.pending
    if interval is None or pending is None or pending.labels or not _public_action(pending):
        return None
    actor = pending.post.actor
    if actor not in scene.cast or scene.controller(actor) != owner:
        return None
    try:
        check_ready(scene)
    except SceneRefused:
        return None
    if sum(_public_action(record) for record in scene.records) % interval:
        return None
    for state in states:  # 040 returns states in id order
        if state.status != "open" or state.attempts or state.obstacle.opens != 0:
            continue
        try:
            check_label_ids(state)
        except SceneRefused:
            continue
        return state.obstacle.obstacle_id
    return None
