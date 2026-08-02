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

An answered row is **not** waved through on the strength of the flag. It also
carries ``resolve_from``, the value the reviewer was shown, and stays under the
same comparison against that snapshot -- `resolve` is permission to overwrite
one specific text, not permission to overwrite whatever is there by the time
the retry lands. See `_basis`.

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

#: Why an already-answered row is back. One line rather than a second table:
#: what matters to the reviewer is not which kind moved -- they are looking at
#: the row -- but that it moved *after* they answered, so the value they were
#: shown is no longer the one they would be overwriting.
_RECHECK_REASON = ("this changed again after you answered — the value you were "
                   "shown is not what is stored now")

#: Kinds whose `before`/`after` are the stored field's own literal text, so a
#: merged draft can be written straight back into it. The rest render a value
#: (a feeling, a bond type, a plot status + beat, a weather axis) into a line
#: that is not what apply writes, and offering merge for them would invite the
#: reviewer to save the rendering.
MERGEABLE: frozenset[str] = frozenset({"character_state", "group_state", "lore", "authored"})

#: The two resolutions that authorize a write over a detected conflict.
RESOLUTIONS: frozenset[str] = frozenset({"replace", "merge"})


def resolved(edit: dict) -> bool:
    """Whether the reviewer already authorized this edit over a conflict.

    The `isinstance` is not decoration: `edits` reaches us straight off a client
    PUT body, `ChronicleSave` validates only that each item is a dict, and
    ``[] in frozenset(...)`` raises rather than returning False.
    """
    if not isinstance(edit, dict):
        return False
    resolve = edit.get("resolve")
    return isinstance(resolve, str) and resolve in RESOLUTIONS


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
    # `target`/`payload` are read INSIDE the guard, not above it. They come
    # off a client PUT body that is validated only as "a dict", so a `target`
    # that is a string or a list makes `.get` raise -- and raising out here
    # would turn one malformed row into a 500 for the whole save, where
    # `apply_edits` has always skipped such a row under its best-effort
    # contract. Unreadable and malformed land in the same place: no verdict.
    try:
        target = edit.get("target") or {}
        tid = target.get("id") or ""
        payload = edit.get("payload") or {}
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
    except Exception:  # noqa: BLE001 -- an unreadable or malformed target proves
        return None                   # no contradiction
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


def _basis(edit: dict) -> tuple[str, bool] | None:
    """The value this edit claims its target holds, and whether that claim is a
    reviewer's answer rather than the staged proposal. None when the edit makes
    no claim at all -- the opt-out no read can overrule.

    An **answered** row is judged against ``resolve_from``, the value the
    reviewer was actually shown, and not against `before`. `resolve` authorizes
    overwriting *that specific text*; the record can move again between the
    refusal and the retry, and treating the flag alone as standing permission
    would let the retry overwrite a value nobody ever saw -- exactly the lost
    update this module exists to stop. A resolution carrying no snapshot keeps
    the older unconditional meaning, so a client that does not send one still
    saves.
    """
    if resolved(edit):
        seen = edit.get("resolve_from")
        return (seen, True) if isinstance(seen, str) else None
    if "before" not in edit:
        return None
    return (edit.get("before") or "", False)


def conflict_row(cid: str, edit: dict) -> dict | None:
    """The conflict this edit is in, or None. Carries everything the reviewer
    needs to choose without a second round-trip: what is stored, what was
    proposed, and the merged draft when merging makes sense for the kind."""
    if not isinstance(edit, dict):
        return None
    kind = edit.get("kind")
    # A `kind` that is not a string is malformed input rather than an unknown
    # kind, and an unhashable one raises out of the lookup. Same boundary as the
    # `target` read in `current_value`.
    if not isinstance(kind, str) or kind not in _REASONS:
        return None
    claim = _basis(edit)
    if claim is None:
        return None
    basis, rechecking = claim
    stored = current_value(cid, edit)
    if stored is None or stored == basis:
        return None
    after, mergeable = edit.get("after", ""), kind in MERGEABLE
    return {"id": edit.get("id", ""), "label": edit.get("label", ""), "kind": kind,
            "field": edit.get("field", ""), "before": edit.get("before") or "",
            "after": after, "stored": stored, "mergeable": mergeable,
            "reason": _RECHECK_REASON if rechecking else _REASONS[kind],
            # Diffed from the BASIS, not from `before`: on a recheck the basis is
            # the text the reviewer was looking at, so basis -> after is their
            # own edit and nothing else.
            "merged": merge_text(basis, after, stored) if mergeable else after}


def batch_verdicts(cid: str, edits: list[dict]) -> list[dict | None]:
    """One verdict per edit, positionally.

    Positional rather than keyed by edit id so two rows cannot collide on a
    blank or duplicated id -- `materialize` dedupes only plot threads, so two
    lore or relationship proposals naming one target really can share an id --
    and computed in a single pass **before** any of the batch is written:
    applying edit A can move the record edit B was staged against, and a check
    interleaved with the writes would report B as conflicted with the batch's
    own work.
    """
    return [conflict_row(cid, e) for e in edits]


def check_conflicts(cid: str, edits: list[dict]) -> list[dict]:
    """Every conflicted edit in the batch, in batch order. A row the reviewer
    already answered comes back only if its target moved since they answered."""
    return [v for v in batch_verdicts(cid, edits) if v is not None]
