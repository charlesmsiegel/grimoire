"""Parsed section orchestration, standing-fact retire/record staging and new-entity/character display/provenance construction."""

from __future__ import annotations

from collections.abc import Callable

from ... import entities, errors, facts, fieldtext, overlay
from ...campaigns import paths as campaigns_paths
from ...paths import slugify
from .. import conflicts, membership, parse, routing, staging, weather
from . import _continuity_rows, _contracts


def record_taken(cid: str, kind: str, name: str) -> str | None:
    """Why a new `kind` record named `name` would duplicate one the campaign
    already has -- the same name-or-slug test staging dedupes on -- or None.

    Asked again at apply, because staging asked it of the row as the model
    wrote it: the reviewer can change a `new_lore` row's kind (and its name),
    and an entry staged as lore may land as an item the campaign already has.
    `create_entity` would then suffix the slug and make a quiet duplicate."""
    name = name.strip()
    noun = _contracts._NEW_RECORD_NOUNS.get(kind, kind)
    noun = ("an " if noun[0] in "aeiou" else "a ") + noun
    if name.lower() in {ent["name"].strip().lower() for ent in overlay.list_entities(cid, kind)}:
        return f"this campaign already has {noun} named {name}"
    try:
        overlay.read_entity(cid, kind, slugify(name))
    except entities.EntityNotFound:
        return None
    return f"this campaign already has {noun} with the id {slugify(name)}"


def _recorded_here(ledger: dict, sid: str, text: str) -> bool:
    """Whether this scene has EVER recorded this standing fact.

    Ever, not "and it is still standing" -- the same rule `facts.record`'s own
    dedup keeps, and for the reason spelled out there: a fact this scene
    recorded and a LATER scene retired is invisible to an active-only lookup, so
    re-absorbing this scene would stage the sentence again and put a truth the
    later scene ended back on the ledger. The scene id is what separates a
    re-extraction from a genuine re-establishment, so status has no work to do
    in this predicate.

    Case-insensitive, like `_new_commitment_id` compares titles: the two absorbs
    of one scene are two model replies, and a re-extraction that differs only in
    capitalisation is the same fact rather than a second one.
    """
    return bool(facts.find(ledger, sid, text))


def _fact_label(text: str, supersedes: str, date: str) -> str:
    """What a fact row is doing, in the reviewer's words.

    Deliberately short and carrying no fact text of its own: the row's
    before/after IS the two facts, rendered as a diff, so repeating either here
    would only give the panel a second, truncated copy to disagree with.
    """
    label = "Fact retired" if not text else ("Fact superseded" if supersedes else "New fact")
    return f"{label} — {date}" if date else label


def _fact_predecessor(ledger: dict, sid: str, text: str, sup: str) -> tuple[str, dict | None] | None:
    """Keep a live, scene-visible predecessor, or drop a mere restatement."""
    prior = ledger.get(sup) if sup else None
    if text and facts.is_active(prior) and facts.restates(prior, text):
        # A RESTATEMENT, not a supersession -- see `facts.restates` for what
        # it costs. The prompt says not to report one and the model does
        # anyway, which is why this is in code rather than only in the
        # prompt. Nothing about the world moved, so the row does not either.
        #
        # `apply` checks again rather than trusting this: the reviewer can
        # edit the replacement text into a restatement after the row was
        # staged, and that path never comes back through here.
        #
        # `text and` guards the BARE RETIREMENT, whose "" would otherwise
        # match a stored fact whose own text reads as "" -- which `record`
        # never writes but a hand-edited or malformed record supplies, and
        # that is exactly the record most worth being able to retire.
        return None
    if not facts.is_active(prior) or facts.recorded_after(prior, sid):
        # A `supersedes` naming a fact that is retired, missing or malformed
        # is dropped rather than obeyed. The snapshot offers only standing
        # facts, so the model was never shown that record, and retiring an
        # already-retired fact would overwrite the pointer saying what
        # really replaced it.
        #
        # So is one recorded AFTER this scene, which `facts.record` will
        # refuse to write (see `recorded_after`): the snapshot is scoped to
        # this scene precisely so the model is not offered it, and a row
        # that names one anyway has to be STAGED as what it will actually
        # do. Left labelled a supersession, the reviewer approves a
        # retirement that silently does not happen.
        #
        # The row's other half survives either way: what is left is an
        # ordinary new fact, or -- when the row carried no text either --
        # nothing, and it is dropped below.
        sup, prior = "", None
    return sup, prior


