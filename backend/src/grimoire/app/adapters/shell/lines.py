"""Search over the lines that were said (``shell.search_lines``): plain text matching, no model, no embeddings.

What can be found is exactly what opening the chat shows of its played turns: each turn is read by 035's
``SceneLibrary`` and turned into lines by the text API's own ``turn_view``, so a post, and the shown lines of the
take that was kept, and nothing else. A take that was not kept, a withdrawn or struck line, a turn a fork choice set
aside and a line the table did not hear are in no view, so in no result. A chat's imported history (its pages from
before turn 1) is not searched.

Reading every turn of every chat costs seconds on a large library, so the lines are kept **in memory**, per chat,
with a stamp of the chat's turn files (each file's name, size, modification time and inode). The index is a
derivative: never written to disk, never synced, gone when the app stops and rebuilt as searches ask for it. A chat
whose stamp differs is read again before it is searched, and every result is checked against its turn file's bytes
(the content etag the line was read at) before it is answered, so a stamp that missed a change cannot put a stale
line in an answer. A search stops building after ``BUDGET_SECONDS`` and after ``MAX_CHATS`` chats and then says so
(``complete: false``): the chats it did not reach were not searched. See docs/APP_SHELL.md.
"""

from __future__ import annotations

import os
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from grimoire.chats import ChatCodecError, NavigationIndex, chat_from_payload, nav_entry
from grimoire.core.identity import ChatRef, InstanceRef, TurnRef
from grimoire.effective import chat_snapshot
from grimoire.scene import parse_turn_id
from grimoire.scenestore import SceneLibrary
from grimoire.store import LibraryStore, StoreError, etag
from grimoire.store.layout import CAMPAIGNS, TURNS, address_parts
from grimoire.textapi.views import turn_view

#: A query is 2 to 200 characters in at most 8 words; every word must be in the line.
MIN_QUERY = 2
MAX_QUERY = 200
MAX_WORDS = 8
#: How many lines one search answers, and how many of them may come from one chat.
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
PER_CHAT = 3
#: The most chats one search looks at, in the chat list's order (the most recent first).
MAX_CHATS = 2000
#: How long one search may go on reading chats it has not read yet (it searches what it holds either way), and how
#: many it reads whatever the time, so that asking again always gets further.
BUDGET_SECONDS = 1.5
MIN_BUILT = 8
#: The most text the index holds, in characters of lines. Past it a chat is read, searched and not kept.
MAX_INDEX_CHARS = 32_000_000
#: The longest text a result carries: the words around the first match.
EXCERPT = 160
_LEAD = 48

_MARKS = {code: None for code in range(0x0300, 0x0370)}


def fold(text: str) -> str:
    """``text`` with case and accents taken out, exactly as the palette folds a name (``palette.js``): NFKD, the
    combining marks U+0300 to U+036F dropped, lower case."""
    return unicodedata.normalize("NFKD", text).translate(_MARKS).lower()


def words_of(query: str) -> tuple[str, ...]:
    """The folded words of a query, each once, in the order typed."""
    return tuple(dict.fromkeys(fold(query).split()))


