"""streaming: display responsibilities."""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool

from ... import store, wire
from ...llm import ATTEMPTED
from . import _state


def _served(meter, asked: str) -> str:
    """The id of the connection that actually answered `meter`'s call: the one
    the facade stamped on the attempt that ran, which is the fallback when the
    primary was exhausted, else `asked`, the provider the turn asked for (its
    resolution's `chain.primary.provider_id`). "" for a connection with no
    id."""
    attempted = ((meter.usage if meter else None) or {}).get(ATTEMPTED)
    return attempted.provider_id if isinstance(attempted, wire.Target) else asked


def _asked(resolved) -> str:
    """The provider a turn's resolution asks for first: its primary target's
    id ("" for none) -- the display fact `_served` falls back on."""
    chain = resolved.chain if resolved is not None else None
    return chain.primary.provider_id if chain is not None else ""


def _display_stream(cid: str | None, asked: str, meter=None) -> store.regex.stream.DisplayStream:
    """The display-phase stream for a turn that asked for provider `asked`,
    built from the connection `meter`'s call is actually running on
    (`_served`) -- after a fallback, the one whose rules the saved message
    will be filed under. `cid=None` is a caller with no campaign to read rules
    for, and gets an inactive stream.
    """
    entries = (store.regex.layers.effective(cid=cid, connection=_served(meter, asked))
               if cid else [])
    return store.regex.stream.DisplayStream(entries)


def _visible_frames(display, text: str, *, last: bool = False) -> list[str]:
    """The frames for `text`, the visible words that would have gone out as one
    `delta`: that `delta`, or when `display` is active the `display` frame the
    throttle allows. `last` closes the stream, which sends whatever the throttle
    held back. No `display` at all is an inactive one."""
    if display is None or not display.active:
        return [_state._sse({"delta": text})] if text else []
    frames = [display.feed(text) if text else None, display.finish() if last else None]
    return [_state._sse({"display": f}) for f in frames if f]


class _Display:
    """One contribution's display stream, built on its first visible text.

    Not before: which connection serves the call is known only once an attempt
    is running, and a fallback takes over before any text arrives (text already
    on screen cannot fail over). So the first visible words are the moment the
    serving connection -- and so its rules -- is settled. Until then there is
    nothing to show and nothing to build.
    """

    def __init__(self, cid: str | None, asked: str, meter) -> None:
        self._cid, self._asked, self._meter = cid, asked, meter
        self.stream: store.regex.stream.DisplayStream | None = None

    @property
    def active(self) -> bool:
        return self.stream is not None and self.stream.active

    async def build(self) -> None:
        """Settle the stream now, text or not -- for a caller that has to know
        whether it is `active` before deciding to ask for any."""
        if self.stream is None:
            self.stream = await run_in_threadpool(
                _display_stream, self._cid, self._asked, self._meter)

    async def frames(self, text: str, *, last: bool = False) -> list[str]:
        if text:
            await self.build()
        return _visible_frames(self.stream, text, last=last)
