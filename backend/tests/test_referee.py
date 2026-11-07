"""Sprint 047 referee: each category, how findings are classified, what goes unchecked, and the boundaries."""

from __future__ import annotations

import pickle
import random
from dataclasses import replace

import pytest

from grimoire.core import Knowledge, Moment, Proposition, Subject
from grimoire.core.authority import Correction, Resolution
from grimoire.referee import ContinuityClaim, Evidence, Facts, RefereeError, flag_mislabel, referee
from grimoire.staging import Placement

from referee_fixtures import (
    ANA, C, CARRIED, CASK, DAGGER, DRAUGHT, HIDING, KEY, MARA, MUG, NOW, ODA, PELL, SATCHEL, TOMA, authority,
    claim, dagger, facts, holdings, satchel, knowledge, labelled, ref, stage, stairs_state,
)

TEXT = "Oda does it."


def one(kind: str, *, text: str = TEXT, words: str = "does it", actor=ODA, f: Facts | None = None, **fields):
    report = referee(text, facts=f or facts(), claims=(claim(text, words, "k-1", kind, actor, **fields),))
    if report.findings:
        (finding,) = report.findings
        return finding
    if report.unchecked:
        (item,) = report.unchecked
        return item[1]
    assert report.clean == ("k-1",)
    return None


# -- presence ---------------------------------------------------------------------------------------------

def test_an_actor_who_left_cannot_act_in_the_room():
    finding = one("present", actor=PELL)
    assert (finding.category, finding.reason, finding.classification) == ("presence", "off_stage", "contradiction")
    assert finding.evidence == (Evidence("stage", f"absence:{PELL}", "off_stage:via=front-door"),)


def test_an_actor_outside_the_cast_is_not_in_the_scene():
    finding = one("present", actor=ref("stranger"))
    assert finding.reason == "not_cast" and finding.material


def test_an_assumed_absence_is_only_uncertain():
    from grimoire.staging import Absence
    finding = one("present", actor=PELL, f=facts(stage=stage(absences=(Absence(PELL, basis="assumed"),))))
    assert finding.classification == "uncertain" and finding.assumed == (f"absence:{PELL}",)


def test_present_actors_check_clean():
    assert one("present", actor=MARA) is None


# -- staging ----------------------------------------------------------------------------------------------

def test_seeing_through_a_shut_door_and_whispering_through_an_open_one():
    shut = one("sees", target=TOMA)
    assert (shut.reason, shut.material) == ("door_shut", True)
    opened = facts(stage=stage(door="open"))
    assert one("sees", target=TOMA, f=opened) is None
    assert one("hears", target=TOMA, channel="speech", f=opened) is None
    whisper = one("hears", target=TOMA, channel="whisper", f=opened)
    assert whisper.reason == "out_of_earshot"
    assert one("hears", target=TOMA, channel="telepathy") is None


def test_a_door_only_assumed_shut_makes_the_answer_uncertain():
    finding = one("sees", target=TOMA, f=facts(stage=stage(door="closed", door_basis="assumed")))
    assert finding.classification == "uncertain" and finding.assumed == ("way:cellar-door",)


def test_reaching_for_what_another_holds():
    finding = one("reaches", target=KEY)
    assert finding.reason == "held_by_other"
    assert Evidence("stage", f"placement:{KEY}", f"held:{MARA}:in=taproom") in finding.evidence


def test_label_errors_and_untracked_things_are_never_passed():
    assert one("reaches", target=MARA) == "bad_label:is_actor"
    assert one("reaches", target=ref("ghost", "item")) == "untracked"
    assert one("hears", target=MUG, channel="speech") == "bad_label:not_actor"
    assert one("sees", target=ODA) == "bad_label:is_self"  # 041 answers yes, but the label is wrong
    assert one("hears", target=ODA, channel="speech") == "bad_label:is_self"
    assert one("present", f=facts(stage=None)) == "no_stage"


# -- possession -------------------------------------------------------------------------------------------

def test_spending_what_is_left_and_what_is_not():
    assert one("spends", item=DRAUGHT, count=1, f=facts(holdings=(holdings(draughts=1),))) is None
    finding = one("spends", item=DRAUGHT, count=2, f=facts(holdings=(holdings(draughts=1),)))
    assert finding.reason == "insufficient"
    assert finding.evidence[0] == Evidence("holdings", f"{ODA}#{DRAUGHT}", "count=1:needs=2")
    assert one("spends", item=DRAUGHT, count=1, f=facts(holdings=())) == "no_holdings"


