"""The end-of-scene review, held on disk between generating it and saving it.

A chat turn's output *is* the transcript: it lands, and the run that produced
it has nothing left to hold. A review's output is a form the reviewer then
reads, edits and saves -- so once `POST /absorb` stopped being the thing that
carries it back to the browser (#396), somewhere had to keep it. Losing it
costs the whole end-of-scene generation, which is the longest single sequence
in the app; losing a turn costs a cheap retry. Asymmetric value, asymmetric
treatment, and this is the durable half.

At most one review per scene, in a sidecar beside the transcript
(`<sid>.review.json`) like the reroll alternates -- so a scene that is deleted
takes its review with it, and the id-recycling hazard both files share is
handled in one place (`scenes.paths._sid_taken`).

Three things ride with the payload, and none of them is decoration:

* **the generation** -- which absorb run produced this review. Cancel
  (`DELETE .../pending-review`) names it, so a Cancel that races a finishing
  absorb cannot delete the record and then have the runner recreate it; and a
  retry that merges into the review checks it, so a retry answering after a
  *fresh* absorb replaced the review cannot fold its phase into the new one.
* **the watermark** -- what the transcript looked like when the review was
  built. The commit epoch does not cover this: `commits.reserve` is called
  from exactly one place (`PUT /chronicle`), so playing on after a review
  lands -- appending turns, cutting posts, retconning -- advances nothing, and
  the review's token would still pass every check while summarising a
  transcript that has moved. See `watermark`.
* **the sid** -- what it was prepared for. Belt and braces after a repoint:
  the file moves with its scene, and a record that disagrees with its own path
  is one nobody should act on.

`merge_audit` / `merge_dossiers` are here rather than in the routes because
they are what the *stored* review means: a retry of one phase replaces that
phase and the edit rows that phase owns, and nothing else. Reproducing what
`useSceneReview` does on screen is not a nicety -- a stored merge that took
more would silently discard staged work the reviewer had already approved, and
one that took less would reopen showing a phase status the retry has since
superseded.
"""

from __future__ import annotations

import contextlib
import hashlib
import json

from . import atomic, locks
from .paths import now_iso
from .scenes import paths as scenes_paths

SCHEMA = 1
"""Stamped into every record. A file without it was written by something else."""


class CorruptReviewError(Exception):
    """The stored review is there and is not a review.

    Distinct from "no review" on purpose. A hand-edited or truncated file
    cannot be repaired by asking again, so the reviewer has to be told to
    re-run rather than shown an empty End Scene panel -- which reads as "the
    absorb never happened" and invites them to spend the whole budget twice.
    """


class NoPendingReviewError(Exception):
    """A retry had nothing to merge into.

    `post_audit` and `post_dossiers` return a *part* of a review --
    `{mechanics, edits}` and `{dossiers, edits}` -- so there is no such thing
    as one standing on its own: writing either whole would destroy the absorb's
    prose, its staged edits and its `commit_token`, and the token is the part
    nothing else can reconstruct.
    """


class ReviewReplacedError(Exception):
    """The review this run was retrying is no longer the stored one.

    A fresh absorb replaced it while the retry was in flight. Folding the
    retry's phase into the new review would report a step that ran against the
    *old* one, so it is refused and the retry's work is dropped.
    """


def _path(cid: str, sid: str):
    return scenes_paths._review_path(cid, sid)


def watermark(messages: list[dict]) -> dict:
    """What the transcript looked like, as the review's own record of it.

    Digested from role, speaker and content -- the three fields the transcript
    is rendered from, so every mutation a scene has (an append, an edit, a
    retcon, a cut, a promoted alternate) moves it, and nothing else does. The
    post timestamps are deliberately out: they are not part of what the model
    read, and a re-stamp would fail a review that describes exactly the text it
    was built from.

    The count rides along beside the digest because a digest can only say
    "different". The count is what lets the refusal say *how* -- "three posts
    were added since" is actionable where "the scene changed" is a shrug.
    """
    h = hashlib.sha256()
    for m in messages:
        h.update(json.dumps(
            [m.get("role", ""), m.get("speaker") or "", m.get("content", "")],
            ensure_ascii=False).encode("utf-8"))
        h.update(b"\x1e")
    return {"count": len(messages), "digest": h.hexdigest()}


