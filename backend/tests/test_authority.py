"""Player-sovereignty authority policy over labelled narration claims.

Synthetic fixtures only. Forbidden PC-authorship errors and where they are pinned:

| Forbidden error                                   | Test                                             |
|---------------------------------------------------|--------------------------------------------------|
| invented PC dialogue                              | test_director_cannot_author_protected_pc_domains |
| paraphrased quoted speech                         | test_expressed_claims_must_be_verbatim           |
| escalated action expressing a contribution        | test_expressed_claims_must_be_verbatim           |
| feeling/belief relabelled from a contribution     | test_expression_must_match_contribution          |
| directorial request used as PC authorship         | test_expression_must_match_contribution          |
| assigned feeling, decided trust, imposed promise  | test_director_cannot_author_protected_pc_domains |
| failure narrated as success                       | test_outcome_must_agree_with_resolution          |
| conflicting resolutions                           | test_outcome_must_agree_with_resolution          |
| fear without/undisclosed/outside-limits condition | test_involuntary_reaction_requires_usable_...    |
| delegation reused for another PC/scene/turn/...   | test_delegation_does_not_creep                   |
| delegation after control change / revocation      | test_delegation_does_not_creep                   |
| delegation granted by non-controller/director     | test_delegation_does_not_creep, test_records_... |
| contribution by non-controller or director        | test_submit_contribution_requires_controller     |
| ambiguous / unknown control                       | test_control_fails_closed                        |
| cross-campaign claim                              | test_cross_campaign_and_duplicate_claims         |
| correction by non-controller, veto of outcome     | test_corrections                                 |
"""

from dataclasses import FrozenInstanceError
from itertools import product

import pytest

from grimoire.core import (
    ALL_DOMAINS, DIRECTOR, OPEN_DOMAINS, PROTECTED_DOMAINS, AuthorityError,
    ControlAssignment, Correction, Delegation, ImposedCondition, NarrationClaim,
    NarrationContext, PlayerContribution, Resolution, Revocation, apply_corrections,
    auto_complete, controller_of, delegate, narration_policy, review_narration,
    submit_contribution,
)

CAMPAIGN = "campaign-a"
CTX = NarrationContext(CAMPAIGN, "scene-1", "turn-1")
P1 = "human:player-one"
P2 = "human:player-two"
CONTROLS = (
    ControlAssignment(CAMPAIGN, "ash", P1),
    ControlAssignment(CAMPAIGN, "bryn", P2),
    ControlAssignment(CAMPAIGN, "cora", DIRECTOR),
)


def claim(claim_id, domain, text="...", subject="ash", **kwargs):
    return NarrationClaim(CAMPAIGN, claim_id, domain, text, subject_id=subject, **kwargs)


def contribution(cid, kind, text, actor="ash", by=P1, scene="scene-1", turn="turn-1"):
    return PlayerContribution(CAMPAIGN, cid, scene, turn, actor, by, kind, text)


def review(claims, **kwargs):
    return review_narration(CTX, claims, CONTROLS, **kwargs)


def reasons(result):
    return {r.claim.claim_id: r.reason for r in result.rejected}


def bases(result):
    return {a.claim.claim_id: a.basis for a in result.accepted}


def fear(cid="fear-1", actor="ash", disclosed=True, limits=True):
    return ImposedCondition(CAMPAIGN, cid, actor, "rule", "rule:terror-aura",
                            "The wraith's aura forces a terror check", disclosed, limits)


# --- records -----------------------------------------------------------------

