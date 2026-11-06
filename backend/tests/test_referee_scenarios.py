"""Sprint 047's proving scenario: three grounded continuity errors, a harmless metaphor, and a PC claim.

A model's narration has Oda (an NPC) reach through the locked cellar door, mention a secret only Toma was
told and uncork a healing draught she already drank. The referee reports exactly those three as material
contradictions, each citing the records it rests on, and does not object to a metaphor. A separately
labelled claim that speaks for Mara (a PC) goes through 003 and is rejected there; one that carries Mara's
words under Oda's name is routed to mislabel review, which Mara's player can flag. The narration is
never changed.
"""

from __future__ import annotations

from grimoire.core.authority import Correction
from grimoire.referee import Evidence, flag_mislabel, referee

from referee_fixtures import ANA, C, CASK, DRAUGHT, HIDING, MUG, authority, claim, facts, labelled

NARRATION = (
    "Oda's laugh rolls through the taproom like a cart over cobbles. She reaches through the cellar door and "
    "draws a jug straight from the cask. \"And the strongbox key is under the third stair, isn't it?\" she "
    "says, and uncorks her last healing draught. Mara nods and agrees to pay double. Oda tells you Mara will "
    "gladly pay."
)


def review():
    text = NARRATION
    claims = (
        claim(text, "reaches through the cellar door and draws a jug straight from the cask", "k-reach",
              "reaches", target=CASK),
        claim(text, "the strongbox key is under the third stair", "k-secret", "knows",
              proposition_id=HIDING.proposition_id),
        claim(text, "uncorks her last healing draught", "k-draught", "spends", item=DRAUGHT, count=1),
        claim(text, "rolls through the taproom like a cart over cobbles", "k-laugh", "present"),
        claim(text, "like a cart over cobbles", "k-cart", "reaches", target=CASK, mode="figurative"),
    )
    authorship = (
        labelled(text, "Mara nods and agrees to pay double.", "a-mara", "commitment", "mara"),
        labelled(text, "Oda tells you Mara will gladly pay.", "a-oda", "voluntary_speech", "oda",
                 voiced_by="mara"),
    )
    return text, referee(text, facts=facts(), claims=claims, authorship=authorship, authority=authority())


def test_three_grounded_errors_and_no_objection_to_a_metaphor():
    text, report = review()
    material = {finding.claim_id: finding for finding in report.material}
    assert set(material) == {"k-reach", "k-secret", "k-draught"}

    reach = material["k-reach"]
    assert (reach.category, reach.reason) == ("staging", "elsewhere")
    assert Evidence("stage", "way:cellar-door", "locked:established") in reach.evidence
    assert Evidence("stage", f"placement:{CASK}", "in_area:cellar") in reach.evidence

    secret = material["k-secret"]
    assert (secret.category, secret.reason) == ("knowledge", "never_told")
    assert secret.evidence[0].ref == f"proposition:{HIDING.proposition_id}"

    draught = material["k-draught"]
    assert (draught.category, draught.reason) == ("possession", "insufficient")
    assert Evidence("holdings", "op:drink-1", "after=0") in draught.evidence

    for finding in report.material:  # each finding points at the exact words it is about
        assert text[finding.span[0]:finding.span[1]] == finding.quote

    (metaphor,) = (f for f in report.findings if f.claim_id == "k-cart")
    assert metaphor.classification == "metaphor" and not metaphor.material
    assert "k-laugh" in report.clean  # Oda is in the taproom


def test_pc_authorship_is_separate_and_a_mislabel_reaches_the_pc_player():
    _, report = review()
    rejected = {r.claim.claim_id: r.reason for r in report.authorship.rejected}
    assert rejected == {"a-mara": "pc_authorship"}
    (mislabel,) = report.mislabels
    assert (mislabel.claim_id, mislabel.reason, mislabel.actor_id) == ("a-oda", "speaks_for_pc", "mara")
    # 003's own corrections refuse Mara's player here (not_controller); the referee's path does not.
    flags, refused = flag_mislabel(report, (Correction(C, "a-oda", ANA, "I never said that"),),
                                   authority().controls)
    assert [flag.mislabel.claim_id for flag in flags] == ["a-oda"] and refused == ()
    assert "a-oda" in {a.claim.claim_id for a in report.authorship.accepted}  # flagged, never struck


def test_the_narration_is_never_changed():
    text, report = review()
    assert text == NARRATION
    import hashlib
    assert report.text_sha256 == hashlib.sha256(NARRATION.encode()).hexdigest()


def test_the_same_narration_after_the_errors_are_fixed_checks_clean():
    text = "Oda reaches for the mug on the bar and asks Mara about the weather."
    report = referee(text, facts=facts(holdings=()), claims=(
        claim(text, "reaches for the mug", "k-mug", "reaches", target=MUG),
        claim(text, "Oda", "k-here", "present"),
    ))
    assert report.findings == () and report.clean == ("k-here", "k-mug")