def test_one_narration_cannot_spend_the_same_stack_twice():
    text = "Oda uncorks a draught and then drinks a second draught."
    f = facts(holdings=(holdings(draughts=1),))
    first = claim(text, "uncorks a draught", "s-1", "spends", item=DRAUGHT, count=1)
    second = claim(text, "drinks a second draught", "s-2", "spends", item=DRAUGHT, count=1)
    for order in ((first, second), (second, first)):
        report = referee(text, facts=f, claims=order)
        assert report.clean == ("s-1",)
        (finding,) = report.material
        assert (finding.claim_id, finding.reason) == ("s-2", "insufficient")
        assert Evidence("holdings", f"{ODA}#{DRAUGHT}", "count=1:needs=2") in finding.evidence
    figurative = replace(first, mode="figurative")  # a turn of phrase spends nothing
    assert referee(text, facts=f, claims=(figurative, second)).clean == ("s-1", "s-2")
    unsure = referee(text, facts=f, claims=(replace(first, certain=False), second))
    assert [(x.claim_id, x.classification) for x in unsure.findings] == [("s-2", "uncertain")]


def test_a_stack_in_a_container_the_actor_carries_makes_a_shortfall_uncertain():
    from grimoire.holdings import Holdings
    in_satchel = Holdings(SATCHEL, 1, 0, {DRAUGHT: 3}, ())
    finding = one("spends", item=DRAUGHT, count=1, f=facts(holdings=(holdings(), in_satchel)))
    assert (finding.reason, finding.classification) == ("insufficient", "uncertain")
    assert Evidence("holdings", f"{SATCHEL}#{DRAUGHT}", "count=3:carried") in finding.evidence
    too_few = one("spends", item=DRAUGHT, count=4, f=facts(holdings=(holdings(), in_satchel)))
    assert too_few.classification == "contradiction"  # 0 of her own and 3 carried still fall short of 4
    pouch = Holdings(ref("pouch", "item"), 1, 0, {DRAUGHT: 1}, ())  # no 034 record: possibly carried
    stacks = (holdings(), in_satchel, pouch)
    forward = one("spends", item=DRAUGHT, count=4, f=facts(holdings=stacks))
    assert forward == one("spends", item=DRAUGHT, count=4, f=facts(holdings=stacks[::-1]))
    assert forward.classification == "uncertain"
    assert Evidence("holdings", f"{ref('pouch', 'item')}#{DRAUGHT}", "count=1:custody_unknown") in forward.evidence


def test_a_unique_item_is_wielded_only_from_the_actors_own_custody():
    assert one("wields", target=DAGGER) is None  # in Oda's satchel
    finding = one("wields", target=DAGGER, actor=MARA)
    assert finding.reason == "not_holder"
    assert Evidence("possession", f"{DAGGER}@rev1", f"holder={SATCHEL}") in finding.evidence
    assert Evidence("possession", f"{SATCHEL}", f"holder={ODA}") in finding.evidence  # the container followed
    assert one("wields", target=DAGGER, f=facts(possessions=(dagger(TOMA),))) .reason == "not_holder"
    assert one("wields", target=KEY) == "no_possession"
    looped = facts(possessions=(dagger(), satchel(DAGGER)))  # inconsistent records prove nothing
    assert one("wields", target=DAGGER, actor=MARA, f=looped) == "custody_unreadable"
    no_satchel = facts(possessions=(dagger(),))  # the satchel's own record is missing: maybe she carries it
    assert one("wields", target=DAGGER, f=no_satchel) == "custody_unreadable"


# -- knowledge --------------------------------------------------------------------------------------------

def test_only_an_actor_who_holds_a_proposition_may_reveal_it():
    assert one("knows", actor=TOMA, proposition_id=HIDING.proposition_id) is None
    assert one("knows", actor=MARA, proposition_id=HIDING.proposition_id) is None  # she sent it
    finding = one("knows", proposition_id=HIDING.proposition_id)
    assert finding.reason == "never_told" and finding.material
    assert one("knows", proposition_id="p-missing") == "unknown_proposition"
    assert one("knows", proposition_id=HIDING.proposition_id, f=facts(knowledge=None)) == "no_knowledge"


