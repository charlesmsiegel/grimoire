"""Retcon replay: re-run each later turn against the edited post, one at a time (#79).

A replay is a walk forward through a transcript that has already been cut. The
posts that were cut are held here — this file is the ONLY copy of them from the
moment the cut lands — and handed back to the walk one step at a time:

- a **verbatim** step is replayed as it was. Player posts are the player's
  words and no model rewrites them; manual dice rolls are a transcript line in
  lockstep with an immutable `rolls.json` entry, so re-appending the line is
  what keeps the two in step.
- a **generation** step is one model turn. It is NOT replayed: the caller
  regenerates it against the edited history (the route composes and streams the
  same way an ordinary turn does), and the reviewer accepts it, rerolls it
  (plain `POST .../regenerate`, since the fresh reply is the trailing run) or
  stops the walk.

Nothing here calls a model. The store's whole job is to remember what the scene
used to say, how far the walk has got, and how to put the rest back.

**The segmentation is taken from `turn_sizes`, not guessed from the grammar.**
Two consecutive generations with no player post between them (an empty send, a
director turn) look like one long run to a reader of the transcript, and
restoring them as one would hand reroll a boundary that spans two generations —
the desync `scenes/turns.py` refuses to act on. Blocks the recorded sizes do not
cover (a legacy prefix, or the remains of a generation the cut ran through) are
each their own step, which is the safe direction: it adds a boundary where there
was none rather than merging two that existed.

**One replay per campaign, in `<campaign>/replay.json`.** Not a per-scene
sidecar: the file holds a transcript's worth of text, it is written once per
step, and two replays running at once in one campaign is not a thing to
support — the walk regenerates turns, and two walks would be two models writing
into one campaign's continuity from different pasts. Being a plain campaign file
also keeps it out of the id-length budget every `<sid>.<suffix>` sidecar has to
fit (`store/scene_ids.py`), and its scene id travels through the ordinary
`scene_refs.repoint` fan-out like every other stored id.

**What a replay refuses to start over.** A cut span containing one of the
scene's own transition lines — a location move or a time advance — is refused
outright. `scenes.delete_from` rewinds `location_history`/`time_history` with
the transcript, so re-appending the line without its history entry leaves the
scene prompted somewhere its transcript never goes, and re-deriving the entry
from the line's prose is exactly the guess `serialize.transition_kind`'s callers
are written not to make. Cast join/leave lines carry the same speaker and are
NOT refused: they are `appearances`' record, the cut never touched it, and the
line is narration of something that still happened.

**Cancelling puts the rest back.** `cancel(restore=True)` cuts the transcript
back to the last accepted step and re-appends every remaining original, which is
what makes starting a replay recoverable rather than a commitment. It cannot put
back what was already accepted — those turns were replaced on purpose, one at a
time, with a review each.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import alternates, atomic, cascade, commits, config, locks, turnstate
from .campaigns import paths as campaigns_paths
from .paths import now_iso
from .scenes import paths as scenes_paths, read as scenes_read, \
    serialize as scenes_serialize, turns as scenes_turns, write as scenes_write


log = logging.getLogger(__name__)


class ReplayError(Exception):
    """A replay step that cannot be taken, phrased for the reader: every one of
    these reaches the player as the reason a button did nothing."""


#: Refused rather than replayed — see the module docstring.
BLOCKED_TRANSITION = ("this scene changes its location or time after that post, and a "
                      "replay cannot rebuild those — cut the scene there instead")


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "replay.json"


def read(cid: str) -> dict:
    """The stored session, or `{}`.

    Tolerant of a garbled file, and that tolerance costs more here than it does
    anywhere else in the store: this file holds the only copy of the posts the
    replay's cut removed, so a `{}` from an unparseable one reads as "there was
    never a replay" and those posts are unrecoverable through this module.
    Raising instead would not bring them back either — it would only leave every
    later request 500ing — and the file itself is untouched on disk for anyone
    who wants to repair it by hand, which is the recovery that exists.
    """
    p = _path(cid)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(cid: str, rec: dict) -> None:
    atomic.write_text(_path(cid), json.dumps(rec, indent=2, sort_keys=True) + "\n")


def _clear(cid: str) -> None:
    _path(cid).unlink(missing_ok=True)


def _message(m: dict) -> dict:
    """One transcript message as the backlog stores it: what `append_message`
    and `append_reply` need to write it back, and nothing else."""
    return {"role": m.get("role", "assistant"), "speaker": m.get("speaker") or "",
            "content": m.get("content", "")}


def _segment(messages: list[dict], cut: int, sizes: list[int]) -> list[dict]:
    """`messages[cut:]` as replay steps. See the module docstring for why the
    recorded turn boundaries drive this rather than the transcript's shape."""
    blocks = scenes_turns._model_blocks(messages)
    prefix = max(len(blocks) - sum(sizes), 0)
    gen: dict[int, int] = {}
    n = 0
    for ordinal, size in enumerate(sizes):
        for _ in range(size):
            if prefix + n < len(blocks):
                gen[blocks[prefix + n]] = ordinal
            n += 1
    for k in range(prefix):        # untracked blocks: each its own generation
        gen[blocks[k]] = -1 - k
    steps: list[dict] = []
    for i in range(cut, len(messages)):
        gid = gen.get(i)
        kind = "verbatim" if gid is None else "generation"
        if not steps or steps[-1]["kind"] != kind or steps[-1].get("gen") != gid:
            steps.append({"kind": kind, "gen": gid, "messages": []})
        steps[-1]["messages"].append(_message(messages[i]))
    return steps


