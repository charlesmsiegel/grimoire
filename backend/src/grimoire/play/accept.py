"""Accepting a turn's structured effect into 021 (035 milestone 2), with 030's recovery at open time.

A turn's effect is proposed as structured 021 operations and holdings lines (no model call proposes one in
this slice), kept whole on the turn (``TurnRecord.effects``), and accepted once it holds every key: the
director's approval (the outcome key, the library owner) and, for each act of a player character, that
player's own endorsement of the exact fingerprint (the authorship key). Whichever key comes last commits.

The commit order is fixed, so a crash anywhere leaves something open-time recovery can read:

1. nothing waits for recovery (``recovery_needed``): no ``accepting`` effect anywhere in the campaign, and no
   turn file that cannot be read (it could hide one);
2. 029's ``check_effect``, then 021's ``accept`` (its preview: nothing is written);
3. play is not paused and control is as the call read it (both re-read under the store lock), and the effect is
   written ``accepting``, with its keys, the allocated event id and the sovereignty ledger position whose
   control it was authorized under, by compare-and-swap;
4. 021's commit (one store transaction);
5. the effect is written ``accepted``.

A crash after 3 leaves an ``accepting`` effect: ``open`` reports it (``effect_unsettled``) with the shape it
finds, and the owner's ``recover_effects`` settles it with 030's ``complete`` or ``adopt`` (or aborts it with
021's ``revoke(partial=True)``), before any other effect is accepted. ``adopt`` replays the control recorded with
``accepting`` from the sovereignty ledger, so a character changing hands since never strands it.

An endorsement given with a player's own trusted handle is also recorded by the library's durable authority (a
``propose`` decision of that handle at ``endorsement_address``, in the ``security`` ledger, which only the
authority writes and sync never carries). A recorded endorsement is presented to 021 again, on a handle minted
for that player, only when that decision is in the authority's audit: an endorsement written into a turn file
by hand or by a peer is refused ``invalid_endorsement``. See docs/TOOL_AUTHORITY.md.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace

from grimoire.core import DIRECTOR
from grimoire.core.authority import AuthorityError, PlayerContribution
from grimoire.core.identity import AcceptedRef, ChatRef, TurnRef
from grimoire.core.knowledge import Moment
from grimoire.core.model import ControlAssignment
from grimoire.effects import EffectError, EffectProposal, EndorsementRecord, authorize, revocation_fingerprint
from grimoire.effectstore import Endorsement, InvalidEffect
from grimoire.play.adjudicate import _029, _UNAVAILABLE, outcome_of
from grimoire.play.scene import NOW, WRITE_RETRIES, _readable, _refuse
from grimoire.scene import MAX_EFFECTS, SceneRefused, TurnEffect, TurnRecord, turn_from_payload
from grimoire.scenestore import StoredTurn
from grimoire.security import Permission, is_human
from grimoire.store import StaleWrite, StoreError
from grimoire.store.layout import address_parts

#: What an aborted acceptance's revocation records as its reason.
ABORTED = "035 recovery: aborted by the director"
#: The shapes ``open`` names for an ``accepting`` effect (030): what landed of its acceptance.
SHAPES = ("landed", "complete", "revoked", "adopt", "unpublished", "unreadable")


def _library(runner):
    if runner.effects is None:
        raise _refuse("no_effects", "no accepted-effects library is configured")
    return runner.effects


# -- sequences ------------------------------------------------------------------------------------

def next_sequence(runner, campaign_id: str, known: int = 0) -> int:
    """The campaign sequence a new labelling or effect takes: one after every sequence already used.

    ``known`` is the newest knowledge sequence the caller read (020). Every accepted-event file (active or
    revoked) and every labelling and effect proposal on the campaign's turns count, read raw, so the
    sequences a campaign hands out only ever increase (021 asks for one non-decreasing sequence across all
    chats of a scope). A file that cannot be read at all is skipped here: an acceptance refuses while one is.
    Call it under the store lock with the write that uses it, so two calls never take the same one.
    """
    latest = known
    for stored in runner.store.campaign_accepted(campaign_id).records.values():
        latest = max(latest, _sequence(_get(stored.record.payload, "moment")))
    for stored in runner.store.campaign_turns(campaign_id).records.values():
        payload = stored.record.payload
        for label in _items(_get(payload, "labels")):
            latest = max(latest, _number(_get(label, "sequence")))
        for effect in _items(_get(payload, "effects")):
            latest = max(latest, _sequence(_get(_get(effect, "proposal"), "moment")))
    return latest + 1


def _get(value: object, key: str) -> object:
    return value.get(key) if isinstance(value, Mapping) else None


def _items(value: object) -> tuple:
    return tuple(value) if isinstance(value, (list, tuple)) else ()


def _number(value: object) -> int:
    return value if type(value) is int else 0


def _sequence(moment: object) -> int:
    return _number(_get(moment, "sequence"))


# -- reading --------------------------------------------------------------------------------------

def contributions_of(record: TurnRecord) -> tuple[PlayerContribution, ...]:
    """The player's own 003 contribution a turn's post is (its id is the turn id), as 021 checks it."""
    post, chat = record.post, record.chat
    if post is None:
        return ()
    return (PlayerContribution(chat.campaign_id, record.turn_id, chat.chat_id, record.turn_id,
                               post.actor.instance_id, post.submitted_by, post.kind, post.text),)


def _turn(scene, turn: object) -> StoredTurn:
    if type(turn) is not str:
        raise _refuse("invalid_field", "a turn is named by its id")
    stored = next((each for each in scene.turns.turns if each.record.turn_id == turn), None)
    if stored is None:
        raise _refuse("unknown_turn", "this chat has no turn with that id")
    return stored


def _effect(stored: StoredTurn, number: object) -> TurnEffect:
    if type(number) is not int:
        raise _refuse("invalid_field", "an effect is named by its number")
    effect = stored.record.effect(number)
    if effect is None:
        raise _refuse("unknown_effect", "this turn has no effect with that number")
    return effect


def _collecting(effect: TurnEffect) -> None:
    """Only an effect still collecting keys takes one."""
    if effect.state == "accepting":
        raise _refuse("effect_accepting", "this effect's acceptance is under way; recover it if it was cut short")
    if effect.state == "accepted":
        raise _refuse("already_accepted", "this effect is accepted already")
    if not effect.live:
        raise _refuse("not_proposed", "only a proposed or approved effect takes a key")


def _playing(runner, chat: ChatRef, by):
    """The scene, the asking principal and the effects library, for a call that changes a turn's effect."""
    library = _library(runner)
    scene = runner._context(chat).scene
    principal = runner._principal(scene, by)
    _readable(scene)
    if scene.paused:
        raise _refuse("paused", "play is paused")
    return scene, principal, library