def _fact_edits(cid: str, sid: str, rows: list[dict], stamp: staging.Stamp) -> list[dict]:
    """Stage the ledger's recordings and retirements without duplicate work."""
    out: list[dict] = []
    # The fact ledger (#114). One section and one edit kind covering two
    # operations, because a row does one thing to one record and only the row
    # can say which: text RECORDS a standing fact -- retiring the fact it names,
    # if it names one -- and a bare `supersedes` retires that fact outright,
    # with nothing put in its place. Splitting them would double the contract
    # the model has to hold, the branch `apply` has to write and the vocabulary
    # the reviewer has to read, to distinguish two rows that already read
    # differently in the diff.
    try:
        ledger = facts.read(cid)
    except Exception:  # noqa: BLE001 — garbled facts.json: skip these, don't 500
        ledger = None
    if not isinstance(ledger, dict):
        # An UNREADABLE ledger stages nothing, for the reason spelled out over
        # `owed` in materialize: falling back to {} would stage every supersession as an
        # ordinary new fact, hiding the retirement the reviewer approved behind
        # a row that claims to retire nothing.
        return out
    retiring: set[str] = set()
    staged_texts: set[str] = set()   # folded text of the new facts THIS batch stages
    for n, e in enumerate(rows):
        text, date = fieldtext.text(e.get("text")), fieldtext.text(e.get("date"))
        sup = fieldtext.text(e.get("supersedes"))
        predecessor = _fact_predecessor(ledger, sid, text, sup)
        if predecessor is None:
            continue
        sup, prior = predecessor
        if not text and not sup:
            continue
        if sup:
            if sup in retiring:
                continue   # one retirement per fact per scene: the second would
            retiring.add(sup)   # retire a record the first already retired
        elif text.casefold() in staged_texts or _recorded_here(ledger, sid, text):
            # Two ways for a row to have nothing left to do, and both end as a
            # duplicate the reviewer approves and does not get:
            #
            # - the scene ALREADY recorded this fact. Absorbing a scene twice is
            #   supported (`POST .../absorb?force`) and re-proposes every fact
            #   the first pass found -- the `timeline.md` re-append this ledger
            #   exists to improve on.
            # - this BATCH already stages it. A reply that says the same thing
            #   in two rows is invisible to the check above, which reads a
            #   ledger neither row has reached yet; `facts.record` would dedupe
            #   the second onto the first at save time and report both as
            #   applied, so two approvals produce one fact with nothing saying
            #   so.
            #
            # `facts.record` dedupes as well and has to: this reads a snapshot
            # that can be stale. What happens here is keeping the row off the
            # panel in the first place.
            continue
        # Recorded for every row that survives, but CONSULTED only by rows that
        # retire nothing. Two rows superseding two different facts with the same
        # replacement text are both real work -- `facts.record` files one fact
        # and each row retires its own predecessor onto it -- so dropping the
        # second would leave a fact standing that the scene ended.
        if text:
            staged_texts.add(text.casefold())
        # A row that retires something addresses THAT record, so that is what
        # `target` names and what `conflicts` judges -- the write it authorizes
        # is the retirement. Recording the new fact creates a record and
        # overwrites nothing, so it needs no target and gets its id at save time
        # (`new_character` stages the same way, and for the same reason).
        # Through `_staged` like every other row: a fact reaches the reviewer as
        # a StagedEdit, so it is routed on the same evidence as the rest. No
        # subjects -- a standing truth about the world belongs to nobody, so no
        # speaker can be first-hand about it (see `routing.authority`).
        out.append(stamp({"id": f"fact:{sup}" if sup else f"fact:{sid}:{n}", "kind": "fact",
                    "target": {"kind": "facts", "id": sup},
                    "label": _fact_label(text, sup, date),
                    "field": "text",
                    # `before` is the retired fact's line, `after` the new
                    # fact's text: the diff reads as the replacement it is, and
                    # a retirement with nothing to replace it reads as the
                    # deletion it is.
                    "before": conflicts.fact_line(prior) if prior else "",
                    "after": text, "authored": False,
                    "payload": {"text": text, "date": date, "supersedes": sup,
                                "scene": sid}}, e))

    return out