def _moves(messages: list[dict], cut: int) -> bool:
    """Whether the cut span contains one of the scene's OWN transitions."""
    return any(scenes_serialize.transition_kind(m) for m in messages[cut:])


def _pending(rec: dict) -> list[dict]:
    steps = rec.get("steps")
    done = rec.get("done", 0)
    if not isinstance(steps, list) or not isinstance(done, int):
        return []
    return [s for s in steps[max(done, 0):] if isinstance(s, dict)]


def _turns_left(rec: dict) -> int:
    return sum(1 for s in _pending(rec) if s.get("kind") == "generation")


def _pending_reply(cid: str, rec: dict) -> bool:
    """Whether a replayed reply is sitting in the transcript, unaccepted.

    The transcript past `mark` is the walk's uncommitted work, and `staged` is
    how much of that the ORIGINALS put there — so anything beyond the two is a
    reply the model wrote and nobody has answered for yet.

    This is a server fact and has to be, because it is the one thing standing
    between "run the next turn" and running it twice. A client that holds it in
    local state loses it on a reload and offers the button again; the second
    generation then lands beside the first, and one `accept` steps past both.
    Reported in `state` for the panel and enforced in `stage`.
    """
    try:
        landed = len(scenes_read.read_scene(cid, rec.get("scene", ""))["messages"])
    except (scenes_paths.SceneNotFound, campaigns_paths.CampaignNotFound):
        return False        # a scene that is gone owes nobody a verdict
    return landed > int(rec.get("mark", 0)) + max(int(rec.get("staged", 0)), 0)


def preview(cid: str, sid: str, index: int) -> dict:
    """What starting a replay at `index` would cost, without starting one.

    `fork` is the nudge (#80): a replay regenerates one model turn per
    generation step, so a long one is expensive in both money and wall clock and
    is the case where forking the campaign first — replaying into a copy, with
    the original left intact — is worth the disk. The threshold is
    configuration (`config.replay_fork_threshold`), not a constant here, so it
    can be tuned without a redeploy.

    `blocked` is non-empty when the replay would be refused; the caller shows it
    instead of the button rather than discovering it on the POST.
    """
    scene = scenes_read.read_scene(cid, sid)     # raises SceneNotFound
    messages = scene["messages"]
    if index < 1 or index >= len(messages):
        raise IndexError(index)
    sizes = scenes_turns._parse_turn_sizes(scene["meta"].get("turn_sizes", ""))
    steps = _segment(messages, index, sizes)
    turns = sum(1 for s in steps if s["kind"] == "generation")
    threshold = config.replay_fork_threshold()
    return {"posts": len(messages) - index, "turns": turns, "threshold": threshold,
            "fork": turns > threshold,
            "blocked": BLOCKED_TRANSITION if _moves(messages, index) else ""}


