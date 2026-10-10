"""Decision capture (roadmap 01b): one prompt-log entry per decision scope.

A **scope** is what a decide site wraps -- one speaker pick, one scene-break
check, one voice-drift phase (every anchored NPC of one review), one identity
check, one reconcile pass -- and `capturing` opens it:

    async with decision_capture.capturing(cid, sid, "scene-break") as scope:
        decision = await operations.decide(..., capture=scope.hook())

Inside it, the per-call hook (`Scope.hook`) only appends what `decide`
hands each settled call (`inference.Capture`) to memory; `Scope.note` adds
what the caller decides afterwards. When the scope exits, it files ONE entry
-- the requests as sent, every call's record, the notes -- in a worker
thread, through `common._record_prompt`, under its non-blocking campaign
lock, into the decision retention pool (`prompt_log.DECIDE`). So a chunked
check is one entry rather than one per chunk, and a fallback stage's answer
sits beside the primary's failure it followed.

Four rules, each costing the capture and never the decision:

- **Off when capture is off.** `prompt_log.capturing()` is read once, off
  the loop, on entry; off, `hook()` is None, so `decide` builds no native
  request body, and nothing is filed.
- **Filed on exit**: on a normal exit, and on an `LLMError` (a failed
  decision is one worth having; `BudgetRefused` is one) or a
  `decisions.DecideRequestError` (raised before any call: an entry with no
  call and the request's own sentence). Never on cancellation, `Abandoned`
  or any other exception, nor when the site's `abandoned` check answers True.
  The exception is re-raised unchanged.
- **Fenced** on the scene's strict identity (`scene_identity_strict`), read
  on entry and again inside the hold that covers the write, plus the site's
  own `fence`: a scene deleted and recreated under the same id never inherits
  a decision, and an identity-less scene captures nothing (no identity is
  minted: a debug capture never writes a scene file). A campaign-level scope
  (`sid=NO_SCENE`: the reconcile sweep, which has no scene) has no identity
  to read, so it must pass the site's `fence`, and is filed through
  `common._record_campaign_prompt`, which proves the campaign exists.
- **Never raises, never waits.** A contended lock, a gone scene, an
  `OSError`, a serialisation error or a failed fence costs the capture; a
  failure is one `warning` naming the task and the exception type, with no
  capture content.

**No provider text is kept.** A failed call's record has its `error`
(`"kind: detail"`, the detail being the provider's own words, which can echo
the input or a key) replaced by `error_kind`, `error_status` and `code`, read
off the `LLMError` that rides beside it (`inference.Outcome.failure`).
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from starlette.concurrency import run_in_threadpool

from .. import content_parts, decisions, inference, store, wire
from ..llm_errors import LLMError
from . import common

log = logging.getLogger(__name__)

#: The prompt-log section a decision's outcome is filed under. The frontend
#: draws a section with this id as the outcome, "not sent", rather than as a
#: prompt section (`frontend/src/components/ContextBreakdown.tsx`,
#: `OUTCOME_ID`): change both together, which `test_character_turns` pins.
OUTCOME_SECTION_ID = "decision"

#: The `operation` every decision capture is filed under.
DECIDE = store.prompt_log.DECIDE

#: The `sid` of a campaign-level scope (the reconcile sweep): filed with no
#: scene, through `common._record_campaign_prompt`, and fenced on the site's
#: own `fence`, which such a scope must pass.
NO_SCENE = store.prompt_log.NO_SCENE

#: What a campaign-level scope is "fenced on" in place of a scene identity:
#: there is nothing to read, so it only says the scope is open.
_CAMPAIGN = "campaign"

#: The speaker pick's task: its entries filed before 01b carry no
#: `operation`, and are decisions all the same (`is_decision`).
SPEAKER_TASK = "response-selector"

#: How many of a scope's calls keep their message sections. Every call of one
#: `decide()` renders the same template, so the first calls show the prompt's
#: shape; later chunks differ only in their items, and their records keep
#: every item's answers and batch index. Structural, to be tuned against real
#: prompts later.
MAX_CALLS_IN_FULL = 4

#: The outcome section's JSON text, at most: past it whole call records are
#: dropped from the end and `truncated` set. Native distributions (up to 255
#: options a question) and rationales are what grow it. Structural, to be
#: tuned against real prompts later.
MAX_OUTCOME_CHARS = 64_000


def is_decision(row: dict) -> bool:
    """Whether a prompt-log row or entry is a decision capture: filed under
    `DECIDE`, or a speaker pick filed before decisions had an operation. The
    frontend's `isDecision` asks the same."""
    return row.get("operation") == DECIDE or row.get("task") == SPEAKER_TASK


