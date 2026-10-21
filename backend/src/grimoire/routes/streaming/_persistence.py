"""streaming: persistence responsibilities."""

from __future__ import annotations

import logging
import time
import uuid

from ... import store

_log = logging.getLogger('grimoire.routes.streaming')


def _turn_settled(cid: str) -> None:
    """Record that a detached turn reached its terminal write (#409).

    Every one of them needs this and the reason is always the same: the
    middleware stamped this campaign's revision when the stream's status line
    went out, which is BEFORE the model ran, and everything a turn persists
    lands after that. A token read while the model was generating would
    otherwise still be current once the turn had finished writing, and a clock
    advance or a checkpoint priced against it would proceed as though nothing
    had happened.

    Placed at the terminal points rather than beside each write, because what a
    turn persists is not only posts: a roll fence that closes with no narration
    at all still writes a proposal record, a continuation commits its narration
    onto that record, and a failed turn's rollback takes the player's post back
    off. Conditioning the stamp on the transcript having grown would miss every
    one of those (Codex review).

    Never raises -- `revision.bump` swallows and logs -- which is what makes it
    safe this late, after work the user already has.
    """
    store.revision.bump(cid)


def _store_phase(cid: str, segments: list[dict], connection: str) -> list[tuple]:
    """Run the store phase over a reply's segments in place, returning
    `(post_id, original, fired, stored)` for each one it rewrote.

    A segment has no `response_id` to key its record by, so one the phase
    changed gets a `post_id` minted for it here, before the append writes it.
    Only one it changed: a segment nothing rewrote keeps the plain block it
    always had, so a store with no `rewrite_stored` rule writes the transcript
    it wrote before rules existed. (The phase never empties a segment: a
    rewrite that would is not applied.)"""
    out = []
    for seg in segments:
        role = store.regex.apply.role_of({"role": "assistant", "speaker": seg["speaker"]})
        if role is None:
            continue
        stored, fired = store.regex.view.store_phase(
            seg["content"], cid=cid, role=role, connection=connection)
        if not fired:
            continue
        original, seg["content"] = seg["content"], stored
        seg["post_id"] = uuid.uuid4().hex
        out.append((seg["post_id"], original, fired, stored))
    return out


def _active_variant(cid: str, sid: str, rid: str) -> str:
    """The variant response `rid` shows now, "" when the ledger names none. Read
    inside the hold that wrote it, so it is the variant a rewrite belongs to."""
    return store.responses.variants_by_response(cid, sid).get(rid, (None, []))[0] or ""


def _carry_rewrite(cid: str, sid: str, rid: str, before: str, skip: str = "") -> None:
    """Move the records of response `rid`'s earlier parts from variant `before`
    onto the one now active, once a continuation part made that variant. The
    earlier parts are still the text their records were made for, so the
    records are still theirs -- left naming `before`, they would stop matching
    and Restore of an earlier part would be refused. `skip` is the key of the
    continuation's own record, written for the new variant already. Fail-soft,
    for `_record_rewrite`'s reason."""
    try:
        store.regex.rewrites.carry(cid, sid, rid, before, _active_variant(cid, sid, rid),
                                   skip=skip)
    except OSError:
        _log.warning("could not carry the stored rewrites of %s in %s/%s",
                     rid, cid, sid, exc_info=True)


def _prune_rewrites(cid: str, sid: str) -> None:
    """Drop the stored-rewrite records of messages the transcript no longer
    has, from inside the hold that removed them (`rewrites.prune`). The posts a
    running replay holds are still the scene's: a cancel puts them back with
    their ids. Fail-soft throughout -- the removal has landed, and a record
    left behind is pruned by the next seam."""
    try:
        held = store.replay.held(cid, sid)
    except OSError:
        _log.warning("could not read the replay backlog of %s/%s", cid, sid, exc_info=True)
        return          # pruning without it could drop what a cancel brings back
    store.regex.rewrites.prune(cid, sid, held=held)