def state(cid: str) -> dict | None:
    """The live session as the client needs it, or None when there is none.

    The backlog itself is never returned. It is a transcript's worth of text
    that the client cannot act on — every step is taken by asking this store to
    take it — and the one thing a reviewer needs to see about the *next* step is
    which kind it is.
    """
    rec = read(cid)
    if not rec.get("steps"):
        return None
    pending = _pending(rec)
    nxt = pending[0]["kind"] if pending else "done"
    if rec.get("staged"):
        # The verbatim posts are already in the transcript; what is owed now is
        # the generation they were staged for.
        nxt = pending[1]["kind"] if len(pending) > 1 else "done"
    return {"scene": rec.get("scene", ""), "cut": rec.get("cut", 0),
            "done": rec.get("done", 0), "steps": len(rec.get("steps") or []),
            "turns_left": _turns_left(rec), "next": nxt,
            "staged": bool(rec.get("staged")),
            # Whether a replayed reply is waiting on a verdict. Derived here
            # rather than remembered by the client -- see `_pending_reply`.
            "pending": _pending_reply(cid, rec),
            "created": rec.get("created", ""),
            # A scene deleted under a running replay leaves a session nothing can
            # advance. Reported rather than silently cleared: the backlog is the
            # only copy of those posts, and dropping it on a READ would destroy
            # them without anyone asking.
            "gone": not scenes_paths._scene_path(cid, rec.get("scene", "")).exists()}


def begin(cid: str, sid: str, index: int) -> dict:
    """Cut the scene at `index`, holding everything from there on for replay.

    Returns the session with the cascade's own report under `cascade` — a
    replay's cut is `cascade.delete_from`, not a plain truncation, because
    everything the removed posts caused the absorb to write has to come back
    out with them. That is the same cut the gutter's delete makes, and the
    reviewer is owed the same report of what it could not reverse.

    The order inside the lock is load-bearing and runs opposite to the rest of
    this package: the backlog is written FIRST, because from the moment the cut
    lands this file is the only copy of those posts. A cut that then fails takes
    the session file back out — a session describing a scene that was never cut
    would replay posts still standing in the transcript, which is worse than the
    loss it was written to prevent.

    Raises `scenes.SceneNotFound`, `IndexError` for an index that would replay
    nothing (or would empty the scene), and `ReplayError` for a span this cannot
    honestly rebuild.
    """
    with locks.campaign_lock(cid):
        if read(cid).get("steps"):
            raise ReplayError("a replay is already running in this campaign")
        scene = scenes_read.read_scene(cid, sid)     # raises SceneNotFound
        messages = scene["messages"]
        if index < 1 or index >= len(messages):
            # `index < 1` rather than `< 0`: replaying from the first post would
            # leave an empty transcript with nothing for the model to answer.
            raise IndexError(index)
        if _moves(messages, index):
            raise ReplayError(BLOCKED_TRANSITION)
        sizes = scenes_turns._parse_turn_sizes(scene["meta"].get("turn_sizes", ""))
        steps = _segment(messages, index, sizes)
        if not any(s["kind"] == "generation" for s in steps):
            raise ReplayError("there is no model turn after that post to replay")
        rec = {"scene": sid, "cut": index, "created": now_iso(),
               "steps": steps, "done": 0, "mark": index, "staged": 0}
        _write(cid, rec)
        try:
            report = cascade.delete_from(cid, sid, index)
        except BaseException:
            _clear(cid)
            raise
        # `state(cid)`, not the record: the backlog is a transcript's worth of
        # text and the client can do nothing with it, which is the rule the GET
        # keeps -- returning it here because it happened to be in hand would
        # break that rule on the one response that carries the most of it. It
        # also means the two ways to learn a session's position give the same
        # shape, so nothing downstream has to know which call it came from.
        return {**(state(cid) or {}), "cascade": report}


