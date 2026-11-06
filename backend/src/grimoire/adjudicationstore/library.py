"""Store-backed fiction-first adjudication (Sprint 029): frames, rulings and decisions on record.

``AdjudicationLibrary`` records into one authority ledger per campaign, ``adjudication/<campaign_id>``,
through the library's ``DurableAuthority``: append-only, hash-chained, written only by the authority's
capability, and replayed and re-checked on load (anything inconsistent is ``LedgerInvalid``, so the
library fails closed). Its handle is the **adjudication key**, a trusted handle the authority issued:
frames and rulings are labels, so only that channel records them, never narration.

Order is the guarantee. A frame (the action, the facts that bear on it, what is at stake) and any 025
ability ruling are recorded *before* the decision, and the decision may cite only what they hold. A
frame may be revised, and a ruling superseded, only until the attempt is decided; a decision is final
in this slice. For a player-controlled actor, the frame is backed by the controller's own 003 action
contribution (``contribution_id == attempt_id``), and a ``consent`` decision needs the controller's
own trusted human handle: the player chooses a worse outcome for their PC, never a better one.

The library computes 025's ``AbilityAssessment`` itself, from the chat's snapshot and the campaign's
live rulings, and the decision records what it relied on (an ``AbilityFinding``). ``ability_use``
hands that assessment to 021 only while it still holds, and ``check_effect`` verifies a proposal's.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from grimoire.adjudication import (
    AbilityFinding, ActionAssessment, ActionFrame, AdjudicationError, Decision, Outcome, assess_action,
    check_decision, decision_from_payload, decision_to_payload, frame_from_payload, frame_to_payload,
    ruling_from_payload, ruling_to_payload,
)
from grimoire.adjudication.model import DIRECTOR_ATTEMPT, PROSE_MAX
from grimoire.chats import ChatCodecError
from grimoire.core.ability_use import AbilityAssessment, AbilityError, AbilityRuling, assess_attempt, check_ruling
from grimoire.core.authority import DIRECTOR, AuthorityError, PlayerContribution, Resolution, controller_of
from grimoire.core.entity import EffectiveEntity
from grimoire.core.identity import TEXT_MAX, InstanceRef, validate_segment
from grimoire.effective import chat_snapshot
from grimoire.effects import EffectProposal
from grimoire.ledgers import DurableAuthority, SovereigntyLedger
from grimoire.ledgers.codec import envelope, exact
from grimoire.security import is_human, is_valid_principal
from grimoire.store import LibraryStore, RecordNotFound
from grimoire.store.errors import InvalidEnvelope, LedgerInvalid, TakenIdsUnlisted
from grimoire.store.ledger import Head, entry_cost

_KINDS = frozenset({"frame", "ruling", "decision"})


def ledger_for(campaign_id: str) -> str:
    """The campaign's adjudication ledger name; an invalid campaign id is ``invalid_field``."""
    if type(campaign_id) is not str:
        raise AdjudicationError("campaign_id must be plain text", "invalid_field")
    try:
        validate_segment(campaign_id, "campaign_id")
    except ValueError as error:
        raise AdjudicationError(str(error), "invalid_field") from None
    return f"adjudication/{campaign_id}"


@dataclass(frozen=True)
class FrameEntry:
    """One recorded revision of an action's frame (revisions count from 1)."""

    frame: ActionFrame
    contribution: PlayerContribution | None
    revision: int
    recorded_by: str


@dataclass(frozen=True)
class RulingEntry:
    ruling: AbilityRuling
    supersedes: frozenset[str]
    recorded_by: str


@dataclass(frozen=True)
class DecisionEntry:
    outcome: Outcome
    revision: int
    ability: AbilityFinding | None


