"""Ingest one rewritten campaign-log scene into a grimoire campaign, running it through
the real absorb pipeline. Built for the ingest-campaign-log skill — see
.claude/skills/ingest-campaign-log/SKILL.md for the end-to-end workflow this drives.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from grimoire.llm import LLMClient
from grimoire.store import (
    absorb, appearances, atomic, campaigns, characters, chronicle, llm_connections, locks, overlay,
    scenes,
)
from grimoire.store.paths import slugify


def ensure_campaign(name: str, world_id: str) -> str:
    for c in campaigns.list_campaigns():
        if c["name"] == name and c["world"] == world_id:
            return c["id"]
    return campaigns.create_campaign(name, world_id)


def _manifest_path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "ingest_manifest.json"


def load_manifest(cid: str) -> dict:
    p = _manifest_path(cid)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_manifest(cid: str, data: dict) -> None:
    atomic.write_text(_manifest_path(cid),
                      json.dumps(data, indent=2, sort_keys=True) + "\n")


def ensure_character(campaign_id: str, spec: dict) -> str:
    """Overlay-aware: a thin campaign's world may already have this character
    (by slug), so dedupe against the world/campaign union — never blank-card
    shadow a character that already exists in the world."""
    target = slugify(spec["name"])
    if target in overlay.character_refs(campaign_id):
        return target
    card = characters.blank_card(spec["name"])
    card["data"]["personality"] = spec.get("personality", "")
    card["data"]["description"] = spec.get("description", "")
    aid, _ = overlay.create_character(campaign_id, spec["name"], "main", card)
    return aid


def ensure_location(campaign_id: str, spec: dict) -> str:
    target = slugify(spec["name"])
    existing = {e["id"] for e in overlay.list_entities(campaign_id, "locations")}
    if target in existing:
        return target
    return overlay.create_entity(campaign_id, "locations", spec["name"], body=spec.get("notes", ""))


def resolve_version(cid: str, kind: str, actor_id: str) -> str:
    """Overlay-aware: a thin campaign's cast is usually still inherited (never
    appeared/materialized), so this must resolve across the world/campaign
    union, not just the campaign's own copy."""
    if kind == "pcs":
        from grimoire.store import pcs
        return pcs.read_pc(overlay.pc_root(cid, actor_id), actor_id)["meta"]["default_version"]
    return characters.read_character(overlay.char_root(cid, actor_id), actor_id)["meta"]["default_version"]


def build_scene(cid: str, scene: dict) -> str:
    for spec in scene.get("new_characters", []):
        ensure_character(cid, spec)
    for spec in scene.get("new_locations", []):
        ensure_location(cid, spec)

    sid = scenes.create_scene(cid, scene["title"])
    if scene.get("date"):
        sid = scenes.set_datetime(cid, sid, scene["date"])["id"]
    if scene.get("location"):
        scenes.set_location(cid, sid, scene["location"])
    for turn in scene["turns"]:
        scenes.append_message(cid, sid, turn["role"], turn["content"], speaker=turn.get("speaker"))
    for actor in scene["characters"]:
        kind, aid = actor["kind"], actor["id"]
        vid = resolve_version(cid, kind, aid)
        appearances.appear(cid, sid, kind, aid, vid, "player" if kind == "pcs" else "npc",
                            narrate=False)
    return sid


async def run_absorb(cid: str, sid: str, client: LLMClient, conn: dict) -> dict:
    scene = scenes.read_scene(cid, sid)
    facts = chronicle.scene_facts(cid, sid)
    transcript = chronicle.transcript_text(scene["messages"])
    messages = absorb.build_prompt(
        transcript, facts, absorb.state_snapshot(cid, sid),
        absorb.relationships_snapshot(cid, sid), absorb.plot_snapshot(cid),
        # `group_snapshot` is the positional gap here, and stays unpassed: this
        # pipeline predates group state, and starting to send it is a change to
        # what ingest extracts rather than a fix to this one. Commitments are
        # named so a later imported scene can resolve one an earlier import
        # opened instead of filing a duplicate.
        commitment_snapshot=absorb.commitment_snapshot(cid))
    text = await client.complete(messages, conn)
    parsed = absorb.parse_output(text)
    edits = absorb.materialize(cid, sid, parsed)
    return {"parsed": parsed, "edits": edits}


