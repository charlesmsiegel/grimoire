"""Session-zero boundary policy over labelled narration segments.

Synthetic fixtures only. Owners: values and changes, scoped rulings and
anonymised projections, presentation decisions, mid-session requests, and
agreed limits for imposed conditions.
"""

import inspect
from dataclasses import FrozenInstanceError

import pytest

from grimoire.core import (
    DIRECTOR, Boundary, BoundaryAuthorityError, BoundaryChange, BoundaryContext,
    BoundaryRequest, CheckInConsent, LimitCheck, NarrationSegment, RequestDecision,
    SegmentDecision, SessionZero, VisibleBoundary, agreed_limits, decide_presentation,
    decide_request, owner_boundaries, rule_themes, visible_boundaries,
)

CAMPAIGN = "campaign-a"
P1, P2, P3 = "human:p1", "human:p2", "human:p3"
NOTE = "secret note about why"
CTX = BoundaryContext(CAMPAIGN, frozenset({P1, P2}))


def player(theme, treatment, owner=P1, boundary_id=None, **kwargs):
    return Boundary(boundary_id or f"b-{owner}-{theme}", "player", owner, theme, treatment,
                    owner, **kwargs)


def group(theme, treatment, scope="campaign", scope_id=CAMPAIGN, set_by=P2,
          boundary_id=None, **kwargs):
    return Boundary(boundary_id or f"g-{scope}-{theme}", scope, scope_id, theme, treatment,
                    set_by, **kwargs)


def config(*boundaries):
    return SessionZero(tuple(boundaries))


def segment(segment_id, themes, status="accepted", event_id=None, campaign=CAMPAIGN):
    if status == "accepted" and event_id is None:
        event_id = f"e-{segment_id}"
    return NarrationSegment(campaign, segment_id, f"text of {segment_id}",
                            None if themes is None else frozenset(themes), status, event_id)


def set_(boundary, by=None):
    return BoundaryChange("set", by or boundary.set_by, boundary=boundary)


# --- values and changes ---------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    dict(theme="Graphic_Violence"), dict(theme=" gore"), dict(theme="gore\n"), dict(theme=None),
    dict(theme="gore--x"), dict(theme=""), dict(scope="table"), dict(treatment="ban"),
    dict(set_by=DIRECTOR, scope_id=DIRECTOR), dict(set_by=P2), dict(attributed="yes"),
    dict(agreed_by=frozenset({P2})), dict(boundary_id=" "), dict(private_note=None),
])
def test_boundary_validation(kwargs):
    base = dict(boundary_id="b1", scope="player", scope_id=P1, theme="gore",
                treatment="avoid", set_by=P1)
    with pytest.raises(ValueError):
        Boundary(**{**base, **kwargs})


def test_group_boundary_validation():
    with pytest.raises(ValueError):
        group("gore", "allow", set_by=DIRECTOR)
    with pytest.raises(ValueError):
        group("gore", "allow", agreed_by=frozenset({DIRECTOR}))
    with pytest.raises(ValueError):
        group("gore", "allow", agreed_by=P1)  # a string is not a set of players


def test_agreed_by_includes_setter():
    b = group("gore", "allow", agreed_by=[P1])
    assert b.agreed_by == frozenset({P1, P2})
    assert player("gore", "avoid").agreed_by == frozenset()


def test_values_are_frozen():
    with pytest.raises(FrozenInstanceError):
        player("gore", "avoid").treatment = "allow"
    with pytest.raises(FrozenInstanceError):
        config().boundaries = ()


def test_session_zero_rejects_duplicate_ids_without_naming_them():
    with pytest.raises(ValueError) as error:
        config(player("gore", "avoid", boundary_id="x"), player("spiders", "skip", boundary_id="x"))
    assert P1 not in str(error.value) and "'x'" not in str(error.value)
    with pytest.raises(ValueError):
        SessionZero(("not a boundary",))


def test_session_zero_stores_tuple_and_hides_contents_in_repr():
    zero = SessionZero([player("gore", "avoid", private_note=NOTE)])
    assert isinstance(zero.boundaries, tuple)
    assert repr(zero) == "SessionZero(1 boundaries)"
    assert NOTE not in repr(zero.boundaries[0])


