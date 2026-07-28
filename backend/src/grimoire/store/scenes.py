"""Scene CRUD — chat transcripts living under <campaign>/scenes/."""

from __future__ import annotations

import re
from pathlib import Path

from . import atomic, calendars, campaigns, overlay, scene_ids, scene_refs
from .frontmatter import dump_frontmatter, parse_frontmatter, parse_frontmatter_head
from .llm_connections import get_active as _get_active_connection
from .paths import now_iso, slugify, uniquify

# The body is a script: every message is `**<Speaker>:** content`. Role is not
# stored — a message is user-side iff its speaker is "You" or a role=player
# cast member's name (derived in read_scene). Reserved labels keep legacy
# files working; their parens sub-speaker form is read but never written.
RESERVED_LABELS = {"You": "user", "Grimoire": "assistant"}
ROLE_TO_LABEL = {"user": "You", "assistant": "Grimoire"}
# Manual dice rolls are appended as assistant-role messages (so they render
# like any other transcript line) but tagged with this speaker so reroll
# logic can tell them apart from an actual LLM reply — rerolling must never
# silently drop a roll line while its entry lives on in rolls.json. Prefixed
# with U+2063 (invisible separator, no visible glyph) so the marker can never
# collide with an ordinary typed speaker label or cast name — a real
# character or NPC named "Roll" round-trips as plain "Roll", not this.
ROLL_SPEAKER = "⁣Roll"
# Scene transitions (location change, time advance, cast join/leave) are
# appended as assistant-role messages so they render inline, but no model wrote
# them. Tagged with this speaker so drift measurement can treat them as turn
# SEPARATORS rather than counting them as model prose — untagged, a transition
# between two replies merges them into one apparently-oversized turn. Same
# U+2063 prefix as ROLL_SPEAKER, for the same anti-collision reason.
#
# It is INTERNAL METADATA and is never displayed: the app transcript, HTML and
# plain-text export and the EPUB all drop it, so a tagged transition renders as
# the unlabelled narration it was before tagging — identical to the untagged
# ones already sitting in every existing campaign.
TRANSITION_SPEAKER = "⁣Scene"
# Speakers that mark a message as not-model-output. Both are excluded from
# drift metrics and neither is ever consumed by reroll — a roll BLOCKS reroll
# (its transcript line must stay in lockstep with rolls.json), a trailing
# transition is stepped over and preserved.
SYNTHETIC_SPEAKERS = (ROLL_SPEAKER, TRANSITION_SPEAKER)
_MARKER = re.compile(r"^\*\*([^*\n]{1,64}?)(?: \(([^)\n]+)\))?:\*\*[ ]?", re.MULTILINE)
_SAFE_LABEL = re.compile(r"^[^*\n]{1,64}$")


def _label(role: str, speaker: str | None) -> str:
    if speaker and _SAFE_LABEL.match(speaker) and speaker not in RESERVED_LABELS:
        return speaker
    return ROLE_TO_LABEL[role]


def _markers(body: str) -> list[re.Match]:
    """Marker matches that actually start a message: at the top of the body or
    after a blank line (the serializer always writes blank lines between
    messages; this keeps bold-label lines inside a paragraph as content)."""
    return [m for m in _MARKER.finditer(body)
            if m.start() == 0 or body[max(0, m.start() - 2):m.start()] == "\n\n"]


def match_name(label: str, names) -> str | None:
    """The cast name `label` refers to, if unambiguous: exact match first
    (case-insensitive), else the single name the label is a word-boundary
    prefix of — "Winifred" names "Winifred Vance"; "Flo" names no one,
    and neither does "Winifred" with two Florences present."""
    low = label.strip().lower()
    if not low:
        return None
    exact = [n for n in names if n.lower() == low]
    if exact:
        return exact[0] if len(exact) == 1 else None
    prefixed = [n for n in names
                if n.lower().startswith(low) and not n[len(low)].isalnum()]
    return prefixed[0] if len(prefixed) == 1 else None


