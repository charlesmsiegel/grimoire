"""Open obstacles on the store (Sprint 040): the bribed guard, novel approaches, escalation and fail-closed replay."""

from __future__ import annotations

import pytest

from grimoire.adjudication import AdjudicationError, Decision, Factor
from grimoire.chatstore import campaign_taken_ids
from grimoire.core.identity import InstanceRef
from grimoire.ledgers import LedgerInvalid
from grimoire.obstacles import Approach, Consequence, ObstacleError, ObstacleFact, obstacle_to_payload
from grimoire.obstaclestore import ObstacleLibrary, obstacle_actor_ids
from grimoire.store import LibraryStore, ProcessLocker, TakenIdsUnlisted
from grimoire.store.ledger import render_batches

from adjudication_fixtures import BOB, IRIS, Q, SOLA, build_library, moment
from obstacle_fixtures import (
    ALARM, ARMED, BRIBE, FORCE, FORGE, GREEDY, LANTERN, LOYAL, REPORTED, SEALS, SILVER, SPENT, TRAINING, WATCH,
    WINDOWLESS, ArchiveTable, archive, attempt, offer, stakes_of,
)


@pytest.fixture
def table(tmp_path):
    locker = ProcessLocker()
    store = LibraryStore.create(tmp_path / "lib", locker=locker)
    build_library(store)
    seated = ArchiveTable(store).seat()
    seated.locker = locker
    yield seated
    seated.close()
    seated.store.close()


def _reopen(table: ArchiveTable) -> ArchiveTable:
    table.close()
    table.store.close()
    fresh = ArchiveTable(LibraryStore.open(table.store.root, locker=table.locker))
    fresh.locker = table.locker
    table.store, table.authority, table.key = fresh.store, fresh.authority, fresh.key
    return table


def _reason(call, *args, **kwargs) -> str:
    with pytest.raises((ObstacleError, AdjudicationError)) as caught:
        call(*args, **kwargs)
    return caught.value.reason


def _bribe(table, library: ObstacleLibrary, *, cid: str = "c10", at=None, extra=(SILVER,), **kwargs):
    at = at or moment()
    required = library.required(Q, "archive", "bribe", at.sequence)
    frame = attempt(required + tuple(extra), stakes_of(BRIBE), cid=cid, at=at)
    return library.frame("archive", frame, approach="bribe", contribution=offer(cid=cid, turn=at.turn_id),
                         author=table.player(), **kwargs)


def _nothing_written(table, library: ObstacleLibrary) -> None:
    assert library.state(Q, "archive").attempts == ()
    assert library.adjudication.state(Q).frames == {}


# -- the proving scenario ---------------------------------------------------------------------

def test_mara_bribes_the_guard_instead_of_fighting_and_pays_for_it(table):
    library = table.obstacles()
    library.record(archive())
    _bribe(table, library)
    assessment = library.adjudication.assess(Q, "c10")
    # The author's facts decide what kind of action this is: the guard's debts favour it, his loyalty
    # and his watch hinder it, and something is at stake, so it can go either way.
    assert (assessment.classification, assessment.allowed) == ("uncertain", ("failure", "partial", "success"))
    library.adjudication.decide(Q, "c10", Decision("success", "adjudicated", {"greedy", "silver"}))
    state = library.state(Q, "archive")
    assert (state.status, state.overcome_by) == ("overcome", "c10")
    # Resources and reputation: the purse is spent and the guard now expects a cut, with no combat engine.
    assert state.due() == (Consequence("cost", "owed", OWED_TEXT), Consequence("cost", "purse", PURSE_TEXT))
    assert _reason(library.frame, "archive", attempt((TRAINING,), stakes_of(FORCE), cid="c11", at=moment(11, "t11")),
                   approach="force", contribution=offer(cid="c11", turn="t11"),
                   author=table.player()) == "already_overcome"

    again = _reopen(table).obstacles().state(Q, "archive")
    assert again == state


OWED_TEXT = "The guard will expect a cut of whatever she takes from the archive."
PURSE_TEXT = "Twelve crowns from Mara's purse go to the guard."