def test_apply_set_replace_withdraw():
    original = config()
    one = original.apply(set_(player("gore", "fade_to_black")))
    assert original.boundaries == ()
    two = one.apply(set_(player("gore", "skip")))
    assert [b.treatment for b in two.boundaries] == ["skip"]
    assert two.apply(BoundaryChange("withdraw", P1, boundary_id="b-human:p1-gore")).boundaries == ()


def test_replace_keeps_position():
    zero = config(player("gore", "avoid"), player("spiders", "skip"))
    replaced = zero.apply(set_(player("gore", "fade_to_black")))
    assert [(b.theme, b.treatment) for b in replaced.boundaries] == [
        ("gore", "fade_to_black"), ("spiders", "skip")]


def test_apply_refusals_are_indistinguishable():
    zero = config(player("gore", "avoid"), group("war", "allow"))
    attempts = [
        BoundaryChange("withdraw", P2, boundary_id="b-human:p1-gore"),
        BoundaryChange("withdraw", P2, boundary_id="missing"),
        BoundaryChange("set", P2, boundary=player("gore", "allow")),  # P2 posing as P1
        BoundaryChange("set", P2, boundary=player("gore", "allow", owner=P2,
                                                  boundary_id="b-human:p1-gore")),
        BoundaryChange("set", P1, boundary=group("war", "avoid", set_by=P1)),
        BoundaryChange("agree", P1, boundary_id="b-human:p1-gore"),  # player scope
        BoundaryChange("agree", P1, boundary_id="missing"),
        BoundaryChange("unagree", P2, boundary_id="g-campaign-war"),  # setter
    ]
    for change in attempts:
        with pytest.raises(BoundaryAuthorityError) as error:
            zero.apply(change)
        assert str(error.value) == "not your boundary"
    assert not isinstance(BoundaryAuthorityError(), OSError)


def test_non_owner_shape_change_is_authority_error():
    zero = config(player("gore", "avoid"))
    moved = Boundary("b-human:p1-gore", "player", P2, "spiders", "allow", P2)
    with pytest.raises(BoundaryAuthorityError):
        zero.apply(set_(moved))


def test_replace_cannot_move_scope_or_theme():
    zero = config(player("gore", "avoid"))
    with pytest.raises(ValueError):
        zero.apply(set_(player("spiders", "avoid", boundary_id="b-human:p1-gore")))


def test_p2_cannot_agree_for_p1():
    with pytest.raises(ValueError):
        config().apply(set_(group("fear", "allow", agreed_by=frozenset({P1}))))
    zero = config(group("fear", "allow"))
    with pytest.raises(ValueError):
        zero.apply(set_(group("fear", "allow", agreed_by=frozenset({P1}))))


def test_agree_unagree_self_only():
    zero = config(group("fear", "allow"))
    agreed = zero.apply(BoundaryChange("agree", P1, boundary_id="g-campaign-fear"))
    assert agreed.boundaries[0].agreed_by == frozenset({P1, P2})
    left = agreed.apply(BoundaryChange("unagree", P1, boundary_id="g-campaign-fear"))
    assert left.boundaries[0].agreed_by == frozenset({P2})


def test_treatment_change_resets_agreement():
    zero = config(group("fear", "fade_to_black", agreed_by=frozenset({P1})))
    same = zero.apply(set_(group("fear", "fade_to_black", private_note="x")))
    assert same.boundaries[0].agreed_by == frozenset({P1, P2})
    changed = zero.apply(set_(group("fear", "allow")))
    assert changed.boundaries[0].agreed_by == frozenset({P2})


def test_change_validation():
    with pytest.raises(ValueError):
        BoundaryChange("set", P1, boundary_id="x")
    with pytest.raises(ValueError):
        BoundaryChange("withdraw", P1, boundary=player("gore", "avoid"))
    with pytest.raises(ValueError):
        BoundaryChange("withdraw", DIRECTOR, boundary_id="x")
    with pytest.raises(ValueError):
        BoundaryChange("erase", P1, boundary_id="x")


# --- context, rulings and projections --------------------------------------------

def test_context_validation():
    for audience in (frozenset(), frozenset({DIRECTOR}), P1, frozenset({" "})):
        with pytest.raises(ValueError):
            BoundaryContext(CAMPAIGN, audience)
    with pytest.raises(ValueError):
        BoundaryContext(CAMPAIGN, frozenset({P1}), world_id="")


