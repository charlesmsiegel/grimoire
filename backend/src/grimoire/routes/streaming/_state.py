"""streaming: state responsibilities."""

from __future__ import annotations

import itertools
import json
import time

# An SSE comment: framing every proxy on the path understands as traffic, and
# that `parseSSEChunk` already skips (it only reads `data:` lines), so the
# client needs no matching change to tolerate one. Emitted whenever the facade
# reports it is still waiting on the model (#95) -- before the first token, a
# generation is otherwise indistinguishable from a dead connection.
_HEARTBEAT = ": heartbeat\n\n"

#: The least time since the last frame of any kind before an empty delta earns
#: a heartbeat. The facade ticks every `llm.HEARTBEAT_INTERVAL` while the model
#: is silent, but the adapters ALSO report every SSE line as an empty delta,
#: blank separators included -- so an unthrottled turn sent a heartbeat beside
#: nearly every token, each one written to the socket and buffered and indexed
#: by its run for a reconnect to replay. Proof of life needs one a second.
HEARTBEAT_GAP = 1.0


class _Liveness:
    """Whether an empty delta has earned a heartbeat frame.

    Due once `HEARTBEAT_GAP` has passed since the last frame this stream
    produced, heartbeat or not, so a stream whose deltas are flowing sends
    none. The first is always due: the quiet stretch before a first token is
    the one #95 exists for, and it must show traffic straight away.

    Consulted where frames are PRODUCED, before a run buffers and indexes them,
    so a frame that is never sent never takes an index and a resume cursor
    still counts a contiguous sequence.
    """

    def __init__(self) -> None:
        self._last = float("-inf")

    def sent(self) -> None:
        """Record a frame that went out."""
        self._last = time.monotonic()

    def due(self) -> bool:
        """Whether a heartbeat goes out now; if so, it counts as sent."""
        now = time.monotonic()
        if now - self._last < HEARTBEAT_GAP:
            return False
        self._last = now
        return True


# Which turn each scene currently belongs to, newest claim wins. A cancelled
# turn's flush runs after its socket has closed, so it has to be able to ask
# whether it is still the turn the scene belongs to before writing anything.
#
# Transcript length cannot answer that, which review caught after it was first
# used for exactly this: three of the four `_chat_stream` callers -- retry,
# regenerate, and the director-note/empty send -- append nothing of their own,
# so two overlapping ones capture an identical length and the older one's abort
# would sail through the check and persist into the newer turn.
#
# In-process only, and that is the right scope: it distinguishes two turns
# racing inside one backend, which is what a player generates. Two *processes*
# on one scene is the case `store/locks.py` already documents as beyond what
# this app can serialize. Entries are overwritten per turn and never removed --
# one small entry per scene ever streamed, which is not worth reaping.
_turn_tokens: dict[tuple[str, str], int] = {}
_turn_seq = itertools.count(1)


def _claim_turn(cid: str, sid: str) -> int:
    """Make this the scene's current turn and return the token proving it."""
    token = next(_turn_seq)   # atomic under the GIL; claims come from request threads
    _turn_tokens[(cid, sid)] = token
    return token


def _owns_turn(cid: str, sid: str, token: int) -> bool:
    return _turn_tokens.get((cid, sid)) == token


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


class StreamOutcome:
    """What a producer decided about its own run, for the runner to apply.

    A detached producer is driven by `runner._guarded`, which cannot read
    success off the wire: these generators handle an upstream `LLMError`, a
    contended finalize, and a scene that changed underneath them by emitting an
    error frame and returning NORMALLY. "Did not raise" therefore covered both
    a delivered reply and three ways of failing, and every one of them was
    recorded `landed` -- so a poll, and the notification that reads it, told the
    user their turn had landed when nothing was persisted.

    Deliberately a plain box the generator fills rather than a return value:
    the producer is an async *iterator*, so there is nowhere for it to return
    to, and its consumer only knows it is finished when iteration stops.

    A producer that never sets anything leaves `result()` at `None`, which the
    runner reads as "infer from how the task ended". That keeps every
    unmigrated caller working exactly as before.
    """

    __slots__ = ("at", "error", "state")

    def __init__(self) -> None:
        self.state: str | None = None
        self.error: dict | None = None
        #: How long the transcript was when this turn's terminal write finished,
        #: or None when it wrote nothing. See `persisted`.
        self.at: int | None = None

    def persisted(self, at: int | None) -> None:
        """The transcript length a terminal write of this turn left behind.

        This is the follow-up boundary (#397): the length `askAfterPost` used
        to read on the client and pass as `upto`, so a fold cannot swallow a
        post whose reply has not been written yet. Recorded here rather than
        read afterwards, because "afterwards" is a second read of a file
        anything may have moved on -- and the server, unlike the client, can
        take it under the very lock the write held.

        The LAST write wins, and there is at most one that matters: a turn
        either persists its reply or persists a partial and stops. A turn that
        wrote nothing leaves this None, which is what tells the runner there is
        no follow-up worth scheduling -- neither the summary's coverage nor the
        scene-break count has moved.

        `None` is accepted and ignored, so a boundary that could not be read
        (`_tail_length`) is a skipped follow-up rather than an exception out of
        a `finalize` that has already written the reply.
        """
        if at is not None:
            self.at = at

    def land(self) -> None:
        """The turn finished and its writes were attempted -- the ordinary end."""
        if self.state is None:            # a failure already recorded wins
            self.state = "landed"

    def fail(self, kind: str, detail: str, **note) -> None:
        """`note` is what the handler DID, carried onto the run as well as the
        wire. Today that is `post_returned`: a failed turn whose rollback took
        the player's words back off the transcript. The live frame said so and
        the run did not, so a client that lost the frame and discovered the
        outcome by polling was told the turn failed but not that its prompt was
        gone -- and the composer could not give the words back. Recovery reads
        the run, so the run has to know."""
        self.state = "failed"
        self.error = {"kind": kind, "detail": detail, **note}

    def result(self) -> dict | None:
        if self.state is None:
            return None
        return {"state": self.state, "error": self.error}
