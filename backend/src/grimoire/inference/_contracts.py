"""Immutable operation, stage, attribution, and capture contracts."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple

from .. import decisions, llm_reasoning, tool_calls, wire
from ..llm import LLMClient
from ..llm_errors import LLMError
from ..store.inference.resolved import ResolvedInference

log = logging.getLogger('grimoire.inference')

STRUCTURED = decisions.STRUCTURED_BACKEND
NATIVE = decisions.NATIVE_BACKEND

#: Given the facade call and the meter's live holder, what to await instead --
#: how a caller's time budget runs inside the meter `decide` opens (I2). The
#: call yields the reply text on a structured stage and an `ItemResult` on a
#: native one; the callers' hooks (`budget.run`, `_bounded_call`) are generic.
Around = Callable[[Awaitable[Any], dict], Awaitable[Any]]

#: Handed each call once it settles (spec 9.4), answered or failed with an
#: `LLMError` -- never a cancelled one: `(messages, outcome, target)`. The
#: messages are the request as sent (a structured chunk's
#: `structured_messages`; a native item's normalised body, `llm.native_body`,
#: as one user message), and `[]` for a call refused before anything went
#: out; the outcome is `decisions.outcome`'s record of it, with the call's
#: `stage` and `at` (its batch indices, 01b §3.2); the target is the
#: one the attempt that answered was sent (`llm.ATTEMPTED`, a fallback's or a
#: prompt-only re-send's included), or the stage's account-stamped primary
#: when the call failed -- a native stage's `without_sampling`, as it was
#: sent. One structured chunk is one call, its prompt-only re-send included;
#: each native item is one call.
Capture = Callable[[list[dict], dict, wire.Target], Awaitable[None]]

#: A caller-supplied next resolver (01d §5.2, `routing.CALLER`): handed the
#: triggered items it is to answer and their triggers, in the order taken;
#: its reply is one result per item (`decisions.ResolverReply`). It opens its
#: own meters, captures its own calls and runs under its own budget.
Resolver = Callable[[tuple[decisions.Item, ...], tuple[decisions.Trigger, ...]],
                    Awaitable[decisions.ResolverReply]]

#: What `decide(escalation=)` awaits once some item triggered (01d §5.2):
#: the escalation role's resolution of the task -- or, for a `CALLER`
#: policy, a `Resolver` -- or None and the sentence why there is none. Soft:
#: a refusal is `(None, sentence)`, never a raised 409.
Escalator = Callable[[], Awaitable[tuple[ResolvedInference | Resolver | None, str]]]


class Outcome(dict):
    """The outcome a `Capture` is handed: `decisions.outcome`'s record, equal
    to and JSON-encoded as that plain dict, with the failed call's `LLMError`
    beside it as `failure` (None when the call answered) -- never inside it.
    The record's `error` is `"kind: detail"`, and the detail is the
    provider's own text; a capture that persists keeps the kind, status and
    code read off `failure` instead (01b §3.3)."""

    failure: LLMError | None = None


class Stage(NamedTuple):
    """One stage of a decide chain: the backend (`mode`, a key of
    `_BACKENDS`), the chain it sends -- a primary, and the fallback the
    facade fails over to when one rides it -- and the primary route's retry
    count: None for the facade's own budget (the primary's `llm_retries`),
    0 on a fallback stage, which gets one attempt (spec 5.4, I3), and on a
    native-first one (01c §4.2.2)."""

    mode: str
    chain: wire.Chain
    retries: int | None
    #: A native-first stage (01c §4.2.1): whatever stops it is a statement
    #: about its decisions endpoint, so `run_stages` never marks its provider
    #: dead, and its failures are not composed into the error of an item a
    #: later stage took. False for every other stage.
    isolated: bool = False


@dataclass(frozen=True)
class _Call:
    """The keyword arguments `decide` received, handed to a backend whole,
    with the stage's own chain and retry count (`Stage`)."""

    task: str
    client: LLMClient
    explain: str
    campaign: str
    scene: str
    post: int | None
    round_id: str
    capture: Capture | None
    around: Around | None
    chain: wire.Chain
    retries: int | None
    #: The stage's index in the chain `run_stages` was handed, and its
    #: positions mapped back to batch indices (`positions[p]` is the batch
    #: index of the stage's item `p`; empty means the identity).
    stage: int = 0
    positions: tuple[int, ...] = ()
    #: `decisions.HOP_ESCALATION` on a call an escalation hop makes (01d
    #: §5.5): its `CallRecord.hop` and its capture outcome's "hop". "" on a
    #: call of the chain itself, which then records and captures nothing new.
    hop: str = ""
    #: Attribution every meter of the call files (02 §3.5, 01g-C3): the
    #: reply a decision serves, and the caller's run and its turn.
    response_id: str = ""
    run_id: str = ""
    loop_turn: int | None = None

    def batch(self, unit: Sequence[int]) -> tuple[int, ...]:
        """`unit`'s stage positions as batch indices."""
        if not self.positions:
            return tuple(unit)
        return tuple(self.positions[p] for p in unit)