def test_scope_matching():
    ctx = BoundaryContext(CAMPAIGN, frozenset({P1}), world_id="w1", setting_id="s1")
    zero = config(
        group("a", "avoid", scope="setting", scope_id="s1"),
        group("b", "avoid", scope="setting", scope_id="s2"),
        group("c", "avoid", scope="world", scope_id="w1"),
        group("d", "avoid", scope="world", scope_id="w2"),
        group("e", "avoid", scope="campaign", scope_id=CAMPAIGN),
        group("f", "avoid", scope="campaign", scope_id="other"),
        player("g", "avoid", owner=P1),
        player("h", "avoid", owner=P3),
    )
    ruled = {r.theme: r.treatment for r in rule_themes(zero, ctx, set("abcdefgh"))}
    assert ruled == dict(a="avoid", b="unset", c="avoid", d="unset", e="avoid", f="unset",
                         g="avoid", h="unset")


@pytest.mark.parametrize("call", [
    lambda zero, ctx: rule_themes(zero, ctx, {"gore"}),
    lambda zero, ctx: visible_boundaries(zero, ctx),
    lambda zero, ctx: decide_presentation(zero, ctx, ()),
    lambda zero, ctx: agreed_limits({"gore"}, zero, ctx, controller=DIRECTOR, target_id="c1"),
])
@pytest.mark.parametrize("scope", ["world", "setting"])
def test_missing_world_or_setting_fails_closed(call, scope):
    zero = config(group("gore", "avoid", scope=scope, scope_id="x"))
    with pytest.raises(ValueError):
        call(zero, CTX)


def test_unset_is_not_allow():
    (ruling,) = rule_themes(config(), CTX, {"gore"})
    assert (ruling.treatment, ruling.sources, ruling.conflict) == ("unset", (), False)
    (ruling,) = rule_themes(config(group("gore", "allow")), CTX, {"gore"})
    assert ruling.treatment == "allow"


def test_rule_themes_rejects_non_slugs():
    with pytest.raises(ValueError):
        rule_themes(config(), CTX, {"Gore"})


def test_strictest_wins_and_conflict_reported():
    zero = config(group("body_horror", "allow", attributed=True), player("body_horror", "avoid", owner=P2))
    (ruling,) = rule_themes(zero, CTX, {"body_horror"})
    assert ruling.treatment == "avoid" and ruling.conflict
    campaign, mine = ruling.sources
    assert (campaign.scope, campaign.set_by, campaign.scope_id) == ("campaign", P2, CAMPAIGN)
    assert (mine.scope, mine.scope_id, mine.set_by, mine.boundary_id) == ("player", None, None, None)


def test_same_player_two_treatments():
    zero = config(player("gore", "allow", boundary_id="b1"), player("gore", "skip", boundary_id="b2"))
    (ruling,) = rule_themes(zero, CTX, {"gore"})
    assert ruling.treatment == "skip" and ruling.conflict


def test_unattributed_group_boundary_hides_owner():
    (shown,) = visible_boundaries(config(group("gore", "allow")), CTX)
    assert (shown.scope_id, shown.set_by, shown.boundary_id) == (CAMPAIGN, None, None)


def test_attributed_shows_owner():
    (shown,) = visible_boundaries(config(player("gore", "avoid", attributed=True)), CTX)
    assert (shown.scope_id, shown.set_by, shown.boundary_id) == (P1, P1, "b-human:p1-gore")


def test_sources_order_ignores_hidden_fields():
    a = player("gore", "avoid", owner=P1, boundary_id="zz")
    b = player("gore", "skip", owner=P2, boundary_id="aa")
    swapped_a = player("gore", "avoid", owner=P2, boundary_id="aa")
    swapped_b = player("gore", "skip", owner=P1, boundary_id="zz")
    assert rule_themes(config(a, b), CTX, {"gore"}) == rule_themes(
        config(swapped_b, swapped_a), CTX, {"gore"})


def test_visible_order_independent_of_config_order():
    items = [player("gore", "avoid"), group("war", "allow"), player("spiders", "skip", owner=P2),
             group("gore", "fade_to_black")]
    assert visible_boundaries(config(*items), CTX) == visible_boundaries(
        config(*reversed(items)), CTX)


