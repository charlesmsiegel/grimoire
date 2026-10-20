"""Secondary review phases stage independent edits and report their own failures."""

from __future__ import annotations

from dataclasses import dataclass

from starlette.concurrency import run_in_threadpool

from ... import decisions, store
from ... import inference as operations
from ...llm import LLMClient
from .. import decision_capture
from ..common import UsableInference, _noting
from . import _review_budget
from ._router import router as router


async def _run_audit(cid: str, sid: str, client: LLMClient,
                     resolved: UsableInference | None,
                     budget: _review_budget._Budget, abandoned=None,
                     unroutable: str = "") -> tuple[list[dict], dict]:
    """(edits, mechanics) for the scene audit. Never raises for absorb; every
    failure is an explicit mechanics status (spec: audit visibility) so absorb
    stays intact even when the audit pipeline blows up.

    `attempted` says whether a request actually reached the model and
    `budget_exhausted` says whether this absorb's clock is why it did not --
    the two facts a bare `status: failed` cannot carry.

    `abandoned` is _stage_dossiers' predicate, and matters more here: the audit
    is ONE call, so there is no "between calls" for a check to sit in. Watching
    the call itself is the only thing that can end it. Absorb passes nothing, so
    the never-raises rule above is untouched on that path."""
    mech = {"status": "skipped", "reason": None, "warnings": [], "dropped": [],
            "attempted": False, "budget_exhausted": False}
    if resolved is None:
        # No usable connection for this phase's route -- reported the way every
        # other audit failure is, so the absorb around it still lands.
        return [], {**mech, "status": "failed", "reason": unroutable or "no connection"}
    excluded: list = []
    try:
        if store.modules.resolve(cid) is None:
            mech["reason"] = "no module"
            return [], mech
        # ONE failure boundary around the ENTIRE audit pipeline (spec:
        # never-fail-absorb) — sheet_blocks, read_scene, transcript,
        # roll_lines, build_prompt, complete, parse AND materialize. Any
        # exception anywhere here is a failed audit, never a 500 absorb.
        blocks, excluded = store.audit.sheet_blocks(cid, sid)
        if not blocks and not excluded:
            mech["reason"] = "no sheeted scope"
            return [], mech
        if not blocks:
            return [], {**mech, "status": "failed",
                        "reason": "all scoped sheets invalid", "dropped": excluded}
        scene = store.scenes.read_scene(cid, sid)
        shown = store.regex.view.view(scene["messages"], cid=cid, phase="prompt")
        transcript = store.chronicle.transcript_text(
            shown, store.appearances.player_label(cid, sid))
        messages = store.audit.build_prompt(transcript, blocks,
                                            store.audit.roll_lines(cid, sid))
        # `mech` is the accumulator every failure return below spreads, so the
        # callback reaches all of them -- and it fires only if the request goes
        # out, which is a fact only `run` holds.
        with store.usage.meter("audit", campaign=cid, scene=sid) as m:
            text = await _review_budget._watched(
                budget.run(operations.generate("audit", messages, client=client,
                                               resolved=resolved, usage=m.usage,
                                               stream=False),
                           lambda: mech.__setitem__("attempted", True),
                           on_timeout=_noting(client, resolved, m.usage)), abandoned)
        parsed = store.audit.parse_output(text)
        edits, dropped = store.audit.materialize(cid, sid, parsed)
    except _review_budget.Abandoned:
        # Ahead of the boundary below on purpose. That boundary exists so a
        # broken audit cannot fail an absorb; this is not a broken audit, it is
        # nobody waiting for one, and turning it into a `failed` mechanics
        # status would report a phase that ran and did not work.
        raise
    except _review_budget.BudgetRefused:
        # Never asked, so there is no finding to doubt -- only work still owed.
        # Still `failed` (not `skipped`) and still paired with the POST /audit
        # retry the UI renders: a fresh budget is exactly what that retry gets.
        return [], {**mech, "status": "failed", "budget_exhausted": True,
                    "reason": "the absorb time budget ran out before the audit could run",
                    "dropped": excluded}
    except store.audit.AuditParseError as exc:
        return [], {**mech, "status": "failed", "reason": str(exc),
                    "dropped": excluded}
    except Exception as exc:  # noqa: BLE001 -- LLMError, store errors, anything
        # A budget overrun reaching *this* handler was cancelled mid-flight, so
        # the request did go out and `mech["attempted"]` stays true -- the never
        # -sent case is `BudgetRefused`, caught above.
        return [], {**mech, "status": "failed", "reason": f"audit failed: {exc}",
                    "dropped": excluded, "budget_exhausted": _review_budget._budget_overrun(exc)}
    dropped = excluded + dropped
    status = "degraded" if dropped else "ok"
    reason = ("some sheets could not be audited" if excluded else
              "some findings could not be validated") if dropped else None
    return edits, {"status": status, "reason": reason,
                   "warnings": parsed["warnings"], "dropped": dropped,
                   "attempted": True, "budget_exhausted": False}


