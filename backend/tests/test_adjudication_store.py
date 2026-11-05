"""Fiction-first adjudication on the store (Sprint 029): the proving scenario, authority and fail-closed replay."""

from __future__ import annotations

import pytest

from grimoire.adjudication import AdjudicationError, Decision, Factor
from grimoire.adjudicationstore import AdjudicationLibrary, adjudication_actor_ids
from grimoire.chatstore import campaign_taken_ids
from grimoire.core import DIRECTOR
from grimoire.core.abilities import Abilities, Ability, Cost
from grimoire.core.ability_use import AbilityRuling, assess_attempt
from grimoire.core.authority import AuthorityError
from grimoire.core.entity import EntityInstance, FacetBundle
from grimoire.core.entity_codec import instance_to_payload
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.effects import EffectProposal
from grimoire.effectstore import InvalidEffect, TurnEffectsLibrary
from grimoire.ledgers import LedgerInvalid
from grimoire.security import Authorizer, Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, ProcessLocker, RecordEnvelope, TakenIdsUnlisted
from grimoire.store.ledger import render_batches

import adjudication_fixtures
from ability_fixtures import effective, light_bending, person, thruster_pack
from adjudication_fixtures import (
    ACROBAT, ANA, BOB, CHAT, COLLAPSE, FALL, GAP, IRIS, MARA, Q, SATCHEL, SOLA, SOLA_DEF, Table, ability, bridge, build_library,
    moment, power_frame, vault,
)
from chat_fixtures import Clock, Ids
from ledger_helpers import OWNER

PARTIAL = Decision("partial", "adjudicated", {"acrobat", "collapse"}, {"satchel"})
STRETCH = dict(needs=("light", "force", "flight"), effect_id=None)  # flight by radiation pressure


@pytest.fixture
def table(tmp_path):
    locker = ProcessLocker()
    store = LibraryStore.create(tmp_path / "lib", locker=locker)
    build_library(store)
    seated = Table(store).seat()
    seated.locker = locker
    yield seated
    seated.close()
    seated.store.close()


def _reopen(table: Table) -> Table:
    table.close()
    table.store.close()
    fresh = Table(LibraryStore.open(table.store.root, locker=table.locker))
    fresh.locker = table.locker
    table.store, table.authority, table.key = fresh.store, fresh.authority, fresh.key
    return table


def _reason(call, *args, **kwargs) -> str:
    with pytest.raises(AdjudicationError) as caught:
        call(*args, **kwargs)
    return caught.value.reason


# -- the proving scenario ---------------------------------------------------------------------

def test_mara_vaults_the_collapsing_bridge_and_every_retry_keeps_the_outcome(table):
    library = table.library()
    library.frame(bridge(), contribution=vault(), author=table.player())
    assessment = library.assess(Q, "c10")
    assert (assessment.classification, assessment.allowed) == ("uncertain", ("failure", "partial", "success"))
    # A difficulty invented with the outcome has no cause on record: the fiat is refused, nothing written.
    invented = Decision("failure", "adjudicated", {"rotten-plank"}, {"fall"})
    assert _reason(library.decide, Q, "c10", invented) == "unknown_factor"
    outcome = library.decide(Q, "c10", PARTIAL)
    assert (outcome.classification, outcome.decided_by) == ("uncertain", "app:adjudicator")
    # A retry asks again and gets the same outcome; it can never get another one.
    assert library.decide(Q, "c10", PARTIAL) == outcome
    assert _reason(library.decide, Q, "c10", Decision("success", "adjudicated", {"acrobat"})) == "already_decided"
    assert _reason(library.frame, bridge(factors=(ACROBAT,)), contribution=vault(), author=table.player()) == "already_decided"
    resolution = library.resolutions(Q)
    assert [(r.attempt_id, r.result) for r in resolution] == [("c10", "partial")]
    assert len(table.store.read_ledger(f"adjudication/{Q}").entries) == 2

    _reopen(table)
    again = table.library()
    assert again.outcome(Q, "c10") == outcome and again.resolutions(Q) == resolution
    assert again.decide(Q, "c10", PARTIAL) == outcome


