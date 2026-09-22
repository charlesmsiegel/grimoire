"""The stream watchers decide each character once, and say what they always said.

`fence.FenceWatcher` and `response_protocol.ResponseWatcher` sit on the event
loop for every streamed reply, and the adapters feed them once per SSE line --
including the blank separator lines, as `""`. They used to rescan the whole
buffer on every feed, which made a reply O(length²) in CPU on the loop that is
also serving everything else. They now search from the first position they
have not already decided, and the empty feed is a no-op.

That change is only worth having if it is INVISIBLE, so the oracles below are
verbatim copies of the rescanning algorithms, kept here on purpose: the
incremental watchers must match them feed for feed -- every returned delta,
`narration` after every feed, and the terminal `complete` / `truncated` /
`body` / `handoff` / `issue` -- over randomized chunkings and over every
chunking of a small alphabet. If the grammar changes, change the oracle with
it; if only the watcher changes, the oracle is the spec.
"""

from __future__ import annotations

import itertools
import json
import random
import re

import pytest

from grimoire.store import fence, response_protocol, turnstate

# ---- oracles: the pre-incremental algorithms, verbatim ---------------------


class _OracleFence:
    def __init__(self) -> None:
        self._buf = ""
        self._emitted = ""
        self._after = ""
        self._open = False
        self.complete = False
        self.truncated = False
        self.body: str | None = None
        self._narration_prefix = ""
        self._finished = False

    def feed(self, chunk: str) -> str:
        if self._finished:
            return ""
        if self._open:
            self._after += chunk
            self._try_close()
            return ""
        self._buf += chunk
        m = fence.OPENER.search(self._buf)
        if m and m.end() < len(self._buf):
            return self._commit(m)
        prefix = re.search(r"(`{1,2}|`{3}[ \t]*(r(o(l(l)?)?)?)?)$", self._buf, re.IGNORECASE)
        safe_len = max(len(self._emitted),
                       len(self._buf) - (len(prefix.group(0)) if prefix else 0))
        out = self._buf[len(self._emitted): safe_len]
        self._emitted = self._buf[:safe_len]
        return out

    def _commit(self, m: re.Match[str]) -> str:
        self._narration_prefix = self._buf[: m.start()]
        out = self._narration_prefix[len(self._emitted):]
        self._emitted = self._narration_prefix
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
            m = fence.OPENER.search(self._buf)
            if m:
                out = self._commit(m)
        if self._open:
            if not self.complete:
                self.truncated = True
                first_nl = self._after.find("\n")
                self.body = self._after[first_nl + 1:] if first_nl >= 0 else ""
            return out
        out = self._buf[len(self._emitted):]
        self._emitted = self._buf
        return out

    @property
    def narration(self) -> str:
        return self._narration_prefix if self._open else self._buf


_O_HANDOFF = re.compile(r"```[ \t]*handoff\b", re.IGNORECASE)
_O_PREFIX = re.compile(
    r"(`{1,2}|`{3}[ \t]*(?:h(?:a(?:n(?:d(?:o(?:f(?:f)?)?)?)?)?)?)?)$", re.IGNORECASE
)


class _OracleResponse:
    def __init__(self):
        self.roll = _OracleFence()
        self.redactor = turnstate.StreamRedactor()
        self.reasoning = ""
        self.raw = ""
        self.visible = 0
        self.handoff = None
        self.issue = None
        self._narration = ""

    def _emit(self, text):
        self.raw += text
        match = _O_HANDOFF.search(self.raw)
        stop = match.start() if match else len(self.raw)
        if match is None:
            prefix = _O_PREFIX.search(self.raw)
            if prefix:
                stop = prefix.start()
        delta = self.raw[self.visible : stop]
        self.visible = stop
        return self.redactor.feed(delta)

    def feed(self, text):
        return self._emit(self.roll.feed(text))

    def finish(self):
        out = self._emit(self.roll.finish())
        match = _O_HANDOFF.search(self.raw)
        self._narration = self.raw[: match.start()] if match else self.raw
        if match and not (self.roll.complete or self.roll.truncated):
            tail = self.raw[match.end() :]
            closed = re.fullmatch(r"[ \t\r]*\n(.*?)\n```[ \t\r\n]*", tail, re.DOTALL)
            try:
                self.handoff = json.loads(closed.group(1)) if closed else None
            except (ValueError, TypeError):
                self.handoff = None
        self.issue = None if self.handoff is not None else "missing or invalid handoff"
        if match is None and _O_PREFIX.search(self.raw) is None:
            out += self.redactor.feed(self.raw[self.visible :])
        out += self.redactor.finish()
        return out

    @property
    def narration(self):
        match = _O_HANDOFF.search(self.raw)
        partial = _O_PREFIX.search(self.raw) if match is None else None
        return (
            self.raw[: match.start()]
            if match
            else self.raw[: partial.start()]
            if partial
            else self.raw
        )


# ---- the comparison ----------------------------------------------------------