# -- proposing -----------------------------------------------------------------------------------

def propose_effect(runner, chat: ChatRef, by, turn: str, summary: str, operations=(), holdings=None, *,
                   abilities=()) -> TurnEffect:
    """See ``SceneRunner.propose_effect``."""
    scene, principal, _ = _playing(runner, chat, by)
    if not isinstance(abilities, (list, tuple)) or abilities:
        raise _refuse("not_in_this_slice", "no ability use is proposed or charged here yet")
    stored = _turn(scene, turn)
    record = stored.record
    if record.post is not None and record.post.kind == "action" and record.post.private:
        raise _refuse("private_action", "a withheld action takes no effect: the table never saw it")
    if record.kept is None:  # a kept turn, or a decided action (whose narration may still be open)
        if record.framed is None:
            raise _refuse("not_kept", "an effect is proposed for a kept turn or a decided action")
        if outcome_of(runner, record) is None:
            raise _refuse("undecided_attempt", "this action is framed and not yet decided; decide it first")
    director = principal in runner.authority.owners
    if not director and (record.post is None or record.post.submitted_by != principal):
        raise _refuse("not_your_turn", "a player proposes an effect only for their own post's turn")
    if any(effect.state == "accepting" for effect in record.effects):
        raise _refuse("effect_accepting", "this turn's effect is being accepted; recover it if it was cut short")
    current = record
    for effect in record.effects:
        if effect.state == "accepted":
            if effect.accepted_on is not None and _shape(runner, effect) == "unpublished" and (
                    effect.accepted_on != runner.scenes.device()):  # _whose: nothing of it here, another device's
                raise _refuse("accepted_elsewhere", f"this turn's effect was accepted on device {effect.accepted_on}, "
                                                    "whose 021 holds it (sync does not carry it)")
            if _in_force(runner, effect):
                raise _refuse("already_accepted", "this turn has an accepted effect (021 accepts one per turn)")
            current = current.with_effect(replace(effect, state="revoked"))  # revoked through 021 since
    number = len(record.effects) + 1
    if number > MAX_EFFECTS:
        raise _refuse("too_many_effects", f"a turn holds at most {MAX_EFFECTS} effect proposals")
    try:
        operations = tuple(operations)
    except TypeError:
        raise _refuse("invalid_field", "operations are 021 Op values") from None
    with runner.store.lock:  # the sequence is taken and written in one step
        sequence = next_sequence(runner, chat.campaign_id, runner._knowledge(chat)[1])
        try:
            proposal = EffectProposal(chat.campaign_id, chat, Moment(sequence, chat.chat_id, record.turn_id),
                                      summary, operations, "director" if director else "player",
                                      attempt_id=None if holdings is None else record.turn_id, holdings=holdings)
            # who must endorse: each player character's voluntary act needs its player's own contribution
            endorsers = authorize(proposal, scene.controls, contributions_of(record)).endorsers
        except EffectError as error:
            raise _refuse(error.reason, "the effect cannot be proposed") from None
        except AuthorityError:
            raise _refuse("ambiguous_control", "who controls an actor of this effect is unclear") from None
        except (TypeError, ValueError) as error:
            raise _refuse("invalid_field", str(error)[:200]) from None
        if not all(is_human(principal) for principal in endorsers.values()):
            raise _refuse("non_human_controller", "only a human player endorses a character's act")
        for effect in record.effects:
            if effect.live:
                current = current.with_effect(replace(effect, state="superseded"))
        effect = TurnEffect(number, "proposed", proposal, tuple(sorted(endorsers.items())))
        runner._write(current.with_effect(effect), stored, unpaused=True)
    return effect


