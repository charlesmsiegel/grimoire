"""Non-destructive reroll: the alternates a scene's trailing generation keeps.

``<campaign>/scenes/<sid>.alts.json`` — one sidecar per scene, in the style of
the campaign-level JSON records (chronicle, plot, rolls), because alternates are
structured and the transcript's frontmatter holds flat string scalars only::

    {"anchor": 4,
     "next_guidance": "",
     "runs": [{"created": "...", "guidance": "", "segments": [{"speaker": ..., "content": ...}]},
              ...]}

**What a set is keyed to.** One reroll can produce several posts
(``scenes.split_reply`` segments a reply per speaker), so the unit here is the
*generation* — exactly what ``scenes.remove_trailing_assistant_run`` removes and
``scenes.append_reply`` records a boundary for — never a single message.

That generation is pinned by an ``anchor``: how many messages sit in front of
it. Never by an index — an index would not survive the reroll it exists to
describe, because ``remove_trailing_assistant_run`` re-parks trailing
scene-transition lines *after* the run it drops, so the replacement starts at a
different index than the one it replaced. Never by a block count either; see
``_slot`` for why counting *messages* is the property that holds. Anything that
genuinely moves the slot — a new turn, a trim, a hand edit that splices a
message in — changes the anchor, and the set is dropped rather than silently
re-pointed at a generation nobody archived it for.

**Reconciliation, not bookkeeping.** Nothing about *what is on screen* is
stored. ``_resolve`` derives the live variant by matching the stored runs
against the transcript: one that matches is the live one, and a live run
matching none of them is a variant the set has not seen yet, so it joins.

That is the whole rule, and it is what lets the regenerate route archive
*before* it streams and then walk away — nothing has to be decided when the
replacement lands, and a stream that dies between the two leaves the outgoing
run recoverable instead of gone. (``reconcile`` does run once the reply is
persisted, but only to write the derivation down: a run that exists solely in
the transcript would otherwise be destroyed by an edit of that transcript.)

It also means a hand edit of the live reply parks the pre-edit text as a
variant, which is the same promise the rest of the feature makes. An earlier
draft tried to treat an edit as a change to the live variant instead, which
needed a stored "a replacement is expected" flag — and that flag could not be
*cleared* by a read, only by the next write, so the two paths disagreed for as
long as nobody rerolled. A rule that a pure read can evaluate is worth more
here than the distinction it drew.

Retention is a cap on ``runs``, oldest first, never touching the live one.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import atomic, locks
from .appearances import cast as appearances_cast
from .scenes import (paths as scenes_paths, read as scenes_read,
                     serialize as scenes_serialize, turns as scenes_turns,
                     write as scenes_write)

#: How many variants one generation keeps. Trimmed oldest-first at write time:
#: a scene rerolled fifty times would otherwise carry fifty transcripts of
#: dead text beside a file whose whole point is the live one.
MAX_ALTERNATES = 8

#: How much of a reroll hint is kept beside the variant it produced. The hint
#: was transient before this store existed and is display-only now, so it gets a
#: bound: `guidance` is an unbounded string on the wire, and nothing else would
#: stop one request from parking megabytes in a file read on every scene open.
MAX_GUIDANCE_CHARS = 500


class AlternateNotFound(Exception):
    """No alternate at that position in this scene's current set."""


def _path(cid: str, sid: str) -> Path:
    return scenes_paths._alts_path(cid, sid)


def _round_tripped(speaker: str | None, content: str) -> dict | None:
    """One segment as the transcript would give it back, or None when the writer
    and the reader would disagree about it.

    Put through the real serializer rather than re-deriving its rules, because
    the rules are a grammar and a copy of them keeps missing cases. Two the copy
    missed, both of which desync `turn_sizes` on the spot — `append_reply`
    counts one model block, the reader sees something else:

    - ``You (Mara)`` is neither the exact reserved label nor an unsafe one, so a
      membership test accepts it; the marker grammar then reads the reserved
      *base* and calls the whole block a player line.
    - a blank-line-preceded ``**Name:**`` buried in the content splits one
      segment into two blocks.

    Only reachable from a hand-edited sidecar — `archive` reads its segments
    back off an already-parsed transcript, which cannot hold either shape.
    """
    parsed = scenes_serialize._parse_messages(
        scenes_serialize._block("assistant", speaker, content), frozenset())
    if len(parsed) != 1 or parsed[0]["role"] != "assistant":
        return None
    return {"speaker": parsed[0].get("speaker"), "content": parsed[0]["content"]}


