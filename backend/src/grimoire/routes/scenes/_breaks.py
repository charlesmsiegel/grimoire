"""Scene-break questions are advisory decisions with a separately committed title."""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from ... import inference as operations
from ... import store
from ...llm import LLMClient
from ...llm_errors import LLMError
from ...store import campaign_settings
from .. import decision_capture, runs
from ..common import (
    UsableInference,
    _campaign_root_or_404,
    _llm_http_error,
    _require_scene,
    _soft_resolved,
    get_llm,
    require_inference,
)
from ._router import router as router

log = logging.getLogger('grimoire.routes.scenes')


# ---- scene-break detection (#84) -------------------------------------------
#
# Heuristic first, model second, player last. `store.scene_break.evaluate` scores
# what the scene already records -- posts, moves, clock advances -- and only a
# crossing buys the one call that asks whether the story actually arrived
# somewhere. Nothing here ever ends or splits a scene: the answer is a
# suggestion in the inspector, and the only writes are the watermark that says
# what has been asked about and the proposal the player accepts or dismisses.


def _break_provider(cid: str):
    """This campaign's primary calendar, or None if it cannot be loaded.

    Only used to SIZE a time skip, so None is not a failure: the fact that the
    clock moved is in `time_history` regardless, and only the extra point for a
    long skip depends on measuring it. Swallowed rather than raised because a
    campaign with an unreadable calendar must still get the other two signals,
    and because this runs off the play loop where raising would put a banner
    over a scene mid-turn.
    """
    root = _campaign_root_or_404(cid)   # outside the try: a 404 is not a calendar failure
    try:
        return store.calendars.primary_provider(root)
    except Exception:  # noqa: BLE001 -- user-authored provider code, hand-edited calendar.json
        return None


def _break_intact(messages: list[dict], stored: dict, player_label: str = "") -> bool:
    """Whether a stored watermark still describes the transcript on disk.

    `store.rolling_intact.view`'s check, for `store.rolling_intact.view`'s reason and with its own
    tool: the transcript is not append-only, so "we have already asked about
    the first thirty posts" stops being true the moment one of those thirty is
    rerolled, edited away or trimmed. An APPEND leaves the prefix alone and
    keeps the watermark good, which it must, or an ordinary scene would forget
    every question it had ever been asked.

    `at > total` short-circuits rather than slicing: `messages[:at]` past the
    end yields the whole list, which would digest-match a transcript trimmed
    back to exactly what the watermark covered.

    A watermark with no digest at all is not intact. That is a scene written
    before this key existed (or hand-edited), and the safe reading of "we asked
    about thirty posts, but cannot say which" is that we did not.
    """
    return bool(stored["digest"]) and stored["at"] <= len(messages) \
        and store.rolling_summary.covered_digest(
            messages[:stored["at"]], player_label) == stored["digest"]


def _break_view(scene: dict, every: int, provider, player_label: str = "") -> dict:
    """The scene's break state, scored out of ONE snapshot of the scene.

    The transcript, the watermark and both histories all come off the `scene`
    handed in rather than from fresh reads, for `store.rolling_intact.view`'s reason and a
    sharper version of it: the histories and the transcript move TOGETHER (a
    move appends its transition line in the same mutator that extends
    `location_history`), so reading them separately can produce a location move
    counted against a transcript that does not contain the post announcing it.

    `player_label` travels with the snapshot for the same reason: it is an
    input to the render the stored digest was taken of, so a view built without
    it would call every watermark void and re-ask the question every time.

    A watermark whose prefix moved is VOID, not merely old: the scene is scored
    from zero, which is the same answer the rolling summary gives a fold whose
    ground moved. Review caught what carrying it forward instead did -- a scene
    rewound from thirty posts to ten and played back up to twenty-five reported
    nothing new for fifteen posts of real story, and went on reporting nothing
    until the count passed thirty again.
    """
    stored = store.scenes.scene_break_fields(scene["meta"])
    intact = _break_intact(scene["messages"], stored, player_label)
    history = store.scenes.histories(scene["meta"])
    scored = store.scene_break.evaluate(
        scene["messages"], history["locations"], history["times"],
        stored if intact else {"at": 0, "locs": 0, "times": 0}, every, provider)
    return {**scored, "stored": stored, "intact": intact}