@pytest.mark.parametrize("approach", ["force", "sneak"])
def test_every_viable_suggestion_is_a_real_path(table, approach):
    library = table.obstacles()
    library.record(archive())
    chosen = archive().approach(approach)
    required = library.required(Q, "archive", approach, 10)
    frame = attempt(required + (TRAINING,), stakes_of(chosen))
    library.frame("archive", frame, approach=approach, contribution=offer(), author=table.player())
    assert library.adjudication.assess(Q, "c10").classification == "uncertain"
    library.adjudication.decide(Q, "c10", Decision("success", "adjudicated", {"training"}))
    assert library.state(Q, "archive").status == "overcome"


def test_a_novel_approach_is_framed_like_any_other_and_brings_its_own_price(table):
    library = table.obstacles()
    library.record(archive())
    assert [f.factor_id for f in library.required(Q, "archive", FORGE, 10)] == ["watch"]
    frame = attempt((SEALS.factor("favours"), WATCH.factor("hinders")), (REPORTED.stake,),
                    intent="Forge a requisition and hand it over.")
    library.frame("archive", frame, approach=FORGE, contribution=offer("I forge a requisition."),
                  author=table.player())
    library.adjudication.decide(Q, "c10", Decision("partial", "adjudicated", {"seals", "watch"}, {"reported"}))
    state = library.state(Q, "archive")
    assert state.status == "overcome"
    assert state.attempts[0].attempt.listed is False
    assert [c.consequence_id for c in state.due()] == ["reported", "forgery"]
    assert _reopen(table).obstacles().state(Q, "archive") == state


def test_the_obstacle_cannot_shut_out_a_novel_approach(table):
    library = table.obstacles()
    library.record(archive())
    walled = attempt((WINDOWLESS.factor("prevents"), WATCH.factor("hinders")), (REPORTED.stake,))
    assert _reason(library.frame, "archive", walled, approach=FORGE, contribution=offer(),
                   author=table.player()) == "novel_prevented"
    # A suggestion's id cannot be reused to escape its facts.
    renamed = Approach("bribe", "A different bribe.", frozenset({"reported"}))
    assert _reason(library.frame, "archive", walled, approach=renamed, contribution=offer(),
                   author=table.player()) == "not_novel"
    _nothing_written(table, library)


@pytest.mark.parametrize("factors, stakes, reason", [
    ((GREEDY.factor("hinders"), LOYAL.factor("hinders"), WATCH.factor("hinders")), None, "fact_changed"),
    ((GREEDY.factor("favours"), WATCH.factor("hinders")), None, "missing_fact"),
    ((GREEDY.factor("favours"), LOYAL.factor("hinders"), WATCH.factor("hinders"), ARMED.factor("hinders")), None,
     "fact_misapplied"),
    ((Factor("greedy", "favours", "He is rich.", 3, "relation:r-guard-debts"), LOYAL.factor("hinders"),
      WATCH.factor("hinders")), None, "fact_changed"),
    (None, (REPORTED.stake, SPENT.stake, ALARM.stake), "stakes_mismatch"),
    (None, (REPORTED.stake,), "stakes_mismatch"),
], ids=["bearing", "dropped", "misapplied", "reworded", "extra-stake", "missing-stake"])
def test_a_suggestions_frame_carries_the_authors_facts_and_risks(table, factors, stakes, reason):
    library = table.obstacles()
    library.record(archive())
    factors = factors or (GREEDY.factor("favours"), LOYAL.factor("hinders"), WATCH.factor("hinders"))
    frame = attempt(factors, stakes or stakes_of(BRIBE))
    assert _reason(library.frame, "archive", frame, approach="bribe", contribution=offer(),
                   author=table.player()) == reason
    _nothing_written(table, library)


def test_029_still_decides_who_may_act(table):
    library = table.obstacles()
    library.record(archive())
    frame = attempt(library.required(Q, "archive", "bribe", 10), stakes_of(BRIBE))
    assert _reason(library.frame, "archive", frame, approach="bribe") == "pc_authorship"
    _nothing_written(table, library)


# -- failure, escalation and order ------------------------------------------------------------