def test_records_validate_and_are_immutable():
    with pytest.raises(ValueError):
        NarrationClaim(CAMPAIGN, "c", "telepathy", "x", subject_id="ash")
    with pytest.raises(ValueError):  # subject required except world_fact
        NarrationClaim(CAMPAIGN, "c", "sensation", "Cold rain")
    with pytest.raises(ValueError):  # outcome needs attempt and result
        claim("c", "outcome", attempt_id="a1")
    with pytest.raises(ValueError):  # result only on outcome
        claim("c", "external_effect", result="failure")
    with pytest.raises(ValueError):  # condition only on involuntary reactions
        claim("c", "interiority", condition_id="fear-1")
    with pytest.raises(ValueError):  # expresses only on protected domains
        claim("c", "sensation", expresses="k1")
    with pytest.raises(ValueError):
        contribution("k", "telepathy", "x")
    with pytest.raises(ValueError):  # the director is reserved, never a player
        contribution("k", "speech", "x", by=DIRECTOR)
    with pytest.raises(ValueError):
        Delegation(CAMPAIGN, "d", "ash", DIRECTOR, {"interiority"}, "campaign", "custom")
    with pytest.raises(ValueError):  # only protected domains are delegable
        Delegation(CAMPAIGN, "d", "ash", P1, {"outcome"}, "campaign", "custom")
    with pytest.raises(ValueError):
        Delegation(CAMPAIGN, "d", "ash", P1, set(), "campaign", "custom")
    with pytest.raises(ValueError):  # turn scope needs scene and turn
        Delegation(CAMPAIGN, "d", "ash", P1, {"interiority"}, "turn", "custom", scene_id="s")
    with pytest.raises(ValueError):  # campaign scope forbids narrower ids
        Delegation(CAMPAIGN, "d", "ash", P1, {"interiority"}, "campaign", "custom", scene_id="s")
    with pytest.raises(ValueError):  # a preset label cannot hide extra domains
        Delegation(CAMPAIGN, "d", "ash", P1, {"voluntary_action", "voluntary_speech", "belief"},
                   "campaign", "incidental_dialogue")
    with pytest.raises(ValueError):
        Delegation(CAMPAIGN, "d", "ash", P1, {"voluntary_action"}, "campaign", "auto_complete")
    with pytest.raises(ValueError):
        Delegation(CAMPAIGN, "d", "ash", P1, {"belief"}, "campaign", "trust-me")
    with pytest.raises(ValueError):
        ImposedCondition(CAMPAIGN, "f", "ash", "narration", "x", "y")
    with pytest.raises(ValueError):
        Resolution(CAMPAIGN, "a1", "")
    d = Delegation(CAMPAIGN, "d", "ash", P1, ["interiority"], "campaign", "custom")
    assert d.domains == frozenset({"interiority"})
    with pytest.raises(FrozenInstanceError):
        d.domains = frozenset(PROTECTED_DOMAINS)  # type: ignore[misc]
    assert ImposedCondition(CAMPAIGN, "f", "ash", "rule", "r", "x").disclosed is False


# --- control and submission ----------------------------------------------------

def test_control_fails_closed():
    assert controller_of(CAMPAIGN, "ash", CONTROLS) == P1
    assert controller_of(CAMPAIGN, "nobody", CONTROLS) is None
    assert controller_of("campaign-b", "ash", CONTROLS) is None
    assert controller_of(CAMPAIGN, "ash", CONTROLS + (CONTROLS[0],)) == P1
    contested = CONTROLS + (ControlAssignment(CAMPAIGN, "ash", P2),)
    with pytest.raises(AuthorityError):
        controller_of(CAMPAIGN, "ash", contested)
    with pytest.raises(AuthorityError):
        review_narration(CTX, [claim("c", "sensation")], contested)
    with pytest.raises(AuthorityError):
        narration_policy(CTX, "ash", contested)
    with pytest.raises(AuthorityError):
        narration_policy(CTX, "nobody", CONTROLS)
    result = review([claim("c", "sensation", subject="nobody")])
    assert reasons(result) == {"c": "unknown_control"}


def test_submit_contribution_requires_controller():
    mine = contribution("k1", "speech", '"Hello."')
    assert submit_contribution(mine, CONTROLS) is mine
    with pytest.raises(AuthorityError):
        submit_contribution(contribution("k2", "speech", '"Hi."', by=P2), CONTROLS)
    with pytest.raises(AuthorityError):  # nobody can play the director's actor
        submit_contribution(contribution("k3", "action", "I wave", actor="cora"), CONTROLS)
    with pytest.raises(AuthorityError):
        submit_contribution(contribution("k4", "action", "I wave", actor="ghost"), CONTROLS)


# --- review ----------------------------------------------------------------------

def test_director_cannot_author_protected_pc_domains():
    claims = [
        claim("speech", "voluntary_speech", '"I love you," you whisper.'),
        claim("feeling", "interiority", "Your heart swells with love for her."),
        claim("trust", "belief", "You decide you can trust her."),
        claim("promise", "commitment", "You swear to protect her."),
        claim("intent", "intent", "You resolve to kill the duke."),
        claim("act", "voluntary_action", "You kiss her."),
    ]
    assert set(reasons(review(claims)).values()) == {"pc_authorship"}


