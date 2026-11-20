"""Store-backed recorded rolls (Sprint 075, first slice): dice drawn once, on record before anyone sees them.

``RollLibrary`` records into one authority ledger per campaign, ``rolls/<campaign_id>``, through the
library's ``DurableAuthority``: append-only, hash-chained, written only by the authority's capability, and
replayed and re-checked on load (anything inconsistent is ``LedgerInvalid``, so the library fails closed).
Its handle is the **roll key**, a trusted handle the authority issued; the library never mints one.

Order is the guarantee. ``roll`` draws the dice an action needs, appends them, and only then returns them:
no result exists anywhere until it is on record, so a crash before the append leaves nothing anyone saw,
and one after it leaves the roll a retry reads back. Asking again for the same request returns the recorded
roll and draws nothing, however often narration is retried; a different request under the same id is
refused (``already_rolled``). The ledger stores faces, never a result: replay derives the result from them
with 062's profile again, so no record can say that a failed roll succeeded.

For a player-controlled actor the request is backed by the controller's own 003 action contribution
(``contribution_id == request_id``), given through the controller's own trusted handle, as 029 frames are:
the roll key draws dice, it never acts for a player. A roll no player backs binds narration as
``director:<request_id>``, so it can never bind narration about a player's attempt.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from grimoire.adjudicationstore import AdjudicationLibrary
from grimoire.core.authority import DIRECTOR, AuthorityError, PlayerContribution, Resolution, controller_of
from grimoire.core.identity import InstanceRef, paired_instances, validate_segment
from grimoire.ledgers import DurableAuthority, SovereigntyLedger
from grimoire.ledgers.codec import envelope, exact
from grimoire.rolls import RecordedRoll, attempt_for, RollError, check_backing, dice_for, roll_from_payload, roll_to_payload
from grimoire.rulesets import ActionRequest, RulesetModule
from grimoire.security import is_human, is_valid_principal
from grimoire.store import LibraryStore
from grimoire.store.errors import InvalidEnvelope, LedgerInvalid, TakenIdsUnlisted
from grimoire.store.ledger import Head, entry_cost

#: Draws one die: given its number of sides, a face in ``1..sides``.
Draw = Callable[[int], int]


def system_draw(sides: int) -> int:
    """The default draw: the operating system's randomness, never a seedable generator."""
    return secrets.randbelow(sides) + 1


def ledger_for(campaign_id: str) -> str:
    """The campaign's roll ledger name; an invalid campaign id is ``invalid_field``."""
    if type(campaign_id) is not str:
        raise RollError("campaign_id must be plain text", "invalid_field")
    try:
        validate_segment(campaign_id, "campaign_id")
    except ValueError as error:
        raise RollError(str(error), "invalid_field") from None
    return f"rolls/{campaign_id}"


@dataclass(frozen=True)
class RollState:
    """A campaign's roll ledger, replayed: every recorded roll by the attempt it binds (``attempt_id``).

    A director's roll under request id ``c10`` is ``director:c10``, so it never takes a player's ``c10``."""

    campaign_id: str
    rolls: Mapping[str, RecordedRoll]

    def resolutions(self) -> tuple[Resolution, ...]:
        """003 resolutions for every recorded roll: narration must agree with them."""
        return tuple(self.rolls[attempt_id].resolution() for attempt_id in sorted(self.rolls))


@dataclass
class _Builder:
    campaign_id: str
    rolls: dict[str, RecordedRoll] = field(default_factory=dict)
    head: Head | None = None

    def check(self, roll: RecordedRoll) -> None:
        if roll.campaign_id != self.campaign_id:
            raise RollError("the roll belongs to another campaign", "campaign_mismatch")
        if roll.attempt_id in self.rolls:
            raise RollError(f"attempt {roll.attempt_id!r} is already rolled", "already_rolled")

    def state(self) -> RollState:
        return RollState(self.campaign_id, dict(self.rolls))