def _fence_trace(w, deltas):
    steps = [(w.feed(d), w.narration) for d in deltas]
    tail = w.finish()
    return steps, tail, w.narration, w.complete, w.truncated, w.body


def _response_trace(w, deltas):
    steps = [(w.feed(d), w.narration, w.visible) for d in deltas]
    tail = w.finish()
    return (steps, tail, w.narration, w.handoff, w.issue,
            w.roll.complete, w.roll.truncated, w.roll.body)


def _assert_same(deltas):
    assert _fence_trace(fence.FenceWatcher(), deltas) == _fence_trace(_OracleFence(), deltas), deltas
    assert (_response_trace(response_protocol.ResponseWatcher(), deltas)
            == _response_trace(_OracleResponse(), deltas)), deltas


#: Pieces chosen to sit on every edge the grammars have: split and stretched
#: openers, a word that merely starts like one (`rollback`, `handoffs`), case,
#: the tracker fence the redactor withholds, and the newline `$` may match in
#: front of -- which is the case the prefix holdback has to reproduce exactly.
_PIECES = [
    "The lantern gutters. ", "`", "``", "```", "``` ", "```\t", "```roll", "```rollback",
    "```handoff", "```handoffs", "```hand", "\n", "```json\n{}\n```\n", "Seraphine says, ",
    '{"next": null}', '```roll\n{"check": "notice"}\n```', '```handoff\n{"next": null}\n```',
    "```state\n", "mood: calm", "\n```\n", "``roll", "````", "  ", "`\n", "``` \n",
    "```ROLL", "```HandOff", "\t", '```\thandoff\n{"next": null}\n```', "``\n", "ro", "ll",
]


def _chunked(text: str, rng: random.Random) -> list[str]:
    out, i = [], 0
    while i < len(text):
        n = rng.choice([1, 1, 2, 3, 4, 7, 12])
        out.append(text[i:i + n])
        i += n
        if rng.random() < 0.5:
            out.append("")  # a blank SSE line, as the adapters feed it
    return out


def test_randomized_chunkings_match_the_rescanning_oracle():
    rng = random.Random(162)
    for _ in range(1500):
        text = "".join(rng.choice(_PIECES) for _ in range(rng.randint(1, 30)))
        _assert_same(_chunked(text, rng))
        _assert_same([text])  # and the whole reply in one delta


def test_every_short_chunking_matches_the_oracle():
    alphabet = ["```", "`", " ", "hand", "off", "s", "\n", "", "x", "roll",
                '{"next": null}\n```']
    for n in range(1, 5):
        for combo in itertools.product(alphabet, repeat=n):
            _assert_same(list(combo))


class _Spy:
    """A compiled pattern that counts the characters each search could visit."""

    def __init__(self, rx: re.Pattern[str]):
        self.rx = rx
        self.scanned = 0

    def search(self, string: str, pos: int = 0):
        self.scanned += len(string) - pos
        return self.rx.search(string, pos)


def _prose(size: int) -> list[str]:
    words = ("The lantern gutters and Seraphine turns a page of the tide ledger, "
             "counting the debts of Saltmarch aloud. ")
    text = (words * (size // len(words) + 1))[:size]
    deltas = []
    for i in range(0, len(text), 4):
        deltas += ["", text[i:i + 4], ""]  # one token per event, blank lines between
    return deltas


def test_a_reply_is_scanned_in_linear_time(monkeypatch):
    size = 20_000
    spies = {name: _Spy(getattr(fence, name)) for name in ("OPENER", "_OPENER_PREFIX")}
    for name, spy in spies.items():
        monkeypatch.setattr(fence, name, spy)
    w = fence.FenceWatcher()
    shown = "".join(w.feed(d) for d in _prose(size)) + w.finish()
    assert len(shown) == size
    # Rescanning from zero on each of ~15,000 feeds visits ~10^8 characters;
    # deciding each one once visits a small multiple of the reply.
    assert sum(s.scanned for s in spies.values()) < 4 * size

    rspies = {name: _Spy(getattr(response_protocol, name)) for name in ("_HANDOFF", "_PREFIX")}
    for name, spy in rspies.items():
        monkeypatch.setattr(response_protocol, name, spy)
    for spy in spies.values():
        spy.scanned = 0
    r = response_protocol.ResponseWatcher()
    shown = "".join(r.feed(d) for d in _prose(size)) + r.finish()
    assert len(shown) == size
    assert sum(s.scanned for s in (*spies.values(), *rspies.values())) < 10 * size


@pytest.mark.parametrize("prefix", ["", "Mara ", "Mara ``", "Mara ```ro", "Mara ```handoff",
                                    "Mara\n```roll\n{", "Mara\n```state\n{"])
def test_an_empty_feed_changes_nothing(prefix):
    for make in (fence.FenceWatcher, response_protocol.ResponseWatcher):
        w = make()
        w.feed(prefix)
        parts = [w, *(getattr(w, k) for k in ("roll", "redactor") if hasattr(w, k))]
        before = [dict(vars(p)) for p in parts]
        assert w.feed("") == ""
        assert [dict(vars(p)) for p in parts] == before