def test_a_failed_bribe_is_reported_and_changes_what_comes_next(table):
    library = table.obstacles()
    library.record(archive())
    _bribe(table, library)
    # Only one attempt is undecided at a time: the next one meets what this one changes.
    sneak = attempt(library.required(Q, "archive", "sneak", 11), (ALARM.stake,), cid="c11", at=moment(11, "t11"))
    assert _reason(library.frame, "archive", sneak, approach="sneak", contribution=offer(cid="c11", turn="t11"),
                   author=table.player()) == "attempt_pending"
    library.adjudication.decide(Q, "c10", Decision("failure", "adjudicated", {"loyal"}, {"reported"}))
    state = library.state(Q, "archive")
    assert state.status == "open" and state.due() == (Consequence("stake", "reported", REPORTED.text),)
    escalated = next(f for f in state.facts if f.fact_id == "reported")
    assert (escalated.kind, escalated.established, escalated.bearing_on("bribe")) == ("escalation", 10, "prevents")

    # A second bribe now meets the report, so it can only fail; slipping past is harder but still open.
    assert [f.factor_id for f in library.required(Q, "archive", "bribe", 12)] == [
        "greedy", "loyal", "reported", "watch"]
    sneak = attempt(library.required(Q, "archive", "sneak", 11) + (TRAINING,), (ALARM.stake,), cid="c11",
                    at=moment(11, "t11"))
    assert [f.factor_id for f in sneak.factors if f.bearing == "hinders"] == ["lantern", "reported", "watch"]
    library.frame("archive", sneak, approach="sneak", contribution=offer(cid="c11", turn="t11"), author=table.player())
    assert library.adjudication.assess(Q, "c11").allowed == ("failure", "partial", "success")


def test_an_attempt_cannot_dodge_an_escalation_by_coming_earlier(table):
    library = table.obstacles()
    library.record(archive())
    _bribe(table, library, at=moment(10, "t10"))
    library.adjudication.decide(Q, "c10", Decision("failure", "adjudicated", {"loyal"}, {"reported"}))
    early = attempt((LANTERN.factor("hinders"), WATCH.factor("hinders")), (ALARM.stake,), cid="c9",
                    at=moment(9, "t9"))
    assert _reason(library.frame, "archive", early, approach="sneak", contribution=offer(cid="c9", turn="t9"),
                   author=table.player()) == "out_of_order"


def test_a_pending_attempt_is_revised_only_by_its_own_approach(table):
    library = table.obstacles()
    library.record(archive())
    _bribe(table, library, extra=())
    revised = _bribe(table, library)  # a favouring fact added before the decision
    assert revised.revision == 2
    frame = attempt(library.required(Q, "archive", "force", 10), stakes_of(FORCE))
    assert _reason(library.frame, "archive", frame, approach="force", contribution=offer(),
                   author=table.player()) == "approach_changed"
    library.adjudication.decide(Q, "c10", Decision("success", "adjudicated", {"silver"}))
    assert _reason(_bribe, table, library) == "already_decided"


def test_an_attempt_decided_elsewhere_cannot_be_pinned_to_the_obstacle_afterwards(table):
    library = table.obstacles()
    library.record(archive())
    frame = attempt(library.required(Q, "archive", "bribe", 10) + (SILVER,), stakes_of(BRIBE))
    library.adjudication.frame(frame, contribution=offer(), author=table.player())
    library.adjudication.decide(Q, "c10", Decision("success", "adjudicated", {"greedy"}))
    assert _reason(library.frame, "archive", frame, approach="bribe", contribution=offer(),
                   author=table.player()) == "already_decided"
    assert library.state(Q, "archive").attempts == ()


def test_a_frame_revised_around_the_obstacle_diverges_it(table):
    library = table.obstacles()
    library.record(archive())
    _bribe(table, library)
    # 029 lets a revision drop a hindrance; the obstacle's author said the guard's loyalty bears on a bribe.
    thinner = attempt((GREEDY.factor("favours"), WATCH.factor("hinders"), SILVER), stakes_of(BRIBE))
    library.adjudication.frame(thinner, contribution=offer(), author=table.player())
    state = library.state(Q, "archive")
    assert (state.status, state.problem) == ("diverged", "missing_fact")
    library.adjudication.decide(Q, "c10", Decision("success", "adjudicated", {"greedy"}))
    assert library.state(Q, "archive").overcome_by is None
    force = attempt(library.required(Q, "archive", "force", 11), stakes_of(FORCE), cid="c11", at=moment(11, "t11"))
    assert _reason(library.frame, "archive", force, approach="force", contribution=offer(cid="c11", turn="t11"),
                   author=table.player()) == "obstacle_diverged"