def test_a_frame_is_revised_until_the_decision_and_decided_on_its_latest_revision(table):
    library = table.library()
    library.frame(bridge(factors=(GAP, COLLAPSE)), contribution=vault(), author=table.player())
    assert library.assess(Q, "c10").allowed == ("failure",)
    revised = library.frame(bridge(), contribution=vault(), author=table.player())
    assert revised.revision == 2
    # A revision is the same action: it cannot move to a later moment that admits later facts.
    later = Factor("snap", "hinders", "A plank snaps.", 11, "event:e-snap")
    moved = bridge(factors=(ACROBAT, GAP, later), at=moment(12, "t10"))
    assert _reason(library.frame, moved, contribution=vault(), author=table.player()) == "frame_moved"
    library.decide(Q, "c10", PARTIAL)  # supported only by the revision that records her training
    state = _reopen(table).library().state(Q)
    assert [entry.revision for entry in state.frames["c10"]] == [1, 2]
    assert state.decisions["c10"].revision == 2


# -- who may frame and decide -----------------------------------------------------------------

@pytest.mark.parametrize("frame, contribution, reason", [
    (bridge(), None, "pc_authorship"),  # a PC's action comes from the player
    (bridge(), vault(by=BOB), "invalid_expression"),  # another player's words
    (bridge(), vault(cid="c11"), "invalid_expression"),  # another contribution
    (bridge(), vault(turn="t9"), "invalid_expression"),
    (bridge(), vault(kind="speech"), "invalid_expression"),
    (bridge(actor=IRIS, attempt_id="c10"), vault(actor_id="iris-1"), "unexpected_backing"),
], ids=["no-contribution", "other-player", "other-id", "other-turn", "speech", "npc-backed"])
def test_only_the_controllers_own_action_frames_a_player_character(table, frame, contribution, reason):
    library = table.library()
    assert _reason(library.frame, frame, contribution=contribution, author=table.player()) == reason
    assert library.state(Q).frames == {}


def test_an_actor_outside_the_chat_cannot_act_in_it(table):
    stranger = InstanceRef(Q, "character", "wren-1")
    table.control("wren-1", DIRECTOR)
    assert _reason(table.library().frame, bridge(actor=stranger, attempt_id="w1")) == "not_in_chat"


def test_only_the_adjudication_key_records(table):
    with pytest.raises(AuthorityError):
        AdjudicationLibrary(table.store, table.authority, table.authority.handle_for("app:narrator"))
    foreign = Authorizer(["human:owner"]).handle_for("app:adjudicator", trusted=True)
    with pytest.raises(AuthorityError):
        AdjudicationLibrary(table.store, table.authority, foreign)


def test_consent_is_the_controllers_setback_for_their_own_character(table):
    library = table.library()
    library.frame(bridge(factors=(ACROBAT,)), contribution=vault(), author=table.player())  # unopposed: it would simply succeed
    messy = Decision("partial", "consent", consequences={"satchel"})
    assert _reason(library.decide, Q, "c10", messy) == "not_controller"
    assert _reason(library.decide, Q, "c10", messy, consent=table.player(BOB)) == "not_controller"
    assert _reason(library.decide, Q, "c10", messy,
                   consent=table.authority.handle_for(ANA)) == "not_controller"  # not trusted
    outcome = library.decide(Q, "c10", messy, consent=table.player(ANA))
    assert (outcome.consented_by, outcome.resolution().result) == (ANA, "partial")
    assert _reopen(table).library().outcome(Q, "c10") == outcome


def test_no_one_consents_for_an_actor_no_player_controls(table):
    # A setback the rules would not force needs someone with standing to choose it; the director deciding
    # alone would be an uncaused outcome, so the actor's failure must come from a recorded cause instead.
    library = table.library()
    library.frame(bridge(actor=SOLA, attempt_id="director:s-1"))
    setback = Decision("failure", "consent", consequences={"fall"})
    assert _reason(library.decide, Q, "director:s-1", setback) == "consent_mismatch"
    assert _reason(library.decide, Q, "director:s-1", setback, consent=table.player(ANA)) == "consent_mismatch"
    assert "director:s-1" not in library.state(Q).decisions


def test_a_contribution_is_given_only_through_its_authors_own_handle(table):
    library = table.library()
    assert _reason(library.frame, bridge(), contribution=vault()) == "not_controller"  # a bare value
    assert _reason(library.frame, bridge(), contribution=vault(), author=table.player(BOB)) == "not_controller"
    untrusted = table.authority.handle_for(ANA)
    assert _reason(library.frame, bridge(), contribution=vault(), author=untrusted) == "not_controller"
    assert library.state(Q).frames == {}
    assert library.frame(bridge(), contribution=vault(), author=table.player()).revision == 1