def test_an_unconfirmed_report_is_not_taken_for_a_secret():
    from grimoire.core import Report
    base = knowledge()
    overheard = Report(C, "r-oda", ODA, HIDING.proposition_id, "overheard", Moment(5), "director", confirmed=False)
    finding = one("knows", proposition_id=HIDING.proposition_id,
                  f=facts(knowledge=Knowledge(C, base.events, base.propositions, evidence=(*base.evidence, overheard))))
    assert (finding.reason, finding.classification) == ("unconfirmed_evidence", "uncertain")
    assert Evidence("knowledge", "evidence:r-oda", "unconfirmed") in finding.evidence


def test_a_line_heard_this_turn_is_not_taken_for_a_secret():
    finding = one("knows", proposition_id=HIDING.proposition_id,
                  f=facts(heard=((ODA, HIDING.proposition_id),)))
    assert (finding.reason, finding.classification) == ("heard_unadmitted", "uncertain")


def test_evidence_about_the_same_subject_is_an_uncertain_inference():
    upstairs = Proposition(C, "p-upstairs", "The strongbox went up to the rooms.", Moment(2), "director",
                           about=(Subject("event", CARRIED),))
    base = knowledge()
    ledger = Knowledge(C, base.events, (*base.propositions, upstairs), evidence=base.evidence)
    finding = one("knows", proposition_id="p-upstairs", f=facts(knowledge=ledger))
    assert (finding.reason, finding.classification) == ("related_evidence", "uncertain")
    assert Evidence("knowledge", "evidence:p-oda", "about_a_subject") in finding.evidence


def test_knowledge_is_judged_as_of_the_review():
    early = facts(as_of=3, stage=stage(as_of=3))
    assert one("knows", actor=TOMA, proposition_id=HIDING.proposition_id, f=early).reason == "never_told"


# -- outcome ----------------------------------------------------------------------------------------------

def test_narration_agrees_with_a_recorded_resolution():
    assert one("resolves", attempt_id="c-5", result="failure") is None
    finding = one("resolves", attempt_id="c-5", result="success")
    assert finding.reason == "contradicts_resolution"
    assert finding.evidence == (Evidence("resolution", "attempt:c-5", "result=failure"),)
    assert one("resolves", attempt_id="c-9", result="success") == "no_resolution"
    split = facts(resolutions=(Resolution(C, "c-5", "failure"), Resolution(C, "c-5", "success")))
    assert one("resolves", attempt_id="c-5", result="success", f=split).classification == "uncertain"


def test_an_obstacle_is_overcome_only_when_its_state_says_so():
    finding = one("overcomes", obstacle_id="stairs")
    assert finding.reason == "not_overcome"
    bribed = one("overcomes", obstacle_id="stairs", approach_id="bribe")
    assert bribed.reason == "prevented"  # 040's escalation: she reported the last bribe
    assert Evidence("obstacle", "fact:reported", "escalation:prevents") in bribed.evidence
    overcome = facts(obstacles=(stairs_state(status="overcome", overcome_by="a-2"),))
    assert one("overcomes", obstacle_id="stairs", actor=MARA, f=overcome) is None
    assert one("overcomes", obstacle_id="stairs", approach_id="sneak", actor=MARA, f=overcome) is None
    assert one("overcomes", obstacle_id="stairs", approach_id="force", actor=MARA, f=overcome).reason == "other_approach"
    diverged = facts(obstacles=(stairs_state(status="diverged"),))
    assert one("overcomes", obstacle_id="stairs", f=diverged).classification == "uncertain"
    assert one("overcomes", obstacle_id="gate") == "no_obstacle"


def test_an_obstacle_overcome_by_someone_else_is_not_the_actors_win():
    toma_won = facts(obstacles=(stairs_state(status="overcome", overcome_by="a-2", winner=TOMA),))
    finding = one("overcomes", obstacle_id="stairs", actor=MARA, f=toma_won)
    assert finding.reason == "other_actor" and finding.material
    assert Evidence("obstacle", "attempt:a-2", f"actor={TOMA}") in finding.evidence
    unframed = facts(obstacles=(stairs_state(status="overcome", overcome_by="a-2", framed=False),))
    assert one("overcomes", obstacle_id="stairs", actor=MARA, f=unframed) == "no_winning_frame"


