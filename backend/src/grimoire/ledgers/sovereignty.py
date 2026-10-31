"""Durable player sovereignty: control history, delegations, revocations, corrections.

Each campaign has one ledger, ``campaigns/<campaign_id>``. Only the player-input
channel records into it, through a trusted human handle issued by the library's
durable authority, and only for the principal the value names: a delegation by
its grantor, a revocation by its revoker, a correction by its corrector, and a
control change by a library owner. Loading re-checks every entry the same way
and fails closed (``LedgerInvalid``) on anything else. Authority *meaning* is
still re-checked by ``grimoire.core.authority`` at review time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from grimoire.core.authority import AuthorityError, Correction, Delegation, Revocation
from grimoire.core.identity import validate_segment
from grimoire.core.model import ControlAssignment
from grimoire.security import is_human
from grimoire.store.errors import LedgerInvalid
from grimoire.store.ledger import Head

from .codec import SOVEREIGNTY_KEYS, ControlChange, decode_sovereignty, encode_sovereignty, envelope, exact
from .security import DurableAuthority


@dataclass(frozen=True)
class ControlEvent:
    seq: int  # position in the campaign ledger, 1-based
    actor_id: str
    controller_id: str | None
    recorded_by: str


@dataclass(frozen=True)
class SovereigntyState:
    campaign_id: str
    controls: tuple[ControlAssignment, ...]
    control_history: tuple[ControlEvent, ...]
    delegations: tuple[Delegation, ...]
    revocations: tuple[Revocation, ...]
    corrections: tuple[Correction, ...]


@dataclass
class _Builder:
    campaign_id: str
    owners: frozenset[str]
    controllers: dict[str, str] = field(default_factory=dict)
    history: list[ControlEvent] = field(default_factory=list)
    delegations: list[Delegation] = field(default_factory=list)
    revocations: list[Revocation] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)
    count: int = 0
    head: Head | None = None  # the newest batch replayed or written

    def check(self, value: object, recorded_by: str) -> None:
        """The provenance and consistency rules; ``AuthorityError`` names what is wrong."""
        if getattr(value, "campaign_id", None) != self.campaign_id:
            raise AuthorityError("the value belongs to another campaign")
        if type(value) is ControlChange:
            if recorded_by not in self.owners:
                raise AuthorityError("only a library owner may change control")
        elif type(value) is Delegation:
            if recorded_by != value.granted_by:
                raise AuthorityError("a delegation can only be recorded by its grantor")
            if any(d.delegation_id == value.delegation_id for d in self.delegations):
                raise AuthorityError(f"delegation {value.delegation_id} already exists")
        elif type(value) is Revocation:
            if recorded_by != value.revoked_by:
                raise AuthorityError("a revocation can only be recorded by its revoker")
            if not any(d.delegation_id == value.delegation_id for d in self.delegations):
                raise AuthorityError(f"no delegation {value.delegation_id} to revoke")
        elif type(value) is Correction:
            if recorded_by != value.corrected_by:
                raise AuthorityError("a correction can only be recorded by its corrector")
        else:
            raise AuthorityError(f"not a sovereignty value: {type(value).__name__}")

    def apply(self, value: object, recorded_by: str) -> None:
        self.count += 1
        if type(value) is ControlChange:
            if value.controller_id is None:
                self.controllers.pop(value.actor_id, None)
            else:
                self.controllers[value.actor_id] = value.controller_id
            self.history.append(ControlEvent(self.count, value.actor_id, value.controller_id, recorded_by))
        elif type(value) is Delegation:
            self.delegations.append(value)
        elif type(value) is Revocation:
            self.revocations.append(value)
        else:
            self.corrections.append(value)  # type: ignore[arg-type]

    def state(self) -> SovereigntyState:
        return SovereigntyState(
            self.campaign_id,
            tuple(ControlAssignment(self.campaign_id, actor, controller)
                  for actor, controller in sorted(self.controllers.items())),
            tuple(self.history), tuple(self.delegations), tuple(self.revocations), tuple(self.corrections))


def _ledger(campaign_id: str) -> str:
    try:
        validate_segment(campaign_id, "campaign_id")
    except ValueError as error:
        raise AuthorityError(str(error)) from None
    return f"campaigns/{campaign_id}"


class SovereigntyLedger:
    """Records and reloads player sovereignty through the library's durable authority.

    The replayed state lives on the authority, so every ``SovereigntyLedger`` over
    one authority shares it, and each append continues from the head it replayed:
    a batch written by anything else makes the append ``StaleWrite``, which poisons
    the authority until it is reopened and the ledger replayed again.
    """

    def __init__(self, authority: DurableAuthority) -> None:
        self._authority = authority

    def record(self, campaign_id: str, value: object, handle: object) -> None:
        """Durably record one value from the player-input channel; refusals are ``AuthorityError``."""
        authority = self._authority
        if not (authority.issued(handle) and getattr(handle, "trusted", False) is True
                and is_human(getattr(handle, "principal", None))):
            raise AuthorityError("only a trusted human handle from this library's authority may record sovereignty")
        principal = handle.principal  # type: ignore[attr-defined]
        name = _ledger(campaign_id)
        try:
            kind, fields = encode_sovereignty(value)
        except ValueError as error:
            raise AuthorityError(str(error)) from None
        with authority.lock:
            builder = self._builder(campaign_id)
            builder.check(value, principal)
            entry = envelope(kind, authority.now(), recorded_by=principal, value=fields)
            try:
                head = authority._append(name, [entry], builder.head)
            except ValueError as error:  # no batch could hold it; nothing was written
                raise AuthorityError(str(error)) from None
            builder.apply(value, principal)
            builder.head = head

    def load(self, campaign_id: str) -> SovereigntyState:
        _ledger(campaign_id)
        with self._authority.lock:
            return self._builder(campaign_id).state()

    def _builder(self, campaign_id: str) -> _Builder:
        name = _ledger(campaign_id)
        views = self._authority.views
        if name not in views:
            ledger = self._authority.read_ledger(name)
            builder = _Builder(campaign_id, self._authority.owners)
            for index, entry in enumerate(ledger.entries, 1):
                kind = entry.get("kind") if isinstance(entry, Mapping) else None
                if kind not in SOVEREIGNTY_KEYS:
                    raise LedgerInvalid(f"{name} entry {index}: unknown kind {kind!r}")
                data = exact(entry, kind, {"recorded_by", "value"})
                fields = data["value"]
                if not isinstance(fields, Mapping) or set(fields) != SOVEREIGNTY_KEYS[kind]:
                    raise LedgerInvalid(f"{name} entry {index}: unexpected fields")
                recorded_by = data["recorded_by"]
                if not is_human(recorded_by):
                    raise LedgerInvalid(f"{name} entry {index}: recorded_by is not a human principal")
                value = decode_sovereignty(kind, fields)
                try:
                    builder.check(value, recorded_by)
                except AuthorityError as error:
                    raise LedgerInvalid(f"{name} entry {index}: {error}") from None
                builder.apply(value, recorded_by)
            builder.head = ledger.head
            views[name] = builder
        return views[name]  # type: ignore[return-value]