def _read_raw(cid: str, sid: str) -> dict | None:
    """The stored record, or ``None`` when there is none.

    Raises `OSError` for a file that is there and would not open, and
    `CorruptReviewError` for one that opened and is not a review. Neither is
    softened to "no review", and the reason is the one `attempts._read`
    records: on the *answering* path a soft failure tells the reviewer their
    absorb is gone when it is merely locked by a sync client for a moment, and
    on the *mutating* path it makes a retry fold its phase into a review it
    could not read -- which is a write against state nobody has seen.
    """
    p = _path(cid, sid)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise CorruptReviewError(str(exc)) from exc
    if not isinstance(data, dict) or data.get("v") != SCHEMA \
            or not isinstance(data.get("review"), dict):
        raise CorruptReviewError("not a pending review")
    return data


def read(cid: str, sid: str) -> dict | None:
    """This scene's pending review record, or ``None``.

    Under the campaign lock, because the callers that act on what it says --
    the retrieval route's watermark check, the save's -- must not have the
    record change between the read and the decision. Reentrant, so a caller
    already holding it pays a recursive acquire.
    """
    with locks.campaign_lock(cid):
        record = _read_raw(cid, sid)
    return record


def publish(cid: str, sid: str, generation: str, review: dict,
            mark: dict) -> dict:
    """Store `review` as this scene's pending review, replacing any other.

    Replacing rather than merging: this is a *fresh* absorb, whose prose,
    staged edits and commit token supersede whatever was there. The caller
    holds the campaign lock across its own cancellation check and this write
    (see `routes.scenes`), which is what stops a Cancel that landed a moment
    ago from being undone by the run it was cancelling.
    """
    record = {"v": SCHEMA, "sid": sid, "generation": generation,
              "watermark": mark, "created": now_iso(), "review": review}
    with locks.campaign_lock(cid):
        atomic.write_text(_path(cid, sid),
                          json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return record


def merge(cid: str, sid: str, generation: str, fold) -> dict:
    """Fold a retry's result into the stored review, under one lock hold.

    `fold` takes the stored review payload and returns the merged one. It runs
    inside the hold so the read, the fold and the write are one step: two
    retries answering at once would otherwise each read the pre-merge review
    and the second write would drop the first's phase.

    Raises `NoPendingReviewError` when there is nothing stored and `ReviewReplacedError`
    when what is stored belongs to a different absorb -- both refusals rather
    than writes, because a retry owns one phase of one review and can say
    nothing about any other.
    """
    with locks.campaign_lock(cid):
        record = _read_raw(cid, sid)
        if record is None:
            raise NoPendingReviewError(sid)
        if record.get("generation") != generation:
            raise ReviewReplacedError(sid)
        merged = {**record, "review": fold(record["review"])}
        atomic.write_text(_path(cid, sid),
                          json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
    return merged


def clear(cid: str, sid: str, generation: str | None = None) -> bool:
    """Drop this scene's pending review. Returns whether a record went.

    Idempotent by design: the reviewer's Cancel and a save that has already
    completed both call this, and neither is a failure when there is nothing
    left to remove -- the intent is satisfied either way.

    With `generation`, only a record from that absorb is removed. Without one,
    whatever is there goes: that is the caller who owns the scene rather than
    one review of it -- a save, a cut, a delete.

    A record that will not parse is removed by an unconditional clear and left
    by a targeted one. It names no generation, so it cannot be shown to be the
    one the reviewer asked to discard; leaving it costs a re-run, and the next
    absorb overwrites it.
    """
    with locks.campaign_lock(cid):
        if generation is not None:
            try:
                record = _read_raw(cid, sid)
            except CorruptReviewError:
                return False
            if record is None or record.get("generation") != generation:
                return False
        try:
            _path(cid, sid).unlink()
        except FileNotFoundError:
            return False
    return True


def drop_scene(cid: str, sid: str) -> None:
    """Discard a live scene's pending review (a cut, a retcon, a save).

    `scenes.delete_scene` unlinks this path itself rather than calling here,
    and deliberately -- it runs that unlink before the transcript's, because a
    scene id is recycled and an orphan left behind would be adopted by the next
    scene to take the id. Nothing here is recycling an id.
    """
    clear(cid, sid)


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids: carry each review to its scene's new id.

    A rename is ordinary use here rather than an exotic race -- once a review
    has landed its scene is no longer held, so renaming a scene before saving
    its review is exactly what a reviewer does -- and without this the review
    sits orphaned under the old id while `GET .../{new_sid}/pending-review`
    answers 404.

    The shape is `alternates.repoint_scenes`', and for its reasons: read every
    source before writing any target so a swapped mapping cannot land one
    review on top of another; publish before clearing so a crash leaves the
    record readable at one path or the other; and never raise, because the
    caller has *already renamed the transcript* by the time this runs and the
    rest of `scene_refs.repoint` still owes half a dozen stores their new id.
    A review that could not be carried is left where it is -- an orphan
    `_sid_taken` already declines to hand out -- rather than deleted.

    Not shared with `alternates`: that module's destination clear is allowed to
    raise (inheriting someone else's parked *transcripts* has no harmless
    reading), and this one's is not, so the two differ exactly where it counts.
    """
    with locks.campaign_lock(cid):
        moving, stranded = {}, set()
        for old in mapping:
            try:
                moving[old] = _path(cid, old).read_bytes()
            except FileNotFoundError:
                continue
            except OSError:
                # Unreadable, and the transcript has already moved. Leaving the
                # file is the only answer that does not turn "we could not
                # carry your review across" into "we deleted it".
                stranded.add(old)
        published = set()
        # Destinations that are themselves sources go last: publishing over one
        # would destroy the last durable copy of what it still owes elsewhere.
        for old in sorted(moving, key=lambda o: mapping[o] in moving):
            try:
                atomic.write_bytes(_path(cid, mapping[old]), moving[old])
            except OSError:
                stranded.add(old)
                continue
            published.add(mapping[old])
        for sid in (*mapping, *mapping.values()):
            if sid in published or sid in stranded:
                continue
            # A source whose bytes are already published, or a destination
            # whose orphan will not go. Either costs one stale file that
            # `_sid_taken` keeps out of circulation; raising would abort the
            # fan-out and strand every other store on an id whose scene is gone.
            with contextlib.suppress(OSError):
                _path(cid, sid).unlink(missing_ok=True)


def _phase_row(rows: list, name: str, block: dict) -> list:
    """`phases` with one row re-projected from the block that reports it.

    The row is a projection of the block (`routes.scenes._phase_report`), so it
    has to move with it: left alone, the panel goes on reporting a budget that
    ran out for a step this retry has since run.
    """
    keys = ("status", "reason", "attempted", "budget_exhausted")
    return [{**r, **{k: block.get(k) for k in keys}}
            if isinstance(r, dict) and r.get("name") == name else r
            for r in rows]


def merge_audit(review: dict, result: dict) -> dict:
    """The audit retry's fold: fresh `mechanics`, fresh sheet rows, nothing else.

    Only the `sheet` rows are replaced. The audit is the phase that proposes
    them and it proposes all of them, so dropping the old ones is exact -- and
    touching any other kind would discard prose, dossier or relationship edits
    the reviewer has already been through.
    """
    mechanics = result.get("mechanics") or {}
    kept = [e for e in review.get("edits", []) if e.get("kind") != "sheet"]
    return {**review, "mechanics": mechanics,
            "phases": _phase_row(review.get("phases", []), "audit", mechanics),
            "edits": kept + list(result.get("edits", []))}


def merge_dossiers(review: dict, result: dict) -> dict:
    """The dossier retry's fold: fresh `dossiers`, and only the rows it re-proposed.

    `proposed` is the phase's own list of who it prepared a dossier for -- the
    same list its status is computed from -- so this cannot drift from what the
    panel says beside it. Rows for NPCs this run did *not* re-propose are kept
    deliberately: that is what makes a partly-failed dossier phase recoverable
    at all, and an unconditional rebuild would let a retry that failed for one
    NPC delete their perfectly good proposal from the first pass and put
    nothing in its place.
    """
    dossiers = result.get("dossiers") or {}
    reproposed = set(dossiers.get("proposed") or [])
    kept = [e for e in review.get("edits", [])
            if e.get("kind") != "dossier"
            or (e.get("target") or {}).get("id") not in reproposed]
    return {**review, "dossiers": dossiers,
            "phases": _phase_row(review.get("phases", []), "dossiers", dossiers),
            "edits": kept + list(result.get("edits", []))}