def test_a_change_of_control_before_the_decision_stops_it(table):
    library = table.library()
    library.frame(bridge(), contribution=vault(), author=table.player())
    table.control("mara-1", BOB)
    assert _reason(library.decide, Q, "c10", PARTIAL) == "control_changed"
    table.control("mara-1", ANA)
    assert library.decide(Q, "c10", PARTIAL).resolution().result == "partial"


def test_a_revision_cannot_add_a_hindrance_or_a_stake_the_action_did_not_have(table):
    library = table.library()
    library.frame(bridge(factors=(ACROBAT, GAP), stakes=(FALL,)), contribution=vault(), author=table.player())
    worse = bridge(factors=(ACROBAT, GAP, COLLAPSE), stakes=(FALL,))
    assert _reason(library.frame, worse, contribution=vault(), author=table.player()) == "frame_worsened"
    costlier = bridge(factors=(ACROBAT, GAP), stakes=(FALL, SATCHEL))
    assert _reason(library.frame, costlier, contribution=vault(), author=table.player()) == "frame_worsened"
    # Dropping a hindrance, or adding a recorded fact that favours the action, stays open.
    assert library.frame(bridge(factors=(ACROBAT,), stakes=(FALL,)), contribution=vault(),
                         author=table.player()).revision == 2


def test_a_revision_cannot_drop_an_advantage_or_bring_in_a_power(table):
    library = table.library()
    library.frame(bridge(), contribution=vault(), author=table.player())
    weaker = bridge(factors=(GAP, COLLAPSE))  # her training, dropped, would leave failure alone
    assert _reason(library.frame, weaker, contribution=vault(), author=table.player()) == "frame_worsened"
    flight = ability(MARA, ("flight",), attempt_id="c10", effect_id=None)
    powered = bridge(ability=flight)  # a power she never claimed would make the vault clarify, then impossible
    assert _reason(library.frame, powered, contribution=vault(), author=table.player()) == "frame_moved"
    assert len(library.state(Q).frames["c10"]) == 1


def test_a_directors_attempt_never_shares_a_players_id(table):
    library = table.library()
    # Iris's attempt under Ana's contribution id would bind narration about Mara to Iris's result.
    assert _reason(library.frame, bridge(actor=IRIS, attempt_id="c10")) == "reserved_attempt_id"
    claimed = vault(cid="director:c10")
    assert _reason(library.frame, bridge(attempt_id="director:c10"), contribution=claimed,
                   author=table.player()) == "reserved_attempt_id"
    assert library.state(Q).frames == {}


def test_a_director_actor_handed_to_a_non_human_is_not_decided(table):
    library = table.library()
    library.frame(bridge(actor=SOLA, attempt_id="director:s-1"))
    table.control("sola-1", "app:bot")
    assert _reason(library.decide, Q, "director:s-1", PARTIAL) == "non_human_controller"


def test_an_unstorable_contribution_is_refused_before_anything_is_written(table):
    library = table.library()
    broken = vault("I vault \ud800 the gap")
    assert _reason(library.frame, bridge(), contribution=broken, author=table.player()) == "invalid_field"
    assert library.state(Q).frames == {}


# -- abilities (025) and the handoff to accepted effects (021) --------------------------------