def _record_rewrite(cid: str, sid: str, key: str, original: str, fired: list[str],
                    stored: str, variant: str = "") -> None:
    """Record what the store phase changed, from inside the hold that wrote
    `stored`. Fail-soft: the rewritten text is already in the transcript, and a
    record file that cannot be written must not turn a landed post into a
    failed one -- it costs Restore original for that message, and is logged.
    `variant` is the response variant a model reply's rewrite belongs to."""
    try:
        store.regex.rewrites.record(cid, sid, key, original=original, rules=fired,
                                    stored=stored, variant=variant)
    except OSError:
        _log.warning("could not record the stored rewrite of %s in %s/%s",
                     key, cid, sid, exc_info=True)


def _persist_reply(cid: str, sid: str, text: str, connection: str = "") -> int:
    """Split one model reply into per-speaker posts and append them (#744),
    returning how many actually landed.

    `connection` is the id of the connection that served it, recorded on every
    post so a rule owned by a connection can tell its own replies. Left empty
    by a caller that did not generate the text (a hand-adopted opener).

    The count is what a caller needs to tell "the model said something" from
    "the transcript grew". They are not the same question and the gap is not
    only the trailing state block: a reply that is nothing but a speaker marker splits
    into no non-empty segment either, and `append_reply` writes nothing for it.
    `post_first_post` is the caller that has to know — it reports success to a
    user who is adopting an opener, and reporting it over an empty scene loses
    the text with no error to show for it.

    Macros are expanded before persisting (#137): {{roll}}/{{random}} must be
    resolved once, not re-rolled on every future context build that re-reads
    this now-historical message. Goes through append_reply so the generation
    records its own turn boundary for drift measurement. The store phase runs
    on each expanded segment (`_store_phase`), and what it rewrote is recorded
    in the same hold as the append.

    A trailing legacy ``state`` block is split off FIRST, so it never reaches
    `split_reply` and so cannot become a post. Nothing records what it said any
    more, but a model still complying from a scene it can see, or from a card, is
    still likely to end a reply with one, and a block is unambiguous enough that
    stripping one nobody asked for costs nothing.

    Art handles are resolved next, for the same reason and in the same spirit:
    `[[art:...]]` is machine-readable output that must become markdown -- or
    nothing -- before `split_reply` runs, so a handle can never be split into a
    post of its own and no handle is ever written to a transcript.
    Unconditionally, again: a model still emitting handles from a scene it can
    see must not start leaking them the moment the section is switched off in
    the prompt layout. `resolve_handles` deletes what it cannot resolve, so a
    store with no described art turns every handle into nothing, which is the
    right answer there too.

    It runs BEFORE macro expansion, so a description containing `{{user}}`
    lands as alt text that expands like any other narration, and AFTER the
    state-block split, so a handle stranded inside a ``state`` block is not
    resolved into markdown that nobody will ever render.
    """
    text, _ = store.state_fence.split_block(text)
    text = store.context.resolve_art_handles(cid, text, sid)
    players = frozenset(store.appearances.player_names(cid, sid))
    subs = store.context.scene_substitutions(cid, sid)
    segments = [{"speaker": seg["speaker"],
                 "content": store.context.expand_macros(seg["content"], subs, cid, sid),
                 **({"connection": connection} if connection else {})}
                for seg in store.scenes.split_reply(text, players)]
    rewritten = _store_phase(cid, segments, connection)
    # One lock over both: a reply landing in a slot a reroll emptied is a
    # variant that exists only in the transcript until `reconcile` writes it
    # down, and an edit arriving in between would destroy it. Reentrant, so the
    # acquisitions inside each call cost nothing. `reconcile` writes nothing for
    # a reply that lands anywhere else, which is every ordinary turn.
    with store.locks.campaign_lock(cid):
        store.scenes.append_reply(cid, sid, segments)
        try:
            store.alternates.reconcile(cid, sid)
        except OSError:
            # The reply is already in the transcript. A sidecar that cannot be
            # written (a full disk, a read-only store) must not turn a landed
            # generation into a failed one: the exception would escape the
            # stream finalizer before its `done` frame, so the client would show
            # a failure over a reply that is on disk and offer a retry that
            # appends a *second* generation. Same judgement `_read_raw` already
            # makes on the way in — the sidecar is a convenience beside the
            # transcript, never a reason to lose or misreport one. The cost is
            # the round-eleven durability window staying open for this turn.
            pass
        for post_id, original, fired, stored in rewritten:
            _record_rewrite(cid, sid, post_id, original, fired, stored)
        # A reply that lands may be replacing posts: a reroll's, or a roll
        # continuation's earlier attempt that `commit_narration` trimmed.
        _prune_rewrites(cid, sid)
        # Counted the way `append_reply` filters, because that is what it wrote.
        kept = sum(1 for s in segments if s["content"].strip())
        if kept:
            # Inside the hold, so the token has moved before any other writer
            # can take the lock and read it. Only when something landed: the
            # callers that persist nothing here have written something else and
            # stamp for themselves at their own terminal point -- see
            # `_turn_settled`. This is also reached from `greetings.py`, which
            # is a plain request and has no finalizer behind it.
            _turn_settled(cid)
    return kept


