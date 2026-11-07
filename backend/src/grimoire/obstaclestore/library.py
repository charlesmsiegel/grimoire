"""Store-backed open obstacles (Sprint 040): obstacles and the attempts against them, on record.

``ObstacleLibrary`` records into one authority ledger per campaign, ``obstacles/<campaign_id>``, through
the library's ``DurableAuthority``: append-only, hash-chained, written only by the authority's
capability, and replayed and re-checked on load (anything inconsistent is ``LedgerInvalid``). Its handle
is the same **adjudication key** as 029's: the director's labelling channel. It holds three kinds of entry:
``obstacle`` (an authored obstacle, or a revision of one no attempt has engaged yet), ``attempt``
(an attempt's link to an obstacle and the approach it takes) and ``withdrawal`` (a pending attempt that
029 cannot decide now, withdrawn for good: ``withdraw``).

Attempts are 029 action frames. ``frame`` checks a frame against the obstacle as it stands (its facts
and the escalations of earlier failures) and against 029's own rules (who may act and speak) without
writing, links it, and then records it through 029's ``AdjudicationLibrary.frame``: a 029 frame never
exists without its link, so a crash cannot leave a decided attempt that escapes its obstacle.
Outcomes are decided by 029 alone, and the obstacle's state is derived on read from its attempts and
their 029 outcomes: nothing here decides anything, and no outcome needs a second record.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from grimoire.adjudication import ActionFrame, AdjudicationError, Factor
from grimoire.adjudicationstore import AdjudicationLibrary, FrameEntry
from grimoire.core.authority import PlayerContribution
from grimoire.core.identity import InstanceRef, validate_segment
from grimoire.ledgers import DurableAuthority
from grimoire.ledgers.codec import envelope, exact
from grimoire.obstacles import (
    Approach, Attempt, Obstacle, ObstacleError, ObstacleState, attempt_from_payload, attempt_to_payload,
    check_attempt, derive, obstacle_from_payload, obstacle_to_payload, required_factors, resolve_approach,
    withdrawal_from_payload, withdrawal_to_payload,
)
from grimoire.security import is_valid_principal
from grimoire.store import LibraryStore
from grimoire.store.errors import InvalidEnvelope, LedgerInvalid, TakenIdsUnlisted
from grimoire.store.ledger import Head, entry_cost

_KINDS = frozenset({"obstacle", "attempt", "withdrawal"})


def ledger_for(campaign_id: str) -> str:
    """The campaign's obstacle ledger name; an invalid campaign id is ``invalid_field``."""
    if type(campaign_id) is not str:
        raise ObstacleError("campaign_id must be plain text", "invalid_field")
    try:
        validate_segment(campaign_id, "campaign_id")
    except ValueError as error:
        raise ObstacleError(str(error), "invalid_field") from None
    return f"obstacles/{campaign_id}"


def _refused(message: str, reason: str) -> ObstacleError:
    return ObstacleError(message, reason)


@dataclass(frozen=True)
class ObstacleEntry:
    """One recorded revision of an obstacle (revisions count from 1)."""

    obstacle: Obstacle
    revision: int
    recorded_by: str


