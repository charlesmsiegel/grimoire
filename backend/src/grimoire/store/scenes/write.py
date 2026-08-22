"""Scene mutations: appending, editing and trimming the transcript, plus the
frontmatter flags a scene carries (dismissals, pcless, greeting, response
settings, the absorbed marker).

Every function that touches the scene file rewrites the whole of it, so every
one of those runs under `locking._serialized`; `split_reply` is the exception —
it parses a reply string and reads no file. Transcript and `turn_sizes` go out
in a single write — see `turns._set_turn_sizes`.
"""

from __future__ import annotations

from .. import atomic, rolling_summary, scene_break, turnstate
from ..appearances import cast
from ..frontmatter import dump_frontmatter, parse_frontmatter
from ..paths import now_iso, safe_id
from . import locking, paths, read, serialize, turns


@locking._serialized
def add_dismissed(cid: str, sid: str, char_id: str) -> None:
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    current = [x for x in meta.get("dismissed", "").split(",") if x]
    if char_id not in current:
        current.append(char_id)
    meta["dismissed"] = ",".join(current)
    atomic.write_text(p, dump_frontmatter(meta, body))


@locking._serialized
def stamp_greeting(cid: str, sid: str, gid: str) -> None:
    """Record the greeting this scene was started from (plot-map unlock linkage)."""
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["greeting"] = gid
    atomic.write_text(p, dump_frontmatter(meta, body))


@locking._serialized
def set_pcless(cid: str, sid: str) -> None:
    """Flag a scene as deliberately player-less (an offscreen greeting stamps it)."""
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["pcless"] = "true"
    atomic.write_text(p, dump_frontmatter(meta, body))


RESPONSE_FIELDS = ("response_preset", "style_id", "length_reply_words", "length_blocks",
                   "length_paragraphs", "length_speakers", "length_blocks_per_speaker")


@locking._serialized
def set_response(cid: str, sid: str, fields: dict) -> None:
    """Write scene-scope response settings. An empty value clears the field
    (inherit); a key that is absent from `fields` is left untouched."""
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    for key in RESPONSE_FIELDS:
        if key in fields:
            meta[key] = str(fields[key] or "")
    atomic.write_text(p, dump_frontmatter(meta, body))