def _in_force(runner, effect: TurnEffect) -> bool:
    """Whether an ``accepted`` effect's event is still active in 021 (it may have been revoked through 021)."""
    try:
        event, _ = runner.effects.read(AcceptedRef(effect.proposal.campaign_id, effect.event_id),
                                       requested_by=DIRECTOR)
    except InvalidEffect as error:
        raise _refuse(error.reason, "the accepted effect cannot be read") from None
    except StoreError as error:
        raise _refuse("effects_unavailable", type(error).__name__) from None
    return event.active


# -- the keys -------------------------------------------------------------------------------------

def approve_effect(runner, chat: ChatRef, by, turn: str, number: int) -> TurnEffect:
    """See ``SceneRunner.approve_effect``."""
    scene, principal, _ = _playing(runner, chat, by)
    if principal not in runner.authority.owners:
        raise _refuse("not_director", "the director (the library owner) approves an effect")
    stored = _turn(scene, turn)
    effect = _effect(stored, number)
    _collecting(effect)
    approved = replace(effect, state="approved", approved_by=principal)
    if approved.complete:  # the last key commits
        return _accept(runner, scene, stored, approved, {})
    runner._write(stored.record.with_effect(approved), stored, unpaused=True)
    return approved


def endorse_effect(runner, chat: ChatRef, by, turn: str, number: int, fingerprint: str) -> TurnEffect:
    """See ``SceneRunner.endorse_effect``."""
    scene, principal, _ = _playing(runner, chat, by)
    stored = _turn(scene, turn)
    effect = _effect(stored, number)
    _collecting(effect)
    if fingerprint != effect.proposal.fingerprint:
        raise _refuse("fingerprint_mismatch", "endorse the effect as it was shown: its fingerprint differs")
    mine = [cid for cid, endorser in effect.endorsers if endorser == principal]
    if not mine:
        raise _refuse("not_endorser", "only the player whose character acts endorses this effect")
    try:
        now = authorize(effect.proposal, scene.controls, contributions_of(stored.record)).endorsers
    except (EffectError, AuthorityError):
        now = None
    if now is None or tuple(sorted(now.items())) != effect.endorsers:
        raise _refuse("control_changed", "who plays a character of this effect changed since it was proposed")
    for cid in mine:  # the authority's own record of this key, before the turn records it
        _record_endorsement(runner, by, stored.record.ref, cid, fingerprint)
    given = {(item.contribution_id, item.principal): item for item in effect.endorsements}
    given.update({(cid, principal): EndorsementRecord(cid, principal, fingerprint) for cid in mine})
    endorsed = replace(effect, endorsements=tuple(given[key] for key in sorted(given)))
    if endorsed.complete:  # the last key commits, with this player's own live handle
        return _accept(runner, scene, stored, endorsed, {principal: by})
    runner._write(stored.record.with_effect(endorsed), stored, unpaused=True)
    return endorsed


def endorsement_address(ref: TurnRef, contribution_id: str, fingerprint: str) -> str:
    """The logical address the authority records an endorsement at: the turn, the contribution, the fingerprint."""
    return "/".join((*address_parts(ref), "endorsements", contribution_id, fingerprint.removeprefix("sha256:")))