@dataclass
class _Builder:
    """The replayed ledger and the rules every entry obeys, at record time and again on replay."""

    campaign_id: str
    obstacles: dict[str, list[ObstacleEntry]] = field(default_factory=dict)
    attempts: dict[str, list[Attempt]] = field(default_factory=dict)
    linked: dict[str, str] = field(default_factory=dict)  # attempt id -> obstacle id
    withdrawn: set[str] = field(default_factory=set)  # attempt ids
    head: Head | None = None

    def check_obstacle(self, obstacle: Obstacle) -> int:
        """The obstacle's revision number, or a refusal."""
        if obstacle.campaign_id != self.campaign_id:
            raise _refused("the obstacle belongs to another campaign", "campaign_mismatch")
        if self.attempts.get(obstacle.obstacle_id):
            # Once a player has chosen an approach, the problem they chose it against is fixed: a fact
            # added afterwards would be a difficulty invented to answer their choice.
            raise _refused(f"obstacle {obstacle.obstacle_id!r} has been attempted and can no longer be revised",
                           "obstacle_engaged")
        return len(self.obstacles.get(obstacle.obstacle_id, ())) + 1

    def check_attempt(self, obstacle_id: str, attempt_id: str, approach_id: str, novel: Approach | None,
                      supersedes: str | None) -> Attempt:
        """The attempt a link records, or a refusal (everything that one ledger alone can check).

        A link supersedes only the attempt linked just before it to the same obstacle (``supersedes_mismatch``):
        one attempt is undecided at a time, so that is the only one that can have been pending.
        """
        entries = self.obstacles.get(obstacle_id)
        if not entries:
            raise _refused(f"no obstacle {obstacle_id!r}", "unknown_obstacle")
        if attempt_id in self.linked:
            raise _refused(f"attempt {attempt_id!r} is already linked to an obstacle", "attempt_linked")
        earlier = self.attempts.get(obstacle_id)
        if supersedes is not None and (not earlier or earlier[-1].attempt_id != supersedes):
            raise _refused(f"attempt {attempt_id!r} supersedes {supersedes!r}, not the attempt before it",
                           "supersedes_mismatch")
        obstacle = entries[-1].obstacle
        approach, listed = resolve_approach(obstacle, approach_id if novel is None else novel)
        return Attempt(attempt_id, approach, listed, supersedes)

    def check_withdrawal(self, obstacle_id: str, attempt_id: str) -> None:
        """Refuse a withdrawal this ledger alone shows is wrong: only the obstacle's latest attempt, once."""
        if not self.obstacles.get(obstacle_id):
            raise _refused(f"no obstacle {obstacle_id!r}", "unknown_obstacle")
        if self.linked.get(attempt_id) != obstacle_id:
            raise _refused(f"attempt {attempt_id!r} is not linked to {obstacle_id!r}", "not_linked")
        if self.attempts[obstacle_id][-1].attempt_id != attempt_id:
            raise _refused(f"attempt {attempt_id!r} is not the latest against {obstacle_id!r}", "withdrawal_mismatch")
        if attempt_id in self.withdrawn:
            raise _refused(f"attempt {attempt_id!r} is already withdrawn", "already_withdrawn")

    def add_obstacle(self, entry: ObstacleEntry) -> None:
        self.obstacles.setdefault(entry.obstacle.obstacle_id, []).append(entry)

    def add_attempt(self, obstacle_id: str, attempt: Attempt) -> None:
        self.attempts.setdefault(obstacle_id, []).append(attempt)
        self.linked[attempt.attempt_id] = obstacle_id

    def add_withdrawal(self, attempt_id: str) -> None:
        self.withdrawn.add(attempt_id)