def test_a_stretch_waits_for_a_ruling_then_charges_once_through_an_accepted_effect(table):
    library = table.library()
    sail = ability(SOLA, **STRETCH)
    library.frame(power_frame(sail, intent="Rise on a sail of sunlight."))
    assert library.assess(Q, "director:a-1").classification == "clarify"
    assert _reason(library.decide, Q, "director:a-1", Decision("failure", "rule")) == "needs_clarification"
    assert _reason(library.rule, AbilityRuling("r1", sail, "new_ability", "approved", "director")) == \
        "wrong_classification"
    library.rule(AbilityRuling("r1", sail, "extrapolation", "refused", "director"))
    assert library.assess(Q, "director:a-1").classification == "impossible"
    # The director changes their mind before the outcome: the new ruling supersedes the refusal.
    library.rule(AbilityRuling("r2", sail, "extrapolation", "approved", "director"), supersedes=["r1"])
    assert library.assess(Q, "director:a-1").classification == "uncertain"
    library.decide(Q, "director:a-1", Decision("partial", "adjudicated", {"#ability", "#ability-uncertain"}, {"fall"}))
    assert _reason(library.rule, AbilityRuling("r3", sail, "extrapolation", "refused", "director"),
                   supersedes=["r2"]) == "already_decided"

    used = library.ability_use(Q, "director:a-1")
    assert (used.ruling_id, [cost.id for _, cost in used.charges]) == ("r2", ["eye_strain", "migraine"])
    proposal = EffectProposal(Q, CHAT, moment(), "Sola rises on a sail of sunlight.", (), "director",
                              abilities=(used,))
    library.check_effect(proposal)
    table.authority.add_grant(Grant("g-fx", OWNER, frozenset({Permission.APPLY}), ResourcePath.parse("campaigns"),
                                    OWNER))
    effects = TurnEffectsLibrary(table.store, table.owner, issued=table.authority.issued, clock=Clock(),
                                 new_id=Ids(), owner=OWNER)
    ref = effects.accept(proposal, controls=[]).commit().ref
    accepted, _ = effects.read(ref, requested_by=DIRECTOR)
    assert [(charge.attempt.attempt_id, charge.charges) for charge in accepted.abilities] == \
        [("director:a-1", (("light_bending", "eye_strain"), ("light_bending", "migraine")))]
    assert effects.accept(proposal, controls=[]).commit().ref == ref  # a retry charges nothing more
    again = EffectProposal(Q, CHAT, moment(11, "t11"), "Sola rises again.", (), "director",
                           abilities=(used,))
    with pytest.raises(InvalidEffect) as caught:
        effects.accept(again, controls=[]).commit()
    assert caught.value.reason == "attempt_reused"

    # A caller cannot charge an assessment it made up: only what was adjudicated here.
    made_up = assess_attempt(sail, effective(SOLA_DEF, Q), [AbilityRuling("x", sail, "extrapolation", "approved", "director")])
    forged = EffectProposal(Q, CHAT, moment(), "Sola rises.", (), "director", abilities=(made_up,))
    assert _reason(library.check_effect, forged) == "assessment_mismatch"
    replayed = _reopen(table).library()
    assert replayed.ability_use(Q, "director:a-1") == used
    assert [entry.ruling.ruling_id for entry in replayed.state(Q).rulings] == ["r1", "r2"]


def test_a_cost_reworded_after_the_decision_is_not_charged(table):
    library = table.library()
    sail = ability(SOLA, **STRETCH)
    library.frame(power_frame(sail, intent="Rise on a sail of sunlight."))
    library.rule(AbilityRuling("r1", sail, "extrapolation", "approved", "director"))
    library.decide(Q, "director:a-1", Decision("success", "adjudicated", {"#ability"}))
    assert library.ability_use(Q, "director:a-1").approval == "approved"
    # Same cost id, a harsher price: the decision was made against aching eyes, not blindness.
    base = light_bending(force_limit=False)
    harsher = Ability("light_bending", "Light bending", effects=base.effects, limits=base.limits,
                      costs=[Cost("eye_strain", "Every use costs her sight for good."), *base.costs[1:]],
                      prerequisites=base.prerequisites)
    table.authority.add_grant(Grant("g-cmp", OWNER, frozenset(Permission), ResourcePath.parse("campaigns"), OWNER))
    stored = table.store.load().instances[SOLA]
    edited = EntityInstance(SOLA, SourceRef.parse(stored.envelope.payload["source"]),
                            replaced=FacetBundle([Abilities([harsher])]))
    txn = table.store.transaction()
    txn.put(RecordEnvelope(SOLA, instance_to_payload(edited), name=stored.envelope.name,
                           extra=dict(stored.envelope.extra)), stored.etag)
    txn.commit(table.owner)
    assert _reason(library.ability_use, Q, "director:a-1") == "assessment_changed"


def test_flight_beyond_a_limit_is_impossible_and_no_ruling_changes_that(table):
    library = table.library()
    flight = ability(IRIS, **STRETCH)
    library.frame(power_frame(flight))
    assert library.assess(Q, "director:a-1").classification == "impossible"
    assert _reason(library.rule, AbilityRuling("r1", flight, "extrapolation", "approved", "director")) == \
        "limit_not_approvable"
    library.decide(Q, "director:a-1", Decision("failure", "rule", {"#ability"}, {"fall"}))
    assert _reason(library.ability_use, Q, "director:a-1") == "ability_not_permitted"


