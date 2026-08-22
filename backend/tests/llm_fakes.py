"""Shared fakes for the LLM gateway, and the canned bodies they replay (#204).

`routes.get_llm` is the injection seam — `app.dependency_overrides[routes.get_llm]`
is how every test that must not reach a provider swaps one of these in, and
these fakes implement exactly the surface `llm.LLMClient` exposes to routes:

    async def stream(messages, conn, usage=None) -> AsyncIterator[str]
    async def complete(messages, conn, usage=None) -> str
    async def list_models(conn) -> list[dict]
    async def check(conn) -> None

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
import threading
from pathlib import Path

import anyio

from grimoire.llm import effective_model
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
    def load(cls, name: str) -> Cassette:
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
                 fail_after: int = 0, usage: dict | None = None,
                 models: list[dict] | None = None,
                 models_error: LLMError | None = None,
                 health_error: LLMError | None = None):
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
        # How many calls answer normally before `error` starts being raised. 0
        # (the default) fails the first call, which is what every existing
        # failure test means. A batch route needs the other shape: a run that
        # got somewhere before the provider died, so the test can say what
        # happens to the part that already landed.
        self.fail_after = fail_after
        self.stall = stall
        self.usage = usage
        #: The catalog `list_models` answers, and the failures the two
        #: non-generating halves of the surface raise (#146, #149). Separate
        #: from `error` on purpose: a connection whose *generation* fails is
        #: not thereby one whose catalog fails, and a test that conflated them
        #: could not tell a route that asks the wrong provider from one that
        #: asks the right provider badly.
        self.models = list(models or [])
        self.models_error = models_error
        self.health_error = health_error
        self.requests: list[dict] = []
        self.calls = 0
        #: The connections `list_models`/`check` were asked about, in order,
        #: and the outcomes a route filed back through `note_outcome`.
        self.listed: list[dict] = []
        self.checked: list[dict] = []
        self.noted: list[tuple] = []

    # ---- the LLMClient surface ----
    async def stream(self, messages, conn, usage=None):
        # Stamped BEFORE anything can fail, like `llm._stamp`: the route is
        # known the moment the attempt starts, and an error frame still has to
        # say which connection produced it.
        if usage is not None:
            # `effective_model`, not `conn["model"]`, for the reason `llm._stamp`
            # uses it: a claude connection with no model still runs one, and a
            # fake that stamped the empty string would let a test assert a model
            # the real facade never records.
            usage.update({"model": effective_model(conn),
                          "connection": conn.get("name") or conn.get("id")
                          or conn.get("kind") or "?",
                          "provider": conn.get("kind", "openrouter"), "attempts": 1})
        deltas = self._next(messages, conn)   # records the request and counts it
        for delta in deltas:
            yield delta
        # After the deltas, so an injected failure is still a turn that died
        # part-way. `calls` counts this one already, so `fail_after=0` (the
        # default) fails the first call and `fail_after=1` the second.
        if self.error is not None and self.calls > self.fail_after:
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

    async def list_models(self, conn) -> list[dict]:
        """The catalog half of the facade's surface (#149).

        Records the whole connection, not just its base URL: which *provider*
        a catalog was fetched from is the entire question the issue is about,
        and a fake that only kept the URL could not tell an OpenRouter fetch
        from a custom endpoint's.
        """
        self.listed.append(conn)
        if self.models_error is not None:
            raise self.models_error
        return list(self.models)

    async def check(self, conn) -> None:
        """The health half (#146). Returns on healthy, raises on not."""
        self.checked.append(conn)
        if self.health_error is not None:
            raise self.health_error

    def note_outcome(self, conn, error) -> None:
        """The outcome a route hands back because the facade could not see it —
        a generation cancelled by its total-duration ceiling (#146)."""
        self.noted.append((conn, error))

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


def from_entries(entries: list[dict], name: str = "<inline>") -> FakeLLM:
    """A fake replaying cassette entries given inline, for a test whose bodies
    are its own rather than the shared `campaign_flow` fixture's.

    The point is not brevity, it is order-independence. A scripted fake answers
    call 1 and then call 2; absorb issues its calls concurrently, so "call 1"
    names nothing. Matching on the prompt that OWNS each call keeps the
    assertion about which reply the code got rather than about which order it
    asked in -- and an unmatched request still raises `CassetteMiss` rather
    than falling through, which is what keeps the migrated tests meaningful.
    """
    return FakeLLM(cassette=Cassette({"entries": entries}, name))


class FakeOpenRouter(FakeLLM):
    """One turn, streamed as the given deltas — the default every route test
    gets from `test_routes.py`'s `client` fixture."""

    def __init__(self, deltas, usage: dict | None = None):
        super().__init__([list(deltas)], usage=usage)


class FakeOpenRouterComplete(FakeLLM):
    """A completer whose reply is a single string (one-call tests) or a list
    consumed one-per-call, in order. The last reply repeats after the list runs
    out, so a single-element list answers every call the same way.

    A MULTI-element list is only correct where the caller's call order is part
    of what the test asserts. Absorb's is not — its phases run concurrently, so
    position names nothing — and absorb tests use `from_entries` (or
    `from_cassette`), matching on the system prompt that owns each call."""

    def __init__(self, text, usage: dict | None = None):
        super().__init__([[t] for t in (text if isinstance(text, list) else [text])],
                         usage=usage)


class CapturingOpenRouter(FakeLLM):
    """Answers "ok" and keeps the request, for tests that assert on what the
    assembler sent rather than on what came back."""

    def __init__(self, deltas=("ok",)):
        super().__init__([list(deltas)])


class FailingOpenRouter(FakeLLM):
    """Streams `deltas`, then fails upstream — a turn that dies part-way.

    `retry_after` is the window a provider named for itself (#144), which the
    routes turn into the `Retry-After` of a 429 (#213). Defaulted to None, the
    shape of every failure that carries no such advice.

    `fail_after` answers that many calls normally first, which is what a route
    making a call PER RECORD needs: a run that got somewhere before the
    provider died is a different case from one that never started, and only the
    first can be asked what happened to the part that already landed."""

    def __init__(self, deltas=(), kind="network", message="connection reset",
                 retry_after: float | None = None, fail_after: int = 0):
        super().__init__([list(deltas)], error=LLMError(kind, message, retry_after),
                         fail_after=fail_after)


class FakeCatalog(FakeLLM):
    """A gateway that answers catalog and health questions, for the routes that
    ask them (#146, #149).

    There used to be a second fake here for a second seam: model listing was a
    dependency of its own, injected at `routes.get_openai_compatible_client`,
    because the gateway dispatched generation by connection kind and knew
    nothing about catalogs. #149 made listing kind-dispatched too — an
    OpenRouter connection lists OpenRouter's models and a custom endpoint lists
    its own — so it moved onto the facade, and the seam it needed went with it.

    `turns` is defaulted rather than required: these tests drive a route that
    never generates, and a fake that demanded a script for a call it will not
    receive would be noise in every one of them.
    """

    def __init__(self, models=None, error=None, health_error=None, turns=(("ok",),)):
        super().__init__([list(t) for t in turns], models=models,
                         models_error=error, health_error=health_error)


class StallingGateway(FakeCatalog):
    """A gateway whose call never answers — for the ceilings that exist to cut
    one off (#146, #272).

    `where` names which call hangs, because the two ceilings are different
    things: `check` is bounded by the health route's own constant, and
    `complete` by `llm_call_budget`. Bounded rather than forever so a
    regression in either fails the suite in seconds instead of hanging it.
    """

    def __init__(self, where="check", seconds=STALL_SECONDS):
        super().__init__()
        self.where = where
        self.seconds = seconds

    async def check(self, conn) -> None:
        if self.where == "check":
            await asyncio.sleep(self.seconds)
        await super().check(conn)

    async def complete(self, messages, conn, usage=None) -> str:
        if self.where == "complete":
            await asyncio.sleep(self.seconds)
        return await super().complete(messages, conn, usage)


# ---- provider doubles ----
# Below the facade rather than in place of it: these stand in for one of the
# three clients `LLMClient` dispatches to, for the tests that need a REAL
# facade (retries, fallback, the health observer) with fake providers under it.
# Shared for the same reason the gateway fakes are -- a provider contract
# written inline in a suite drifts the moment the facade changes, and the suite
# goes on passing.
class ScriptedProvider:
    """Streams `chunks`, then raises `error` if it was given one."""

    def __init__(self, chunks=("hi",), error=None):
        self.chunks = list(chunks)
        self.error = error
        self.calls = 0

    async def stream(self, messages, *args, **kwargs):
        self.calls += 1
        for chunk in self.chunks:
            yield chunk
        if self.error is not None:
            raise self.error


class FlakyProvider:
    """Fails the first attempt and serves every one after it — the shape a
    retry and a fallback are both tested against."""

    def __init__(self, error, chunks=("hi",)):
        self.error = error
        self.chunks = list(chunks)
        self.calls = 0

    async def stream(self, messages, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise self.error
            yield  # pragma: no cover - unreachable; makes this a generator
        for chunk in self.chunks:
            yield chunk


class RecordingProvider:
    """Answers the two non-generating calls and records what it was asked.

    Whole argument tuples, because which *provider* was asked with which
    credential is the entire question #149 turns on — a double that kept only
    the answer could not tell an OpenRouter fetch from a custom endpoint's.
    """

    def __init__(self, models=(), list_error=None, probe_error=None):
        self.models = list(models)
        self.list_error = list_error
        self.probe_error = probe_error
        self.listed: list[tuple] = []
        self.probed: list[tuple] = []

    async def list_models(self, *args):
        self.listed.append(args)
        if self.list_error is not None:
            raise self.list_error
        return list(self.models)

    async def probe(self, *args):
        self.probed.append(args)
        if self.probe_error is not None:
            raise self.probe_error


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


class HeldOpenRouter(FakeLLM):
    """A provider that stops after its first delta until the test releases it.

    What makes "mid-generation" a defined MOMENT rather than a hopeful sleep: a
    sleep long enough to be safe is also long enough to let the whole turn
    finish, which passes vacuously -- and the detach tests exist precisely to
    catch a turn that did not survive. Only useful against a real server, since
    `TestClient` buffers a streaming response to completion.

    `replies` maps a marker to the reply a request carrying that marker gets, so
    two concurrent turns are distinguishable. A single string answers every
    request the same way. Matching on the request rather than on call order
    because concurrent calls have no order: that is the whole point of the tests
    that use this, and a scripted-by-position fake would make "no cross-
    contamination" unfalsifiable.
    """

    def __init__(self, replies: str | dict[str, str] = "The lamps are already lit."):
        self._replies = ({"": replies} if isinstance(replies, str) else dict(replies))
        super().__init__([["…"]])          # never consulted; `_next` is overridden
        self._first = threading.Event()
        self._go = threading.Event()

    def await_first_delta(self, timeout: float = 5.0) -> None:
        assert self._first.wait(timeout), "the provider never produced a delta"

    def release(self) -> None:
        self._go.set()

    def _reply_for(self, messages) -> str:
        text = " ".join(str(m.get("content", "")) for m in messages)
        for marker, reply in self._replies.items():
            if marker and marker in text:
                return reply
        return self._replies.get("", next(iter(self._replies.values())))

    def _next(self, messages, conn):
        # Two deltas: the first arrives immediately, the second only after
        # `release()`, so a test can act between them.
        reply = self._reply_for(messages)
        head, _, tail = reply.partition(" ")
        return [head + " ", tail]

    async def stream(self, messages, conn, usage=None):
        first = True
        async for delta in super().stream(messages, conn, usage):
            yield delta
            if first:
                first = False
                self._first.set()
                # A worker thread rather than an anyio Event: `release()` is
                # called from the TEST thread, and setting an anyio event from
                # off-loop is not safe.
                await anyio.to_thread.run_sync(self._go.wait)
