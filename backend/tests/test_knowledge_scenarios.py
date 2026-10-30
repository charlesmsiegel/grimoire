"""012 proving scenarios: who may know about a private betrayal, and when.

All synthetic. Distinctive tokens (``evt-q7``, ``SUMMARY-TOKEN``,
``TRUTH-TOKEN``) prove absence by substring over ``repr`` and every field.
"""

import pytest

from grimoire.core import (
    DIRECTOR, AuthorityError, ControlAssignment, Event, InstanceRef, Knowledge, KnowledgeError,
    Proposition, Ruling, Subject, actor_knowledge, director_knowledge, knowledge_about,
    player_knowledge, standing,
)
from knowledge_fixtures import (
    BETRAYAL, C, DUKE, GUILD, GUILDMASTER, INES, LIE, ORRIN, SUMMARY, TAVI, TRUTH, WREN, actor, at,
    believes, betrayal_knowledge, betrayal_records, disclosure, leaks, told,
)

SECRETS = (SUMMARY, "evt-q7", TRUTH)
CAST = (INES, ORRIN, TAVI, WREN, GUILDMASTER, GUILD, DUKE)


def buckets(view):
    return (view.evidence, view.sent, view.current, view.history, view.inherited)


# --- A: a private betrayal witnessed by two of several -------------------------------

def test_a_witnesses_see_only_their_own_percepts():
    knowledge = betrayal_knowledge()
    ines, orrin = (actor_knowledge(knowledge, who, as_of=1) for who in (INES, ORRIN))
    assert [e.content for e in ines.evidence] == ["a sealed charter changing hands in the chapel"]
    assert [e.content for e in orrin.evidence] == ["two cloaked figures and the clink of coin"]
    assert ines.evidence[0].kind == "perception" and ines.evidence[0].proposition_id is None
    assert "clink of coin" not in repr(ines) and "sealed charter" not in repr(orrin)


@pytest.mark.parametrize("as_of", [0, 1, 2, 4, 9])
def test_a_no_view_of_anyone_ever_carries_the_event_or_the_truth_unless_told(as_of):
    knowledge = betrayal_knowledge()
    for who in CAST:
        view = actor_knowledge(knowledge, who, as_of=as_of)
        allowed = (TRUTH,) if who in (TAVI, INES) and as_of >= 4 else ()  # Ines tells Tavi at 4
        assert leaks(view, *SECRETS) == list(allowed)
        for subject in (BETRAYAL, Subject("instance", str(GUILD)), Subject("instance", str(DUKE))):
            about = knowledge_about(knowledge, who, subject, as_of=as_of)
            assert leaks(about, *SECRETS) == [t for t in allowed if subject != Subject("instance", str(GUILD))]


def test_a_absent_actors_and_mere_participants_know_nothing_at_the_event():
    knowledge = betrayal_knowledge()
    for who in (TAVI, WREN, GUILDMASTER, GUILD, DUKE):
        assert buckets(actor_knowledge(knowledge, who, as_of=1)) == ((), (), (), (), None)


def test_a_knowledge_about_the_event_is_only_the_witnesses_percepts():
    knowledge = betrayal_knowledge()
    ines = knowledge_about(knowledge, INES, BETRAYAL, as_of=1)
    assert [e.evidence_id for e in ines.evidence] == ["p-ines"]
    assert leaks(ines, "guildmaster", "guild/") == []  # participants never surface
    for who in CAST:
        for subject in (Subject("instance", str(GUILD)), Subject("instance", str(GUILDMASTER))):
            assert buckets(knowledge_about(knowledge, who, subject, as_of=9)) == ((), (), (), (), None)


def test_a_participation_and_group_membership_grant_nothing():
    records = betrayal_records()
    records["events"].append(Event(C, "council", "the guild council met", at(1), "director",
                                   participants=(GUILD, INES, TAVI)))
    knowledge = Knowledge(C, **records)
    assert actor_knowledge(knowledge, TAVI, as_of=1) == actor_knowledge(betrayal_knowledge(), TAVI, as_of=1)


# --- B: a distorted report is believed without becoming canon -----------------------

