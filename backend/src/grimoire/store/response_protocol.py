"""Hidden response control, independent of provider chunk boundaries."""

from __future__ import annotations

import json
import re

from . import fence, turnstate

_HANDOFF = re.compile(r"```[ \t]*handoff\b", re.IGNORECASE)
_PREFIX = re.compile(
    r"(`{1,2}|`{3}[ \t]*(?:h(?:a(?:n(?:d(?:o(?:f(?:f)?)?)?)?)?)?)?)$", re.IGNORECASE
)


def validate_handoff(payload, eligible, used):
    if not isinstance(payload, dict) or set(payload) != {"next"}:
        return None, "missing or invalid handoff"
    ref = payload["next"]
    if ref is None:
        return None, None
    if not isinstance(ref, str) or ref not in eligible or ref in used:
        return None, "ineligible or repeated speaker"
    return ref, None


class ResponseWatcher:
    """Rolls interrupt first; state precedes the final handoff and stays hidden."""

    def __init__(self):
        self.roll = fence.FenceWatcher()
        self.redactor = turnstate.StreamRedactor()
        self.raw = ""
        self.visible = 0
        self.handoff = None
        self.issue = None
        self._narration = ""

    def _emit(self, text):
        self.raw += text
        match = _HANDOFF.search(self.raw)
        stop = match.start() if match else len(self.raw)
        if match is None:
            prefix = _PREFIX.search(self.raw)
            if prefix:
                stop = prefix.start()
        delta = self.raw[self.visible : stop]
        self.visible = stop
        return self.redactor.feed(delta)

    def feed(self, text):
        return self._emit(self.roll.feed(text))

    def finish(self):
        out = self._emit(self.roll.finish())
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
