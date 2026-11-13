"""Actor-local experiment planning. No provider, writer, or ensemble dependency."""
from dataclasses import dataclass, field, fields, InitVar

from grimoire.core.identity import InstanceRef
from grimoire.core.inference import TurnInferencePlan, OptionalRequest, InferenceStage, plan_turn
from grimoire.generation import GenerationError, checked_controls, controller, plan_generation_turn

_SEAL = object()


def refs(values, campaign):
    if isinstance(values, (str, bytes, dict)):
        raise GenerationError('bad_present', 'expected a collection of typed actor references')
    try:
        values = tuple(values)
    except TypeError:
        raise GenerationError('bad_present', 'expected typed actor references') from None
    if any(not isinstance(r, InstanceRef) or r.campaign_id != campaign for r in values):
        raise GenerationError('bad_present', 'foreign or untyped actor reference')
    if len({r.instance_id for r in values}) != len(values):
        raise GenerationError('duplicate_actor', 'instance ids must be unique across kinds')
    return values


def owners(campaign, present, controls):
    controls = checked_controls(controls)
    result = tuple((r.instance_id, controller(campaign, r.instance_id, controls)) for r in present)
    if any(owner is None for _, owner in result):
        raise GenerationError('unassigned_actor', 'every present actor needs a controller')
    return tuple(sorted(result))


@dataclass(frozen=True, slots=True)
class ExperimentPlan(TurnInferencePlan):
    """A TurnInferencePlan with sealed typed cast and authority snapshot."""
    present_refs: tuple[InstanceRef, ...] = ()
    control_snapshot: tuple[tuple[str, str], ...] = ()
    seal: InitVar[object] = None

    def __post_init__(self, seal):
        if seal is not _SEAL:
            raise GenerationError('unsealed_plan', 'use plan_experiment')
        super(ExperimentPlan, self).__post_init__()


def plan_experiment(campaign_id, turn_id, *, present, speakers, controls,
                    planners=(), critics=(), policy=None) -> TurnInferencePlan:
    present = refs(present, campaign_id)
    speakers = refs(speakers, campaign_id)
    planners = refs(planners, campaign_id)
    critics = refs(critics, campaign_id)
    controls = checked_controls(controls)
    snapshot = owners(campaign_id, present, controls)
    if any(r not in present for r in speakers) or any(r not in speakers for r in planners + critics):
        raise GenerationError('absent_speaker', 'extras require a contributing present NPC')
    baseline = plan_generation_turn(campaign_id, turn_id, present=[r.instance_id for r in present],
                                    speakers=[r.instance_id for r in speakers], controls=controls, policy=policy)
    plan = plan_turn(campaign_id, turn_id, present_actor_ids=baseline.present_actor_ids,
                     player_controlled_ids=baseline.player_controlled_ids, policy=baseline.policy,
                     phase='planning', optional_requests=tuple(
                         OptionalRequest(InferenceStage.PLANNING, r.instance_id, 'presentation suggestion')
                         for r in planners))
    plan = plan.add_phase('generation', speaking_npc_ids=[r.instance_id for r in speakers])
    plan = plan.add_phase('critique', optional_requests=tuple(
        OptionalRequest(InferenceStage.CRITIQUE, r.instance_id, 'presentation critique') for r in critics))
    return ExperimentPlan(**{f.name: getattr(plan, f.name) for f in fields(TurnInferencePlan)},
                          present_refs=present, control_snapshot=snapshot, seal=_SEAL)