def _narration(watcher) -> str:
    """What this turn actually SAID -- the reply with its state block already
    split off.

    Every "did this turn produce anything?" test goes through here, because
    `watcher.narration` answers a different question once a state block can
    exist. A reply consisting only of a block is non-empty raw and empty in the
    transcript, and the callers below use that test to decide whether to put
    back a reply that reroll deleted, whether to take a stranded user post off,
    and whether the turn is worth persisting at all. Testing the raw text there
    made a state-block-only regenerate look like a successful reply, skip the
    restore, and delete a reply nothing else held a copy of.

    Cheap and pure, so calling it beside `_persist_reply`'s own split costs a
    scan of one reply and keeps the grammar in one place.
    """
    return store.state_fence.split_block(watcher.narration)[0]


def _would_land(cid: str, sid: str, text: str) -> int:
    """How many posts `_persist_reply` would keep from `text`, without writing.

    The authoritative answer is `_persist_reply`'s own return, and the callers
    that can afford to persist first use that. Two cannot: `on_abort` chooses
    between restoring and finalizing, and finalizing is what persists;
    `_continuation_stream` has to decide before `commit_narration`, which runs
    the write inside its own lock and marks the record `narrated` on the way
    out. Both need the count in advance, so this predicts it the same way —
    state block off, then the marker grammar, then non-empty content.

    Deliberately skips macro expansion, which `_persist_reply` does and which
    can only ever shrink a segment. That makes this conservative in the safe
    direction: it can say "something lands" when nothing does, never the
    reverse, and the caller's fallback for that is the path it would have taken
    anyway.
    """
    narration, _ = store.state_fence.split_block(text)
    players = frozenset(store.appearances.player_names(cid, sid))
    return sum(1 for seg in store.scenes.split_reply(narration, players)
               if seg["content"].strip())


def _tail_length(cid: str, sid: str) -> int | None:
    """How many posts the transcript holds right now, or None if it cannot be read.

    Called under the campaign-lock hold a terminal write took, immediately
    after it, so what comes back is the boundary that write produced rather
    than a length something else has since moved on. What it is FOR is
    `StreamOutcome.persisted`, whose docstring carries that reasoning.

    **It may not raise**, and that is why it is a function rather than a
    `len(read_scene(...))` at each call site. It runs inside `finalize`, after
    the reply is already on disk, and `_fence_stream` converts only `StoreBusy`
    there -- so a scene momentarily unreadable (a sync client mid-replace, a
    Windows sharing violation) would escape to the runner and mark a landed
    turn `failed`, over a reply that is perfectly persisted, with a retry then
    free to append a second one. Losing the follow-up is the right cost of
    that: the next turn asks again with a boundary of its own, where a false
    failure is a duplicated reply nobody asked for.
    """
    try:
        return len(store.scenes.read_scene(cid, sid)["messages"])
    except Exception:                                        # noqa: BLE001
        _log.warning("could not read the follow-up boundary for %s/%s",
                     cid, sid, exc_info=True)
        return None