def _canonical(segments: object) -> list[dict] | None:
    """Stored segments exactly as `append_reply` would write them, or None if
    this is not a shape worth offering at all.

    Normalising, not merely validating. `_resolve` decides which variant is live
    by comparing stored segments against a run read back *out of* the
    transcript, and the writer does not store what it is handed verbatim: it
    strips content, drops blank segments, and rewrites a speaker it will not
    honour. A hand-edited sidecar holding any other spelling of the same run
    would therefore never match what promoting it produces — so each promotion
    would file the normalised result as a *new* variant, report that one as
    active instead of the one asked for, and eat into the retention cap.
    Canonicalising here makes the comparison meaningful; `_round_tripped` is
    what decides the canonical form of a single segment.

    `archive` never produces a non-canonical segment (it reads them back off a
    parsed transcript), so everything below is about a file someone edited.
    """
    if not isinstance(segments, list):
        return None
    out: list[dict] = []
    for s in segments:
        if not isinstance(s, dict) or not isinstance(s.get("content"), str):
            return None
        speaker = s.get("speaker")
        if speaker is not None and not isinstance(speaker, str):
            return None
        content = s["content"].strip()
        if not content:
            continue                      # `append_reply` drops these
        seg = _round_tripped(speaker, content)
        if seg is None:
            return None
        # A synthetic speaker is internal metadata, never model output.
        # `append_reply` would count such a segment in `turn_sizes` while every
        # reader excludes it from the model blocks, desyncing the scene on the
        # spot — and a roll-tagged one would then block reroll forever with no
        # entry in rolls.json to justify it. Rejected rather than normalised: a
        # roll line is not a variant of anything.
        #
        # Tested on the round-tripped speaker, not the stored one, because the
        # marker grammar can *produce* a synthetic label from one that is not:
        # `Grimoire (⁣Roll)` is neither reserved nor synthetic as written, and
        # comes back as the bare `⁣Roll`. Checking the input is the same
        # rule-copy mistake `_round_tripped` exists to stop making — and it is
        # redundant besides, since a stored `⁣Roll` round-trips to itself.
        if seg["speaker"] in scenes_serialize.SYNTHETIC_SPEAKERS:
            return None
        out.append(seg)
    # An all-blank variant would promote to nothing at all: the swap would empty
    # the slot and leave the count pointing at a variant the transcript cannot
    # show. Never offer it.
    return out or None


def _read_raw(cid: str, sid: str) -> dict:
    """The stored record, or {} for absent/unreadable/malformed — the sidecar is
    a convenience beside the transcript and must never make a scene unopenable."""
    p = _path(cid, sid)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        return {}
    runs = []
    for r in data["runs"]:
        segments = _canonical(r.get("segments")) if isinstance(r, dict) else None
        if segments is not None:
            runs.append({**r, "segments": segments})
    return {**data, "runs": runs} if runs else {}