def _recorded(runner, ref: TurnRef, item: EndorsementRecord) -> bool:
    """Whether the authority's audit holds ``item``'s endorsement as given with that player's own trusted handle."""
    address = endorsement_address(ref, item.contribution_id, item.fingerprint)
    return any(record.principal == item.principal and record.trusted is True
               and record.permission is Permission.PROPOSE and record.resource == address
               for record in reversed(runner.authority.audit()))


def _record_endorsement(runner, handle, ref: TurnRef, contribution_id: str, fingerprint: str) -> None:
    """Have the authority record that ``handle`` (the endorsing player's own) gave this key. The decision's outcome
    does not matter (a player holds no grant there): its audit record, which only that trusted handle could make,
    is what a later presentation checks. Written to the ledger at the latest with the turn write that follows."""
    item = EndorsementRecord(contribution_id, handle.principal, fingerprint)
    handle.authorize(Permission.PROPOSE, endorsement_address(ref, contribution_id, fingerprint),
                     fingerprint.encode("utf-8"))
    if not _recorded(runner, ref, item):
        raise _refuse("authority_unavailable", "the authority could not record this endorsement; give it again")


def _endorsements(runner, record: TurnRecord, effect: TurnEffect, live: Mapping[str, object]
                  ) -> list[Endorsement]:
    """021's endorsements for ``effect``'s recorded ones: the endorser's own live handle when they are the one
    asking now, else a handle for the recorded principal, minted only when the authority's audit records that
    player's own trusted handle giving that key for that turn and fingerprint (TOOL_AUTHORITY's 035 exception):
    a key given before a restart still counts after it, and one written into the turn file never does."""
    found = []
    for item in effect.endorsements:
        handle = live.get(item.principal)
        if handle is None:
            if not _recorded(runner, record.ref, item):
                raise _refuse("invalid_endorsement", "the authority has no record of an endorsement on this turn: "
                                                     "the player endorses again, or is present to recover it")
            handle = runner.authority.handle_for(item.principal, trusted=True)
        found.append(Endorsement(item.contribution_id, handle, item.fingerprint))
    return found


# -- accepting ------------------------------------------------------------------------------------

def blockers(runner, chat: ChatRef) -> tuple[list[str], int]:
    """What must be recovered before the campaign accepts another effect (030): this chat's ``accepting`` effects
    (``<turn id>:<event id>``), and how many others the campaign holds, each an ``accepting`` effect of another
    chat or a turn file that cannot be read (it could hide one), named by count only: a player is never told
    another chat's turn ids. A 021 receipt mismatch blocks nothing here: an ``accepting`` effect already names
    every acceptance this runner left partly published, and 021 accepts unrelated effects over any other.

    An ``accepting`` effect another device started (a turn sync brought) blocks nothing here either, nor does one
    of unknown device (``_whose``): nothing of it is in this library's 021 (sync holds 021 back), so no acceptance
    here can meet it. A damaged device identity refuses (``device_identity``): whose each one is cannot be told."""
    mine = runner.scenes.device()
    here, elsewhere = [], 0
    read: list = []

    def shape(effect: TurnEffect) -> str:  # 021 is read at most once, and only for an effect not tagged as mine
        if not read:
            read.append(_continuity(runner, chat.campaign_id))
        return shape_of(read[0], effect.event_id)
    listed = runner.store.campaign_turns(chat.campaign_id)
    elsewhere += len(listed.problems)
    for ref, stored in listed.records.items():
        try:
            record = turn_from_payload(ref, stored.record.schema, stored.record.payload)
        except SceneRefused:
            elsewhere += 1
            continue
        waiting = [f"{record.turn_id}:{effect.event_id}" for effect in record.effects
                   if effect.state == "accepting" and _whose(effect, mine, lambda: shape(effect)) == "here"]
        if record.chat == chat:
            here += waiting
        else:
            elsewhere += len(waiting)
    return sorted(here), elsewhere


def _whose(effect: TurnEffect, mine: str | None, shape: Callable[[], str]) -> str:
    """Whose acceptance an effect with an event is, on this device (tag ``mine``; ``None`` with no identity):
    ``here`` (this library settles it), ``elsewhere`` (another device's: nothing of it is here) or ``untagged``
    (no device is named, the library has one, and nothing of it is here: an old record of this library's, or
    an acceptance from a device without identity, so the owner says which, ``recover_effects(own=True)``).

    It is this library's when tagged with this device, and whatever its tag whenever this library's 021 holds
    any trace of its event (``shape``, read only when needed, is not ``unpublished``): an identity replaced since
    (a copied library's fix) or lost never strands what landed here. 021's event ids are random, so another
    device's event never shares an id with one of this library's."""
    if effect.accepted_on == mine or shape() != "unpublished":
        return "here"
    return "elsewhere" if effect.accepted_on is not None else "untagged"


