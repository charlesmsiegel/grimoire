"""Cascade post-delete: cut a scene at a post and undo what the scene wrote (#75).

Two halves, and the second is the one that needed an argument.

**The cut.** `scenes.delete_from` slices the transcript at an index and takes
everything from there on. That much is mechanical.

**The reversal.** "Undo what the scene wrote" is bounded by what the continuity
pipeline actually tags with a scene id, and nothing may be reverted on a guess.
Two mechanisms carry that tag, and they are not equivalent:

- The **change journal** (`store/journal.py`, #31) holds one row per write-back
  the absorb pass landed, each carrying the reversal `store/undo.py` snapshotted
  *before* the write — the record's prior value, plus the value it read back
  afterwards. Reversing through it puts the actual value back, and refuses when
  the record has moved since (`undo`'s compare-and-swap). That is what makes this
  sound where the issue's own sketch was not: it proposed reapplying
  `changes.json`'s stored `before`, and noted the flaw — that log keeps only the
  LAST write-back per record, so a later scene touching the same record makes its
  `before` a lie. The journal keeps every row and refuses rather than guessing.
- The **scene-tagged stores** — `chronicle` (keyed by scene id), `plot` and
  `commitments` (`beats[].scene`), `changes` (`{"scene": sid, ...}`) and
  `provenance` (each citation's `scene`) — carry the id on the row itself, so
  "which of these belongs to this scene" is exact. They are swept after the
  journal pass, and they are what covers the writes the journal no longer can:
  retention bounds it (`journal.RETENTION` rows, `journal.MAX_BYTES`), so an old
  scene's rows may simply be gone.

**A record the scene CREATED is reported, not deleted.** `store/undo.py` declines
`new_character` / `new_location` / `new_lore` and points at this issue for the
cascade — and the cascade declines them too, on purpose. A created character has
since been cast into scenes, given a dossier, an appearance record and a locked
version, and may be named by lore and by later scenes' beats; deleting it here
would corrupt every one of those, and `overlay.forget_world_record` exists
because freeing its id hands the next create everything filed under it. So the
creation stands and the report names it, which is a state the player can act on:
deleting the record itself is a deliberate act with its own route.

What is deliberately NOT reverted, and why each one is the honest answer:

- **`relationships.json` beyond what the journal names.** The bonds file has no
  scene attribution of its own — `since_scene` is schema-present and never
  populated — and `scene_refs.repoint` does not fan out to it. A journalled
  feeling or bond IS attributable and is reversed with everything else; anything
  older than the journal reaches is not, and nothing here will invent a link.
- **`rolls.json`.** Append-only by design: an entry is never deleted and an id is
  never reused. The cut removes a manual roll's transcript LINE, so the record of
  the roll survives without the post that reported it. Said out loud in the
  route's response, because the player is entitled to know the ledger kept it.
- **`facts.json`.** The fact ledger models supersession rather than deletion —
  `undo.NOT_UNDOABLE` refuses a fact for exactly this reason, and retiring one is
  the supported move.
- **`timeline.md`.** Append-only dated lines with no scene field to match on.
- **`prompt_log`.** Frozen per-turn snapshots: an audit record of what was sent,
  which stays true whatever became of the reply.
- **`appearances`.** Each actor's `scenes` list records that they were cast into
  this scene, and they still were — the scene is shortened, not deleted, and its
  cast is not a consequence of the posts that went. A join or leave *is* narrated
  into the transcript, so the cut can remove the line announcing one; that is a
  deliberate stopping point rather than an oversight. `appear` locks the actor's
  version on first appearance and `leave` is what a re-add would have to undo, so
  reversing a join means unlocking a version other scenes may now be reading —
  a cascade well past what a player asked for by cutting a post. The scene's own
  setting and clock ARE rolled back, in `scenes.delete_from`: those are the
  scene's state, they are exactly derivable from the transition lines that
  survive, and they are read into every later prompt.
- **`audit` baselines.** The sheet as it stood when the scene opened, and the
  mechanism `undo.NOT_UNDOABLE` defers a sheet edit to. Dropping it would make
  that edit irreversible; keeping it is what lets a re-absorb of the shortened
  transcript compute its delta against the right starting point.
- **`scene_ideas`.** A saved idea's `used_scene` records which scene it became,
  and it still became this one.

That accounts for every store `scene_refs.repoint` fans out to. The list is the
point of this module rather than a courtesy: a store that persists a scene id and
is not named here is one nobody has decided about.

**The reversal is not gated on `meta.done`.** It could have been — an absorb is
the only thing that writes any of the above — but every step is driven purely by
what carries the scene's id, so an un-absorbed scene reverts nothing by
construction and a scene whose `done` was hand-cleared still gets cleaned up.
One less pair of things that can disagree.

**A cut anywhere in an absorbed scene un-absorbs the whole of it.** There is no
record of which post produced which claim — the chronicle summary is an
extraction over the transcript as a whole — so "the cut removed absorbed
content" is the only reading available, and re-running the absorb over what is
left is cheaper than a wrong partial reversal.

Everything runs under one `locks.campaign_lock(cid)` hold. The cut and the
reversal are not independently meaningful: a transcript truncated with its
chronicle record still standing is a scene that reads as finished and says
something else. Like `store/undo.py`, this module is deliberately absent from
`locks.DOMAIN_MODULES`: it writes no file of its own, so
`test_lock_domain_guard.py` does not survey it as a mutator, and every module it
delegates to is classified in its own right.
"""

