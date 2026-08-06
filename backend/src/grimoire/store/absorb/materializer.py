"""Turning the parsed sections into StagedEdits — the diff the reviewer sees.

The file is named for the role, not the function, because `materialize` is a
public function this package re-exports: a submodule spelled the same way
would be overwritten by that export, and a later `from ..absorb import
materialize` would bind the function rather than the module.
"""

from __future__ import annotations

from .. import (characters, commitments, entities, facts, groupstate, overlay,
                pcs, playstate, plot, relationships)
from .. import (characters, commitments, config, entities, groupstate, overlay, pcs,
                playstate, plot, relationships, turnstate)
from ..appearances import paths as appearances_paths, versions as appearances_versions
from ..campaigns import paths as campaigns_paths
from ..paths import slugify
from ..scenes import paths as scenes_paths, read as scenes_read
from . import conflicts, parse, routing, weather

_CARD_FIELDS = ("description", "personality", "scenario")


def _char_name(cid: str, char_id: str) -> str:
    """Overlay-aware: a thin campaign's NPC is usually still inherited (never
    materialized croot-side), so the display name must resolve across the union."""
    try:
        return characters.read_character(overlay.char_root(cid, char_id), char_id)["meta"].get("name", char_id)
    except characters.CharacterNotFound:
        return char_id


def _actor_exists(cid: str, token: str) -> bool:
    """Overlay-aware: a thin campaign's cast is mostly inherited (never
    materialized croot-side), so existence must be checked across the union,
    not just the campaign's own copy."""
    kind, _, aid = token.partition(":")
    try:
        if kind == "pcs":
            pcs.read_pc(overlay.pc_root(cid, aid), aid)
        elif kind == "characters":
            characters.read_character(overlay.char_root(cid, aid), aid)
        else:
            return False
        return True
    except (characters.CharacterNotFound, pcs.PCNotFound):
        return False


def _entity_kind(cid: str, eid: str) -> str | None:
    for kind in ("lore", "locations"):
        try:
            overlay.read_entity(cid, kind, eid)
            return kind
        except entities.EntityNotFound:
            continue
    return None


def _text(value) -> str:
    """A stored field as text, or "" for anything that is not a string.

    commitments.json is hand-editable and read by a bare `json.loads`, so every
    field inside a record is whatever the file says — a list-valued `status`
    concatenated into a label, or a list-valued `due` handed to `.strip()`,
    raises from inside `materialize`. That is AFTER the extraction call and is
    not caught by the absorb route, so one malformed record turns a paid-for
    absorb into a 500. Checking the document's top-level shape does not reach
    this; the fields have to be coerced where they are read.
    """
    return value.strip() if isinstance(value, str) else ""