def _append_steps(cid: str, sid: str, steps: list[dict]) -> int:
    """Write original steps back onto the transcript, exactly as they were.

    A generation goes through `append_reply`, so it lands with its turn
    boundary — appending its blocks one at a time would leave reroll counting
    back through a boundary that no longer describes a generation. Everything
    else is a post at a time.
    """
    written = 0
    for step in steps:
        if step.get("kind") == "generation":
            scenes_write.append_reply(
                cid, sid, [{"speaker": m["speaker"] or None, "content": m["content"]}
                           for m in step["messages"]])
        else:
            for m in step["messages"]:
                scenes_write.append_message(cid, sid, m["role"], m["content"],
                                            speaker=m["speaker"] or None)
        written += len(step["messages"])
    return written


def stage(cid: str) -> dict:
    """Append the next verbatim step to the transcript, if one is owed.

    Idempotent by construction: `staged` records that it has run, and the count
    it holds is what `accept` steps over. A caller that retries a failed
    generation calls this again and appends nothing.
    """
    with locks.campaign_lock(cid):
        rec = read(cid)
        pending = _pending(rec)
        if not pending:
            raise ReplayError("this replay has no steps left")
        sid = rec.get("scene", "")
        # The refusal that makes running a turn twice impossible, wherever the
        # second click came from -- a reload that lost the client's own memory
        # of having run it, a second tab, a stale panel. The reviewer answers
        # the reply that is there (accept it, or reroll it) before another is
        # generated on top of it.
        if _pending_reply(cid, rec):
            raise ReplayError("this replayed turn is waiting on you — accept it or "
                              "try it again before running the next one")
        if not rec.get("staged") and pending[0]["kind"] == "verbatim":
            for m in pending[0]["messages"]:
                scenes_write.append_message(cid, sid, m["role"], m["content"],
                                            speaker=m["speaker"] or None)
            # The COUNT, not a flag. `accept` has to tell a replayed reply from
            # the originals staged in front of it, and both raise the
            # transcript's length -- so the count is what its guard subtracts.
            rec["staged"] = len(pending[0]["messages"])
            _write(cid, rec)
        return rec


def accept(cid: str) -> dict | None:
    """Keep what was just generated and step past the original it replaced.

    Returns the session, or None when that was the last step and the session is
    over. Refuses when nothing new is in the transcript: the mark is what the
    walk has committed to, so accepting an empty step would drop an original
    turn and put nothing in its place — a deletion wearing the word "accept".
    """
    with locks.campaign_lock(cid):
        rec = read(cid)
        pending = _pending(rec)
        if not pending:
            raise ReplayError("this replay has no steps left")
        sid = rec.get("scene", "")
        landed = len(scenes_read.read_scene(cid, sid)["messages"])
        mark = int(rec.get("mark", 0))
        staged = max(int(rec.get("staged", 0)), 0)
        # `mark + staged`, not `mark`. The player's own posts are put back by
        # `stage` and raise the transcript's length on their own, so a guard
        # against `mark` would read them as a replayed reply — and accepting
        # then steps past an original model turn with nothing in its place,
        # which is a deletion wearing the word "accept".
        if landed <= mark + staged:
            raise ReplayError("nothing has been replayed yet to accept")
        # The verbatim step (if one was staged) and the generation it led to are
        # accepted together: they are one step of the walk to the reviewer, who
        # never saw the player's own post as a decision.
        taken = 1 if staged else 0
        step = taken + (1 if len(pending) > taken else 0)
        rec["done"] = int(rec.get("done", 0)) + step
        rec["staged"] = 0
        rec["mark"] = landed
        # A tail with no model turn left in it is not a step anybody reviews:
        # the scene ended on the player's own posts, and there is nothing to
        # regenerate against them. Written back and the session closed, rather
        # than left as a walk whose next button would do nothing.
        rest = _pending(rec)
        if not any(s.get("kind") == "generation" for s in rest):
            _append_steps(cid, rec.get("scene", ""), rest)
            _clear(cid)
            return None
        _write(cid, rec)
        return rec