class SceneVanished(Exception):
    """The scene a manifest entry resumes on stopped existing.

    Raised from INSIDE the campaign lock. Checking before taking it is a
    check-then-act: `scenes` mutators serialize on the same lock, so a rename
    that starts after the check completes before the replay acquires it, and the
    replay then stamps beats and change records with an id no scene has. The
    unlocked check upstream stays as the cheap early exit; this one is the one
    that holds.
    """


def _timeline_lines(events: list[dict]) -> list[str]:
    """The lines `chronicle.append_timeline` will write for these events.

    A deliberate duplicate of the store's rendering, and the one place this
    script knows that format. `test_the_timeline_check_agrees_with_the_store`
    pins the agreement, so a change to either side fails there rather than
    silently making the check below stop matching -- which would bring the
    duplicate events back.
    """
    return [f"- **{e.get('date', '')}** {e.get('text', '').strip()}".rstrip()
            for e in events]


#: `timeline_before` is absent from this manifest entry -- it was written before
#: the field existed, or by hand. Distinguished from a stored `None`, which is
#: the pre-image of a campaign that had NO timeline file yet and is a real
#: answer. Without the distinction an old entry would read as "the timeline was
#: empty when this scene started" and re-file a batch that had already landed.
_NO_PREIMAGE = object()


#: What `chronicle.append_timeline` seeds a missing timeline with. Duplicated
#: here for the same reason `_timeline_lines` duplicates the line format, and
#: pinned by the same test: the pre-image has to describe the file the store
#: will actually append to, including the one it is about to create.
_TIMELINE_HEADER = "# Timeline\n"


def _timeline_head(cid: str) -> str:
    """Exactly the text `append_timeline` will keep in front of its new lines.

    It writes `existing.rstrip() + "\\n" + lines + "\\n"`, so this is that
    rstripped prefix -- the offset our own append starts at, if it runs.
    """
    p = campaigns.campaign_root(cid) / "timeline.md"   # paths-ok: the store's own root
    return (p.read_text(encoding="utf-8") if p.exists() else _TIMELINE_HEADER).rstrip()


def _timeline_preimage(cid: str) -> dict:
    """The timeline as a POSITION plus a fingerprint of everything before it.

    Not a whole-file digest, which was the round-thirty-six version and which
    only answered "has anything changed". The size is what makes the check
    scene-specific: our append, if it ran, starts at exactly this offset, so a
    batch that belongs to some other scene is on the far side of it and can no
    longer be mistaken for ours by matching the tail.
    """
    head = _timeline_head(cid)
    return {"size": len(head),
            "digest": hashlib.sha256(head.encode("utf-8")).hexdigest()}


