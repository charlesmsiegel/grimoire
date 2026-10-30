"""Session-zero acceptance scenarios on synthetic fixtures (sprint 016, first slice).

Headless policy only: these prove decisions, not playable skip support. The
Wave 5 text runner executes them.
"""

import pytest

from grimoire.core import (
    DIRECTOR, Boundary, BoundaryAuthorityError, BoundaryChange, BoundaryContext,
    BoundaryRequest, CheckInConsent, ControlAssignment, ImposedCondition, NarrationClaim,
    NarrationContext, NarrationSegment, PlayerContribution, Resolution, SessionZero,
    agreed_limits, controller_of, decide_presentation, decide_request, owner_boundaries,
    review_narration, rule_themes, visible_boundaries,
)

CAMPAIGN = "campaign-duel"
P1, P2, P3 = "human:ines", "human:oto", "human:remy"
TABLE = BoundaryContext(CAMPAIGN, frozenset({P1, P2}))
NOTE = "P1-only reason, never shown"
CONTROLS = (ControlAssignment(CAMPAIGN, "pc-ines", P1), ControlAssignment(CAMPAIGN, "pc-oto", P2),
            ControlAssignment(CAMPAIGN, "npc-duelist", DIRECTOR))
NARRATION = NarrationContext(CAMPAIGN, "scene-1", "turn-1")

DUEL = NarrationSegment(CAMPAIGN, "seg-duel", "The duelist's blade opens a long wound.",
                        frozenset({"violence", "graphic_violence"}), "accepted", "e-duel")
STANDOFF = NarrationSegment(CAMPAIGN, "seg-standoff", "Steel rings; neither yields.",
                            frozenset({"violence"}), "proposed")


def session_zero():
    """Campaign keeps high-stakes conflict; P1 privately asks for graphic violence to fade."""
    zero = SessionZero()
    zero = zero.apply(BoundaryChange("set", P2, boundary=Boundary(
        "campaign-violence", "campaign", CAMPAIGN, "violence", "allow", P2)), TABLE)
    zero = zero.apply(BoundaryChange("agree", P1, boundary_id="campaign-violence"), TABLE)
    return zero.apply(BoundaryChange("set", P1, boundary=Boundary(
        "ines-gore", "player", P1, "graphic_violence", "fade_to_black", P1,
        private_note=NOTE)), TABLE)


def test_proving_scenario_fade_preserves_duel():
    zero = session_zero()
    duel, standoff = decide_presentation(zero, TABLE, [DUEL, STANDOFF])
    assert (duel.action, duel.preserved_event_id) == ("fade_to_black", "e-duel")
    assert standoff.action == "present"

    skipped = decide_request(BoundaryRequest(CAMPAIGN, "r1", P1, "skip", ("seg-duel",)),
                             TABLE, [DUEL, STANDOFF])
    assert (skipped.skip_presentation, skipped.preserved_events) == (("seg-duel",), ("e-duel",))

    # Easy correction: P1 tightens their own boundary; the outcome still stands.
    tighter = zero.apply(BoundaryChange("set", P1, boundary=Boundary(
        "ines-gore", "player", P1, "graphic_violence", "skip", P1, private_note=NOTE)), TABLE)
    (duel,) = decide_presentation(tighter, TABLE, [DUEL])
    assert (duel.action, duel.preserved_event_id) == ("skip", "e-duel")


def test_group_conflict_is_visible_and_anonymous():
    zero = SessionZero((Boundary("c-bh", "campaign", CAMPAIGN, "body_horror", "allow", P1,
                                 attributed=True),
                        Boundary("o-bh", "player", P2, "body_horror", "avoid", P2)))
    (ruling,) = rule_themes(zero, TABLE, {"body_horror"})
    assert (ruling.treatment, ruling.conflict) == ("avoid", True)
    assert [(s.scope, s.set_by) for s in ruling.sources] == [("campaign", P1), ("player", None)]


def test_private_data_never_leaves_owner_view():
    zero = session_zero()
    outputs = [
        zero, visible_boundaries(zero, TABLE), rule_themes(zero, TABLE, {"graphic_violence"}),
        decide_presentation(zero, TABLE, [DUEL, STANDOFF]),
        decide_request(BoundaryRequest(CAMPAIGN, "r1", P1, "redirect", ("seg-standoff",),
                                       "after the duel"), TABLE, [DUEL, STANDOFF]),
        agreed_limits({"graphic_violence"}, zero, TABLE, controller=P1, target_id="cond-1"),
    ]
    errors = []
    for change in (BoundaryChange("withdraw", P2, boundary_id="ines-gore"),
                   BoundaryChange("set", P2, boundary=Boundary(
                       "ines-gore", "player", P2, "graphic_violence", "allow", P2))):
        with pytest.raises(BoundaryAuthorityError) as error:
            zero.apply(change, TABLE)
        errors.append(str(error.value))
    with pytest.raises(ValueError) as error:
        SessionZero((*zero.boundaries, zero.boundaries[-1]))
    errors.append(str(error.value))

    for text in (*map(repr, outputs), *errors):
        assert NOTE not in text and P1 not in text and "ines-gore" not in text
    # The owner view is the one place both appear.
    (mine,) = [b for b in owner_boundaries(zero, P1) if b.scope == "player"]
    assert mine.private_note == NOTE


def test_hard_limit_proposal_vs_canon():
    zero = SessionZero((Boundary("oto-spiders", "player", P2, "spiders", "avoid", P2),))
    nest = NarrationSegment(CAMPAIGN, "seg-nest", "Spiders pour from the vault.",
                            frozenset({"spiders"}), "proposed")
    bite = NarrationSegment(CAMPAIGN, "seg-bite", "The earlier spider bite still aches.",
                            frozenset({"spiders"}), "accepted", "e-bite")
    proposal, canon = decide_presentation(zero, TABLE, [nest, bite])
    assert (proposal.action, proposal.notices) == ("withhold_proposal", ("avoided_theme",))
    assert (canon.action, canon.notices, canon.preserved_event_id) == (
        "skip", ("canon_touches_avoided_theme",), "e-bite")


