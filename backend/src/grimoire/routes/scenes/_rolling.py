"""A rolling summary folds one intact transcript prefix while coalescing duplicate paid work."""

from __future__ import annotations

import asyncio
import contextlib
import threading

from fastapi import Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from ... import inference as operations
from ... import store
from ...llm import LLMClient
from ...llm_errors import LLMError
from ...store import campaign_settings
from .. import runs
from ..common import UsableInference, _llm_http_error, _require_scene, get_llm, require_inference
from ._router import router as router

# ---- the live rolling summary (#85) ----
# A reading aid for the scene being PLAYED, where every other summary grimoire
# holds describes a scene that has ended. Display-only: it is deliberately not
# injected into the scene context (see the design spec), so nothing here can
# change what the model is told on the next turn.

# Scenes with a refresh already at the provider. The stored coverage does not
# advance until a call RETURNS, so without this every turn landing during a slow
# completion passes the same due check and starts its own call: one threshold
# crossing, billed several times. `_rolling_commit`'s supersession check stops
# the duplicate WRITE, but only after both calls have been paid for, and paying
# is the part that matters here.
#
# In-process only, which is the same scope `streaming._turn_tokens` documents
# for the same reason: this distinguishes two refreshes racing inside one
# backend, which is what one player generates. Two *processes* on one scene is
# the case `store/locks.py` already calls beyond what this app can serialize.
#
# A real lock rather than `_claim_turn`'s bare-dict-under-the-GIL idiom, because
# this is a check-then-add and that is two bytecodes: `next()` and one assignment
# are individually atomic, `key not in s` followed by `s.add(key)` is not.
_rolling_inflight: set[tuple[str, str]] = set()
_rolling_inflight_guard = threading.Lock()
# How a forced refresh waits out a fold already at the provider (`_rolling_wait`).
# Polled rather than signalled: the claim is a plain set behind a threading lock,
# shared with whatever thread a sync route runs on, and a poll keeps it that way
# instead of introducing a per-scene asyncio primitive bound to one loop.
_ROLLING_WAIT_POLL = 0.05
# How long a forced refresh may be held open waiting, in wall-clock seconds.
#
# Its OWN number, deliberately not `llm_timeout`. Review caught the first
# version borrowing that one on the reasoning that a wait could then only run
# out after the call it waited for was itself entitled to give up -- which is
# false: `llm._guard` is an *idle* bound that resets after every provider frame,
# and says so ("deliberately an *idle* bound, not a total one. Callers that need
# a ceiling on total duration impose it themselves"). A reasoning-heavy
# completion can stream frames for many minutes while perfectly healthy, so
# borrowing that value would 503 a forced refresh whose fold was progressing
# fine. This is the ceiling `_guard`'s docstring is talking about, and what it
# actually measures is how long a browser request may hang -- `absorb_budget` is
# the same shape of decision for the same reason.
_ROLLING_WAIT_CEILING = 120.0


@contextlib.contextmanager
def _rolling_claim(cid: str, sid: str):
    """Yield whether this refresh may go to the provider, releasing on the way
    out however it leaves.

    The release is in a `finally` and gated on having claimed, so a failed call
    -- a provider error, a cancelled request, a raised anything -- frees the
    scene. A claim that outlived its request would wedge that scene's summary for
    the life of the process, which is strictly worse than the duplicate call this
    prevents.
    """
    key = (cid, sid)
    with _rolling_inflight_guard:
        claimed = key not in _rolling_inflight
        if claimed:
            _rolling_inflight.add(key)
    try:
        yield claimed
    finally:
        if claimed:
            with _rolling_inflight_guard:
                _rolling_inflight.discard(key)
def _rolling_due(view: dict, every: int, force: bool) -> bool:
    """Whether a refresh is worth an LLM call.

    Server-side rather than in `CampaignView`, so the policy has one home and is
    exercised by this suite: the client fires after every turn and this decides.

    `pending > 0` guards both callers. Automatic refreshes cannot reach here
    with nothing pending, but *force* can -- the panel's button on a
    already-current scene -- and folding an empty list of posts onto a summary
    would pay a provider to restate it.
    """
    if view["fresh"] <= 0:
        return False   # nothing pending is in context: an all-hidden span folds nothing
    pending = view["total"] - view["base"]
    if pending <= 0:
        return False
    return force or (every > 0 and pending >= every)