def test_director_narrates_world_npcs_sensation_and_harm_without_refusal():
    claims = [
        claim("fact", "world_fact", "The bridge is rotten.", subject=None),
        claim("sense", "sensation", "Cold rain soaks through your collar."),
        claim("harm", "external_effect", "The arrow takes you in the shoulder."),
        claim("npc-line", "voluntary_speech", '"Not tonight," Cora says.', subject="cora"),
        claim("npc-heart", "interiority", "Cora resents the intrusion.", subject="cora"),
        claim("npc-fail", "outcome", "Cora fumbles the latch.", subject="cora",
              attempt_id="cora-latch", result="failure"),
    ]
    result = review(claims)
    assert result.rejected == ()
    assert set(bases(result).values()) == {"director"}
    assert [a.claim.claim_id for a in result.accepted] == [c.claim_id for c in claims]


def test_expressed_claims_must_be_verbatim():
    said = contribution("k-say", "speech", '"Wait for me."')
    reach = contribution("k-reach", "action", "I reach for her hand.")
    claims = [
        claim("exact", "voluntary_speech", '  "Wait for me."  ', expresses="k-say"),
        claim("paraphrase", "voluntary_speech", '"Wait for me, my love."', expresses="k-say"),
        claim("act", "voluntary_action", "I reach for her hand.", expresses="k-reach"),
        claim("escalate", "voluntary_action", "You kiss her.", expresses="k-reach"),
    ]
    result = review(claims, contributions=[said, reach])
    assert bases(result) == {"exact": "player:k-say", "act": "player:k-reach"}
    assert reasons(result) == {"paraphrase": "not_verbatim", "escalate": "not_verbatim"}


def test_expression_must_match_contribution():
    wary = contribution("k-wary", "interiority", "I'm wary of her.")
    request = contribution("k-ooc", "directorial_request", "Have her confess she likes me.")
    reach = contribution("k-reach", "action", "I reach for her hand.")
    old = contribution("k-old", "speech", '"Hello."', turn="turn-0")
    other_scene = contribution("k-elsewhere", "speech", '"Hello."', scene="scene-2")
    forged = contribution("k-forged", "speech", '"I yield."', by=P2)
    promise = contribution("k-vow", "speech", '"I promise to return."')
    claims = [
        claim("trust", "belief", "I'm wary of her.", expresses="k-wary"),  # ok: verbatim
        claim("ooc", "interiority", "Have her confess she likes me.", expresses="k-ooc"),
        claim("relabel", "belief", "I reach for her hand.", expresses="k-reach"),
        claim("stale", "voluntary_speech", '"Hello."', expresses="k-old"),
        claim("scene", "voluntary_speech", '"Hello."', expresses="k-elsewhere"),
        claim("forged", "voluntary_speech", '"I yield."', expresses="k-forged"),
        claim("bryn", "voluntary_speech", '"I promise to return."', subject="bryn",
              expresses="k-vow"),
        claim("missing", "voluntary_speech", '"?"', expresses="k-none"),
        claim("vow", "commitment", '"I promise to return."', expresses="k-vow"),
    ]
    result = review(claims, contributions=[wary, request, reach, old, other_scene, forged, promise])
    assert bases(result) == {"trust": "player:k-wary", "vow": "player:k-vow"}
    assert set(reasons(result).values()) == {"invalid_expression"}
    assert len(result.rejected) == 7


def test_outcome_must_agree_with_resolution():
    reach = contribution("k-reach", "action", "I reach for her hand.")
    earlier = contribution("k-climb", "action", "I start climbing the tower.", turn="turn-0")
    forged = contribution("k-forged", "action", "I grab the key.", by=P2)
    failed = Resolution(CAMPAIGN, "k-reach", "failure")
    climbing = Resolution(CAMPAIGN, "k-climb", "success")
    claims = [
        claim("lie", "outcome", "Your fingers close around hers.", attempt_id="k-reach",
              result="success"),
        claim("true", "outcome", "She pulls away before you touch.", attempt_id="k-reach",
              result="failure"),
        claim("later", "outcome", "You reach the top.", attempt_id="k-climb", result="success"),
        claim("fiat", "outcome", "You grab the key.", attempt_id="k-unknown", result="success"),
        claim("forged", "outcome", "You grab the key.", attempt_id="k-forged", result="success"),
        claim("npc", "outcome", "Cora's spell holds.", subject="cora", attempt_id="cora-1",
              result="success"),
    ]
    result = review(claims, contributions=[reach, earlier, forged],
                    resolutions=[failed, climbing, Resolution(CAMPAIGN, "cora-1", "failure")])
    assert bases(result) == {"true": "resolution:k-reach", "later": "resolution:k-climb"}
    assert reasons(result) == {
        "lie": "contradicts_resolution", "fiat": "unknown_attempt",
        "forged": "unknown_attempt", "npc": "contradicts_resolution",
    }
    unresolved = review([claim("u", "outcome", "x", attempt_id="k-reach", result="partial")],
                        contributions=[reach])
    assert bases(unresolved) == {"u": "director"}
    split = review([claim("s", "outcome", "x", attempt_id="k-reach", result="failure")],
                   contributions=[reach],
                   resolutions=[failed, Resolution(CAMPAIGN, "k-reach", "success")])
    assert reasons(split) == {"s": "conflicting_resolutions"}


