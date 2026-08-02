"""Contradiction detection: does the record still say what the proposal was
staged against?

`materializer.materialize` computes each StagedEdit's `before` from the campaign
copies at absorb time; `apply.apply_edits` writes its `after` back at SAVE time,
which can be much later -- the review panel survives a scene switch, two reviews
can be open on the same NPC, and a second device can be writing into the same
synced store. Between those two moments the record can move, and every kind
whose apply *replaces* a stored value (state bodies, lore bodies, card fields,
feelings, bonds) would overwrite that movement without a word.

This module is the deterministic layer of #111: recompute the stored value and,
when it no longer equals the staged `before`, call the edit **conflicted**. No
LLM and no guessing -- it reports only what it can prove by reading the record
back. The reviewer then picks one of three:

- **keep** -- the stored value stands. Purely a client-side choice: a kept edit
  is simply not sent, so nothing here needs to represent it.
- **replace** -- write the staged `after` anyway.
- **merge** -- write text the reviewer assembled from both sides, prefilled by
  `merge_text`.

`replace` and `merge` reach the store as the *same* authorization -- a
``resolve`` key on the edit -- and differ only in what ends up in `after`. Two
names rather than one because they are different decisions to the human making
them, and because the panel offers merge only where `before`/`after` are the
field's own text (`MERGEABLE`): a plot row's `before` is a rendering of status
plus last beat, so pasting it into the beat textarea would write the rendering
back as a beat.

Two deliberate silences, both so the check never invents a conflict it cannot
prove:

- An edit with **no `before` key at all** has no staged basis, so no drift can
  be shown and none is claimed. Every edit `materialize` emits carries one; a
  hand-written batch that omits it opts out of the check. An `before` that is
  present and empty is a real basis ("nothing was stored") and is checked.
- A target that cannot be **read** yields no verdict (`current_value` returns
  None). Turning a failed read into a reported contradiction would recast a
  missing entity -- which `apply_edits` has always tolerated by skipping -- as a
  conflict the reviewer has to resolve.

Two kinds sit outside this module on purpose. `dossier` has carried its own
staged-`before` guard since #235, inside the handler that also owns its
existence check and its I/O error contract, and splitting that apart would lose
those; its reason string is phrased to match the family below. `sheet` edits
carry `expect` values and are resolved by `audit/apply.py`, which owns the sheet
conflict contract.
"""

from __future__ import annotations

from .. import (changes, characters, groupstate, overlay, playstate, plot,
                relationships, weather as weather_store)
from ..appearances import (paths as appearances_paths,
                           versions as appearances_versions)
from ..campaigns import paths as campaigns_paths

#: Why each kind is conflicted, in the reviewer's words. Membership *is* the set
#: of kinds this module can judge: a kind absent here is never reported, which
#: is how `dossier`, `sheet` and the `new_*` rows (whose target does not exist
#: yet, so there is nothing to have drifted) stay out. The `dossier` wording in
#: `apply.apply_edits` deliberately reads as one more line of this table.
_REASONS: dict[str, str] = {
    "character_state": "this character's state changed since the scene was absorbed",
    "group_state": "this group's state changed since the scene was absorbed",
    "lore": "this entry changed since the scene was absorbed",
    "authored": "this card field changed since the scene was absorbed",
    "relationship": "this relationship changed since the scene was absorbed",
    "bond": "this bond changed since the scene was absorbed",
    "plot": "this plot thread changed since the scene was absorbed",
    "weather": "the weather here changed since the scene was absorbed",
}

#: Kinds whose `before`/`after` are the stored field's own literal text, so a
#: merged draft can be written straight back into it. The rest render a value
#: (a feeling, a bond type, a plot status + beat, a weather axis) into a line
#: that is not what apply writes, and offering merge for them would invite the
#: reviewer to save the rendering.
MERGEABLE: frozenset[str] = frozenset({"character_state", "group_state", "lore", "authored"})

#: The two resolutions that authorize a write over a detected conflict.
RESOLUTIONS: frozenset[str] = frozenset({"replace", "merge"})


def resolved(edit: dict) -> bool:
    """Whether the reviewer already authorized this edit over a conflict."""
    return isinstance(edit, dict) and edit.get("resolve") in RESOLUTIONS


def plot_line(thread: dict) -> str:
    """A plot thread's current position as one line: its status, plus its most
    recent beat when it has one. Shared with `materializer.materialize` so the
    `before` it stages and the value checked against it cannot drift apart."""
    beats = thread.get("beats") or []
    if beats:
        return f"{thread.get('status', 'open')} — {beats[-1]['text']}"
    return thread.get("status", "open")