def _break_body(view: dict, every: int) -> dict:
    """What both scene-break routes answer with.

    `verdict` is a tri-state string rather than a boolean: `""` is "nothing has
    been asked, or the last answer was dismissed", which is a different thing
    from "asked, and the model said no" -- and the panel says different things
    about them.

    `due` always answers the AUTOMATIC question, never the forced one, for
    `_rolling_body`'s reason: the panel reads this field to say when the next
    question is coming, and a forced call that found nothing new would
    otherwise report `due: false` about a different question entirely.
    """
    stored = view["stored"]
    return {"verdict": stored["verdict"], "reason": stored["reason"],
            "title": stored["title"],
            # A standing answer whose watermark was voided describes a
            # transcript that no longer exists -- the posts it reasoned about
            # were rerolled, edited or cut. `_rolling_body`'s `stale`, for its
            # reason: the panel may still show the prose, it may just not
            # present it as current. A scene with no answer is not stale; it has
            # nothing to be stale about.
            "stale": bool(stored["verdict"]) and not view["intact"],
            "posts": view["posts"], "score": view["score"],
            "signals": view["signals"], "every": every, "due": view["due"]}


@router.get("/campaigns/{cid}/scenes/{sid}/scene-break")
def get_scene_break(cid: str, sid: str):
    """Read the score and the standing proposal without ever spending a call.

    Needs no LLM connection on purpose, like the rolling-summary GET: the
    inspector reads this on every scene select, and a store with no key
    configured must still render the panel.
    """
    scene = _require_scene(cid, sid)
    every = campaign_settings.count(cid, "scene_break_every")
    return _break_body(_break_view(scene, every, _break_provider(cid),
                                   store.appearances.player_label(cid, sid)), every)


@router.post("/campaigns/{cid}/scenes/{sid}/scene-break")
async def post_scene_break(cid: str, sid: str, force: bool = False,
                           upto: int | None = None,
                           client: LLMClient = Depends(get_llm)):
    """Ask the model whether the scene has reached a place to stop.

    Fired by the client after every turn and not awaited by it, so the ordinary
    outcome is `asked: false` having touched no provider -- the heuristic
    declined. `force` is the panel's own button, and also the only way to reach
    the feature when `scene_break_every` is 0.

    The connection check runs BEFORE the due check, deliberately and for
    `post_rolling_summary`'s reason: a 409 that only appeared once a scene
    happened to be due would be indistinguishable, on the client, from the quiet
    no-op that is this route's normal answer.

    `upto` bounds the question to a transcript the caller knows was a clean
    boundary, exactly as the rolling summary does: the play loop releases the
    scene before firing this, so a fast next send can append an unanswered
    player post, and a question that took that post as the scene's END would be
    asking about a beat whose reply had not arrived.

    No in-flight coalescing, unlike the rolling summary, and the difference is
    the write rather than the call: two overlapping folds regress a summary's
    coverage if the older one lands second, where two overlapping questions
    produce two answers about nearly the same transcript and the later write
    simply wins. `_break_commit` still refuses a write whose transcript moved
    underneath it, which is the case that actually corrupts.
    """
    # `post_rolling_summary`'s refusal, for its reason: a forced question about
    # a closed branch is a paid answer about a scene nobody can play.
    if force:
        runs.require_scene_open(cid, sid)
    return await _break_once(cid, sid, force, upto, client)


async def _break_once(cid: str, sid: str, force: bool, upto: int | None,
                      client: LLMClient) -> dict:
    """The whole of the route above, from a fresh read of the scene.

    Split out for `_rolling_once`'s second reason rather than its first:
    there is no coalesced retry to redo the read for here, but there is a
    second caller. A landed turn asks this question itself now (#397), and
    that caller has no request to resolve `Depends` against -- so what it
    drives has to be a plain function of the same four decisions the route
    makes, or the two callers would disagree about what is due."""
    scene = _require_scene(cid, sid)
    if upto is not None:
        if upto < 0:
            raise HTTPException(status_code=400, detail="upto must not be negative")
        scene = {**scene, "messages": scene["messages"][:upto]}
    resolved = require_inference("scene-break", cid, operation="decide")
    every = campaign_settings.count(cid, "scene_break_every")
    provider = _break_provider(cid)
    view = _break_view(scene, every, provider,
                       store.appearances.player_label(cid, sid))
    # `force` overrides the threshold, never the emptiness: a forced question
    # about a scene with nothing new since the last one would pay a provider to
    # answer the question it just answered.
    # Nor does it override a span that is all hidden from context: those posts
    # are in no prompt, so `evaluate` does not count them, and the question
    # would be asked about nothing.
    if not (view["due"] or (force and view["posts"] > 0)):
        return {**_break_body(view, every), "asked": False}
    return await _break_ask(cid, sid, scene, view, every, resolved, client, provider)