def _shape(runner, effect: TurnEffect) -> str:
    return shape_of(_continuity(runner, effect.proposal.campaign_id), effect.event_id)


def _continuity(runner, campaign_id: str):
    try:
        return runner.effects.continuity(campaign_id, as_of=NOW, requested_by=DIRECTOR)
    except InvalidEffect as error:
        raise _refuse(error.reason, "the accepted effects cannot be read") from None
    except StoreError as error:
        raise _refuse("effects_unavailable", type(error).__name__) from None


def _accept(runner, scene, stored: StoredTurn, effect: TurnEffect, live: Mapping[str, object]) -> TurnEffect:
    """Accept ``effect``, which holds every key, in the fixed order (module docstring).

    The whole order runs under the store lock, so in this process no other acceptance passes the gate, and no
    recovery reads the effect as unpublished, while this one is between its ``accepting`` write and its commit.
    """
    with runner.store.lock:
        return _accepting(runner, scene, stored, effect, live)


def _accepting(runner, scene, stored: StoredTurn, effect: TurnEffect, live: Mapping[str, object]) -> TurnEffect:
    library, chat, record = runner.effects, scene.chat, stored.record
    device = runner.scenes.device()  # a damaged identity starts nothing (as new turns): only it can settle this
    here, elsewhere = blockers(runner, chat)
    if here or elsewhere:
        named = ([f"this chat's {', '.join(here)}"] if here else []) + (
            [f"{elsewhere} elsewhere in the campaign (another chat's acceptance, or a turn file that cannot be "
             "read)"] if elsewhere else [])
        raise _refuse("recovery_needed", ("recover first: " + "; ".join(named))[:200])
    if runner.adjudication is None:
        raise _refuse("no_adjudication", "every acceptance is checked by 029 first")
    proposal = effect.proposal
    try:
        _029(runner.adjudication.check_effect, proposal)
    except SceneRefused as error:
        if error.reason in _UNAVAILABLE:
            raise
        return _write(runner, stored, replace(effect, state="refused", reason=error.reason))
    try:
        change = library.accept(proposal, controls=scene.controls, contributions=contributions_of(record),
                                endorsements=_endorsements(runner, record, effect, live))
    except InvalidEffect as error:
        return _write(runner, stored, replace(effect, state="refused", reason=error.reason))
    except AuthorityError:
        return _write(runner, stored, replace(effect, state="refused", reason="ambiguous_control"))
    except StoreError as error:
        raise _refuse("effects_unavailable", type(error).__name__) from None
    event_id = change.ref.event_id
    controls_at = _settled_play(runner, scene)
    if change.duplicate_of is not None:  # already in force (a retry): nothing to publish, only to record
        _commit(change, effect)
        landed = effect
    else:
        landed = replace(effect, state="accepting", event_id=event_id, controls_at=controls_at, accepted_on=device)
        # compare-and-swap: a turn changed since it was read (another key, a relabel) commits nothing, and so
        # does a pause anywhere in the chat, a sync conflict, a fork arriving, or a choice that set the turn aside
        runner._write(record.with_effect(landed), stored, unpaused=True)
        try:
            _commit(change, effect)
        except SceneRefused as error:
            if error.reason == "effects_changed":  # nothing was written: the keys wait for another try
                _rewrite(runner, chat, record.ref, landed, replace(effect, state="approved", event_id=None))
            raise
    accepted = replace(landed, state="accepted", event_id=event_id)
    if _rewrite(runner, chat, record.ref, landed, accepted) is None:
        raise _refuse("effect_unsettled", "the effect is accepted, but its turn could not be marked; recover it")
    return accepted


def _settled_play(runner, scene) -> int:
    """Under the store lock, before anything is written: play is not paused (a pause may land on a later turn
    than the effect's, which the compare-and-swap would not see) and control is what the call read; the
    sovereignty ledger position of that control (the seq of its latest control change, 0 for none)."""
    if runner.scenes.turns(scene.chat).paused:  # any pause of the chat, a set-aside turn's or a kept version's
        raise _refuse("turn_changed", "play was paused meanwhile; resume, then give the key again")
    state = runner.sovereignty.load(scene.chat.campaign_id)
    if set(state.controls) != set(scene.controls):
        raise _refuse("control_changed", "who plays a character changed meanwhile; give the key again")
    return max((event.seq for event in state.control_history), default=0)