@dataclass(frozen=True)
class AdjudicationState:
    """A campaign's adjudication ledger, replayed: every frame revision, ruling and decision."""

    campaign_id: str
    frames: Mapping[str, tuple[FrameEntry, ...]]  # by attempt id, oldest revision first
    rulings: tuple[RulingEntry, ...]
    superseded: frozenset[str]
    decisions: Mapping[str, DecisionEntry]

    def frame(self, attempt_id: str) -> FrameEntry | None:
        revisions = self.frames.get(attempt_id)
        return revisions[-1] if revisions else None

    def live_rulings(self) -> tuple[AbilityRuling, ...]:
        """Recorded rulings that no later ruling superseded: the ones 025 assessments use."""
        return tuple(entry.ruling for entry in self.rulings if entry.ruling.ruling_id not in self.superseded)

    def resolutions(self) -> tuple[Resolution, ...]:
        """003 resolutions for every decided attempt: narration must agree with them."""
        return tuple(self.decisions[attempt].outcome.resolution() for attempt in sorted(self.decisions))


def _refused(message: str, reason: str) -> AdjudicationError:
    return AdjudicationError(message, reason)


@dataclass
class _Builder:
    """The replayed state and the rules every entry obeys, at record time and again on replay."""

    campaign_id: str
    frames: dict[str, list[FrameEntry]] = field(default_factory=dict)
    rulings: list[RulingEntry] = field(default_factory=list)
    superseded: set[str] = field(default_factory=set)
    decisions: dict[str, DecisionEntry] = field(default_factory=dict)
    head: Head | None = None

    def check_frame(self, frame: ActionFrame, contribution: PlayerContribution | None) -> int:
        """The frame's revision number, or a refusal."""
        if frame.campaign_id != self.campaign_id:
            raise _refused("the frame belongs to another campaign", "campaign_mismatch")
        revisions = self.frames.get(frame.attempt_id, [])
        if revisions and revisions[0].frame.actor != frame.actor:
            raise _refused(f"attempt {frame.attempt_id!r} is another actor's", "attempt_reused")
        if (contribution is None) != frame.attempt_id.startswith(DIRECTOR_ATTEMPT):
            # One namespace per kind of actor: a director's attempt can never share a player's contribution
            # id, so its outcome can never bind narration about a player character (003 keys by attempt id).
            raise _refused(f"an attempt with no player contribution, and only one, is named "
                           f"{DIRECTOR_ATTEMPT!r}...", "reserved_attempt_id")
        if revisions:
            first = revisions[0]
            if (first.frame.chat, first.frame.moment, first.contribution, first.frame.ability) != (
                    frame.chat, frame.moment, contribution, frame.ability):
                # The same action: a later moment would admit facts established after it was attempted, and
                # another power is another action.
                raise _refused("a revision keeps the action's chat, moment, contribution and ability",
                               "frame_moved")
            worse = _worsened(first.frame, frame)
            if worse:
                # A difficulty or a consequence invented after the action was framed, or a recorded advantage
                # dropped, is fiat by revision.
                raise _refused(f"a revision cannot worsen the action: {', '.join(worse)}", "frame_worsened")
        if frame.attempt_id in self.decisions:
            raise _refused(f"attempt {frame.attempt_id!r} is already decided", "already_decided")
        if contribution is not None:
            _check_backing(frame, contribution)
        return len(revisions) + 1

    def check_ruling(self, ruling: AbilityRuling, supersedes: frozenset[str]) -> FrameEntry:
        """The frame the ruling is on, or a refusal."""
        attempt = ruling.attempt
        if attempt.actor.campaign_id != self.campaign_id:
            raise _refused("the ruling belongs to another campaign", "campaign_mismatch")
        entry = self.frames.get(attempt.attempt_id)
        if not entry or entry[-1].frame.ability != attempt:
            raise _refused("a ruling decides the ability use of a recorded frame, labelled exactly as there",
                           "ruling_mismatch")
        if attempt.attempt_id in self.decisions:
            raise _refused(f"attempt {attempt.attempt_id!r} is already decided", "already_decided")
        if any(each.ruling.ruling_id == ruling.ruling_id for each in self.rulings):
            raise _refused(f"ruling id {ruling.ruling_id!r} is already used", "duplicate_ruling")
        live = {each.ruling.ruling_id: each.ruling for each in self.rulings
                if each.ruling.ruling_id not in self.superseded}
        for ruling_id in sorted(supersedes):
            found = live.get(ruling_id)
            if found is None or found.attempt.attempt_id != attempt.attempt_id or found.attempt.actor != attempt.actor:
                raise _refused(f"no live ruling {ruling_id!r} on this attempt to supersede", "unknown_ruling")
        return entry[-1]

    def check_decision(self, attempt_id: str, revision: int, classification: str, decision: Decision,
                       consented_by: str | None, ability: AbilityFinding | None) -> Outcome:
        """The outcome the decision records, re-checked against its frame revision, or a refusal."""
        revisions = self.frames.get(attempt_id)
        if not revisions:
            raise _refused(f"no recorded frame for attempt {attempt_id!r}", "unknown_attempt")
        if attempt_id in self.decisions:
            raise _refused(f"attempt {attempt_id!r} is already decided", "already_decided")
        if revision != len(revisions):
            raise _refused("a decision is on the frame's latest revision", "stale_frame")
        frame = revisions[-1].frame
        if ability is not None:
            live = [each.ruling for each in self.rulings if each.ruling.ruling_id not in self.superseded
                    and each.ruling.attempt == frame.ability]
            if ability.ruling_id is not None:
                ruling = next((each for each in live if each.ruling_id == ability.ruling_id), None)
                if ruling is None:
                    raise _refused("the decision relies on a ruling that is not live on this attempt",
                                   "unknown_ruling")
                if (ruling.decides, ruling.decision) != (ability.classification, ability.approval):
                    # The finding must say what its ruling decided: a refusal cannot be recorded as approval.
                    raise _refused(f"ruling {ruling.ruling_id!r} {ruling.decision} a {ruling.decides}, not what "
                                   "the decision recorded", "ruling_mismatch")
            elif ability.approval == "pending" and ability.classification != "combination" and any(
                    each.decides == ability.classification for each in live):
                # Only a combination's ruling can fail to apply (its terms); any other live ruling decides it.
                raise _refused("the decision recorded a pending approval that a live ruling had decided",
                               "ruling_mismatch")
        assessment = assess_action(frame, ability)
        if assessment.classification != classification:
            raise _refused("the recorded classification is not the frame's", "classification_mismatch")
        check_decision(assessment, decision)
        if (decision.basis == "consent") != (consented_by is not None):
            raise _refused("a consent decision, and only one, names who consented", "consent_mismatch")
        if consented_by is not None:
            backing = revisions[-1].contribution
            if backing is None or backing.submitted_by != consented_by:
                raise _refused("consent comes from the player who authored the action", "consent_mismatch")
        return Outcome(frame, assessment.classification, decision, "", consented_by)

    def add_frame(self, entry: FrameEntry) -> None:
        self.frames.setdefault(entry.frame.attempt_id, []).append(entry)

    def add_ruling(self, entry: RulingEntry) -> None:
        self.rulings.append(entry)
        self.superseded |= entry.supersedes

    def state(self) -> AdjudicationState:
        return AdjudicationState(self.campaign_id, {key: tuple(value) for key, value in self.frames.items()},
                                 tuple(self.rulings), frozenset(self.superseded), dict(self.decisions))