def obstacle_actor_ids(store: LibraryStore, campaign_id: str) -> frozenset[str]:
    """Every actor instance id this campaign's obstacle ledger names.

    Part of ``grimoire.chatstore.campaign_taken_ids``: an obstacle names the actors in the way by
    reference, so a new individual given a hand-deleted guard's id would inherit its post. The batches
    are scanned raw, with no replay or chain check, so a broken ledger still protects its ids: every
    reference text in an ``actors`` list that parses as this campaign's instance. (Attempt actors are in
    029's ledger, reserved there.) A ledger that cannot all be read raises ``TakenIdsUnlisted``.
    """
    documents, unread = store.ledger_documents(ledger_for(campaign_id))
    if unread:
        raise TakenIdsUnlisted(f"the obstacle ledger of {campaign_id} cannot all be read ({', '.join(unread)}), "
                               "so the actor ids it names cannot be reserved", unread)
    taken: set[str] = set()
    stack: list[object] = list(documents)
    while stack:
        value = stack.pop()
        if isinstance(value, Mapping):
            actors = value.get("actors")
            if isinstance(actors, (list, tuple)):
                for text in actors:
                    if not isinstance(text, str):
                        continue
                    try:
                        ref = InstanceRef.parse(text)
                    except ValueError:
                        continue
                    if ref.campaign_id == campaign_id:
                        taken.add(ref.instance_id)
            stack.extend(value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
    return frozenset(taken)


class ObstacleLibrary:
    """Records obstacles and links attempts to them through the library's durable authority.

    ``handle`` is the adjudication key (a trusted handle ``authority`` issued), shared with the 029
    ``AdjudicationLibrary`` this library frames attempts through and reads outcomes from. The replayed
    state lives on the authority (``views``), so every library over one authority shares it.
    """

    def __init__(self, store: LibraryStore, authority: DurableAuthority, handle: object) -> None:
        self._adjudication = AdjudicationLibrary(store, authority, handle)  # checks the handle
        self.store = store
        self._authority = authority
        self._principal: str = handle.principal  # type: ignore[attr-defined]

    @property
    def adjudication(self) -> AdjudicationLibrary:
        """The 029 library over the same authority and key: attempts are decided there."""
        return self._adjudication

    # -- reading ------------------------------------------------------------------------------

    def state(self, campaign_id: str, obstacle_id: str) -> ObstacleState:
        """The obstacle replayed against the 029 outcomes of its attempts, now."""
        with self._authority.lock:
            return self._derive(self._builder(campaign_id), obstacle_id)

    def states(self, campaign_id: str) -> tuple[ObstacleState, ...]:
        """Every obstacle of the campaign, by id."""
        with self._authority.lock:
            builder = self._builder(campaign_id)
            return tuple(self._derive(builder, obstacle_id) for obstacle_id in sorted(builder.obstacles))

    def linked(self, campaign_id: str) -> dict[str, str]:
        """Every attempt this ledger links, and the obstacle it is against: ``{attempt id: obstacle id}`` (a copy).
        A caller that holds its own record of an attempt's obstacle checks it against this, the ledger's own."""
        with self._authority.lock:
            return dict(self._builder(campaign_id).linked)

    def entries(self, campaign_id: str, obstacle_id: str) -> tuple[ObstacleEntry, ...]:
        with self._authority.lock:
            return tuple(self._builder(campaign_id).obstacles.get(obstacle_id, ()))

    def required(self, campaign_id: str, obstacle_id: str, approach: str | Approach, sequence: int
                 ) -> tuple[Factor, ...]:
        """The obstacle factors a frame for ``approach`` at ``sequence`` must carry, for the labeller.

        For a suggestion, its risks are its stakes (``Obstacle.stake(id).stake``); a novel approach brings
        the ``"*"`` facts and may add any other obstacle fact as favouring or hindering.
        """
        state = self.state(campaign_id, obstacle_id)
        given, _ = resolve_approach(state.obstacle, approach)
        return required_factors(state.facts, given.approach_id, sequence)

    # -- recording ----------------------------------------------------------------------------

    def record(self, obstacle: Obstacle) -> ObstacleEntry:
        """Record a new obstacle, or a revision of one no attempt has engaged (``obstacle_engaged``).

        The campaign must exist (``missing_campaign``) and every actor must be one of its instances
        (``unknown_actor``). Recording the latest revision again returns it and writes nothing.
        """
        if type(obstacle) is not Obstacle:
            raise TypeError("expected an Obstacle")
        campaign_id = obstacle.campaign_id
        payload = obstacle_to_payload(obstacle)
        with self._authority.lock:
            builder = self._builder(campaign_id)
            existing = builder.obstacles.get(obstacle.obstacle_id)
            if existing and existing[-1].obstacle == obstacle:
                return existing[-1]
            if self.store.campaign_presence(campaign_id) != "present":
                raise _refused(f"no campaign {campaign_id!r}", "missing_campaign")
            instances = set(self.store.campaign_instances(campaign_id))
            unknown = sorted(str(actor) for actor in obstacle.actors if actor not in instances)
            if unknown:
                raise _refused(f"actors that are not instances of the campaign: {', '.join(unknown)}",
                               "unknown_actor")
            revision = builder.check_obstacle(obstacle)
            entry = ObstacleEntry(obstacle, revision, self._principal)
            self._append(builder, "obstacle", payload)
            builder.add_obstacle(entry)
            return entry

    def frame(self, obstacle_id: str, frame: ActionFrame, *, approach: str | Approach,
              contribution: PlayerContribution | None = None, author: object = None) -> FrameEntry:
        """Frame an attempt against ``obstacle_id`` by ``approach``: a suggestion's id, or a novel ``Approach``.

        The frame must agree with the obstacle as it stands (``check_attempt``) and pass 029's own checks
        (a player character's action is backed by the player's own contribution, given through
        ``author``). The attempt is then linked, and only then framed through 029: a link always comes
        before its frame, so no frame decided in 029 can lose the obstacle it was made against. One
        attempt is undecided at a time (``attempt_pending``): the next one meets what this one changes,
        unless the pending one cannot be decided now (its actor changed hands, or its frame was never
        written), when the next one supersedes it: the link names it, and it stays void for good, even
        if control comes back and 029 decides it.
        A frame for the pending attempt is a 029 revision and keeps its approach (``approach_changed``);
        it may also bring a diverged pending attempt back into agreement. Refused too:
        ``unknown_obstacle``, ``obstacle_diverged``, ``already_overcome``, ``attempt_linked`` (another
        obstacle's attempt) and ``already_decided`` (an attempt decided, or decided before it was linked).
        """
        with self._authority.lock:  # the obstacle is read, the link appended and the frame recorded as one step
            builder, link = self._admit(obstacle_id, frame, approach, contribution, author)
            if link is not None:
                obstacle, attempt, payload = link
                self._append(builder, "attempt", payload)
                builder.add_attempt(obstacle, attempt)
            return self._adjudication.frame(frame, contribution=contribution, author=author)

    def check(self, obstacle_id: str, frame: ActionFrame, *, approach: str | Approach,
              contribution: PlayerContribution | None = None, author: object = None) -> None:
        """Raise the refusal ``frame`` would raise now; return ``None`` when it would be recorded. Writes nothing.

        Every check ``frame`` makes, the obstacle's and 029's (``AdjudicationLibrary.check``), for a caller that
        must write its own record first and then the frame (035 marks a turn framed before it frames)."""
        with self._authority.lock:
            self._admit(obstacle_id, frame, approach, contribution, author)

    def withdraw(self, campaign_id: str, obstacle_id: str, attempt_id: str, *, set_aside: bool = False) -> None:
        """Withdraw the obstacle's undecided attempt ``attempt_id`` for good: it is ``void`` from now on.

        For an attempt whose turn passes while 029 cannot decide it (its actor changed hands, or its frame
        was never written): without this, control coming back would make it decidable and pending again,
        and nothing could be attempted against the obstacle until it was decided. Only the obstacle's
        latest attempt (``not_linked``, ``withdrawal_mismatch``), only while it is pending, or diverged by its
        own frame (no revision of it can come once its turn has passed), and undecided (``not_pending``;
        ``already_withdrawn``), and only while 029 cannot decide it (``attempt_decidable``). 035 decides an attempt
        at an obstacle only under the same lock, after reading that it is not void (``refuse_void_attempt``).

        ``set_aside``: the caller vouches that the attempt's turn is on a branch a fork choice sets aside (028), where
        nothing in play can decide it however 029 stands, so 029's ``undecidable`` is not asked; one 029 decided
        already is still ``attempt_decidable``.
        """
        with self._authority.lock:
            builder = self._builder(campaign_id)
            builder.check_withdrawal(obstacle_id, attempt_id)
            last = self._derive(builder, obstacle_id).attempts[-1]
            if last.attempt.attempt_id != attempt_id or not (
                    last.status == "pending" or (last.status == "diverged" and last.problem != "attempt_out_of_turn")):
                raise _refused(f"attempt {attempt_id!r} is not pending ({last.status})", "not_pending")
            if (self._adjudication.outcome(campaign_id, attempt_id) is not None
                    or (not set_aside and last.frame is not None
                        and self._adjudication.undecidable(campaign_id, attempt_id) is None)):
                raise _refused(f"attempt {attempt_id!r} can be decided now", "attempt_decidable")
            self._append(builder, "withdrawal", withdrawal_to_payload(obstacle_id, attempt_id))
            builder.add_withdrawal(attempt_id)

    def _admit(self, obstacle_id: str, frame: ActionFrame, approach: str | Approach,
               contribution: PlayerContribution | None, author: object):
        """Every check ``frame`` makes before it writes: the builder, and the link to append (``None`` for a
        revision of the pending attempt): ``(obstacle id, attempt, payload)``."""
        if type(frame) is not ActionFrame:
            raise TypeError("expected an ActionFrame")
        campaign_id = frame.campaign_id
        builder = self._builder(campaign_id)
        if obstacle_id not in builder.obstacles:
            raise _refused(f"no obstacle {obstacle_id!r}", "unknown_obstacle")
        state = self._derive(builder, obstacle_id)
        given, listed = resolve_approach(state.obstacle, approach)
        attempt = Attempt(frame.attempt_id, given, listed)
        linked = builder.linked.get(frame.attempt_id)
        if linked is not None and linked != obstacle_id:
            raise _refused(f"attempt {frame.attempt_id!r} is against obstacle {linked!r}", "attempt_linked")
        last = state.attempts[-1] if state.attempts else None
        if frame.attempt_id in builder.withdrawn:
            raise _refused(f"attempt {frame.attempt_id!r} was withdrawn", "attempt_withdrawn")
        if linked is not None:
            revisable = (last is not None and last.attempt.attempt_id == frame.attempt_id
                         and (last.status == "pending" or (last.status == "diverged"
                                                           and last.problem != "attempt_out_of_turn"))
                         and self._adjudication.outcome(campaign_id, frame.attempt_id) is None)
            if not revisable:
                if state.status == "diverged":
                    raise _refused(f"obstacle {obstacle_id!r} no longer agrees with its attempts "
                                   f"({state.problem})", "obstacle_diverged")
                raise _refused(f"attempt {frame.attempt_id!r} is already decided", "already_decided")
            assert last is not None
            if (last.attempt.approach, last.attempt.listed) != (given, listed):
                raise _refused("a revision keeps the attempt's approach", "approach_changed")
            earlier = [a.frame.moment.sequence for a in state.attempts[:-1] if a.frame is not None]
            self._check(state, attempt, frame, max(earlier) if earlier else None)
            self._adjudication.check(frame, contribution=contribution, author=author)  # 029's refusals, unwritten
            return builder, None
        if state.status == "diverged":
            raise _refused(f"obstacle {obstacle_id!r} no longer agrees with its attempts ({state.problem})",
                           "obstacle_diverged")
        if state.status == "overcome":
            raise _refused(f"obstacle {obstacle_id!r} is already overcome", "already_overcome")
        pending = state.pending
        if (pending is not None and pending.frame is not None
                and self._adjudication.undecidable(campaign_id, pending.attempt.attempt_id) is None):
            raise _refused(f"attempt {pending.attempt.attempt_id!r} against {obstacle_id!r} is undecided",
                           "attempt_pending")
        # This one supersedes a pending attempt that cannot be decided now: a link whose frame was never
        # written, or a frame whose actor changed hands (029 makes the new controller's action a new one).
        # The link names it, so it stays void even if control comes back and 029 decides it after all.
        superseded = None if pending is None else pending.attempt.attempt_id
        if self._adjudication.outcome(campaign_id, frame.attempt_id) is not None:
            # Linking a known result would let a success be pinned to an obstacle after the fact.
            raise _refused(f"attempt {frame.attempt_id!r} is already decided", "already_decided")
        attempt = builder.check_attempt(obstacle_id, attempt.attempt_id, given.approach_id,
                                        None if listed else given, superseded)
        self._check(state, attempt, frame, state.after)
        self._adjudication.check(frame, contribution=contribution, author=author)  # 029's refusals, unwritten
        payload = attempt_to_payload(obstacle_id, attempt.attempt_id, given, listed, superseded)
        try:  # what _append would refuse, refused before anything is written
            entry_cost(envelope("attempt", self._authority.now(), recorded_by=self._principal, value=payload))
        except InvalidEnvelope as error:
            raise _refused(str(error), "invalid_field") from None
        except ValueError as error:
            raise _refused(str(error), "too_large") from None
        return builder, (obstacle_id, attempt, payload)

    # -- internals ----------------------------------------------------------------------------

    @staticmethod
    def _check(state: ObstacleState, attempt: Attempt, frame: ActionFrame, after: int | None) -> None:
        """The frame against the obstacle's facts as they stand now (its own, and earlier escalations)."""
        check_attempt(state.obstacle, state.facts, attempt, frame, after)

    def _derive(self, builder: _Builder, obstacle_id: str) -> ObstacleState:
        entries = builder.obstacles.get(obstacle_id)
        if not entries:
            raise _refused(f"no obstacle {obstacle_id!r}", "unknown_obstacle")
        adjudication = self._adjudication.state(builder.campaign_id)
        attempts = builder.attempts.get(obstacle_id, [])
        frames = {a.attempt_id: entry.frame for a in attempts
                  if (entry := adjudication.frame(a.attempt_id)) is not None}
        outcomes = {a.attempt_id: adjudication.decisions[a.attempt_id].outcome for a in attempts
                    if a.attempt_id in adjudication.decisions}
        return derive(entries[-1].obstacle, entries[-1].revision, attempts, frames, outcomes,
                      builder.withdrawn)

    def _append(self, builder: _Builder, kind: str, payload: dict[str, object]) -> None:
        name = ledger_for(builder.campaign_id)
        entry = envelope(kind, self._authority.now(), recorded_by=self._principal, value=payload)
        try:
            builder.head = self._authority._append(name, [entry], builder.head)
        except InvalidEnvelope as error:  # text the store cannot hold; nothing was written
            raise _refused(str(error), "invalid_field") from None
        except ValueError as error:  # no batch could hold it; nothing was written
            raise _refused(str(error), "too_large") from None

    def _builder(self, campaign_id: str) -> _Builder:
        name = ledger_for(campaign_id)
        views = self._authority.views
        if name not in views:
            ledger = self._authority.read_ledger(name)
            views[name] = _replay(campaign_id, name, ledger.entries, ledger.head)
        return views[name]  # type: ignore[return-value]


def _replay(campaign_id: str, name: str, entries: Iterable[object], head: Head | None) -> _Builder:
    """Re-check every entry with the record-time rules this ledger alone can check; else ``LedgerInvalid``.

    What needs 029's ledger too (an attempt linked while another was pending, a frame that no longer
    agrees with its obstacle) is not ledger damage: ``derive`` reports it as a diverged obstacle.
    """
    builder = _Builder(campaign_id)
    for index, entry in enumerate(entries, 1):
        kind = entry.get("kind") if isinstance(entry, Mapping) else None
        if kind not in _KINDS:
            raise LedgerInvalid(f"{name} entry {index}: unknown kind {kind!r}")
        data = exact(entry, kind, {"recorded_by", "value"})  # type: ignore[arg-type]
        recorded_by = data["recorded_by"]
        if not is_valid_principal(recorded_by):
            raise LedgerInvalid(f"{name} entry {index}: recorded_by is not a principal")
        try:
            if kind == "obstacle":
                obstacle = obstacle_from_payload(campaign_id, data["value"])
                builder.add_obstacle(ObstacleEntry(obstacle, builder.check_obstacle(obstacle),
                                                   recorded_by))  # type: ignore[arg-type]
            elif kind == "withdrawal":
                obstacle_id, attempt_id = withdrawal_from_payload(data["value"])
                builder.check_withdrawal(obstacle_id, attempt_id)
                builder.add_withdrawal(attempt_id)
            else:
                obstacle_id, attempt_id, approach_id, novel, supersedes = attempt_from_payload(data["value"])
                builder.add_attempt(obstacle_id, builder.check_attempt(obstacle_id, attempt_id, approach_id, novel,
                                                                       supersedes))
        except (ObstacleError, AdjudicationError) as error:
            raise LedgerInvalid(f"{name} entry {index}: {error} ({error.reason})") from None
    builder.head = head
    return builder