@dataclass(frozen=True)
class _NpcPhaseWords:
    """How a per-NPC phase's row words each way it can end. The dossier and
    voice phases differ only in these nouns; the rule that picks among them is
    `_npc_phase_result`, one for both."""

    none_present: str
    none_succeeded: str
    none_in_time: str
    some_failed: str
    rest_out_of_time: str


_DOSSIER_WORDS = _NpcPhaseWords(
    none_present="no npcs present",
    none_succeeded="no dossier could be prepared",
    none_in_time="the absorb time budget ran out before any dossier could be prepared",
    some_failed="some dossiers could not be prepared",
    rest_out_of_time="the absorb time budget ran out before the rest could be prepared")

_VOICE_WORDS = _NpcPhaseWords(
    none_present="no anchored npcs present",
    none_succeeded="no voice check could be run",
    none_in_time="the absorb time budget ran out before any voice check could be run",
    some_failed="some voice checks could not be run",
    rest_out_of_time="the absorb time budget ran out before the rest could be checked")


def _npc_phase_result(edits: list[dict], out: dict, succeeded: str,
                      words: _NpcPhaseWords) -> tuple[list[dict], dict]:
    """A per-NPC phase's `(edits, block)` once its loop is over, the status
    read off its tallies: `out[succeeded]` (the NPCs it got an answer for),
    `failed`, and `skipped` (the ones the budget never reached)."""
    if not out[succeeded] and not out["failed"] and not out["skipped"]:
        return edits, {**out, "reason": words.none_present}
    if not out[succeeded]:
        # A budget that ran out before the first call is a different story from
        # calls that were made and went wrong; say which one happened.
        return edits, {**out, "status": "failed",
                       "reason": words.none_succeeded if out["failed"] else words.none_in_time}
    if out["failed"]:  # the more specific story when both happened; `skipped` still lists the rest
        return edits, {**out, "status": "degraded", "reason": words.some_failed}
    if out["skipped"]:
        return edits, {**out, "status": "degraded", "reason": words.rest_out_of_time}
    return edits, {**out, "status": "ok"}


def _npc_failed(out: dict, aid: str, exc: BaseException) -> None:
    """Tally one NPC's failed attempt on its phase's `out`, flagging the budget
    when this absorb's own clock is what stopped it. Type-prefixed: a bare
    str() is useless for the store's own errors (CharacterNotFound("aese")
    stringifies to just "aese")."""
    detail = str(exc).strip()
    out["budget_exhausted"] = out["budget_exhausted"] or _review_budget._budget_overrun(exc)
    out["failed"].append({
        "id": aid,
        "reason": f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__})