def roll_actor_ids(store: LibraryStore, campaign_id: str) -> frozenset[str]:
    """Every actor instance id this campaign's roll ledger names.

    Part of ``grimoire.chatstore.campaign_taken_ids``: a roll names its actor, so a new individual given a
    hand-deleted actor's id would inherit its results. The batches are scanned raw, with no replay or
    chain check, so a broken ledger still protects its ids. A ledger that cannot all be read raises
    ``TakenIdsUnlisted``.
    """
    documents, unread = store.ledger_documents(ledger_for(campaign_id))
    if unread:
        raise TakenIdsUnlisted(f"the roll ledger of {campaign_id} cannot all be read ({', '.join(unread)}), "
                               "so the actor ids it names cannot be reserved", unread)
    taken: set[str] = set()
    stack: list[object] = list(documents)
    while stack:
        value = stack.pop()
        if isinstance(value, Mapping):
            actor = value.get("actor")
            if isinstance(actor, str):
                try:
                    ref = InstanceRef.parse(actor)
                except ValueError:
                    ref = None
                if ref is not None and ref.campaign_id == campaign_id:
                    taken.add(ref.instance_id)
            actor_id = value.get("actor_id")
            if isinstance(actor_id, str) and "contribution_id" in value:
                taken.add(actor_id)
            stack.extend(value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
    return frozenset(taken)


class RollLibrary:
    """Records and reads rolls through the library's durable authority.

    ``handle`` is the roll key: a trusted handle ``authority`` issued. ``draw`` is the source of faces
    (``system_draw`` by default); only whoever constructs the library chooses it, never a caller of
    ``roll``. The replayed state lives on the authority (``views``), so every library over one authority
    shares it; a batch written by anything else poisons the authority until it is reopened and replayed.
    """

    def __init__(self, store: LibraryStore, authority: DurableAuthority, handle: object, *,
                 draw: Draw = system_draw) -> None:
        if type(authority) is not DurableAuthority:
            raise TypeError("expected the library's DurableAuthority")
        if not (authority.issued(handle) and getattr(handle, "trusted", False) is True):
            raise AuthorityError("the roll key must be a trusted handle issued by this library's authority")
        if not callable(draw):
            raise TypeError("draw must be callable")
        self.store = store
        self._authority = authority
        self._handle = handle
        self._principal: str = handle.principal  # type: ignore[attr-defined]
        self._draw = draw

    @property
    def authority(self) -> DurableAuthority:
        return self._authority

    @property
    def principal(self) -> str:
        """Who the roll key records entries as."""
        return self._principal

    # -- reading ------------------------------------------------------------------------------

    def state(self, campaign_id: str) -> RollState:
        ledger_for(campaign_id)
        with self._authority.lock:
            return self._builder(campaign_id).state()

    def rolled(self, campaign_id: str, attempt_id: str) -> RecordedRoll | None:
        """The roll binding ``attempt_id``: a player's contribution id, or ``director:<request id>``."""
        return self.state(campaign_id).rolls.get(attempt_id)

    def resolutions(self, campaign_id: str) -> tuple[Resolution, ...]:
        return self.state(campaign_id).resolutions()

    # -- recording ----------------------------------------------------------------------------

    def roll(self, selected: RulesetModule | None, request: ActionRequest, *,
             contribution: PlayerContribution | None = None, author: object = None) -> RecordedRoll:
        """Draw and record the dice ``request`` needs, or return the roll already recorded for it.

        ``selected`` is the campaign's one selected rules module; a request for any other is refused
        (``mechanics_disabled`` with none, ``module_mismatch`` otherwise), as 062's ``propose`` refuses it.
        Asking again with the same request and backing returns the recorded roll and draws nothing; any
        other request under the same id is ``already_rolled``. A player-controlled actor's request needs
        the controller's own action ``contribution`` with the request's id (``pc_authorship`` without one),
        given through ``author``, the controller's own trusted handle (``not_controller`` otherwise).
        """
        if type(request) is not ActionRequest:
            raise TypeError("expected an ActionRequest")
        if selected is None:
            raise RollError("mechanics are disabled for this campaign", "mechanics_disabled")
        if type(selected) is not RulesetModule or selected != request.module:
            raise RollError("the request is for another rules module than the one selected", "module_mismatch")
        if contribution is not None:
            check_backing(request, contribution)
        sides = dice_for(request.action)
        campaign_id = request.actor.campaign_id
        attempt = attempt_for(request, contribution)
        with self._authority.lock:  # control is read, the dice drawn and the entry appended as one step
            builder = self._builder(campaign_id)
            done = builder.rolls.get(attempt)
            if done is not None:
                if (done.request, done.contribution) == (request, contribution):
                    return done  # a retry reads the roll back; it never draws again
                raise RollError(f"attempt {attempt!r} is already rolled", "already_rolled")
            self._check_actor(request.actor)
            self._check_authority(request.actor, contribution, author)
            self._check_unadjudicated(attempt, request.actor.campaign_id)
            # Everything the append could refuse is refused before a die is drawn, so no refusal ever follows a
            # result someone could have seen: an unusable authority, text the store cannot hold, an entry no
            # batch can hold (the highest faces bound its size).
            self._authority._require_usable()
            self._cost(roll_to_payload(request, contribution, sides))
            faces = tuple(self._draw(side) for side in sides)
            try:
                roll = RecordedRoll(request, contribution, faces, self._principal)
            except RollError as error:  # a draw that is no face of its die: nothing is written
                raise RollError(f"the draw gave impossible faces: {error}", "invalid_draw") from None
            builder.check(roll)
            self._append(builder, roll_to_payload(request, contribution, faces))
            builder.rolls[roll.attempt_id] = roll
            return roll

    # -- internals ----------------------------------------------------------------------------

    def _check_actor(self, actor: InstanceRef) -> None:
        if self.store.campaign_presence(actor.campaign_id) != "present":
            raise RollError(f"no campaign {actor.campaign_id!r}", "missing_campaign")
        instances = tuple(self.store.campaign_instances(actor.campaign_id))
        if actor not in instances:
            raise RollError(f"{actor} is not an instance of its campaign", "unknown_actor")
        if actor in paired_instances(instances):  # another kind uses its id: no one individual (IDENTITY.md)
            raise RollError(f"another kind also uses {actor.instance_id!r}", "instance_id_conflict")

    def _check_authority(self, actor: InstanceRef, contribution: PlayerContribution | None,
                         author: object) -> None:
        controls = SovereigntyLedger(self._authority).load(actor.campaign_id).controls
        controller = controller_of(actor.campaign_id, actor.instance_id, controls)
        if controller is None or controller == DIRECTOR:
            if contribution is not None or author is not None:
                raise RollError("an actor no player controls has no contribution to back it", "unexpected_backing")
        elif not is_human(controller):
            raise RollError(f"{actor} is controlled by {controller!r}, not a human", "non_human_controller")
        elif contribution is None:
            raise RollError("a player character's roll is backed by the player's own action", "pc_authorship")
        elif contribution.submitted_by != controller:
            raise RollError("the contribution is not the current controller's", "invalid_expression")
        elif not (self._authority.issued(author) and getattr(author, "trusted", False) is True
                  and getattr(author, "principal", None) == controller):
            raise RollError("only the controller's own trusted handle gives their contribution", "not_controller")

    def _check_unadjudicated(self, attempt: str, campaign_id: str) -> None:
        """An attempt 029 already frames is adjudicated there, not rolled: two records would bind one attempt."""
        frames = AdjudicationLibrary(self.store, self._authority, self._handle).state(campaign_id)
        if frames.frame(attempt) is not None:
            raise RollError(f"attempt {attempt!r} is adjudicated by 029, not rolled", "attempt_adjudicated")

    def _cost(self, payload: dict[str, object]) -> None:
        """What ``_append`` would refuse for this payload, refused now."""
        try:
            entry_cost(envelope("roll", self._authority.now(), recorded_by=self._principal, value=payload))
        except InvalidEnvelope as error:
            raise RollError(str(error), "invalid_field") from None
        except ValueError as error:
            raise RollError(str(error), "too_large") from None

    def _append(self, builder: _Builder, payload: dict[str, object]) -> None:
        entry = envelope("roll", self._authority.now(), recorded_by=self._principal, value=payload)
        try:
            builder.head = self._authority._append(ledger_for(builder.campaign_id), [entry], builder.head)
        except InvalidEnvelope as error:  # text the store cannot hold; nothing was written
            raise RollError(str(error), "invalid_field") from None
        except ValueError as error:  # no batch could hold it; nothing was written
            raise RollError(str(error), "too_large") from None

    def _builder(self, campaign_id: str) -> _Builder:
        name = ledger_for(campaign_id)
        views = self._authority.views
        if name not in views:
            ledger = self._authority.read_ledger(name)
            views[name] = _replay(campaign_id, name, ledger.entries, ledger.head)
        return views[name]  # type: ignore[return-value]


def _replay(campaign_id: str, name: str, entries: Iterable[object], head: Head | None) -> _Builder:
    """Re-check every entry with the record-time rules; anything else is ``LedgerInvalid``."""
    builder = _Builder(campaign_id)
    for index, entry in enumerate(entries, 1):
        kind = entry.get("kind") if isinstance(entry, Mapping) else None
        if kind != "roll":
            raise LedgerInvalid(f"{name} entry {index}: unknown kind {kind!r}")
        data = exact(entry, kind, {"recorded_by", "value"})  # type: ignore[arg-type]
        recorded_by = data["recorded_by"]
        if not is_valid_principal(recorded_by):
            raise LedgerInvalid(f"{name} entry {index}: recorded_by is not a principal")
        try:
            roll = roll_from_payload(campaign_id, data["value"], recorded_by)  # type: ignore[arg-type]
            builder.check(roll)
        except RollError as error:
            raise LedgerInvalid(f"{name} entry {index}: {error} ({error.reason})") from None
        builder.rolls[roll.attempt_id] = roll
    builder.head = head
    return builder