def test_a_diverged_pending_attempt_is_brought_back_by_its_own_revision(table):
    library = table.obstacles()
    library.record(archive())
    _bribe(table, library)
    thinner = attempt((GREEDY.factor("favours"), WATCH.factor("hinders"), SILVER), stakes_of(BRIBE))
    library.adjudication.frame(thinner, contribution=offer(), author=table.player())
    assert library.state(Q, "archive").status == "diverged"
    _bribe(table, library)  # the author's facts again
    restored = library.state(Q, "archive")
    assert (restored.status, restored.pending.attempt.attempt_id) == ("open", "c10")


# -- authoring --------------------------------------------------------------------------------

def test_an_obstacle_is_fixed_once_attempted(table):
    library = table.obstacles()
    first = library.record(archive())
    assert library.record(archive()) == first  # the same again writes nothing
    revised = library.record(archive(goal="Reach the sealed ledger before dawn."))
    assert revised.revision == 2
    _bribe(table, library)
    harder = archive(goal="Reach the sealed ledger before dawn.",
                     facts=(GREEDY, LOYAL, ARMED, WATCH, SEALS, WINDOWLESS,
                            ObstacleFact("dog", "constraint", "A mastiff sleeps by the door.", 7, "event:e-dog",
                                         {"*": "hinders"})))
    assert _reason(library.record, harder) == "obstacle_engaged"
    assert len(table.store.read_ledger(f"obstacles/{Q}").entries) == 3


def test_an_obstacle_names_only_the_campaigns_own_actors(table):
    library = table.obstacles()
    stranger = InstanceRef(Q, "character", "wren-1")
    assert _reason(library.record, archive(actors=frozenset({stranger}))) == "unknown_actor"
    assert _reason(library.record, archive(campaign_id="q-9", actors=frozenset())) == "missing_campaign"
    assert library.states(Q) == ()


# -- replay, ids and campaign reservation -----------------------------------------------------

def _forge(table, kind: str, value: dict) -> None:
    name = f"obstacles/{Q}"
    head = table.store.read_ledger(name).head
    entry = {"schema": 1, "kind": kind, "recorded_at": "x", "recorded_by": "app:adjudicator", "value": value}
    parts, data = render_batches(name, head, [entry])[0]
    (table.store.root / "/".join(parts)).write_bytes(data)


@pytest.mark.parametrize("kind, value, match", [
    ("attempt", {"obstacle_id": "archive", "attempt_id": "c10", "approach_id": "dig", "novel": None},
     "unknown_approach"),
    ("attempt", {"obstacle_id": "vault", "attempt_id": "c10", "approach_id": "bribe", "novel": None},
     "unknown_obstacle"),
    ("obstacle", None, "railroaded"),
], ids=["approach", "obstacle", "railroad"])
def test_a_forged_entry_makes_the_ledger_unreadable(table, kind, value, match):
    table.obstacles().record(archive())
    if value is None:
        value = obstacle_to_payload(archive())
        value["facts"].append({"fact_id": "sealed", "kind": "constraint", "text": "The door is bricked up.",
                               "established": 1, "source": "event:e-bricks",
                               "bearings": {"force": "prevents", "sneak": "prevents"}})
        value["facts"].sort(key=lambda f: f["fact_id"])
    _forge(table, kind, value)
    with pytest.raises(LedgerInvalid, match=match):
        _reopen(table).obstacles().state(Q, "archive")


def test_the_ledger_reserves_the_ids_of_the_actors_it_names(table):
    table.obstacles().record(archive(actors=frozenset({IRIS, SOLA})))
    assert obstacle_actor_ids(table.store, Q) == {"iris-1", "sola-1"}
    assert {"iris-1", "sola-1"} <= campaign_taken_ids(table.store, Q)
    folder = table.store.root / "authority" / "obstacles" / Q
    next(folder.iterdir()).write_bytes(b"not json")
    with pytest.raises(TakenIdsUnlisted):
        obstacle_actor_ids(table.store, Q)