def _worsened(first: ActionFrame, frame: ActionFrame) -> list[str]:
    """What a revision would do against the action: a new hindrance, prevention or stake, or a lost advantage."""
    against = {f for f in first.factors if f.bearing != "favours"}
    favours = {f for f in frame.factors if f.bearing == "favours"}
    added = [f.factor_id for f in frame.factors if f.bearing != "favours" and f not in against]
    dropped = [f.factor_id for f in first.factors if f.bearing == "favours" and f not in favours]
    stakes = [s.stake_id for s in frame.stakes if s not in first.stakes]
    return added + dropped + stakes


def _check_backing(frame: ActionFrame, contribution: PlayerContribution) -> None:
    """A player's action contribution backs exactly this frame: same id, actor, scene and turn."""
    if type(contribution) is not PlayerContribution:
        raise _refused("a frame is backed by a PlayerContribution", "invalid_field")
    if (contribution.campaign_id != frame.campaign_id or contribution.contribution_id != frame.attempt_id
            or contribution.actor_id != frame.actor.instance_id or contribution.kind != "action"
            or contribution.scene_id != frame.moment.scene_id or contribution.turn_id != frame.moment.turn_id):
        raise _refused("the contribution is not this actor's action for this turn under this attempt id",
                       "invalid_expression")
    for label in ("contribution_id", "scene_id", "turn_id", "actor_id", "submitted_by", "text"):
        value = getattr(contribution, label)
        if type(value) is not str or not _storable(value):
            raise _refused(f"the contribution's {label} cannot be stored", "invalid_field")
        if label != "text" and len(value) > TEXT_MAX:
            raise _refused(f"the contribution's {label} cannot be stored", "invalid_field")
    if len(contribution.text) > PROSE_MAX:
        raise _refused(f"the contribution is longer than {PROSE_MAX} characters", "contribution_too_long")