def excerpt(text: str, words: Sequence[str], limit: int = EXCERPT) -> str:
    """At most ``limit`` characters of ``text`` around the first place one of ``words`` matches, cut at spaces
    where it can be, with ``…`` at a cut end. The whole text when it is short enough."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    folded, origin = [], []
    for at, char in enumerate(flat):  # folded one character at a time, to know where each folded character came from
        piece = fold(char)
        folded.append(piece)
        origin.extend([at] * len(piece))
    joined = "".join(folded)
    found = [joined.find(word) for word in words]
    first = min((at for at in found if at >= 0), default=0)
    start = max(0, (origin[first] if first < len(origin) else 0) - _LEAD)
    start = min(start, len(flat) - limit)
    if start > 0:
        space = flat.find(" ", start, start + 16)
        start = start if space == -1 else space + 1
    end = min(len(flat), start + limit)
    if end < len(flat):
        space = flat.rfind(" ", end - 16, end)
        end = end if space == -1 else space
    return ("…" if start > 0 else "") + flat[start:end].strip() + ("…" if end < len(flat) else "")


@dataclass(frozen=True, slots=True)
class Line:
    """One thing said in a played turn, as the chat shows it. ``etag`` is the content etag of the turn file it was
    read from; ``actor`` who posted it, for a post (its speaker is named when it is answered)."""

    turn: str
    number: int
    etag: str
    kind: str  # "post" or "line"
    speaker: str | None
    actor: InstanceRef | None
    text: str
    folded: str


@dataclass(frozen=True, slots=True)
class _Entry:
    stamp: tuple
    lines: tuple[Line, ...]
    blob: str  # every line's folded text, one per row: a chat no word of the query is in is skipped unread
    chars: int


def chat_lines(scenes: SceneLibrary, chat: ChatRef) -> tuple[Line, ...]:
    """Every line the chat's played turns show, oldest first, through the text API's own turn view."""
    lines: list[Line] = []
    for stored in scenes.turns(chat).turns:
        view = turn_view(stored, {})
        record = stored.record
        if view["post"] is not None:
            text = view["post"]["text"]
            lines.append(Line(record.turn_id, record.number, stored.etag, "post", None, record.post.actor, text, fold(text)))
        if view["kept"] is not None:
            for said in view["kept"]["lines"]:
                lines.append(Line(record.turn_id, record.number, stored.etag, "line", said["speaker"], None,
                                  said["text"], fold(said["text"])))
    return tuple(lines)


def _stamps(root, campaign_id: str) -> Callable[[ChatRef], tuple]:
    """A function giving each chat of one campaign its stamp: the name, size, modification time and inode of every
    file in the campaign's turns folder that is not plainly another chat's turn (the files 035 reads for it)."""
    by_chat: dict[str, list[tuple]] = {}
    shared: list[tuple] = []
    state: tuple = ("listed",)
    try:
        with os.scandir(os.path.join(root, CAMPAIGNS, campaign_id, TURNS)) as found:
            for item in found:
                try:
                    stat = item.stat(follow_symlinks=False)
                    fact = (item.name, stat.st_size, stat.st_mtime_ns, stat.st_ino)
                except OSError as error:
                    fact = (item.name, -1, error.errno or 0, 0)
                parsed = parse_turn_id(item.name[:-len(".json")]) if item.name.endswith(".json") else None
                if parsed is None:
                    shared.append(fact)  # 035 counts such a file against every chat of the campaign
                else:
                    by_chat.setdefault(parsed[0], []).append(fact)
    except FileNotFoundError:
        state = ("none",)
    except OSError as error:
        state = ("unlisted", error.errno or 0)
    shared.sort()
    return lambda chat: (state, tuple(shared), tuple(sorted(by_chat.get(chat.chat_id, ()))))


class LineIndex:
    """The lines of one open library's chats, kept between searches. Not thread-safe by itself: an adapter op runs
    under the library's one lock."""

    def __init__(self, store: LibraryStore, handle: object, *, owner: str | None = None,
                 clock: Callable[[], float] = time.monotonic, budget: float = BUDGET_SECONDS,
                 max_chats: int = MAX_CHATS, max_chars: int = MAX_INDEX_CHARS) -> None:
        self.store = store
        # A reader of its own, so reading every chat does not push the chat being played out of the runner's.
        self.scenes = SceneLibrary(store, handle, owner=owner)
        self.clock = clock
        self.budget = budget
        self.max_chats = max_chats
        self.max_chars = max_chars
        self._entries: dict[ChatRef, _Entry] = {}
        self._chars = 0
        #: How many chats the last search read from their files (the rest it held already).
        self.built = 0

    def forget(self) -> None:
        """Drop everything held. Nothing is lost: the next search reads the chats again."""
        self._entries.clear()
        self._chars = 0

    # -- the index ----------------------------------------------------------------------------------------
    def _build(self, chat: ChatRef, stamp: tuple) -> _Entry:
        lines = chat_lines(self.scenes, chat)
        entry = _Entry(stamp, lines, "\n".join(line.folded for line in lines), sum(len(line.text) for line in lines))
        self._drop(chat)
        if self._chars + entry.chars <= self.max_chars:
            self._entries[chat] = entry
            self._chars += entry.chars
        self.built += 1
        return entry

    def _drop(self, chat: ChatRef) -> None:
        old = self._entries.pop(chat, None)
        if old is not None:
            self._chars -= old.chars

    def _current(self, line: Line, chat: ChatRef) -> bool:
        """Whether the turn file a line was read from still holds the bytes it was read at."""
        try:
            path = self.store.root.joinpath(*address_parts(TurnRef(chat.campaign_id, line.turn)))
            return etag(path.read_bytes()) == line.etag
        except (OSError, ValueError):
            return False

    # -- searching ----------------------------------------------------------------------------------------
    def search(self, query: str, limit: int = DEFAULT_LIMIT) -> dict:
        """The lines every word of ``query`` is in, chats in the chat list's order (the most recent first) and, in a chat, the
        latest turn first: at most ``limit``, and ``PER_CHAT`` from one chat."""
        started = self.clock()
        words = words_of(query)
        view = self.store.load()
        records = {}
        for ref, stored in view.chats.items():
            try:
                records[ref] = chat_from_payload(ref, stored.record.schema, stored.record.payload)
            except ChatCodecError:
                continue  # a chat this version cannot read cannot be opened either
        order = NavigationIndex.build(nav_entry(ref, record) for ref, record in records.items()).all_chats()
        for gone in [chat for chat in self._entries if chat not in records]:
            self._drop(gone)
        stamps: dict[str, Callable[[ChatRef], tuple]] = {}
        self.built = 0
        found: list[dict] = []
        searched, unread, more = 0, 0, False
        for chat in order[:self.max_chats]:
            if len(found) >= limit:
                more = True  # the limit ended the search: older chats were not looked at
                break
            if chat.campaign_id not in stamps:
                stamps[chat.campaign_id] = _stamps(self.store.root, chat.campaign_id)
            stamp = stamps[chat.campaign_id](chat)
            entry = self._entries.get(chat)
            if entry is None or entry.stamp != stamp:
                self._drop(chat)  # what was held is of other bytes: never searched
                if self.built >= MIN_BUILT and self.clock() - started > self.budget:
                    unread += 1
                    continue
                entry = self._read(chat, stamp)
                if entry is None:
                    unread += 1
                    continue
            searched += 1
            hits = _matching(entry, words)
            if hits and not all(self._current(line, chat) for line in hits[:PER_CHAT + 1]):
                entry = self._read(chat, stamps[chat.campaign_id](chat))  # the stamp missed a change: read it again
                hits = [] if entry is None else _matching(entry, words)
            if len(hits) > PER_CHAT or len(found) + len(hits) > limit:
                more = True
            if hits:
                found += self._answers(view, chat, records[chat], hits[:min(PER_CHAT, limit - len(found))], words)
        stopped = "time" if unread else "chats" if len(order) > self.max_chats else None
        return {"query": query, "lines": found, "complete": stopped is None, "stopped": stopped, "more": more,
                "chats": len(order), "searched": searched, "unread": unread}

    def _read(self, chat: ChatRef, stamp: tuple) -> _Entry | None:
        try:
            return self._build(chat, stamp)
        except (StoreError, OSError, ValueError):
            return None  # its turns cannot be read now: not searched, and said so

    def _answers(self, view, chat: ChatRef, record, hits: Sequence[Line], words: Sequence[str]) -> list[dict]:  # type: ignore[no-untyped-def]
        title = _title(view, chat, record)
        names: Mapping[InstanceRef, str] | None = None
        answers = []
        for line in hits:
            speaker = line.speaker
            if line.kind == "post":
                if names is None:
                    names = _post_names(view, chat, [hit.actor for hit in hits if hit.actor is not None])
                speaker = names.get(line.actor, line.actor.instance_id)  # type: ignore[union-attr]
            answers.append({"chat": str(chat), "title": title, "turn": line.number, "turn_id": line.turn,
                            "kind": line.kind, "speaker": speaker, "text": excerpt(line.text, words)})
        return answers