def _controls_at(runner, campaign_id: str, position: int) -> tuple[ControlAssignment, ...]:
    """The campaign's control as the sovereignty ledger stood at ``position`` (replayed from its history)."""
    controllers: dict[str, str] = {}
    for event in runner.sovereignty.load(campaign_id).control_history:
        if event.seq > position:
            break
        if event.controller_id is None:
            controllers.pop(event.actor_id, None)
        else:
            controllers[event.actor_id] = event.controller_id
    return tuple(ControlAssignment(campaign_id, actor, controller) for actor, controller in sorted(controllers.items()))


def _commit(change, effect: TurnEffect) -> None:
    try:
        change.commit()
    except StaleWrite:  # the library changed since the preview (021 re-checks everything): nothing written
        raise _refuse("effects_changed", "the accepted effects or relations changed meanwhile; give the key "
                                         "again") from None
    except (StoreError, OSError) as error:  # possibly published in part: open-time recovery reads what landed
        raise _refuse("effect_unsettled",
                      f"the acceptance was cut short ({type(error).__name__}); recover it") from None


def _write(runner, stored: StoredTurn, effect: TurnEffect) -> TurnEffect:
    runner._write(stored.record.with_effect(effect), stored)
    return effect


def _rewrite(runner, chat: ChatRef, ref: TurnRef, expected: TurnEffect, new: TurnEffect) -> TurnEffect | None:
    """Write ``new`` over the turn's effect while it is still ``expected`` (the same proposal, in ``expected``'s
    state, or ``approved`` again after a recovery found it unpublished mid-commit); ``None`` when it moved on.

    It records what 021 now holds (or that nothing was written), over the runner's own ``accepting`` mark, so it
    lands as a finished take does (``landing``): over a pause, a fork, or a choice that set the turn aside
    (it is found among the set-aside turns too). Only the turn file itself must be as read; a sync conflict is
    checked by the caller under the same store lock."""
    allowed = {expected.state, "approved"} if expected.state == "accepting" else {expected.state}
    for _ in range(WRITE_RETRIES):
        turns = runner.scenes.turns(chat)
        stored = next((each for each in (*turns.turns, *turns.set_aside) if each.record.ref == ref), None)
        current = None if stored is None else stored.record.effect(new.number)
        if current is None or current.proposal != new.proposal or current.state not in allowed or (
                current.event_id not in (expected.event_id, None)):
            return None
        try:
            runner._write(stored.record.with_effect(replace(new, approved_by=new.approved_by
                                                                  or current.approved_by)), stored, landing=True)
            return new
        except SceneRefused as error:
            if error.reason != "turn_changed":
                raise
    return None


# -- open-time recovery ---------------------------------------------------------------------------

def shape_of(continuity, event_id: str) -> str:
    """What landed of an ``accepting`` effect's acceptance, from 021's continuity (030's shapes):

    ``landed`` (the event is in force and its receipts verify), ``complete`` (the event landed without some
    of its changes: shape A), ``revoked`` (the event was revoked since), ``adopt`` (changes landed without the
    event file: shape B), ``unpublished`` (nothing landed), or ``unreadable`` (a file that cannot be read may
    hold part of it)."""
    events = {event.event_id: event for event in (*continuity.events, *continuity.revoked)}
    codes = {problem.code for problem in continuity.problems if problem.event_id in (event_id, None)}
    graph = continuity.graph
    # checked first: a file that cannot be read may hold part of it, even when its event file landed (a holder
    # file 021 reads as undecodable_holdings, never as a receipt mismatch), so no settled shape is told over it
    if codes & {"undecodable_event", "undecodable_holdings"} or graph.problems or any(
            problem.code == "undecodable" for problem in graph.index.problems):
        return "unreadable"
    event = events.get(event_id)
    if event is not None:
        if not event.active:
            return "revoked"
        mine = {problem.code for problem in continuity.problems if problem.event_id == event_id}
        return "complete" if "receipt_mismatch" in mine else "landed"
    orphaned = any(problem.code == "orphan_effect" and problem.event_id == event_id
                   for problem in continuity.problems)
    return "adopt" if orphaned else "unpublished"