def _timeline_already_has(cid: str, events: list[dict], before=_NO_PREIMAGE) -> bool:
    """Whether this scene's events are ALREADY the tail of the timeline.

    A flag written after the append cannot answer this: if the flag's own write
    is what failed, the resume sees no flag and appends a second time -- the
    window just moved. The events themselves are the durable record, so the
    resume asks the artifact rather than a note about it.

    The whole batch must match as a CONTIGUOUS block at the end. Matching
    anywhere would drop a batch that legitimately repeats an earlier one, and
    matching per line would drop the half of a batch that happens to recur.

    Asked ONLY when resuming -- see `apply_scene`. Tail equality cannot tell a
    retry from a new scene whose events repeat the batch before it, and two
    consecutive scenes can honestly extract the same date and wording; on a
    FIRST attempt this check would then skip the append and lose that scene's
    events outright, which is worse than the duplicate it exists to prevent.

    Scene identity is not enough on its own, and neither is the tail. Inside a
    resume the same coincidence returns: if the PRECEDING scene filed an
    identical batch, the tail matches whether or not this scene's own append ever
    ran. The pre-image is what makes the question scene-specific -- but only if
    it is a POSITION, not just a fingerprint of the whole file. "Has anything
    changed since this scene started" is still answered yes by somebody else's
    append, and the tail read after it is still not ours.

    So the check is positional. `append_timeline` writes `head + "\\n" + lines +
    "\\n"`, where `head` is the rstripped prior content; the pre-image records
    that head's length and digest, so this asks whether the file still begins
    with what we started from and whether OUR lines are what follows at exactly
    that offset. A batch belonging to another scene lies before the offset and is
    no longer reachable by this question at all.

    The remainder must be ours EXACTLY, or ours followed by a line break: an
    append that landed and was then followed by somebody else's is still
    recognized as landed, but a bare `startswith` is not enough for that. Our
    last rendered line is a prefix of any longer line beginning with the same
    text -- ours reading `She paid` against another writer's `She paid in full`
    -- so the prefix test read a different batch as our completed append and the
    retry dropped this scene's event. Only whole rendered lines count, which is
    what the newline says. Anything else -- a head that no longer matches, or a
    remainder that is not ours -- returns False and the batch is filed. That is
    the deliberate direction: a duplicate is visible and repairable, a dropped
    scene is neither.

    A pre-image is durable and is still not the flag the top of this docstring
    rules out: it records what was true BEFORE the step, so failing to write it
    means the step never runs at all -- where a flag written after the step is
    unwritable exactly when it is needed.

    Residual, and it is the one thing content cannot answer: if this scene's
    append never ran and a concurrent web absorb then wrote a BYTE-IDENTICAL
    batch at the same offset, nothing here can tell that apart from our own
    append having landed. Closing it needs an idempotency key on
    `chronicle.append_timeline` itself -- an identity carried with the write
    rather than inferred from what the write produced.
    """
    if not events:
        return True
    if not isinstance(before, dict) or not isinstance(before.get("size"), int):
        # An entry written before the pre-image existed, or by hand. Nothing to
        # anchor on, so the events are filed: this path can duplicate, and the
        # alternative -- guessing from the tail alone -- can lose a scene.
        return False
    cur = _timeline_head(cid)
    size = before["size"]
    if len(cur) < size or hashlib.sha256(
            cur[:size].encode("utf-8")).hexdigest() != before.get("digest"):
        return False                      # not the file this scene started from
    want = "\n" + "\n".join(_timeline_lines(events))
    rest = cur[size:]
    return rest == want or rest.startswith(want + "\n")


def apply_scene(cid: str, sid: str, parsed: dict, edits: list[dict],
                resuming: bool = False, timeline_before=_NO_PREIMAGE,
                record_preimage=None) -> tuple[list[str], list[dict]]:
    """Write the scene's absorb through, returning what landed AND what did not.

    `append_timeline` is the one step here that APPENDS, where the chronicle
    record is keyed by scene id and `mark_absorbed` sets fields. So a failure
    after it left the manifest `in_progress` and the next run replayed the whole
    sequence, filing the same events a second time, permanently. The resume
    checks the timeline itself for them rather than trusting a flag it may not
    have managed to write. Every other step is safe to repeat, which is why only
    this one is checked.

    The failures used to be dropped here, on the grounds that this pipeline
    predates sheets and surfaces nothing about them. That was survivable while
    `apply_edits` reported only sheet conflicts; it is not now that commitments
    report their own (#115). An unreadable store, a conflicting concurrent
    update or a full disk would lose an extracted movement while the manifest
    recorded the scene as `done`, so the run could never be retried into a
    correct state -- and a batch import is exactly where nobody is watching.

    One hold across the whole sequence, for the same reason the web
    chronicle-save route takes one (`routes/scenes.py:put_chronicle`, #234).
    `apply_edits` decides the whole batch's conflict verdicts before its first
    write; with no lock around that span, a concurrent live save can append a
    newer movement in between, so the stale `before` is accepted rather than
    reported and `commitments.set_movement` -- which locks only its own write --
    appends this older ingested beat after the newer one and rewinds
    `last_scene`. Holding it here makes the check and the writes one span, and
    an ingest that loses the race is refused with `StoreBusy` before the
    chronicle record is written, so the retry below is safe. The lock is
    reentrant, so the inner acquisitions cost nothing.
    """
    with locks.campaign_lock(cid):
        if not _scene_exists(cid, sid):
            raise SceneVanished(sid)
        facts = chronicle.scene_facts(cid, sid)
        chronicle.absorb(cid, {"id": sid, "one_line": parsed["one_line"],
                               "summary": parsed["summary"],
                               "keywords": parsed["keywords"], **facts})
        # `resuming` is what makes the check safe: only a run finishing a scene
        # a PREVIOUS run started can have already filed these events. A first
        # attempt always appends, so a scene whose events legitimately repeat
        # the one before it is not mistaken for a retry of it. Inside a resume
        # the pre-image does the same job against the same coincidence: the
        # tail is only consulted once the file has actually changed since this
        # scene's extraction was recorded.
        if not (resuming and _timeline_already_has(
                cid, parsed["timeline_events"], timeline_before)):
            # The pre-image is RE-TAKEN here, inside the lock, if the timeline
            # has moved since the write-ahead recorded one. The write-ahead runs
            # before this lock is acquired, and `put_chronicle` appends while
            # holding it -- so a live absorb can land in between, and the
            # pre-image the resume would compare against describes a file that
            # no longer exists. Re-taking it under the lock makes the capture and
            # the append one span nothing else can enter, which is the window
            # this round's finding is about. Persisted BEFORE the append, so a
            # failure to write it means the append does not happen.
            if record_preimage is not None:
                now = _timeline_preimage(cid)
                if now != timeline_before:
                    record_preimage(now)
            chronicle.append_timeline(cid, parsed["timeline_events"])
        scenes.mark_absorbed(cid, sid, parsed["one_line"], parsed["summary"])
        return absorb.apply_edits(cid, edits, sid)


