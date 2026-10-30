"""Player-sovereignty scenarios over synthetic, already-labelled narration claims.

These prove the authority policy, not a prose narrator: no model or scene runner
is involved, and claim labelling is assumed to be done by a non-narrator labeller.
"""

from grimoire.core import (
    DIRECTOR, ControlAssignment, Correction, Delegation, ImposedCondition, NarrationClaim,
    NarrationContext, PlayerContribution, Resolution, Revocation, apply_corrections,
    auto_complete, delegate, narration_policy, review_narration, submit_contribution,
)

CAMPAIGN = "synthetic-campaign"
CTX = NarrationContext(CAMPAIGN, "scene-ballroom", "turn-7")
ROWAN, SABLE = "human:rowan-player", "human:sable-player"
CONTROLS = (
    ControlAssignment(CAMPAIGN, "rowan", ROWAN),
    ControlAssignment(CAMPAIGN, "sable", SABLE),
    ControlAssignment(CAMPAIGN, "lise", DIRECTOR),
    ControlAssignment(CAMPAIGN, "warden", DIRECTOR),
)


def said(cid, kind, text, actor="rowan", by=ROWAN):
    return submit_contribution(
        PlayerContribution(CAMPAIGN, cid, CTX.scene_id, CTX.turn_id, actor, by, kind, text),
        CONTROLS)


def claim(cid, domain, text, subject="rowan", **kwargs):
    return NarrationClaim(CAMPAIGN, cid, domain, text, subject_id=subject, **kwargs)


def verdicts(result):
    return ({a.claim.claim_id: a.basis for a in result.accepted},
            {r.claim.claim_id: r.reason for r in result.rejected})


def test_proving_scenario_reach_for_her_hand():
    reach = said("k-reach", "action", "I reach for her hand.")
    failed = Resolution(CAMPAIGN, "k-reach", "failure")
    narration = [
        claim("restate", "voluntary_action", "I reach for her hand.", expresses="k-reach"),
        claim("her", "voluntary_action", "Lise steps back, glancing at the watching guard.",
              subject="lise"),
        claim("obstacle", "world_fact", "A servant cuts between you with a tray.", subject=None),
        claim("result", "outcome", "Your fingers close on empty air.", attempt_id="k-reach",
              result="failure"),
        claim("confess", "voluntary_speech", '"I love you," you confess.'),
        claim("love", "interiority", "You realise you have loved her for years."),
        claim("trust", "belief", "You now trust her completely."),
        claim("success", "outcome", "She takes your hand.", attempt_id="k-reach",
              result="success"),
    ]
    accepted, rejected = verdicts(review_narration(
        CTX, narration, CONTROLS, contributions=[reach], resolutions=[failed]))
    assert accepted == {"restate": "player:k-reach", "her": "director", "obstacle": "director",
                        "result": "resolution:k-reach"}
    assert rejected == {"confess": "pc_authorship", "love": "pc_authorship",
                        "trust": "pc_authorship", "success": "contradicts_resolution"}


def test_romance_with_characterization_delegation_stays_correctable():
    grant = delegate("characterization", campaign_id=CAMPAIGN, delegation_id="d-char",
                     actor_id="rowan", granted_by=ROWAN, scope="scene",
                     scene_id=CTX.scene_id)
    narration = [
        claim("flutter", "interiority", "Your pulse quickens when she laughs."),
        claim("trust", "belief", "You decide she would never betray you."),
        claim("vow", "commitment", "You resolve to marry her."),
    ]
    first = review_narration(CTX, narration, CONTROLS, delegations=[grant])
    assert verdicts(first) == ({"flutter": "delegation:d-char"},
                               {"trust": "pc_authorship", "vow": "pc_authorship"})
    struck, refused = apply_corrections(
        first, [Correction(CAMPAIGN, "flutter", ROWAN, "Too soon for that")], CONTROLS)
    assert refused == () and struck.accepted == ()
    # The player revokes; the same overreach does not quietly return next turn.
    later = NarrationContext(CAMPAIGN, CTX.scene_id, "turn-8")
    again = review_narration(later, narration[:1], CONTROLS, delegations=[grant],
                             revocations=[Revocation(CAMPAIGN, "d-char", ROWAN)])
    assert verdicts(again) == ({}, {"flutter": "pc_authorship"})


def test_ensemble_players_cannot_author_or_correct_each_others_pcs():
    rowan_grant = delegate("incidental_dialogue", campaign_id=CAMPAIGN, delegation_id="d-r",
                           actor_id="rowan", granted_by=ROWAN, scope="campaign")
    sable_line = said("k-sable", "speech", '"Stay behind me."', actor="sable", by=SABLE)
    narration = [
        claim("rowan-hi", "voluntary_speech", '"Evening," you nod.'),
        claim("sable-hi", "voluntary_speech", '"Evening," Sable nods.', subject="sable"),
        claim("sable-own", "voluntary_speech", '"Stay behind me."', subject="sable",
              expresses="k-sable"),
        claim("sable-cross", "voluntary_speech", '"Stay behind me."', subject="rowan",
              expresses="k-sable"),
    ]
    result = review_narration(CTX, narration, CONTROLS, contributions=[sable_line],
                              delegations=[rowan_grant])
    assert verdicts(result) == (
        {"rowan-hi": "delegation:d-r", "sable-own": "player:k-sable"},
        {"sable-hi": "pc_authorship", "sable-cross": "invalid_expression"})
    _, refused = apply_corrections(
        result, [Correction(CAMPAIGN, "sable-own", ROWAN)], CONTROLS)
    assert [r.reason for r in refused] == ["not_controller"]
    policies = {a: narration_policy(CTX, a, CONTROLS, [rowan_grant]) for a in ("rowan", "sable")}
    assert "voluntary_speech" in policies["rowan"].director_may
    assert "voluntary_speech" in policies["sable"].reserved