def test_b_a_lie_is_believed_but_never_established():
    knowledge = betrayal_knowledge()
    tavi = actor_knowledge(knowledge, TAVI, as_of=2)
    assert [(e.kind, e.content, e.attributed_to) for e in tavi.evidence] == [("report", LIE, ORRIN)]
    assert [(b.proposition_id, b.stance) for b in tavi.current] == [("duke-innocent", "believed")]
    assert leaks(tavi, *SECRETS, "refuted", "established") == []
    director = director_knowledge(knowledge, as_of=2, requested_by=DIRECTOR)
    by_id = {p.proposition.proposition_id: (p.standing, p.ruling_id) for p in director.propositions}
    assert by_id == {"duke-betrayed": ("established", "rule-truth"), "duke-innocent": ("refuted", "rule-lie")}
    assert any(b.belief_id == "b-tavi-1" for b in director.beliefs)


def test_b_the_liar_remembers_sending_the_lie():
    sent = actor_knowledge(betrayal_knowledge(), ORRIN, as_of=2).sent
    assert [(s.recipient, s.proposition_id, s.text, s.sent_at) for s in sent] == [
        (TAVI, "duke-innocent", LIE, at(2))]


def test_b_standing_without_rulings_is_unasserted_then_reported():
    records = betrayal_records()
    records["rulings"] = []
    knowledge = Knowledge(C, **records)
    assert standing(knowledge, "duke-innocent", as_of=1) == "unasserted"  # not yet even proposed
    assert standing(knowledge, "duke-innocent", as_of=2) == "reported"
    assert standing(knowledge, "duke-betrayed", as_of=3) == "unasserted"  # only witnessed, never told
    assert standing(knowledge, "duke-betrayed", as_of=4) == "reported"


def test_b_believing_never_changes_standing():
    without = Knowledge(C, **{**betrayal_records(), "beliefs": [], "rulings": []})
    with_beliefs = Knowledge(C, **{**betrayal_records(), "rulings": []})
    for proposition in ("duke-innocent", "duke-betrayed"):
        for as_of in range(6):
            assert standing(without, proposition, as_of=as_of) == standing(with_beliefs, proposition, as_of=as_of)


def test_b_an_unconfirmed_report_alone_makes_a_proposition_reported():
    records = betrayal_records()
    records["rulings"] = []
    records["evidence"].append(told("r-maybe", WREN, "duke-betrayed", 3, confirmed=False))
    assert standing(Knowledge(C, **records), "duke-betrayed", as_of=3) == "reported"


# --- C: correction keeps history ---------------------------------------------------

def test_c_superseded_belief_stays_current_until_the_correction_happens():
    knowledge = betrayal_knowledge()
    before = actor_knowledge(knowledge, TAVI, as_of=3)
    assert [b.belief_id for b in before.current] == ["b-tavi-1"] and before.history == ()
    after = actor_knowledge(knowledge, TAVI, as_of=4)
    assert [(b.belief_id, b.stance) for b in after.current] == [("b-tavi-2", "rejected"),
                                                                ("b-tavi-3", "believed")]
    assert [(b.belief_id, b.stance) for b in after.history] == [("b-tavi-1", "believed")]
    assert after.current[0].supersedes == "b-tavi-1"


# --- D: time --------------------------------------------------------------------------

def test_d_nothing_is_known_before_it_happens():
    knowledge = betrayal_knowledge()
    for who in CAST:
        assert buckets(actor_knowledge(knowledge, who, as_of=0)) == ((), (), (), (), None)
    assert buckets(actor_knowledge(knowledge, TAVI, as_of=1)) == ((), (), (), (), None)
    director = director_knowledge(knowledge, as_of=0, requested_by=DIRECTOR)
    assert director.events == director.evidence == director.propositions == ()


# --- E: an unconfirmed transcript line --------------------------------------------------

def test_e_unconfirmed_delivery_is_director_only():
    knowledge = betrayal_knowledge(evidence=[told("r-offstage", WREN, "duke-innocent", 3, sender=ORRIN,
                                                  confirmed=False)])
    assert actor_knowledge(knowledge, WREN, as_of=9).evidence == ()
    flagged = [e for e in director_knowledge(knowledge, as_of=9, requested_by=DIRECTOR).evidence
               if e.evidence_id == "r-offstage"]
    assert len(flagged) == 1 and flagged[0].confirmed is False


# --- F: the player's view --------------------------------------------------------------

CONTROLS = (ControlAssignment(C, "ines", "player-one"), ControlAssignment(C, "orrin", DIRECTOR),
            ControlAssignment(C, "tavi", DIRECTOR))