async def _break_ask(cid: str, sid: str, scene: dict, view: dict, every: int,
                     resolved: UsableInference, client: LLMClient, provider) -> dict:
    """The paid half of `post_scene_break`, split out so the route above reads as
    the decision it is.

    Two calls at most, in this order, and the order is the design (spec 7.4).
    The verdict is a closed question, asked through `decide()` and committed
    first, exactly as the old one-call answer was. Only a YES that LANDED buys
    the second call, the title -- prose, which a closed question cannot carry,
    drafted on the `summary` route (`_break_title`) and written by a second
    guarded write (`_break_title_commit`). A YES therefore costs two calls and
    two copies of the transcript; a NO, or a verdict whose transcript moved
    under it, costs one. A title that fails leaves the verdict standing with an
    empty title: the proposal is the verdict, and the title only names it."""
    messages = scene["messages"]
    # Zero when the watermark was VOIDED, not the stale count it still holds.
    # Review caught the pair coming apart: a rewound scene scores from zero and
    # records a watermark covering the whole transcript, so slicing from the old
    # count would show the model the last ten posts while the answer went on
    # file as an answer about all fifty.
    base = min(view["stored"]["at"], len(messages)) if view["intact"] else 0
    scene_title = scene["meta"].get("title", "")

    def prepare():
        # Store reads and template renders, so off the loop: the follow-up a
        # landed turn schedules awaits this on the lifespan loop (CODE-M1).
        facts = store.chronicle.scene_facts(cid, sid)
        shown = store.regex.view.view(messages[base:], cid=cid, phase="prompt",
                                      offset=base, total=len(messages))
        transcript = store.chronicle.transcript_text(
            shown, store.appearances.player_label(cid, sid))
        item = store.scene_break.build_item(transcript, view["signals"], facts, scene_title)
        return facts, transcript, item, store.scene_break.explain()

    facts, transcript, item, explanation = await run_in_threadpool(prepare)
    try:
        # One decision scope (roadmap 01b): the verdict only, never the title
        # drafted after it (a `generate`).
        async with decision_capture.capturing(cid, sid, "scene-break") as scope:
            decision = await operations.decide(
                "scene-break", [item], client=client, resolved=resolved,
                explain=explanation, campaign=cid, scene=sid, capture=scope.hook())
    except LLMError as exc:
        raise _llm_http_error(exc) from exc
    # The title is "" here: it is drafted below, and only for a YES that lands.
    answer = store.scene_break.verdict_of(decision.items[0])
    # The prefix the question was asked ABOUT, digested before the write goes
    # anywhere near the file. `rolling_summary.covered_digest` is the right tool
    # and is reused rather than reimplemented: same transcript, same question
    # ("is this still the same prose?"), same three fields.
    digest = store.rolling_summary.covered_digest(
        messages, store.appearances.player_label(cid, sid))
    try:
        result = await run_in_threadpool(_break_commit, cid, sid, view["watermark"],
                                         answer, digest)
    except store.scenes.SceneNotFound:
        # Renamed or deleted while the model was answering -- the race
        # `_rolling_refresh` documents, reachable here for the same reason (the
        # play loop fires this after releasing the scene). There is nowhere to
        # put the answer and this call is fire-and-forget from the client.
        return {**_break_body(view, every), "asked": False}
    if result["landed"] and answer["break"]:
        title = await _break_title(cid, sid, transcript, facts, scene_title,
                                   answer["reason"], client)
        if title:
            result = {**result, "scene": await _break_titled(
                cid, sid, view["watermark"], digest, answer["reason"], title,
                result["scene"])}
    # Scored OUTSIDE the hold, from the scene the commit read back. Sizing a
    # time skip runs the campaign's calendar provider, which is user-authored
    # code, and this codebase does not run that under the campaign lock --
    # `scenes.moment.set_datetime` and `scenes.lifecycle._date_hint` make the
    # same cut, and review caught that resolving the provider outside the hold
    # while still CALLING it inside was only half of the rule.
    return {**_break_body(_break_view(result["scene"], every, provider,
                                      store.appearances.player_label(cid, sid)), every),
            "asked": result["landed"]}


