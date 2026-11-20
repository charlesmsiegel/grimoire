"""Recorded rolls on the store (Sprint 075): the proving scenario, authority and fail-closed replay.

Mara (Ana's PC) slips past a guard: a 5e Dexterity (Stealth) check, DC 15, modifier +3. The die shows 4,
so the check fails, and it stays failed through every narration retry, a retried roll and a reopened
library. Every name is synthetic.
"""

from __future__ import annotations

import pytest

from grimoire.chatstore import campaign_taken_ids
from grimoire.core import DIRECTOR
from grimoire.core.authority import AuthorityError, NarrationClaim, NarrationContext, review_narration
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.ledgers import AuthorityUnavailable, ControlChange, LedgerInvalid, SovereigntyLedger
from grimoire.rolls import RollError, roll_to_payload
from grimoire.rollstore import RollLibrary, roll_actor_ids
from grimoire.rulesets import (
    ActionRequest, ArsStudy, D20Check, ModuleBinding, PbtaMove, RulesetModule, WodTest,
)
from grimoire.store import LibraryStore, ProcessLocker
from grimoire.store.ledger import render_batches

from adjudication_fixtures import ANA, BOB, IRIS, MARA, Q, Table, bridge, build_library, vault

ROLLER = "app:roller"
D20 = ModuleBinding("d20", "5e-2024")
MODULE = RulesetModule(SourceRef("synthetic", "ruleset", "checks", "v1"), (D20,))
STEALTH = D20Check("dexterity", "stealth", 15, 3)


class Dice:
    """A scripted draw that records every die it was asked for."""

    def __init__(self, *faces: int) -> None:
        self.faces, self.asked = list(faces), []

    def __call__(self, sides: int) -> int:
        self.asked.append(sides)
        return self.faces.pop(0)


def sneak(request_id: str = "c10", *, actor=MARA, action=STEALTH, module=MODULE, binding=D20) -> ActionRequest:
    return ActionRequest(request_id, actor, module, binding, action)


def slip(cid: str = "c10", *, by: str = ANA):
    return vault("I slip past the guard while he yawns.", cid=cid, by=by)


@pytest.fixture
def table(tmp_path):
    locker = ProcessLocker()
    store = LibraryStore.create(tmp_path / "lib", locker=locker)
    build_library(store)
    seated = Table(store).seat()
    seated.locker = locker
    seated.roller = seated.authority.handle_for(ROLLER, trusted=True)
    yield seated
    seated.close()
    seated.store.close()


def _library(table, dice) -> RollLibrary:
    return RollLibrary(table.store, table.authority, table.roller, draw=dice)


def _reopen(table) -> None:
    table.close()
    table.store.close()
    table.store = LibraryStore.open(table.store.root, locker=table.locker)
    fresh = Table(table.store)
    table.authority, table.key = fresh.authority, fresh.key
    table.roller = table.authority.handle_for(ROLLER, trusted=True)


def _reason(call, *args, **kwargs) -> str:
    with pytest.raises(RollError) as caught:
        call(*args, **kwargs)
    return caught.value.reason


def _says(result: str, claim_id: str = "n1", attempt_id: str = "c10", subject: str = "mara-1") -> NarrationClaim:
    return NarrationClaim(Q, claim_id, "outcome", f"Mara {result}s to slip past.", subject_id=subject,
                          attempt_id=attempt_id, result=result)


def _review(table, library, claim):
    controls = SovereigntyLedger(table.authority).load(Q).controls
    review = review_narration(NarrationContext(Q, "s1", "t10"), (claim,), controls, (slip(),),
                              resolutions=library.resolutions(Q))
    return [(r.claim.claim_id, r.reason) for r in review.rejected], [(a.claim.claim_id, a.basis)
                                                                      for a in review.accepted]


# -- the proving scenario ---------------------------------------------------------------------