def _slot(cid: str, sid: str) -> dict:
    """Where the generation a reroll would replace sits, and where the
    transcript ends.

    ``{"gen": anchor | None, "segments": [...] | None, "end": anchor}``

    An **anchor** is how many messages sit in front of a point, not counting
    transition lines immediately before it — `remove_trailing_assistant_run`
    re-parks those around the run it drops, so counting them would make the
    number move when nothing did. Since the messages in front of a point are a
    *prefix* of the transcript, their count identifies them: two reads that
    agree on the anchor are looking at the same place.

    ``gen`` anchors the generation a reroll would replace; ``end`` anchors the
    transcript's end. ``end`` is not derivable from ``gen``: consecutive
    generations are possible (an empty send and a director turn both persist no
    player message), so removing the latest one leaves *another* generation at
    the tail. ``gen`` then describes that older one, and matching on it alone
    would read the set as stale and drop the reply it just parked.

    Counting *messages* rather than model blocks is deliberate, and was not
    always so. A block count moves when a message is **reclassified** without
    the transcript changing at all: role is not stored, so seating a player
    whose name matches an NPC label re-reads that historical block as a user
    message. The slot has not moved, but a block-count key stops matching and
    every parked variant becomes unreachable. A message count is blind to role,
    which is exactly the property a position key wants. (An edit rewriting a
    message in place is likewise not a move — the alternates still belong where
    they are — and content is what `_resolve` matches on anyway.)

    ``segments`` is None when there is nothing to alternate: an empty scene, one
    ending on a player line or a manual dice roll, or one whose recorded turn
    boundaries no longer fit its transcript (the case
    ``remove_trailing_assistant_run`` refuses on, so we archive nothing either).
    """
    messages = scenes_read.read_scene(cid, sid)["messages"]
    # trailing transitions sit ON TOP of the generation reroll targets
    core = messages[:len(messages) - scenes_read.trailing_transitions(messages)]
    # `core` has had its trailing transitions stripped, so its length is already
    # the anchor rule ("not counting transitions immediately before the point").
    end = len(core)
    nothing = {"gen": None, "segments": None, "end": end}
    if (not core or core[-1]["role"] != "assistant"
            or core[-1].get("speaker") in scenes_serialize.SYNTHETIC_SPEAKERS):
        return nothing
    sizes = scenes_turns.get_turn_sizes(cid, sid)
    if sizes:
        if not scenes_turns._tracked_suffix_fits(core, sizes):
            return nothing
        size = sizes[-1]
    else:
        size = scenes_turns._trailing_model_run(core)   # untracked: the whole run
    head, run = core[:len(core) - size], core[len(core) - size:]
    return {"gen": len(head) - scenes_read.trailing_transitions(head),
            "segments": [{"speaker": m.get("speaker"), "content": m["content"]} for m in run],
            "end": end}


def _unforged(segments: list[dict], players: frozenset[str]) -> list[dict]:
    """A variant as it would be *replayed* under the current cast: a stored
    speaker that now names a seated player loses it.

    This is `split_reply`'s "never store a forged player line" rule, re-applied
    at replay time. A variant is archived with the speakers it had, and the cast
    can change afterwards (a join appends a transition line, which deliberately
    does not retire the set). Once a stored NPC label matches a player name —
    exactly, or as the word-boundary prefix `match_name` also accepts —
    replaying it verbatim writes a block `append_reply` counts in `turn_sizes`
    but the next read parses as a *user* message. The counts then disagree with
    the transcript, which reads as desynced boundaries and makes reroll refuse.

    Applied to both sides of the match in `_resolve`, not just to what `promote`
    appends: two variants are "the same" when they would replay identically, so
    promoting one lands on the entry the caller picked instead of looking like a
    new variant. A run read out of the transcript needs no unforging — a
    player-named block parses as a user message and is not part of a model run
    at all — so this is the identity on that side.
    """
    return [{**s, "speaker": None}
            if s.get("speaker") and scenes_serialize.match_name(s["speaker"], players)
            else dict(s)
            for s in segments]


def _distinct(runs: list[dict], players: frozenset[str]) -> list[dict]:
    """The stored runs with replay-equivalent ones collapsed, earliest kept.

    Two variants that differ only by a speaker label become the *same* take once
    that character is seated as a player, because `_unforged` strips the label
    from both. Left as two entries they are indistinguishable and one of them is
    unreachable: `_resolve` reports the earlier as live, so promoting the later
    rewrites an identical transcript and resolves straight back — the ‹/›
    control sticks on the pair, and the swap retires a roll proposal while
    changing nothing on screen.

    Earliest kept, which is also always the live one when there is one: the
    active lookup below takes the first match, so the entry it selects is the
    one this keeps.

    The collapse reaches disk once something calls `reconcile` or `archive`,
    and is not undone by unseating the player later. That is the cost, and it
    is the right side of the trade: what it drops is a variant the reader
    cannot tell apart from one they still have, and what it buys is a control
    that is not wedged.
    """
    out: list[dict] = []
    seen: list[list[dict]] = []
    for r in runs:
        replayed = _unforged(r["segments"], players)
        if replayed in seen:
            continue
        seen.append(replayed)
        out.append(dict(r))
    return out


def _players(cid: str, sid: str) -> frozenset[str]:
    return frozenset(appearances_cast.player_names(cid, sid))