def test_owner_view_includes_note_visible_does_not():
    zero = config(player("gore", "avoid", private_note=NOTE), player("spiders", "skip", owner=P2))
    (mine,) = owner_boundaries(zero, P1)
    assert mine.private_note == NOTE
    assert NOTE not in repr(visible_boundaries(zero, CTX))
    assert not any(hasattr(v, "private_note") for v in visible_boundaries(zero, CTX))


def test_owner_view_rejects_director():
    with pytest.raises(ValueError):
        owner_boundaries(config(), DIRECTOR)


# --- presentation ------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    dict(status="accepted", event_id=None), dict(status="proposed", event_id="e1"),
    dict(status="done"), dict(themes=frozenset({"Graphic_Violence"})),
    dict(themes=frozenset({" gore"})), dict(themes="gore"), dict(text=""),
])
def test_segment_validation(kwargs):
    base = dict(campaign_id=CAMPAIGN, segment_id="s1", text="t", themes=frozenset(),
                status="accepted", event_id="e1")
    with pytest.raises(ValueError):
        NarrationSegment(**{**base, **kwargs})


def test_consent_validation():
    with pytest.raises(ValueError):
        CheckInConsent(CAMPAIGN, "s1", "gore", DIRECTOR)
    with pytest.raises(ValueError):
        CheckInConsent(CAMPAIGN, "s1", "Gore", P1)


@pytest.mark.parametrize("treatment, status, action, notices", [
    ("allow", "proposed", "present", ()),
    ("allow", "accepted", "present", ()),
    ("check_in", "proposed", "check_in", ()),
    ("check_in", "accepted", "check_in", ()),
    ("fade_to_black", "proposed", "fade_to_black", ()),
    ("fade_to_black", "accepted", "fade_to_black", ()),
    ("skip", "proposed", "withhold_proposal", ("skipped_proposal",)),
    ("skip", "accepted", "skip", ()),
    ("avoid", "proposed", "withhold_proposal", ("avoided_theme",)),
    ("avoid", "accepted", "skip", ("canon_touches_avoided_theme",)),
])
def test_decision_table(treatment, status, action, notices):
    (decision,) = decide_presentation(config(player("gore", treatment)), CTX,
                                      [segment("s1", {"gore"}, status)])
    assert (decision.action, decision.notices) == (action, notices)
    assert decision.preserved_event_id == ("e-s1" if status == "accepted" else None)


def test_strictest_theme_decides_segment():
    zero = config(player("gore", "fade_to_black"), player("spiders", "avoid"))
    (decision,) = decide_presentation(zero, CTX, [segment("s1", {"gore", "spiders", "war"})])
    assert decision.action == "skip"
    assert [r.theme for r in decision.rulings] == ["gore", "spiders", "war"]


def test_unlabelled_holds_only_under_limits():
    (held,) = decide_presentation(config(player("gore", "fade_to_black")), CTX,
                                  [segment("s1", None)])
    assert (held.action, held.notices, held.rulings) == ("hold_unlabelled", ("unlabelled",), ())
    assert held.preserved_event_id == "e-s1"
    (shown,) = decide_presentation(config(group("gore", "allow")), CTX, [segment("s1", None)])
    assert shown.action == "present"


def test_labelled_empty_presents():
    (decision,) = decide_presentation(config(player("gore", "avoid")), CTX, [segment("s1", ())])
    assert (decision.action, decision.rulings) == ("present", ())


def test_mixed_unset_and_allowed():
    (decision,) = decide_presentation(config(group("war", "allow")), CTX,
                                      [segment("s1", {"war", "rain"})])
    assert decision.action == "present"
    assert {r.theme: r.treatment for r in decision.rulings} == {"rain": "unset", "war": "allow"}


def test_check_in_cleared_only_by_whole_audience():
    zero = config(player("romance", "check_in"))
    seg = [segment("s1", {"romance"}, "proposed")]

    def decide(*givers, target="s1", theme="romance"):
        consents = [CheckInConsent(CAMPAIGN, target, theme, who) for who in givers]
        (decision,) = decide_presentation(zero, CTX, seg, consents)
        return decision

    assert decide(P1).action == "check_in"
    assert decide(P1, P3).action == "check_in"  # outsider's consent is ignored
    assert decide(P1, P2, target="s2").action == "check_in"
    cleared = decide(P1, P2)
    assert (cleared.action, cleared.notices) == ("present", ("check_in_cleared",))
    assert cleared.rulings[0].treatment == "check_in"


