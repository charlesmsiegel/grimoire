"""Read a SillyTavern chat export (JSONL) into transcript posts (Sprint 036). Pure.

The bytes are decoded elsewhere (``grimoire.archaeologystore``): this module receives each non-blank
line as ``(line number, decoded JSON object)``. It preserves, it does not reinterpret:

- every message becomes one ``Post`` in file order, its exact text and every swipe kept as variants,
  with the swipe SillyTavern showed as ``active``. When the shown text (``mes``) differs from the
  active swipe (an edit), the shown text is added as one more variant and made active, and the
  difference is reported (``edited_text_differs``): neither text is lost and neither is guessed;
- who wrote a post is a **speaker**: role plus the source's own avatar file and display name, verbatim.
  Two speakers with one display name but different avatars stay two speakers and are reported
  (``same_label_speakers``); nothing here maps a speaker to an actor, least of all by name;
- the header's ``chat_metadata`` (author's note, chat lorebook, integrity id ...) is kept verbatim and
  reported as kept-not-interpreted, as is every message field this slice does not read;
- ``audience`` is ``unknown`` on every post: a line in a transcript is evidence someone wrote it, not
  that anyone observed it. A hidden message (SillyTavern's ``is_system`` on a user or character
  message) keeps its ``hidden`` flag and stays in order.

A malformed message (no ``mes`` text, a non-boolean ``is_user``) refuses the whole file with its line
number (``StError``): an import that silently dropped a line could not claim every message is
recoverable. Errors and losses name lines and fields, never message text.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from grimoire.transcripts import MAX_VARIANTS, Post, Speaker, TranscriptError, Variant, opaque

FORMAT = "sillytavern-jsonl"
#: header keys that mark the first line as SillyTavern's chat header rather than a message
HEADER_KEYS = frozenset({"user_name", "character_name", "create_date", "chat_metadata"})
#: fields only a message holds: a first line with any of them is read as a message, never the header
MESSAGE_KEYS = frozenset({"mes", "name", "is_user", "is_system", "swipes", "swipe_id", "send_date"})
#: message fields this reader interprets; every other field is kept verbatim in the post's ``extras``
READ_FIELDS = frozenset({"mes", "swipes", "swipe_id", "swipe_info"})
MAX_MESSAGES = 200_000
MAX_SPEAKERS = 1000


class StError(ValueError):
    """A SillyTavern export this reader refuses; ``reason`` is machine-readable, ``line`` 1-based or 0."""

    def __init__(self, message: str, reason: str, line: int = 0) -> None:
        super().__init__(message)
        self.reason = reason
        self.line = line

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason, self.line))


@dataclass(frozen=True, slots=True)
class Loss:
    """Something kept but not interpreted, or a choice the reader made, for a reviewer: ``line`` (0 for
    the whole file), the field path and a machine-readable reason. Never message text."""

    line: int
    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class StSpeaker:
    """One distinct writer in the export: ``id`` is derived from role, avatar and name, so the same
    export always yields the same ids. ``label`` and ``avatar`` are the source's values verbatim."""

    id: str
    role: str
    label: str | None
    avatar: str | None
    posts: int
    first_line: int


@dataclass(frozen=True, slots=True)
class StChat:
    """A parsed export: the header verbatim (``None`` when the file has none), posts in order with each
    post's speaker id, the speakers, and the reader's losses."""

    header: Mapping | None
    posts: tuple[Post, ...]
    speaker_ids: tuple[str, ...]
    speakers: tuple[StSpeaker, ...]
    losses: tuple[Loss, ...]

    def speaker(self, speaker_id: str) -> StSpeaker:
        for speaker in self.speakers:
            if speaker.id == speaker_id:
                return speaker
        raise KeyError(speaker_id)


def speaker_id(role: str, avatar: str | None, label: object) -> str:
    """``s-`` and 12 hex digits of a digest of the speaker's role, avatar and name (``None`` distinct
    from empty text; a name the label cannot hold, too long or not text, by the source's own value)."""
    key = repr((role, avatar, label)).encode("utf-8", "surrogatepass")
    return "s-" + hashlib.sha256(key).hexdigest()[:12]


def _role(message: Mapping, line: int) -> str:
    is_user = message.get("is_user", False)
    is_system = message.get("is_system", False)
    if type(is_user) is not bool or type(is_system) is not bool:
        raise StError(f"line {line}: is_user and is_system must be true or false", "invalid_message", line)
    extra = message.get("extra")
    kind = extra.get("type") if isinstance(extra, Mapping) else None
    if kind == "narrator":
        return "narrator"
    if is_user:
        return "user"
    if is_system and type(kind) is str:
        return "system"
    return "character"


def _avatar(message: Mapping, role: str) -> str | None:
    """The source's own avatar file, verbatim: a group member's ``original_avatar``, else ``force_avatar``."""
    for key in (("original_avatar", "force_avatar") if role == "character" else ("force_avatar",)):
        value = message.get(key)
        if type(value) is str and value:
            return value
    return None