def test_a_failed_use_of_a_power_charges_nothing(table):
    library = table.library()
    glare = ability(SOLA, ("light", "glare"), effect_id="dazzle")
    library.frame(power_frame(glare, intent="Dazzle the guard."))
    assert library.assess(Q, "director:a-1").classification == "uncertain"  # a feat: its own uncertainty hinders it
    library.decide(Q, "director:a-1", Decision("failure", "adjudicated", {"#ability-uncertain"}, {"fall"}))
    assert _reason(library.ability_use, Q, "director:a-1") == "action_failed"


def test_rulings_belong_to_a_recorded_frame_labelled_exactly_as_there(table):
    library = table.library()
    sail = ability(SOLA, **STRETCH)
    assert _reason(library.rule, AbilityRuling("r1", sail, "extrapolation", "approved", "director")) == \
        "ruling_mismatch"
    library.frame(power_frame(sail))
    relabelled = ability(SOLA, needs=("light", "flight"), effect_id=None)
    assert _reason(library.rule, AbilityRuling("r1", relabelled, "extrapolation", "approved", "director")) == \
        "ruling_mismatch"
    library.rule(AbilityRuling("r1", sail, "extrapolation", "approved", "director"))
    assert _reason(library.rule, AbilityRuling("r1", sail, "extrapolation", "approved", "director")) == \
        "duplicate_ruling"
    assert _reason(library.rule, AbilityRuling("r2", sail, "extrapolation", "approved", "director"),
                   supersedes=["r9"]) == "unknown_ruling"
    assert _reason(library.ability_use, Q, "director:a-1") == "undecided"


# -- the ledger fails closed ------------------------------------------------------------------

def _forge(table: Table, kind: str, value: dict) -> None:
    name = f"adjudication/{Q}"
    head = table.store.read_ledger(name).head
    entry = {"schema": 1, "kind": kind, "recorded_at": "x", "recorded_by": "app:adjudicator", "value": value}
    parts, data = render_batches(name, head, [entry])[0]
    (table.store.root / "/".join(parts)).write_bytes(data)


@pytest.mark.parametrize("decision, match", [
    ({"result": "success", "basis": "adjudicated", "cites": [], "consequences": []}, "uncited_result"),
    ({"result": "failure", "basis": "rule", "cites": ["gap"], "consequences": ["fall"]}, "rule_mismatch"),
    ({"classification": "straightforward"}, "classification_mismatch"),
    ({"revision": 2}, "stale_frame"),
    ({"result": "failure", "basis": "consent", "cites": [], "consequences": ["fall"], "consented_by": BOB},
     "consent_mismatch"),  # consent from someone other than the player who authored the action
])
def test_a_forged_decision_makes_the_ledger_unreadable(table, decision, match):
    library = table.library()
    library.frame(bridge(), contribution=vault(), author=table.player())
    value = {"attempt_id": "c10", "actor": str(MARA), "revision": 1, "classification": "uncertain",
             "result": "partial", "basis": "adjudicated", "cites": ["acrobat", "gap"], "consequences": ["satchel"],
             "consented_by": None, "ability": None, **decision}
    _forge(table, "decision", value)
    with pytest.raises(LedgerInvalid, match=match):
        _reopen(table).library().state(Q)


def test_a_forged_finding_cannot_turn_a_refusal_into_an_approval(table):
    library = table.library()
    sail = ability(SOLA, **STRETCH)
    library.frame(power_frame(sail, intent="Rise on a sail of sunlight."))
    library.rule(AbilityRuling("r1", sail, "extrapolation", "refused", "director"))
    _forge(table, "decision", {
        "attempt_id": "director:a-1", "actor": str(SOLA), "revision": 1, "classification": "uncertain",
        "result": "success", "basis": "adjudicated", "cites": ["#ability"], "consequences": [],
        "consented_by": None, "ability": {"ability_id": "light_bending", "classification": "extrapolation",
                                          "approval": "approved", "ruling_id": "r1", "uncertain": True,
                                          "charges": []}})
    with pytest.raises(LedgerInvalid, match="ruling_mismatch"):
        _reopen(table).library().state(Q)


