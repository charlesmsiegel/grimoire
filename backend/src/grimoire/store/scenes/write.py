"""Scene mutations: appending, editing and trimming the transcript, plus the
frontmatter flags a scene carries (dismissals, pcless, greeting, response
settings, the absorbed marker).

Every function that touches the scene file rewrites the whole of it, so every
one of those runs under `locking._serialized`; `split_reply` is the exception —
it parses a reply string and reads no file. Transcript and `turn_sizes` go out
in a single write — see `turns._set_turn_sizes`.
"""

from __future__ import annotations

from .. import atomic
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
def append_message(cid: str, sid: str, role: str, content: str, speaker: str | None = None) -> None:
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    body = serialize._append_block(body, serialize._block(role, speaker, content))
    meta["updated"] = now_iso()
    atomic.write_text(p, dump_frontmatter(meta, body))


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
def remove_trailing_assistant_run(cid: str, sid: str) -> None:
    """Drop the trailing run of assistant-side messages (one turn's output).

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
        del messages[len(messages) - sizes[-1]:]
        sizes = sizes[:-1]
    else:
        # Untracked (or unparseable, which means the same thing): the whole
        # trailing model run comes off, as it did before boundaries existed.
        del messages[len(messages) - turns._trailing_model_run(messages):]
    meta["updated"] = now_iso()
    turns._set_turn_sizes(meta, sizes)
    atomic.write_text(p, dump_frontmatter(
        meta, serialize._serialize_messages(messages + tail)))


@locking._serialized
def remove_trailing_user_post(cid: str, sid: str, content: str) -> bool:
    """Take back the trailing user message, if it is still the one described.

    The undo half of a transactional turn (#95): a chat turn appends the
    player's post before streaming, and a generation that fails having produced
    nothing would otherwise leave that post sitting unanswered, indistinguishable
    from one the model chose to skip.

    Conditional on purpose, and returning whether it fired. The post is only
    removed while it is genuinely the last message AND still carries the content
    the caller wrote — anything appended behind it (a manual dice roll, a scene
    transition, a reply from a concurrent turn) means the transcript has moved
    on, and deleting from under that is worse than leaving one orphan. Content
    is the discriminator rather than an index because indices go stale the
    moment anything else writes, and this runs after an LLM call, not before.

    `turn_sizes` is untouched deliberately: it counts model blocks
    (`turns._model_blocks`), and a user post has never been one of them.

    Role comes from `read_scene`, which resolves a speaker marker against the
    scene's PCs — so a post whose speaker has since left the cast reads back as
    model output and is left alone. That is the safe direction: an orphan
    survives, which a player can delete, where the alternative is this deleting
    a reply it misread.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    messages = read.read_scene(cid, sid)["messages"]
    if not messages:
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