async def _stage_dossiers(cid: str, sid: str, transcript: str, client: LLMClient,
                          resolved: UsableInference | None, budget: _review_budget._Budget,
                          abandoned=None,
                          unroutable: str = "") -> tuple[list[dict], dict]:
    """Propose a refreshed campaign dossier for every present NPC, reporting the
    outcome.

    The LLM call happens here; the WRITE does not (#235). Each dossier comes back
    as a StagedEdit that lands with the rest of the batch in PUT /chronicle, so an
    absorb that dies partway through this loop -- or a reviewer who hits Cancel --
    leaves nothing behind. `proposed` therefore names the NPCs whose dossier was
    generated, not written; an NPC whose paragraph came back unchanged is proposed
    with no edit to show for it.

    Never raises for absorb -- a dossier failure must not fail absorb -- but it
    is not silent either: failures (#236) and budget skips (#243) come back as a
    status the inspector renders, mirroring _run_audit's shape -- including
    `attempted` and `budget_exhausted`, the two flags a phase row is built from.

    `abandoned` is an optional async predicate: "is anyone still waiting for
    this?". It is asked between NPCs and, via `_watched`, alongside the call in
    flight; when it says no, `Abandoned` comes out and the caller that supplied
    the predicate turns it into a response. Absorb passes nothing -- its caller
    is holding a review open and has not gone anywhere -- so the never-raises
    rule above is untouched on that path: no predicate, no Abandoned."""
    out: dict = {"status": "skipped", "reason": None,
                 "proposed": [], "failed": [], "skipped": [],
                 "attempted": False, "budget_exhausted": False}
    if resolved is None:
        return [], {**out, "status": "failed", "reason": unroutable or "no connection"}
    edits: list[dict] = []
    try:
        cast = store.appearances.scene_cast(cid, sid)
        croot = store.appearances.locked_actor_root(cid)   # cast actors are locked, so campaign-side
    except Exception as exc:  # noqa: BLE001 -- an unreadable cast is a failed phase, not a 500
        # Recorded as well as reported (#156): the phase status is visible in
        # the review that is open right now, and gone the moment it is closed.
        # `record_exception`, like the generic handler below, so the frames
        # survive too. This row is the only durable trace of a cast read that
        # failed -- the phase status dies with the review it is rendered in --
        # and a row without the stack is the one that cannot be acted on.
        store.errors.record_exception(exc, "dossier",
                                      detail=f"could not read the scene cast: {exc}",
                                      campaign=cid, scene=sid, task="dossier")
        return [], {**out, "status": "failed", "reason": f"could not read the scene cast: {exc}"}
    # Dossiers feed the npc-only "Active elsewhere" tier; player cards are skipped.
    npcs = [a for a in cast if a["kind"] == "characters" and a["role"] == "npc"]

    def drop_tail(i: int) -> None:
        """Record the NPC at `i` and everyone after it as never reached.

        The extraction call is the part worth keeping, so the tail is dropped
        rather than run unbounded (#243) — but named, not silently, for the same
        reason failures are (#236)."""
        out["skipped"] = [b["id"] for b in npcs[i:]]
        out["budget_exhausted"] = True

    for i, a in enumerate(npcs):
        if budget.spent():
            drop_tail(i)
            break
        # The reviewer walked away -- closed the review, saved it, switched
        # campaigns -- and the client aborted. Nothing will ever read this
        # reply, so every remaining NPC is an LLM call spent on nobody's behalf,
        # and `absorb_budget = 0` means the check above will never stop them.
        #
        # This one keeps the NEXT call from being issued at all; `_watched`
        # below stops the one already in flight. Both, because they cover
        # different moments: a disconnect during the gap between NPCs is caught
        # here at once, rather than costing a doomed call that `_watched` then
        # has to cancel a poll interval later.
        if abandoned is not None and await abandoned():
            raise _review_budget.Abandoned
        try:
            name = store.characters.read_character(croot, a["id"])["meta"].get("name", a["id"])
            # Read ONCE, before the await. Re-reading after it to build the
            # staged `before` would record a paragraph another review wrote
            # while this call was in flight -- the conflict guard would then
            # pass and this stale output would overwrite that newer one (#235).
            prior = store.dossiers.read(croot, a["id"])
            msgs = store.dossiers.build_prompt(name, prior, transcript)
            # The loop's own check is stale by now -- the two reads and the
            # prompt build above are not free -- so the attempt is recorded by
            # `run`, which alone can decide it atomically with the deadline.
            with store.usage.meter("dossier", campaign=cid, scene=sid) as m:
                d_text = await _review_budget._watched(
                    budget.run(operations.generate("dossier", msgs, client=client,
                                                   resolved=resolved, usage=m.usage,
                                                   stream=False),
                               lambda: out.__setitem__("attempted", True),
                               on_timeout=_noting(client, resolved, m.usage)), abandoned)
            parsed_dossier = store.dossiers.parse_output(d_text)
            # stage_edit returns None for an unchanged paragraph AND for a blank
            # reply; only the first is a success. Left conflated, a model that
            # answers "" for every NPC reports `ok` with nothing staged -- exactly
            # #236's symptom (dossiers quietly stop updating) wearing a status.
            if not parsed_dossier:
                # #236's symptom, recorded rather than only reported. This is
                # the branch the error store was asked for by name: no
                # exception is raised here, so nothing else in the app --
                # not the meter, not the logging bridge -- ever sees it, and
                # the phase status carrying it dies with the review.
                store.errors.record("dossier", "empty_reply",
                                    f"{name} came back with an empty dossier",
                                    campaign=cid, scene=sid, task="dossier")
                out["failed"].append({"id": a["id"], "reason": "empty dossier reply"})
                continue
            edit = store.dossiers.stage_edit(a["id"], name, prior, parsed_dossier)
        except _review_budget.Abandoned:
            # Ahead of the generic handler below on purpose: this is not this
            # NPC's failure to record and move past, it is the whole run being
            # over. The loop's caller turns it into a status rather than a 500.
            raise
        except _review_budget.BudgetRefused:
            # Refused, not failed: nothing was sent, so this NPC is one more the
            # clock never reached — and so is everyone after them.
            drop_tail(i)
            break
        except Exception as exc:  # noqa: BLE001 -- LLMError, store errors, anything
            # Recorded here as well as reported, and `record_exception` is what
            # makes that safe: a provider failure has already been written down
            # by the meter above and is marked, so this call leaves it alone,
            # while the failures the meter never saw -- an unreadable character
            # file, a dossier that would not parse -- are #156's "fails with
            # zero trace anywhere" and get their row here.
            store.errors.record_exception(exc, "dossier", campaign=cid, scene=sid,
                                          task="dossier")
            _npc_failed(out, a["id"], exc)
        else:
            out["proposed"].append(a["id"])
            if edit:
                edits.append(edit)
    return _npc_phase_result(edits, out, "proposed", _DOSSIER_WORDS)