class _Reader:
    def __init__(self) -> None:
        self.losses: list[Loss] = []

    def lose(self, line: int, path: str, reason: str) -> None:
        self.losses.append(Loss(line, path, reason))

    def kept(self, value: object, label: str, line: int) -> object:
        try:
            return opaque(value, label)
        except TranscriptError as error:
            raise StError(f"line {line}: {error}", "invalid_message", line) from None

    def variants(self, message: Mapping, line: int, extras: dict) -> tuple[tuple[Variant, ...], int]:
        mes = message["mes"]
        send_date = message.get("send_date")
        at = send_date if type(send_date) is str else None
        swipes = message.get("swipes")
        if swipes is None:
            for key in ("swipes", "swipe_id", "swipe_info"):
                if key in message:
                    extras[key] = message[key]
                    self.lose(line, key, "kept_without_swipes")
            return (Variant(mes, at, {"field": "mes"}),), 0
        if (not isinstance(swipes, (list, tuple)) or not swipes or not all(type(s) is str for s in swipes)
                or len(swipes) >= MAX_VARIANTS):
            for key in ("swipes", "swipe_id", "swipe_info"):
                if key in message:
                    extras[key] = message[key]
            self.lose(line, "swipes", "invalid_swipes")
            return (Variant(mes, at, {"field": "mes"}),), 0
        info = message.get("swipe_info")
        if "swipe_info" in message and not (isinstance(info, (list, tuple)) and len(info) == len(swipes)
                                            and all(isinstance(entry, Mapping) for entry in info)):
            extras["swipe_info"] = info  # the variants cannot reproduce it exactly: keep it verbatim
            self.lose(line, "swipe_info", "kept_not_interpreted")
        if info is not None and not (isinstance(info, (list, tuple)) and len(info) <= len(swipes)):
            extras["swipe_info"] = info
            self.lose(line, "swipe_info", "invalid_swipe_info")
            info = None
        variants = []
        for index, text in enumerate(swipes):
            entry = info[index] if info is not None and index < len(info) else None
            if entry is not None and not isinstance(entry, Mapping):
                self.lose(line, f"swipe_info[{index}]", "invalid_swipe_info")
                entry = None
            stamp = entry.get("send_date") if entry is not None else None
            variants.append(Variant(text, stamp if type(stamp) is str else None, {"swipe": index},
                                    self.kept(entry, f"swipe_info[{index}]", line) if entry is not None else {}))
        chosen = message.get("swipe_id")
        if type(chosen) is not int or not 0 <= chosen < len(swipes):
            if "swipe_id" in message:
                extras["swipe_id"] = chosen
            self.lose(line, "swipe_id", "invalid_swipe_id")
            chosen = swipes.index(mes) if mes in swipes else None
        if chosen is None or swipes[chosen] != mes:
            variants.append(Variant(mes, at, {"field": "mes"}))
            chosen = len(variants) - 1
            self.lose(line, "mes", "edited_text_differs")
        return tuple(variants), chosen


def read_st_chat(lines: Sequence[tuple[int, Mapping]]) -> StChat:
    """Read decoded export lines (``(line number, object)``, blank lines already skipped)."""
    reader = _Reader()
    items = list(lines)
    header: Mapping | None = None
    if items and not MESSAGE_KEYS & set(items[0][1]) and HEADER_KEYS & set(items[0][1]):
        line, raw = items.pop(0)
        header = reader.kept(raw, "header", line)  # type: ignore[assignment]
        metadata = raw.get("chat_metadata")
        if isinstance(metadata, Mapping):
            for key in sorted(str(k) for k in metadata):
                reader.lose(line, f"chat_metadata.{key}", "kept_not_interpreted")
    if not items:
        raise StError("the export holds no messages", "no_messages")
    if len(items) > MAX_MESSAGES:
        raise StError(f"the export holds more than {MAX_MESSAGES} messages", "too_large")
    posts: list[Post] = []
    ids: list[str] = []
    found: dict[str, list] = {}
    for number, (line, message) in enumerate(items, start=1):
        if not isinstance(message, Mapping) or type(message.get("mes")) is not str:
            raise StError(f"line {line}: a message needs its text (mes)", "invalid_message", line)
        role = _role(message, line)
        name = message.get("name")
        # the speaker is told apart by the source's own value, even one the label cannot show
        named = name if name is None or type(name) is str else ("name", repr(name))
        if name is not None and type(name) is not str:
            reader.lose(line, "name", "invalid_name")
            name = None
        elif name is None:
            reader.lose(line, "name", "missing_name")
        if name is not None and len(name) > 1000:
            reader.lose(line, "name", "name_too_long")
            name = None
        avatar = _avatar(message, role)
        sid = speaker_id(role, avatar, named)
        if sid not in found:
            if len(found) >= MAX_SPEAKERS:
                raise StError(f"the export has more than {MAX_SPEAKERS} speakers", "too_many_speakers", line)
            found[sid] = [role, name, avatar, 0, line]
        found[sid][3] += 1
        extras = {key: value for key, value in message.items() if key not in READ_FIELDS}
        variants, active = reader.variants(message, line, extras)
        try:
            posts.append(Post(f"p{number:06d}", Speaker(role, None, name), variants, active,
                              hidden=message.get("is_system", False) is True, source={"line": line},
                              extras=extras))
        except TranscriptError as error:
            raise StError(f"line {line}: {error}", "invalid_message", line) from None
        ids.append(sid)
    speakers = tuple(StSpeaker(sid, role, name, avatar, count, first)
                     for sid, (role, name, avatar, count, first) in found.items())
    by_label: dict[str | None, list[StSpeaker]] = {}
    for speaker in speakers:
        by_label.setdefault(speaker.label, []).append(speaker)
    for label, same in by_label.items():
        if len(same) > 1 and label is not None:
            for speaker in same:
                reader.lose(speaker.first_line, "name", "same_label_speakers")
    return StChat(header, tuple(posts), tuple(ids), speakers, tuple(reader.losses))