def _rolling_commit(cid: str, sid: str, summary: str, covered: int, digest: str,
                    facts_key: str) -> dict:
    """Store a fold only if the prefix it was computed FROM is still the prefix
    on disk, and report the state that results either way.

    Read-verify-write under ONE campaign hold (reentrant, so the mutator's own
    acquisition is free). The verification is the whole point, and review caught
    that "self-healing on the next refresh" was not enough for two reasons:

    - The panel's Refresh button trusts this route's answer directly, so a
      summary stored over a transcript that changed during the call would be
      presented as CURRENT until some later GET happened to notice.
    - `delete_scene` frees a scene's id and the numbering reuses it, so a scene
      deleted and remade under the same title mid-call hands this write the very
      id it holds -- attaching one scene's prose to another. On an empty
      replacement not even Refresh clears it: there is nothing pending for a
      forced refold to fold.

    Both are the same question, so one check answers them: is `messages[:covered]`
    still what it was? A recycled scene fails it (different transcript, usually
    none), an edit or reroll inside the covered prefix fails it, a trim fails it
    -- and an ordinary turn APPENDING during the call passes, which it must, or
    every busy scene would throw away the summary it just paid for. (Unless the
    append carries a covered post across a depth-bounded prompt rule that then
    changes it: the prefix's prompt view moved, see `store.rolling_intact.prefix_digest`.)
    """
    with store.locks.campaign_lock(cid):
        scene = store.scenes.read_scene(cid, sid)
        facts = store.chronicle.scene_facts(cid, sid)
        stored = store.rolling_intact.view(cid, sid, scene, facts)
        # Two independent refusals, and review found the second one after the
        # first was in place, because they fail on opposite facts.
        #
        # The prefix must be intact -- the fold describes those messages.
        intact = store.rolling_intact.prefix_digest(cid, sid, scene["messages"][:covered],
                                 len(scene["messages"])) == digest
        # ...the facts must be the facts it was given, for the same reason the
        # prefix must be the prefix. Review caught that adding facts to the
        # stored validity key did not, on its own, make them a PRECONDITION: a
        # first location assigned while the model was answering is a silent
        # write, so the message digest stays intact and prose generated without
        # that location would land stamped with the old facts key -- immediately
        # stale on the very next read, with no refold scheduled to repair it.
        facts_intact = (store.rolling_summary.facts_digest(facts) == facts_key)
        # ...and nothing at least as complete may already be stored. Two
        # background refreshes can overlap, and the newer one can finish first:
        # its extra posts are all APPENDED, so the older one's prefix is still
        # perfectly intact and passes the check above. Writing then regresses
        # `rolling_at` from twelve back to ten, showing the less complete prose
        # as current and pulling the next automatic refresh early. Coverage only
        # ever moves forward while the stored summary is itself valid; a stale
        # stored summary is no bar, since re-folding over it is the repair.
        superseded = bool(stored["summary"]) and not stored["stale"] \
            and stored["at"] >= covered
        landed = intact and facts_intact and not superseded
        if landed:
            store.scenes.set_rolling_summary(cid, sid, summary, covered, digest,
                                             facts_key)
            # In the hold that covers the write, and only when it landed (#409).
            # This is reached two ways and one of them has no response line
            # behind it: the panel's Refresh button is a request the activity
            # middleware stamps, but `schedule_follow_ups` fires this as a
            # BACKGROUND run after every turn (#397), minutes after that turn's
            # own status line went out. A token read while the fold was running
            # would otherwise still be current once it had written.
            store.revision.bump(cid)
            scene = store.scenes.read_scene(cid, sid)
            facts = store.chronicle.scene_facts(cid, sid)
        return {"landed": landed, "view": store.rolling_intact.view(cid, sid, scene, facts)}


def _rolling_reread(cid: str, sid: str, fallback: dict) -> dict:
    """The scene's rolling state as it is right now, for a path that is about to
    answer without writing.

    Every answer this route gives AFTER the LLM call has to be reconciled, not
    asserted -- the panel's Refresh button renders it directly. `_rolling_commit`
    does that for the write path; this is the same duty for the early return
    that had nothing to write, where the pre-call snapshot would report
    `stale: false` about a summary that went stale during the call.

    A scene that vanished falls back: there is nothing left to reconcile
    against, and this call is fire-and-forget from the client anyway.
    """
    try:
        return store.rolling_intact.view(cid, sid, _require_scene(cid, sid),
                             store.chronicle.scene_facts(cid, sid))
    except HTTPException:
        return fallback


def _rolling_body(view: dict, every: int) -> dict:
    """`due` always answers the AUTOMATIC question — would a plain per-turn POST
    spend a call — never the forced one. A forced call that found nothing new
    would otherwise report `due: false` for an unrelated reason, and the panel
    reads this field to say when the next refresh is coming."""
    return {"summary": view["summary"], "at": view["at"], "total": view["total"],
            "stale": view["stale"], "every": every,
            "due": _rolling_due(view, every, force=False)}