def _voice_item(name: str, record: dict, transcript: str,
                flag: dict) -> tuple[decisions.Item, str]:
    """The voice judge's `decide()` item and its rationale instruction: both
    render templates, so `_stage_voice_drift` builds them in a worker thread."""
    return (store.voice_drift.judge_item(name, record, transcript, flag),
            store.voice_drift.explain())


async def _stage_voice_drift(cid: str, sid: str, transcript: str, client: LLMClient,
                             resolved: UsableInference | None, budget: _review_budget._Budget,
                             unroutable: str = "",
                             abandoned=None) -> tuple[list[dict], dict]:
    """Judge every present NPC's dialogue in this scene against their voice
    anchor, proposing a drift flag (or a clear) for each.

    Runs ONLY for NPCs that actually have an anchor (#59). That is the cost
    control for the whole feature: a library with no anchors makes no extra LLM
    calls, and adding one is how a user opts a character in.

    Each judgment is one `decide()` item (spec 7.4) on the `voice_drift` route,
    which runs on the Decision role: a choice of verdict, with the corrective
    as the item's rationale. `resolved` is the fan-out's soft resolution of
    that route -- None, with `unroutable` saying why, when it could not
    resolve -- so a route that cannot run fails this phase and never the
    absorb.

    The LLM call happens here; the WRITE does not, for _stage_dossiers' reason
    (#235) -- a flag that landed before the reviewer saved would survive a
    Cancel and go on correcting the model for a scene the chronicle never
    recorded.

    Never raises -- voice drift must not fail absorb -- but it is not silent
    either: failures and budget skips come back as a status the inspector
    renders, mirroring _stage_dossiers' shape -- including `attempted` and
    `budget_exhausted`, the two flags a phase row is built from.

    The whole phase is ONE decision scope (roadmap 01b): every anchored NPC's
    call is a record of one prompt-log entry, told apart by `part`, the NPC's
    record id. The loop catches every `Exception` per NPC, `Abandoned`
    included, so the phase would exit normally after a close: `abandoned`,
    the review's own check, is what keeps the scope from filing then."""
    out: dict = {"status": "skipped", "reason": None, "checked": [], "flagged": [],
                 "unjudged": [], "noteless": [], "failed": [], "skipped": [],
                 "attempted": False, "budget_exhausted": False}
    if resolved is None:
        return [], {**out, "status": "failed", "reason": unroutable or "no connection"}
    edits: list[dict] = []
    try:
        cast = store.appearances.scene_cast(cid, sid)
        croot = store.appearances.locked_actor_root(cid)   # cast actors are locked, so campaign-side
        roster = store.appearances.roster_names(cid)
    except Exception as exc:  # noqa: BLE001 -- an unreadable cast is a failed phase, not a 500
        return [], {**out, "status": "failed", "reason": f"could not read the scene cast: {exc}"}
    # Resolved up front so the budget check below covers only the actors that
    # will really cost a call: an anchorless NPC must not be reported as
    # "skipped for time" when it was never going to be judged at all.
    todo, out["failed"] = _judgeable_npcs(cid, cast, roster)

    def drop_tail(i: int) -> None:
        """Record the anchored NPC at `i` and everyone after it as never
        reached -- named, not silently dropped, for _stage_dossiers' reason."""
        out["skipped"] = [b for b, _, _ in todo[i:]]
        out["budget_exhausted"] = True

    async with decision_capture.capturing(cid, sid, "voice-drift",
                                          abandoned=abandoned) as scope:
        for i, (aid, name, record) in enumerate(todo):
            if budget.spent():
                drop_tail(i)
                break
            try:
                # Read ONCE, before the await -- dossiers' rule: re-reading after it
                # would record a flag another review wrote while this call was in
                # flight, and the conflict guard would then pass on stale output.
                # One snapshot, so the note and the provenance staged as `before`
                # always describe the same committed flag (voice_drift.read_record).
                flag = store.voice_drift.read_record(croot, aid)
                # The item is built over the EFFECTIVE anchor (all the generator
                # ever saw: a rule past the cap is enforced against neither) and
                # the stored correction only while it is STILL IN FORCE -- the test
                # `context/cast.py` applies before putting a note in the scene
                # prompt. A note fingerprinted to a REPLACED anchor, handed to the
                # judge as current, would mint a fresh flag against the anchor that
                # replaced it (`voice_drift.judge_item`, `live_correction`).
                # Off the loop, as the scene-break check's item is (CODE-M1): the
                # item and the rationale instruction render templates, and absorb
                # runs on the lifespan loop a detached turn shares.
                item, explain = await run_in_threadpool(_voice_item, name, record,
                                                        transcript, flag)
                # The absorb budget runs INSIDE `decide()`'s meter (`around`), as
                # it ran inside this phase's own meter before: an overrun is that
                # meter's `error/timeout` row, and `_noting` reads the live holder,
                # so a fallback that had taken over is the connection told. The
                # attempt is recorded by `run`, which alone can decide it
                # atomically with the deadline.
                decision = await operations.decide(
                    "voice-drift", [item], client=client, resolved=resolved,
                    explain=explain, campaign=cid, scene=sid, capture=scope.hook(aid),
                    around=lambda call, holder: budget.run(
                        call, lambda: out.__setitem__("attempted", True),
                        on_timeout=_noting(client, resolved, holder)))
                finding = store.voice_drift.finding_of(decision.items[0])
                # An unreadable verdict is a FAILED call, not a quiet pass, and a
                # drift is only usable with a corrective that fits in front of
                # every later turn (`check_failure` has the three checks and their
                # words).
                reason = store.voice_drift.check_failure(finding)
                if reason:
                    out["failed"].append({"id": aid, "reason": reason})
                    continue
                # `before` is that snapshot, never a re-read (the rule above).
                edit = store.voice_drift.stage_edit(aid, name, flag["note"], finding,
                                                    record["text"], record["id"], flag["anchor"])
            except _review_budget.BudgetRefused:
                # Refused, not failed: nothing was sent, so this NPC is one more the
                # clock never reached — and so is everyone after them.
                drop_tail(i)
                break
            except Exception as exc:  # noqa: BLE001 -- LLMError, store errors, anything
                _npc_failed(out, aid, exc)
            else:
                _tally_voice_verdict(out, aid, finding)
                if edit:
                    edits.append(edit)
    return _npc_phase_result(edits, out, "checked", _VOICE_WORDS)