def test_a_failed_stealth_roll_stays_failed_through_every_narration_retry(table):
    dice = Dice(4)
    library = _library(table, dice)
    roll = library.roll(MODULE, sneak(), contribution=slip(), author=table.player())
    assert (roll.faces, roll.result.total, roll.result.success, roll.outcome) == ((4,), 7, False, "failure")
    assert (roll.recorded_by, dice.asked) == (ROLLER, [20])
    # A narration take that turns the miss into a hit is rejected; a retry that keeps it is bound to the roll.
    assert _review(table, library, _says("success")) == ([("n1", "contradicts_resolution")], [])
    assert _review(table, library, _says("failure", "n2")) == ([], [("n2", "resolution:c10")])
    # Asking for the roll again (the narration retry re-running its turn) reads it back and draws nothing.
    again = _library(table, Dice(20))
    assert again.roll(MODULE, sneak(), contribution=slip(), author=table.player()) == roll
    assert again._draw.asked == []
    # Reopened, the ledger replays the same faces and derives the same failure; a lucky die changes nothing.
    _reopen(table)
    lucky = Dice(20)
    replayed = _library(table, lucky)
    assert replayed.rolled(Q, "c10") == roll and replayed.rolled(Q, "c10").result == roll.result
    assert replayed.roll(MODULE, sneak(), contribution=slip(), author=table.player()) == roll
    assert lucky.asked == []
    assert _review(table, replayed, _says("success", "n3")) == ([("n3", "contradicts_resolution")], [])


def test_a_changed_request_under_the_same_id_is_not_a_second_roll(table):
    library = _library(table, Dice(4, 20))
    library.roll(MODULE, sneak(), contribution=slip(), author=table.player())
    easier = D20Check("dexterity", "stealth", 5, 3)
    assert _reason(library.roll, MODULE, sneak(action=easier), contribution=slip(), author=table.player()) \
        == "already_rolled"
    reworded = vault("I wait for the guard to look away.", cid="c10")
    assert _reason(library.roll, MODULE, sneak(), contribution=reworded, author=table.player()) == "already_rolled"
    assert library.resolutions(Q)[0].result == "failure" and library._draw.asked == [20]


def test_wod_and_pbta_roll_through_the_same_interface(table):
    wod = ModuleBinding("wod", "v5")
    pbta = ModuleBinding("pbta", "dungeon-world-1e")
    hybrid = RulesetModule(SourceRef("synthetic", "ruleset", "hybrid", "v1"), (wod, pbta),
                           (SourceRef("synthetic", "ruleset", "v5", "v1"),
                            SourceRef("synthetic", "ruleset", "dw", "v1")))
    library = _library(table, Dice(2, 3, 1, 3, 4))
    pool = library.roll(hybrid, sneak("director-1", actor=IRIS, module=hybrid, binding=wod,
                                      action=WodTest("wits", "awareness", 3, 1, 3)))
    move = library.roll(hybrid, sneak("director-2", actor=IRIS, module=hybrid, binding=pbta,
                                      action=PbtaMove("Iris crosses a collapsing bridge", "dexterity", 1)))
    assert (pool.result.outcome, pool.outcome) == ("bestial-failure", "failure")
    assert (move.result.outcome, move.outcome) == ("partial", "partial")
    assert [r.attempt_id for r in library.resolutions(Q)] == ["director:director-1", "director:director-2"]
    _reopen(table)
    assert _library(table, Dice()).state(Q).rolls == {"director:director-1": pool, "director:director-2": move}


# -- authority -----------------------------------------------------------------------------

