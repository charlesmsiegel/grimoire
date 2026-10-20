"""Known capability gaps, native/structured decision policy and the one target account/structured stamping primitive."""

from __future__ import annotations

import dataclasses

from ... import routing
from .. import capabilities
from ..resolved import Attempt, ResolvedInference
from . import _contracts


def _needs(route: routing.Route | None, operation: str) -> tuple[frozenset[str], ...]:
    """What an attempt must be able to do for this route and operation, as
    groups of alternatives: the operation's own (`OPERATION_CAPABILITY`) as
    one group, and each of the route's `requires` as a group of its own."""
    own = frozenset(_contracts.OPERATION_CAPABILITY.get(operation, ("generate",)))
    return (own, *(frozenset({cap}) for cap in (route.requires if route is not None else ())))


def _known_no(found: capabilities.Cap | None) -> bool:
    """Whether `found` is a `no` that is knowledge rather than a guess."""
    return (found is not None and found.value == capabilities.NO
            and found.source not in _contracts._GUESSES)


def _missing(attempt: Attempt, needs: tuple[frozenset[str], ...]) -> tuple[str, ...]:
    """Every member of every group in `needs` whose members `attempt` is ALL
    known (`no`) not to have, in `capabilities.NAMES` order -- a group with
    one member it may have is met. `unknown` is never missing, and neither is
    a guess (`_GUESSES`)."""
    caps = attempt.capabilities
    unmet = {cap for group in needs if all(_known_no(caps.get(c)) for c in group)
             for cap in group}
    return tuple(cap for cap in capabilities.NAMES if cap in unmet)


def known_lacks(resolved: ResolvedInference, cap: str, *, fallback: bool = False) -> bool:
    """Whether `resolved`'s primary -- or, with `fallback`, its fallback
    attempt -- is KNOWN (`no`, never a guess) unable to do `cap`: the seam's
    own `_missing` test, for a caller that may offer a capability its route
    does not require (01g's `routing.TOOLS_OPTIONAL`), which the resolver's
    `fallback_missing` therefore never names. A resolution of nothing (or
    with no fallback) lacks nothing it could be asked for."""
    index = 1 if fallback else 0
    if len(resolved.attempts) <= index:
        return False
    return cap in _missing(resolved.attempts[index], (frozenset({cap}),))


def generates(attempt: Attempt) -> bool:
    """Whether `attempt` can generate: `generate` is not among what it is
    KNOWN (`_missing`) not to do, so `unknown` and a name-rule guess both
    still generate. What `decision_mode`, the same-provider rule (`_apart`)
    and the decide chain (`inference.stages`) ask."""
    return not _missing(attempt, (frozenset({"generate"}),))


def decides_natively(attempt: Attempt) -> bool:
    """Whether `attempt` may decide natively: `decide_native` is not among
    what it is KNOWN (`_missing`) not to do, so `unknown` and a name-rule
    guess both still may (spec 5.3). `generates`' twin, asked where a native
    decision is forced on an attempt (`evals/runner.chain`)."""
    return not _missing(attempt, (frozenset({"decide_native"}),))


def native_only(caps: dict[str, capabilities.Cap]) -> bool:
    """Whether a model with these capabilities is answered natively: it is
    known (`no`, from a source other than the name rule) unable to
    `generate`, and not known unable to `decide_native` -- `unknown` is
    allowed (spec 5.3). The one rule the resolver and the controls preview
    both ask, so the two never disagree."""
    return _known_no(caps.get("generate")) and not _known_no(caps.get("decide_native"))


def native_capable(attempt: Attempt) -> bool:
    """Whether `attempt` may be asked natively FIRST (spec 01c §3.2, §4.2):
    its `decide_native` is a known `yes` -- never `unknown`, which
    `native_only` allows because a native-only model has no other way to
    answer, and a native-first one does. That a kind with no decisions
    endpoint never reads `yes` is the presets' business: each lists
    `decide_native` in `never`, an adapter-source `no` no override lifts
    (`capabilities.resolve_caps`), and a test holds every preset to it -- so
    the store needs no gateway import (`adapters`: the store never imports
    the gateway). Production only: `evals/runner.chain` keeps
    `decides_natively`, so evidence can be gathered on an `unknown`."""
    found = attempt.capabilities.get("decide_native")
    return found is not None and found.value == capabilities.YES


def native_first(resolved: ResolvedInference) -> bool:
    """Whether `resolved`'s decide chain asks its primary's decisions endpoint
    before its structured stage (01c-C1): a decide resolution whose primary
    is `structured`, whose adapter kind its task's code policy lists
    (`routing.policy(resolved.task).native_first`), and which is
    `native_capable`. The one rule `inference.stages` (and a later settings
    readout) asks. Pure: reads only the resolution and the policy."""
    if resolved.operation != "decide" or not resolved.attempts:
        return False
    primary = resolved.attempts[0]
    return (primary.decision_mode == "structured"
            and primary.target.kind in routing.policy(resolved.task).native_first
            and native_capable(primary))


def decision_mode(attempt: Attempt) -> str:
    """The backend that would answer `attempt` on a decide resolution (ruling
    1, C1): "native" (its provider's decisions endpoint) when it is
    `native_only`; else "structured" (`generate(schema=)` and the parser)
    when it `generates`, whatever its `decide_native` says -- a model that
    can generate stays structured; a task whose policy lists its kind in
    `native_first` asks its decisions endpoint first, which is a stage of
    `inference.stages` (`native_first`), not a mode; else "", for an attempt
    that can do neither."""
    if native_only(attempt.capabilities):
        return "native"
    return "structured" if generates(attempt) else ""


def structured_capable(attempt: Attempt) -> bool:
    """Whether `attempt` is asked for its provider's structured mode when a
    call sends a schema: its `structured_output` is `yes`. A `no` or an
    `unknown` is not -- a strict endpoint answers the envelope it does not
    know with a 400, and the user's lever is the facts override (spec 01f
    3.2). The one rule a decide resolution's flags (`_flag_structured`) and a
    generate call's per-call chain (`inference._structured_chain`) both ask."""
    found = attempt.capabilities.get("structured_output")
    return found is not None and found.value == capabilities.YES


def _flag_structured(attempts: list[Attempt], operation: str) -> list[Attempt]:
    """Flag each attempt that is `structured_capable` (spec 7.2) on its
    target (`_stamp`). A decide resolution only: a generate resolution's
    targets are what they were before slice F (plan Minor 4) -- a generation
    that sends a schema flags its own per-call targets instead (01f 3.2)."""
    if operation != "decide":
        return attempts
    return [_stamp(attempt, structured=True) if structured_capable(attempt) else attempt
            for attempt in attempts]


def _stamp(attempt: Attempt, *, account: dict | None = None,
           structured: bool = False) -> Attempt:
    """`attempt` with `account` laid over its target's account and, when
    `structured`, its target flagged for its provider's structured mode --
    the one place a resolution writes either. The target is frozen, so the
    attempt is replaced around a new one."""
    target = attempt.target
    if account:
        target = target.with_account(**account)
    if structured:
        target = dataclasses.replace(target, structured=True)
    return dataclasses.replace(attempt, target=target)