def message_sections(messages: list[dict], *, call: int | None = None) -> list[dict]:
    """`messages` as prompt-log sections, counted. One call's are
    `message_{i}`, labelled by role; with `call` (a scope of several calls)
    they are `c{call}_message_{i}`, labelled `call {call+1} · {role}`. A
    message may hold image references (#377): the record keeps the text and
    charges each reference what the packer does."""
    prefix = "" if call is None else f"c{call}_"
    rows = []
    for i, m in enumerate(messages):
        text = content_parts.text_of(m["content"])
        rows.append({
            "id": f"{prefix}message_{i}",
            "label": m["role"] if call is None else f"call {call + 1} · {m['role']}",
            "text": text, "tier": "lock-in", "dropped": False, "pinned": False,
            "trimmed": 0,
            "tokens": (store.tokens.count_tokens(text)
                       + store.context.pack.IMAGE_TOKENS
                       * len(content_parts.image_refs(m["content"])))})
    return rows


def _sanitised(outcome: dict) -> dict:
    """A call's record without provider text: a failed call's `error`
    replaced by its kind, status and -- where the error has one -- code."""
    record = dict(outcome)
    text = record.pop("error", None)
    if text is None:
        return record
    failure = getattr(outcome, "failure", None)
    if isinstance(failure, LLMError):
        kind, status, code = failure.kind, failure.status, failure.code
    else:
        # A capture handed a plain record: the kind is what precedes the
        # detail, and nothing else of it is kept.
        kind, status, code = str(text).split(": ", 1)[0], None, None
    record.update(error_kind=kind, error_status=status)
    if code:
        record["code"] = code
    return record


@dataclass
class _Held:
    """One settled call, as the scope keeps it: its record (with `part`),
    what it was sent on, and its messages -- None once `MAX_CALLS_IN_FULL`
    calls are held (`elided`), so a later call's prompt is never kept."""

    record: dict
    target: wire.Target
    messages: list[dict] | None
    sent: bool
    elided: bool = False


@dataclass
class Scope:
    """What a decide site captures into (`capturing`)."""

    task: str
    on: bool
    calls: list[_Held] = field(default_factory=list)
    notes: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: A refused request's own sentence (`DecideRequestError`), else "".
    error: str = ""

    def hook(self, part: str = "") -> inference.Capture | None:
        """The `capture=` to hand `decide`, or None when this scope captures
        nothing. `part` is an opaque id naming a sub-decision (voice drift's
        anchored NPC's record id, never a display name); "" elsewhere."""
        if not self.on:
            return None

        async def capture(messages: list[dict], outcome: dict, target: wire.Target) -> None:
            self._take(part, messages, outcome, target)
        return capture

    def note(self, part: str, key: str, value: Any) -> None:
        """Record something the caller decided AFTER `decide()` returned,
        which no per-call hook can see, under `notes[part][key]`. No I/O; it
        counts against `MAX_OUTCOME_CHARS`."""
        if self.on:
            self.notes.setdefault(part, {})[key] = value

    def _take(self, part: str, messages: list[dict], outcome: dict,
              target: wire.Target) -> None:
        full = len(self.calls) < MAX_CALLS_IN_FULL
        self.calls.append(_Held(
            record={"part": part, **_sanitised(outcome)}, target=target,
            messages=list(messages) if full else None, sent=bool(messages),
            elided=bool(messages) and not full))


def _envelope(scope: Scope) -> str:
    """The outcome section's text (01b §3.4): every call's record, the notes,
    how many calls' messages were elided, and -- past `MAX_OUTCOME_CHARS` --
    whole records dropped from the end with `truncated` set."""
    body: dict[str, Any] = {
        "task": scope.task, "calls": [held.record for held in scope.calls],
        "notes": scope.notes, "elided_calls": sum(held.elided for held in scope.calls),
        "truncated": False}
    if scope.error:
        body["error"] = scope.error

    def dump() -> str:
        return json.dumps(body, indent=2, ensure_ascii=False)

    text = dump()
    while len(text) > MAX_OUTCOME_CHARS and body["calls"]:
        body["calls"] = body["calls"][:-1]
        body["truncated"] = True
        text = dump()
    if len(text) > MAX_OUTCOME_CHARS and body["notes"]:
        # The notes alone are past the cap: dropped whole too, never cut.
        body["notes"] = {}
        text = dump()
    return text