def effect_problems(runner, scene) -> list[tuple[str, str]]:
    """``open``'s report: every 021 problem of the campaign (``effects``, ``<code>:<event id>``), and each of this
    chat's ``accepting`` effects as ``effect_unsettled`` (``<turn id>:<event id>:<shape>``), or, by ``_whose``,
    one another device started as ``effect_elsewhere`` (``<turn id>:<event id>:<device tag>``: that device
    settles it) and one of unknown device as ``effect_untagged`` (``<turn id>:<event id>``: the owner says).
    With a damaged device identity every one reads as ``effect_unsettled`` (recovery then refuses)."""
    if runner.effects is None:
        return []
    try:
        mine, told = runner.scenes.device(), True
    except SceneRefused:  # whose each acceptance is cannot be told
        mine, told = None, False
    try:
        continuity = _continuity(runner, scene.chat.campaign_id)
    except SceneRefused as error:
        return [("effects", f"unreadable:{error.reason}")]
    found = [("effects", f"{problem.code}:{problem.event_id or ''}") for problem in continuity.problems]
    for stored in (*scene.turns.turns, *scene.turns.set_aside):  # one set aside since it was cut short too
        for effect in stored.record.effects:
            if effect.state != "accepting":
                continue
            named, shape = f"{stored.record.turn_id}:{effect.event_id}", shape_of(continuity, effect.event_id)
            whose = _whose(effect, mine, lambda: shape) if told else "here"
            found.append(("effect_elsewhere", f"{named}:{effect.accepted_on}") if whose == "elsewhere" else
                         ("effect_untagged", named) if whose == "untagged" else
                         ("effect_unsettled", f"{named}:{shape}"))
    return list(dict.fromkeys(found))


def recover_effects(runner, chat: ChatRef, by, *, abort: bool = False, endorsers: Iterable[object] = (),
                    own: bool = False) -> tuple[TurnEffect, ...]:
    """See ``SceneRunner.recover_effects``."""
    _library(runner)
    scene = runner._context(chat).scene
    principal = runner._principal(scene, by)
    if principal not in runner.authority.owners:
        raise _refuse("not_director", "the director (the library owner) recovers an acceptance")
    _recoverable(scene.turns)
    if type(abort) is not bool or type(own) is not bool:
        raise _refuse("invalid_field", "abort and own are true or false")
    live = _live(runner, endorsers)
    mine = runner.scenes.device()  # a damaged identity settles nothing: whose each acceptance is cannot be told
    settled, theirs, untagged = [], [], []
    with runner.store.lock:  # never while an acceptance of this process is between its steps
        turns = runner.scenes.turns(chat)
        # oldest first, set-aside turns after the line; the first that cannot be settled stops
        for stored in (*turns.turns, *turns.set_aside):
            for effect in stored.record.effects:
                if effect.state != "accepting":
                    continue
                whose = _whose(effect, mine, lambda: _shape(runner, effect))
                named = f"{stored.record.turn_id}:{effect.event_id}"
                if whose == "elsewhere":  # its 021 files are on that device: read here, it looks unpublished
                    theirs.append(f"device {effect.accepted_on} ({named})")
                    continue
                if whose == "untagged" and not own:  # never put back or dropped on a guess
                    untagged.append(named)
                    continue
                _recoverable(runner.scenes.turns(chat))  # again under the lock, before each 021 write
                settled.append(_recover(runner, scene, stored.record, effect, abort, live))
    # this device's own were settled above; the rest never are here (untagged ones, not without the owner's word)
    if untagged:
        raise _refuse("untagged_acceptance", f"{len(settled)} settled here; nothing of {_listed(untagged)} is in this "
                                             "library and no device is named: own=True if it is this library's")
    if theirs:
        raise _refuse("accepted_elsewhere", f"{len(settled)} settled here; settle on the device that accepted it: "
                                            f"{_listed(theirs)}")
    return tuple(settled)


def _listed(items: list[str]) -> str:
    """At most two items, then how many more (a refusal's message stays short)."""
    return ", ".join(items[:2]) + (f" and {len(items) - 2} more" if len(items) > 2 else "")


def _recoverable(turns) -> None:
    """Recovery settles what an acceptance already committed, so it goes on while play waits: over a pause, at a
    fork the owner has not chosen yet (it marks only the effect's own turn, whichever branch it is on), and on a
    turn a choice set aside. It still needs every turn file of the chat readable (one could hold an unsettled
    effect), and waits while a turn of the chat is in a sync conflict: each device then reads its own version of
    it, so settling one version could be undone by the ``resolve``."""
    if not turns.complete and set(turns.problems) - {(str(turns.chat), "turn_fork")}:
        raise _refuse("turns_unreadable", "a turn file of this chat cannot be read; nothing is written")
    if turns.conflicted:
        raise _refuse("turn_conflict", "a turn of this chat is in a sync conflict; resolve it, then recover")