def _speaker_and_role(m: re.Match, players: frozenset[str]) -> tuple[str | None, str]:
    base, sub = m.group(1), m.group(2)
    if base in RESERVED_LABELS:
        return sub, RESERVED_LABELS[base]
    speaker = f"{base} ({sub})" if sub else base
    return speaker, "user" if match_name(speaker, players) else "assistant"


class SceneNotFound(Exception):
    pass


def _scenes_dir(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "scenes"


def _scene_path(cid: str, sid: str) -> Path:
    return _scenes_dir(cid) / f"{sid}.md"


def _safe_id(sid: str) -> bool:
    """Reject ids that could escape the scenes directory (defense in depth)."""
    return sid not in ("", ".", "..") and "/" not in sid and "\\" not in sid


def _require_campaign(cid: str) -> None:
    if not campaigns.campaign_meta_path(cid).exists():
        raise campaigns.CampaignNotFound(cid)


def _numbering(cid: str) -> tuple[int, int]:
    """(next number, current pad width) from the files on disk — no stored
    counter. Width starts at MIN_WIDTH and follows the widest number present;
    legacy (unmigrated) stems don't parse and are ignored."""
    top, width = 0, scene_ids.MIN_WIDTH
    d = _scenes_dir(cid)
    if d.exists():
        for p in d.glob("*.md"):
            parsed = scene_ids.parse_sid(p.stem)
            if parsed:
                top = max(top, parsed["number"])
                width = max(width, parsed["width"])
    return top + 1, width


def repad(cid: str, width: int) -> None:
    """Re-pad every scene number to `width` digits (renames files, repoints all
    referencing stores). Keeps widths uniform so lexicographic order stays exact."""
    mapping = {}
    for p in _scenes_dir(cid).glob("*.md"):
        parsed = scene_ids.parse_sid(p.stem)
        if parsed and parsed["width"] != width:
            mapping[p.stem] = scene_ids.format_sid(
                parsed["number"], width, parsed["date_slug"], parsed["title_slug"])
    for old, new in mapping.items():
        _scene_path(cid, old).rename(_scene_path(cid, new))
    scene_refs.repoint(cid, mapping)


def create_scene(cid: str, title: str, suggested_date: str | None = None,
                 pcless: bool = False) -> str:
    _require_campaign(cid)
    d = _scenes_dir(cid)
    d.mkdir(parents=True, exist_ok=True)
    number, width = _numbering(cid)
    if len(str(number)) > width:  # 999 -> 1000: widen the whole campaign first
        width = len(str(number))
        repad(cid, width)
    now = now_iso()
    base = scene_ids.format_sid(number, width, None, slugify(title))
    sid = uniquify(base, lambda c: _scene_path(cid, c).exists())
    active = _get_active_connection()
    meta = {"title": title, "model": active["model"] if active else "",
             "created": now, "updated": now}
    if pcless:
        meta["pcless"] = "true"
    if suggested_date:
        try:
            provider = calendars.get_provider(
                calendars.read_calendar(campaigns.campaign_root(cid))["primary"])
            meta["suggested_date"] = calendars.normalize(provider, suggested_date)
        except (calendars.CalendarError, KeyError):
            pass  # only a hint — a bad one is dropped, never an error
    atomic.write_text(_scene_path(cid, sid), dump_frontmatter(meta, ""))
    from . import audit  # lazy: audit imports campaigns/sheets, scenes must not cycle
    audit.capture_baseline(cid, sid)
    return sid


def list_scenes(cid: str) -> list[dict]:
    _require_campaign(cid)
    out: list[dict] = []
    d = _scenes_dir(cid)
    if d.exists():
        for p in d.glob("*.md"):
            meta = parse_frontmatter_head(p)  # never reads the transcript body
            history = [x for x in meta.get("time_history", "").split(",") if x]
            out.append({
                "id": p.stem,
                "title": meta.get("title", p.stem),
                "model": meta.get("model", ""),
                "created": meta.get("created", ""),
                "updated": meta.get("updated", ""),
                "date": history[0] if history else "",
                "pcless": meta.get("pcless") == "true",
            })
    out.sort(key=lambda m: m["updated"], reverse=True)
    return out


def is_pcless(cid: str, sid: str) -> bool:
    """A scene deliberately without a player character (director-driven)."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        return False
    return parse_frontmatter_head(p).get("pcless") == "true"


def _parse_messages(body: str, players: frozenset[str]) -> list[dict]:
    matches = _markers(body)
    messages = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        speaker, role = _speaker_and_role(m, players)
        msg = {"role": role, "content": body[start:end].strip()}
        if speaker:
            msg["speaker"] = speaker
        messages.append(msg)
    return messages


def read_scene(cid: str, sid: str) -> dict:
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    from . import appearances  # lazy: appearances lazily imports scenes too
    players = frozenset(appearances.player_names(cid, sid))
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"meta": {"id": sid, **meta}, "messages": _parse_messages(body, players)}


def read_scene_meta(cid: str, sid: str) -> dict:
    """A scene's frontmatter without parsing its transcript. For bulk scans
    (response-preset usage) where the messages are irrelevant and reading them
    for every scene in the library is pure waste."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    return {"id": sid, **parse_frontmatter_head(p)}


def rename_scene(cid: str, sid: str, title: str) -> str:
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["title"] = title
    parsed = scene_ids.parse_sid(sid)
    if parsed:  # keep number and date sections verbatim; only the title re-slugs
        base = scene_ids.format_sid(
            parsed["number"], parsed["width"], parsed["date_slug"], slugify(title))
    else:  # legacy (pre-migration) id: keep the old created-date prefix scheme
        base = f"{meta.get('created', now_iso())[:10]}-{slugify(title)}"
    new_sid = uniquify(base, lambda c: c != sid and _scene_path(cid, c).exists())
    atomic.write_text(p, dump_frontmatter(meta, body))
    if new_sid != sid:
        p.rename(_scene_path(cid, new_sid))
        # a scene's id is its filename: carry every store's references across
        scene_refs.repoint(cid, {sid: new_sid})
    return new_sid


def delete_scene(cid: str, sid: str) -> None:
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    p.unlink()


def get_dismissed(cid: str, sid: str) -> list[str]:
    """Suggestion ids the user dismissed for this scene. Missing scene ⇒ none."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        return []
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return [x for x in meta.get("dismissed", "").split(",") if x]


def add_dismissed(cid: str, sid: str, char_id: str) -> None:
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    current = [x for x in meta.get("dismissed", "").split(",") if x]
    if char_id not in current:
        current.append(char_id)
    meta["dismissed"] = ",".join(current)
    atomic.write_text(p, dump_frontmatter(meta, body))


def stamp_greeting(cid: str, sid: str, gid: str) -> None:
    """Record the greeting this scene was started from (plot-map unlock linkage)."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["greeting"] = gid
    atomic.write_text(p, dump_frontmatter(meta, body))


def set_pcless(cid: str, sid: str) -> None:
    """Flag a scene as deliberately player-less (an offscreen greeting stamps it)."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["pcless"] = "true"
    atomic.write_text(p, dump_frontmatter(meta, body))


RESPONSE_FIELDS = ("response_preset", "style_id", "length_reply_words", "length_blocks",
                   "length_paragraphs", "length_speakers", "length_blocks_per_speaker")


def set_response(cid: str, sid: str, fields: dict) -> None:
    """Write scene-scope response settings. An empty value clears the field
    (inherit); a key that is absent from `fields` is left untouched."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    for key in RESPONSE_FIELDS:
        if key in fields:
            meta[key] = str(fields[key] or "")
    atomic.write_text(p, dump_frontmatter(meta, body))


def append_message(cid: str, sid: str, role: str, content: str, speaker: str | None = None) -> None:
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    body = _append_block(body, _block(role, speaker, content))
    meta["updated"] = now_iso()
    atomic.write_text(p, dump_frontmatter(meta, body))


def _block(role: str, speaker: str | None, content: str) -> str:
    return f"**{_label(role, speaker)}:** {content.strip()}\n"


def _append_block(body: str, block: str) -> str:
    return (body.rstrip() + "\n\n" + block) if body.strip() else block


def _parse_turn_sizes(raw: str) -> list[int]:
    """Parse the boundary list STRICTLY: every token must be a positive whole
    number, or the whole field is discarded.

    Dropping only the bad tokens invents boundaries. "2,garbled,1" would parse
    to [2, 1] and a zero would pass through as a real turn — and both are then
    treated as authoritative by measurement AND by reroll, which deletes
    sizes[-1] blocks. A field that cannot be trusted end to end means "no
    tracking": no metrics, and reroll takes the untracked path.
    """
    sizes: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            n = int(token)
        except ValueError:
            return []
        if n <= 0:
            return []
        sizes.append(n)
    return sizes


def get_turn_sizes(cid: str, sid: str) -> list[int]:
    """Block counts for each recorded model generation, oldest first.

    Describes the LAST sum(turn_sizes) model blocks — a tracked suffix, not the
    whole transcript. Blocks written before turn tracking existed form an
    untracked prefix that measurement ignores, which is what lets an upgraded
    scene start being measured once new generations land. Empty on a scene with
    no tracking yet; such a scene is simply not measured.
    """
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        return []
    return _parse_turn_sizes(parse_frontmatter_head(p).get("turn_sizes", ""))


def _set_turn_sizes(meta: dict, sizes: list[int]) -> None:
    """Stage the boundary list into `meta` for the caller's single write.

    Every mutation that touches both the transcript and its boundaries writes
    them TOGETHER: a crash between two writes leaves turn_sizes describing a
    transcript that no longer exists, and the next reroll then trusts sizes[-1]
    and deletes blocks belonging to an older generation. Irreversible.

    A scene with no tracking keeps none: writing an empty key would stamp
    turn_sizes onto every legacy scene it touches for no gain.
    """
    if sizes or "turn_sizes" in meta:
        meta["turn_sizes"] = ",".join(str(n) for n in sizes)


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
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    kept = [s for s in segments if s["content"].strip()]
    if not kept:
        return
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    for seg in kept:
        body = _append_block(body, _block("assistant", seg.get("speaker"), seg["content"]))
    sizes = _parse_turn_sizes(meta.get("turn_sizes", "")) + [len(kept)]
    meta["turn_sizes"] = ",".join(str(n) for n in sizes)
    meta["updated"] = now_iso()
    atomic.write_text(p, dump_frontmatter(meta, body))


def _serialize_messages(messages: list[dict]) -> str:
    body = ""
    for m in messages:
        body = _append_block(body, _block(m["role"], m.get("speaker"), m["content"]))
    return body


def stamp_user_speaker(cid: str, sid: str, name: str) -> None:
    """Backfill: give every speakerless user message the (sole) player's name."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    messages = read_scene(cid, sid)["messages"]
    stamped = False
    for m in messages:
        if m["role"] == "user" and not m.get("speaker"):
            m["speaker"] = name
            stamped = True
    if not stamped:
        return
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    atomic.write_text(p, dump_frontmatter(meta, _serialize_messages(messages)))


def split_reply(text: str, players: frozenset[str]) -> list[dict]:
    """Split one model reply into per-speaker segments on the marker grammar.
    Unlabeled leading text, reserved labels, and player-named blocks (never
    store a forged player line) all go to the narrator (speaker None)."""
    text = text.strip()
    matches = _markers(text)
    segments: list[dict] = []

    def add(speaker: str | None, content: str) -> None:
        if content.strip():
            segments.append({"speaker": speaker, "content": content.strip()})

    add(None, text[:matches[0].start()] if matches else text)
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        speaker, role = _speaker_and_role(m, players)
        add(None if role == "user" else speaker, text[m.end():end])
    return segments


def _model_blocks(messages: list[dict]) -> list[int]:
    """Indices of the messages that count as MODEL blocks — assistant-role and
    not synthetically authored (dice rolls, scene transitions). turn_sizes is
    expressed in these, so everything that reads or repairs it counts the same
    way."""
    return [i for i, m in enumerate(messages)
            if m["role"] == "assistant" and m.get("speaker") not in SYNTHETIC_SPEAKERS]


def trailing_transitions(messages: list[dict]) -> int:
    """How many messages at the tail are scene-transition lines.

    Reroll steps OVER these: a join/leave/location/time line is a record of
    something the player did, not model output, so it survives a reroll of the
    reply beneath it (whereas a manual dice roll blocks reroll outright — its
    entry lives on in rolls.json and the transcript line must stay in lockstep).
    """
    n = 0
    while n < len(messages) and messages[-1 - n].get("speaker") == TRANSITION_SPEAKER:
        n += 1
    return n


class TurnSizesDesynced(Exception):
    """The recorded boundaries can no longer describe the stored transcript.

    Raised INSTEAD of deleting anything. turn_sizes is what tells reroll how
    many blocks the last generation had; a list that doesn't fit the transcript
    would have it count back through blocks belonging to earlier generations,
    destroying transcript nobody asked to reroll. There is no safe guess to
    fall back on — the untracked path eats every consecutive generation — so
    the operation refuses and the scene is left exactly as it was.
    """


def _trailing_model_run(messages: list[dict]) -> int:
    """How many messages at the tail are contiguous model blocks."""
    n = 0
    while n < len(messages) and messages[-1 - n]["role"] == "assistant" \
            and messages[-1 - n].get("speaker") not in SYNTHETIC_SPEAKERS:
        n += 1
    return n


def _tracked_suffix_fits(messages: list[dict], sizes: list[int]) -> bool:
    """Whether `sizes` can still describe this transcript's trailing blocks.

    Two ways it can't: the list claims more model blocks than exist at all, or
    the last recorded generation isn't sitting contiguously at the tail (a user
    line or a dice roll has been spliced into the middle of what it claims).
    Either way the list is describing some other transcript.
    """
    return (sum(sizes) <= len(_model_blocks(messages))
            and sizes[-1] <= _trailing_model_run(messages))


def remove_trailing_assistant_run(cid: str, sid: str) -> None:
    """Drop the trailing run of assistant-side messages (one turn's output).

    Trailing scene-transition lines are stepped over and PRESERVED in order —
    the generation removed is the last one beneath them. Stops at (and refuses
    to touch) a manual dice-roll line: rerolling must never delete a roll's
    transcript entry while it still lives in rolls.json.

    Transcript and boundaries land in ONE write; see _set_turn_sizes.
    """
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    messages = read_scene(cid, sid)["messages"]
    keep = len(messages) - trailing_transitions(messages)
    tail = messages[keep:]          # transitions, preserved verbatim and in order
    messages = messages[:keep]
    if (not messages or messages[-1]["role"] != "assistant"
            or messages[-1].get("speaker") in SYNTHETIC_SPEAKERS):
        raise IndexError("no trailing assistant reply")
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    sizes = _parse_turn_sizes(meta.get("turn_sizes", ""))
    if sizes:
        # Validate BEFORE deleting. Hand-edited or externally-corrupted
        # boundaries must not authorize a deletion this large.
        if not _tracked_suffix_fits(messages, sizes):
            raise TurnSizesDesynced(sid)
        # Remove EXACTLY the last recorded generation. A role-based trailing-run
        # removal would eat every consecutive generation, because director turns
        # persist no user message to stop at — deleting more than the caller
        # asked to reroll, and desyncing turn_sizes permanently.
        del messages[len(messages) - sizes[-1]:]
        sizes = sizes[:-1]
    else:
        # Untracked (or unparseable, which means the same thing): the whole
        # trailing model run comes off, as it did before boundaries existed.
        del messages[len(messages) - _trailing_model_run(messages):]
    meta["updated"] = now_iso()
    _set_turn_sizes(meta, sizes)
    atomic.write_text(p, dump_frontmatter(meta, _serialize_messages(messages + tail)))


def trim_continuation(cid: str, sid: str, from_index: int) -> None:
    """Roll a scene back to `from_index`, discarding a crashed or
    superseded continuation attempt (proposals.commit_narration's crash
    recovery) — except `ROLL_SPEAKER` messages at or past that index,
    which are preserved in order. The trim-safety rule: manual dice-roll
    lines are the only non-superseding writer active during the crash
    window; our own continuation segments never carry ROLL_SPEAKER."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    messages = read_scene(cid, sid)["messages"]
    kept = messages[:from_index] + [
        m for m in messages[from_index:] if m.get("speaker") == ROLL_SPEAKER]
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
    sizes = _parse_turn_sizes(meta.get("turn_sizes", ""))
    prefix = max(len(_model_blocks(messages)) - sum(sizes), 0)  # untracked legacy blocks
    tracked_after = max(len(_model_blocks(kept)) - prefix, 0)
    while sizes and sum(sizes) > tracked_after:
        sizes.pop()
    meta["updated"] = now_iso()
    _set_turn_sizes(meta, sizes)
    atomic.write_text(p, dump_frontmatter(meta, _serialize_messages(kept)))


class RollMessageImmutable(Exception):
    """Raised when editing a manual dice-roll transcript line is attempted —
    its content must stay in lockstep with the immutable rolls.json entry."""


def edit_message(cid: str, sid: str, index: int, content: str) -> None:
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    from . import appearances  # lazy: appearances lazily imports scenes too
    players = frozenset(appearances.player_names(cid, sid))
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    messages = _parse_messages(body, players)
    if index < 0 or index >= len(messages):
        raise IndexError(index)
    if messages[index].get("speaker") == ROLL_SPEAKER:
        raise RollMessageImmutable(index)
    before = _model_blocks(messages)
    messages[index]["content"] = content.strip()
    # Re-parse the body we are about to store rather than reading it back after
    # writing: the edited text is re-split at read time, so the new block count
    # is only knowable from the serialized form — and body and boundaries have
    # to go out together (see _set_turn_sizes).
    new_body = _serialize_messages(messages)
    after = _model_blocks(_parse_messages(new_body, players))
    meta["updated"] = now_iso()
    _set_turn_sizes(meta, _reconciled_turn_sizes(
        _parse_turn_sizes(meta.get("turn_sizes", "")), before, after, index))
    atomic.write_text(p, dump_frontmatter(meta, new_body))


def _reconciled_turn_sizes(sizes: list[int], before: list[int], after: list[int],
                           index: int) -> list[int]:
    """turn_sizes describing the transcript's real trailing model blocks after
    an edit changed how the body PARSES.

    Stored content is re-split at read time on blank-line-preceded
    `**Speaker:**` markers, so editing a message can add model blocks (a marker
    typed into the text) or remove one (a legacy `**Grimoire (Name):**` label
    is re-saved as plain `**Name:**`, which parses as a user line when Name is
    a seated player). turn_sizes knows nothing of either: left alone, reroll
    consumes sizes[-1] blocks of a reply that is no longer that long, and drift
    segmentation takes the last sum(sizes) blocks from the wrong place.

    The invariant restored: sum(turn_sizes) is the number of model blocks the
    tracked turns actually cover, and those turns are the transcript's true
    trailing blocks. Dropping older entries is acceptable — they merely stop
    being measured. Keeping an entry that describes the wrong blocks is not.
    """
    if not sizes:
        return sizes                             # untracked scene: nothing to keep in step
    delta = len(after) - len(before)
    if delta == 0:
        return sizes                             # the ordinary edit: content only
    sizes = list(sizes)
    while sizes and sum(sizes) > len(before):
        sizes.pop(0)                             # hand-edited: claims blocks that never existed
    prefix = len(before) - sum(sizes)             # untracked pre-tracking blocks
    starts, at = [], prefix                       # each tracked turn's first block position
    for n in sizes:
        starts.append(at)
        at += n

    pos = before.index(index) if index in before else None
    if pos is None:
        # Not a model block itself, but its text can still splice one in or out.
        # Before the tracked suffix that changes nothing (the suffix is anchored
        # at the end); inside it, keep only the turns lying wholly after the edit.
        blocks_before = sum(1 for i in before if i < index)
        if blocks_before > prefix:
            sizes = [n for n, s in zip(sizes, starts) if s >= blocks_before]
    elif pos >= prefix:
        turn = max(i for i, s in enumerate(starts) if s <= pos)
        sizes[turn] += delta
        if sizes[turn] <= 0:
            sizes = sizes[turn + 1:]              # conservative: never misattribute
    # pos < prefix: the edit landed in the untracked legacy prefix and the
    # tracked suffix still covers exactly the blocks it always did.
    while sizes and sum(sizes) > len(after):
        sizes.pop(0)
    return sizes


def mark_absorbed(cid: str, sid: str, one_line: str, summary: str) -> None:
    """Record a scene's absorbed summary into its frontmatter and flag it done."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["one_line"] = one_line
    meta["summary"] = summary
    meta["done"] = "true"
    meta["updated"] = now_iso()
    atomic.write_text(p, dump_frontmatter(meta, body))


def get_location_history(cid: str, sid: str) -> list[str]:
    """Ordered campaign-location ids this scene has been at; last is current. Missing ⇒ []."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        return []
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return [x for x in meta.get("location_history", "").split(",") if x]


def set_location(cid: str, sid: str, eid: str) -> dict:
    """Make campaign location `eid` the scene's current setting.

    First setting on a location-less scene is silent; a real change appends an
    assistant transition line. Re-selecting the current location is a no-op.
    Returns {"moved": bool, "name": str}.
    """
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    name = overlay.read_entity(cid, "locations", eid)["meta"].get("name", eid)  # raises EntityNotFound
    history = get_location_history(cid, sid)
    if history and history[-1] == eid:
        return {"moved": False, "name": name}
    moved = bool(history)
    if moved:
        append_message(cid, sid, "assistant", f"*The scene moves to {name}.*",
                       speaker=TRANSITION_SPEAKER)
    # re-read after the possible append_message rewrite, then record the new current
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    history.append(eid)
    meta["location_history"] = ",".join(history)
    atomic.write_text(p, dump_frontmatter(meta, body))
    return {"moved": moved, "name": name}


def get_time_history(cid: str, sid: str) -> list[str]:
    """Ordered scene moments (native datetime strings); last is current. Missing ⇒ []."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        return []
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return [x for x in meta.get("time_history", "").split(",") if x]


def get_suggested_date(cid: str, sid: str) -> str:
    """The creation-time date hint, if the scene still carries one. Missing ⇒ ""."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        return ""
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return meta.get("suggested_date", "")


def set_datetime(cid: str, sid: str, native: str) -> dict:
    """Set the scene's current moment (in the primary calendar). The first set is
    silent and stamps the start date into the filename (the id changes); later
    changes append an assistant transition line. Returns {"advanced", "friendly",
    "id"} where id is the possibly-renamed scene id."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    cfg = calendars.read_calendar(campaigns.campaign_root(cid))
    provider = calendars.get_provider(cfg["primary"])
    canonical = calendars.normalize(provider, native)  # raises calendars.CalendarError
    friendly = calendars.friendly(provider, canonical)
    history = get_time_history(cid, sid)
    if history and history[-1] == canonical:
        return {"advanced": False, "friendly": friendly, "id": sid}
    advanced = bool(history)
    if advanced:
        append_message(cid, sid, "assistant", f"*Time passes. It is now {friendly}.*",
                       speaker=TRANSITION_SPEAKER)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta.pop("suggested_date", None)  # the hint is stale once a real date exists
    history.append(canonical)
    meta["time_history"] = ",".join(history)
    atomic.write_text(p, dump_frontmatter(meta, body))
    if not advanced:
        sid = _stamp_start_date(cid, sid, canonical)
    return {"advanced": advanced, "friendly": friendly, "id": sid}


def _stamp_start_date(cid: str, sid: str, canonical: str) -> str:
    """First date set: insert the date section into the filename. The start date
    is fixed — later advances never touch the name. Legacy ids are left alone."""
    parsed = scene_ids.parse_sid(sid)
    if parsed is None or parsed["date_slug"] is not None:
        return sid
    base = scene_ids.format_sid(parsed["number"], parsed["width"],
                                scene_ids.date_slug_of(canonical), parsed["title_slug"])
    new_sid = uniquify(base, lambda c: c != sid and _scene_path(cid, c).exists())
    _scene_path(cid, sid).rename(_scene_path(cid, new_sid))
    scene_refs.repoint(cid, {sid: new_sid})
    return new_sid