def test_f_player_sees_their_pc_and_timed_table_disclosures_only():
    knowledge = betrayal_knowledge(disclosures=[disclosure("d-late", 10)],
                                   events=[Event(C, "plot", "PLOT-TOKEN", at(1), "director")])
    early = player_knowledge(knowledge, "player-one", CONTROLS, as_of=5)
    assert [a.actor for a in early.actors] == [INES] and early.table_events == ()
    assert leaks(early, SUMMARY, "PLOT-TOKEN", "clink of coin", LIE) == []
    late = player_knowledge(knowledge, "player-one", CONTROLS, as_of=10)
    assert [(t.event_id, t.summary, t.moment) for t in late.table_events] == [("evt-q7", SUMMARY, at(10))]
    assert leaks(late.actors, SUMMARY) == []  # the PC still does not know it


def test_f_npc_era_beliefs_are_inherited_not_the_pcs_own():
    knowledge = betrayal_knowledge(beliefs=[believes("b-ines-npc", INES, "duke-betrayed", "believed", 1,
                                                     ["p-ines"])])
    view = player_knowledge(knowledge, "player-one", CONTROLS, as_of=5).actors[0]
    assert view.current == () and [b.belief_id for b in view.inherited] == ["b-ines-npc"]
    assert actor_knowledge(knowledge, INES, as_of=5).inherited is None  # not computed there


@pytest.mark.parametrize("player, controls", [
    (DIRECTOR, CONTROLS),
    ("player-two", CONTROLS),
    ("player-one", (ControlAssignment("campaign-b", "ines", "player-one"),)),
])
def test_f_director_and_controllers_of_nothing_are_refused(player, controls):
    with pytest.raises(AuthorityError):
        player_knowledge(betrayal_knowledge(), player, controls, as_of=5)


def test_f_contested_control_fails_closed_and_controls_may_be_a_generator():
    contested = (ControlAssignment(C, "ines", "player-one"), ControlAssignment(C, "ines", "player-two"))
    with pytest.raises(AuthorityError):
        player_knowledge(betrayal_knowledge(), "player-one", contested, as_of=5)
    view = player_knowledge(betrayal_knowledge(), "player-one", (c for c in CONTROLS), as_of=5)
    assert [a.actor for a in view.actors] == [INES]


def test_f_player_actors_are_sorted():
    controls = (ControlAssignment(C, "tavi", "player-one"), ControlAssignment(C, "ines", "player-one"))
    view = player_knowledge(betrayal_knowledge(), "player-one", controls, as_of=5)
    assert [a.actor for a in view.actors] == [INES, TAVI]


# --- H, I, L: senders, interception and timing ----------------------------------------------

def test_h_a_forged_letter_shows_the_apparent_author_never_the_real_one():
    knowledge = betrayal_knowledge(evidence=[told("r-letter", WREN, "duke-innocent", 3, sender=ORRIN,
                                                  attributed_to=GUILDMASTER, addressed=True, sent_at=3,
                                                  channel="written")])
    wren = actor_knowledge(knowledge, WREN, as_of=3)
    assert wren.evidence[0].attributed_to == GUILDMASTER
    assert leaks(wren, "orrin") == []


def test_i_an_overheard_report_is_not_in_the_speakers_sent_list():
    knowledge = betrayal_knowledge(evidence=[told("r-overheard", INES, "duke-innocent", 2, sender=ORRIN,
                                                  attributed_to=ORRIN, channel="overheard")])
    assert [e.content for e in actor_knowledge(knowledge, INES, as_of=2).evidence if e.kind == "report"] == [LIE]
    assert [s.recipient for s in actor_knowledge(knowledge, ORRIN, as_of=9).sent] == [TAVI]


def test_l_a_sent_letter_appears_when_sent_and_reveals_nothing_on_delivery():
    knowledge = betrayal_knowledge(evidence=[
        told("r-letter", WREN, "duke-innocent", 9, sender=ORRIN, attributed_to=ORRIN,
             addressed=True, sent_at=3, channel="written"),
        told("r-lost", GUILDMASTER, "duke-innocent", 9, sender=ORRIN, attributed_to=ORRIN,
             addressed=True, sent_at=3, channel="written", confirmed=False),
    ])
    assert [s.evidence_id for s in actor_knowledge(knowledge, ORRIN, as_of=2).sent] == ["r-orrin-tavi"]
    at3, at8, at9 = (actor_knowledge(knowledge, ORRIN, as_of=n) for n in (3, 8, 9))
    assert [(s.evidence_id, s.sent_at) for s in at3.sent] == [
        ("r-orrin-tavi", at(2)), ("r-letter", at(3)), ("r-lost", at(3))]
    assert buckets(at3) == buckets(at8) == buckets(at9)  # delivery and loss look alike to the sender
    assert {type(s).__name__ for s in at3.sent} == {"SentReport"}
    assert not hasattr(at3.sent[0], "confirmed") and not hasattr(at3.sent[0], "moment")
    assert actor_knowledge(knowledge, WREN, as_of=8).evidence == ()