def _breakdown(scope: Scope) -> dict:
    """The entry: a `context_breakdown`-shaped payload, each call's messages
    in settle order (ids `message_{i}` for a one-call scope, else
    `c{k}_message_{i}`), then the outcome section, zero tokens and last.
    `total_tokens` counts the messages kept in full only."""
    single = len(scope.calls) == 1
    rows: list[dict] = []
    for k, held in enumerate(scope.calls):
        if held.messages:
            rows.extend(message_sections(held.messages, call=None if single else k))
    total = sum(row["tokens"] for row in rows)
    rows.append({"id": OUTCOME_SECTION_ID, "label": "decision", "text": _envelope(scope),
                 "tier": "lock-in", "dropped": False, "pinned": False, "trimmed": 0,
                 "tokens": 0})
    return {"sections": rows, "total_tokens": total, "dropped_tokens": 0,
            "budget_tokens": store.context.budget_tokens()}


def _file(scope: Scope, cid: str, sid: str, still: Callable[[], bool]) -> None:
    """Build and file the entry (a worker thread). The index row's model is
    the first call that answered, else the first call's; the sampler report
    is the first call that went out's, and none when it was native."""
    answered = next((h for h in scope.calls if "error_kind" not in h.record), None)
    named = answered or (scope.calls[0] if scope.calls else None)
    out = next((h for h in scope.calls if h.sent), None)
    conn = (out.target if out is not None
            and out.record.get("mode") != decisions.NATIVE_BACKEND else None)
    model = named.target.model if named else ""
    kind = named.target.kind if named else ""
    if sid == NO_SCENE:
        common._record_campaign_prompt(cid, scope.task, _breakdown(scope), model=model,
                                       kind=kind, conn=conn, operation=DECIDE, still=still)
        return
    common._record_prompt(cid, sid, scope.task, _breakdown(scope), model=model, kind=kind,
                          conn=conn, operation=DECIDE, still=still)


def _opening(cid: str, sid: str, task: str) -> str | None:
    """The scene's identity when this scope should capture, else None:
    capture off, a scene that predates identities or is gone, or a read that
    raised (that one is the warning). A campaign-level scope has no identity
    to read, and answers `_CAMPAIGN` while capture is on."""
    try:
        if not store.prompt_log.capturing():
            return None
        if sid == NO_SCENE:
            return _CAMPAIGN
        return store.scenes.scene_identity_strict(cid, sid)
    except Exception as exc:  # noqa: BLE001 - a debug capture never fails a decision
        log.warning("could not capture a %s decision: %s", task, type(exc).__name__)
        return None


async def _close(scope: Scope, cid: str, sid: str, identity: str | None,
                 fence: Callable[[], bool] | None,
                 abandoned: Callable[[], Awaitable[bool]] | None) -> None:
    """File the scope's one entry, or nothing; never raises an `Exception`."""
    if identity is None or not scope.on:
        return
    if not scope.calls and not scope.notes and not scope.error:
        return      # nothing was asked: no entry to tell
    try:
        if abandoned is not None and await abandoned():
            return

        def still() -> bool:
            if fence is not None and not fence():
                return False
            return (sid == NO_SCENE
                    or store.scenes.scene_identity_strict(cid, sid) == identity)

        await run_in_threadpool(_file, scope, cid, sid, still)
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        log.warning("could not capture a %s decision: %s", scope.task, type(exc).__name__)


@contextlib.asynccontextmanager
async def capturing(cid: str, sid: str, task: str, *,
                    fence: Callable[[], bool] | None = None,
                    abandoned: Callable[[], Awaitable[bool]] | None = None,
                    ) -> AsyncIterator[Scope]:
    """Open one decision scope over scene `sid` of campaign `cid` -- or over
    the campaign itself, `sid=NO_SCENE` -- filed under `task` (see the module
    docstring). `fence` is asked inside the hold that covers the write, and a
    False answer drops the capture; a campaign-level scope has nothing else
    to be fenced on, so it must pass one (`ValueError`, before anything is
    read). `abandoned` is the review's own check, asked before filing, for a
    site whose loop swallows `Abandoned` (voice drift)."""
    if sid == NO_SCENE and fence is None:
        raise ValueError(f"a campaign-level {task} capture needs its site's fence")
    identity = await run_in_threadpool(_opening, cid, sid, task)
    scope = Scope(task, identity is not None)
    try:
        yield scope
    except decisions.DecideRequestError as exc:
        scope.error = f"invalid_request: {exc}"
        await _close(scope, cid, sid, identity, fence, abandoned)
        raise
    except LLMError:
        await _close(scope, cid, sid, identity, fence, abandoned)
        raise
    await _close(scope, cid, sid, identity, fence, abandoned)