def test_presentation_rejects_cross_campaign_and_duplicates():
    with pytest.raises(ValueError):
        decide_presentation(config(), CTX, [segment("s1", (), campaign="other")])
    with pytest.raises(ValueError):
        decide_presentation(config(), CTX, [segment("s1", ()), segment("s1", ())])
    with pytest.raises(ValueError):
        decide_presentation(config(), CTX, [], [CheckInConsent("other", "s1", "gore", P1)])


def test_decision_has_no_canon_edit_fields():
    assert set(SegmentDecision.__slots__) == {
        "segment_id", "action", "rulings", "notices", "preserved_event_id"}
    assert set(RequestDecision.__slots__) == {
        "request_id", "status", "reason", "halt", "skip_presentation", "withdrawn_proposals",
        "rerender", "preserved_events", "direction"}


# --- requests ------------------------------------------------------------------------

SEGMENTS = (segment("a1", {"war"}), segment("p1", {"war"}, "proposed"), segment("a2", ()))


@pytest.mark.parametrize("kwargs", [
    dict(kind="pause", segment_ids=("a1",)), dict(kind="skip", segment_ids=()),
    dict(kind="redirect", segment_ids=()), dict(kind="rephrase", segment_ids=()),
    dict(kind="skip", segment_ids=("a1", "a1")), dict(kind="pause", direction="later"),
    dict(kind="skip", segment_ids=("a1",), direction="later"), dict(requested_by=DIRECTOR),
    dict(kind="veto"), dict(kind="skip", segment_ids="a1"), dict(kind="skip", segment_ids=("",)),
])
def test_request_validation(kwargs):
    base = dict(campaign_id=CAMPAIGN, request_id="r1", requested_by=P1, kind="pause")
    with pytest.raises(ValueError):
        BoundaryRequest(**{**base, **kwargs})


def request(kind, *targets, by=P1, direction="", campaign=CAMPAIGN):
    return BoundaryRequest(campaign, "r1", by, kind, tuple(targets), direction)


def test_pause_halts_only():
    decision = decide_request(request("pause"), CTX, SEGMENTS)
    assert (decision.status, decision.reason, decision.halt) == ("accepted", "accepted", True)
    assert decision.skip_presentation == decision.withdrawn_proposals == decision.rerender == ()


def test_skip_mixed_targets():
    decision = decide_request(request("skip", "p1", "a1"), CTX, SEGMENTS)
    assert decision.skip_presentation == ("a1",)
    assert decision.withdrawn_proposals == ("p1",)
    assert decision.preserved_events == ("e-a1",)
    assert not decision.halt


def test_redirect_mixed_targets():
    decision = decide_request(request("redirect", "a1", "p1", direction="the morning after"),
                              CTX, SEGMENTS)
    assert decision.withdrawn_proposals == ("p1",)
    assert decision.preserved_events == ("e-a1",)
    assert decision.skip_presentation == decision.rerender == ()
    assert decision.direction == "the morning after"


def test_rephrase_rerenders_and_preserves():
    decision = decide_request(request("rephrase", "a1", "p1"), CTX, SEGMENTS)
    assert decision.rerender == ("a1", "p1")
    assert decision.preserved_events == ("e-a1",)
    assert decision.withdrawn_proposals == ()


@pytest.mark.parametrize("req, reason", [
    (request("skip", "missing", campaign="other", by=P3), "cross_campaign"),
    (request("skip", "missing", by=P3), "not_participant"),
    (request("skip", "a1", "missing"), "unknown_segment"),
])
def test_refusals_change_nothing(req, reason):
    decision = decide_request(req, CTX, SEGMENTS)
    assert decision == RequestDecision("r1", "refused", reason)
    assert (decision.halt, decision.direction, decision.preserved_events) == (False, "", ())