def retry_edits(cid: str, sid: str, edits: list[dict]) -> tuple[list[str], list[dict]]:
    """Replay approved rows that a previous attempt did not land -- and nothing else.

    Deliberately NOT `apply_scene`: the chronicle record, the timeline events and
    `mark_absorbed` all belong to the first attempt and have already run.
    Re-running them is not harmless -- `append_timeline` appends, so a second
    pass writes every event of that scene into the timeline twice -- and neither
    is re-applying the rows that DID land, since `plot`/`commitments` beats
    append too. Same hold as `apply_scene` for the same reason.
    """
    with locks.campaign_lock(cid):
        if not _scene_exists(cid, sid):
            raise SceneVanished(sid)
        return absorb.apply_edits(cid, edits, sid)


def _scene_exists(cid: str, sid: str) -> bool:
    try:
        scenes.read_scene_meta(cid, sid)
    except scenes.SceneNotFound:
        return False
    return True


def _vanished(sid: str) -> str:
    return (f"the scene this entry resumes on ({sid}) no longer exists — it was renamed "
            "or deleted after the run that recorded it; reconcile this key by hand")


def _record_vanished(cid: str, manifest: dict, key: str, sid: str) -> dict:
    """Persist the vanished-scene outcome, then report it.

    Returning `incomplete` without saving left the entry `in_progress` on disk,
    so nothing downstream could act on what this run had just discovered:
    `status` kept printing `in_progress`, and `resolve` -- the ONLY way out of a
    state a rerun cannot clear, and the reason the "delete the key" instruction
    was removed -- refuses anything not persisted as `incomplete`. The key was
    stuck: no rerun could finish it, because the scene is gone, and no person
    could close it either.

    The detail rides along, because `incomplete` alone would read as ordinary
    unapplied rows and send the next run looking for `pending` that is not the
    problem. Everything else on the entry is kept: its sid is the missing scene
    and its `applied`/`failures` are the record of what did land before it went.
    """
    entry = {**(manifest.get(key) or {}), "status": "incomplete", "sid": sid,
             "detail": _vanished(sid)}
    manifest[key] = entry
    save_manifest(cid, manifest)
    return {"key": key, **entry}


#: Edit kinds that write more than one thing, so a failure can leave the first
#: one made. Never replayed -- see `_unapplied`.
_MULTI_STEP = ("new_character", "new_location", "new_lore")