def current_value(cid: str, edit: dict) -> str | None:
    """What the edit's target says right now, or None when there is no verdict
    to give -- an unjudgeable kind, or a target that would not read."""
    if not isinstance(edit, dict):
        return None
    kind = edit.get("kind")
    target = edit.get("target") or {}
    tid = target.get("id") or ""
    payload = edit.get("payload") or {}
    try:
        if kind == "character_state":
            st = playstate.read_state(campaigns_paths.campaign_root(cid), tid)
            if not st:
                return ""
            return playstate.compose_body(st["current_state"], st["knows"], st["suspects"])
        if kind == "group_state":
            st = groupstate.read_state(campaigns_paths.campaign_root(cid), tid)
            if not st:
                return ""
            return groupstate.compose_body({k: st[k] for k in groupstate.FIELDS})
        if kind == "lore":
            return overlay.read_entity(cid, target.get("kind", ""), tid)["body"].strip()
        if kind == "authored":
            vid = appearances_versions.locked_version(cid, "characters", tid)
            if not vid:
                return None   # unlocked actor: apply would not find a card either
            card = characters.read_card(appearances_paths.locked_actor_root(cid), tid, vid)
            return (card["data"].get(edit.get("field", ""), "") or "").strip()
        if kind == "relationship":
            cur = relationships.get_feeling(cid, payload.get("from", ""), payload.get("to", ""))
            return relationships._render_feeling(cur) if cur else ""
        if kind == "bond":
            cur = relationships.get_bond(cid, payload.get("a", ""), payload.get("b", ""))
            return cur["type"] if cur else ""
        if kind == "plot":
            cur = plot.read(cid).get(tid)
            return plot_line(cur) if isinstance(cur, dict) else ""
        if kind == "weather":
            now = weather_store.current_weather(cid, payload.get("location", ""),
                                                payload.get("native"))
            if now is None:
                return None   # unparseable moment: the case the resolver declines
            return now.get(edit.get("field", ""))
    except Exception:  # noqa: BLE001 -- an unreadable target proves no contradiction
        return None
    return None


def merge_text(before: str, after: str, stored: str) -> str:
    """Prefill for the **merge** choice: the stored text with whatever the
    proposal *added* appended, so the reviewer trims rather than retypes.

    Additions are the insert side of `changes.line_diff` -- the same diff the
    panel renders -- minus any line the stored text already has, which is what
    keeps the common case (a lore append landing on a body someone else has
    since appended to) from repeating itself. A proposal that added no new line
    of its own leaves the stored text alone; there is nothing to carry over, and
    the reviewer can still edit the box by hand.
    """
    have = set(stored.splitlines())
    fresh = [d["text"] for d in changes.line_diff(before, after)
             if d["op"] == "insert" and d["text"].strip() and d["text"] not in have]
    if not fresh:
        return stored
    return (stored.rstrip() + "\n\n" + "\n".join(fresh)).strip()


def conflict_row(cid: str, edit: dict) -> dict | None:
    """The conflict this edit is in, or None. Carries everything the reviewer
    needs to choose without a second round-trip: what is stored, what was
    proposed, and the merged draft when merging makes sense for the kind."""
    if not isinstance(edit, dict) or "before" not in edit:
        return None
    reason = _REASONS.get(edit.get("kind", ""))
    if reason is None:
        return None
    stored = current_value(cid, edit)
    before = edit.get("before") or ""
    if stored is None or stored == before:
        return None
    kind, after = edit["kind"], edit.get("after", "")
    mergeable = kind in MERGEABLE
    return {"id": edit.get("id", ""), "label": edit.get("label", ""), "kind": kind,
            "field": edit.get("field", ""), "before": before, "after": after,
            "stored": stored, "reason": reason, "mergeable": mergeable,
            "merged": merge_text(before, after, stored) if mergeable else after}


def batch_verdicts(cid: str, edits: list[dict]) -> list[dict | None]:
    """One verdict per edit, positionally.

    Positional rather than keyed by edit id so two rows cannot collide on a
    blank or duplicated id, and computed in a single pass **before** any of the
    batch is written: applying edit A can move the record edit B was staged
    against, and a check interleaved with the writes would report B as
    conflicted with the batch's own work.
    """
    return [None if resolved(e) else conflict_row(cid, e) for e in edits]


def check_conflicts(cid: str, edits: list[dict]) -> list[dict]:
    """Every conflicted edit in the batch, in batch order. Rows the reviewer has
    already resolved (`replace`/`merge`) are authorized and never reported."""
    return [v for v in batch_verdicts(cid, edits) if v is not None]
