"""Display-phase rules over a reply that is still arriving.

The client used to append every `delta` to what it showed. With a display rule
in force that is wrong the moment a `</think>` closes over text already on
screen, so the server runs the display phase over everything received so far
and sends the *difference* from what it last sent: the client truncates its
buffer to `keep` characters and appends `tail`.

Throttled, because the whole text is re-run through every rule each time: one
frame per `interval`, and `finish` always sends whatever the last one left out.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from . import apply


class DisplayStream:
    """One contribution's raw text in, display-text diffs out.

    `entries` is the resolved rule list for the message being produced. `active`
    is false when no rule in it applies to a newest model reply on screen, and
    an inactive stream is never fed -- the caller keeps sending plain deltas, so
    a library with no display rules sees the bytes it always did.
    """

    def __init__(self, entries: list[apply.Entry], *,
                 clock: Callable[[], float] = time.monotonic, interval: float = 0.1):
        self._entries = entries
        self._clock = clock
        self._interval = interval
        self._raw = ""
        self._sent = ""
        self._last: float | None = None
        self.active = any(
            apply.skip_reason(e, role="model", phase="display", depth=0) is None
            for e in entries
        )

    def feed(self, delta: str) -> dict | None:
        """Take more raw text; a frame if the interval has elapsed and the
        display text moved, else None."""
        self._raw += delta
        now = self._clock()
        if self._last is not None and now - self._last < self._interval:
            return None
        self._last = now
        return self._diff()

    def finish(self) -> dict | None:
        """The remainder, whatever the throttle held back. None if the client
        already shows the final text."""
        return self._diff()

    def _diff(self) -> dict | None:
        shown = apply.run(self._raw, self._entries, role="model", phase="display", depth=0)
        keep = 0
        limit = min(len(shown), len(self._sent))
        while keep < limit and shown[keep] == self._sent[keep]:
            keep += 1
        if keep == len(shown) == len(self._sent):
            return None
        self._sent = shown
        return {"keep": keep, "tail": shown[keep:]}