async def _break_titled(cid: str, sid: str, watermark: dict, digest: str,
                        reason: str, title: str, scene: dict) -> dict:
    """The scene after writing `title` onto the verdict it was drafted for
    (`_break_title_commit`), or `scene` -- the one the verdict's commit read
    back -- when the title could not be written. Never raises for that: the
    verdict landed first, so a failed title write is the title lost, not the
    question."""
    try:
        titled = await run_in_threadpool(_break_title_commit, cid, sid, watermark,
                                         digest, reason, title)
    except store.scenes.SceneNotFound:
        # Renamed or deleted while the title was drafted: the verdict landed
        # (it is on the scene, wherever it went), the title is dropped, and the
        # answer is the verdict's.
        return scene
    except Exception as exc:  # noqa: BLE001 - the verdict already landed
        # A busy store or an unwritable file (CODE-M2): the answer is the
        # verdict's, untitled, rather than a 409 or a 500 over a proposal that
        # stands. Logged by kind, as `_break_title`'s skip is. Cancellation is
        # not an `Exception`, and passes through.
        log.warning("scene-break title not written for %s/%s: %s",
                    cid, sid, type(exc).__name__)
        return scene
    return titled["scene"]


def _break_commit(cid: str, sid: str, watermark: dict, answer: dict,
                  digest: str) -> dict:
    """Store a verdict only if the transcript it was formed FROM is still there,
    and report the state that results either way.

    Read-verify-write under one campaign hold (reentrant, so the mutators' own
    acquisitions are free). The verification is `_rolling_commit`'s, for its
    reasons and one of this feature's own: `delete_scene` frees a scene's id and
    the numbering reuses it, so a scene deleted and remade under the same title
    mid-call hands this write the very id it holds -- and a proposal is prose
    ABOUT a story, so landing one on a different scene puts a suggestion to end
    a scene that has not started under a reason nobody can place.

    An ordinary turn appending during the call passes, which it must, or a busy
    scene could never be asked about at all. What fails is an edit or a reroll
    inside the prefix, a trim, and a recycled id.

    ...and nothing that already covers at least as much may be stored. Review
    caught that "the later write simply wins" was the wrong rule, and not even a
    description of what happened: two questions can be in flight at once (the
    panel's button beside the play loop's), the newer one can finish first, and
    the older one's prefix is still perfectly intact because everything since is
    an APPEND. Writing then replaced the answer the player had just asked for
    with a staler verdict about fewer posts AND regressed the watermark, so the
    next automatic question came early and re-asked what had just been answered.
    A DISMISSAL moves the watermark the same way and is caught by the same rule,
    which is what it should be: "not here" must not be undone by a question that
    was already out when the player said it.

    Scores nothing and reads no calendar: this runs under the hold, and the view
    its caller needs is built outside it.
    """
    with store.locks.campaign_lock(cid):
        scene = store.scenes.read_scene(cid, sid)
        stored = store.scenes.scene_break_fields(scene["meta"])
        # Two questions about the same file, named apart because they are
        # answered against different watermarks: whether the posts THIS answer
        # was formed from are still on disk, and whether a DIFFERENT answer
        # already covers at least as much.
        unchanged = (watermark["at"] <= len(scene["messages"])
                     and store.rolling_summary.covered_digest(
                         scene["messages"][:watermark["at"]],
                         store.appearances.player_label(cid, sid)) == digest)
        # Against the stored watermark only while IT is still about this
        # transcript: a rewind voids it, and a voided watermark is no bar to an
        # answer about the scene as it now stands.
        superseded = (_break_intact(scene["messages"], stored,
                                    store.appearances.player_label(cid, sid))
                      and stored["at"] >= watermark["at"])
        landed = unchanged and not superseded
        if landed:
            store.scenes.set_scene_break(
                cid, sid, watermark["at"], watermark["locs"], watermark["times"],
                digest, "yes" if answer["break"] else "no", answer["reason"],
                answer["title"] if answer["break"] else "")
            # As in `_rolling_commit`, and for the same reason: the play loop
            # fires this as a background run after every turn, with no response
            # line for the middleware to stamp (#409).
            store.revision.bump(cid)
            scene = store.scenes.read_scene(cid, sid)
        return {"landed": landed, "scene": scene}