def test_involuntary_reaction_requires_usable_condition():
    claims = [
        claim("bare", "involuntary_reaction", "Terror floods you."),
        claim("ok", "involuntary_reaction", "Terror floods you.", condition_id="fear-1"),
        claim("hidden", "involuntary_reaction", "You flinch.", condition_id="fear-2"),
        claim("limits", "involuntary_reaction", "You flee.", condition_id="fear-3"),
        claim("other-pc", "involuntary_reaction", "You flinch.", condition_id="fear-b"),
        claim("say", "voluntary_speech", '"I surrender!" you scream.'),
    ]
    result = review(claims, conditions=[
        fear(), fear("fear-2", disclosed=False), fear("fear-3", limits=False),
        fear("fear-b", actor="bryn"),
    ])
    assert bases(result) == {"ok": "condition:fear-1"}
    assert reasons(result) == {
        "bare": "missing_condition", "hidden": "undisclosed_condition",
        "limits": "outside_agreed_limits", "other-pc": "missing_condition",
        "say": "pc_authorship",  # a condition never authorizes protected domains
    }


def test_delegation_does_not_creep():
    def grant(did, *, actor="ash", by=P1, scope="campaign", **ids):
        return Delegation(CAMPAIGN, did, actor, by, {"interiority"}, scope, "custom", **ids)

    feeling = claim("c", "interiority", "You feel a pang of longing.")
    cases = {
        "campaign": (grant("d"), ()),
        "scene": (grant("d", scope="scene", scene_id="scene-1"), ()),
        "turn": (grant("d", scope="turn", scene_id="scene-1", turn_id="turn-1"), ()),
    }
    for did_grant, revs in cases.values():
        assert bases(review([feeling], delegations=[did_grant], revocations=revs)) == {
            "c": "delegation:d"}
    denied = [
        grant("d", actor="bryn"),  # another PC
        grant("d", by=P2),  # not the controller
        grant("d", scope="scene", scene_id="scene-2"),
        grant("d", scope="turn", scene_id="scene-2", turn_id="turn-1"),
        grant("d", scope="turn", scene_id="scene-1", turn_id="turn-2"),
        Delegation(CAMPAIGN, "d", "ash", P1, {"belief"}, "campaign", "custom"),  # other domain
        Delegation("campaign-b", "d", "ash", P1, {"interiority"}, "campaign", "custom"),
    ]
    for bad in denied:
        assert reasons(review([feeling], delegations=[bad])) == {"c": "pc_authorship"}, bad
    # revocation by the controller ends it; a revocation by someone else does not
    assert reasons(review([feeling], delegations=[grant("d")],
                          revocations=[Revocation(CAMPAIGN, "d", P1)])) == {"c": "pc_authorship"}
    assert bases(review([feeling], delegations=[grant("d")],
                        revocations=[Revocation(CAMPAIGN, "d", P2)])) == {"c": "delegation:d"}
    # a delegation does not survive a control change
    moved = (ControlAssignment(CAMPAIGN, "ash", P2),) + CONTROLS[1:]
    stale = review_narration(CTX, [feeling], moved, delegations=[grant("d")])
    assert reasons(stale) == {"c": "pc_authorship"}
    with pytest.raises(ValueError):
        review([feeling], delegations=[grant("d"), grant("d", scope="scene", scene_id="scene-1")])


