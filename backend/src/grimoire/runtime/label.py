"""Issue a turn's planned ``labelling`` call on an untrusted handle and decode its reply (035 milestone 2).

The labeller's reply is model output that the runner reads into proposed 029 labels, so, unlike 027's
generation runtime, this runtime refuses a trusted handle (the requester's decision in the Wave 6 audit:
the labeller gets its own untrusted handle). Every attempt therefore needs an approval, which the app's
``approvals`` callback issues only for the human press that asked for the labels. Routing comes from
trusted configuration, never from the reply. The reply is only decoded here (JSON, bounded); what it may
say is ``grimoire.labelling``'s to judge.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from grimoire.core.inference import InferenceStage, PlannedCall
from grimoire.providers.adapter import Approvals, ProviderAdapter
from grimoire.providers.cancel import CancelToken
from grimoire.providers.config import RouteChoice
from grimoire.providers.meter import TurnMeter
from grimoire.providers.request import ChatMessage, ModelRequest
from grimoire.security import WorkerHandle

#: Room for up to 32 cited facts and 16 short stakes as JSON.
DEFAULT_LABEL_TOKENS = 1200
#: The most reply text decoded at all: a longer reply is refused before parsing.
MAX_REPLY_CHARS = 24_000
_FENCED = re.compile(r"```(?:json)?\s*\n(?P<body>.*?)\n?```", re.DOTALL)

LabelStatus = Literal["decoded", "unparseable", "incomplete", "failed", "cancelled", "refused"]


@dataclass(frozen=True, slots=True)
class LabelReply:
    """How the call ended. ``decoded`` is the reply's JSON value, set only when ``status == "decoded"``."""

    status: LabelStatus
    decoded: object = field(default=None, repr=False)
    reason: str | None = None


class _Duplicate(ValueError):
    pass


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    found: dict[str, object] = {}
    for key, value in pairs:
        if key in found:
            raise _Duplicate(key)
        found[key] = value
    return found


def _no_constant(name: str) -> object:
    raise _Duplicate(name)  # NaN and Infinity are not JSON a labeller writes


def decode_reply(text: str) -> object:
    """The JSON value of a reply: the whole text, or one fenced ```json block. ``ValueError`` otherwise."""
    if type(text) is not str or len(text) > MAX_REPLY_CHARS:
        raise ValueError("reply too long")
    body = text.strip()
    fenced = _FENCED.fullmatch(body)
    if fenced is not None:
        body = fenced["body"].strip()
    try:
        return json.loads(body, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except (_Duplicate, json.JSONDecodeError, RecursionError):
        raise ValueError("not one JSON value") from None


class LabelRuntime:
    """Fixed routing for ``labelling`` calls, over an untrusted handle the adapter's authorizer issued."""

    def __init__(self, adapter: ProviderAdapter, routes: Sequence[RouteChoice], handle: WorkerHandle, *,
                 approvals: Approvals, max_output_tokens: int = DEFAULT_LABEL_TOKENS) -> None:
        routes = tuple(routes)
        if not routes or not all(isinstance(route, RouteChoice) for route in routes):
            raise ValueError("routes must be a nonempty sequence of RouteChoice")
        if not isinstance(handle, WorkerHandle) or handle.trusted is not False:
            raise ValueError("the labeller reads model output into proposals: its handle must be untrusted")
        if not callable(approvals):
            raise ValueError("an untrusted handle needs an approval for every attempt")
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or max_output_tokens < 1:
            raise ValueError("max_output_tokens must be an int >= 1")
        self._adapter = adapter
        self._routes = routes
        self._handle = handle
        self._approvals = approvals
        self._max_output_tokens = max_output_tokens

    def label(self, meter: TurnMeter, call: PlannedCall, messages: Sequence[tuple[str, str]], *,
              cancel: CancelToken | None = None) -> LabelReply:
        """Issue one planned ``labelling`` call and decode its reply; nothing is parsed into labels here."""
        if not isinstance(meter, TurnMeter) or not isinstance(call, PlannedCall):
            raise ValueError("label needs the meter and one of its planned calls")
        if call.stage is not InferenceStage.LABELLING:
            raise ValueError(f"{call.call_id} is a {call.stage} call, not labelling")
        request = ModelRequest(tuple(ChatMessage(role, content) for role, content in messages),
                               max_output_tokens=self._max_output_tokens, temperature=0.0)
        result = self._adapter.execute(meter, call, request, self._routes, self._handle,
                                       approvals=self._approvals, cancel=cancel)
        if result.status != "accepted":
            reason = (result.refusal_reason if result.status == "refused"
                      else result.finish_reason if result.status == "incomplete"
                      else result.error.kind if result.error is not None else None)
            return LabelReply(result.status, reason=reason)
        try:
            return LabelReply("decoded", decode_reply(result.reply or ""))
        except ValueError:
            return LabelReply("unparseable", reason="unparseable")


__all__ = ["DEFAULT_LABEL_TOKENS", "LabelReply", "LabelRuntime", "MAX_REPLY_CHARS", "decode_reply"]