def _judgeable_npcs(cid: str, cast: list[dict],
                    roster: list) -> tuple[list[tuple[str, str, dict]], list[dict]]:
    """`(todo, failed)`: the present NPCs with a voice anchor whose lines the
    judge can be pointed at, as `(id, name, anchor record)`, and every one it
    cannot, with the reason -- an unreadable anchor or card, a card name no
    transcript label can carry, or a name another speaker can also be
    labelled. `roster` is the campaign's speaker names (`roster_names`).

    A failure here costs that actor its voice check, never the phase."""
    # The speaker labels this scene's transcript can carry. transcript.j2 labels
    # each line with the speaker's card name and nothing else, so an anchored NPC
    # sharing a label with ANY other speaker is unjudgeable -- the judge cannot
    # tell whose lines belong to its subject, and answers confidently regardless.
    #
    # Counted over the CAMPAIGN roster, not the present cast, because the present
    # cast is not the set of speakers: `scene_cast` drops an actor the moment it
    # leaves, while the transcript keeps every line it spoke, still wearing its
    # name. `_drift_roster` reaches for the same list against the same problem.
    # Nothing records which scene a departed actor spoke in, so this over-counts
    # a same-named actor who was never here -- the safe direction, since the
    # alternative is a corrective persisted against the wrong character.
    # Plus the role fallbacks transcript.j2 uses for a line carrying no speaker
    # stamp -- those are labels too. Whether this scene has any such line is not
    # recoverable from the rendered transcript, and an ambiguous judgment must
    # never be persisted, so a character wearing one of these names is
    # disqualified unconditionally rather than on a guess.
    speakers = [n for n in roster if isinstance(n, str) and n.strip()]
    speakers += ["You", "Grimoire"]
    todo: list[tuple[str, str, dict]] = []
    failed: list[dict] = []
    for a in cast:
        if a["kind"] != "characters" or a["role"] != "npc":
            continue   # a player character's voice is the user's to drift
        try:
            record = store.overlay.voice_anchor_record(cid, a["id"])
        except Exception as exc:  # noqa: BLE001 -- unreadable anchor: skip this actor, keep the phase
            failed.append({"id": a["id"], "reason": f"{type(exc).__name__}: {exc}"})
            continue
        if not record["text"]:
            continue
        # `a["name"]` is the LOCKED VERSION's card name, which is what the
        # transcript labels this character's lines with. The container's
        # meta name can differ from it, and naming the judge a character the
        # transcript never mentions is how a multi-NPC scene gets judged
        # against the wrong lines (or comes back `not_enough`).
        #
        # Cards are stored as arbitrary dicts, so `data.name` can be a
        # number or an object -- import and version PUT both accept them.
        # Checked HERE, inside the per-actor boundary: everything below
        # treats the name as text, and one bad card must cost that actor its
        # voice check rather than 500 the whole absorb.
        # The RAW card name (`voice_drift.locked_name`), not `a["name"]`:
        # `_actor_name` substitutes the actor id for a card that carries no
        # usable one, and that id is a display convenience, not something
        # the transcript is known to label anyone with. Judging against it
        # means judging against a name nobody agreed on.
        try:
            name = store.voice_drift.locked_name(cid, a["id"])
        except Exception as exc:  # noqa: BLE001 -- unreadable card: skip this actor
            failed.append({"id": a["id"], "reason": f"{type(exc).__name__}: {exc}"})
            continue
        label, reason = _transcript_label(name, speakers)
        if reason:
            failed.append({"id": a["id"], "reason": reason})
            continue
        todo.append((a["id"], label, record))
    return todo, failed