def _new_commitment_id(owed: dict, staged: dict, slug: str, title: str) -> str:
    """`slug`, or the first `slug-N` that is free or holds the SAME commitment.

    Only for a movement the model opened WITHOUT an id, where the id is derived
    from the title and a collision may be an accident rather than a reference.
    A collision is honoured only when the stored record is unresolved AND its
    title is the one the model wrote: then the model saw that record in the
    snapshot under that title, and treating the movement as a beat on it is
    what "one edit per commitment per scene" means. Anything else gets a fresh
    id, for two different reasons:

    - a **resolved** record cannot have been meant: `commitment_snapshot` offers
      only unresolved ones, so the model was never shown it. Approving the row
      would reopen a fulfilled promise and file the new beat into the closed
      record's history.
    - a **different title** is a slug accident. `slugify` strips everything that
      is not `[a-z0-9]`, so it is not merely near-misses that collide: every
      title with no ASCII letters at all — a CJK or Cyrillic one, say — maps to
      the literal `untitled`, and the second such commitment a campaign opens
      would otherwise be swallowed by the first, keeping the first's title and
      leaving the new one with no record of its own.

    Titles are compared case- and space-insensitively; a rename between the two
    absorbs looks like a different commitment here, and opening a second record
    is the safe direction — nothing is lost or overwritten, and the reviewer can
    see both rows.

    `staged` is {id: folded title} for the rows this same batch has already
    placed, and closes the same collision one scope in: two new commitments in
    ONE absorb are both absent from `owed`, so slug-alone would hand them the
    same id and the caller's one-edit-per-commitment dedup would drop the
    second outright. A candidate this batch already took is reusable only when
    it was taken under the same title, which is the case the dedup is for.

    A movement that DOES carry an id keeps pointing where it says, resolved or
    not: that is a reference, and silently redirecting it would be the opposite
    mistake.
    """
    want = title.strip().casefold()

    def _free(candidate: str) -> bool:
        if candidate in staged:
            return staged[candidate] == want
        cur = owed.get(candidate)
        if not isinstance(cur, dict):
            return True
        # `.lower()` for the reason `commitments.open_commitments` folds too, and
        # it has to be repeated because this is a SECOND reader of the same
        # field: a hand-edited `"Fulfilled"` is hidden from the snapshot by that
        # fix, and if this allocator still read it as unresolved the model's new
        # commitment of the same title would land on the record it was never
        # shown -- reopening it, which is exactly what the resolved check exists
        # to prevent.
        if _text(cur.get("status")).lower() in commitments.RESOLVED:
            return False
        return _text(cur.get("title")).casefold() == want

    n, candidate = 1, slug
    while not _free(candidate):
        n += 1
        candidate = f"{slug}-{n}"
    return candidate


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
def _character_state_edit(cid: str, char_id: str, before: str, after: str) -> dict:
    return {"id": f"character_state:{char_id}", "kind": "character_state",
            "target": {"kind": "characters", "id": char_id},
            "label": f"{_char_name(cid, char_id)} — current state",
            "field": "current_state",
            "before": before, "after": after, "authored": False}


def _promote(cid: str, sid: str, out: list[dict], stage, ledger: list | None) -> None:
    """Fold #121's reinforced transient values into the staged character-state
    edits, in place.

    Promotion is a SOURCE of StagedEdits, never a writer: it rides the same
    review checklist, the same `apply_edits` and the same `changes.json` deltas
    the model's own proposals do, so the "extraction proposes, the user
    approves" invariant holds without a new path to audit.

    Merged onto the model's edit for the same character rather than emitted
    beside it. Two rows with the id `character_state:<id>` would be two
    reviewer decisions over one file, and whichever applied second would erase
    the other's body wholesale — `apply_edits` writes a composed snapshot, not
    a patch.

    `stage` is `materialize`'s `_staged`, and every row this touches goes
    through it with an EMPTY citation (#112). A promoted value has no quote to
    offer: its evidence is a streak across posts, not a line somebody said, and
    synthesizing a speaker or an excerpt is precisely the lie `routing.authority`
    checks the transcript to catch. Uncited lands the row in the collapsed
    section, unchecked, which is the direction `routing` says every nudge must
    run — a reviewer ticks it deliberately or it is never written.

    A row promotion MERGES into is re-stamped for the same reason, losing the
    model's own citation: its `after` is no longer the text that citation
    corroborated, so leaving a `high` band on it would pre-approve a promoted
    line nothing in the transcript ever quoted.
    """
    need = config.promote_streak()
    # The feature switch, not just the promotion one. With `turnstate_depth` at
    # 0 the tracker instruction and the prompt section are both gone, and the
    # Configuration page says that turns the whole thing off -- so a retained
    # ledger, or blocks a model volunteered while it was off all along, must not
    # keep proposing canonical state behind that promise. `promote_streak` stays
    # the narrower switch: promotion off, tracking still on.
    if need <= 0 or config.turnstate_depth() <= 0:
        return
    if ledger is None:
        try:
            tail = len(scenes_read.read_scene(cid, sid)["messages"])
        except (scenes_paths.SceneNotFound, OSError, UnicodeDecodeError):
            return
        ledger = turnstate.entries(cid, sid, tail)
    promoted = turnstate.streaks_from(ledger, need)
    if not promoted:
        return
    croot = campaigns_paths.campaign_root(cid)
    staged = {e["id"]: e for e in out}
    for token, fields in sorted(promoted.items()):
        kind, _, char_id = token.partition(":")
        if kind != "characters" or not char_id:
            continue
        try:
            characters.read_character(overlay.char_root(cid, char_id), char_id)
        except characters.CharacterNotFound:
            continue
        st = playstate.read_state(croot, char_id)
        edit = staged.get(f"character_state:{char_id}")
        # The model's own edit is the base when it has one: promotion adjusts
        # what this absorb is already proposing, not what is on disk, or the
        # merged row would silently revert the extraction's prose.
        base = playstate.parse_body(edit["after"]) if edit else (
            st or {"current_state": "", "knows": "", "suspects": ""})
        after = playstate.compose_body(
            playstate.fold_fields(base["current_state"], fields),
            base["knows"], base["suspects"])
        before = edit["before"] if edit else (
            playstate.compose_body(st["current_state"], st["knows"], st["suspects"]) if st else "")
        if not after or before == after:
            if edit is not None and before == after:
                out.remove(edit)          # promotion cancelled the model's own edit out
            continue
        if edit is not None:
            edit["after"] = after
            stage(edit, {}, f"characters:{char_id}")
        else:
            out.append(stage(_character_state_edit(cid, char_id, before, after),
                             {}, f"characters:{char_id}"))