def _scene_moved(cid: str, sid: str, identity: str | None) -> bool:
    """Whether `sid` no longer names the scene this turn started on.

    Scene ids are recycled -- `serialize._numbering` derives the next number
    from the files on disk with no stored counter -- so a turn held open while
    its scene is deleted and a same-titled replacement is created finds a
    perfectly valid path at exactly the id it captured, and appends its reply to
    somebody else's scene. `_owns_turn` cannot catch it: the claim is keyed by
    `sid` too, and the replacement has not claimed anything.

    `None` means the caller asked for no fence and this is always False --
    deliberately explicit, because comparing two `None`s always matches and
    that is the failure this exists to prevent. `reserve_turn` mints an identity
    rather than reading one for the same reason.

    MUST be called under the campaign lock, next to the write it guards: read
    outside one, it answers a question that was true a moment ago.

    STRICT, and this is the difference between a fence and a data-loss bug. The
    tolerant reader answers `None` for a header it could not open, which is
    indistinguishable here from a scene that was replaced -- so a sync client
    holding the file for the moment generation finalizes would make a complete,
    correct reply look like it belonged to a scene that no longer exists, and
    the fence would throw it away and mark the run failed with the original
    identity still sitting on disk. An unreadable fence is a store failure and
    is raised as one; the caller reports it as retryable contention, which is
    what it is.
    """
    if identity is None:
        return False
    for attempt in range(_FENCE_READS):
        try:
            return store.scenes.scene_identity_strict(cid, sid) != identity
        except OSError as exc:
            if attempt == _FENCE_READS - 1:
                # Raised as contention, not invented as an answer. `StoreBusy`
                # is the kind every caller here already handles -- the error
                # path swallows it, the abort path swallows everything, and
                # finalize turns it into the `busy` frame -- so an unverifiable
                # fence ends the turn the same way a contended store does, and
                # nothing is written to a scene we could not identify.
                raise store.locks.StoreBusy(
                    f"could not read the scene's identity: {exc}") from exc
            time.sleep(_FENCE_RETRY_SECONDS)
    raise AssertionError("unreachable")           # pragma: no cover


_MOVED = ("the scene this turn started on is gone -- its id now names a "
          "different scene, so the reply was not saved")

_FENCE_READS = 3
_FENCE_RETRY_SECONDS = 0.05
"""How hard to try to READ the fence before giving up on the turn.

The conditions that make a header momentarily unopenable -- a sync client
mid-write, a Windows sharing violation -- last milliseconds, and this runs in a
worker thread where a short sleep costs nothing. Retrying is what keeps a
transient blip from ending a turn the provider spent minutes on; giving up
after a few is what keeps a genuinely stuck file from holding the campaign
lock indefinitely.
"""


#: The one `_chat_stream` task whose turn answers no player post -- an
#: ephemeral director note, which is never written to the transcript.
DIRECTOR = "director"


def _continuation_post(cid: str, sid: str, pid: str) -> int | None:
    """Which player post a continuation of proposal `pid` is answering.

    Read off the proposal record rather than re-derived from the transcript,
    and the difference is not academic: a continuation is the second half of a
    turn that already decided this question, and asking it again from here gets
    a DIFFERENT answer for the case the first half got right. A director turn
    is recorded against no post -- its note is ephemeral and never reaches the
    transcript -- but by the time its roll is accepted the last user message is
    some earlier, visible post, which a fresh derivation would charge for
    narration the player never asked for.

    A record that names no post therefore answers None, and so does a record
    that is gone or belongs to a different proposal. That is the honest
    fallback: the cost stays in the scene's totals either way, and a missing
    attribution is a smaller error than a wrong one.
    """
    try:
        rec = store.proposals.get(cid, sid)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("id") != pid:
        return None
    post = rec.get("post")
    if isinstance(post, bool) or not isinstance(post, int) or post < 0:
        return None
    return post


