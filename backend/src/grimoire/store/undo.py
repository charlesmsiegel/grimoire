"""True undo of a journalled write (#31).

`store/journal.py` keeps the history; this module is what makes an entry in it
*reversible*. The two halves are deliberately far apart in time: the reversal is
computed at write time, when the record still holds the value the write is about
to replace, and executed much later, when the reader clicks Undo.

**Snapshot, not inverse operation.** Each reversal is
``{"target": <descriptor>, "restore": <prior value>, "expect": <value after the
write>}``. It would be tidier to store the write's arguments and run them
backwards, and for the text kinds that is what it amounts to -- but three of the
kinds here have no inverse call at all. `plot.set_movement` appends a beat and
may move the title and status in one go; `relationships.set_feeling` cannot
express "there was nothing here"; `commitments.set_movement` does both. So the
value itself is kept, and the owning module grew a `restore` that puts one
record back (`plot.restore`, `commitments.restore`,
`relationships.restore_feeling` / `restore_bond`). Every reversal still lands
through the module that owns the record -- nothing here writes a file.

**The guard is what makes it safe, and it is a compare-and-swap.** `expect` is
read back immediately after the original write, and undo refuses unless the
record still reads exactly that. That is the same discipline
`absorb/conflicts.py` applies on the way in, for the same reason: between the
edit and the reversal, a later absorb, a manual edit or another device sharing
the store can have moved the record, and writing `restore` over that would be a
silent lost update -- the reader asked to undo one edit, not to discard
everything since. A refusal is a 409 the reader can act on; a silent overwrite
is not.

Comparison is on the value `read_value` returns, never on the display text the
journal shows. For a card that value carries the character's currently-locked
version alongside the field text, so a version swapped in through
`import-version` since the edit fails the compare rather than having the old
text written into whichever version is locked now.

**Kinds that carry no reversal.** `probe` returns None for them and the journal
entry says why in `why`, rather than offering an Undo button that fails:

- ``fact`` -- the ledger already models supersession, and `facts.record` retires
  one record while writing another. Un-recording a fact would mean deleting a
  ledger entry, which is the one thing that ledger exists not to do.
- ``weather`` / ``sheet`` -- both own their own conflict contract
  (`weather/overrides.py`, `audit/apply.py`), and a reversal that did not go
  through it would be a second, weaker definition of the same rule.
- ``new_character`` / ``new_location`` / ``new_lore`` -- undoing a creation is a
  deletion, and a created character has already been cast into the scene, given
  a dossier and an appearance record. That cascade is #75's subject, not this
  module's.
- a ``commitment`` whose id the write reallocated (`materializer.
  _new_commitment_id` moves off a taken id), because the record the snapshot was
  taken from is then not the record that was written.

`undo` takes ``locks.campaign_lock(cid)`` across the whole read-check-write-stamp
-- the compare-and-swap is not one otherwise, since two readers clicking Undo on
the same entry would both pass the check. It is deliberately NOT declared in
``locks.DOMAIN_MODULES``: nothing here writes a file, so
``test_lock_domain_guard.py`` does not survey this module as a mutator at all
(its mutation surface is filesystem calls, and it does not follow imports), and
a declaration it cannot check would be a phantom entry. The modules this
delegates to are each classified in their own right.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from . import (changes, characters, commitments, dossiers, groupstate, journal,
               locks, overlay, playstate, plot, relationships, voice_drift)
from .appearances import paths as appearances_paths, versions as appearances_versions
from .campaigns import paths as campaigns_paths

log = logging.getLogger(__name__)


class UndoError(Exception):
    """Base for everything a reversal can refuse over."""


class EntryNotFound(UndoError):
    pass


class AlreadyUndone(UndoError):
    pass


class NotUndoable(UndoError):
    pass


class UndoConflict(UndoError):
    pass


#: Why a kind carries no reversal, in the reader's words. Membership *is* the
#: set of kinds this module declines on purpose -- `probe` returning None for a
#: kind absent here means an edit whose target could not be named, which
#: `GENERIC` covers.
NOT_UNDOABLE: dict[str, str] = {
    "fact": "a ledger fact cannot be un-recorded — retire it instead, so the "
            "record of what was believed and when survives",
    "weather": "weather is resolved from the campaign's climate and its "
               "overrides; edit the override rather than undoing this",
    "sheet": "a sheet edit is reversed through the sheet itself, which keeps "
             "its own baseline of what each scene changed",
    "new_character": "undoing a created character means deleting it, along with "
                     "the scene appearance and dossier it was given",
    "new_location": "undoing a created location means deleting it",
    "new_lore": "undoing a created lore entry means deleting it",
}

GENERIC = "this change did not record enough to be reversed"

CONFLICT = ("this record changed after the edit you are undoing — undoing now "
            "would overwrite that later change")

UNREADABLE = "this record can no longer be read, so there is nothing to put back"


def why(kind) -> str:
    """The message an entry with no reversal carries."""
    return NOT_UNDOABLE.get(kind, GENERIC) if isinstance(kind, str) else GENERIC


def probe(cid: str, edit: dict) -> dict | None:
    """A descriptor naming the record `edit` is about to write, or None when the
    kind carries no reversal.

    Read off the edit rather than off the write, so it can be taken *before* the
    write happens -- which is the only moment the prior value still exists. The
    one kind whose write can move off this record (`commitment`, whose id
    `apply` may reallocate) is dropped by the caller, which can see both ids.
    """
    if not isinstance(edit, dict):
        return None
    kind = edit.get("kind")
    target = edit.get("target")
    target = target if isinstance(target, dict) else {}
    payload = edit.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    tid = target.get("id")
    tid = tid if isinstance(tid, str) else ""
    field = edit.get("field")
    field = field if isinstance(field, str) else ""
    if kind == "character_state" and tid:
        return {"w": "state", "id": tid}
    if kind == "group_state" and tid:
        return {"w": "group_state", "id": tid}
    if kind == "dossier" and tid:
        return {"w": "dossier", "id": tid}
    if kind == "voice_drift" and tid:
        return {"w": "voice_drift", "id": tid}
    if kind == "lore" and tid:
        ekind = target.get("kind")
        return {"w": "entity", "kind": ekind, "id": tid} if isinstance(ekind, str) and ekind else None
    if kind == "authored" and tid:
        # Pinned to the version this write lands in, not re-resolved at undo
        # time: `read_value` reports the currently-locked version beside the
        # field, so a swap since the edit fails the compare instead of writing
        # the old text into a version nobody was looking at.
        vid = appearances_versions.locked_version(cid, "characters", tid)
        return {"w": "card", "id": tid, "version": vid, "field": field} if vid and field else None
    if kind == "relationship":
        a, b = payload.get("from"), payload.get("to")
        return {"w": "feeling", "from": a, "to": b} if isinstance(a, str) and isinstance(b, str) else None
    if kind == "bond":
        a, b = payload.get("a"), payload.get("b")
        return {"w": "bond", "a": a, "b": b} if isinstance(a, str) and isinstance(b, str) else None
    if kind == "plot" and tid:
        return {"w": "plot", "id": tid}
    if kind == "commitment" and tid:
        return {"w": "commitment", "id": tid}
    return None


def read_value(cid: str, target: dict):
    """What the named record holds right now, as a JSON-comparable value.

    Raises for a record that will not read -- a caller journalling a snapshot
    treats that as "no reversal", and a caller performing one treats it as a
    refusal. Both are better than a None that compares equal to a genuinely
    absent record.
    """
    croot = campaigns_paths.campaign_root(cid)
    w = target.get("w")
    if w == "state":
        st = playstate.read_state(croot, target["id"])
        return playstate.compose_body(st["current_state"], st["knows"], st["suspects"]) if st else ""
    if w == "group_state":
        st = groupstate.read_state(croot, target["id"])
        return groupstate.compose_body({k: st[k] for k in groupstate.FIELDS}) if st else ""
    if w == "dossier":
        return dossiers.read(croot, target["id"])
    if w == "voice_drift":
        return voice_drift.read_record(croot, target["id"])
    if w == "entity":
        return overlay.read_entity(cid, target["kind"], target["id"])["body"]
    if w == "card":
        # `locked_actor_root`, the same root `absorb.apply` writes the card
        # through: an appeared actor's campaign copy is authoritative at its
        # locked version, and nothing else may be read off a raw campaign root.
        card = characters.read_card(appearances_paths.locked_actor_root(cid),
                                    target["id"], target["version"])
        return {"locked": appearances_versions.locked_version(cid, "characters", target["id"]),
                "text": card["data"].get(target["field"], "")}
    if w == "feeling":
        return relationships.get_feeling(cid, target["from"], target["to"])
    if w == "bond":
        return relationships.get_bond(cid, target["a"], target["b"])
    if w == "plot":
        return plot.get(cid, target["id"])
    if w == "commitment":
        return commitments.get(cid, target["id"])
    raise UndoError(f"no reversal is defined for {w!r}")


def write_value(cid: str, target: dict, value) -> None:
    """Put `value` back, through the module that owns the record."""
    croot = campaigns_paths.campaign_root(cid)
    w = target.get("w")
    if w == "state":
        playstate.write_state(croot, target["id"], value or "")
    elif w == "group_state":
        groupstate.write_state(croot, target["id"], value or "")
    elif w == "dossier":
        dossiers.write(croot, target["id"], value or "")
    elif w == "voice_drift":
        record = value if isinstance(value, dict) else {}
        voice_drift.write(croot, target["id"], record.get("note", ""), record.get("anchor", ""))
    elif w == "entity":
        overlay.update_entity(cid, target["kind"], target["id"], body=value or "")
    elif w == "card":
        root = appearances_paths.locked_actor_root(cid)
        card = characters.read_card(root, target["id"], target["version"])
        # Only the field this edit moved. The rest of the card may have been
        # edited since through the version route, and a whole-document restore
        # would take those with it.
        card["data"][target["field"]] = (value or {}).get("text", "")
        characters.update_version(root, target["id"], target["version"], card)
    elif w == "feeling":
        relationships.restore_feeling(cid, target["from"], target["to"], value)
    elif w == "bond":
        relationships.restore_bond(cid, target["a"], target["b"], value)
    elif w == "plot":
        plot.restore(cid, target["id"], value)
    elif w == "commitment":
        commitments.restore(cid, target["id"], value)
    else:
        raise UndoError(f"no reversal is defined for {w!r}")


def snapshot(cid: str, edit: dict) -> tuple[dict | None, object]:
    """The record `edit` is about to move, and what it holds now. Never raises.

    Called on the applying path, before the write. A probe that cannot read its
    target yields no reversal rather than sinking an edit the reviewer approved:
    the write itself is the point, and the history is the thing that can be
    missing.
    """
    try:
        target = probe(cid, edit)
        return (target, read_value(cid, target)) if target is not None else (None, None)
    except Exception:  # noqa: BLE001 — an unreadable target records no reversal
        return None, None


def seal(cid: str, target: dict, restore) -> dict | None:
    """The reversal to journal, completed by reading the record back after the
    write. None when it stopped reading, for the same reason `snapshot` is
    tolerant."""
    try:
        return {"target": target, "restore": restore, "expect": read_value(cid, target)}
    except Exception:  # noqa: BLE001 — no post-write reading, so nothing to compare against
        return None


def _display(value) -> str:
    """A snapshot value as the panel's diff text. The two writers `journalled`
    serves both hold plain bodies; anything else journals a row with no diff
    rather than raising out of a write that already succeeded."""
    return value if isinstance(value, str) else ""


@contextmanager
def journalled(cid: str, target: dict, *, kind: str, ref: dict, field: str, label: str):
    """Journal a hand edit made outside the absorb pipeline (#31).

    `absorb.apply` was the only writer that recorded anything, so every edit
    typed into the app went unlogged and unreversible -- the gap this closes for
    the campaign routes whose write is one text body through one of the writers
    above. The record is read before the block and again after it, which is the
    same snapshot-and-seal pair the absorb path uses; `scene` is "" because a
    hand edit belongs to no scene.

    A failure to journal never propagates. The block's write has already
    happened by then, and losing the history of an edit is a smaller harm than
    500-ing the edit itself -- the same call `apply_edits` makes when
    `changes.record` fails after its writes landed.
    """
    before = None
    try:
        before = read_value(cid, target)
    except Exception:  # noqa: BLE001 — no prior reading, so no reversal to offer
        target = None
    yield
    if target is None:
        return
    try:
        plan = seal(cid, target, before)
        if plan is not None and plan["expect"] == before:
            # The route wrote, but not to the value this row would describe --
            # an entity PUT that only moved `keys` or `owners`, or a save of an
            # unedited form. Journalling it would put a row with an empty diff
            # and a no-op Undo button in front of the reader for every save.
            return
        journal.append(cid, [{
            "scene": "", "source": "manual", "kind": kind, "ref": ref,
            "field": field, "label": label,
            "before": _display(before),
            "after": _display(plan["expect"]) if plan else "",
            "undo": plan, "why": "" if plan else UNREADABLE}])
    except Exception:  # the edit landed; only its history did not
        log.warning("could not journal a manual edit to %s in %s", ref, cid,
                    exc_info=True)


def undo(cid: str, jid: str) -> dict:
    """Reverse one journalled change and journal the reversal. Returns the new
    entry -- which carries its own reversal, so undoing it again is redo.

    The whole check-write-stamp runs under the campaign lock, so two readers
    clicking Undo on the same entry cannot both pass the compare-and-swap: the
    loser finds the record already moved (or the entry already stamped) and is
    refused.
    """
    with locks.campaign_lock(cid):
        entry = journal.get(cid, jid)
        if entry is None:
            raise EntryNotFound(jid)
        if entry.get("undone"):
            raise AlreadyUndone(jid)
        plan = entry.get("undo")
        target = plan.get("target") if isinstance(plan, dict) else None
        if not isinstance(target, dict):
            raise NotUndoable(entry.get("why") or why(entry.get("kind")))
        try:
            stored = read_value(cid, target)
        except Exception as exc:  # a vanished record is a refusal, not a 500
            raise UndoConflict(UNREADABLE) from exc
        if stored != plan.get("expect"):
            raise UndoConflict(CONFLICT)
        write_value(cid, target, plan.get("restore"))
        # The reversal is itself a change, journalled like any other -- and its
        # own reversal is the pair read backwards, which is what makes redo fall
        # out rather than needing a second mechanism.
        row = {"scene": entry.get("scene", ""), "source": "undo",
               "kind": entry.get("kind", ""), "ref": entry.get("ref"),
               "field": entry.get("field", ""),
               "label": entry.get("label", ""),
               "before": entry.get("after", ""), "after": entry.get("before", ""),
               "reverted": jid,
               "undo": seal(cid, target, plan.get("expect"))}
        row["why"] = "" if row["undo"] else UNREADABLE
        written = journal.append(cid, [row])[0]
        journal.mark_undone(cid, jid, written["id"])
        _roll_back_changes(cid, entry, row)
        return written


def _roll_back_changes(cid: str, entry: dict, row: dict) -> None:
    """Point the rolling per-record delta (`changes.json`) at the reversal.

    That log means "how this record last moved", and after a reversal it last
    moved back -- leaving it alone would have the Records panel describing a
    change that is no longer in the record. It can only ever be describing THIS
    change anyway: the compare-and-swap above has already established that
    nothing has written to the record since, and every absorb write-back
    journals, so the entry `changes.json` holds for a browsable record is the one
    just undone.

    Scoped to the same kinds that log there, and only for an entry that came
    from a scene: `changes.record` labels its entry with a scene id, and a manual
    edit has none to give. The scene named is the one whose change was reversed,
    which is the truest label available -- the reversal itself happened outside
    any scene, and the History panel carries the exact story either way.

    Never fatal. The reversal has already landed by the time this runs, and a
    stale display log is the smaller harm -- the call `apply_edits` makes for the
    same trade.
    """
    ref = entry.get("ref")
    ref = ref if isinstance(ref, dict) else {}
    kind, rid, sid = ref.get("kind"), ref.get("id"), entry.get("scene")
    if (entry.get("kind") not in changes.BROWSABLE_KINDS
            or not isinstance(kind, str) or not kind
            or not isinstance(rid, str) or not rid
            or not isinstance(sid, str) or not sid):
        return
    try:
        changes.record(cid, sid, {f"{kind}/{rid}": [
            {"field": row.get("field", ""), "label": row.get("label", ""),
             "before": row.get("before", ""), "after": row.get("after", "")}]})
    except Exception:  # the reversal landed; only the panel is stale
        log.warning("could not roll back the changes panel for %s in %s", ref, cid,
                    exc_info=True)