@router.get("/campaigns/{cid}/scenes/{sid}/rolling-summary")
def get_rolling_summary(cid: str, sid: str):
    """Read the summary without ever spending a call.

    Needs no LLM connection on purpose: the inspector reads this on every scene
    select, and a store with no key configured must still render the panel.
    """
    scene = _require_scene(cid, sid)
    return _rolling_body(store.rolling_intact.view(cid, sid, scene,
                                       store.chronicle.scene_facts(cid, sid)),
                         campaign_settings.count(cid, "rolling_summary_every"))


@router.post("/campaigns/{cid}/scenes/{sid}/rolling-summary")
async def post_rolling_summary(cid: str, sid: str, force: bool = False,
                               upto: int | None = None,
                               client: LLMClient = Depends(get_llm)):
    """Refold the summary if enough has happened since the last one.

    Fired by the client after every turn and not awaited by it, so the ordinary
    outcome is `refreshed: false` having touched no provider. `force` is the
    panel's own Refresh button -- also the only way to reach the feature at all
    when `rolling_summary_every` is 0.

    The connection check runs BEFORE the due check, deliberately: a 409 that
    only appeared once a scene happened to be due would be indistinguishable, on
    the client, from the quiet no-op that is this route's normal answer.

    `upto` bounds the fold to a transcript the caller knows was a clean
    boundary. The play loop releases the scene before firing this, so a fast
    next send can append its player post before this request takes its snapshot
    -- and a fold that swallows an unanswered prompt does not self-repair: the
    eventual reply is an APPEND, which leaves the digest valid, so it stays out
    of the "current" summary until another whole threshold goes by. The client
    already knows how long the transcript was when its turn finished, so it says
    so. Omitted (the panel's own button, which is held while a turn streams) the
    scene is taken as it is.
    """
    # A forced fold on a closed branch spends a call on a scene nobody can play
    # (play controls III). The unforced call is the automatic path and is left
    # alone: a closed scene takes no turns, so it has nothing due to fold.
    if force:
        runs.require_scene_open(cid, sid)
    # Two passes at most. The second only happens for a FORCED call that found
    # another fold already at the provider: it waits for that one, then starts
    # over from a fresh read, because everything below -- the scene, the facts,
    # the view, whether anything is still due -- may have moved while it waited.
    for attempt in (0, 1):
        answer = await _rolling_once(cid, sid, force, upto, client)
        if answer is not None:
            return answer
        # None means a FORCED call was coalesced (an automatic one answers for
        # itself inside). The player pressed a button that says "now", and
        # review caught that answering `refreshed: false` there let the panel
        # clear its busy state and report success while nothing had happened:
        # if that fold belongs to another tab, or fails and has its error
        # swallowed by the automatic caller that made it, the summary this tab
        # was promised never arrives and nothing says so.
        if attempt or not await _rolling_wait(cid, sid):
            raise HTTPException(
                status_code=503,
                detail="a refresh for this scene is already running")
    raise HTTPException(status_code=503,  # unreachable; the loop returns or raises
                        detail="a refresh for this scene is already running")


async def _rolling_wait(cid: str, sid: str) -> bool:
    """Wait for another request's fold on this scene to leave the provider.

    True if it freed, False if the wait ran out -- bounded rather than
    open-ended because this holds a request open, by a ceiling that is about
    exactly that and nothing else (see `_ROLLING_WAIT_CEILING`).

    Running out is not a claim that the fold failed, and the 503 does not say
    it did: it says one is already running, which stays true either way.
    """
    limit = _ROLLING_WAIT_CEILING
    waited = 0.0
    while waited < limit:
        with _rolling_inflight_guard:
            if (cid, sid) not in _rolling_inflight:
                return True
        await asyncio.sleep(_ROLLING_WAIT_POLL)
        waited += _ROLLING_WAIT_POLL
    return False