def test_auto_complete_is_bounded_to_one_submitted_action():
    reach = contribution("k-reach", "action", "I reach for her hand.")
    consent = auto_complete(reach, "d-auto")
    assert consent.scope == "contribution" and consent.domains == {"voluntary_action"}
    assert consent.granted_by == P1 and consent.contribution_id == "k-reach"
    claims = [
        claim("phrased", "voluntary_action", "You stretch out, slow and careful.",
              expresses="k-reach"),
        claim("unrelated", "voluntary_action", "You draw your sword."),
        claim("speech", "voluntary_speech", '"May I?"', expresses="k-reach"),
    ]
    result = review(claims, contributions=[reach], delegations=[consent])
    # Atomicity (no second action smuggled into "phrased") is the labeller's duty.
    assert bases(result) == {"phrased": "delegation:d-auto"}
    assert reasons(result) == {"unrelated": "pc_authorship", "speech": "invalid_expression"}
    with pytest.raises(ValueError):
        auto_complete(contribution("k-s", "speech", '"Hi"'), "d")
    with pytest.raises(ValueError):  # contribution scope is only for explicit consent
        delegate("incidental_dialogue", campaign_id=CAMPAIGN, delegation_id="d",
                 actor_id="ash", granted_by=P1, scope="contribution")


def test_presets_are_frozen_and_exclude_intent_belief_and_commitment():
    incidental = delegate("incidental_dialogue", campaign_id=CAMPAIGN, delegation_id="d1",
                          actor_id="ash", granted_by=P1, scope="scene", scene_id="scene-1")
    character = delegate("characterization", campaign_id=CAMPAIGN, delegation_id="d2",
                         actor_id="ash", granted_by=P1, scope="campaign")
    assert incidental.domains == {"voluntary_action", "voluntary_speech"}
    assert character.domains == {"voluntary_action", "voluntary_speech", "interiority"}
    for d in (incidental, character):
        assert not d.domains & {"intent", "belief", "commitment"}
    with pytest.raises(ValueError):
        delegate("autopilot", campaign_id=CAMPAIGN, delegation_id="d", actor_id="ash",
                 granted_by=P1, scope="campaign")


def test_cross_campaign_and_duplicate_claims():
    foreign = NarrationClaim("campaign-b", "x", "sensation", "Rain.", subject_id="ash")
    assert reasons(review([foreign])) == {"x": "cross_campaign"}
    with pytest.raises(ValueError):
        review([claim("x", "sensation"), claim("x", "world_fact", subject=None)])


def test_review_accepts_single_pass_iterables_and_preserves_order():
    claims = [claim(f"c{i}", "sensation" if i % 2 else "interiority") for i in range(6)]
    result = review_narration(CTX, iter(claims), iter(CONTROLS))
    assert [a.claim.claim_id for a in result.accepted] == ["c1", "c3", "c5"]
    assert [r.claim.claim_id for r in result.rejected] == ["c0", "c2", "c4"]
    assert isinstance(result.accepted, tuple) and result.disputed == ()


# --- policy ------------------------------------------------------------------------

def test_policy_reserves_protected_domains_for_players():
    pc = narration_policy(CTX, "ash", CONTROLS)
    assert pc.controller_id == P1
    assert pc.reserved == PROTECTED_DOMAINS
    assert pc.director_may == OPEN_DOMAINS | {"outcome"}
    npc = narration_policy(CTX, "cora", CONTROLS)
    assert npc.director_may == ALL_DOMAINS and npc.reserved == frozenset()
    granted = narration_policy(
        CTX, "ash", CONTROLS,
        delegations=[
            delegate("incidental_dialogue", campaign_id=CAMPAIGN, delegation_id="d1",
                     actor_id="ash", granted_by=P1, scope="campaign"),
            auto_complete(contribution("k", "action", "I run."), "d-auto"),
        ],
        conditions=[fear(), fear("fear-2", disclosed=False)])
    assert granted.delegation_ids == ("d1",)  # contribution consent doesn't widen policy
    assert granted.condition_ids == ("fear-1",)
    assert granted.reserved == {"intent", "interiority", "belief", "commitment"}
    assert {"voluntary_speech", "involuntary_reaction"} <= granted.director_may