# --- K: told-about is not traversal ---------------------------------------------------------

def test_k_a_false_membership_is_only_a_held_proposition():
    cult = actor("cult", "group")
    in_cult = Subject("instance", str(cult))
    knowledge = betrayal_knowledge(
        propositions=[Proposition(C, "orrin-in-cult", "Orrin serves the cult", at(3), "director",
                                  about=(Subject("instance", str(ORRIN)), in_cult))],
        rulings=[Ruling(C, "rule-cult", "orrin-in-cult", "refuted", at(3), "director")],
        events=[Event(C, "cult-rite", "CULT-SECRET", at(2), "director", participants=(cult, WREN))],
        evidence=[told("r-cult", TAVI, "orrin-in-cult", 3, sender=INES, attributed_to=INES,
                       addressed=True, sent_at=3)],
    )
    about = knowledge_about(knowledge, TAVI, in_cult, as_of=5)
    assert [e.evidence_id for e in about.evidence] == ["r-cult"]
    assert about.current == about.history == about.sent == ()
    assert leaks(about, "CULT-SECRET", "cult-rite", "refuted", "wren") == []


def test_k_unknown_and_unheld_subjects_are_indistinguishable():
    knowledge = betrayal_knowledge()
    real = knowledge_about(knowledge, TAVI, Subject("event", "evt-q7"), as_of=1)
    ghost = knowledge_about(knowledge, TAVI, Subject("event", "no-such-event"), as_of=1)
    assert buckets(real) == buckets(ghost) == ((), (), (), (), None)
    with pytest.raises(KnowledgeError) as caught:
        knowledge_about(knowledge, TAVI, Subject("instance", "campaign:campaign-b/character/x"), as_of=1)
    assert caught.value.reason == "campaign_mismatch"


def test_k_knowledge_about_filters_every_bucket():
    knowledge = betrayal_knowledge()
    about = knowledge_about(knowledge, TAVI, Subject("instance", str(DUKE)), as_of=4)
    full = actor_knowledge(knowledge, TAVI, as_of=4)
    assert buckets(about) == buckets(full)  # everything Tavi holds is about the duke
    nothing = knowledge_about(knowledge, TAVI, Subject("relation", "unrelated"), as_of=4)
    assert buckets(nothing) == ((), (), (), (), None)


# --- independence, determinism and input validation ------------------------------------------

def test_an_actor_projection_depends_only_on_its_own_records():
    base = actor_knowledge(betrayal_knowledge(), TAVI, as_of=9)
    noisy = betrayal_knowledge(
        rulings=[Ruling(C, "rule-later", "duke-innocent", "unknown", at(5), "director")],
        beliefs=[believes("b-ines", INES, "duke-innocent", "rejected", 2, ["p-ines"]),
                 believes("b-orrin", ORRIN, "duke-betrayed", "believed", 1, ["p-orrin"])],
        evidence=[told("r-ines-orrin", ORRIN, "duke-innocent", 3, sender=INES, addressed=True, sent_at=3),
                  told("r-tavi-mutter", WREN, "duke-innocent", 3, sender=TAVI, attributed_to=TAVI),
                  told("r-tavi-unconfirmed", TAVI, "duke-betrayed", 5, sender=ORRIN, confirmed=False)],
        disclosures=[disclosure("d-1", 2)],
    )
    assert actor_knowledge(noisy, TAVI, as_of=9) == base


def test_director_records_are_ordered_by_moment_then_id():
    director = director_knowledge(betrayal_knowledge(), as_of=9, requested_by=DIRECTOR)
    assert [e.evidence_id for e in director.evidence] == ["p-ines", "p-orrin", "r-orrin-tavi", "r-ines-tavi"]
    assert [b.belief_id for b in director.beliefs] == ["b-tavi-1", "b-tavi-2", "b-tavi-3"]