class _Answered(NamedTuple):
    """What one stage made of the items it was given, by position: each
    item's result (None for an item its stage failed to answer), each failed
    unit -- a chunk on a structured stage, an item on a native one -- as its
    items' positions and its final error, in order, and the stage's ledger
    rows and what answered (`decisions.Decision.served`). Every unanswered
    item is in exactly one failed unit. `stopped` is the failure that made
    the stage start nothing more, or None -- `run_stages` reads it to skip a
    later stage that would only meet it again. `calls` is one
    `decisions.CallRecord` per metered request the stage made, in the order
    they settled."""

    results: tuple[decisions.ItemResult | None, ...]
    failed: tuple[tuple[tuple[int, ...], LLMError], ...]
    rows: tuple[dict, ...]
    served: tuple[tuple[str, str], ...]
    stopped: LLMError | None = None
    calls: tuple[decisions.CallRecord, ...] = ()


#: A backend: one stage's attempt at every item it is given.
Backend = Callable[[tuple[decisions.Item, ...], _Call], Awaitable[_Answered]]


# ==== the tool loop (01g-S4; spec 3.6-3.11) ====
#
# `run_tools` is the one door to a bounded loop of model turns and tool calls.
# The caller supplies the tools, the executor that runs them and the run id;
# the loop sends each turn through the facade (`client.complete`, spelled only
# in this module) under a meter of its own, checks the run's budget before
# every send and every execution, and hands back a `tool_calls.LoopResult`.
# A tool never runs inside an adapter: the loop hands each validated call to
# the caller's `execute`.

#: Handed each turn once it settles (spec 3.10), outside its meter and
#: guarded: the messages as sent (the tool definitions first, as one system
#: message), the outcome `{finish, calls: [{name, arguments}], text_chars}`,
#: and the target that answered (`llm.ATTEMPTED`).
TurnCapture = Callable[[list[dict], dict, wire.Target], Awaitable[None]]


@dataclass(frozen=True)
class LoopOptions:
    """Everything a tool run is handed besides its task and prompt
    (`run_tools`, `stream_tools`): what the caller supplies, the run's
    attribution, and how its turns are read. One value, built at the call
    site and handed through the entry points to the run whole."""

    #: The tools offered, and the executor that runs them
    #: (`tool_calls.registered` is the default): the loop never executes a
    #: tool itself.
    toolset: tool_calls.Toolset
    execute: tool_calls.Execute
    client: LLMClient
    #: The call site's resolution of the task, on a route that requires tools
    #: or lists them as optional.
    resolved: ResolvedInference
    budget: tool_calls.RunBudget
    #: Normally the detached run's `Run.id`: the loop never mints a run id.
    run_id: str
    #: Attribution every turn's meter files (01g-C3), with the caller's round
    #: and reply ids; `scene_identity` is the tools' (`ToolContext`).
    campaign: str = ""
    scene: str = ""
    scene_identity: str = ""
    post: int | None = None
    round_id: str = ""
    response_id: str = ""
    #: Polled before each send and each execution; True ends the run as
    #: `asyncio.CancelledError`.
    cancelled: Callable[[], bool] | None = None
    #: The answer is read and checked against it, with one finalize turn for
    #: one that does not conform.
    final_schema: dict | None = None
    tool_choice: Literal["auto", "required"] = "auto"
    capture: TurnCapture | None = None
    #: 01g-C6: visible text is final -- a turn that showed text and ended in
    #: tool calls ends the run with them declined.
    decline_after_text: bool = False
    #: A caller's display reasoning buffer, ridden in each turn's holder.
    reasoning: llm_reasoning.Buffer | None = None
    #: A fresh predicate per turn (02 §8.2): does this delta show the
    #: reader anything? None reads every non-empty delta as visible.
    visibility: Callable[[], Callable[[str], bool]] | None = None
    #: The caller's list each turn's usage holder is appended to as its
    #: meter opens (02 §8.5): the facade stamps the holder in place, so
    #: the caller reads which attempt is serving while the turn streams.
    holders: list[dict] | None = None

    def attribution(self) -> dict:
        """The keyword arguments every turn's meter is opened with besides
        the task, the run id and the turn (`store.usage.meter`)."""
        return {"campaign": self.campaign, "scene": self.scene, "post": self.post,
                "round_id": self.round_id, "response_id": self.response_id}