def _unapplied(edits: list[dict], failures: list[dict]) -> list[dict]:
    """The rows a failed apply did not land AND could still land, in batch order.

    Matched by id: `apply_edits` reports one failure per row and `materialize`
    ids each row `<kind>:<target>`. Each reported failure is consumed once, so a
    batch that somehow carried an id twice cannot turn one failure into a retry
    of the row that landed.

    **A conflict is not replayable and is deliberately excluded.** An `error` is
    the store failing to accept a write -- unreadable file, full disk -- and the
    same row can land once that clears. A `conflict` is `batch_verdicts` judging
    the row's staged `before` against a record that has since moved; the stored
    row still carries that same `before`, so replaying it reproduces the
    identical verdict on every future run, and the scene could never leave
    `incomplete`. Rebasing it means re-deriving the beat against the new value,
    which is an extraction, and resolving it means writing `resolve: replace`
    over somebody else's newer movement with nobody watching -- exactly the
    silent overwrite the conflict exists to prevent. So it stays in `failures`
    as a standing reason, is carried across retries so a later run cannot report
    the scene `done` without it, and needs a person.

    **A row that CREATES a record is excluded for a different reason**: it may
    have half landed. `new_character` writes the character and then seats it in
    the scene; `new_location` writes the entity and may then set it as the
    scene's setting; each is more than one step inside one edit, and a failure
    after the first leaves the record made. Replaying that does not retry the
    step that failed -- `overlay.create_*` uniquifies an occupied slug, so the
    retry mints a SECOND character or location and seats that one instead. The
    fix is the same shape as for a conflict: report it, keep it out of
    `pending`, and let a person look at what exists before anything else is
    written. (Making these replayable properly means persisting the created id
    and resuming mid-edit, which is a change to `apply_edits`' contract rather
    than to this script.)
    """
    outstanding = [f.get("id") for f in failures
                   if isinstance(f, dict) and f.get("kind") != "conflict"]
    # An id shared by two rows cannot be resolved to one of them. `apply_edits`
    # reports a failure by id and nothing else, and `materialize` does not
    # promise uniqueness -- two lore appends against one entry are both
    # `lore:<eid>`. Matching in order would queue whichever came FIRST, which is
    # the one that landed, and drop the one that failed: the retry then re-applies
    # stale text and reports the scene `done` with the real proposal gone. An
    # ambiguous id is therefore replayed by nobody; the failure stands and a
    # person reads it.
    ids = [e.get("id") for e in edits if isinstance(e, dict)]
    ambiguous = {i for i in ids if ids.count(i) > 1}
    pending: list[dict] = []
    for e in edits:
        if isinstance(e, dict) and e.get("id") in outstanding:
            outstanding.remove(e.get("id"))
            if e.get("kind") not in _MULTI_STEP and e.get("id") not in ambiguous:
                pending.append(e)
    return pending