def _matching(entry: _Entry, words: Sequence[str]) -> list[Line]:
    """The entry's lines every word is in, the latest first."""
    if not all(word in entry.blob for word in words):
        return []
    return [line for line in reversed(entry.lines) if all(word in line.folded for word in words)]


def _title(view, chat: ChatRef, record) -> str:  # type: ignore[no-untyped-def]
    """The chat's name as the shell titles it: its label, else the name of what it was started from, else its ref."""
    if record.label:
        return record.label
    ref = record.launch.ref
    name = None
    if ref is not None:
        try:
            name = view.index.resolve(ref.unpinned()).name
        except Exception:  # noqa: BLE001 -- a name is presentation only; a missing source shows the chat's ref
            name = None
    return str(name) if name else str(chat)


def _post_names(view, chat: ChatRef, actors: Sequence[InstanceRef]) -> dict[InstanceRef, str]:  # type: ignore[no-untyped-def]
    """The names of the characters that posted, from the chat's own snapshot; one that cannot be named is left out
    and shows its instance id, as in the chat."""
    names: dict[InstanceRef, str] = {}
    try:
        snapshot = chat_snapshot(view, chat).snapshot
    except Exception:  # noqa: BLE001 -- 022's and 107's refusals: the posts keep their ids
        return names
    for actor in dict.fromkeys(actors):
        try:
            name = snapshot.entity(actor).name
        except Exception:  # noqa: BLE001 -- 022's refusals
            name = None
        if name:
            names[actor] = str(name)
    return names


__all__ = ["BUDGET_SECONDS", "DEFAULT_LIMIT", "EXCERPT", "MAX_CHATS", "MAX_INDEX_CHARS", "MAX_LIMIT", "MAX_QUERY",
           "MAX_WORDS", "MIN_BUILT", "MIN_QUERY", "PER_CHAT", "Line", "LineIndex", "chat_lines", "excerpt", "fold",
           "words_of"]
