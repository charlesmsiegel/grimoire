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
    doomed = [e.get("id") for e in journal.read(cid)
              if e.get("scene") == sid and e.get("source") == "absorb"
              and not e.get("undone") and isinstance(e.get("id"), str)]
    reverted, refused = 0, []
    for jid in reversed(doomed):
        entry = journal.get(cid, jid) or {}
        label = entry.get("label") or entry.get("field") or jid
        try:
            undo.undo(cid, jid)
            reverted += 1
        except undo.UndoError as exc:
            refused.append({"label": label, "reason": str(exc)})
        except Exception as exc:  # one bad row must not sink the cut
            log.warning("could not reverse journal entry %s in %s", jid, cid, exc_info=True)
            refused.append({"label": label, "reason": str(exc)})
    return reverted, refused


def delete_from(cid: str, sid: str, index: int) -> dict:
    """Delete the post at `index` and everything after it, then undo what the
    scene wrote. Returns a report of what happened.

    Raises `scenes.SceneNotFound` for an unknown scene and `IndexError` for an
    index that removes nothing; the route turns those into 404 and 400.

    Ordering is load-bearing at three points:

    - The **cut goes first**. It is the operation the player asked for and the
      only one that can fail on the index, so a bad index costs nothing. It is
      also the irreversible half: reverting continuity first and then failing to
      write the transcript would leave a scene whose posts claim things the
      records no longer hold.
    - The **journal pass runs before the sweep**. `undo.undo` rolls the display
      panels back as it goes — it re-records a `changes.json` row, tagged with
      this scene — so a sweep that ran first would leave exactly the rows it
      exists to remove.
    - **`commits.retire_scene` before the un-absorb.** A review prepared from the
      pre-cut transcript is still holding a valid commit token, and saving it
      would write the deleted posts' summary and edits straight back in. Retiring
      the scene's epoch is what fences it, and it is the same call
      `scenes.delete_scene` makes for the same reason.
    """
    with locks.campaign_lock(cid):
        meta = scenes_read.read_scene_meta(cid, sid)   # raises SceneNotFound
        was_absorbed = str(meta.get("done", "")).lower() == "true"
        # Which parked variant is live, read BEFORE the cut — see below.
        try:
            live_before = alternates.state(cid, sid)["active"]
        except OSError:
            live_before = None
        removed = scenes_write.delete_from(cid, sid, index)

        # The transient-state ledger from the cut on (#120): its entries are
        # keyed by post index, so every one at or past the cut describes a post
        # that no longer exists. Never fatal — the transcript is already written,
        # the same judgement `edit_message`'s route and
        # `remove_trailing_assistant_run` both make about this file.
        try:
            turnstate.supersede(cid, sid, index)
        except OSError:
            pass

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
        # A set that was ALREADY unresolved (`before is None` — the state a
        # reroll whose stream died leaves) is dropped too, and that is a real if
        # small loss: those variants were still promotable. It is the safe
        # direction. The set claims a slot the cut has just moved, and leaving it
        # lets a scene played forward to the same message count adopt takes of a
        # generation the player deliberately erased — someone else's replies
        # offered as this reply's alternates.
        try:
            if live_before is None or alternates.state(cid, sid)["active"] != live_before:
                alternates.drop_scene(cid, sid)
        except OSError:
            pass

        reverted, refused = _revert_journalled(cid, sid)
        report = {
            "index": index, "removed": removed, "was_absorbed": was_absorbed,
            "records": reverted, "refused": refused,
            "chronicle": chronicle.forget(cid, sid),
            "plot_beats": plot.forget_scene(cid, sid),
            "commitment_beats": commitments.forget_scene(cid, sid),
            "changes": changes.forget_scene(cid, sid),
            "citations": provenance.forget_scene(cid, sid),
        }
        commits.retire_scene(cid, sid)
        if was_absorbed:
            scenes_write.unmark_absorbed(cid, sid)
        return report