async def ingest_one_scene(cid: str, scene: dict, client: LLMClient, conn: dict) -> dict:
    manifest = load_manifest(cid)
    key = scene["key"]
    entry = manifest.get(key)
    if entry and entry.get("status") == "done":
        return {"key": key, **entry, "status": "skipped"}

    if entry and entry.get("sid") and entry.get("status") in ("in_progress", "incomplete") \
            and not _scene_exists(cid, entry["sid"]):
        # The scene this entry resumes on was renamed or deleted between the
        # attempt that recorded it and now. A rename is an id change and leaves
        # no old->new trace: `scene_refs.repoint` follows the seven STORES that
        # persist scene ids, and this manifest is the script's own journal, not
        # one of them -- so the new id is unrecoverable here, and every pending
        # row is stamped `scene: <the old id>` besides. Resuming would write
        # beats and change records pointing at a scene that does not exist;
        # rebuilding would mint a duplicate of the renamed one. Neither is a
        # decision to make unattended, so this stops and names the missing id.
        return _record_vanished(cid, manifest, key, entry["sid"])

    # An "incomplete" entry means a prior attempt absorbed this scene and some
    # approved rows did not land; it carries those rows in `pending`, so the
    # resume replays exactly them. Re-running the whole scene instead would
    # re-extract (another LLM call), append the scene's timeline events a second
    # time, and re-apply the rows that DID land -- and plot/commitment beats
    # append, so the retry would file the same beat twice while "fixing" the one
    # that was lost.
    pending = entry.get("pending") if entry else None
    # The timeline PRE-IMAGE this scene started from, carried on every path that
    # can leave the entry unfinished. It belongs to the attempt that recorded the
    # extraction and is read back rather than re-taken: taken now it would be the
    # state AFTER whatever that attempt managed to append, and would answer its
    # own question. `_NO_PREIMAGE` is an entry written before this field existed,
    # which falls back to tail equality alone.
    timeline_before = (entry or {}).get("timeline_before", _NO_PREIMAGE)
    if entry and entry.get("status") == "incomplete" and entry.get("sid"):
        if not (isinstance(pending, list) and pending):
            # Failures with nothing replayable behind them: conflicts, which
            # reproduce forever (see `_unapplied`). Re-running the whole scene is
            # not the fallback -- that duplicates the timeline events and the
            # beats that already landed to recover rows that cannot land anyway.
            # This run does nothing and says so; the exit code is what stops a
            # batch, and the manifest already carries the reasons.
            return {"key": key, **entry, "status": "incomplete"}
        sid = entry["sid"]
        one_line = entry.get("one_line", "")
        # Carried, not re-derived: a retry that leaves the scene incomplete
        # again must keep the extraction its timeline was written from, and the
        # edits whose `before` records what the proposal actually saw.
        parsed = entry.get("parsed")
        edits_taken = entry.get("edits")
        try:
            applied, failures = retry_edits(cid, sid, pending)
        except SceneVanished:
            # Lost the race the check above cannot close: the scene went while
            # this run was between that check and the lock.
            return _record_vanished(cid, manifest, key, sid)
        # The first attempt's applied ids stay on the record: this run did not
        # re-apply them, and dropping them would report the scene as having
        # landed only what the retry touched.
        applied = [a for a in (entry.get("applied") or []) if isinstance(a, str)] + applied
        # Every prior failure this run did NOT replay stays too, keyed on what
        # was actually replayed rather than on `kind`. Conflicts were the first
        # reason for this, then record-creating rows and the `changes` log
        # joined them, and a `kind == "conflict"` filter silently dropped the
        # new ones -- so a retry that cleared the last I/O error reported the
        # scene `done` while a half-created character still stood unreconciled.
        # The question is not what kind of failure it is; it is whether anything
        # has since answered for it.
        replayed = {e.get("id") for e in pending if isinstance(e, dict)}
        failures = [f for f in (entry.get("failures") or [])
                    if isinstance(f, dict) and f.get("id") not in replayed] + failures
        pending = _unapplied(pending, failures)
    else:
        # build_scene runs at most once per key: an "in_progress" entry means a
        # prior attempt already minted the scene and died before absorb/apply
        # completed, so this retry resumes with that sid instead of creating a
        # duplicate scene. There remains a narrow window — a crash inside
        # apply_scene, or between it and the manifest save below — where a retry
        # re-absorbs an already-applied scene; see SKILL.md for that residual
        # risk. An "incomplete" entry with no usable `pending` (hand-edited, or
        # written before this script recorded it) lands here too: a full re-run
        # is the only thing left, and it is what this path always did.
        if entry and entry.get("status") in ("in_progress", "incomplete") and entry.get("sid"):
            sid = entry["sid"]
        else:
            sid = build_scene(cid, scene)
            manifest[key] = {"status": "in_progress", "sid": sid}
            save_manifest(cid, manifest)

        # The extraction is PERSISTED and resumed, not re-run. A retry after a
        # failure partway through the sequence would otherwise pair extraction
        # B's chronicle record and edits with extraction A's timeline events --
        # the model is nondeterministic, so B can name events A never had and
        # omit ones already filed, and nothing downstream would ever reconcile
        # the two. Resuming the saved one also means the retry costs no tokens.
        # `materialize` is re-run against the CURRENT store, so a record that
        # moved in between is judged fresh.
        stored = (entry or {}).get("parsed")
        stored_edits = (entry or {}).get("edits")
        resuming = isinstance(stored, dict)
        if resuming:
            parsed = stored
            # The EDITS are resumed too, not re-materialized. Re-running
            # `materialize` against the store as it is now takes each record's
            # CURRENT value as the proposal's `before` -- so a movement another
            # save made in between becomes the basis the stale row was written
            # against instead of the conflict it is, and `apply_edits` waves it
            # through: an older scene's beat appended after a newer one, or a
            # commitment reopened after it was fulfilled. The whole point of
            # `before` is that it records what the reviewer's proposal actually
            # saw. (I argued the opposite one round ago; that was wrong, and
            # this is why.)
            edits = stored_edits if isinstance(stored_edits, list) \
                else absorb.materialize(cid, sid, parsed)
        else:
            result = await run_absorb(cid, sid, client, conn)
            parsed, edits = result["parsed"], result["edits"]
            # The timeline's PRE-IMAGE rides with the extraction, recorded in
            # the same write and for the same reason: a resume has to be able to
            # tell this scene's append from an identical one filed by the scene
            # before it, and only a value captured before the append can say so.
            timeline_before = _timeline_preimage(cid)
            manifest[key] = {**(manifest.get(key) or {}), "status": "in_progress",
                             "sid": sid, "parsed": parsed, "edits": edits,
                             "timeline_before": timeline_before}
            save_manifest(cid, manifest)
        one_line = parsed["one_line"]
        edits_taken = edits

        def _record_preimage(pre: dict) -> None:
            """Persist a pre-image re-taken inside the campaign lock, and adopt
            it here so the entry written at the end of this run carries it."""
            nonlocal timeline_before
            timeline_before = pre
            manifest[key] = {**(manifest.get(key) or {}), "timeline_before": pre}
            save_manifest(cid, manifest)

        try:
            applied, failures = apply_scene(
                cid, sid, parsed, edits, resuming=resuming,
                timeline_before=timeline_before, record_preimage=_record_preimage)
        except SceneVanished:
            return _record_vanished(cid, manifest, key, sid)
        pending = _unapplied(edits, failures)

    # A scene whose edits did not all land is NOT done. Marking it done would
    # make the loss permanent: `ingest_one_scene` skips a done key outright, so
    # the only record of the dropped edit would be a line of console output in a
    # batch run. `incomplete` keeps the sid AND the unapplied rows, so a re-run
    # resumes this scene rather than minting a duplicate, and carries both the
    # reasons and the work still owed with it.
    status = "done" if not failures else "incomplete"
    manifest[key] = {"status": status, "sid": sid, "one_line": one_line, "applied": applied}
    if failures:
        # The extraction rides along on anything not `done`: an `incomplete`
        # entry with nothing replayable falls back to a full re-run, and that
        # fallback must resume this scene's extraction rather than buy a second
        # one that disagrees with what already landed.
        if isinstance(parsed, dict):
            manifest[key]["parsed"] = parsed
        if isinstance(edits_taken, list):
            manifest[key]["edits"] = edits_taken
        # And so does the pre-image, for the fallback that re-runs the whole
        # scene: dropping it here would leave that run with nothing but tail
        # equality again, which is the state this round's finding is about. It
        # is the value from the attempt that recorded the extraction, never a
        # fresh reading -- taken now it would already include the append.
        if timeline_before is not _NO_PREIMAGE:
            manifest[key]["timeline_before"] = timeline_before
        manifest[key]["failures"] = failures
        if pending:   # omitted when nothing is replayable, which is what the resume reads
            manifest[key]["pending"] = pending
    save_manifest(cid, manifest)
    return {"key": key, **manifest[key], "status": status}