def _storable(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate could never be stored
        return False
    return True


def adjudication_actor_ids(store: LibraryStore, campaign_id: str) -> frozenset[str]:
    """Every actor instance id this campaign's adjudication ledger names.

    Part of ``grimoire.chatstore.campaign_taken_ids``: frames, rulings and decisions name their actor
    by reference, so a new individual given a hand-deleted actor's id would inherit its outcomes. The
    batches are scanned raw, with no replay or chain check, so a broken ledger still protects its ids:
    every reference text under an ``actor`` key that parses as this campaign's instance, and every
    contribution ``actor_id``. A ledger that cannot all be read raises ``TakenIdsUnlisted``.
    """
    documents, unread = store.ledger_documents(ledger_for(campaign_id))
    if unread:
        raise TakenIdsUnlisted(f"the adjudication ledger of {campaign_id} cannot all be read ({', '.join(unread)}), "
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


class AdjudicationLibrary:
    """Records and reads fiction-first adjudication through the library's durable authority.

    ``handle`` is the adjudication key: a trusted handle ``authority`` issued. The replayed state lives
    on the authority (``views``), so every library over one authority shares it, and each append
    continues from the head it replayed: a batch written by anything else poisons the authority until
    it is reopened and the ledger replayed and re-checked.
    """

    def __init__(self, store: LibraryStore, authority: DurableAuthority, handle: object) -> None:
        if type(authority) is not DurableAuthority:
            raise TypeError("expected the library's DurableAuthority")
        if not (authority.issued(handle) and getattr(handle, "trusted", False) is True):
            raise AuthorityError("the adjudication key must be a trusted handle issued by this library's authority")
        self.store = store
        self._authority = authority
        self._principal: str = handle.principal  # type: ignore[attr-defined]

    # -- reading ------------------------------------------------------------------------------

    def state(self, campaign_id: str) -> AdjudicationState:
        ledger_for(campaign_id)
        with self._authority.lock:
            return self._builder(campaign_id).state()

    def outcome(self, campaign_id: str, attempt_id: str) -> Outcome | None:
        entry = self.state(campaign_id).decisions.get(attempt_id)
        return None if entry is None else entry.outcome

    def resolutions(self, campaign_id: str) -> tuple[Resolution, ...]:
        return self.state(campaign_id).resolutions()

    def assess(self, campaign_id: str, attempt_id: str) -> ActionAssessment:
        """The latest frame of ``attempt_id`` assessed now, against the live rulings and the chat."""
        state = self.state(campaign_id)
        frame = self._latest(state, attempt_id).frame
        return assess_action(frame, self._ability(frame, state.live_rulings()))

    def ability_use(self, campaign_id: str, attempt_id: str) -> AbilityAssessment:
        """025's assessment of a decided, permitted ability use, for 021 to charge: only while it still holds.

        Refused: ``undecided``, ``no_ability``, ``ability_not_permitted`` (the decision relied on a use
        the power does not permit), ``action_failed`` (a failed action charges and applies nothing in
        this slice), and ``assessment_changed`` (a re-assessment now, against the chat and
        live rulings, is not what the decision recorded: a definition or ruling changed since).
        """
        state = self.state(campaign_id)
        entry = state.decisions.get(attempt_id)
        if entry is None:
            raise _refused(f"attempt {attempt_id!r} is not decided", "undecided")
        frame = entry.outcome.frame
        if frame.ability is None or entry.ability is None:
            raise _refused(f"attempt {attempt_id!r} relies on no ability", "no_ability")
        if not entry.ability.permitted:
            raise _refused("the decided use is not one the power permits", "ability_not_permitted")
        if entry.outcome.decision.result == "failure":
            raise _refused("the action failed, so the power did nothing to charge or apply", "action_failed")
        assessment = self._ability(frame, state.live_rulings())
        if AbilityFinding.of(assessment) != entry.ability:  # type: ignore[arg-type]
            raise _refused("the ability use no longer assesses as it was decided", "assessment_changed")
        return assessment  # type: ignore[return-value]

    def check_effect(self, proposal: EffectProposal) -> None:
        """Refuse a 021 proposal whose ability uses are not exactly those adjudicated here.

        Each assessment must be ``ability_use`` of its attempt (``assessment_mismatch`` otherwise), so a
        caller cannot charge an assessment it made up, nor one that no longer holds.
        """
        if type(proposal) is not EffectProposal:
            raise TypeError("expected an EffectProposal")
        for given in proposal.abilities:
            current = self.ability_use(proposal.campaign_id, given.attempt.attempt_id)
            if current != given:
                raise _refused(f"attempt {given.attempt.attempt_id!r} was not adjudicated as proposed",
                               "assessment_mismatch")

    # -- recording ----------------------------------------------------------------------------

    def frame(self, frame: ActionFrame, *, contribution: PlayerContribution | None = None,
              author: object = None) -> FrameEntry:
        """Record ``frame`` (a new attempt, or a revision of an undecided one).

        The actor must be in the frame's chat, with its definitions resolving. A player-controlled
        actor's frame must be backed by the controller's own action ``contribution`` for this turn
        (``pc_authorship`` without one), given through ``author``, the controller's own trusted human
        handle from this authority (``not_controller`` otherwise): the adjudication key labels actions,
        it never speaks for a player. An actor no human controls takes neither (``unexpected_backing``).
        """
        with self._authority.lock:  # control is read and the entry appended as one step
            builder, revision, payload = self._prepare(frame, contribution, author)
            entry = FrameEntry(frame, contribution, revision, self._principal)
            self._append(builder, "frame", payload)
            builder.add_frame(entry)
            return entry

    def check(self, frame: ActionFrame, *, contribution: PlayerContribution | None = None,
              author: object = None) -> int:
        """The revision ``frame`` would record now, or the refusal ``frame`` would raise. Writes nothing.

        For a caller that must write its own record first and then the frame, under the authority's
        lock (040 links an attempt to its obstacle before framing it). The entry is rendered too, so
        text the store cannot hold (``invalid_field``) or an entry too large for a batch (``too_large``)
        is refused here, not when ``frame`` writes it.
        """
        with self._authority.lock:
            return self._prepare(frame, contribution, author)[1]

    def undecidable(self, campaign_id: str, attempt_id: str) -> str | None:
        """Why the recorded attempt cannot be decided now, whatever is decided: its actor's control
        changed since it was framed (``control_changed``, ``non_human_controller``). ``None`` when it can be,
        or when there is no such frame or it is already decided. This is as of now: if control returns to
        whoever held it when the attempt was framed, it can be decided again."""
        with self._authority.lock:
            state = self._builder(campaign_id).state()
            entry = state.frame(attempt_id)
            if entry is None or attempt_id in state.decisions:
                return None
            try:
                self._check_control(entry)
            except AdjudicationError as error:
                return error.reason
            return None

    def _prepare(self, frame: ActionFrame, contribution: PlayerContribution | None, author: object
                 ) -> tuple[_Builder, int, dict[str, object]]:
        """Every check ``frame`` makes before it writes: the ledger, the revision and the payload."""
        if type(frame) is not ActionFrame:
            raise TypeError("expected an ActionFrame")
        campaign_id = frame.campaign_id
        if contribution is not None:
            _check_backing(frame, contribution)
        payload = frame_to_payload(frame, contribution)
        controller = self._controller(campaign_id, frame.actor.instance_id)
        if controller is None or controller == DIRECTOR:
            if contribution is not None or author is not None:
                raise _refused("an actor no player controls has no contribution to back it",
                               "unexpected_backing")
        elif not is_human(controller):
            raise _refused(f"{frame.actor} is controlled by {controller!r}, not a human", "non_human_controller")
        elif contribution is None:
            raise _refused("a player character's action is backed by the player's own contribution",
                           "pc_authorship")
        elif contribution.submitted_by != controller:
            raise _refused("the contribution is not the current controller's", "invalid_expression")
        elif not self._is_own(author, controller):
            raise _refused("only the controller's own trusted handle gives their contribution",
                           "not_controller")
        self._entity(frame)
        builder = self._builder(campaign_id)
        revision = builder.check_frame(frame, contribution)
        try:  # what _append would refuse, refused before anything is written
            entry_cost(envelope("frame", self._authority.now(), recorded_by=self._principal, value=payload))
        except InvalidEnvelope as error:
            raise _refused(str(error), "invalid_field") from None
        except ValueError as error:
            raise _refused(str(error), "too_large") from None
        return builder, revision, payload

    def rule(self, ruling: AbilityRuling, *, supersedes: Iterable[str] = ()) -> RulingEntry:
        """Record a director's 025 ruling on a recorded frame's ability use, before its decision.

        ``supersedes`` names live rulings on the same attempt that this one replaces (a director
        changing their mind before the outcome); 025's ``check_ruling`` then runs against the rest.
        """
        if type(ruling) is not AbilityRuling:
            raise TypeError("expected an AbilityRuling")
        replaced = frozenset(supersedes)
        if not all(type(each) is str for each in replaced):
            raise _refused("supersedes names ruling ids", "invalid_field")
        campaign_id = ruling.attempt.actor.campaign_id
        payload = ruling_to_payload(ruling, replaced)
        with self._authority.lock:
            builder = self._builder(campaign_id)
            entry = builder.check_ruling(ruling, replaced)
            existing = [each for each in builder.state().live_rulings() if each.ruling_id not in replaced]
            try:
                check_ruling(ruling, self._entity(entry.frame), existing)
            except AbilityError as error:
                raise _refused(str(error), error.reason) from None
            recorded = RulingEntry(ruling, replaced, self._principal)
            self._append(builder, "ruling", payload)
            builder.add_ruling(recorded)
            return recorded

    def decide(self, campaign_id: str, attempt_id: str, decision: Decision, *,
               consent: object = None) -> Outcome:
        """Record the outcome of ``attempt_id``'s latest frame, assessed now; final in this slice.

        A ``consent`` decision needs ``consent``: for a player character, the controller's own trusted
        human handle from this authority; for an actor no player controls, none (the director's choice).
        Deciding the same again returns the recorded outcome; a different decision is ``already_decided``.
        """
        if type(decision) is not Decision:
            raise TypeError("expected a Decision")
        with self._authority.lock:
            state = self._builder(campaign_id).state()
            done = state.decisions.get(attempt_id)
            if done is not None:
                if done.outcome.decision == decision:
                    return done.outcome
                raise _refused(f"attempt {attempt_id!r} is already decided", "already_decided")
            latest = self._latest(state, attempt_id)
            frame = latest.frame
            self._check_control(latest)
            consented_by = self._consent(latest, decision, consent)
            ability = self._ability(frame, state.live_rulings())
            finding = None if ability is None else AbilityFinding.of(ability)
            revision = len(state.frames[attempt_id])
            assessment = assess_action(frame, finding)
            builder = self._builder(campaign_id)
            outcome = builder.check_decision(attempt_id, revision, assessment.classification, decision,
                                             consented_by, finding)
            outcome = Outcome(outcome.frame, outcome.classification, decision, self._principal, consented_by)
            self._append(builder, "decision", decision_to_payload(frame, revision, assessment.classification,
                                                                  decision, consented_by, finding))
            builder.decisions[attempt_id] = DecisionEntry(outcome, revision, finding)
            return outcome

    # -- internals ----------------------------------------------------------------------------

    def _latest(self, state: AdjudicationState, attempt_id: str) -> FrameEntry:
        entry = state.frame(attempt_id)
        if entry is None:
            raise _refused(f"no recorded frame for attempt {attempt_id!r}", "unknown_attempt")
        return entry

    def _controller(self, campaign_id: str, actor_id: str) -> str | None:
        controls = SovereigntyLedger(self._authority).load(campaign_id).controls
        return controller_of(campaign_id, actor_id, controls)

    def _is_own(self, handle: object, principal: str) -> bool:
        return (self._authority.issued(handle) and getattr(handle, "trusted", False) is True
                and getattr(handle, "principal", None) == principal)

    def _check_control(self, entry: FrameEntry) -> None:
        """The actor is still controlled as when it was framed: by its author, or by no player."""
        frame = entry.frame
        controller = self._controller(frame.campaign_id, frame.actor.instance_id)
        if controller is not None and controller != DIRECTOR and not is_human(controller):
            raise _refused(f"{frame.actor} is controlled by {controller!r}, not a human", "non_human_controller")
        player = None if controller in (None, DIRECTOR) else controller
        author = None if entry.contribution is None else entry.contribution.submitted_by
        if player != author:
            raise _refused(f"control of {frame.actor} changed since the action was framed; this attempt cannot "
                           "be decided, and the new controller's action is a new attempt", "control_changed")

    def _consent(self, entry: FrameEntry, decision: Decision, consent: object) -> str | None:
        if decision.basis != "consent":
            if consent is not None:
                raise _refused("only a consent decision takes a consenting handle", "consent_mismatch")
            return None
        if entry.contribution is None:
            # The director has rule and adjudicated decisions, which cite causes; an uncaused setback
            # is a player's own authorial choice, never the director's.
            raise _refused("only a player consents, for their own character's action", "consent_mismatch")
        author = entry.contribution.submitted_by
        if not self._is_own(consent, author):
            raise _refused("only the action's author consents, with their own trusted handle", "not_controller")
        return author

    def _entity(self, frame: ActionFrame) -> EffectiveEntity:
        """The actor as the frame's chat composes it now; refusals name what is missing."""
        try:
            captured = chat_snapshot(self.store.load(), frame.chat)
        except RecordNotFound:
            raise _refused(f"no chat {frame.chat}", "missing_chat") from None
        except ChatCodecError as error:
            raise _refused(f"chat {frame.chat} cannot be read: {error}", "missing_chat") from None
        if all(entry.actor != frame.actor for entry in captured.chat.roster):
            raise _refused(f"{frame.actor} is not in chat {frame.chat}", "not_in_chat")
        try:
            return captured.snapshot.entity(frame.actor)
        except (ValueError, LookupError) as error:
            raise _refused(f"{frame.actor} does not resolve: {error}", "unresolved_actor") from None

    def _ability(self, frame: ActionFrame, rulings: tuple[AbilityRuling, ...]) -> AbilityAssessment | None:
        if frame.ability is None:
            return None
        try:
            return assess_attempt(frame.ability, self._entity(frame), rulings)
        except AbilityError as error:
            raise _refused(str(error), error.reason) from None

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
    """Re-check every entry with the record-time rules; anything else is ``LedgerInvalid``."""
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
            if kind == "frame":
                frame, contribution = frame_from_payload(campaign_id, data["value"])
                builder.add_frame(FrameEntry(frame, contribution, builder.check_frame(frame, contribution),
                                             recorded_by))  # type: ignore[arg-type]
            elif kind == "ruling":
                ruling, supersedes = ruling_from_payload(campaign_id, data["value"])
                builder.check_ruling(ruling, supersedes)
                builder.add_ruling(RulingEntry(ruling, supersedes, recorded_by))  # type: ignore[arg-type]
            else:
                attempt_id, actor, revision, classification, decision, consented_by, finding = (
                    decision_from_payload(campaign_id, data["value"]))
                outcome = builder.check_decision(attempt_id, revision, classification, decision, consented_by,
                                                 finding)
                if outcome.frame.actor != actor:
                    raise _refused("the decision names another actor", "attempt_reused")
                outcome = Outcome(outcome.frame, outcome.classification, decision,
                                  recorded_by, consented_by)  # type: ignore[arg-type]
                builder.decisions[attempt_id] = DecisionEntry(outcome, revision, finding)
        except AdjudicationError as error:
            raise LedgerInvalid(f"{name} entry {index}: {error} ({error.reason})") from None
    builder.head = head
    return builder