# -- classification ---------------------------------------------------------------------------------------

def test_how_a_contradiction_is_softened_and_never_hidden():
    assert one("present", actor=PELL, certain=False).classification == "uncertain"
    assert one("present", actor=PELL, mode="unreliable").classification == "unreliable"
    figurative = one("present", actor=PELL, mode="figurative")
    assert figurative.classification == "metaphor" and not figurative.material
    assert one("present", actor=MARA, mode="figurative") is None  # true even read literally: nothing to say


# -- anchors, facts and inputs ----------------------------------------------------------------------------

def test_a_label_that_does_not_match_the_prose_is_unchecked():
    wrong = ContinuityClaim(C, "k-1", "present", PELL, (0, 3), "Ida")
    report = referee(TEXT, facts=facts(), claims=(wrong,))
    assert report.unchecked == (("k-1", "bad_anchor"),) and report.findings == ()
    past = ContinuityClaim(C, "k-2", "present", PELL, (10, 20), "x" * 10)
    assert referee(TEXT, facts=facts(), claims=(past,)).unchecked == (("k-2", "bad_anchor"),)


@pytest.mark.parametrize("fields, reason", [
    (dict(kind="teleports"), "unknown_kind"),
    (dict(kind="reaches"), "missing_field"),
    (dict(kind="present", target=MUG), "invalid_field"),
    (dict(kind="hears", target=TOMA, channel="shout"), "invalid_field"),
    (dict(kind="spends", item=DRAUGHT, count=0), "invalid_field"),
    (dict(kind="present", mode="poetic"), "invalid_field"),
    (dict(kind="present", certain="yes"), "invalid_field"),
    (dict(kind="present", quote="Oda does"), "invalid_field"),
    (dict(kind="present", actor=ref("x").__class__("other", "character", "oda")), "campaign_mismatch"),
    (dict(kind="resolves", attempt_id="c-5", result="ok\nthen"), "invalid_field"),
])
def test_malformed_claims_are_refused(fields, reason):
    values = dict(campaign_id=C, claim_id="k-1", kind="present", actor=ODA, span=(0, 3), quote="Oda")
    values.update(fields)
    with pytest.raises(RefereeError) as error:
        ContinuityClaim(**values)
    assert error.value.reason == reason
    assert pickle.loads(pickle.dumps(error.value)).reason == reason


def test_facts_are_one_campaign_at_one_sequence():
    with pytest.raises(RefereeError, match="stale_stage"):
        facts(stage=stage(as_of=NOW - 1))
    with pytest.raises(RefereeError, match="duplicate_id"):
        facts(holdings=(holdings(), holdings()))
    with pytest.raises(RefereeError, match="campaign_mismatch"):
        facts(resolutions=(Resolution("other", "c-5", "failure"),))
    with pytest.raises(RefereeError, match="invalid_field"):
        facts(heard=((ODA,),))


def test_review_inputs_are_checked():
    good = claim(TEXT, "Oda", "k-1", "present")
    with pytest.raises(RefereeError, match="invalid_text"):
        referee("", facts=facts(), claims=(good,))
    with pytest.raises(RefereeError, match="duplicate_id"):
        referee(TEXT, facts=facts(), claims=(good, good))
    with pytest.raises(RefereeError, match="too_many_claims"):
        referee(TEXT, facts=facts(), claims=tuple(claim(TEXT, "Oda", f"k-{i}", "present") for i in range(257)))
    with pytest.raises(RefereeError, match="campaign_mismatch"):
        referee(TEXT, facts=Facts("other", NOW), claims=(good,))
    with pytest.raises(RefereeError, match="needs_authority"):
        referee(TEXT, facts=facts(), authorship=(labelled(TEXT, "Oda does it.", "a-1", "world_fact", None),))
    for bad in (None, good, "k-1"):
        with pytest.raises(RefereeError, match="invalid_field"):
            referee(TEXT, facts=facts(), claims=bad)
    from grimoire.core.authority import ControlAssignment
    from referee_fixtures import CONTROLS
    ambiguous = authority(controls=(*CONTROLS, ControlAssignment(C, "mara", "human:bo")))
    with pytest.raises(RefereeError) as refused:
        referee(TEXT, facts=facts(), authorship=(labelled(TEXT, "Oda does it.", "a-1", "commitment", "mara"),),
                authority=ambiguous)
    assert refused.value.args == ("invalid_authority",) and "mara" not in str(refused.value)