def resolve_key(cid: str, key: str) -> tuple[bool, object]:
    """Close an `incomplete` key whose failures have been reconciled by hand,
    KEEPING its sid. Returns (ok, the new entry) or (False, an explanation).

    This exists because the recovery it replaces was worse than the problem. A
    standing conflict cannot be replayed (`_unapplied`), so the only way out of
    `incomplete` used to be deleting the key -- and a deleted key is an unknown
    scene: the next `ingest` takes the no-entry branch, calls `build_scene`, and
    absorbs the transcript again. That is a duplicate scene, a second copy of
    its timeline events and a second copy of every beat that already landed --
    exactly what the manifest entry exists to prevent, produced by following the
    instruction printed when the entry could not be finished any other way.

    The failures are moved to `reconciled` rather than dropped. The scene is
    `done` because a person has dealt with it, not because the pipeline landed
    every row, and the manifest should not read as if it had.
    """
    manifest = load_manifest(cid)
    entry = manifest.get(key)
    if not isinstance(entry, dict):
        return False, f"no manifest entry for {key!r} in this campaign"
    if entry.get("status") == "done":
        return True, entry                       # idempotent: nothing left to close
    if entry.get("status") != "incomplete":
        return False, (f"{key!r} is {entry.get('status')!r}, not incomplete — only a scene "
                       "that absorbed and failed to land some rows can be resolved")
    closed = {k: v for k, v in entry.items() if k not in ("failures", "pending")}
    closed["status"] = "done"
    if entry.get("failures"):
        closed["reconciled"] = entry["failures"]
    manifest[key] = closed
    save_manifest(cid, manifest)
    return True, closed


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Ingest a rewritten campaign-log scene into a grimoire campaign.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_setup = sub.add_parser("setup", help="create (or find) the target campaign")
    p_setup.add_argument("--world", required=True)
    p_setup.add_argument("--name", required=True)

    p_ingest = sub.add_parser("ingest", help="ingest one scene JSON file")
    p_ingest.add_argument("--campaign", required=True)
    p_ingest.add_argument("--input", required=True, type=Path)

    p_status = sub.add_parser("status", help="print the ingest manifest")
    p_status.add_argument("--campaign", required=True)

    p_resolve = sub.add_parser(
        "resolve", help="close an incomplete scene whose failures you have reconciled by hand")
    p_resolve.add_argument("--campaign", required=True)
    p_resolve.add_argument("--key", required=True)

    args = ap.parse_args()
    if args.cmd == "setup":
        print(ensure_campaign(args.name, args.world))
        return 0
    if args.cmd == "status":
        print(json.dumps(load_manifest(args.campaign), indent=2, sort_keys=True))
        return 0
    if args.cmd == "resolve":
        ok, result = resolve_key(args.campaign, args.key)
        print(json.dumps(result, indent=2, sort_keys=True) if ok else f"error: {result}",
              file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    scene = json.loads(args.input.read_text(encoding="utf-8"))
    conn = llm_connections.get_active()
    if conn is None:
        print("error: no LLM connection selected (set one up in grimoire's Configuration page)",
              file=sys.stderr)
        return 1
    if conn["kind"] == "openrouter" and not conn["api_key"]:
        print("error: the active OpenRouter connection has no key set", file=sys.stderr)
        return 1
    if conn["kind"] == "openai_compatible" and not conn["base_url"]:
        print("error: the active custom connection has no base URL set", file=sys.stderr)
        return 1
    client = LLMClient()
    result = asyncio.run(ingest_one_scene(args.campaign, scene, client, conn))
    print(json.dumps(result, indent=2))
    if result["status"] == "incomplete":
        # Nonzero, because the caller is a batch driver working through a log in
        # strict scene order and its only signal is this exit status. Scene N+1's
        # absorb is primed with the state scene N wrote; carrying on past a scene
        # whose movement never landed extracts every later scene against a
        # snapshot that is missing it, and the damage compounds silently down the
        # file. Better to stop the run than to import the rest of the log wrong.
        if result.get("detail"):
            print(f"error: {result['detail']}", file=sys.stderr)
            return 1
        print(f"error: {len(result.get('failures') or [])} approved edit(s) did not land — "
              f"this scene is NOT finished", file=sys.stderr)
        for f in result.get("failures") or []:
            if isinstance(f, dict):
                print(f"       - {f.get('id', '')}: {f.get('reason', '')}", file=sys.stderr)
        if result.get("pending"):
            print("       re-run this same command to replay them", file=sys.stderr)
        else:
            # NOT "they conflict": the reasons are listed above and this branch
            # covers every kind of unreplayable failure, of which a conflict is
            # only one. `apply_edits` also reports a `changes` row -- the
            # write-back delta the Changes panel reads, which is a log of what
            # the edits did rather than one of the edits. Replaying it would
            # mean re-running the whole batch to rebuild a delta nothing else
            # depends on, so it is reported and left to the operator like the
            # rest, and the campaign state it describes is already complete.
            print("       none of them can be replayed. Deal with them by hand (the reasons "
                  "are above), then close the key with:", file=sys.stderr)
            print(f"         ingest_scene.py resolve --campaign {args.campaign} "
                  f"--key {scene.get('key', '<key>')}", file=sys.stderr)
            print("       (do NOT delete the key — that makes the next run rebuild and "
                  "re-absorb the scene)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