def test_only_the_controller_rolls_for_their_own_character(table):
    library = _library(table, Dice(4))
    assert _reason(library.roll, MODULE, sneak()) == "pc_authorship"
    assert _reason(library.roll, MODULE, sneak(), contribution=slip()) == "not_controller"
    assert _reason(library.roll, MODULE, sneak(), contribution=slip(), author=table.player(BOB)) == "not_controller"
    assert _reason(library.roll, MODULE, sneak(), contribution=slip(), author=table.key) == "not_controller"
    assert _reason(library.roll, MODULE, sneak(), contribution=slip(by=BOB), author=table.player(BOB)) \
        == "invalid_expression"
    assert _reason(library.roll, MODULE, sneak(), contribution=vault(cid="c11"), author=table.player()) \
        == "invalid_expression"
    assert _reason(library.roll, MODULE, sneak("d1", actor=IRIS), contribution=slip(), author=table.player()) \
        == "invalid_expression"
    SovereigntyLedger(table.authority).record(Q, ControlChange(Q, "mara-1", "app:bot"), table.owner)
    assert _reason(library.roll, MODULE, sneak(), contribution=slip(), author=table.player()) \
        == "non_human_controller"
    assert library.state(Q).rolls == {} and library._draw.asked == []


def test_a_director_roll_takes_no_backing_and_never_binds_a_players_attempt(table):
    library = _library(table, Dice(20))
    assert _reason(library.roll, MODULE, sneak("c10", actor=IRIS), contribution=vault(actor_id="iris-1"),
                   author=table.player()) == "unexpected_backing"
    roll = library.roll(MODULE, sneak("c10", actor=IRIS))
    assert roll.resolution().attempt_id == "director:c10"
    # Iris's success under the same request id says nothing about Mara's attempt c10.
    assert _review(table, library, _says("failure")) == ([], [("n1", "director")])
    # Mara still rolls her own c10, and the two rolls stay apart on record and after a reopen.
    mara = _library(table, Dice(4)).roll(MODULE, sneak(), contribution=slip(), author=table.player())
    _reopen(table)
    reread = _library(table, Dice())
    assert reread.rolled(Q, "c10") == mara and reread.rolled(Q, "director:c10") == roll
    assert _review(table, reread, _says("success")) == ([("n1", "contradicts_resolution")], [])


@pytest.mark.parametrize("contribution, reason", [
    (vault("x" * 4001, cid="c10"), "contribution_too_long"),
    (vault("I slip \ud800 past.", cid="c10"), "invalid_field"),
    (vault(turn="t" * 600), "invalid_field"),
])
def test_a_contribution_no_ledger_could_hold_is_refused_before_a_die_is_drawn(table, contribution, reason):
    library = _library(table, Dice(4))
    assert _reason(library.roll, MODULE, sneak(), contribution=contribution, author=table.player()) == reason
    assert library._draw.asked == [] and library.state(Q).rolls == {}


def test_an_unusable_authority_refuses_before_a_die_is_drawn(table):
    library = _library(table, Dice(4))
    library.roll(MODULE, sneak("d0", actor=IRIS))  # the ledgers are read, so only the roll itself can refuse
    library._draw = Dice(20)
    table.authority._poison(OSError("disk gone"))
    with pytest.raises(AuthorityUnavailable):
        library.roll(MODULE, sneak("d1", actor=IRIS))
    assert library._draw.asked == []


@pytest.mark.parametrize("selected, request_kwargs, reason", [
    (None, {}, "mechanics_disabled"),
    (RulesetModule(SourceRef("synthetic", "ruleset", "checks", "v2"), (D20,)), {}, "module_mismatch"),
    (MODULE, {"actor": InstanceRef(Q, "character", "nobody")}, "unknown_actor"),
    (MODULE, {"actor": InstanceRef("q-9", "character", "mara-1")}, "missing_campaign"),
])
def test_a_roll_needs_the_selected_module_and_a_real_actor(table, selected, request_kwargs, reason):
    library = _library(table, Dice(4))
    assert _reason(library.roll, selected, sneak("d1", **request_kwargs)) == reason
    assert library._draw.asked == []