from __future__ import annotations

import logging

from . import (alternates, changes, chronicle, commitments, commits, journal,
               locks, plot, provenance, turnstate, undo)
from .scenes import read as scenes_read, write as scenes_write

log = logging.getLogger(__name__)

#: What a refusal says when the failure was not one the reversal has words for.
UNREVERSIBLE = "this record could not be read or written, so it was left alone"


def _revert_journalled(cid: str, sid: str) -> tuple[int, list[dict]]:
    """Reverse this scene's journalled write-backs, newest first.

    Newest first because reversals compare against the *current* value: two rows
    touching one record were applied in order, and putting the older one back
    first would leave the newer row's `expect` matching nothing.

    Only `source == "absorb"` rows, and only ones not already undone. A row with
    `source == "undo"` is itself a reversal — reversing that is redo, which would
    reapply the very edit this is trying to remove. A manual edit carries no
    scene (`journal` writes `""` for one), so it cannot match here anyway; the
    filter is explicit rather than incidental.

    Ids are collected before the first reversal rather than iterated live:
    `undo.undo` APPENDS a row for the reversal it performs, tagged with the same
    scene, so a live iteration would find its own output and undo it again.

    A refusal is reported, never raised, and it covers two different situations
    the caller must not conflate: a record the compare-and-swap declined (a later
    scene, another device or a hand edit moved it since) and a kind that carries
    no reversal at all (a creation, a fact, a sheet). Neither is a reason to
    abandon the rest of the cut, and both leave a record holding what the deleted
    scene gave it — so each row carries the store's own words for why.
    """
    # Label taken from this one pass rather than re-read per row: `journal.get`
    # reads and parses the whole file, and that file is capped at `MAX_BYTES`
    # (2 MB) — one absorb of a long scene can journal dozens of rows, and a
    # re-read each is megabytes of parsing to recover a string already in hand.
    doomed = [(e["id"], e.get("label") or e.get("field") or e["id"])
              for e in journal.read(cid)
              if e.get("scene") == sid and e.get("source") == "absorb"
              and not e.get("undone") and isinstance(e.get("id"), str)]
    reverted, refused = 0, []
    for jid, label in reversed(doomed):
        try:
            undo.undo(cid, jid)
            reverted += 1
        except undo.UndoError as exc:
            # The store's own words. Every one of these is written for a reader
            # (`undo.NOT_UNDOABLE`, `undo.CONFLICT`), which is precisely what
            # this report needs.
            refused.append({"label": label, "reason": str(exc)})
        except Exception:  # one bad row must not sink the cut
            # NOT `str(exc)`. An arbitrary exception's text is a stack-level
            # detail — a bare KeyError renders as a quoted field name — and this
            # string goes into a banner in front of the player. The log keeps the
            # traceback for whoever can act on it.
            log.warning("could not reverse journal entry %s in %s", jid, cid, exc_info=True)
            refused.append({"label": label, "reason": UNREVERSIBLE})
    return reverted, refused