def _landed_at(cid: str, sid: str) -> str:
    """When the transcript last changed — the closest honest stamp for a variant
    reconciled out of it.

    `_resolve` is a pure read (a GET that wrote would have to take the campaign
    lock on every scene open), so a newly observed variant cannot be stamped
    "now": that is the *read's* time, and it would change on every read until
    something happened to persist it. The scene's own `updated` is when the
    reply landed, is stable across reads, and is already on disk.
    """
    return scenes_read.read_scene_meta(cid, sid).get("updated", "")


def _trimmed(runs: list[dict], active: int | None) -> tuple[list[dict], int | None]:
    """Enforce MAX_ALTERNATES, dropping oldest first and never the live run."""
    while len(runs) > MAX_ALTERNATES:
        drop = next((i for i in range(len(runs)) if i != active), 0)
        runs = runs[:drop] + runs[drop + 1:]
        if active is not None and drop < active:
            active -= 1
    return runs, active


def _resolve(cid: str, sid: str) -> dict:
    """The stored record reconciled against the live transcript.

    Returns ``{}`` when the scene has no usable set — either none was ever
    written, or the slot it was keyed to has moved and the variants describe a
    generation that is no longer the one reroll targets.
    """
    rec = _read_raw(cid, sid)
    if not rec:
        return {}
    slot = _slot(cid, sid)
    key = rec.get("anchor")
    if slot["segments"] is not None and slot["gen"] == key:
        live = slot["segments"]
    elif slot["end"] == key:
        live = None            # the transcript stops exactly here: the slot is empty
    else:
        return {}              # the slot moved; these variants describe another one
    anchor = key
    players = _players(cid, sid)
    runs = _distinct(rec["runs"], players)
    if live is None:
        active = None                       # the slot is empty (a reroll in flight)
    else:
        active = next((i for i, r in enumerate(runs)
                       if _unforged(r["segments"], players) == live), None)
        if active is None:
            runs.append({"created": _landed_at(cid, sid),
                         "guidance": rec.get("next_guidance", ""), "segments": live})
            active = len(runs) - 1
        elif rec.get("next_guidance"):
            # The model answered a guided reroll with text the set already had.
            # Deduplicating is right — two identical takes are one variant — but
            # the hint is spent either way, so leaving the matched run labelled
            # with the *older* instruction credits what is on screen to one it
            # was not generated from. The latest wins: it is the one that
            # actually produced this take.
            runs[active] = {**runs[active], "guidance": rec["next_guidance"],
                            "created": _landed_at(cid, sid)}
    runs, active = _trimmed(runs, active)
    # The hint is one-shot: it steers whatever fills the slot, and the run above
    # has just recorded it. Carrying it forward would re-label the *next* thing
    # to land there — a hand edit of that reply, most obviously — with an
    # instruction nothing about it received. Spent as soon as a live run
    # occupies the slot, matched or newly appended, since either way a
    # generation it steered is what produced what is on screen.
    pending = "" if live is not None else rec.get("next_guidance", "")
    return {"anchor": anchor, "next_guidance": pending, "runs": runs, "active": active}


def _write(cid: str, sid: str, rec: dict) -> None:
    """Persist a resolved record. `active` is dropped: it is derived from the
    transcript on every read, and a stored copy could only ever be a second
    answer to a question the transcript already answers."""
    atomic.write_text(_path(cid, sid),
                      json.dumps({k: v for k, v in rec.items() if k != "active"},
                                 indent=2) + "\n")


def _clear(cid: str, sid: str) -> None:
    _path(cid, sid).unlink(missing_ok=True)


def state(cid: str, sid: str) -> dict:
    """``{"active": int|None, "runs": [...]}`` for this scene's trailing
    generation. ``active`` is the variant currently in the transcript, and None
    means the slot is empty — the state a reroll whose stream died leaves, from
    which promoting a variant puts it back."""
    rec = _resolve(cid, sid)
    return {"active": rec["active"], "runs": rec["runs"]} if rec else {"active": None, "runs": []}


