"""Where a tool run's turns are sent, and with what tool choice (spec 3.9, 3.11)."""

from __future__ import annotations

from .. import llm_sampling, wire
from ..store.inference import probes
from . import _requests
from ._contracts import LoopOptions


class _Targets:
    """The chain each turn of one run is sent and the tool choice it is sent
    with: the run's resolution, the fallback alone once it has served
    (`fallen`), and thinking off on a target that inherits tool turns another
    `(provider_id, model)` wrote (`writers`)."""

    def __init__(self, options: LoopOptions):
        self.options = options
        #: Once the fallback has served, it serves alone (spec 3.11 rule 1).
        self.fallen = False
        #: Each `(provider_id, model)` that wrote a tool turn the history holds.
        self.writers: set[tuple[str, str]] = set()

    def chain(self, final: bool) -> tuple[wire.Chain, list[str]]:
        """The turn's chain (spec 3.9, 3.11): the per-turn output cap through
        `call_chain` (with the final schema on a finalize turn), the fallback
        alone once it has served, a fallback known unable to call tools
        never (the resolver drops one only on a route that requires them),
        and thinking off on a target that inherits another model's tool
        turns -- a fallback that would think anyway left out instead."""
        notes: list[str] = []
        resolved = self.options.resolved
        full = _requests.call_chain(resolved,
                                    schema=self.options.final_schema if final else None,
                                    max_tokens=self.options.budget.max_output_tokens)
        if self.fallen and full.fallback is not None:
            full = wire.Chain(full.fallback)
        else:
            full, dropped = _requests._tool_chain(resolved, full)
            if dropped is not None:
                notes.append(f"fallback {dropped.model} cannot call tools: not sent")
        sent = [self._inheriting(t, notes, droppable=n > 0)
                for n, t in enumerate(full.attempts)]
        kept = [t for t in sent if t is not None]
        return wire.Chain(kept[0], kept[1] if len(kept) > 1 else None), notes

    def _inheriting(self, target: wire.Target, notes: list[str], *,
                    droppable: bool) -> wire.Target | None:
        """`target` as sent to a turn whose history may hold tool turns
        another `(provider_id, model)` wrote (spec 3.11 rule 4): with
        thinking off, noted only where off is SENT (`llm_sampling`'s
        reading of the new target). An Anthropic model that thinks when
        nothing is sent (`adaptive_thinking`) and cannot be sent off would be
        refused that history, so as a fallback it is left out (None); a
        target the catalog says nothing of is sent off and noted unconfirmed."""
        if all(w == (target.provider_id, target.model) for w in self.writers):
            return target
        off = target.with_thinking_off()
        state = llm_sampling.effective(off)["controls"].get(
            llm_sampling.REASONING_PARAM.name, {}).get("state")
        if state in (llm_sampling.SUPPORTED, llm_sampling.TRANSLATED):
            notes.append(f"thinking off on {target.model}")
            return off
        thinks = (target.kind == "anthropic" and isinstance(target.model_features, dict)
                  and target.model_features.get("adaptive_thinking") is True)
        if thinks and droppable:
            notes.append(f"fallback {target.model} cannot be sent thinking off: not sent")
            return None
        notes.append(f"thinking off not confirmed on {target.model}")
        return off

    def choice(self, chain: wire.Chain, notes: list[str]) -> str:
        """`required` downgraded to `auto` for a chain holding an attempt that
        would be refused it -- forced tool use beside thinking, implicit
        adaptive thinking included (`probes.tool_choice`'s rule)."""
        choice = self.options.tool_choice
        if choice != "required" or not any(self._auto_only(t) for t in chain.attempts):
            return choice
        notes.append("required sent as auto")
        return "auto"

    def final_choice(self, chain: wire.Chain, notes: list[str]) -> str:
        """The finalize turn's `none` (spec 3.7 step 7), sent as `auto` to a
        chain holding a provider that takes only `auto`
        (`probes.AUTO_ONLY_PRESETS`): the finalize prompt asks for no more
        calls, and any the turn makes are declined, as on every target."""
        if not any(self._preset(t) in probes.AUTO_ONLY_PRESETS for t in chain.attempts):
            return "none"
        notes.append("none sent as auto")
        return "auto"

    def _preset(self, target: wire.Target) -> str:
        return next((a.provider_preset for a in self.options.resolved.attempts
                     if (a.provider_id, a.model) == (target.provider_id, target.model)), "")

    def _auto_only(self, target: wire.Target) -> bool:
        sent = llm_sampling.effective(target)["effective"].get("thinking")
        return probes.tool_choice(self._preset(target), target.kind, target.model_features,
                                  sent, target.model) == "auto"

    def primary(self) -> wire.Target | None:
        """The resolution's primary attempt, or None when it resolved none."""
        chain = self.options.resolved.chain
        return chain.primary if chain is not None else None

    def switched_to(self, target: wire.Target) -> bool:
        primary = self.primary()
        return (not self.fallen and primary is not None
                and (target.provider_id, target.model) != (primary.provider_id, primary.model))

    def wrote(self, target: wire.Target | None) -> None:
        """`target` wrote a tool turn the history now holds."""
        if target is not None:
            self.writers.add((target.provider_id, target.model))
