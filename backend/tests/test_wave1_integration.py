"""Seams between Wave 1 slices that were built independently.

Each test pins how two packages meet today, so a later sprint that changes
one side has to change the documented contract too. Where the packages do
not yet agree, the test pins the documented divergence and its owner rather
than pretending they do.
"""

from pathlib import Path

import pytest

from grimoire.core import CampaignActor, ControlAssignment
from grimoire.core.authority import (
    CONTRIBUTION_DOMAINS, AuthorityError, NarrationClaim, NarrationContext, PlayerContribution,
    review_narration, submit_contribution,
)
from grimoire.core.entity import instance_from_actor
from grimoire.core.inference import InferenceStage, Measurement
from grimoire.core.roster import CampaignRoster
from grimoire.evals import format as evals_format
from grimoire.evals.checks import RP_PURPOSES
from grimoire.evals.loader import benchmark_file, load_json
from grimoire.intent import RoutingContext, Utterance, route
from grimoire.intent.model import IntentKind
from grimoire.library import V1_ROOT_MARKERS
from grimoire.security import RESERVED_ROOTS, InvalidResourcePath, ResourcePath

DOCS = Path(__file__).resolve().parents[2] / "docs"

# ---- intent (008) -> player sovereignty (003) --------------------------------------

#: The documented mapping (docs/intent-routing.md, "Handing a PC intent to the authority
#: policy"). The router has no lane for interiority or commitment contributions.
INTENT_TO_CONTRIBUTION = {
    "pc_speech": "speech",
    "pc_action": "action",
    "director_instruction": "directorial_request",
}

CAMPAIGN, PC, ALICE, BOB = "c1", "pc-1", "human:alice", "human:bob"
CONTROLS = (ControlAssignment(CAMPAIGN, PC, ALICE),)


def contribution_from(intent, submitted_by: str, number: int = 1) -> PlayerContribution:
    return PlayerContribution(CAMPAIGN, f"k{number}", "s1", "t1", intent.actor_id or PC, submitted_by,
                              INTENT_TO_CONTRIBUTION[intent.kind], intent.text)


def test_intent_lanes_map_onto_contribution_kinds_as_documented():
    pc_lanes = {"pc_speech", "pc_action", "director_instruction"}
    assert pc_lanes <= set(IntentKind.__args__)
    assert set(INTENT_TO_CONTRIBUTION) == pc_lanes
    assert set(INTENT_TO_CONTRIBUTION.values()) <= set(CONTRIBUTION_DOMAINS)
    text = (DOCS / "intent-routing.md").read_text(encoding="utf-8")
    for lane, kind in INTENT_TO_CONTRIBUTION.items():
        assert f"| `{lane}` | `{kind}` |" in text


def test_only_the_controller_can_turn_a_routed_line_into_their_pcs_contribution():
    # Routing trusts its caller's `active_pc`; submission checks who submits.
    intent = route(Utterance('"I reach for her hand."'), RoutingContext(PC, False))
    assert intent.kind == "pc_speech" and intent.actor_id == PC
    accepted = submit_contribution(contribution_from(intent, ALICE), CONTROLS)
    assert accepted.kind == "speech"
    with pytest.raises(AuthorityError):
        submit_contribution(contribution_from(intent, BOB), CONTROLS)


def test_a_promise_routed_as_an_action_cannot_back_a_commitment_claim():
    # Known gap (documented): no intent lane yields a commitment or interiority
    # contribution, so such claims fail closed instead of being authorized.
    intent = route(Utterance("I promise to return at dawn"), RoutingContext(PC, False))
    assert intent.kind == "pc_action"
    submitted = submit_contribution(contribution_from(intent, ALICE), CONTROLS)
    claim = NarrationClaim(CAMPAIGN, "n1", "commitment", "I promise to return at dawn",
                           subject_id=PC, expresses=submitted.contribution_id)
    review = review_narration(NarrationContext(CAMPAIGN, "s1", "t1"), [claim], CONTROLS,
                              contributions=[submitted])
    assert review.accepted == () and len(review.rejected) == 1


# ---- identity (002) -> authority (003) ---------------------------------------------

def test_a_campaign_actor_id_is_the_authority_policys_actor_id():
    actor = CampaignActor(CAMPAIGN, PC, local_name="Wren")
    controls = (ControlAssignment(actor.campaign_id, actor.actor_id, ALICE),)
    intent = route(Utterance("I wave."), RoutingContext(actor.actor_id, False))
    assert submit_contribution(contribution_from(intent, ALICE), controls).actor_id == actor.actor_id


def test_a_full_instance_reference_is_not_silently_taken_for_the_actor():
    # Authority and intent ids are a campaign-local segment; a reference text is
    # not one and still fails closed. Entity Facets (009) reconciles the grammars
    # with one explicit adapter: `CampaignRoster.segment_for` (docs/ENTITIES.md).
    actor = CampaignActor(CAMPAIGN, PC, local_name="Wren")
    reference = str(actor.ref)
    assert reference != actor.actor_id
    controls = (ControlAssignment(CAMPAIGN, PC, ALICE),)
    intent = route(Utterance("I wave."), RoutingContext(reference, False))
    with pytest.raises(AuthorityError):
        submit_contribution(contribution_from(intent, ALICE), controls)
    roster = CampaignRoster(CAMPAIGN, [instance_from_actor(actor)])
    intent = route(Utterance("I wave."), RoutingContext(roster.segment_for(reference), False))
    assert submit_contribution(contribution_from(intent, ALICE), controls).actor_id == PC


# ---- inference flags (004) <-> story-quality evals (005) ---------------------------

#: Where the evals interchange format knowingly differs from core.inference, and who
#: reconciles it (docs/evals/STORY_QUALITY.md, "Divergence from the inference contract").
DOCUMENTED_DIVERGENCE = {
    "narration purpose": "per-npc-generation-runtime (027) decides its stage",
    "provenance vocabulary": "v1-cost-and-voice-regression (015)",
    "token fields": "v1-cost-and-voice-regression (015)",
}


def test_eval_call_purposes_are_inference_stages_or_documented_divergence():
    stages = {stage.value for stage in InferenceStage}
    assert evals_format.NPC_GENERATION == InferenceStage.NPC_GENERATION.value
    baseline = load_json(benchmark_file("guild_betrayal", "baseline.json"))
    purposes = {call["purpose"] for call in baseline["calls"]} | set(RP_PURPOSES)
    flags = {call["feature_flag"] for call in baseline["calls"]} - {None}
    assert purposes - stages == {evals_format.NARRATION}  # the only, documented, gap
    assert flags <= stages
    text = (DOCS / "evals" / "STORY_QUALITY.md").read_text(encoding="utf-8")
    for item in DOCUMENTED_DIVERGENCE:
        assert item in text


def test_provenance_vocabularies_differ_only_as_documented():
    assert set(evals_format.PROVENANCES) == {"synthetic", "measured"}
    assert {m.value for m in Measurement} == {"provider_reported", "estimated", "synthetic"}


# ---- library isolation (001) <-> tool authority (007) ------------------------------

def test_no_worker_can_create_a_v1_root_entry_in_a_v2_library():
    # A V2 root with any of these beside `worlds/` reads as a V1 library.
    addressable = set()
    for marker in V1_ROOT_MARKERS:
        try:
            addressable.add(ResourcePath.parse(marker).segments[0])
        except InvalidResourcePath:
            continue  # not even a valid address
    assert addressable and addressable <= RESERVED_ROOTS
    assert set(V1_ROOT_MARKERS) <= RESERVED_ROOTS
    assert not ResourcePath.parse("worlds/w/regex.json").reserved