def test_policy_and_review_agree():
    def grant(scope, by=P1, **ids):
        return Delegation(CAMPAIGN, "d", "ash", by, {"interiority", "belief"}, scope,
                          "custom", **ids)

    grants = [
        None, grant("campaign"), grant("scene", scene_id="scene-1"),
        grant("scene", scene_id="scene-2"),
        grant("turn", scene_id="scene-1", turn_id="turn-1"), grant("campaign", by=P2),
    ]
    revocation_sets = [(), (Revocation(CAMPAIGN, "d", P1),)]
    control_sets = [CONTROLS, (ControlAssignment(CAMPAIGN, "ash", P2),) + CONTROLS[1:]]
    condition_sets = [(), (fear(),), (fear(disclosed=False),)]
    for subject, g, revs, controls, conds in product(
            ["ash", "cora"], grants, revocation_sets, control_sets, condition_sets):
        delegations = () if g is None else (g,)
        policy = narration_policy(CTX, subject, controls, delegations, revs, conds)
        for domain in sorted(ALL_DOMAINS - {"outcome", "involuntary_reaction"}):
            c = claim("c", domain, subject=subject)
            result = review_narration(CTX, [c], controls, delegations=delegations,
                                      revocations=revs, conditions=conds)
            assert bool(result.accepted) == (domain in policy.director_may), (subject, g, domain)
        reaction = claim("r", "involuntary_reaction", subject=subject, condition_id="fear-1")
        result = review_narration(CTX, [reaction], controls, conditions=conds)
        if subject == "ash":
            assert bool(result.accepted) == ("fear-1" in policy.condition_ids)
        assert bool(policy.condition_ids) <= ("involuntary_reaction" in policy.director_may)
        assert "outcome" in policy.director_may


# --- corrections ---------------------------------------------------------------------

def test_corrections():
    reach = contribution("k-reach", "action", "I reach for her hand.")
    char = delegate("characterization", campaign_id=CAMPAIGN, delegation_id="d",
                    actor_id="ash", granted_by=P1, scope="campaign")
    claims = [
        claim("love", "interiority", "Your heart swells with love."),  # delegated
        claim("own", "voluntary_action", "I reach for her hand.", expresses="k-reach"),
        claim("wound", "external_effect", "The arrow takes you in the shoulder."),
        claim("fail", "outcome", "She pulls away.", attempt_id="k-reach", result="failure"),
        claim("fear", "involuntary_reaction", "Terror floods you.", condition_id="fear-1"),
        claim("fact", "world_fact", "Kael says 'I love you'.", subject=None),
        claim("npc", "interiority", "Cora is jealous.", subject="cora"),
    ]
    original = review(claims, contributions=[reach], delegations=[char], conditions=[fear()],
                      resolutions=[Resolution(CAMPAIGN, "k-reach", "failure")])
    assert len(original.accepted) == 7
    corrections = [
        Correction(CAMPAIGN, "love", P1, "Ash would never"),
        Correction(CAMPAIGN, "own", P1),
        Correction(CAMPAIGN, "wound", P1, "I think this is a mislabel"),
        Correction(CAMPAIGN, "fail", P1),
        Correction(CAMPAIGN, "fear", P1),
        Correction(CAMPAIGN, "fact", P2),  # any player with an actor may flag a subjectless fact
        Correction(CAMPAIGN, "npc", P1),
        Correction(CAMPAIGN, "wound", P2),  # another player cannot correct my PC
        Correction(CAMPAIGN, "love", P1),  # already struck
        Correction(CAMPAIGN, "missing", P1),
        Correction(CAMPAIGN, "fact", "human:stranger"),
        Correction("campaign-b", "own", P1),
    ]
    corrected, refused = apply_corrections(original, corrections, CONTROLS)
    assert [a.claim.claim_id for a in corrected.accepted] == ["wound", "fail", "fear", "fact", "npc"]
    assert [(r.claim.claim_id, r.reason) for r in corrected.rejected] == [
        ("love", "player_correction"), ("own", "player_correction")]
    assert [(c.claim_id, c.corrected_by) for c in corrected.disputed] == [
        ("wound", P1), ("fact", P2)]
    assert [(r.correction.claim_id, r.reason) for r in refused] == [
        ("fail", "not_player_authority"), ("fear", "not_player_authority"),
        ("npc", "not_controller"), ("wound", "not_controller"), ("love", "unknown_claim"),
        ("missing", "unknown_claim"), ("fact", "not_controller"), ("own", "unknown_claim"),
    ]
    assert len(original.accepted) == 7 and original.disputed == ()  # inputs untouched
    # Unresolved outcomes can be flagged, never vetoed.
    unresolved = review([claim("u", "outcome", "x", attempt_id="k-reach", result="failure")],
                        contributions=[reach])
    flagged, none = apply_corrections(unresolved, [Correction(CAMPAIGN, "u", P1)], CONTROLS)
    assert none == () and flagged.accepted == unresolved.accepted and len(flagged.disputed) == 1
    # Revocation, a separate input, keeps a struck delegated overreach from recurring.
    again = review([claims[0]], delegations=[char], revocations=[Revocation(CAMPAIGN, "d", P1)])
    assert reasons(again) == {"love": "pc_authorship"}