def test_a_report_is_independent_of_claim_order():
    text = "Pell waves, Oda sees Toma and drinks a draught, and Toma knows the hiding place."
    claims = [claim(text, "Pell waves", "k-1", "present", PELL),
              claim(text, "Oda sees Toma", "k-2", "sees", target=TOMA),
              claim(text, "drinks a draught", "k-3", "spends", item=DRAUGHT, count=1),
              claim(text, "Toma knows the hiding place", "k-4", "knows", TOMA, proposition_id=HIDING.proposition_id),
              claim(text, "Oda", "k-5", "present")]
    expected = referee(text, facts=facts(), claims=claims)
    for seed in range(5):
        random.Random(seed).shuffle(claims)
        assert referee(text, facts=facts(), claims=claims) == expected
    assert [f.claim_id for f in expected.findings] == ["k-1", "k-2", "k-3"]
    words = ("Pell waves", "Oda sees Toma", "drinks a draught")
    authored = [labelled(text, w, f"a-{i}", "world_fact", None) for i, w in enumerate(words)]
    with_authorship = referee(text, facts=facts(), authorship=authored, authority=authority())
    assert referee(text, facts=facts(), authorship=authored[::-1], authority=authority()) == with_authorship


# -- mislabels --------------------------------------------------------------------------------------------

def test_pc_speech_bundled_into_an_outcome_is_routed_and_npc_voices_are_not():
    text = "Mara slips past and says \"easy\". Oda grumbles. Mara says hello."
    items = (labelled(text, "Mara slips past and says \"easy\".", "a-1", "outcome", "mara", voiced_by="mara",
                      attempt_id="c-5", result="failure"),
             labelled(text, "Oda grumbles.", "a-2", "voluntary_speech", "oda", voiced_by="oda"),
             labelled(text, "Mara says hello.", "a-3", "voluntary_speech", "mara", voiced_by="mara"))
    report = referee(text, facts=facts(), authorship=items, authority=authority())
    assert [(m.claim_id, m.reason) for m in report.mislabels] == [("a-1", "speech_in_outcome")]
    flags, refused = flag_mislabel(report, (Correction(C, "a-1", "director"), Correction(C, "a-2", ANA),
                                            Correction(C, "a-1", "human:bob")), authority().controls)
    assert flags == ()
    assert [(r.correction.claim_id, r.reason) for r in refused] == [
        ("a-1", "not_controller"), ("a-2", "unknown_claim"), ("a-1", "not_controller")]


def test_authorship_reviews_exactly_the_anchored_words():
    from grimoire.core.authority import NarrationClaim
    from grimoire.referee import LabelledClaim
    text = "Mara says she will pay double."
    with pytest.raises(RefereeError, match="invalid_field"):
        LabelledClaim(NarrationClaim(C, "a-1", "voluntary_speech", "No, I will not pay.", subject_id="mara"),
                      (0, len(text)), text)


def test_the_same_authority_in_another_order_gives_the_same_review():
    from grimoire.core.authority import Delegation
    grants = [Delegation(C, f"d{i}", "mara", ANA, frozenset({"commitment"}), "campaign", "custom") for i in (1, 2)]
    text = "Mara agrees to pay double."
    authored = (labelled(text, text, "a-1", "commitment", "mara"),)
    reports = [referee(text, facts=facts(), authorship=authored, authority=authority(delegations=order))
               for order in (grants, grants[::-1])]
    assert reports[0] == reports[1]
    assert [a.basis for a in reports[0].authorship.accepted] == ["delegation:d1"]


def test_unanchored_authorship_claims_are_not_reviewed():
    text = "Oda grumbles."
    moved = labelled("Oda rumbles!.", "Oda rumbles!.", "a-1", "voluntary_speech", "oda")  # other words
    report = referee(text, facts=facts(), authorship=(moved,), authority=authority())
    assert report.unchecked == (("a-1", "bad_anchor"),) and report.authorship.accepted == ()


def test_a_stage_with_extra_things_still_answers():
    placed = facts(stage=stage(extra=(Placement(ref("lamp", "item"), area="cellar"),)))
    assert one("sees", target=ref("lamp", "item"), f=placed).reason == "door_shut"
    assert CASK and SATCHEL  # fixtures in use