async def _break_title(cid: str, sid: str, transcript: str, facts: dict | None,
                       title: str, reason: str, client: LLMClient) -> str:
    """The title a stored YES suggests for the next scene, or "" for none.

    Its own call on its own task, `scene-break-title`, which the `summary`
    route claims -- so it is drafted by that route's model, not the Decision
    role's: the verdict is a closed question any decider can answer, the title
    is prose. Shown the same head and transcript the verdict was formed from,
    plus the verdict's reason (`build_title_prompt`), and read back cleaned
    and capped (`parse_title`), since it is one frontmatter line shown in a
    chip.

    Never raises for the failures this call can have. A route that cannot
    resolve (`_soft_resolved`), a failure preparing the call (a store read
    while resolving, the prompt's render) and a provider error are each "" --
    the verdict is already on file and stands without a title. The meter files a
    provider's failure, which is the one place LLM failures are recorded; a
    route that cannot resolve makes no call and so no ledger row, and is
    logged here instead, so an untitled proposal can be explained. The line
    carries the refusal's KIND and never its sentence, which names the
    provider and model."""
    # Both off the loop (CODE-M1): resolving reads `config.md`, the
    # connections and the catalog sidecar, and the prompt renders templates.
    try:
        resolved, _why, kind = await run_in_threadpool(
            _soft_resolved, lambda: require_inference("scene-break-title", cid))
        if resolved is not None:
            prompt = await run_in_threadpool(
                store.scene_break.build_title_prompt, transcript, facts, title, reason)
    except Exception as exc:  # noqa: BLE001 - the verdict already landed
        # An unreadable store file while resolving, or a broken override
        # template under `GRIMOIRE_TEMPLATES`: the title is lost, not the
        # question. Logged by kind, as the skip below and `_break_titled` are;
        # cancellation is not an `Exception`, and passes through.
        log.warning("scene-break title skipped for %s/%s: %s", cid, sid,
                    type(exc).__name__)
        return ""
    if resolved is None:
        log.warning("scene-break title skipped for %s/%s: the summary route "
                    "cannot run (%s)", cid, sid, kind or "unknown")
        return ""
    try:
        with store.usage.meter("scene-break-title", campaign=cid, scene=sid) as m:
            text = await operations.generate("scene-break-title", prompt, client=client,
                                             resolved=resolved, usage=m.usage, stream=False)
    except LLMError:
        return ""
    return store.scene_break.parse_title(text)


def _break_title_commit(cid: str, sid: str, watermark: dict, digest: str,
                        reason: str, title: str) -> dict:
    """Write `title` onto the verdict it was drafted for, if that verdict is
    still the one on file; report the scene either way.

    The verdict was committed first (`_break_commit`) and the title call ran
    after it, outside any hold. Anything may have moved in between -- a
    dismissal, a newer question, a rewind that voided the watermark -- and a
    title is prose ABOUT one specific verdict. So the write lands only when
    the stored proposal still reads exactly as `_break_commit` left it: the
    same watermark (position, moves and advances) and digest, a `yes`, the
    same reason, and no title yet. Otherwise nothing is written and the paid title is dropped.

    Read-verify-write under one campaign hold, as `_break_commit`, and it bumps
    the campaign's write token where it writes for the same reason (#409): a
    background run has no response line for the middleware to stamp.
    """
    with store.locks.campaign_lock(cid):
        scene = store.scenes.read_scene(cid, sid)
        stored = store.scenes.scene_break_fields(scene["meta"])
        drafted_for = (stored["at"] == watermark["at"]
                       and stored["locs"] == watermark["locs"]
                       and stored["times"] == watermark["times"]
                       and stored["digest"] == digest
                       and stored["verdict"] == "yes" and stored["reason"] == reason
                       and stored["title"] == "")
        if drafted_for:
            store.scenes.set_scene_break(
                cid, sid, watermark["at"], watermark["locs"], watermark["times"],
                digest, "yes", reason, title)
            store.revision.bump(cid)
            scene = store.scenes.read_scene(cid, sid)
        return {"scene": scene}


@router.post("/campaigns/{cid}/scenes/{sid}/scene-break/dismiss")
def post_scene_break_dismiss(cid: str, sid: str):
    """"Not here" — retire the proposal and start counting from the scene as it
    stands.

    The watermark is moved by the mutator off the file it already holds open,
    not by this route off a read of its own: a dismissal that recorded a length
    read a moment earlier would leave the post that landed in between outside
    both the retired question and the next one.
    """
    _require_scene(cid, sid)
    store.scenes.dismiss_scene_break(cid, sid)
    scene = _require_scene(cid, sid)
    every = campaign_settings.count(cid, "scene_break_every")
    return _break_body(_break_view(scene, every, _break_provider(cid),
                                   store.appearances.player_label(cid, sid)), every)