def _transcript_label(name: object, speakers: list[str]) -> tuple[str, str]:
    """`(name, "")` when the transcript labels a card's lines `name` and no
    other speaker's, so the voice judge can be pointed at them, else
    `("", why not)`. `name` is the locked card's raw name, which need not even
    be text."""
    # `label_preserved`, not merely "is a nonblank string": the
    # serializer silently writes the generic role label instead of a
    # name it cannot form a marker from (one holding `*` or a newline,
    # or over 64 characters). Those lines land in the transcript as
    # "Grimoire", so pointing the judge at the card name hunts for a
    # speaker that cannot appear -- and risks charging generic
    # assistant prose to this character instead.
    if not isinstance(name, str) or not store.scenes.label_preserved(name):
        return "", ("the locked card has no name that can appear as a transcript "
                    "label, so the judge cannot be pointed at its lines")
    # Report the clash instead of judging through it -- naming it is
    # actionable (rename a card), whereas judging is a coin flip
    # presented as a finding, and a wrong one persists a corrective
    # that nags a character for dialogue someone else spoke.
    # `scenes.confusable`, not a whole-name comparison: `match_name` is
    # what decided which cast member a written label meant when the
    # reply was stored, so it is what decides whether a label is
    # ambiguous. "Winifred Vance" and "Winifred Vale" are distinct
    # strings, but a block labelled "Winifred" belongs to neither.
    if store.scenes.confusable(name, speakers):
        return "", (f"another speaker in this scene can also be labelled {name!r}, "
                    "so their lines cannot be told apart in the transcript")
    return name, ""


def _tally_voice_verdict(out: dict, aid: str, finding: dict) -> None:
    """Tally one NPC's usable verdict on the voice phase's `out`: checked, and
    flagged, noteless or unjudged as the verdict reads."""
    out["checked"].append(aid)
    if finding["verdict"] == store.voice_drift.DRIFT:
        out["flagged"].append(aid)
        if not finding["note"].strip():
            # Only a native drift reaches here with no note
            # (`check_failure`). Shown, and `stage_edit` proposes
            # nothing for it: a standing flag is neither replaced nor
            # cleared by a verdict that has no corrective to store.
            out["noteless"].append(aid)
    elif finding["verdict"] == store.voice_drift.NOT_ENOUGH:
        # A real judgment that produces no edit -- like a dossier that
        # came back unchanged. Named so "checked, not flagged" does not
        # read as "confirmed in voice" when nobody actually heard them.
        out["unjudged"].append(aid)