def materialize(cid: str, sid: str, parsed: dict,
                messages: list[dict] | None = None,
                player_label: str | None = None, *,
                on_identity_error: Callable[[BaseException], None] | None = None,
                membership_capture: membership.Capture | None = None,
                ) -> list[dict]:
    """Turn the parsed edit lists into before/after StagedEdits against the campaign
    copies. Targets that don't exist are dropped (tolerated, not an error).

    `messages` is the transcript the extraction call was SHOWN. Pass it whenever
    the caller has it -- the citations are judged against it, and the scene can
    move between rendering the prompt and this call (see `routing.speaker_index`).
    `player_label` is the same snapshot one layer down: the name the prompt's
    transcript put on the player's unstamped posts, which a rename landing
    mid-call would otherwise change underneath the citations.

    Rows the identity step examined carry `identity_check`; they are staged
    with it (alternatives added), at band ``low`` when the check found a
    possible duplicate or never answered. The two identity-only passes --
    re-checking accepted targets, building alternatives -- run outside every
    absorb phase boundary, so a defect in either stages the rows without it
    rather than failing a paid-for extraction: it is recorded and reported
    to `on_identity_error`, and the return shape is unchanged.
    """
    croot = campaigns_paths.campaign_root(cid)
    out: list[dict] = []
    # Once per absorb, not once per edit: every row is checked against the same
    # transcript, and the index costs a scene read and a cast read.
    index = routing.speaker_index(cid, sid, messages, player_label)

    def _staged(edit: dict, row: dict, *subjects: str) -> dict:
        """One StagedEdit, stamped with the review block the panel routes on.

        Every section builder receives this stamp rather than writing the key
        itself. A row that skipped it would arrive at the
        reviewer looking like the rows that genuinely have no citation to check
        -- the dossier, voice and sheet proposals staged elsewhere -- and so be
        pre-approved on the strength of a signal that was actually present and
        simply dropped. `subjects` are the actors the record BELONGS to (see
        `routing.authority`); a record that belongs to nobody passes none.

        A row the identity step examined carries its `identity_check` onto
        the edit (alternatives filled in later), and is forced to band
        ``low`` when the check found a possible duplicate or never answered;
        the citation's own score and quote are reported unchanged.
        """
        row = dict(row)
        row.pop(_contracts.AS_NEW_KEY, None)
        edit["review"] = routing.review(index, row, subjects)
        ic = row.get("identity_check")
        if isinstance(ic, dict):
            edit["identity_check"] = {**ic, "alternatives": []}
            if ic.get("decision") in _contracts._LOW_DECISIONS:
                edit["review"] = {**edit["review"], "band": "low"}
        return edit

    def _identity_failed(exc: BaseException) -> None:
        errors.record_exception(exc, "continuity-identity", campaign=cid, scene=sid)
        if on_identity_error is not None:
            on_identity_error(exc)

    out.extend(staging.character_states(cid, croot, parsed, _staged))
    out.extend(staging.group_states(cid, croot, parsed, _staged))
    out.extend(staging.memberships(cid, sid, parsed, membership_capture, _staged))
    out.extend(staging.lore_appends(cid, parsed, _staged))
    out.extend(staging.authored(cid, parsed, _staged))
    out.extend(staging.feelings(cid, parsed, _staged))
    out.extend(staging.bonds(cid, parsed, _staged))
    parsed = _continuity_rows._continuity_edits(cid, sid, parsed, out, stamp=_staged,
                               identity_failed=_identity_failed)

    out.extend(_fact_edits(cid, sid, parsed.get("facts", []), _staged))

    out.extend(staging.new_characters(cid, parsed, _staged))
    out.extend(staging.new_entities(cid, parsed, _staged))
    out.extend(weather._weather_edits(cid, sid, parsed, index))
    return out


def _new_character_provenance(after: str, payload: dict) -> str:
    lines = []
    evidence = (payload.get("evidence", "") or "").strip()
    confidence = parse._confidence(payload.get("confidence", ""))
    open_questions = (payload.get("open_questions", "") or "").strip()
    if evidence:
        lines.append(f"Evidence: {evidence}")
    lines.append(f"Confidence: {confidence}")
    if open_questions:
        lines.append(f"Open questions: {open_questions}")
    return (after.rstrip() + "\n\n## Play Provenance\n" + "\n".join(lines)).strip()


def _new_character_dossier(name: str, payload: dict) -> str:
    confidence = parse._confidence(payload.get("confidence", ""))
    evidence = (payload.get("evidence", "") or "").strip()
    open_questions = (payload.get("open_questions", "") or "").strip()
    parts = [f"{name} was introduced through play as a {confidence} emergent character."]
    if evidence:
        parts.append(f"Scene evidence: {evidence}")
    if open_questions:
        parts.append(f"Open questions: {open_questions}")
    return " ".join(parts)