def test_beliefs_never_show_authorship_or_control_to_the_actor():
    knowledge = betrayal_knowledge(beliefs=[believes("b-ines", INES, "duke-betrayed", "believed", 1,
                                                     ["p-ines"], authored_by="player-one")])
    view = actor_knowledge(knowledge, INES, as_of=9)  # Ines is an NPC again; her PC era is not hers to see
    assert view.current and leaks(view, "player-one", DIRECTOR) == []
    assert not hasattr(view.current[0], "authored_by")


def test_f_superseded_npc_era_beliefs_are_inherited_history():
    knowledge = betrayal_knowledge(beliefs=[
        believes("b-ines-1", INES, "duke-betrayed", "suspected", 1, ["p-ines"]),
        believes("b-ines-2", INES, "duke-betrayed", "believed", 3, ["p-ines"], supersedes="b-ines-1",
                 authored_by="player-one"),
    ])
    view = player_knowledge(knowledge, "player-one", CONTROLS, as_of=5).actors[0]
    assert [b.belief_id for b in view.current] == ["b-ines-2"] and view.history == ()
    assert view.inherited == () and [b.belief_id for b in view.inherited_history] == ["b-ines-1"]


def test_f_a_twice_disclosed_event_shows_its_earliest_disclosure():
    knowledge = betrayal_knowledge(disclosures=[disclosure("d-b", 7), disclosure("d-a", 3), disclosure("d-c", 5)])
    table = player_knowledge(knowledge, "player-one", CONTROLS, as_of=9).table_events
    assert [(t.event_id, t.moment) for t in table] == [("evt-q7", at(3))]


@pytest.mark.parametrize("as_of", [True, -1, 1.5, None, "3"])
def test_every_projection_validates_as_of(as_of):
    knowledge = betrayal_knowledge()
    for call in (lambda: actor_knowledge(knowledge, TAVI, as_of=as_of),
                 lambda: knowledge_about(knowledge, TAVI, BETRAYAL, as_of=as_of),
                 lambda: standing(knowledge, "duke-innocent", as_of=as_of),
                 lambda: director_knowledge(knowledge, as_of=as_of, requested_by=DIRECTOR),
                 lambda: player_knowledge(knowledge, "player-one", CONTROLS, as_of=as_of)):
        with pytest.raises(KnowledgeError) as caught:
            call()
        assert caught.value.reason == "invalid_as_of"


def test_projections_refuse_malformed_inputs_as_knowledge_errors():
    knowledge = betrayal_knowledge()
    for call in (lambda: actor_knowledge(None, TAVI, as_of=1),
                 lambda: standing(knowledge, f"{TRUTH} as an id", as_of=1),
                 lambda: standing(knowledge, ["x"], as_of=1),
                 lambda: player_knowledge(knowledge, "player-one", [object()], as_of=1)):
        with pytest.raises(KnowledgeError) as caught:
            call()
        assert TRUTH not in str(caught.value)


def test_projections_refuse_other_campaigns_and_unknown_propositions():
    knowledge = betrayal_knowledge()
    with pytest.raises(KnowledgeError) as caught:
        actor_knowledge(knowledge, InstanceRef("campaign-b", "character", "tavi"), as_of=1)
    assert caught.value.reason == "campaign_mismatch"
    with pytest.raises(KnowledgeError) as caught:
        standing(knowledge, "ghost", as_of=1)
    assert caught.value.reason == "unknown_proposition"


def test_director_view_needs_the_director_perspective():
    with pytest.raises(AuthorityError):
        director_knowledge(betrayal_knowledge(), as_of=9, requested_by="player-one")


def test_director_sees_a_letter_from_when_it_was_sent():
    knowledge = betrayal_knowledge(evidence=[told("r-letter", WREN, "duke-innocent", 9, sender=ORRIN,
                                                  addressed=True, sent_at=3)])
    at3 = director_knowledge(knowledge, as_of=3, requested_by=DIRECTOR)
    assert "r-letter" in {e.evidence_id for e in at3.evidence}


def test_non_character_holders_have_projections_too():
    blade = actor("whisper-blade", "item")
    knowledge = betrayal_knowledge(evidence=[told("r-blade", blade, "duke-innocent", 3)])
    assert [e.evidence_id for e in actor_knowledge(knowledge, blade, as_of=3).evidence] == ["r-blade"]