async def _rolling_once(cid: str, sid: str, force: bool, upto: int | None,
                        client: LLMClient) -> dict | None:
    """One evaluation of the whole route, from a fresh read of the scene.

    Returns the response, or None to mean "another fold for this scene is
    already at the provider" -- the one outcome the caller may retry, and the
    reason this is a function rather than a block: a retry has to redo the read
    and every decision that hangs off it, not just the claim.
    """
    scene = _require_scene(cid, sid)
    if upto is not None:
        if upto < 0:
            raise HTTPException(status_code=400, detail="upto must not be negative")
        # A bounded request that a NEWER fold has already overtaken is finished
        # before it starts, and this is checked against the unclamped scene
        # because clamping is what would hide it: stored coverage of 20 against a
        # transcript clamped to 10 reads as `at > total`, i.e. stale, which resets
        # `base` to zero and buys a whole-transcript refold -- one that
        # `_rolling_commit` then refuses as superseded, after the provider has
        # been paid. Review caught it there: the refusal was in the right place
        # to protect the STORE and the wrong place to protect the bill.
        ahead = store.rolling_intact.view(cid, sid, scene, store.chronicle.scene_facts(cid, sid))
        if ahead["summary"] and not ahead["stale"] and ahead["at"] >= upto:
            return {**_rolling_body(ahead, campaign_settings.count(cid, "rolling_summary_every")),
                    "refreshed": False}
        # Clamped here and nowhere else: every decision below -- due, base,
        # digest, what gets folded, what `covered` records -- then works from the
        # same bounded transcript, rather than each having to remember the bound.
        scene = {**scene, "messages": scene["messages"][:upto]}
    resolved = require_inference("rolling-summary", cid)
    every = campaign_settings.count(cid, "rolling_summary_every")
    facts = store.chronicle.scene_facts(cid, sid)
    view = store.rolling_intact.view(cid, sid, scene, facts)
    if not _rolling_due(view, every, force):
        return {**_rolling_body(view, every), "refreshed": False}

    with _rolling_claim(cid, sid) as claimed:
        if not claimed:
            # A refresh for this scene is already at the provider and will cover
            # these posts too. Claimed AFTER the due check on purpose: a POST
            # that was never going to spend anything has nothing to coalesce.
            # An automatic refresh is content to be coalesced -- the fold in
            # flight covers its posts too, and the client fires again after the
            # next transcript write. A forced one is not, and says so by
            # returning None for the caller to wait on and retry.
            if not force:
                return {**_rolling_body(view, every), "refreshed": False}
            return None
        return await _rolling_refresh(cid, sid, scene, view, every, resolved, client,
                                      facts)


async def _rolling_refresh(cid: str, sid: str, scene: dict, view: dict, every: int,
                           resolved: UsableInference, client: LLMClient,
                           facts: dict) -> dict:
    """The paid half of `post_rolling_summary`, split out so the in-flight claim
    brackets exactly the span that reaches the provider."""
    messages = scene["messages"]
    # The snapshot is what gets recorded, not a re-read afterwards: a turn
    # landing while the model is answering must not be counted as covered by a
    # summary that never saw it.
    covered, base = len(messages), view["base"]
    digest = store.rolling_intact.prefix_digest(cid, sid, messages, len(messages))
    # Depth counts over the whole snapshot, not the slice being folded.
    shown = store.regex.view.view(messages[base:], cid=cid, phase="prompt",
                                  offset=base, total=len(messages))
    prompt = store.rolling_summary.build_prompt(
        view["prior"],
        store.chronicle.transcript_text(shown, store.appearances.player_label(cid, sid)),
        facts)
    try:
        with store.usage.meter("rolling-summary", campaign=cid, scene=sid) as m:
            text = await operations.generate("rolling-summary", prompt, client=client,
                                             resolved=resolved, usage=m.usage, stream=False)
    except LLMError as exc:
        raise _llm_http_error(exc) from exc
    summary = store.rolling_summary.parse_output(text)
    if not summary:
        # A provider can return an empty completion. Storing it would blank a
        # summary the player can still read AND record it as covering the whole
        # scene, so the next refresh would fold new posts onto nothing and never
        # recover what was lost.
        #
        # Reconciled rather than answered from the pre-call snapshot: the
        # transcript may have moved under us during the call, and reporting
        # `stale: false` here would present a summary that just went stale as
        # current on a panel that renders this answer directly.
        return {**_rolling_body(_rolling_reread(cid, sid, view), every),
                "refreshed": False}
    # Off the event loop: this takes the campaign lock, whose acquisition blocks
    # for up to LOCK_TIMEOUT, and this handler is async -- inline it would freeze
    # every unrelated request and open stream on the backend rather than just
    # this refresh. Same treatment `post_absorb` and `streaming.py` give theirs.
    try:
        result = await run_in_threadpool(_rolling_commit, cid, sid, summary, covered,
                                         digest,
                                         store.rolling_summary.facts_digest(facts))
    except store.scenes.SceneNotFound:
        # The scene was renamed or deleted while the model was answering, which
        # mints a new id and moves the file out from under this write -- the
        # same race `streaming.py`'s `on_error` already catches, and reachable
        # here from the UI because the play loop fires this refresh AFTER
        # releasing the scene lock that holds rename off during a turn.
        #
        # Not an error to report: the summary describes a transcript that has
        # moved on, there is nowhere to put it, and this call is fire-and-forget
        # from the client. Left unhandled it is a 500 with no handler above it.
        return {**_rolling_body(view, every), "refreshed": False}
    # The reconciled view, never the snapshot: `_rolling_commit` re-reads under
    # its own hold, so this reports the scene as it actually is -- including the
    # posts that landed during the call, and including a refusal's `stale: true`
    # rather than the `stale: false` a just-written summary would otherwise
    # always claim. The panel's Refresh button renders this answer directly.
    return {**_rolling_body(result["view"], every), "refreshed": result["landed"]}