def test_an_obstacle_ledger_keeps_a_campaign_id_taken(tmp_path):
    store = LibraryStore.create(tmp_path / "lib", locker=ProcessLocker())
    table = ArchiveTable(store)
    entry = {"schema": 1, "kind": "obstacle", "recorded_at": "x", "recorded_by": "app:adjudicator", "value": {}}
    parts, data = render_batches("obstacles/q-7", None, [entry])[0]
    path = store.root / "/".join(parts)
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    assert store.campaign_authority_presence("q-7") == "present"
    table.close()
    store.close()


def test_a_hopeless_suggestion_may_still_be_tried(table):
    library = table.obstacles()
    library.record(archive())
    climb = attempt(library.required(Q, "archive", "climb", 10), ())
    library.frame("archive", climb, approach="climb", contribution=offer("I look for a window."),
                  author=table.player())
    assessment = library.adjudication.assess(Q, "c10")
    assert (assessment.classification, assessment.allowed) == ("impossible", ("failure",))
    library.adjudication.decide(Q, "c10", Decision("failure", "rule", {"windowless"}))
    assert library.state(Q, "archive").status == "open"


def _crash(*args, **kwargs):
    raise OSError("disk gone")


def test_a_crash_between_the_link_and_the_frame_leaves_the_attempt_waiting(table, monkeypatch):
    library = table.obstacles()
    library.record(archive())
    frame = attempt(library.required(Q, "archive", "bribe", 10) + (SILVER,), stakes_of(BRIBE))
    with monkeypatch.context() as patch:
        patch.setattr(library.adjudication, "frame", _crash)
        with pytest.raises(OSError):
            library.frame("archive", frame, approach="bribe", contribution=offer(), author=table.player())
    library = _reopen(table).obstacles()
    waiting = library.state(Q, "archive")
    assert ([a.status for a in waiting.attempts], waiting.pending.frame) == (["pending"], None)
    # The link came first, so the problem the player chose against is already fixed.
    assert _reason(library.record, archive(goal="Reach the sealed ledger before dawn.")) == "obstacle_engaged"
    library.frame("archive", frame, approach="bribe", contribution=offer(), author=table.player())
    assert library.state(Q, "archive").pending.frame == frame
    # Decided as a failure, the guard's report escalates as it would have without the crash.
    library.adjudication.decide(Q, "c10", Decision("failure", "adjudicated", {"loyal"}, {"reported"}))
    assert any(f.fact_id == "reported" for f in library.state(Q, "archive").facts)


def test_an_attempt_whose_frame_was_never_written_is_superseded_by_the_next(table, monkeypatch):
    library = table.obstacles()
    library.record(archive())
    with monkeypatch.context() as patch:
        patch.setattr(library.adjudication, "frame", _crash)
        with pytest.raises(OSError):
            _bribe(table, library)
    _bribe(table, library, cid="c11", at=moment(11, "t11"))
    state = library.state(Q, "archive")
    assert ([a.status for a in state.attempts], state.pending.attempt.attempt_id) == (["void", "pending"], "c11")
    assert _reason(_bribe, table, library) == "already_decided"  # the void link is not reopened


def test_an_attempt_whose_actor_changed_hands_gives_way_to_the_new_controllers(table):
    library = table.obstacles()
    library.record(archive())
    _bribe(table, library)
    assert _reason(_bribe, table, library, cid="c11", at=moment(11, "t11")) == "attempt_pending"
    table.control("mara-1", BOB)  # 029 can no longer decide Ana's bribe; Bob's action is a new attempt
    frame = attempt(library.required(Q, "archive", "sneak", 11) + (TRAINING,), (ALARM.stake,), cid="c11",
                    at=moment(11, "t11"))
    library.frame("archive", frame, approach="sneak", contribution=offer(cid="c11", turn="t11", by=BOB),
                  author=table.player(BOB))
    state = library.state(Q, "archive")
    assert ([a.status for a in state.attempts], state.pending.attempt.attempt_id) == (["void", "pending"], "c11")


def test_text_the_store_cannot_hold_is_refused_before_the_link(table):
    library = table.obstacles()
    library.record(archive())
    frame = attempt(library.required(Q, "archive", "bribe", 10) + (SILVER,), stakes_of(BRIBE))
    assert _reason(library.frame, "archive", frame, approach="bribe", contribution=offer("I offer \ud800 crowns."),
                   author=table.player()) == "invalid_field"
    _nothing_written(table, library)
    assert library.record(archive(goal="Reach the sealed ledger before dawn.")).revision == 2