def test_requester_independent():
    first = decide_request(request("redirect", "p1", "a1", by=P1, direction="d"), CTX, SEGMENTS)
    second = decide_request(request("redirect", "p1", "a1", by=P2, direction="d"), CTX, SEGMENTS)
    assert first == second
    assert P1 not in repr(first) and P2 not in repr(second)


def test_request_rejects_bad_supplied_segments():
    with pytest.raises(ValueError):
        decide_request(request("pause"), CTX, (segment("a1", ()), segment("a1", ())))
    with pytest.raises(ValueError):
        decide_request(request("pause"), CTX, (segment("a1", (), campaign="other"),))


def test_request_takes_no_configuration():
    assert list(inspect.signature(decide_request).parameters) == ["request", "context", "segments"]


# --- agreed limits ---------------------------------------------------------------------

def limits(zero, *themes, controller=P1, ctx=CTX, consents=()):
    return agreed_limits(frozenset(themes), zero, ctx, controller=controller, target_id="c1",
                         consents=consents)


def test_agreed_limits_validation():
    with pytest.raises(ValueError):
        limits(config())
    with pytest.raises(ValueError):
        limits(config(), "Fear")
    with pytest.raises(ValueError):
        limits(config(), "fear", controller=None)
    with pytest.raises(ValueError):
        limits(config(), "fear", consents=[CheckInConsent("other", "c1", "fear", P1)])


def test_director_controlled_is_npc():
    assert limits(config(player("fear", "avoid")), "fear", controller=DIRECTOR) == LimitCheck(
        True, "npc")


def test_own_allow_is_agreed():
    assert limits(config(player("fear", "allow")), "fear") == LimitCheck(True, "agreed")
    assert limits(config(), "fear") == LimitCheck(False, "unset")


def test_group_allow_needs_controller_in_agreed_by():
    assert limits(config(group("fear", "allow")), "fear") == LimitCheck(False, "not_agreed")
    zero = config(group("fear", "allow")).apply(
        BoundaryChange("agree", P1, boundary_id="g-campaign-fear"))
    assert limits(zero, "fear").within


def test_controller_boundaries_apply_outside_audience():
    outside = BoundaryContext(CAMPAIGN, frozenset({P2}))
    assert limits(config(player("fear", "avoid")), "fear", ctx=outside).reason == "avoided"
    assert limits(config(player("fear", "allow")), "fear", ctx=outside).within


def test_audience_member_avoid_applies_only_while_present():
    zero = config(player("fear", "allow"), player("fear", "avoid", owner=P2))
    assert limits(zero, "fear").reason == "avoided"
    alone = BoundaryContext(CAMPAIGN, frozenset({P1}))
    assert limits(zero, "fear", ctx=alone).within


@pytest.mark.parametrize("treatment", ["fade_to_black", "skip"])
def test_fade_and_skip_limit_depiction_not_occurrence(treatment):
    assert limits(config(player("fear", treatment)), "fear").within


def test_check_in_needs_controller_consent():
    zero = config(player("fear", "check_in"))
    assert limits(zero, "fear").reason == "needs_check_in"
    assert limits(zero, "fear", consents=[CheckInConsent(CAMPAIGN, "c1", "fear", P2)]).reason \
        == "needs_check_in"
    assert limits(zero, "fear", consents=[CheckInConsent(CAMPAIGN, "c2", "fear", P1)]).reason \
        == "needs_check_in"
    assert limits(zero, "fear", consents=[CheckInConsent(CAMPAIGN, "c1", "fear", P1)]).within


def test_reason_precedence_is_worst_across_themes():
    zero = config(player("z", "avoid"), player("c", "check_in"), group("n", "allow"))
    assert limits(zero, "a", "z").reason == "avoided"
    assert limits(zero, "a", "c").reason == "needs_check_in"
    assert limits(zero, "a", "n").reason == "unset"
    assert limits(zero, "n").reason == "not_agreed"


def test_recomputation_after_tightening():
    zero = config(player("fear", "allow"))
    assert limits(zero, "fear").within
    tightened = zero.apply(set_(player("fear", "avoid")))
    assert limits(tightened, "fear") == LimitCheck(False, "avoided")


def test_visible_boundary_carries_no_private_fields():
    assert set(VisibleBoundary.__slots__) == {
        "scope", "theme", "treatment", "scope_id", "set_by", "boundary_id"}
