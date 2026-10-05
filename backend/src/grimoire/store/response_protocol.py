"""Hidden response control, independent of provider chunk boundaries."""

from __future__ import annotations

import json
import re

from . import fence, state_fence

_HANDOFF = re.compile(r"```[ \t]*handoff\b", re.IGNORECASE)
_PREFIX = re.compile(
    r"(`{1,2}|`{3}[ \t]*(?:h(?:a(?:n(?:d(?:o(?:f(?:f)?)?)?)?)?)?)?)$", re.IGNORECASE
)
# Every character `_PREFIX` can match, plus the newline its `$` may match in
# front of: a match lies inside the trailing run of these (`fence.trailing_run`).
_PREFIX_RUN = frozenset("` \thandofHANDOF\n")


def validate_handoff(payload, eligible, used):
    """`(next, issue)` for a handoff payload. A ref that is eligible but has
    already spoken is reported as `"repeated speaker"`, apart from an
    ineligible one: with automatic rounds remaining, the caller turns that
    repeat into the next round's lead instead of rejecting it."""
    if not isinstance(payload, dict) or set(payload) != {"next"}:
        return None, "missing or invalid handoff"
    ref = payload["next"]
    if ref is None:
        return None, None
    if not isinstance(ref, str) or ref not in eligible:
        return None, "ineligible or repeated speaker"
    if ref in used:
        return None, "repeated speaker"
    return ref, None


class _PreparationPrefix:
    """Remove one leading preparation fence before response controls see it.

    Only a leading fence is special; ordinary prose keeps its existing grammar.
    Prefix bytes wait until distinguishable, and an interrupted hidden block
    stays hidden. Completed lines are consumed once, avoiding rescans of the
    preparation body. Future leading preparation fields can share this parser.
    """

    def __init__(self, enabled):
        self.pending = ""
        self.mode = "prefix" if enabled else "prose"
        self.body = ""

    @staticmethod
    def _possible(text):
        head = text.lstrip(" \t\r\n")
        head = re.sub(r"^```[ \t]*", "```", head).rstrip(" \t\r").lower()
        return "```perception".startswith(head)

    def feed(self, text):
        if self.mode == "prose":
            return text
        self.pending += text
        if self.mode == "prefix":
            candidate = self.pending.lstrip(" \t\r\n")
            head, sep, rest = candidate.partition("\n")
            if not sep and self._possible(candidate):
                return ""
            if sep and re.fullmatch(r"```[ \t]*perception[ \t\r]*", head, re.IGNORECASE):
                self.mode = "hidden"
                self.pending = rest
            else:
                self.mode = "prose"
                out, self.pending = self.pending, ""
                return out
        while "\n" in self.pending:
            line, _, self.pending = self.pending.partition("\n")
            if line.strip(" \t\r") == "```":
                self.mode = "prose"
                out, self.pending = self.pending, ""
                return out
            self.body += line + "\n"
        return ""

    def finish(self):
        if self.mode == "hidden":
            if self.pending.strip(" \t\r") != "```":
                self.body += self.pending
            self.pending = ""
            return ""
        if self.mode == "prefix" and self._possible(self.pending) and self.pending.lstrip().startswith("```"):
            self.pending = ""
            return ""
        out, self.pending = self.pending, ""
        return out


def strip_preparation(text: str) -> str:
    """`text` without one leading perception fence, as a watcher with
    perception on would have streamed it; a no-op for text that has none.

    For a reply whose watcher ran with perception OFF but whose model wrote a
    fence anyway -- a "Keep writing" prefill that failed over to an instruction
    route. The prose keeps its leading whitespace, which is what says how a
    continuation joins the reply before it."""
    parser = _PreparationPrefix(True)
    return parser.feed(text) + parser.finish()


class ResponseWatcher:
    """Rolls interrupt first; state precedes the final handoff and stays hidden."""

    def __init__(self, *, perception=False):
        self.preparation = _PreparationPrefix(perception)
        self.roll = fence.FenceWatcher()
        self.redactor = state_fence.StreamRedactor()
        self.reasoning = ""
        self.raw = ""
        self.visible = 0
        self.handoff = None
        self.issue = None
        self._narration = ""

    def _emit(self, text):
        # Everything before `visible` is decided, so the searches start there
        # -- `fence.FenceWatcher.feed` makes the same argument, and for the
        # same reason: rescanning `raw` from 0 on every feed made a reply
        # O(length²) on the event loop. `visible` only ever stops at the start
        # of a handoff match or of a `_PREFIX` match, every prefix of a handoff
        # opener is itself a `_PREFIX` match, and a search from there finds the
        # same leftmost match a search from 0 would.
        start = self.visible
        self.raw += text
        match = _HANDOFF.search(self.raw, start)
        stop = match.start() if match else len(self.raw)
        if match is None:
            prefix = _PREFIX.search(self.raw, fence.trailing_run(self.raw, _PREFIX_RUN, start))
            if prefix:
                stop = prefix.start()
        delta = self.raw[self.visible : stop]
        self.visible = stop
        return self.redactor.feed(delta)

    def feed(self, text):
        # A no-op for "" in every state (the roll watcher, the searches and the
        # redactor all reach the answer they already had), and the adapters send
        # one per SSE line -- so it is answered before any of them runs.
        if not text:
            return ""
        return self._emit(self.roll.feed(self.preparation.feed(text)))

    def finish(self):
        out = self._emit(self.roll.feed(self.preparation.finish()))
        out += self._emit(self.roll.finish())
        match = _HANDOFF.search(self.raw)
        self._narration = self.raw[: match.start()] if match else self.raw
        if match and not (self.roll.complete or self.roll.truncated):
            tail = self.raw[match.end() :]
            closed = re.fullmatch(r"[ \t\r]*\n(.*?)\n```[ \t\r\n]*", tail, re.DOTALL)
            try:
                self.handoff = json.loads(closed.group(1)) if closed else None
            except (ValueError, TypeError):
                self.handoff = None
        self.issue = None if self.handoff is not None else "missing or invalid handoff"
        if match is None and _PREFIX.search(self.raw) is None:
            out += self.redactor.feed(self.raw[self.visible :])
        out += self.redactor.finish()
        return out

    @property
    def preparation_note(self):
        # The existing reasoning artifact keeps this inspectable without adding
        # private preparation to the post or conversation history.
        return "\n\n[Perception preparation]\n" + self.preparation.body if self.preparation.body else ""

    @property
    def narration(self):
        match = _HANDOFF.search(self.raw)
        partial = _PREFIX.search(self.raw) if match is None else None
        return (
            self.raw[: match.start()]
            if match
            else self.raw[: partial.start()]
            if partial
            else self.raw
        )
