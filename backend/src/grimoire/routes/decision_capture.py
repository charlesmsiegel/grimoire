"""Decision capture: one prompt-log entry per decision a site makes (roadmap
01b, `docs/superpowers/specs/2026-10-09-roadmap-01b-decision-capture-design.md`).

Every `decide()` call site wraps its decision in a **capture scope**
(`capturing`) and hands `decide` the scope's hook (`Scope.hook`). Inside the
scope the hook only appends each settled call -- its request as sent, its
outcome (`decisions.outcome`, stamped with `stage` and `at`), the target that
answered -- to a list in memory. When the scope exits it files **one**
prompt-log entry, in a worker thread, in the prompt log's decision pool
(`store.prompt_log.DECIDE`), so a decision never evicts a turn's snapshot.

    async with decision_capture.capturing(cid, sid, "scene-break") as scope:
        decision = await operations.decide(..., capture=scope.hook())

The rules, each of which the spec argues:

- **Off when capture is off.** `prompt_log.capturing()` is read once, off the
  loop, on entry; False makes `hook()` None, so `decide` builds no native
  request body and nothing is kept or filed.
- **Filed on exit**, normal or by an `LLMError` (a `BudgetRefused` included)
  or a `decisions.DecideRequestError` -- a failed decision is one of the most
  worth having. Never on cancellation, on `Abandoned` or on any other
  exception crossing the scope, and never when the site's `abandoned` check
  answers True (voice drift swallows `Abandoned` per NPC). The exception is
  re-raised unchanged. A scope that made no call and raised nothing files
  nothing: it decided nothing.
- **Fenced.** A scene-level scope reads the scene's identity strictly on
  entry and captures nothing for a scene that has none (or cannot be read);
  at filing the same strict read is repeated inside the hold that covers the
  write, and a different answer files nothing. A recycled scene id never
  inherits a decision, and no identity is ever minted for a capture. A
  campaign-level scope (`NO_SCENE`: the continuity sweep) has no identity to
  read, so it is fenced by the site's own `fence`, asked in that same hold;
  its entries are listed by `GET /campaigns/{cid}/prompts`.

  The residual, stated: between a campaign's store delete and
  `runs.forget_subject` (the window `routes/runs.py` documents as partial),
  only the filer's campaign-exists check covers a sweep's capture, so a
  same-named campaign recreated inside that window is the one path left to a
  misfiled campaign-level entry.
- **Never raises, never waits.** A contended lock, a gone scene, an I/O or
  serialisation error, a failed fence: each costs the capture alone, and
  writes one `warning` line naming the task and the exception type -- never
  the capture's content.
- **No provider text.** A failed call is kept as its error kind, HTTP status
  and code (`native_unrepresentable`), never the `detail`, which is the
  provider's own words and can echo the input or a key.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from starlette.concurrency import run_in_threadpool

from .. import content_parts, decisions, store, wire
from .. import inference as operations
from ..llm_errors import LLMError
from . import common

log = logging.getLogger(__name__)

#: The `sid` of a campaign-level scope (`store.prompt_log.NO_SCENE`): a
#: decision no scene made, which the site fences itself.
NO_SCENE = store.prompt_log.NO_SCENE

#: The prompt-log section a decision's outcome is filed under. The frontend
#: draws a section with this id as the outcome, "not sent", rather than as a
#: prompt section (`frontend/src/components/ContextBreakdown.tsx`,
#: `OUTCOME_ID`): change both together, which `test_character_turns` pins.
OUTCOME_SECTION_ID = "decision"

#: How many calls of one scope keep their message sections. A later call
#: keeps its outcome only, counted in the envelope's `elided_calls`: every
#: call of one `decide()` renders the same template, so the first ones show
#: the prompt's shape, and the outcome keeps every item's answers and batch
#: positions. Structural, to be tuned against real prompts later.
MAX_CALLS_IN_FULL = 4

#: The longest the outcome section's JSON text may be. Past it whole call
#: records are dropped from the end, never cut mid-object, and `truncated`
#: is set. Native distributions (up to `decisions.NATIVE_MAX_OPTIONS`
#: options per question) and rationales are what grow it.
MAX_OUTCOME_CHARS = 64_000


def message_row(row_id: str, label: str, content: Any) -> dict:
    """One sent message as a breakdown section: its text, charged what the
    packer charges -- each image reference (#377) at `IMAGE_TOKENS` -- and
    never droppable, since it was sent whole."""
    text = content_parts.text_of(content)
    return {"id": row_id, "label": label, "text": text, "tier": "lock-in",
            "dropped": False, "pinned": False, "trimmed": 0,
            "tokens": (store.tokens.count_tokens(text)
                       + store.context.pack.IMAGE_TOKENS
                       * len(content_parts.image_refs(content)))}


def outcome_row(text: str) -> dict:
    """The outcome section: last, never sent, so it costs no tokens."""
    return {"id": OUTCOME_SECTION_ID, "label": "decision", "text": text,
            "tier": "lock-in", "dropped": False, "pinned": False, "trimmed": 0,
            "tokens": 0}


def is_decision(entry: dict) -> bool:
    """Whether a prompt-log row or entry is a decision's capture: one filed in
    the decision pool, or a speaker pick captured before there was one (the
    frontend's `isDecision` asks the same)."""
    return (entry.get("operation") == store.prompt_log.DECIDE
            or entry.get("task") == "response-selector")


@dataclass
class _Held:
    """One settled call, as the hook keeps it. `messages` is None once the
    scope holds `MAX_CALLS_IN_FULL` calls: dropped at append time, so a long
    sweep keeps only its outcomes in memory."""

    part: str
    messages: list[dict] | None
    record: dict
    target: wire.Target


def _record_of(part: str, outcome: dict, error: LLMError | None) -> dict:
    """A call's record in the envelope: `decisions.outcome` (with `stage` and
    `at`) and the scope's `part`, a failure's text replaced by its kind,
    status and code."""
    record = {"part": part, **outcome}
    text = record.pop("error", None)
    if text is None and error is None:
        return record
    kind = error.kind if error is not None else str(text).split(":", 1)[0]
    record["error_kind"] = kind
    record["error_status"] = error.status if error is not None else None
    if error is not None and error.code:
        record["code"] = error.code
    return record


class _Hook(operations.Recorder):
    """What `Scope.hook` hands `decide`: appends, does no I/O."""

    def __init__(self, scope: Scope, part: str) -> None:
        self.scope = scope
        self.part = part

    async def settled(self, messages: list[dict], outcome: dict, target: wire.Target,
                      error: LLMError | None) -> None:
        self.scope._held(self.part, messages, outcome, target, error)

    def wants_messages(self) -> bool:
        return self.scope._room()


@dataclass
class Scope:
    """One decision's capture. Only `hook` and `note` are a site's to call."""

    task: str
    live: bool
    calls: list[_Held] = field(default_factory=list)
    notes: dict[str, dict[str, Any]] = field(default_factory=dict)
    elided: int = 0
    invalid: str = ""

    def hook(self, part: str = "") -> operations.Capture | None:
        """The `capture=` for one `decide()` call, or None when this scope
        captures nothing (capture off, or a scene with no identity). `part`
        names a sub-decision within the scope by an opaque id -- voice
        drift's anchored NPC record id, never a display name -- and is ""
        elsewhere."""
        return _Hook(self, part) if self.live else None

    def note(self, part: str, key: str, value: Any) -> None:
        """Record what the site decided after `decide()` returned, which no
        per-call hook sees (a sampled draw, a legal set's size). Filed in the
        outcome's `notes` as `{part: {key: value}}`, counted against
        `MAX_OUTCOME_CHARS`. No I/O; nothing is kept when the scope is off."""
        if self.live:
            self.notes.setdefault(part, {})[key] = value

    def _room(self) -> bool:
        """Whether another call's messages would be kept: only calls that sent
        something take one of the `MAX_CALLS_IN_FULL` places, so a run of
        calls refused unsent never crowds out the prompt that did go out."""
        return sum(1 for held in self.calls if held.messages) < MAX_CALLS_IN_FULL

    def _held(self, part: str, messages: list[dict], outcome: dict,
              target: wire.Target, error: LLMError | None) -> None:
        keep = self._room()
        if messages and not keep:
            self.elided += 1
        self.calls.append(_Held(part, list(messages) if keep else None,
                                _record_of(part, outcome, error), target))


def _envelope(scope: Scope, records: list[dict], truncated: bool) -> dict:
    envelope: dict[str, Any] = {"task": scope.task, "calls": records,
                                "notes": scope.notes, "elided_calls": scope.elided,
                                "truncated": truncated}
    if scope.invalid:
        envelope["error"] = f"invalid_request: {scope.invalid}"
    return envelope


def _outcome_text(scope: Scope) -> str:
    """The envelope as the outcome section's text, within `MAX_OUTCOME_CHARS`
    by dropping whole call records from the end (then the notes, should they
    alone overrun it)."""
    records = [held.record for held in scope.calls]

    def spelled(records: list[dict], truncated: bool) -> str:
        return json.dumps(_envelope(scope, records, truncated), indent=2,
                          ensure_ascii=False)

    text = spelled(records, False)
    while len(text) > MAX_OUTCOME_CHARS and records:
        records = records[:-1]
        text = spelled(records, True)
    if len(text) > MAX_OUTCOME_CHARS:
        scope.notes = {}
        text = spelled(records, True)
    return text


def _breakdown(scope: Scope) -> dict:
    """The entry: a `context_breakdown`-shaped payload `ContextBreakdown`
    renders unchanged -- each kept call's messages, then the outcome. A
    scope of one call keeps the speaker's `message_{i}` ids; several are
    `c{k}_message_{i}`, labelled by call. `total_tokens` sums the sections
    kept in full, so with `elided_calls` it is a floor."""
    rows: list[dict] = []
    single = len(scope.calls) == 1
    for k, held in enumerate(scope.calls):
        for i, message in enumerate(held.messages or ()):
            role = message.get("role", "")
            rows.append(message_row(f"message_{i}", role, message.get("content", ""))
                        if single else
                        message_row(f"c{k}_message_{i}", f"call {k + 1} · {role}",
                                    message.get("content", "")))
    total = sum(row["tokens"] for row in rows)
    rows.append(outcome_row(_outcome_text(scope)))
    return {"sections": rows, "total_tokens": total, "dropped_tokens": 0,
            "budget_tokens": store.context.budget_tokens()}


def _named(scope: Scope) -> wire.Target | None:
    """The target the index row names: the first call that answered, else
    the first stage-0 call's (its stage's primary), else the first call's."""
    for held in scope.calls:
        if "error_kind" not in held.record:
            return held.target
    for held in scope.calls:
        if held.record.get("stage") == 0:
            return held.target
    return scope.calls[0].target if scope.calls else None


def _sampled(scope: Scope) -> wire.Target | None:
    """The target whose sampler report the entry carries: the first call
    that went out, and none when it was native -- a native call is sent no
    preset."""
    for held in scope.calls:
        if held.messages:
            native = held.record.get("mode") == decisions.NATIVE_BACKEND
            return None if native else held.target
    return None


def _file(cid: str, sid: str, scope: Scope, identity: str | None,
          fence: Callable[[], bool] | None) -> None:
    """File the scope's entry, in a worker thread. Never raises."""
    def holds() -> bool:
        # Inside the hold `_record_prompt` takes: the same scene, and the
        # site's own fence. A raise is a failed fence.
        try:
            if identity is not None and (
                    store.scenes.scene_identity_strict(cid, sid) != identity):
                return False
            return fence is None or bool(fence())
        except Exception:  # noqa: BLE001 - a fence that cannot be read does not hold
            return False

    try:
        named = _named(scope)
        model = named.model if named is not None else ""
        kind = named.kind if named is not None else ""
        if sid == NO_SCENE:
            common._record_campaign_prompt(cid, scope.task, _breakdown(scope), model=model,
                                           kind=kind, conn=_sampled(scope),
                                           operation=store.prompt_log.DECIDE, fence=holds)
        else:
            common._record_prompt(cid, sid, scope.task, _breakdown(scope), model=model,
                                  kind=kind, conn=_sampled(scope),
                                  operation=store.prompt_log.DECIDE, fence=holds)
    except Exception as exc:  # noqa: BLE001 - a capture costs itself, never the decision
        log.warning("could not capture a %s decision: %s", scope.task, type(exc).__name__)


def _opening(cid: str, sid: str) -> tuple[bool, str | None]:
    """`(live, identity)` on entry: whether this scope captures at all, and
    the scene identity its filing must still find."""
    if not store.prompt_log.capturing():
        return False, None
    if sid == NO_SCENE:
        return True, None
    try:
        identity = store.scenes.scene_identity_strict(cid, sid)
    except Exception:  # noqa: BLE001 - unreadable: capture nothing, cost nothing
        return False, None
    return identity is not None, identity


@contextlib.asynccontextmanager
async def capturing(cid: str, sid: str, task: str, *,
                    fence: Callable[[], bool] | None = None,
                    abandoned: Callable[[], Awaitable[bool]] | None = None,
                    ) -> AsyncIterator[Scope]:
    """One decision's capture scope (module docstring). `fence` is asked
    inside the hold that covers the write, beside the scene identity, and
    `abandoned` once on exit; either answering against the scope files
    nothing."""
    try:
        live, identity = await run_in_threadpool(_opening, cid, sid)
    except Exception as exc:  # noqa: BLE001 - see `_file`
        log.warning("could not capture a %s decision: %s", task, type(exc).__name__)
        live, identity = False, None
    if live and sid == NO_SCENE and fence is None:
        # A campaign-level scope has no identity to fence on, so it must bring
        # its own (01b-C1). One that does not captures nothing, rather than
        # leaving the campaign-exists check as its only guard -- the residual
        # accepted for the sweep, and for nothing else.
        log.warning("could not capture a %s decision: %s", task, "NoFence")
        live = False
    scope = Scope(task, live)
    try:
        yield scope
    except decisions.DecideRequestError as exc:
        # Raised before any call: our own sentence, never a provider's.
        scope.invalid = str(exc)
        await _close(cid, sid, scope, identity, fence, abandoned)
        raise
    except LLMError:
        await _close(cid, sid, scope, identity, fence, abandoned)
        raise
    await _close(cid, sid, scope, identity, fence, abandoned)


async def _close(cid: str, sid: str, scope: Scope, identity: str | None,
                 fence: Callable[[], bool] | None,
                 abandoned: Callable[[], Awaitable[bool]] | None) -> None:
    """File the scope once it has settled, unless nothing is to be filed."""
    if not scope.live or not (scope.calls or scope.invalid):
        return
    try:
        if abandoned is not None and await abandoned():
            return
    except Exception as exc:  # noqa: BLE001 - see `_file`
        log.warning("could not capture a %s decision: %s", scope.task, type(exc).__name__)
        return
    await run_in_threadpool(_file, cid, sid, scope, identity, fence)
