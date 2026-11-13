"""Independent, authorized CRITIQUE execution for 047. No narrator result carries its own review."""
from __future__ import annotations

from collections.abc import Sequence

from grimoire.core.inference import InferenceStage, PlannedCall
from grimoire.providers.adapter import Approvals, ProviderAdapter
from grimoire.providers.cancel import CancelToken
from grimoire.providers.config import RouteChoice
from grimoire.providers.meter import TurnMeter
from grimoire.providers.request import ChatMessage, ModelRequest
from grimoire.runtime.label import LabelReply, decode_reply
from grimoire.security import WorkerHandle

DEFAULT_EXTRACTION_TOKENS = 4096


class ExtractionRuntime:
    """An independent worker with fixed trusted routing, no grant/apply or narrator authority.

    EXECUTE authorization covers each attempt's exact bytes. The owning runner separately authorizes
    PROPOSE on its library handle before any parsed labels or reports enter a persisted proposal.
    """
    def __init__(self, adapter: ProviderAdapter, routes: Sequence[RouteChoice], handle: WorkerHandle, *,
                 approvals: Approvals, max_output_tokens: int = DEFAULT_EXTRACTION_TOKENS) -> None:
        routes = tuple(routes)
        if not routes or not all(type(route) is RouteChoice for route in routes):
            raise ValueError('routes must be nonempty RouteChoice values')
        if type(handle) is not WorkerHandle or handle.trusted is not False:
            raise ValueError('extraction requires an untrusted handle')
        if not callable(approvals):
            raise ValueError('extraction requires per-attempt approval')
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 32768:
            raise ValueError('max_output_tokens must be 1..32768')
        self._adapter, self._routes, self._handle = adapter, routes, handle
        self._approvals, self._max_output_tokens = approvals, max_output_tokens

    def extract(self, meter: TurnMeter, call: PlannedCall, messages: Sequence[tuple[str, str]], *,
                cancel: CancelToken | None = None) -> LabelReply:
        if type(meter) is not TurnMeter or type(call) is not PlannedCall or call.stage is not InferenceStage.CRITIQUE:
            raise ValueError('extraction needs its metered critique call')
        request = ModelRequest(tuple(ChatMessage(role, text) for role, text in messages),
                               max_output_tokens=self._max_output_tokens, temperature=0.0)
        result = self._adapter.execute(meter, call, request, self._routes, self._handle,
                                       approvals=self._approvals, cancel=cancel)
        if result.status != 'accepted':
            reason = (result.refusal_reason if result.status == 'refused'
                      else result.finish_reason if result.status == 'incomplete'
                      else result.error.kind if result.error is not None else None)
            return LabelReply(result.status, reason=reason)
        try:
            return LabelReply('decoded', decode_reply(result.reply or ''))
        except ValueError:
            return LabelReply('unparseable', reason='unparseable')