def _live(runner, handles: Iterable[object]) -> dict[str, object]:
    """The endorsers present now, by principal: each a trusted human handle this library's authority issued."""
    found = {}
    for handle in tuple(handles):
        principal = getattr(handle, "principal", None)
        if not (runner.authority.issued(handle) and getattr(handle, "trusted", False) is True
                and isinstance(principal, str) and is_human(principal)):
            raise _refuse("bad_handle", "an endorser is present with their own trusted handle")
        found[principal] = handle
    return found


def _recover(runner, scene, record: TurnRecord, effect: TurnEffect, abort: bool, live: Mapping[str, object]
             ) -> TurnEffect:
    """Settle one ``accepting`` effect by the shape of what landed, exactly once; the effect as now recorded."""
    library, chat = runner.effects, scene.chat
    ref = AcceptedRef(chat.campaign_id, effect.event_id)
    shape = shape_of(_continuity(runner, chat.campaign_id), effect.event_id)
    if shape == "unreadable":
        raise _refuse("effects_unreadable", f"what landed of {effect.event_id} cannot be read; repair the file")
    if shape == "unpublished":  # nothing landed: the keys wait for another try, or the effect is dropped
        put_back = dict(event_id=None, controls_at=None, accepted_on=None)
        settled = replace(effect, state="refused", reason="aborted", **put_back) if abort \
            else replace(effect, state="approved", **put_back)
    elif shape == "revoked":
        settled = replace(effect, state="revoked")
    else:
        if abort:  # every original endorser consents to the undo, now, with their own handle
            missing = sorted({item.principal for item in effect.endorsements} - set(live))
            if missing:
                raise _refuse("not_endorsed", "aborting needs every endorser of this effect present")
        if shape == "complete":  # shape A: finish it (or, aborting, undo what landed)
            if not abort:
                _, etag = _021(library.read, ref, requested_by=DIRECTOR)
                _021(lambda: runner.recovery.complete(ref, expected=etag).commit())
        elif shape == "adopt":  # shape B: the event file under the landed changes' id, then (aborting) undo it
            # checked like the acceptance it retries: under the control it was authorized under, not today's
            controls = scene.controls if effect.controls_at is None else _controls_at(
                runner, chat.campaign_id, effect.controls_at)
            # an endorser present now with their own trusted handle stands in for a missing audit record (a
            # ledger restored from before the endorsement, a device the turn reached but authority/ never does)
            endorsements = _endorsements(runner, record, effect, live)
            _021(lambda: runner.recovery.adopt(effect.proposal, controls=controls,
                                               contributions=contributions_of(record),
                                               endorsements=endorsements).commit())
        if abort:
            event, etag = _021(library.read, ref, requested_by=DIRECTOR)
            if {item.principal for item in event.endorsements} - set(live):  # as 021 recorded them (adopt)
                raise _refuse("not_endorsed", "aborting needs every endorser of this effect present")
            undo = [Endorsement(item.contribution_id, live[item.principal],
                                revocation_fingerprint(ref, event.fingerprint)) for item in event.endorsements]
            _021(lambda: library.revoke(ref, reason=ABORTED, expected=etag, endorsements=undo,
                                        partial=True).commit())
            settled = replace(effect, state="revoked", reason="aborted")
        else:
            settled = replace(effect, state="accepted")
    if _rewrite(runner, chat, record.ref, effect, settled) is None:
        raise _refuse("turn_changed", "the turn changed while its effect was recovered; recover again")
    return settled


def _021(call: Callable, *args, **kwargs):
    """Run one 021 or 030 call; a refusal or a changed library comes back as a ``SceneRefused``."""
    try:
        return call(*args, **kwargs)
    except InvalidEffect as error:
        raise _refuse(error.reason, str(error)[:200]) from None
    except StaleWrite:
        raise _refuse("effects_changed", "the accepted effects or relations changed meanwhile; recover again") from None
    except AuthorityError:
        raise _refuse("ambiguous_control", "who controls an actor of this effect is unclear") from None
    except StoreError as error:
        raise _refuse("effects_unavailable", type(error).__name__) from None


__all__ = ["ABORTED", "SHAPES", "approve_effect", "blockers", "contributions_of", "effect_problems",
           "endorse_effect", "endorsement_address", "next_sequence", "propose_effect", "recover_effects", "shape_of"]
