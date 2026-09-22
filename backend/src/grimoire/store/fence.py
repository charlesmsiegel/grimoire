"""Incremental ```roll fence detection over a streamed reply (#162).

Pure: no I/O. The watcher withholds a small tail so a fence opener split
across deltas is never emitted, releases withheld text when it turns out
not to be a fence, and stops emitting entirely once an opener is seen.
"""

from __future__ import annotations

import json
import re

# Public: store/length_drift.py builds its whole-fence pattern from this, so the
# opener grammar has exactly one definition in the codebase.
OPENER = re.compile(r"```[ \t]*roll\b", re.IGNORECASE)
# A buffer suffix that could still grow into an opener: 1-3 backticks,
# then (only after all 3) optional spaces/tabs and a prefix of "roll".
_OPENER_PREFIX = re.compile(r"(`{1,2}|`{3}[ \t]*(r(o(l(l)?)?)?)?)$", re.IGNORECASE)
# Every character `_OPENER_PREFIX` can match, plus the one newline its `$` may
# match in front of. A match lies wholly inside the trailing run of these, so
# that run is all `_opener_prefix_len` has to search.
_PREFIX_RUN = frozenset("` \trolROL\n")


def trailing_run(buf: str, chars: frozenset, start: int) -> int:
    """Where the run of `chars` that ends `buf` begins, looking no further back
    than `start`. Public for `response_protocol`, which bounds its own prefix
    search the same way."""
    i = len(buf)
    while i > start and buf[i - 1] in chars:
        i -= 1
    return i


def _opener_prefix_len(buf: str, start: int = 0) -> int:
    """How long the suffix of `buf` is that could still grow into an opener.

    `start` is where the caller's undecided text begins: no prefix can start
    before it (see `FenceWatcher.feed`), and searching only the trailing run is
    what keeps a feed from costing the length of everything already streamed.
    The match found is the leftmost one either way, which is what the holdback
    has always measured -- including the one `$` finds in front of a final
    newline, whose length leaves that newline out.
    """
    m = _OPENER_PREFIX.search(buf, trailing_run(buf, _PREFIX_RUN, start))
    return len(m.group(0)) if m else 0


class FenceWatcher:
    def __init__(self) -> None:
        self._buf = ""            # unemitted tail (pre-fence mode)
        self._emitted = 0         # how much of `_buf` has been returned to the caller
        self._after = ""          # everything from the opener onward
        self._open = False
        self.complete = False
        self.truncated = False
        self.body: str | None = None
        self._narration_prefix = ""
        self._finished = False

    def feed(self, chunk: str) -> str:
        # An empty chunk changes nothing, whatever the state: the adapters feed
        # one per SSE line, blank separators included, as proof of life.
        if self._finished or not chunk:
            return ""
        if self._open:
            self._after += chunk
            self._try_close()
            return ""
        # Everything before `_emitted` is DECIDED, so both searches start there
        # rather than at 0. Rescanning the whole buffer on every feed made a
        # reply cost O(length²) in CPU, on the event loop.
        # Starting later cannot miss a match: text is only emitted once the
        # holdback below has ruled out an opener beginning inside it, and every
        # prefix of an opener is itself an `_OPENER_PREFIX` match, so an opener
        # can never begin before `_emitted`. The one character the `$`-before-
        # a-final-newline holdback lets out ("``" + "\n" withholds one backtick
        # and the newline) cannot begin one either -- nothing continues a run of
        # backticks across a newline. `test_stream_watchers_incremental.py`
        # holds this to the rescanning algorithm, feed for feed.
        start = self._emitted
        self._buf += chunk
        m = OPENER.search(self._buf, start)
        if m and m.end() < len(self._buf):
            return self._commit(m)
        # A match ending exactly at the buffer end is deferred: its \b was
        # satisfied only by end-of-string, and the next chunk could extend
        # the word ("```rollback" is not an opener). The prefix-state
        # holdback withholds the trailing "```roll" meanwhile; the next
        # chunk either lets OPENER match for real or flushes it.
        #
        # prefix-state holdback: withhold the longest suffix that could
        # still extend into an opener (backticks + optional spaces/tabs +
        # a prefix of "roll"); a fixed-length tail leaks backticks when
        # the optional whitespace stretches the opener.
        safe_len = max(start, len(self._buf) - _opener_prefix_len(self._buf, start))
        out = self._buf[start:safe_len]
        self._emitted = safe_len
        return out

    def _commit(self, m: re.Match[str]) -> str:
        self._narration_prefix = self._buf[: m.start()]
        out = self._narration_prefix[self._emitted:]
        self._emitted = len(self._narration_prefix)
        self._after = self._buf[m.start():]
        self._open = True
        self._buf = ""
        self._try_close()
        return out

    def _try_close(self) -> None:
        if self.complete:
            return
        first_nl = self._after.find("\n")
        if first_nl < 0:
            return
        close = re.search(r"^```", self._after[first_nl + 1:], re.MULTILINE)
        if close:
            self.body = self._after[first_nl + 1: first_nl + 1 + close.start()]
            self.complete = True

    def finish(self) -> str:
        if self._finished:
            return ""
        self._finished = True
        out = ""
        if not self._open:
            # End of stream is a word boundary: a deferred opener held at
            # the buffer end (see feed) is a real opener after all.
            m = OPENER.search(self._buf, self._emitted)
            if m:
                out = self._commit(m)
        if self._open:
            if not self.complete:
                self.truncated = True
                first_nl = self._after.find("\n")
                self.body = self._after[first_nl + 1:] if first_nl >= 0 else ""
            return out
        out = self._buf[self._emitted:]
        self._emitted = len(self._buf)
        return out

    @property
    def narration(self) -> str:
        return self._narration_prefix if self._open else self._buf


_KEY_RE = {
    "check": re.compile(r'["\']?check["\']?\s*[:=]\s*["\']([^"\']+)["\']'),
    "actor": re.compile(r'["\']?actor["\']?\s*[:=]\s*["\']([^"\']+)["\']'),
    "reason": re.compile(r'["\']?reason["\']?\s*[:=]\s*["\']([^"\']+)["\']'),
    "difficulty": re.compile(r'["\']?difficulty["\']?\s*[:=]\s*(-?\d+)'),
    "modifier": re.compile(r'["\']?modifier["\']?\s*[:=]\s*(-?\d+)'),
}


def parse_roll_body(text: str) -> tuple[dict, list[str]]:
    text = (text or "").strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data, []
    except (json.JSONDecodeError, ValueError):
        pass
    fields: dict = {}
    for key, rx in _KEY_RE.items():
        m = rx.search(text)
        if m:
            fields[key] = int(m.group(1)) if key in ("difficulty", "modifier") else m.group(1)
    problems = [] if fields else ["roll request was unparseable"]
    if fields and "check" not in fields:
        problems.append("roll request had no check id")
    return fields, problems