def materialize(cid: str, sid: str, parsed: dict,
                messages: list[dict] | None = None,
                ledger: list | None = None) -> list[dict]:
    """Turn the parsed edit lists into before/after StagedEdits against the campaign
    copies. Targets that don't exist are dropped (tolerated, not an error).

    `messages` is the transcript the extraction call was SHOWN. Pass it whenever
    the caller has it -- the citations are judged against it, and the scene can
    move between rendering the prompt and this call (see `routing.speaker_index`).

    `ledger` is the transient-state entries (#120) the caller's review is being
    built from. `post_absorb` captures them WITH the scene, under one lock,
    before awaiting the extraction call, so promotion (#121) measures the same
    scene version the summary and the other edits describe. A length alone was
    not enough: an edit or a reroll landing mid-absorb rewrites entries *below*
    the tail, and only a copy taken at the same instant is immune to that.
    Defaulting to a live read keeps every other caller — the ingest script, the
    tests — working off the scene as it stands.
    """
    croot = campaigns_paths.campaign_root(cid)
    out: list[dict] = []
    # Once per absorb, not once per edit: every row is checked against the same
    # transcript, and the index costs a scene read and a cast read.
    index = routing.speaker_index(cid, sid, messages)

    def _staged(edit: dict, row: dict, *subjects: str) -> dict:
        """One StagedEdit, stamped with the review block the panel routes on.

        Every `out.append` in this function goes through this rather than
        writing the key itself. A row that skipped it would arrive at the
        reviewer looking like the rows that genuinely have no citation to check
        -- the dossier, voice and sheet proposals staged elsewhere -- and so be
        pre-approved on the strength of a signal that was actually present and
        simply dropped. `subjects` are the actors the record BELONGS to (see
        `routing.authority`); a record that belongs to nobody passes none.
        """
        edit["review"] = routing.review(index, row, subjects)
        return edit

    for e in parsed.get("character_state_edits", []):
        raw_id = e.get("id", "")
        if not raw_id:
            continue
        # The model echoes ids from the "Present: <kind>/<id>, ..." context line (or,
        # less reliably, a bare id) — strip any "characters/" or "characters:" prefix so
        # both forms resolve. playstate.py only tracks "characters" (not "pcs"), matching
        # its own docstring scope, so a pcs-prefixed id is dropped rather than misfiled.
        kind, sep, rest = raw_id.partition("/")
        if not sep:
            kind, _, rest = raw_id.partition(":")
        char_id = rest if kind in ("characters", "pcs") else raw_id
        if kind == "pcs":
            continue
        try:
            # overlay-aware: a thin campaign's NPC is usually still inherited
            # (never appeared/materialized), and a state edit for it must not
            # be silently dropped just because croot lacks the character dir
            characters.read_character(overlay.char_root(cid, char_id), char_id)
        except characters.CharacterNotFound:
            continue
        st = playstate.read_state(croot, char_id)
        cur_knows = st["knows"] if st else ""
        cur_suspects = st["suspects"] if st else ""
        # Keep-on-omit: an omitted knows/suspects preserves the stored value; an explicit
        # "" clears it. Prevents an absorb that only touches current_state from silently
        # erasing established knowledge.
        knows = e["knows"] if "knows" in e else cur_knows
        suspects = e["suspects"] if "suspects" in e else cur_suspects
        after = playstate.compose_body(e.get("current_state", ""), knows, suspects)
        if not after:
            continue
        before = playstate.compose_body(st["current_state"], cur_knows, cur_suspects) if st else ""
        if before == after:
            continue
        out.append(_staged(_character_state_edit(cid, char_id, before, after),
                           e, f"characters:{char_id}"))

    # After the model's own proposals, so a reinforced value merges onto the row
    # the reviewer would already have seen rather than opening a second one.
    _promote(cid, sid, out, _staged, ledger)

    for e in parsed.get("group_state_edits", []):
        raw_id = e.get("id", "")
        if not raw_id:
            continue
        kind, sep, rest = raw_id.partition("/")
        if not sep:
            kind, _, rest = raw_id.partition(":")
        gid = rest if kind == "groups" else raw_id
        try:
            name = overlay.read_entity(cid, "groups", gid)["meta"].get("name", gid)
        except entities.EntityNotFound:
            continue
        st = groupstate.read_state(croot, gid)
        cur = {k: (st[k] if st else "") for k in groupstate.FIELDS}
        new = {k: (e[k] if k in e else cur[k]) for k in groupstate.FIELDS}
        after = groupstate.compose_body(new)
        if not after:
            continue
        before = groupstate.compose_body(cur) if st else ""
        if before == after:
            continue
        out.append(_staged({"id": f"group_state:{gid}", "kind": "group_state",
                            "target": {"kind": "groups", "id": gid},
                            "label": f"{name} — group state", "field": "group_state",
                            "before": before, "after": after, "authored": False}, e))

    for e in parsed.get("lore_edits", []):
        eid, append = e.get("id", ""), (e.get("append", "") or "").strip()
        if not eid or not append:
            continue
        kind = _entity_kind(cid, eid)
        if not kind:
            continue
        ent = overlay.read_entity(cid, kind, eid)
        before = ent["body"].strip()
        after = (before + "\n\n" + append).strip()
        out.append(_staged({"id": f"lore:{eid}", "kind": "lore",
                            "target": {"kind": kind, "id": eid},
                            "label": f"{ent['meta'].get('name', eid)} — {kind}", "field": "body",
                            "before": before, "after": after, "authored": False}, e))

    for e in parsed.get("authored_edits", []):
        char_id, field, text = e.get("id", ""), e.get("field", ""), (e.get("text", "") or "").strip()
        if not char_id or field not in _CARD_FIELDS or not text:
            continue
        vid = appearances_versions.locked_version(cid, "characters", char_id)
        if not vid:
            continue
        try:
            # locked_version returned a version, so the actor is in the appearance
            # record and its card is materialized campaign-side
            before = characters.read_card(appearances_paths.locked_actor_root(cid),
                                          char_id, vid)["data"].get(field, "").strip()
        except (characters.CharacterNotFound, characters.VersionNotFound):
            continue
        out.append(_staged({"id": f"authored:{char_id}:{field}", "kind": "authored",
                            "target": {"kind": "characters", "id": char_id},
                            "label": f"{_char_name(cid, char_id)} — {field} (card edit)",
                            "field": field, "before": before, "after": text, "authored": True},
                           e, f"characters:{char_id}"))

    for e in parsed.get("relationship_deltas", []):
        frm, to = e.get("from", ""), e.get("to", "")
        if not _actor_exists(cid, frm) or not _actor_exists(cid, to):
            continue
        payload = {"from": frm, "to": to, "trust": e.get("trust", 0), "affection": e.get("affection", 0),
                   "tension": e.get("tension", 0), "note": e.get("note", "")}
        after = relationships._render_feeling(payload)
        cur = relationships.get_feeling(cid, frm, to)
        before = relationships._render_feeling(cur) if cur else ""
        if before == after:
            continue
        # `frm` alone is the subject: the feeling is the FROM side's, so the TO
        # side describing it is a third party's read of somebody else's heart.
        out.append(_staged({"id": f"feeling:{relationships.feeling_key(frm, to)}",
                            "kind": "relationship",
                            "target": {"kind": "relationships",
                                       "id": relationships.feeling_key(frm, to)},
                            "label": f"{relationships.actor_name(cid, frm)} → {relationships.actor_name(cid, to)}",
                            "field": "feeling", "before": before, "after": after,
                            "authored": False, "payload": payload}, e, frm))

    for e in parsed.get("bond_changes", []):
        a_tok, b_tok, typ = e.get("a", ""), e.get("b", ""), (e.get("type", "") or "").strip()
        if not typ or not _actor_exists(cid, a_tok) or not _actor_exists(cid, b_tok):
            continue
        cur = relationships.get_bond(cid, a_tok, b_tok)
        before = cur["type"] if cur else ""
        if before == typ:
            continue
        # Both ends are subjects, unlike a feeling: a bond is the pair's shared
        # relationship type, so either of them naming it is first-hand.
        out.append(_staged({"id": f"bond:{relationships.bond_key(a_tok, b_tok)}", "kind": "bond",
                            "target": {"kind": "relationships",
                                       "id": relationships.bond_key(a_tok, b_tok)},
                            "label": f"{relationships.actor_name(cid, a_tok)} & {relationships.actor_name(cid, b_tok)}",
                            "field": "bond", "before": before, "after": typ, "authored": False,
                            "payload": {"a": a_tok, "b": b_tok, "type": typ}},
                           e, a_tok, b_tok))

    try:
        threads = plot.read(cid)
    except Exception:  # noqa: BLE001 — garbled plot.json: skip plot movements, don't 500
        threads = {}
    seen_pids: set[str] = set()
    for e in parsed.get("plot_movements", []):
        beat = (e.get("beat", "") or "").strip()
        if not beat:
            continue
        mid = (e.get("id", "") or "").strip()
        title = (e.get("title", "") or "").strip()
        status = e.get("status", "open")
        if mid:
            pid = mid
        elif any(c.isalnum() for c in title):
            pid = slugify(title)  # new thread — needs a title with real content
        else:
            continue  # no id and no usable title -> drop
        if pid in seen_pids:
            continue  # one edit per thread per scene (avoids duplicate ids / double-apply)
        seen_pids.add(pid)
        cur = threads.get(pid)
        if isinstance(cur, dict):  # existing thread (by id, or a new title that collides)
            # Rendered by `conflicts`, not here: the staleness check recomputes
            # this same line at save time, and two copies of the format would
            # let a harmless reformat read as a contradiction (#111).
            before = conflicts.plot_line(cur)
            disp_title = cur.get("title") or title or pid  # keep the stored title
        else:
            before, disp_title = "", title or pid
        out.append(_staged({"id": f"plot:{pid}", "kind": "plot",
                            "target": {"kind": "plot", "id": pid},
                            "label": f"{disp_title} — {status}",
                            "field": "beat", "before": before, "after": beat, "authored": False,
                            "payload": {"id": pid, "title": disp_title, "status": status,
                                        "scene": sid}}, e))

    # Same shape as plot_movements above, and deliberately a second block rather
    # than a parameterized shared one: the two record types agree on "id or a
    # slugged title, one edit per record per scene" and on nothing else -- the
    # label, the payload and the vocabulary the status is drawn from all differ,
    # so the factored version would be a function whose body is mostly branches
    # on which of the two called it.
    try:
        owed = commitments.read(cid)
    except Exception:  # noqa: BLE001 — garbled commitments.json: skip these, don't 500
        owed = None
    if not isinstance(owed, dict):
        # `read` is a bare json.loads, so a commitments.json holding `[]` is
        # valid JSON of the wrong shape: it raises nothing and `owed.get` below
        # would then throw. That happens AFTER the extraction call, turning a
        # paid-for absorb into a 500 rather than a dropped section.
        owed = None
    # An UNREADABLE store stages nothing, where an empty one stages normally.
    # Falling back to {} conflates the two and every movement is staged as a new
    # commitment -- a row whose `before` says "nothing is stored" when the truth
    # is unknown, and whose save is worse than the lie: `apply_edits` hits the
    # same broken read, its per-edit `except` swallows it, and the reviewer's
    # panel closes on a 200 with the approved commitment gone and no failure
    # reported. Staging nothing costs this section (the same price a garbled
    # file already pays in `render_open` and the ledger) and cannot lose an
    # approval, because there is no approval to lose.
    seen_mids: set[str] = set()
    staged_titles: dict[str, str] = {}   # id -> folded title, for new rows in THIS batch
    for e in (parsed.get("commitment_movements", []) if owed is not None else []):
        beat = (e.get("beat", "") or "").strip()
        if not beat:
            continue
        given = (e.get("id", "") or "").strip()
        title = (e.get("title", "") or "").strip()
        # Blank means "the model said nothing" -- see parse.py. Carried into the
        # payload AS blank so `set_movement` keeps the stored value; the label
        # below shows the resolved value the reviewer will actually get.
        kind = (e.get("kind", "") or "").strip()
        status = (e.get("status", "") or "").strip()
        # None, not "": the key's PRESENCE is the signal (see parse.py). "" is
        # an instruction to clear the deadline; absent means leave it alone.
        due = _text(e["due"]) if "due" in e else None
        if given:
            mid = given
            # An explicit id is RESERVED too, not just remembered as seen. The
            # allocator consults `owed` and this map; an explicit id naming a
            # commitment that does not exist yet is in neither, so a later new
            # row whose title slugs to it was handed the same id -- and then
            # dropped outright by the one-edit-per-commitment check below,
            # never reaching the reviewer. Reserved under this row's title, so
            # the same title still merges (that is what the dedup is for) and a
            # different one gets a suffix.
            #
            # Under the STORED title when the row omits one, which it may:
            # `{"id": "the-debt", "beat": ...}` is a valid movement, and
            # reserving it under "" made the reservation disagree with the
            # record it names -- so the same commitment named by TITLE later in
            # the batch missed the merge and staged `the-debt-2`, which the
            # reviewer would then approve into a duplicate. The reservation must
            # say what the id means, not what this row happened to repeat.
            stored = owed.get(mid)
            staged_titles.setdefault(
                mid, (title or (_text(stored.get("title")) if isinstance(stored, dict)
                                else "")).strip().casefold())
        elif any(c.isalnum() for c in title):
            # New commitment — needs a title with real content, and an id that
            # does not land on somebody else's record.
            mid = _new_commitment_id(owed, staged_titles, slugify(title), title)
            staged_titles[mid] = title.strip().casefold()
        else:
            continue  # no id and no usable title -> drop
        if mid in seen_mids:
            continue  # one edit per commitment per scene (avoids duplicate ids / double-apply)
        seen_mids.add(mid)
        cur = owed.get(mid)
        if isinstance(cur, dict):  # existing commitment (by id, or a colliding new title)
            # The STORED head, deadline included: `due` is applied on save and
            # then steers the ledger and every later scene prompt, so a model
            # that invents or overwrites one must not be able to do it in a row
            # whose only visible text is the beat. Here it is what the deadline
            # was; the label below is what it will be. It doubles as the
            # staleness token `apply_edits` re-checks at save time.
            before = conflicts.commitment_line(cur)
            stored_due = _text(cur.get("due"))
            disp_title = _text(cur.get("title")) or title or mid  # keep the stored title
            # What the record will read AFTER the save: the model's value where
            # it gave one, the stored value where it did not.
            disp_kind = kind or _text(cur.get("kind")) or "promise"
            disp_status = status or _text(cur.get("status")) or "open"
            disp_due = stored_due if due is None else due
        else:
            before, disp_title = "", title or mid
            disp_kind = kind or "promise"      # set_movement's own defaults, for
            disp_status = status or "open"     # a commitment being created here
            disp_due = due or ""
        label = f"{disp_title} — {disp_kind}, {disp_status}"
        if disp_due:
            label += f", due {disp_due}"
        out.append(_staged({"id": f"commitment:{mid}", "kind": "commitment",
                            "target": {"kind": "commitments", "id": mid},
                            "label": label,
                            "field": "beat", "before": before, "after": beat, "authored": False,
                            "payload": {"id": mid, "title": disp_title, "kind": kind,
                                        "status": status, "due": due, "scene": sid}}, e))

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
        # `owed` above: falling back to {} would stage every supersession as an
        # ordinary new fact, hiding the retirement the reviewer approved behind
        # a row that claims to retire nothing.
        ledger = None
    retiring: set[str] = set()
    staged_texts: set[str] = set()   # folded text of the new facts THIS batch stages
    for n, e in enumerate(parsed.get("facts", []) if ledger is not None else []):
        text, date = _text(e.get("text")), _text(e.get("date"))
        sup = _text(e.get("supersedes"))
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
            continue
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
        out.append(_staged({"id": f"fact:{sup}" if sup else f"fact:{sid}:{n}", "kind": "fact",
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

    existing_char_names = {c["name"].strip().lower() for c in overlay.list_characters(cid)}
    for e in parsed.get("new_characters", []):
        name = (e.get("name", "") or "").strip()
        description = (e.get("description", "") or "").strip()
        if not name or not description:
            continue
        if name.lower() in existing_char_names:
            continue
        candidate_id = slugify(name)
        try:
            characters.read_character(overlay.char_root(cid, candidate_id), candidate_id)
            continue  # id already taken -- treat as the same character
        except characters.CharacterNotFound:
            pass
        # The reviewed description is the W++ block plus the generated history, so the
        # staged diff shows the full text that lands in the card's description field.
        history = (e.get("history", "") or "").strip()
        after = f"{description}\n\n{history}" if history else description
        # No subject: the person has no record yet, so nothing they said in the
        # scene can be first-hand ABOUT a record. Their own lines still
        # corroborate the citation, which is what separates a proposal drawn
        # from dialogue from one drawn from nowhere.
        out.append(_staged({"id": f"new_character:{candidate_id}", "kind": "new_character",
                            "target": {"kind": "characters", "id": ""},
                            "label": f"New character — {name}", "field": "description",
                            "before": "", "after": after, "authored": False,
                            "payload": {"name": name, "sd_prompt": e.get("sd_prompt", ""),
                                        "personality": e.get("personality", ""),
                                        "mes_example": e.get("mes_example", ""),
                                        "evidence": e.get("evidence", ""),
                                        "confidence": parse._confidence(e.get("confidence", "")),
                                        "open_questions": e.get("open_questions", "")}}, e))

    for kind, parsed_key, prefix, label_noun in (
        ("locations", "new_locations", "new_location", "location"),
        ("lore", "new_lore", "new_lore", "lore entry"),
    ):
        existing_names = {ent["name"].strip().lower() for ent in overlay.list_entities(cid, kind)}
        for e in parsed.get(parsed_key, []):
            name = (e.get("name", "") or "").strip()
            body = (e.get("body", "") or "").strip()
            if not name or not body:
                continue
            if name.lower() in existing_names:
                continue
            candidate_id = slugify(name)
            try:
                overlay.read_entity(cid, kind, candidate_id)
                continue
            except entities.EntityNotFound:
                pass
            payload = {"name": name, "keys": e.get("keys", "")}
            if kind == "locations":
                payload["sd_prompt"] = e.get("sd_prompt", "")
                payload["current_setting"] = e.get("current_setting", False)
            out.append(_staged({"id": f"{prefix}:{candidate_id}", "kind": prefix,
                                "target": {"kind": kind, "id": ""},
                                "label": f"New {label_noun} — {name}", "field": "body",
                                "before": "", "after": body, "authored": False,
                                "payload": payload}, e))

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