def cancel(cid: str, restore: bool = True) -> dict:
    """End the replay. With `restore`, put the unreplayed originals back.

    Restoring is a cut and a re-append, in that order and under one lock: the
    transcript goes back to the last accepted step (dropping a fresh reply the
    reviewer never accepted, which is what "cancel" means at that point) and
    every remaining original is appended as it was — a generation through
    `append_reply`, so it lands with its turn boundary, and everything else
    through `append_message`.

    Without `restore` the walk simply stops: the transcript keeps what has been
    replayed so far and the originals are dropped. That is a real deletion and
    the caller asks for it explicitly.
    """
    with locks.campaign_lock(cid):
        rec = read(cid)
        if not rec.get("steps"):
            raise ReplayError("no replay is running in this campaign")
        sid = rec.get("scene", "")
        restored = 0
        if restore:
            landed = len(scenes_read.read_scene(cid, sid)["messages"])
            # Clamped, because `mark` is this store's memory of a transcript
            # somebody else may have shortened since -- the gutter's cut is one
            # click away and takes no notice of a running replay. Past the end,
            # `delete_from` would raise IndexError out of a cancel whose whole
            # job is to be the way back.
            mark = min(int(rec.get("mark", 0)), landed)
            if mark < landed:
                # Ahead of the truncation, like every other fence in this store:
                # the scene is un-absorbed for the walk's duration, so a review
                # CAN be opened mid-replay, and its token would still be valid
                # over a transcript this is about to replace.
                commits.retire_scene(cid, sid)
                scenes_write.delete_from(cid, sid, mark)
                # The two sidecars that key off post positions, cleaned exactly
                # as `cascade.delete_from` cleans them after ITS cut -- a raw
                # truncation is not the whole of a truncation anywhere else in
                # this store, and it is not here either. The transient-state
                # ledger would otherwise keep entries at indices the restored
                # originals now occupy, and the reroll sidecar could be left
                # anchored at a generation that has just been deleted, offering
                # a discarded replay's takes as alternates of the original
                # reply. Best-effort, and after the cut on purpose: the
                # transcript is already back, and neither sidecar is a reason to
                # fail a cancel that has done the thing it was asked to do.
                #
                # The sidecar goes UNCONDITIONALLY, where `cascade.delete_from`
                # first checks whether its cut moved the live variant. That
                # check has nothing to weigh here: a parked set belongs to the
                # trailing generation and the next post retires it (`stage`
                # alone does that), so the only set that can exist when this
                # runs is one parked on the uncommitted reply the truncation is
                # removing. A conditional would be machinery for a state this
                # walk cannot be in.
                for clean in (lambda: turnstate.supersede(cid, sid, mark),
                              lambda: alternates.drop_scene(cid, sid)):
                    try:
                        clean()
                    except Exception:  # a sidecar, cleaned after the fact
                        log.warning("replay cancel in %s/%s: cleanup failed", cid, sid,
                                    exc_info=True)
            restored = _append_steps(cid, sid, _pending(rec))
        _clear(cid)
        return {"scene": sid, "restored": restored, "dropped": 0 if restore else
                sum(len(s["messages"]) for s in _pending(rec))}


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow a renamed scene id. Registered in `scene_refs.repoint` for the
    reason every other store there is: the session names a scene by id, and a
    rename that left it behind would strand the only copy of that scene's
    original posts."""
    with locks.campaign_lock(cid):
        rec = read(cid)
        if rec.get("scene") in mapping:
            rec["scene"] = mapping[rec["scene"]]
            _write(cid, rec)
