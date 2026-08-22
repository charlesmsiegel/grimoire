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

from .. import (
    changes,
    characters,
    commitments,
    facts,
    groupstate,
    overlay,
    playstate,
    plot,
    relationships,
)
from .. import weather as weather_store
from ..appearances import paths as appearances_paths
from ..appearances import versions as appearances_versions
from ..campaigns import paths as campaigns_paths

#: Why each kind is conflicted, in the reviewer's words. Membership *is* the set
#: of kinds this module can judge: a kind absent here is never reported, which
#: is how `dossier`, `sheet` and the `new_*` rows (whose target does not exist
#: yet, so there is nothing to have drifted) stay out. The `dossier` wording in
#: `apply.apply_edits` deliberately reads as one more line of this table.
_REASONS: dict[str, str] = {
    "character_state": "this character's state changed since the scene was absorbed",
    "group_state": "this group's state changed since the scene was absorbed",
    "lore": "this record changed since the scene was absorbed",
    "authored": "this card field changed since the scene was absorbed",
    "relationship": "this relationship changed since the scene was absorbed",
    "bond": "this bond changed since the scene was absorbed",
    "plot": "this plot thread changed since the scene was absorbed",
    "commitment": "this commitment changed since the scene was absorbed",
    "fact": "the fact this row retires changed since the scene was absorbed",
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


def _text(value) -> str | None:
    """A textual field as a string, or None when it is not one.

    JSON ``null`` reads as empty -- a client's way of saying "nothing was
    stored". Anything else that is not a string (a dict, a list, a number) is
    malformed input and gets no verdict, the same boundary `current_value`
    keeps around `target`: `merge_text` diffs these fields through
    `changes.line_diff`, whose `.splitlines()` would otherwise raise out of the
    whole save.
    """
    if value is None:
        return ""
    return value if isinstance(value, str) else None


def replaced_value(edit: dict) -> str:
    """The value this edit's write actually replaces.

    For an answered row that is `resolve_from`, the text the reviewer was
    looking at -- not the staged `before`, which is what the record said at
    absorb time and has since been superseded. `apply_edits` logs the Changes
    entry from this: diffing an answered edit against the staged `before` would
    render text that was already stored as part of what the edit added.
    """
    if resolved(edit):
        seen = edit.get("resolve_from")
        if isinstance(seen, str):
            return seen
    return _text(edit.get("before")) or ""


def plot_line(thread: dict) -> str:
    """A plot thread's current position as one line: its status, plus its most
    recent beat when it has one. Shared with `materializer.materialize` so the
    `before` it stages and the value checked against it cannot drift apart."""
    beats = thread.get("beats") or []
    if beats:
        return f"{thread.get('status', 'open')} — {beats[-1]['text']}"
    return thread.get("status", "open")


def commitment_line(rec: dict) -> str:
    """A commitment's current position as one line: its kind, its status, the
    deadline if it has one, and its most recent beat. `plot_line`'s sibling, and
    shared with `materializer.materialize` for the same reason — the `before` it
    stages and the value checked against it here cannot be allowed to drift
    apart.

    The KIND is in the line because `kind` is a field an absorb can change and
    the reviewer approves the row on what they can see. A reclassification from
    `threat` to `promise` shows in the staged label, which names the resulting
    kind; without the stored one here there is nothing to read it against, and
    the row looks like an ordinary beat. Same reasoning that put `due` here.

    The trailing stamp -- how many beats the record has and where it last moved
    -- is what makes this a fingerprint rather than a description. Kind, status,
    deadline and latest beat TEXT can all be identical across two different
    movements: a second absorb that produces the same sentence ("She missed the
    payment.") leaves every visible field unchanged, so a review staged before
    it would read as unmoved and apply, appending its older-scene beat AFTER the
    newer one and rewinding `last_scene` -- the exact lost update this module
    exists to stop, in the one place the rendering could not see it. The beat
    count and `last_scene` move on every movement by construction, so they close
    it. They are shown to the reviewer rather than checked behind their back:
    the staged `before` and the value checked against it are one string on
    purpose (see the module docstring), and "last moved in ..." is the same
    thing the ledger tells them anyway.

    Every field is coerced rather than trusted: commitments.json is
    hand-editable and read by a bare `json.loads`, so a list-valued `status`
    would otherwise be concatenated into the line and raise. `plot_line` does
    not do this and predates the concern -- and carries the fingerprint gap
    above for the same reason; the difference is deliberate rather than an
    inconsistency to tidy away, since changing plot's behaviour belongs to
    whoever hardens `plot.read` (see the PR discussion).
    """
    def _field(value) -> str:
        return value.strip() if isinstance(value, str) else ""

    due = _field(rec.get("due"))
    head = (f"{_field(rec.get('kind')) or 'promise'}, "
            f"{_field(rec.get('status')) or 'open'}"
            + (f", due {due}" if due else ""))
    beats = rec.get("beats")
    beats = beats if isinstance(beats, list) else []
    last = beats[-1] if beats else None
    text = _field(last.get("text")) if isinstance(last, dict) else ""
    line = f"{head} — {text}" if text else head
    scene = _field(rec.get("last_scene"))
    stamp = f"{len(beats)} beat{'' if len(beats) == 1 else 's'}"
    return f"{line} [{stamp}{f', last moved in {scene}' if scene else ''}]"


def fact_line(rec: dict) -> str:
    """A ledger fact's current position as one line: its lifecycle state, what
    superseded it if anything, and its text. `plot_line`'s sibling, and shared
    with `materializer.materialize` for the same reason -- the `before` it
    stages and the value checked against it here cannot drift apart.

    No beat count and no scene stamp, unlike `commitment_line`, because neither
    would say anything a fact ledger cannot already prove. A fact's text is
    immutable by design (`store/facts.py`) and so is its date, so `status` and
    `superseded_by` are the ONLY fields any mutator can move -- the line is a
    complete fingerprint rather than an approximate one. That also keeps a scene
    id out of the staged `before`, which is what spares the review panel the
    rename-repointing `commitment_line`'s trailing stamp forces on it.

    Every field is coerced rather than trusted: facts.json is hand-editable and
    read by a bare `json.loads`, so a list-valued `status` would otherwise be
    concatenated into the line and raise.
    """
    status = facts._field(rec.get("status"), facts.ACTIVE)
    superseded_by = facts._field(rec.get("superseded_by"))
    text = facts._field(rec.get("text"))
    head = status + (f" (superseded by {superseded_by})" if superseded_by else "")
    return f"{head} — {text}" if text else head


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
        if kind == "commitment":
            cur = commitments.get(cid, tid)
            return commitment_line(cur) if isinstance(cur, dict) else ""
        if kind == "fact":
            # A fact row's target is the fact it RETIRES, and a row that retires
            # nothing (an ordinary new fact, which overwrites no record) names
            # none -- so there is nothing to have drifted, and no verdict, the
            # same silence `new_lore` and the other creations keep by staying
            # out of `_REASONS` entirely. This kind cannot: the same kind covers
            # both, and only the row can say which it is.
            if not tid:
                return None
            cur = facts.get(cid, tid)
            return fact_line(cur) if isinstance(cur, dict) else ""
        if kind == "weather":
            now = weather_store.current_weather(cid, payload.get("location", ""),
                                                payload.get("native"))
            if now is None:
                return None   # unparseable moment: the case the resolver declines
            return now.get(edit.get("field", ""))
    except Exception:  # noqa: BLE001 -- an unreadable or malformed target proves
        return None                   # no contradiction
    return None


def target_key(edit: dict) -> tuple | None:
    """Which record `current_value` would read this edit's value FROM, as a
    hashable key. None when the edit names no judgeable record.

    Two edits with equal keys address one record, so a value written by one is
    what the other's read returns. `apply._outside_drift` needs exactly that: a
    resumed commit recognises its own earlier writes, and a value alone cannot
    tell them apart from an outside change to a DIFFERENT record that happens to
    hold the same text -- which is not exotic, since "" and other common state
    values collide readily.

    Every component is stringified, because these come off a client PUT body:
    a `target` id that arrives as a dict would otherwise make the key
    unhashable and raise out of the set it goes into.

    This tracks the dispatch in `current_value` and is kept honest by
    `test_every_judged_kind_has_a_target_key` -- a kind added to `_REASONS`
    without a key here fails that test rather than quietly losing the
    protection.
    """
    if not isinstance(edit, dict):
        return None
    kind = edit.get("kind")
    if not isinstance(kind, str) or kind not in _REASONS:
        return None
    try:
        target = edit.get("target") or {}
        payload = edit.get("payload") or {}
        field = str(edit.get("field", ""))
        # `commitment` keys like `plot` and for the same reason: both are a
        # whole-record line rendered from one id, so two edits naming the same id
        # address the same record.
        # `fact` keys off its target like the four above, and for the same
        # reason: the record it can move is the one its target names -- the fact
        # it retires. A row that retires nothing keys as ("fact", ""), which
        # names no record and can match nothing, because `current_value` never
        # returns a reading for one and `apply_edits` only ever pairs this key
        # with a reading.
        if kind in ("character_state", "group_state", "plot", "commitment", "fact"):
            return (kind, str(target.get("id", "")))
        if kind == "lore":
            return (kind, str(target.get("kind", "")), str(target.get("id", "")))
        if kind == "authored":
            return (kind, str(target.get("id", "")), field)
        if kind == "relationship":
            return (kind, str(payload.get("from", "")), str(payload.get("to", "")))
        if kind == "bond":
            # Not normalised into an unordered pair even though `get_bond` treats
            # it as one: a key that is merely too FINE costs a false conflict the
            # reviewer resolves, where one too coarse costs a silent overwrite.
            return (kind, str(payload.get("a", "")), str(payload.get("b", "")))
        if kind == "weather":
            return (kind, str(payload.get("location", "")),
                    str(payload.get("native")), field)
    except Exception:  # noqa: BLE001 -- a malformed target names no record
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
    before = _text(edit.get("before"))
    return None if before is None else (before, False)


def survey(cid: str, edit: dict) -> tuple[dict | None, str | None]:
    """This edit's conflict row, and what its target read while judging it.

    The reading comes back even when there is no conflict, because
    `apply.apply_edits` journals it (#271): a resumed commit compares the target
    against the value the interrupted attempt judged, which is how it tells an
    outside write from its own earlier edits. ``None`` means no reading was
    taken at all -- an unjudged kind, a row making no claim, a target that would
    not read -- and nothing can be proven about that row's drift either.
    """
    if not isinstance(edit, dict):
        return None, None
    kind = edit.get("kind")
    # A `kind` that is not a string is malformed input rather than an unknown
    # kind, and an unhashable one raises out of the lookup. Same boundary as the
    # `target` read in `current_value`.
    if not isinstance(kind, str) or kind not in _REASONS:
        return None, None
    claim = _basis(edit)
    if claim is None:
        return None, None
    basis, rechecking = claim
    after = _text(edit.get("after"))
    if after is None:
        return None, None   # malformed: nothing to diff, and apply cannot write it either
    stored = current_value(cid, edit)
    if stored is None or stored == basis:
        return None, stored
    mergeable = kind in MERGEABLE
    return {"id": edit.get("id", ""), "label": edit.get("label", ""), "kind": kind,
            "field": edit.get("field", ""), "before": _text(edit.get("before")) or "",
            "after": after, "stored": stored, "mergeable": mergeable,
            "reason": _RECHECK_REASON if rechecking else _REASONS[kind],
            # Diffed from the BASIS, not from `before`: on a recheck the basis is
            # the text the reviewer was looking at, so basis -> after is their
            # own edit and nothing else.
            "merged": merge_text(basis, after, stored) if mergeable else after}, stored


def conflict_row(cid: str, edit: dict) -> dict | None:
    """The conflict this edit is in, or None. Carries everything the reviewer
    needs to choose without a second round-trip: what is stored, what was
    proposed, and the merged draft when merging makes sense for the kind."""
    return survey(cid, edit)[0]


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


def batch_survey(cid: str, edits: list[dict]) -> tuple[list[dict | None], list[str | None]]:
    """`batch_verdicts`, plus the reading each verdict was drawn from.

    Two lists rather than one of pairs because both are journalled positionally
    and read back positionally; see `survey` for what the readings are for.
    """
    rows = [survey(cid, e) for e in edits]
    return [row for row, _ in rows], [stored for _, stored in rows]


def check_conflicts(cid: str, edits: list[dict]) -> list[dict]:
    """Every conflicted edit in the batch, each stamped with its `index` in the
    submitted batch. A row the reviewer already answered comes back only if its
    target moved since they answered.

    The stamp is what makes a client able to put a verdict back on the right
    row. Order alone is not enough: this drops the unconflicted rows, so with
    two edits sharing an id -- which `materialize` allows for everything except
    plot threads -- a client walking its own rows and matching on id would hand
    the *second* row's conflict to the *first*, then answer the wrong proposal.
    """
    return [{**v, "index": i}
            for i, v in enumerate(batch_verdicts(cid, edits)) if v is not None]