def _answering_post(messages: list[dict]) -> int | None:
    """The index of the thing a turn generated against this transcript is
    answering, or None when there is not one.

    The last message the PLAYER put there, which is what every producing route
    is replying to: a send appends it and streams, a retry and a regenerate
    re-answer the one already sitting there, and a replay re-runs the turn that
    followed it. A scene whose transcript is all narration -- an opener, a
    director-driven scene before the player has said anything -- has no such
    message, and gets no attribution rather than a wrong one.

    A director note counts, and that is the half worth reading twice. It is
    assistant-role by the transcript format's construction (the role is derived
    from the label and `You` is the only label that means user), so a plain
    user-role scan would step straight over it to some earlier, visible post --
    and a retry or a reroll of a director turn would then be charged to a post
    the player made ten turns ago. The question this answers is "what did the
    player put there that this turn is about", and a note is one of the two
    ways they can put something there.

    Reads the message list defensively because it is the one thing here that
    came off disk: a `messages` that is not a list of dicts must cost the
    attribution, never the turn it is being attributed to.
    """
    if not isinstance(messages, list):
        return None
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, dict):
            continue
        if message.get("role") == "user" or store.scenes.is_director_note(message):
            return index
    return None


def _is_director_note(messages: list[dict], index: int | None) -> bool:
    """Whether `index` names a director note in `messages`.

    Reads defensively for `_answering_post`'s reason: the list came off disk,
    and a malformed row must cost the attribution rather than the turn.
    """
    if index is None or not isinstance(messages, list) or not 0 <= index < len(messages):
        return False
    message = messages[index]
    return isinstance(message, dict) and store.scenes.is_director_note(message)


# ---- roll-proposal derivation and projection ----
# Used by the turn strategies above and by the roll-proposal routes in
# `mechanics`, which is why they live here rather than beside those routes.
def _make_proposal(cid: str, sid: str, watcher) -> dict:
    """Build the proposal payload from a closed/truncated fence: parse the
    body, resolve the actor against the scene's available checks (exact
    kind:id, then case-insensitive label), and collect `problems`. A proposal
    is never silently dropped — a bad one just opens the chip in Modify."""
    fields, problems = store.fence.parse_roll_body(watcher.body or "")
    if watcher.truncated:
        problems = [*problems, "roll fence truncated"]

    actors = store.checks.available_checks(cid, sid)
    available = {a["ref"]: a["checks"] for a in actors}

    actor, actor_label = _resolve_proposal_actor(actors, fields.get("actor"))
    if actor is None:
        problems = [*problems, "actor could not be resolved"]

    check_labels = _module_check_labels(cid)
    check = fields.get("check")
    problems = [*problems, *_check_problems(fields, check_labels, available, actor)]

    return {"check": check, "check_label": check_labels.get(check, check),
            "actor": actor, "actor_label": actor_label,
            "difficulty": fields.get("difficulty"), "modifier": fields.get("modifier", 0),
            "reason": fields.get("reason", ""), "available": available,
            "problems": problems}


def _resolve_proposal_actor(actors: list[dict], actor_raw) -> tuple:
    """`(ref, label)` of the actor a fence names: an exact kind:id first, then
    a case-insensitive label. Unresolved, `(None, actor_raw)`."""
    if not actor_raw:
        return None, actor_raw
    for a in actors:
        if a["ref"] == actor_raw:
            return a["ref"], a["label"]
    for a in actors:
        if a["label"].lower() == str(actor_raw).strip().lower():
            return a["ref"], a["label"]
    return None, actor_raw


def _module_check_labels(cid: str) -> dict[str, str]:
    """Check id -> label for the campaign's bound module; empty with none."""
    mid = store.modules.resolve(cid)
    if mid is None:
        return {}
    pack = store.modules.load_pack(mid)
    cd = pack["checks"] if isinstance(pack["checks"], dict) else {}
    return {k: (v.get("label", k) if isinstance(v, dict) else k)
            for k, v in cd.items() if k != "_defaults"}


def _check_problems(fields: dict, check_labels: dict[str, str], available: dict,
                    actor: str | None) -> list[str]:
    """What is wrong with the fence's check id, if anything."""
    check = fields.get("check")
    if check is not None:
        if check not in check_labels:
            return ["unknown check id"]
        if actor is not None and check not in dict(available.get(actor, [])):
            return ["check unavailable to this actor"]
        return []
    if fields:
        # fields is non-empty (e.g. valid JSON with an actor but no `check`
        # key) yet carries no check id — parse_roll_body's own tolerant path
        # already flags this same case (see fence.py); a wholly unparseable
        # body (fields == {}) is left alone here since "roll request was
        # unparseable" already covers it and a second problem would be noise.
        return ["roll request had no check id"]
    return []