def test_combat_failure_harm_and_fear_without_stealing_choices():
    strike = said("k-strike", "action", "I swing at the warden.")
    terror = ImposedCondition(CAMPAIGN, "c-terror", "rowan", "rule", "rule:warden-dread",
                              "Dread aura: failed resolve forces a step back",
                              disclosed=True, within_agreed_limits=True)
    narration = [
        claim("miss", "outcome", "Your blade glances off his pauldron.", attempt_id="k-strike",
              result="failure"),
        claim("counter", "voluntary_action", "The warden drives his shield forward.",
              subject="warden"),
        claim("hit", "external_effect", "The shield rim cracks against your ribs."),
        claim("pain", "sensation", "Pain flares hot along your side."),
        claim("dread", "involuntary_reaction", "Dread drives you a step back.",
              condition_id="c-terror"),
        claim("yield", "voluntary_speech", '"I yield!" you cry.'),
        claim("flee", "intent", "You decide to abandon the fight."),
    ]
    result = review_narration(CTX, narration, CONTROLS, contributions=[strike],
                              conditions=[terror],
                              resolutions=[Resolution(CAMPAIGN, "k-strike", "failure")])
    accepted, rejected = verdicts(result)
    assert accepted == {"miss": "resolution:k-strike", "counter": "director", "hit": "director",
                        "pain": "director", "dread": "condition:c-terror"}
    assert rejected == {"yield": "pc_authorship", "flee": "pc_authorship"}
    # A player may flag harm for review but cannot veto it or the recorded failure.
    after, refused = apply_corrections(result, [
        Correction(CAMPAIGN, "hit", ROWAN), Correction(CAMPAIGN, "miss", ROWAN),
        Correction(CAMPAIGN, "dread", ROWAN)], CONTROLS)
    assert len(after.accepted) == len(result.accepted)
    assert [c.claim_id for c in after.disputed] == ["hit"]
    assert [r.reason for r in refused] == ["not_player_authority"] * 2


def test_mystery_evidence_is_the_directors_deduction_is_the_players():
    hunch = said("k-hunch", "interiority", "I suspect the steward.")
    narration = [
        claim("clue", "world_fact", "The steward's cuffs are stained with lamp oil.",
              subject="lise"),
        claim("smell", "sensation", "You smell lamp oil near the study door."),
        claim("solve", "belief", "You realise the steward is the killer."),
        claim("own-hunch", "belief", "I suspect the steward.", expresses="k-hunch"),
        claim("lise-lie", "voluntary_speech", '"I was in the chapel all night," Lise says.',
              subject="lise"),
        claim("accuse", "intent", "You resolve to accuse him at dinner."),
    ]
    accepted, rejected = verdicts(review_narration(CTX, narration, CONTROLS,
                                                   contributions=[hunch]))
    assert accepted == {"clue": "director", "smell": "director", "own-hunch": "player:k-hunch",
                        "lise-lie": "director"}
    assert rejected == {"solve": "pc_authorship", "accuse": "pc_authorship"}


def test_delegation_modes_are_adjustable_and_explicit():
    walk = said("k-walk", "action", "I head to the stables.")
    walking = claim("walk", "voluntary_action", "You cross the yard to the stables.",
                    expresses="k-walk")
    greet = claim("greet", "voluntary_speech", '"Morning," you tell the groom.')
    feel = claim("feel", "interiority", "You feel at home among the horses.")
    trust = claim("trust", "belief", "You trust the groom.")
    narration = [walking, greet, feel, trust]

    def run(*delegations):
        return verdicts(review_narration(CTX, narration, CONTROLS, contributions=[walk],
                                         delegations=delegations))

    strict = run()
    assert strict == ({}, {"walk": "not_verbatim", "greet": "pc_authorship",
                           "feel": "pc_authorship", "trust": "pc_authorship"})
    assert run(auto_complete(walk, "d-auto"))[0] == {"walk": "delegation:d-auto"}
    incidental = delegate("incidental_dialogue", campaign_id=CAMPAIGN, delegation_id="d-inc",
                          actor_id="rowan", granted_by=ROWAN, scope="turn",
                          scene_id=CTX.scene_id, turn_id=CTX.turn_id)
    assert set(run(incidental)[0]) == {"walk", "greet"}
    character = delegate("characterization", campaign_id=CAMPAIGN, delegation_id="d-char",
                         actor_id="rowan", granted_by=ROWAN, scope="campaign")
    assert set(run(character)[0]) == {"walk", "greet", "feel"}
    custom = Delegation(CAMPAIGN, "d-belief", "rowan", ROWAN, {"belief"}, "campaign", "custom")
    assert run(character, custom)[0] == {"walk": "delegation:d-char", "greet": "delegation:d-char",
                                         "feel": "delegation:d-char", "trust": "delegation:d-belief"}
