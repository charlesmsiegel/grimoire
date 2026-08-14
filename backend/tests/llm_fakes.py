"""Shared fakes for the LLM gateway, and the canned bodies they replay (#204).

`routes.get_llm` is the injection seam — `app.dependency_overrides[routes.get_llm]`
is how every test that must not reach a provider swaps one of these in, and
these fakes implement exactly the surface `llm.LLMClient` exposes to routes:

    async def stream(messages, conn, usage=None) -> AsyncIterator[str]
    async def complete(messages, conn, usage=None) -> str

`usage` is the accounting holder the real facade fills in place (#152). Every
call stamps the route it ran on, exactly as `llm._stamp` does -- not a courtesy,
but the half of the contract `store.usage.Meter` reads to tell "the request went
out and reported nothing" from "the request was never made". A fake built with
`usage=` then adds what a *provider* would report on top, so a test can drive a
route and assert on the ledger row it filed; the default adds nothing, which is
what an endpoint that reports no usage does.

Before this module, seven near-identical fakes lived inline in `test_routes.py`
and each new call site grew another one. There is now one implementation,
`FakeLLM`; the named classes below are the shapes tests actually ask for,
spelled as constructors rather than as copies.

## Scripted replies vs. a cassette

A **scripted** fake answers by call order: turn 1, then turn 2, and the last
turn repeats once the script runs out. That is right for a test that knows the
sequence it triggers.

A **cassette** answers by what the request looks like — the same trick the
`verify` skill's browser-driven mock plays, promoted out of that throwaway
launcher into a fixture file both can read (`.claude/skills/verify/SKILL.md`
now points at it). That is right for a flow whose
call *order* is an implementation detail (absorb makes several calls of
different kinds), and for proving the fixture bodies still match the prompts the
templates produce: a matcher that stops matching fails the test rather than
silently falling through to a default.

## What "golden" does and does not mean here

The bodies under `fixtures/llm/` are **hand-authored**, not recorded from a
provider, and nothing here ever calls one. They are what a well-formed reply of
each kind looks like, frozen so that parsing and prompt-shape regressions get
caught. They are *not* evidence that any model would answer this way — a replay
fixture never is, even one recorded live, because model output moves under you.
Treat a cassette hit as "the code handled this reply correctly", never as "the
model says this".
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from grimoire.llm_errors import LLMError

FIXTURES = Path(__file__).parent / "fixtures" / "llm"

#: How long `StallingOpenRouter` holds a connection open. Bounded rather than
#: forever so a regression in the caller's own timeout fails the test instead of
#: hanging the suite.
STALL_SECONDS = 30


class CassetteMiss(AssertionError):
    """A request matched no entry in the cassette.

    An assertion, not a runtime error: it means the test drove a call the
    fixtures do not cover, or a prompt template moved out from under a matcher.
    Both are things a test run must report, not paper over with a default reply.
    """


class Cassette:
    """Canned replies keyed by what the request looks like.

    A cassette file is `{"note": ..., "entries": [{"when": {...}, "reply": ...}]}`.
    `when` holds substring predicates over the request, all of which must hold:

    - `system_contains` — text in any `system` message
    - `user_contains` — text in any `user` message
    - `contains` — text in any message at all

    Entries are tried in order and the first match wins, so put the specific
    ones first. `reply` is a string, or a list of strings streamed as deltas.
    """

    def __init__(self, data: dict, name: str = "<inline>"):
        self.name = name
        self.note = data.get("note", "")
        self.entries = data["entries"]
        if not self.entries:
            raise ValueError(f"cassette {name} has no entries")

    @classmethod
    def load(cls, name: str) -> "Cassette":
        path = FIXTURES / f"{name}.json"
        return cls(json.loads(path.read_text(encoding="utf-8")), name)

    @staticmethod
    def _roles(messages: list[dict], role: str) -> str:
        return "\n".join(m.get("content", "") for m in messages if m.get("role") == role)

    def _matches(self, when: dict, messages: list[dict]) -> bool:
        haystacks = {
            "system_contains": self._roles(messages, "system"),
            "user_contains": self._roles(messages, "user"),
            "contains": "\n".join(m.get("content", "") for m in messages),
        }
        for key, needle in when.items():
            if key not in haystacks:
                raise ValueError(f"cassette {self.name}: unknown matcher {key!r}")
            if needle not in haystacks[key]:
                return False
        return True

    def reply(self, messages: list[dict]) -> list[str]:
        """The deltas for this request, or `CassetteMiss` naming what was tried."""
        for entry in self.entries:
            if self._matches(entry.get("when", {}), messages):
                reply = entry["reply"]
                if isinstance(reply, str):
                    return [reply]
                if isinstance(reply, list) and all(isinstance(d, str) for d in reply):
                    return list(reply)
                # A dict here means someone wrote the JSON payload as JSON
                # instead of as the string the model would send; iterating it
                # would stream its keys and fail somewhere far away from here.
                raise ValueError(
                    f"cassette {self.name}: a reply must be a string or a list of "
                    f"strings, got {type(reply).__name__}")
        raise CassetteMiss(
            f"no entry in cassette {self.name!r} matches this request.\n"
            f"tried: {[e.get('when', {}) for e in self.entries]}\n"
            f"request roles: {[m.get('role') for m in messages]}\n"
            f"system message begins: {self._roles(messages, 'system')[:400]!r}")


class FakeLLM:
    """Stands in for `llm.LLMClient` at the `routes.get_llm` seam.

    `turns` is a list of turns, each a list of deltas; calls consume them in
    order and the last turn repeats once the script is exhausted (so a
    single-turn fake answers every call the same way). Pass `cassette` instead
    to answer by request shape.

    Every call is recorded in `requests`; `calls` counts them and `messages` is
    the last request's message list, which is what most assertions want.
    """

    def __init__(self, turns: list[list[str]] | None = None, *,
                 cassette: Cassette | None = None,
                 error: LLMError | None = None, stall: bool = False,
                 usage: dict | None = None):
        if (turns is None) == (cassette is None):
            raise ValueError("FakeLLM takes exactly one of `turns` or `cassette`")
        if turns is not None and not turns:
            # Rejected here rather than at the first call, where it would be an
            # IndexError from inside the fake with nothing pointing at the test
            # that built it.
            raise ValueError("FakeLLM needs at least one turn")
        self.turns = [list(t) for t in (turns or [])]
        self.cassette = cassette
        self.error = error
        self.stall = stall
        self.usage = usage
        self.requests: list[dict] = []
        self.calls = 0

    # ---- the LLMClient surface ----
    async def stream(self, messages, conn, usage=None):
        # Stamped BEFORE anything can fail, like `llm._stamp`: the route is
        # known the moment the attempt starts, and an error frame still has to
        # say which connection produced it.
        if usage is not None:
            usage.update({"model": conn.get("model", ""),
                          "connection": conn.get("name") or conn.get("id")
                          or conn.get("kind") or "?",
                          "provider": conn.get("kind", "openrouter"), "attempts": 1})
        for delta in self._next(messages, conn):
            yield delta
        if self.error is not None:
            raise self.error
        # After the deltas and after the error, like the real thing: a provider
        # reports what a call cost on its final frame, so a stream that dies
        # part-way reports nothing and the row records the failure alone.
        if usage is not None and self.usage is not None:
            usage.update(self.usage)
        if self.stall:
            await asyncio.sleep(STALL_SECONDS)

    async def complete(self, messages, conn, usage=None) -> str:
        # Consumes `stream`, exactly as the real `LLMClient.complete` does,
        # rather than reaching for the next turn itself. That is not a style
        # choice: a fake whose two methods are written separately drifts, and it
        # drifts silently — an injected stall or failure that only `stream`
        # honoured would let a completing route (absorb, dossier, tagline,
        # suggestions) sail past the very condition the test set up. One call is
        # still recorded, because `stream` records exactly once.
        return "".join([delta async for delta in self.stream(messages, conn, usage)])

    # ---- inspection ----
    @property
    def messages(self) -> list[dict] | None:
        return self.requests[-1]["messages"] if self.requests else None

    @property
    def conn(self) -> dict | None:
        return self.requests[-1]["conn"] if self.requests else None

    def _next(self, messages, conn) -> list[str]:
        self.requests.append({"messages": messages, "conn": conn})
        index, self.calls = self.calls, self.calls + 1
        if self.cassette is not None:
            return self.cassette.reply(messages)
        return self.turns[min(index, len(self.turns) - 1)]


def from_cassette(name: str) -> FakeLLM:
    """A fake replaying `fixtures/llm/<name>.json`."""
    return FakeLLM(cassette=Cassette.load(name))


class FakeOpenRouter(FakeLLM):
    """One turn, streamed as the given deltas — the default every route test
    gets from `test_routes.py`'s `client` fixture."""

    def __init__(self, deltas, usage: dict | None = None):
        super().__init__([list(deltas)], usage=usage)