def test_a_second_decision_on_record_is_refused_on_replay(table):
    library = table.library()
    library.frame(bridge(), contribution=vault(), author=table.player())
    library.decide(Q, "c10", PARTIAL)
    _forge(table, "decision", {"attempt_id": "c10", "actor": str(MARA), "revision": 1,
                               "classification": "uncertain", "result": "success", "basis": "adjudicated",
                               "cites": ["acrobat"], "consequences": [], "consented_by": None, "ability": None})
    with pytest.raises(LedgerInvalid, match="already_decided"):
        _reopen(table).library().state(Q)


# -- ids and campaign reservation -------------------------------------------------------------

def test_the_ledger_reserves_the_ids_of_the_actors_it_names(table):
    library = table.library()
    library.frame(bridge(), contribution=vault(), author=table.player())
    library.frame(bridge(actor=SOLA, attempt_id="director:s-1", factors=(Factor("x", "hinders", "x", 1, "s"),)))
    assert adjudication_actor_ids(table.store, Q) == {"mara-1", "sola-1"}
    assert {"mara-1", "sola-1"} <= campaign_taken_ids(table.store, Q)
    assert table.store.campaign_authority_presence("q-9") == "absent"


def test_an_unreadable_ledger_fails_allocation_closed(table):
    table.library().frame(bridge(), contribution=vault(), author=table.player())
    folder = table.store.root / "authority" / "adjudication" / Q
    next(folder.iterdir()).write_bytes(b"not json")
    with pytest.raises(TakenIdsUnlisted):
        adjudication_actor_ids(table.store, Q)


def test_an_adjudication_ledger_keeps_a_campaign_id_taken(tmp_path):
    store = LibraryStore.create(tmp_path / "lib", locker=ProcessLocker())
    table = Table(store)
    entry = {"schema": 1, "kind": "frame", "recorded_at": "x", "recorded_by": "app:adjudicator", "value": {}}
    parts, data = render_batches("adjudication/q-7", None, [entry])[0]
    path = store.root / "/".join(parts)
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    assert store.campaign_authority_presence("q-7") == "present"
    table.close()
    store.close()


PACKED_SOLA = person("sola", "Sola", light_bending(), thruster_pack())


@pytest.fixture
def packed_table(tmp_path, monkeypatch):
    """The table with Sola also wearing a thruster pack, so flight by light and force is a combination."""
    monkeypatch.setattr(adjudication_fixtures, "SOLA_DEF", PACKED_SOLA)
    locker = ProcessLocker()
    store = LibraryStore.create(tmp_path / "lib", locker=locker)
    build_library(store)
    seated = Table(store).seat()
    seated.locker = locker
    yield seated
    seated.close()
    seated.store.close()


def test_a_combination_is_approved_only_by_a_ruling_naming_its_exact_terms_then_charged_once(packed_table):
    """CMS's Wave 3 call (025/029): a combination is approvable case by case by a recorded director ruling
    naming the assessment's exact ``terms``; the approved attempt is decided and charged on both powers."""
    library = packed_table.library()
    lift = ability(SOLA, ("light", "force", "flight"), attempt_id="director:combo-1", effect_id=None,
                   conditions=("ambient_light", "thruster_pack/fuel"))
    library.frame(power_frame(lift, intent="Rise on light and thrust."))
    assessed = library.assess(Q, lift.attempt_id)
    assert (assessed.classification, assessed.ability.classification, assessed.ability.approval) == \
        ("clarify", "combination", "pending")
    terms = assess_attempt(lift, effective(PACKED_SOLA, Q)).terms
    assert terms == ("light_bending/no_force", "thruster_pack/lift")
    wrong = AbilityRuling("r1", lift, "combination", "approved", "director", terms=frozenset({"light_bending/no_force"}))
    assert _reason(library.rule, wrong) == "terms_mismatch"
    library.rule(AbilityRuling("r1", lift, "combination", "approved", "director", terms=frozenset(terms)))
    assert library.assess(Q, lift.attempt_id).classification == "uncertain"
    library.decide(Q, lift.attempt_id, Decision("success", "adjudicated", {"#ability"}))
    used = library.ability_use(Q, lift.attempt_id)
    assert [(ability_id, cost.id) for ability_id, cost in used.charges] == [
        ("light_bending", "eye_strain"), ("light_bending", "migraine"),
        ("thruster_pack", "cooldown"), ("thruster_pack", "fuel_burn")]
    assert _reopen(packed_table).library().ability_use(Q, lift.attempt_id) == used
