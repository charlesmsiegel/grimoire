"""Shared identity envelope, with family semantics kept in their own records.

Nothing here is an authority receipt. Callers must authenticate the intended
actor/control assignment and filter knowledge before proposing; later acceptance
must recheck both and commit through the existing guarded effects owner.
"""
from dataclasses import dataclass

from grimoire.core.identity import InstanceRef, SourceRef, validate_segment
from grimoire.rulesets.families import ACTION_TYPES, FamilyAction, FamilyResult, check_result

PROFILES = {'d20': '5e-2024', 'wod': 'v5', 'pbta': 'dungeon-world-1e', 'ars': '5e'}


@dataclass(frozen=True)
class ModuleBinding:
    family: str
    edition: str

    def __post_init__(self) -> None:
        if type(self.family) is not str or type(self.edition) is not str:
            raise ValueError('family and edition must be text')
        if PROFILES.get(self.family) != self.edition:
            raise ValueError('unsupported family/edition profile')


def pinned(ref: object) -> None:
    if type(ref) is not SourceRef or not ref.pinned:
        raise ValueError('module identity must be a pinned SourceRef')


@dataclass(frozen=True)
class RulesetModule:
    ref: SourceRef
    bindings: tuple[ModuleBinding, ...]
    components: tuple[SourceRef, ...] = ()

    def __post_init__(self) -> None:
        pinned(self.ref)
        if type(self.bindings) is not tuple or not 1 <= len(self.bindings) <= 4:
            raise ValueError('module needs one to four explicit profile bindings')
        if any(type(binding) is not ModuleBinding for binding in self.bindings):
            raise ValueError('module bindings must be ModuleBinding values')
        if len({binding.family for binding in self.bindings}) != len(self.bindings):
            raise ValueError('module cannot ambiguously bind a family twice')
        if type(self.components) is not tuple or len(self.components) > 4:
            raise ValueError('component provenance must be a bounded tuple')
        for ref in self.components:
            pinned(ref)
        if len(set(self.components)) != len(self.components) or self.ref in self.components:
            raise ValueError('duplicate or self-referential component')
        if len(self.bindings) > 1 and len(self.components) < 2:
            raise ValueError('a composite needs explicit pinned component provenance')


@dataclass(frozen=True)
class ActionRequest:
    request_id: str
    actor: InstanceRef
    module: RulesetModule
    binding: ModuleBinding
    action: FamilyAction

    def __post_init__(self) -> None:
        if type(self.request_id) is not str:
            raise ValueError('request ID must be text')
        validate_segment(self.request_id, 'request_id')
        if type(self.actor) is not InstanceRef:
            raise ValueError('actor must be a campaign InstanceRef')
        if type(self.module) is not RulesetModule or type(self.binding) is not ModuleBinding:
            raise ValueError('request needs explicit module and binding')
        if self.binding not in self.module.bindings:
            raise ValueError('binding not supported by selected module')
        if type(self.action) is not ACTION_TYPES[self.binding.family]:
            raise ValueError('action does not match family binding')


@dataclass(frozen=True)
class ResolutionProposal:
    """Unaccepted data, intentionally incompatible with core.authority.Resolution."""
    request: ActionRequest
    result: FamilyResult

    def __post_init__(self) -> None:
        if type(self.request) is not ActionRequest:
            raise ValueError('proposal needs a complete request')
        check_result(self.request.action, self.result)


def propose(selected: RulesetModule | None, request: ActionRequest | None,
            result: FamilyResult | None) -> ResolutionProposal | None:
    """Validate a supplied record; no RNG, I/O, hooks, implicit default or acceptance.

    Retries are pure equal values, not durable exactly-once resolution. Sprint075
    owns stored roll identity/recovery; callers cannot treat request_id as a receipt.
    """
    if selected is None and request is None and result is None:
        return None
    if type(selected) is not RulesetModule or type(request) is not ActionRequest:
        raise ValueError('mechanics disabled or invalid module/request')
    if selected != request.module:
        raise ValueError('request differs from exact selected module')
    return ResolutionProposal(request, result)