class FakeOpenRouterComplete(FakeLLM):
    """A completer whose reply is a single string (one-call tests) or a list
    consumed one-per-call, in order — absorb's extraction `complete()` followed
    by the audit's, say. The last reply repeats after the list runs out."""

    def __init__(self, text, usage: dict | None = None):
        super().__init__([[t] for t in (text if isinstance(text, list) else [text])],
                         usage=usage)


class CapturingOpenRouter(FakeLLM):
    """Answers "ok" and keeps the request, for tests that assert on what the
    assembler sent rather than on what came back."""

    def __init__(self, deltas=("ok",)):
        super().__init__([list(deltas)])


class FailingOpenRouter(FakeLLM):
    """Streams `deltas`, then fails upstream — a turn that dies part-way."""

    def __init__(self, deltas=(), kind="network", message="connection reset"):
        super().__init__([list(deltas)], error=LLMError(kind, message))


class StallingOpenRouter(FakeLLM):
    """Streams `deltas`, then holds the connection open — the model still
    talking when the client walks away."""

    def __init__(self, deltas=()):
        super().__init__([list(deltas)], stall=True)


class QuietThenAnswers(FakeLLM):
    """Reports liveness with no text before answering — what the facade
    surfaces while it is still waiting on the provider."""

    def __init__(self, text="At last."):
        super().__init__([["", text]])