def variant_id(run: dict) -> str:
    """What a client should name a variant by, since its position is not its
    identity.

    Retention drops the oldest run when a set at MAX_ALTERNATES gains one, and
    every index below it shifts. A snapshot taken before that drop still names
    an *in-range* index afterwards — a different take — so an index-addressed
    pick silently promotes content nobody previewed. Derived from the segments
    rather than stored, for the same reason `created` could not be minted at
    read time: `_resolve` is a pure read, and an id it invents would change on
    every call until something persisted it. Content is already the identity
    `_resolve` matches on, it survives a shift, and it simply stops existing
    once the variant is trimmed away — so a stale pick 404s.
    """
    body = json.dumps(run["segments"], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def reconcile(cid: str, sid: str) -> None:
    """Write down what a read already derives, so a variant that has only ever
    existed in the transcript survives the next edit of it.

    `_resolve` folds a landed replacement into the set on every read — but the
    only copy of that run is the transcript's, because the sidecar holds just
    what was archived. Editing the live reply therefore rewrites the sole
    durable copy of a *generated* variant, and the next read rebuilds the set
    from the old sidecar plus the edited text: the pre-edit reply is not parked,
    it is gone. Deleting it loses it the same way.

    So the module docstring's "no callback has to fire when the replacement
    lands" holds for correctness — a read reconciles identically either way —
    but not for durability, and this is that callback. It is the smallest one
    possible: it tells the store nothing and decides nothing, it only persists
    the resolution. A reply landing anywhere else moves the anchor, `_resolve`
    returns {}, and nothing is written.
    """
    with locks.campaign_lock(cid):
        rec = _resolve(cid, sid)
        if rec:
            _write(cid, sid, rec)


def archive(cid: str, sid: str, guidance: str = "") -> None:
    """Keep the generation a reroll is about to replace. Called BEFORE the
    removal, so the outgoing run survives even a stream that never lands; the
    replacement joins the set on its own when the next read reconciles.
    `guidance` is the hint steering that replacement and is recorded against
    it, not against the run being archived."""
    with locks.campaign_lock(cid):
        slot = _slot(cid, sid)
        live = slot["segments"]
        rec = _resolve(cid, sid)
        if live is None:
            # Nothing to keep — the slot is already empty, which is where the
            # previous reroll's stream died. Still record the new hint: it is
            # the one steering whatever lands next, and that is the run it will
            # be shown against.
            if rec:
                _write(cid, sid, {**rec, "next_guidance": guidance[:MAX_GUIDANCE_CHARS]})
            return
        # `_resolve` has already folded the live generation into `runs`; an
        # empty result means this generation is the set's first variant.
        rec = rec or {"anchor": slot["gen"],
                      "runs": [{"created": _landed_at(cid, sid),
                                "guidance": "", "segments": live}]}
        _write(cid, sid, {**rec, "next_guidance": guidance[:MAX_GUIDANCE_CHARS]})


def disown_guidance(cid: str, sid: str) -> None:
    """Take the pending hint back, without reconciling on the way.

    `archive(cid, sid, "")` is the usual way to re-aim it and is right over an
    *empty* slot, where there is no live run to credit. Over a live one it
    resolves first — and resolving now stamps that run with the pending hint,
    which is the very attribution this is trying to undo.

    For the one caller that records a hint and then fails to make room for what
    it was aimed at: a reroll whose removal did not complete. The reply on
    screen is the one the hint was meant to replace, not something it produced.
    """
    with locks.campaign_lock(cid):
        rec = _read_raw(cid, sid)
        if rec.get("next_guidance"):
            _write(cid, sid, {**rec, "next_guidance": ""})


def promote(cid: str, sid: str, index: int) -> None:
    """Put variant `index` into the transcript, parking whatever was live.

    The swap goes through the same pair reroll uses — drop the trailing
    generation, append the chosen one — so turn boundaries, the `updated`
    stamp and trailing transition lines all land exactly as they do for a
    fresh reply, rather than through a second write path that has to remember
    the same rules.
    """
    with locks.campaign_lock(cid):
        rec = _resolve(cid, sid)
        if not rec or not 0 <= index < len(rec["runs"]):
            raise AlternateNotFound(index)
        if rec["active"] == index:
            return
        live = rec["active"] is not None
        # Persist the reconciliation before the swap, so a variant only ever
        # seen in a view is on disk before the transcript stops carrying it.
        _write(cid, sid, {**rec, "next_guidance": ""})
        if live:
            scenes_write.remove_trailing_assistant_run(cid, sid)
        scenes_write.append_reply(
            cid, sid, _unforged(rec["runs"][index]["segments"], _players(cid, sid)))


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids: carry each sidecar to its scene's new id.

    Reads every source before writing any target, so a mapping that swaps two
    ids does not have one scene's set land on top of the other's — and
    publishes before clearing anything, so a crash cannot leave a set existing
    only in memory. The caller has already renamed the transcript by then, so
    "the scene moved but its parked replies are gone" is the one outcome this
    must not produce. A mapping that is a true cycle (A→B and B→A) still has a
    window at the moment the second file is overwritten; no caller makes one —
    `repad` only widens ids and a rename moves a single id.

    Every DESTINATION is cleared, not just the ones receiving a file. A
    destination id is by definition changing hands, so a sidecar already sitting
    there belonged to some other scene and must not be inherited by the one
    moving in. `_sid_taken` keeps the *allocating* paths off an occupied id, but
    `repad` does not allocate — it must land on the width-normalised id, so an
    orphan there is only reachable to clean up here. Removing it is the correct
    end: its own transcript is gone, which is what left it an orphan.

    Bytes, not text: the caller has *already renamed the transcript* by the time
    this runs (`scenes.lifecycle.rename_scene`), so a sidecar that a hand edit
    left undecodable must not raise here — that would abandon the rename with
    the old scene id gone and the other six stores un-repointed. Moving the file
    verbatim also keeps it exactly as it was; unreadable content still reads as
    "no alternates" through `_read_raw`, which is where that judgement belongs.
    """
    with locks.campaign_lock(cid):
        moving, stranded = {}, set()
        for old in mapping:
            try:
                moving[old] = _path(cid, old).read_bytes()
            except FileNotFoundError:
                continue      # nothing to carry; its destination is still cleared
            except OSError:
                stranded.add(old)
                # Unreadable (a hand-chmod'd sidecar, a vanished file): leave it
                # where it is rather than abandoning the rename half-done. Moving
                # bytes already covers *undecodable* content, but the read itself
                # can still fail, and by now the transcript has moved — a raise
                # here means a 500 with the old scene id gone and the other six
                # stores un-repointed. The scene loses its alternates, which is
                # the same thing `_read_raw` reports for a file it cannot read,
                # and the leftover is an orphan `_sid_taken` already knows about.
                #
                # `stranded`, not a bare skip: the cleanup below sweeps every
                # source id, so a file this could not read would be *unlinked* —
                # turning "we could not carry your variants across" into "we
                # deleted them". Left untouched, they are still on disk for
                # whoever fixes the permission.
        # A source path stays on disk until its own bytes have been published,
        # so an interrupted repoint leaves every set readable at one path or the
        # other rather than only in this process's memory. Destinations that are
        # themselves sources go last, for the same reason: publishing over one
        # destroys the last durable copy of what it still owes elsewhere.
        published = set()
        for old in sorted(moving, key=lambda o: mapping[o] in moving):
            try:
                atomic.write_bytes(_path(cid, mapping[old]), moving[old])
            except OSError:
                # Same judgement as the unreadable source above, at the other
                # end: the caller has already renamed the transcript, and
                # `scene_refs.repoint` still owes appearances, audit, chronicle,
                # plot and rolls their new id. Raising here strands all of them
                # on an id whose scene is gone, to save a sidecar that is a
                # convenience. Keep the source instead — the set is still
                # readable at the old path — and let the fan-out finish.
                stranded.add(old)
                continue
            published.add(mapping[old])
        destinations = set(mapping.values())
        for sid in (*mapping, *mapping.values()):
            if sid in published or sid in stranded:
                continue
            if sid in destinations:
                # A DESTINATION must actually be cleared. It is changing hands,
                # so a sidecar sitting there belongs to some other scene — and
                # leaving it hands the transcript moving in someone else's
                # parked variants the moment their anchor happens to match.
                # There is no harmless outcome to fall back on here, unlike a
                # source, so this one is allowed to raise.
                _clear(cid, sid)
            else:
                try:
                    _clear(cid, sid)
                except OSError:
                    # A SOURCE, whose bytes are already published at their
                    # destination: refusing to unlink costs an orphan and
                    # nothing more — one `_sid_taken` already declines to hand
                    # out. Raising would abort the fan-out and leave every other
                    # store keyed to a scene id that no longer exists.
                    pass