@locking._serialized
def append_message(cid: str, sid: str, role: str, content: str,
                   speaker: str | None = None) -> int:
    """Append one message; returns the index it landed at.

    The index is read under the same lock as the write, which is what makes it
    usable as an identity later (`remove_trailing_user_post`). Counted from the
    body being appended to rather than by re-reading afterwards: a second read
    would be a second chance for another writer to get in first, which is
    precisely the race the index exists to detect.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    # Markers, not parsed messages: `_parse_messages` counts exactly these and
    # differs only in resolving each one's role against the cast, which cannot
    # change how many there are. Counting them directly keeps the index free of
    # any dependence on who is currently in the scene.
    index = len(serialize._markers(body))
    body = serialize._append_block(body, serialize._block(role, speaker, content))
    meta["updated"] = now_iso()
    atomic.write_text(p, dump_frontmatter(meta, body))
    return index


@locking._serialized
def append_messages(cid: str, sid: str, messages: list[dict],
                    turn_sizes: list[int] | None = None) -> int:
    """Append MANY messages in one read-modify-write; returns the first index.

    `append_message` in a loop is one lock acquisition, one whole-file read and
    one whole-file write per message, so writing n messages costs O(n²) bytes
    and holds the campaign lock n times. That is invisible for the one-message
    appends the play loop makes and ruinous for the only caller that appends a
    whole transcript at once: importing a 3200-post log took 27 seconds of
    continuous acquire/release, starving every other writer in that campaign
    (#92). This is the same shape `append_reply` already uses for a multi-block
    reply, generalized to messages that are not one generation.

    `turn_sizes` is staged into the SAME write, never a second one, for the
    reason `turns._set_turn_sizes` gives: a crash between the transcript write
    and the boundary write leaves the boundaries describing a transcript that
    does not exist, and the next reroll trusts them. Callers pass it only when
    it actually describes these messages -- `turns._tracked_suffix_fits` is the
    check -- and otherwise leave it None, which keeps the scene untracked
    rather than mistracked.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    index = len(serialize._markers(body))
    # Joined once, not accumulated. `_append_block` copies the whole body twice
    # per call (`rstrip` re-copies), so appending in a loop is quadratic in
    # BYTES even when it is one file write -- 5000 posts of ordinary length
    # measured 7.8s of pure string copying against 3.8ms for the join, all of
    # it inside the campaign lock. Fixing the I/O without fixing this fixes the
    # smaller half.
    blocks = [serialize._block(m["role"], m.get("speaker"), m["content"]) for m in messages]
    if blocks:
        tail = "\n\n".join(b.rstrip("\n") for b in blocks) + "\n"
        body = (body.rstrip() + "\n\n" + tail) if body.strip() else tail
    if turn_sizes is not None:
        turns._set_turn_sizes(meta, turn_sizes)
    meta["updated"] = now_iso()
    atomic.write_text(p, dump_frontmatter(meta, body))
    return index


@locking._serialized
def append_reply(cid: str, sid: str, segments: list[dict]) -> None:
    """Persist ONE model generation as per-speaker posts, recording its block
    count as a turn boundary.

    The single entry point for model output, because boundaries cannot be
    recovered later: ephemeral director notes and empty sends are never
    persisted, so consecutive generations leave no user message between them.
    Counts rather than indices — a message EDIT leaves counts untouched, where
    indices would need rewriting on every edit.

    Blocks and their boundary are written in ONE file write. Appending each
    segment separately and recording the count afterwards leaves a window where
    untracked blocks sit at the tail: drift segmentation would then misalign the
    recorded boundaries, and — worse — reroll would consume `sizes[-1]` blocks
    counting back through the orphans into the previous completed reply,
    destroying transcript that was never meant to be rerolled.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    kept = [s for s in segments if s["content"].strip()]
    if not kept:
        return
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    for seg in kept:
        body = serialize._append_block(
            body, serialize._block("assistant", seg.get("speaker"), seg["content"]))
    sizes = turns._parse_turn_sizes(meta.get("turn_sizes", "")) + [len(kept)]
    meta["turn_sizes"] = ",".join(str(n) for n in sizes)
    meta["updated"] = now_iso()
    atomic.write_text(p, dump_frontmatter(meta, body))


@locking._serialized
def stamp_user_speaker(cid: str, sid: str, name: str) -> None:
    """Backfill: give every speakerless user message the (sole) player's name."""
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    messages = read.read_scene(cid, sid)["messages"]
    stamped = False
    for m in messages:
        if m["role"] == "user" and not m.get("speaker"):
            m["speaker"] = name
            stamped = True
    if not stamped:
        return
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    atomic.write_text(p, dump_frontmatter(meta, serialize._serialize_messages(messages)))


def split_reply(text: str, players: frozenset[str]) -> list[dict]:
    """Split one model reply into per-speaker segments on the marker grammar.
    Unlabeled leading text, reserved labels, and player-named blocks (never
    store a forged player line) all go to the narrator (speaker None)."""
    text = text.strip()
    matches = serialize._markers(text)
    segments: list[dict] = []

    def add(speaker: str | None, content: str) -> None:
        if content.strip():
            segments.append({"speaker": speaker, "content": content.strip()})

    add(None, text[:matches[0].start()] if matches else text)
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        speaker, role = serialize._speaker_and_role(m, players)
        add(None if role == "user" else speaker, text[m.end():end])
    return segments


@locking._serialized
def remove_trailing_assistant_run(cid: str, sid: str) -> dict:
    """Drop the trailing run of assistant-side messages (one turn's output).

    Returns what it took, so the caller can put it back. Reroll deletes the old
    reply *before* generating its replacement — the context builders read the
    transcript, so the reply has to be gone before the model is asked for
    another — and a generation that then produces nothing would otherwise have
    destroyed a reply the player still had (#95). The token is opaque; feed it
    to `restore_trailing_assistant_run`.

    Trailing scene-transition lines are stepped over and PRESERVED in order —
    the generation removed is the last one beneath them. Stops at (and refuses
    to touch) a manual dice-roll line: rerolling must never delete a roll's
    transcript entry while it still lives in rolls.json.

    Transcript and boundaries land in ONE write; see _set_turn_sizes.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    messages = read.read_scene(cid, sid)["messages"]
    keep = len(messages) - read.trailing_transitions(messages)
    tail = messages[keep:]          # transitions, preserved verbatim and in order
    messages = messages[:keep]
    if (not messages or messages[-1]["role"] != "assistant"
            or messages[-1].get("speaker") in serialize.SYNTHETIC_SPEAKERS):
        raise IndexError("no trailing assistant reply")
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    sizes = turns._parse_turn_sizes(meta.get("turn_sizes", ""))
    if sizes:
        # Validate BEFORE deleting. Hand-edited or externally-corrupted
        # boundaries must not authorize a deletion this large.
        if not turns._tracked_suffix_fits(messages, sizes):
            raise turns.TurnSizesDesynced(sid)
        # Remove EXACTLY the last recorded generation. A role-based trailing-run
        # removal would eat every consecutive generation, because director turns
        # persist no user message to stop at — deleting more than the caller
        # asked to reroll, and desyncing turn_sizes permanently.
        cut = len(messages) - sizes[-1]
        removed, size = messages[cut:], sizes[-1]
        del messages[cut:]
        sizes = sizes[:-1]
    else:
        # Untracked (or unparseable, which means the same thing): the whole
        # trailing model run comes off, as it did before boundaries existed.
        cut = len(messages) - turns._trailing_model_run(messages)
        removed, size = messages[cut:], None
        del messages[cut:]
    meta["updated"] = now_iso()
    turns._set_turn_sizes(meta, sizes)
    atomic.write_text(p, dump_frontmatter(
        meta, serialize._serialize_messages(messages + tail)))
    # Retire the transient-state ledger from the cut (#120). Here rather than in
    # the callers, because there are two and they must not diverge: the reroll
    # route, and `alternates.promote`, which swaps a parked take in through this
    # same pair. Deleting the generation is what invalidates its recorded
    # mood/intent/posture, so the invalidation belongs where the deletion is.
    #
    # `cut`, not the post-removal length: trailing transition lines are preserved
    # and re-appended, so the replacement lands ABOVE where the old generation
    # sat. Superseding from the new landing index would step over the dead entry
    # and leave it describing what is now a transition line.
    #
    # Not fatal: the transcript is already written. A ledger that will not write
    # must not turn a completed reroll into a failed one -- same judgement
    # `_persist_reply` makes about this file.
    #
    # Captured into the token BEFORE it is dropped, so `restore_trailing_
    # assistant_run` can put it back with the reply. Reroll deletes before it
    # generates, and a generation that then fails, is cancelled, or says nothing
    # but a tracker block puts the original reply back -- restoring its
    # narration while its recorded mood stayed deleted would leave the reply
    # visibly present and silently unaccounted for in the next prompt.
    parked: list = []
    try:
        parked = [e for e in turnstate.entries(cid, sid) if e[0] >= cut]
        turnstate.supersede(cid, sid, cut)
    except OSError:
        pass
    # `kept` is the transcript this leaves behind, transitions excluded, and it
    # is what the restore checks it still sees: anything written since means the
    # tail is no longer the one this took from.
    return {"messages": removed, "size": size, "kept": len(messages),
            "turnstate": parked}


@locking._serialized
def restore_trailing_assistant_run(cid: str, sid: str, token: dict) -> bool:
    """Put back what `remove_trailing_assistant_run` took, if nothing has been
    written since. Returns whether it fired.

    Reroll's deletion and its replacement are two writes with a model call in
    between, so a generation that produces nothing leaves the player short one
    reply they never asked to lose (#95). This is the other half, and it is
    conditional for the same reason the user-post undo is: a transcript that
    moved on underneath belongs to something else now.

    Trailing transition lines are stepped over, exactly as the removal stepped
    over them, so the restored run goes back beneath them rather than on top.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    if not token or not token.get("messages"):
        return False
    messages = read.read_scene(cid, sid)["messages"]
    keep = len(messages) - read.trailing_transitions(messages)
    if keep != token["kept"]:
        return False
    body = messages[:keep] + list(token["messages"]) + messages[keep:]
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    sizes = turns._parse_turn_sizes(meta.get("turn_sizes", ""))
    if token.get("size"):
        # Only when the removal took a *tracked* generation. Pushing a boundary
        # onto a scene whose sizes were unparseable would invent tracking that
        # was never there, and reroll trusts sizes[-1] absolutely.
        sizes = sizes + [token["size"]]
    meta["updated"] = now_iso()
    turns._set_turn_sizes(meta, sizes)
    atomic.write_text(p, dump_frontmatter(meta, serialize._serialize_messages(body)))
    # The transient state the removal parked, back at the indices it held. Safe
    # to re-file at those exact indices because the restore is refused unless
    # the transcript below the trailing transitions is still the one the removal
    # left (`keep != token["kept"]`), so the reply goes back exactly where it
    # came from. AFTER the write, and never fatal: the reply is what matters,
    # and a ledger that will not write must not report the restore as refused.
    try:
        for idx, actors in token.get("turnstate") or []:
            turnstate.record(cid, sid, idx, actors)
    except OSError:
        pass
    return True


@locking._serialized
def remove_trailing_user_post(cid: str, sid: str, index: int, content: str) -> bool:
    """Take back the user message `append_message` put at `index`, if it is
    still the last one and still says what was written there.

    The undo half of a transactional turn (#95): a chat turn appends the
    player's post before streaming, and a generation that fails having produced
    nothing would otherwise leave that post sitting unanswered, indistinguishable
    from one the model chose to skip.

    Conditional on purpose, and returning whether it fired. Anything appended
    behind the post (a manual dice roll, a scene transition, a reply from a
    concurrent turn) means the transcript has moved on, and deleting from under
    that is worse than leaving one orphan.

    The index is load-bearing, not belt-and-braces — review caught the version
    that matched on content alone. The campaign lock serializes each append and
    each removal, but emphatically NOT the LLM call between them, so two
    overlapping turns can both append. If they carry the same text (a retried
    send, a second tab, a replay), the earlier turn's undo would find the
    *later* turn's post at the tail, match it on content, and delete a post
    whose generation is still running — destroying live work while leaving the
    orphan it was aiming at. Requiring the post to still sit at the index its
    own append reported makes that case a refusal instead: the second append
    moved the tail, so the first turn's undo declines and the orphan survives,
    which is the direction this function is allowed to be wrong in.

    `turn_sizes` is untouched deliberately: it counts model blocks
    (`turns._model_blocks`), and a user post has never been one of them.

    Role comes from `read_scene`, which resolves a speaker marker against the
    scene's PCs — so a post whose speaker has since left the cast reads back as
    model output and is left alone. Same safe direction: an orphan survives,
    which a player can delete, where the alternative is this deleting a reply it
    misread.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    messages = read.read_scene(cid, sid)["messages"]
    if len(messages) != index + 1:
        return False
    last = messages[-1]
    if last["role"] != "user" or last["content"].strip() != content.strip():
        return False
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["updated"] = now_iso()
    atomic.write_text(p, dump_frontmatter(meta, serialize._serialize_messages(messages[:-1])))
    return True


@locking._serialized
def trim_continuation(cid: str, sid: str, from_index: int) -> None:
    """Roll a scene back to `from_index`, discarding a crashed or
    superseded continuation attempt (proposals.commit_narration's crash
    recovery) — except `ROLL_SPEAKER` messages at or past that index,
    which are preserved in order. The trim-safety rule: manual dice-roll
    lines are the only non-superseding writer active during the crash
    window; our own continuation segments never carry ROLL_SPEAKER."""
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    messages = read.read_scene(cid, sid)["messages"]
    kept = messages[:from_index] + [
        m for m in messages[from_index:] if m.get("speaker") == serialize.ROLL_SPEAKER]
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    # Trimming drops model blocks, so the tracked suffix must shrink to match or
    # segmentation is left describing blocks that no longer exist. Whole
    # generations come off: a partially-trimmed one is not a generation worth
    # measuring.
    #
    # Clamp against TRACKED blocks, not every retained assistant block. On an
    # upgraded scene the untracked legacy prefix would otherwise absorb the
    # difference — 10 legacy blocks plus sizes [1, 2] trimmed back to the first
    # tracked turn still leaves 11 blocks, so a total-based comparison keeps the
    # stale 2 and segmentation then reads legacy messages as a turn.
    #
    # Body and boundaries go out in ONE write (see _set_turn_sizes): a crash
    # between them would leave sizes describing blocks this call just deleted.
    sizes = turns._parse_turn_sizes(meta.get("turn_sizes", ""))
    prefix = max(len(turns._model_blocks(messages)) - sum(sizes), 0)  # untracked legacy blocks
    tracked_after = max(len(turns._model_blocks(kept)) - prefix, 0)
    while sizes and sum(sizes) > tracked_after:
        sizes.pop()
    meta["updated"] = now_iso()
    turns._set_turn_sizes(meta, sizes)
    atomic.write_text(p, dump_frontmatter(meta, serialize._serialize_messages(kept)))
    # Retire the transient-state ledger from the same index (#120). The tail
    # filter does not cover this: preserved ROLL_SPEAKER lines are compacted
    # DOWN toward `from_index`, so a roll can land on the index a crashed
    # continuation's tracker entry holds, leaving the entry pointing at a post
    # that still exists and is no longer the narration it described. Never
    # fatal, for the reason the removal's own supersede is not.
    try:
        turnstate.supersede(cid, sid, from_index)
    except OSError:
        pass


@locking._serialized
def delete_from(cid: str, sid: str, index: int) -> int:
    """Cut the transcript at `index`: that post and everything after it go (#75).

    Returns how many were removed. `IndexError` for an index outside
    `0 <= index < len(messages)` — a cut that removes nothing is a caller bug,
    not a no-op, and the route turns it into a 400.

    This is the arbitrary cut point `remove_trailing_assistant_run` is not. That
    one takes exactly the last recorded generation and is the reroll primitive;
    this one is the player deciding the scene went wrong three posts ago.

    Nothing below the cut is preserved, and the two exceptions the other
    truncators make are deliberately absent:

    - **Trailing scene-transition lines are NOT stepped over.** Reroll steps over
      them because it is replacing the generation beneath them and the player's
      join/leave/move still happened. A cut at `index` is a claim about the
      transcript from `index` on, and a transition sitting inside that span is
      part of what is being discarded.
    - **Manual dice-roll lines go too**, where `edit_message` refuses to touch
      one and `trim_continuation` re-parks them. Both of those are protecting a
      line whose content must stay in lockstep with an immutable `rolls.json`
      entry — during a reroll, or during crash recovery, neither of which the
      player asked for. Here they did. The ledger entry survives (`rolls` is
      append-only by design and never drops one), so what is lost is the
      transcript line, not the record that the roll happened; the route says so
      in the confirmation the player sees.

    `turn_sizes` is clamped exactly as `trim_continuation` clamps it, and against
    TRACKED blocks for the same reason: on a scene with an untracked legacy
    prefix a total-based comparison keeps a stale boundary and segmentation then
    reads legacy messages as a turn. Body and boundaries go out in ONE write —
    see `turns._set_turn_sizes`.

    `location_history` and `time_history` are rolled back with the transcript,
    which is what `_rewound_history` is for and why this is not merely a slice.
    Both are the scene's OWN state and both poison every later turn if left: the
    last entry of each is the scene's current setting and current moment, and
    `chronicle.scene_facts` feeds them straight into the absorb prompt and the
    rolling-summary facts digest while the context builder puts the location in
    front of the model. A cut back past the move to the wharf that leaves the
    scene at the wharf is a scene prompted somewhere its transcript never goes.

    Everything else the frontmatter carries is deliberately untouched:
    `dismissed` is a per-scene preference, not a consequence of a post; the
    rolling summary invalidates itself (`routes.scenes._rolling_view` refuses a
    stored `at` past the transcript's length and re-checks the digest); and cast
    membership is `appearances`' record, not this file's — see `store/cascade.py`
    for why a join is not un-done.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    messages = read.read_scene(cid, sid)["messages"]
    if index < 0 or index >= len(messages):
        raise IndexError(index)
    kept = messages[:index]
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    sizes = turns._parse_turn_sizes(meta.get("turn_sizes", ""))
    prefix = max(len(turns._model_blocks(messages)) - sum(sizes), 0)  # untracked legacy blocks
    tracked_after = max(len(turns._model_blocks(kept)) - prefix, 0)
    while sizes and sum(sizes) > tracked_after:
        sizes.pop()
    for key, kind in (("location_history", "location"), ("time_history", "time")):
        rewound = _rewound_history(meta.get(key, ""), messages, kept, kind)
        if rewound is not None:
            meta[key] = rewound
    meta["updated"] = now_iso()
    turns._set_turn_sizes(meta, sizes)
    atomic.write_text(p, dump_frontmatter(meta, serialize._serialize_messages(kept)))
    return len(messages) - index


def _rewound_history(raw: str, before: list[dict], after: list[dict],
                     kind: str) -> str | None:
    """`location_history` / `time_history` as the surviving transcript leaves it,
    or None to leave the stored value alone.

    The mapping is exact by construction: `moment.set_location` and
    `moment._apply_datetime` append one entry per move, and every move but the
    FIRST also appends one transition line (the first is silent — there is no
    "from" to narrate). So n entries imply exactly n-1 lines, and the entries a
    cut leaves standing are one more than the lines it leaves standing.

    Which makes the accounting check the whole safety argument, and it is the
    same discipline `turns._tracked_suffix_fits` applies before a deletion:
    **validate first, and let data that does not add up authorize nothing.** The
    classifier reads a line's prose (`serialize.transition_kind`), so a scene
    written by an older build with different wording, or one whose transition
    lines were edited or hand-placed, will not tally — and every one of those
    cases returns None and keeps what is stored. Trimming a history on a
    miscount would silently move a scene's setting to somewhere the player never
    left, which is worse than the stale value this is fixing.
    """
    entries = [x for x in raw.split(",") if x]
    if not entries:
        return None
    lines = sum(1 for m in before if serialize.transition_kind(m) == kind)
    if lines != len(entries) - 1:
        return None                 # the two do not account for each other
    survivors = sum(1 for m in after if serialize.transition_kind(m) == kind)
    return ",".join(entries[:survivors + 1])


@locking._serialized
def unmark_absorbed(cid: str, sid: str) -> None:
    """Undo `mark_absorbed`: the scene is unfinished again and may be re-absorbed.

    The keys are REMOVED rather than blanked. `list_scenes` and
    `routes.scenes._already_absorbed` both read `done` out of a hand-editable
    file and compare case-insensitively against `"true"`, so an empty value would
    read as unfinished either way — but `one_line` and `summary` are rendered
    wherever they are non-empty, and a scene carrying the summary of a transcript
    that has been cut in half is worse than one carrying none.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    for key in ("done", "one_line", "summary"):
        meta.pop(key, None)
    meta["updated"] = now_iso()
    atomic.write_text(p, dump_frontmatter(meta, body))


class RollMessageImmutable(Exception):
    """Raised when editing a manual dice-roll transcript line is attempted —
    its content must stay in lockstep with the immutable rolls.json entry."""


@locking._serialized
def edit_message(cid: str, sid: str, index: int, content: str) -> None:
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    players = frozenset(cast.player_names(cid, sid))
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    messages = serialize._parse_messages(body, players)
    if index < 0 or index >= len(messages):
        raise IndexError(index)
    if messages[index].get("speaker") == serialize.ROLL_SPEAKER:
        raise RollMessageImmutable(index)
    before = turns._model_blocks(messages)
    messages[index]["content"] = content.strip()
    # Re-parse the body we are about to store rather than reading it back after
    # writing: the edited text is re-split at read time, so the new block count
    # is only knowable from the serialized form — and body and boundaries have
    # to go out together (see _set_turn_sizes).
    new_body = serialize._serialize_messages(messages)
    after = turns._model_blocks(serialize._parse_messages(new_body, players))
    meta["updated"] = now_iso()
    turns._set_turn_sizes(meta, turns._reconciled_turn_sizes(
        turns._parse_turn_sizes(meta.get("turn_sizes", "")), before, after, index))
    atomic.write_text(p, dump_frontmatter(meta, new_body))


@locking._serialized
def set_rolling_summary(cid: str, sid: str, summary: str, at: int, digest: str,
                        facts: str = "") -> None:
    """Record the live running summary and what it was folded from (#85).

    The four keys move together and are only meaningful together: prose, the
    length of the prefix it covers, that prefix's digest, and a digest of the
    scene facts the prompt was given. Writing them in one file write is what
    keeps a reader from seeing a new summary against an old digest and deciding
    the fold is stale when it is not.

    `facts` is a separate key rather than part of `digest` because the two
    invalidate for different reasons -- the transcript changing under the fold,
    versus a fact changing with no transcript change at all, which the silent
    first-location and first-date writes do.

    The summary is collapsed to a single line HERE, not merely by whoever parsed
    the model's reply. `dump_frontmatter` writes one line per key and `_quote`
    does not escape newlines, so a value containing one is written as a second
    physical line -- read back as a junk key if it holds a colon, dropped if it
    does not, and, beginning `---`, as the end of the frontmatter block, taking
    every key after it with it. That is scene-file corruption from a model reply,
    so the guarantee belongs to the store rather than to a caller remembering.

    `updated` is deliberately NOT stamped, and it is load-bearing that it is not.
    Two readers treat that stamp as "when the TRANSCRIPT last changed", which a
    summary write does not do: `list_scenes` orders the scene rail by it, so a
    background refresh would make the rail jump for a write the player never
    made, and `alternates._landed_at` stamps a newly observed reroll variant
    with it -- so a refresh landing between the reply and the variant's
    reconciliation would misdate the variant to the summary rather than to the
    generation it came from.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["rolling_summary"] = " ".join(summary.split())
    meta["rolling_at"] = str(max(0, at))
    meta["rolling_digest"] = digest
    meta["rolling_facts"] = facts
    atomic.write_text(p, dump_frontmatter(meta, body))


@locking._serialized
def set_scene_break(cid: str, sid: str, at: int, locs: int, times: int,
                    digest: str = "", verdict: str = "", reason: str = "",
                    title: str = "") -> None:
    """Record what a scene-break question covered, and what it answered (#84).

    The watermark and the proposal go out in one write because they are only
    meaningful together: the watermark says which posts, moves and clock
    advances have already been considered, and the verdict is what was
    concluded about exactly those. A reader that saw a new verdict against an
    old watermark would show the answer to a question nobody asked.

    `digest` is `rolling_summary.covered_digest` over the prefix the counts
    describe, and it travels with them for the same reason it does there: the
    transcript is not append-only, so a count alone cannot say whether the
    posts it claims to have covered are still the posts on disk.

    Defaulting `verdict`/`reason`/`title` to empty is how a DISMISSAL is
    written -- the counts move forward, the proposal is cleared -- so
    `dismiss_scene_break` is this function with the counts read off the same
    file, rather than a second way to write these six keys.

    `reason` and `title` are collapsed to one line HERE, for
    `set_rolling_summary`'s reason: `dump_frontmatter` writes one line per key
    and `_quote` does not escape newlines, so a model reply containing one is
    written as a second physical line and read back as junk -- or, if it begins
    `---`, as the end of the frontmatter block, taking every key after it.
    Scene-file corruption from a model reply is the store's to prevent, not a
    caller's to remember.

    `updated` is deliberately NOT stamped, for `set_rolling_summary`'s reason:
    two readers treat that stamp as "when the transcript last changed", and a
    background question is not the player writing anything.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["break_at"] = str(max(0, at))
    meta["break_locs"] = str(max(0, locs))
    meta["break_times"] = str(max(0, times))
    meta["break_digest"] = digest
    meta["break_verdict"] = verdict
    meta["break_reason"] = " ".join(reason.split())
    meta["break_title"] = " ".join(title.split())
    atomic.write_text(p, dump_frontmatter(meta, body))


@locking._serialized
def dismiss_scene_break(cid: str, sid: str) -> None:
    """Retire a scene-break proposal, and start counting again from here (#84).

    "Not here" is an answer about the scene as it stands, so the watermark
    moves to the scene as it stands: the transcript's current length and both
    histories' current move counts. That is what stops a dismissed suggestion
    from being re-earned by the same posts on the very next evaluation, and it
    is why dismissal is a mutator rather than a client-side flag.

    The counts are read from the file this write already holds open under the
    lock, NOT handed in by the caller. A caller that read them first would be
    dismissing a scene it saw before the post that landed in between, leaving
    that post outside both the retired question and the next one.

    Returns nothing. An earlier draft returned the watermark it wrote "so the
    route need not read the file again", which was not true of any caller: what
    the route has to answer with is a SCORED view -- signals, score, whether
    anything is still due -- and no watermark can stand in for that. A return
    value no caller can use is one a later caller will use wrongly.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    scene = read.read_scene(cid, sid)
    history = read.histories(scene["meta"])
    at = len(scene["messages"])
    locs = scene_break.moves(history["locations"])
    times = scene_break.moves(history["times"])
    # Digested here too, off the same read: a dismissal's watermark has to
    # survive a later rewind exactly as a question's does, and one written
    # without a digest would be a watermark no reader could check.
    digest = rolling_summary.covered_digest(scene["messages"])
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["break_at"], meta["break_locs"], meta["break_times"] = str(at), str(locs), str(times)
    meta["break_digest"] = digest
    meta["break_verdict"] = meta["break_reason"] = meta["break_title"] = ""
    atomic.write_text(p, dump_frontmatter(meta, body))


@locking._serialized
def mark_absorbed(cid: str, sid: str, one_line: str, summary: str) -> None:
    """Record a scene's absorbed summary into its frontmatter and flag it done."""
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["one_line"] = one_line
    meta["summary"] = summary
    meta["done"] = "true"
    meta["updated"] = now_iso()
    atomic.write_text(p, dump_frontmatter(meta, body))