def test_redirect_and_skip_mixed_targets():
    targets = ("seg-duel", "seg-standoff")
    redirect = decide_request(BoundaryRequest(CAMPAIGN, "r1", P2, "redirect", targets,
                                              "cut to the tavern"), TABLE, [DUEL, STANDOFF])
    assert (redirect.withdrawn_proposals, redirect.preserved_events) == (
        ("seg-standoff",), ("e-duel",))
    skip = decide_request(BoundaryRequest(CAMPAIGN, "r2", P2, "skip", targets), TABLE,
                          [DUEL, STANDOFF])
    assert (skip.skip_presentation, skip.withdrawn_proposals) == (("seg-duel",), ("seg-standoff",))


def test_absent_player():
    zero = SessionZero((Boundary("remy-fear", "player", P3, "fear", "avoid", P3),))
    scare = NarrationSegment(CAMPAIGN, "seg-scare", "Something breathes behind the door.",
                             frozenset({"fear"}), "proposed")
    (decision,) = decide_presentation(zero, TABLE, [scare])
    assert decision.action == "present"
    # ...but it still protects P3's own PC from an imposed fear condition.
    check = agreed_limits({"fear"}, zero, TABLE, controller=P3, target_id="cond-1")
    assert (check.within, check.reason) == (False, "avoided")


@pytest.mark.parametrize("boundaries, within", [
    ((Boundary("ines-fear", "player", P1, "fear", "allow", P1),), True),
    ((Boundary("c-fear", "campaign", CAMPAIGN, "fear", "allow", P2),), False),
    ((), False),
    ((Boundary("ines-fear", "player", P1, "fear", "avoid", P1),), False),
])
def test_agreed_limits_feed_imposed_condition(boundaries, within):
    controller = controller_of(CAMPAIGN, "pc-ines", CONTROLS)
    check = agreed_limits({"fear"}, SessionZero(boundaries), TABLE, controller=controller,
                          target_id="cond-terror")
    assert check.within is within
    condition = ImposedCondition(CAMPAIGN, "cond-terror", "pc-ines", "rule", "spell-1",
                                 "supernatural terror", disclosed=True,
                                 within_agreed_limits=check.within)
    claim = NarrationClaim(CAMPAIGN, "c1", "involuntary_reaction", "Ines stumbles back.",
                           subject_id="pc-ines", condition_id="cond-terror")
    review = review_narration(NARRATION, [claim], CONTROLS, conditions=[condition])
    if within:
        assert [a.basis for a in review.accepted] == ["condition:cond-terror"]
    else:
        assert [r.reason for r in review.rejected] == ["outside_agreed_limits"]


def test_check_in_cleared_by_whole_audience():
    zero = SessionZero((Boundary("oto-romance", "player", P2, "romance", "check_in", P2),))
    kiss = NarrationSegment(CAMPAIGN, "seg-kiss", "The duelist leans closer.",
                            frozenset({"romance"}), "proposed")
    consents = [CheckInConsent.for_segment(kiss, "romance", P2)]
    assert decide_presentation(zero, TABLE, [kiss], consents)[0].action == "check_in"
    consents.append(CheckInConsent.for_segment(kiss, "romance", P1))
    assert decide_presentation(zero, TABLE, [kiss], consents)[0].action == "present"


def test_rephrase_cannot_launder_outcome_or_pc_speech():
    """A rephrase only re-renders; the replacement passes review_narration again."""
    decision = decide_request(BoundaryRequest(CAMPAIGN, "r1", P1, "rephrase", ("seg-duel",),
                                              "make the attack land instead"), TABLE, [DUEL])
    assert (decision.rerender, decision.preserved_events) == (("seg-duel",), ("e-duel",))
    assert decision.withdrawn_proposals == decision.skip_presentation == ()

    lunge = PlayerContribution(CAMPAIGN, "act-lunge", "scene-1", "turn-1", "pc-ines", P1,
                               "action", "I lunge at the duelist")
    resolution = Resolution(CAMPAIGN, "act-lunge", "miss")
    for segment_id in decision.rerender:
        replacement = [
            NarrationClaim(CAMPAIGN, f"{segment_id}/hit", "outcome", "Ines's lunge lands.",
                           subject_id="pc-ines", attempt_id="act-lunge", result="hit"),
            NarrationClaim(CAMPAIGN, f"{segment_id}/yield", "voluntary_speech",
                           '"I yield," Ines says.', subject_id="pc-ines"),
            NarrationClaim(CAMPAIGN, f"{segment_id}/miss", "outcome",
                           "The duelist turns the lunge aside.", subject_id="pc-ines",
                           attempt_id="act-lunge", result="miss"),
        ]
        review = review_narration(NARRATION, replacement, CONTROLS, contributions=[lunge],
                                  resolutions=[resolution])
        assert {r.claim.claim_id: r.reason for r in review.rejected} == {
            f"{segment_id}/hit": "contradicts_resolution",
            f"{segment_id}/yield": "pc_authorship"}
        assert [a.claim.claim_id for a in review.accepted] == [f"{segment_id}/miss"]
        # The rephrased text is a new depiction: earlier check-in consent doesn't carry over.
        rephrased = NarrationSegment(CAMPAIGN, segment_id, "The duelist turns the lunge aside.",
                                     DUEL.themes, "accepted", "e-duel")
        assert rephrased.digest != DUEL.digest