def test_an_ars_study_rolls_nothing(table):
    ars = ModuleBinding("ars", "5e")
    module = RulesetModule(SourceRef("synthetic", "ruleset", "ars", "v1"), (ars,))
    library = _library(table, Dice())
    study = sneak("d1", actor=IRIS, module=module, binding=ars, action=ArsStudy("ignem", 13, 5, 8, 1220, "spring"))
    assert _reason(library.roll, module, study) == "no_roll"


@pytest.mark.parametrize("face", [0, 21, True, "4"])
def test_a_draw_that_is_no_face_writes_nothing(table, face):
    library = _library(table, Dice(face))
    assert _reason(library.roll, MODULE, sneak(), contribution=slip(), author=table.player()) == "invalid_draw"
    assert library.state(Q).rolls == {}
    # Nothing anyone saw was drawn: the next roll draws afresh.
    assert _library(table, Dice(12)).roll(MODULE, sneak(), contribution=slip(), author=table.player()).outcome \
        == "success"


def test_an_attempt_029_frames_is_not_rolled(table):
    table.library().frame(bridge(), contribution=vault(), author=table.player())
    library = _library(table, Dice(4))
    assert _reason(library.roll, MODULE, sneak(), contribution=vault(), author=table.player()) \
        == "attempt_adjudicated"


def test_the_roll_key_is_a_trusted_handle_of_this_authority(table):
    with pytest.raises(AuthorityError):
        RollLibrary(table.store, table.authority, table.authority.handle_for(ROLLER))
    with pytest.raises(AuthorityError):
        RollLibrary(table.store, table.authority, object())


# -- reserved ids and the ledger -------------------------------------------------------------

def test_a_rolled_actors_id_stays_reserved(table):
    _library(table, Dice(4)).roll(MODULE, sneak(), contribution=slip(), author=table.player())
    _library(table, Dice(4)).roll(MODULE, sneak("d1", actor=IRIS))
    assert roll_actor_ids(table.store, Q) == {"mara-1", "iris-1"}
    assert {"mara-1", "iris-1"} <= campaign_taken_ids(table.store, Q)
    assert table.store.campaign_authority_presence(Q) == "present"


def _forge(table, value: dict, kind: str = "roll") -> None:
    name = f"rolls/{Q}"
    head = table.store.read_ledger(name).head
    entry = {"schema": 1, "kind": kind, "recorded_at": "x", "recorded_by": ROLLER, "value": value}
    parts, data = render_batches(name, head, [entry])[0]
    (table.store.root / "/".join(parts)).write_bytes(data)


@pytest.mark.parametrize("change, match", [
    ({"faces": [21]}, "undecodable_entry"),
    ({"faces": [12, 12]}, "undecodable_entry"),
    ({"result": "success"}, "undecodable_entry"),
    ({}, "already_rolled"),  # the same request rolled twice: a reroll smuggled in by hand
])
def test_a_forged_roll_makes_the_ledger_unreadable(table, change, match):
    library = _library(table, Dice(4))
    roll = library.roll(MODULE, sneak(), contribution=slip(), author=table.player())
    _forge(table, {**roll_to_payload(roll.request, roll.contribution, (20,)), **change})
    _reopen(table)
    with pytest.raises(LedgerInvalid, match=match):
        _library(table, Dice()).state(Q)


def test_an_unknown_entry_kind_makes_the_ledger_unreadable(table):
    _library(table, Dice(4)).roll(MODULE, sneak("d1", actor=IRIS))
    _forge(table, {}, kind="reroll")
    _reopen(table)
    with pytest.raises(LedgerInvalid, match="unknown kind"):
        _library(table, Dice()).resolutions(Q)


def test_director_control_needs_no_backing(table):
    SovereigntyLedger(table.authority).record(Q, ControlChange(Q, "mara-1", DIRECTOR), table.owner)
    roll = _library(table, Dice(15)).roll(MODULE, sneak("d1"))
    assert roll.resolution().attempt_id == "director:d1" and roll.outcome == "success"