def delete_from(cid: str, sid: str, index: int) -> dict:
    """Delete the post at `index` and everything after it, then undo what the
    scene wrote. Returns a report of what happened.

    Raises `scenes.SceneNotFound` for an unknown scene and `IndexError` for an
    index that removes nothing; the route turns those into 404 and 400. **Those
    are the only two things it raises**, and that is the contract that matters:
    everything after the cut is guarded, because the cut is irreversible and a
    500 landing on top of it would leave a truncated transcript beside a
    chronicle record still describing the posts that went — the exact state this
    module's docstring says must not exist. A step that could not run is named in
    `failed` instead, the posture `absorb.apply_edits` takes with the display logs
    it writes after its edits have landed.

    That guard is not theoretical. `chronicle.read_chronicle`, `plot.read` and
    `commitments.read` each parse with a bare `json.loads`, and every store here
    is a hand-editable file the user owns — so one stray byte in `plot.json` is
    enough to reach it.

    Ordering is load-bearing at four points:

    - **The index is validated before anything happens.** `scenes.delete_from`
      checks it too, under the same held lock, so this is not a race — it is what
      makes a bad index cost nothing at all, including the epoch bump below.
    - **`commits.retire_scene` before the cut.** A review prepared from the
      pre-cut transcript is holding a valid commit token, and saving it would
      write the deleted posts' summary and edits straight back in. Retiring the
      scene's epoch fences it, and it goes FIRST for the reason
      `scenes.delete_scene` puts its own ledger writes ahead of the unlink: a
      fence that fails before the cut costs a request, where one that fails after
      leaves the transcript cut and the stale review free to save.
    - **The journal pass before the sweep.** `undo.undo` rolls the display panels
      back as it goes — it re-records a `changes.json` row, tagged with this
      scene — so a sweep that ran first would leave exactly the rows it exists to
      remove.
    - **The un-absorb last.** It is the flag that says the scene may be reviewed
      again; clearing it before the records it summarises have gone would invite
      a re-absorb onto half-reverted state.
    """
    with locks.campaign_lock(cid):
        scene = scenes_read.read_scene(cid, sid)       # raises SceneNotFound
        was_absorbed = str(scene["meta"].get("done", "")).lower() == "true"
        if index < 0 or index >= len(scene["messages"]):
            raise IndexError(index)
        # Which parked variant is live, read BEFORE the cut — see below. Broad,
        # because this is only an input to a cleanup decision: `_resolve` reads
        # the transcript and the scene's cast to derive it, and no failure in
        # that derivation is a reason to refuse the player's cut. An unreadable
        # answer takes the drop-it branch, which is the safe direction.
        try:
            live_before = alternates.state(cid, sid)["active"]
        except Exception:  # noqa: BLE001 — an underivable slot is treated as moved
            live_before = None
        commits.retire_scene(cid, sid)
        removed = scenes_write.delete_from(cid, sid, index)

        # ---- past here the transcript is gone, so nothing may raise ----
        failed: list[str] = []

        def step(name: str, fn):
            """Run one cleanup, or record that it could not run."""
            try:
                return fn()
            except Exception:  # the cut landed; this step did not
                log.warning("cascade delete in %s/%s: %s failed", cid, sid, name,
                            exc_info=True)
                failed.append(name)
                return None

        # The transient-state ledger from the cut on (#120): its entries are
        # keyed by post index, so every one at or past the cut describes a post
        # that no longer exists.
        step("turnstate", lambda: turnstate.supersede(cid, sid, index))

        # The reroll sidecar parks the variants of ONE generation, and the cut
        # decides its fate by whether it reached that far: a cut below it takes
        # the generation those variants are takes of, while a cut that only
        # removes what sits ABOVE it — trailing scene-transition lines, which the
        # anchor rule steps over — leaves the set as valid as it was.
        #
        # Read as "is the live variant still the live variant" rather than
        # computed from the index, because the anchor is alternates' own private
        # arithmetic — messages in front of the generation, transitions excluded
        # — and a second copy of it here would be one to keep in step. `active`
        # is the variant currently in the transcript; unchanged means the cut
        # never reached the generation.
        #
        # A set that was ALREADY unresolved (`live_before is None` — the state a
        # reroll whose stream died leaves) is dropped too, and that is a real if
        # small loss: those variants were still promotable. It is the safe
        # direction. The set claims a slot the cut has just moved, and leaving it
        # lets a scene played forward to the same message count adopt takes of a
        # generation the player deliberately erased — someone else's replies
        # offered as this reply's alternates.
        def drop_stale_alternates():
            if live_before is None or alternates.state(cid, sid)["active"] != live_before:
                alternates.drop_scene(cid, sid)
        step("alternates", drop_stale_alternates)

        # `_revert_journalled` catches per row, so this guard is for the journal
        # READ that feeds it — a garbled journal.json must not sink the sweep
        # below, which is what covers the same writes when the history cannot.
        reverted, refused = step("journal", lambda: _revert_journalled(cid, sid)) or (0, [])
        report = {"index": index, "removed": removed, "was_absorbed": was_absorbed,
                  "records": reverted, "refused": refused}
        # Each on its own, so a garbled `plot.json` does not cost the chronicle
        # record its deletion. A step that failed reports its count as zero and
        # names itself in `failed`; a zero with no name beside it means there was
        # nothing of this scene's there.
        for name, fn, empty in (
                ("chronicle", lambda: chronicle.forget(cid, sid), False),
                ("plot_beats", lambda: plot.forget_scene(cid, sid), 0),
                ("commitment_beats", lambda: commitments.forget_scene(cid, sid), 0),
                ("changes", lambda: changes.forget_scene(cid, sid), 0),
                ("citations", lambda: provenance.forget_scene(cid, sid), 0)):
            result = step(name, fn)
            report[name] = empty if result is None else result
        if was_absorbed:
            step("unabsorb", lambda: scenes_write.unmark_absorbed(cid, sid))
        report["failed"] = failed
        return report
