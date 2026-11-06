"""Recorded NPC decisions on the store (Sprint 045): written once per NPC and turn, kept across a restart.

Iris (a director NPC of the synthetic bridge chat) decides to fall back from the collapsing bridge; the
decision is recorded, the library is closed and opened again as an app restart would, and the reroll
replays the very same choice from the ledger. A second, different decision for the same turn is refused.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from grimoire.chatstore import campaign_taken_ids
from grimoire.core import Belief, Knowledge, Moment, Proposition, Report, actor_knowledge
from grimoire.core.authority import DIRECTOR
from grimoire.core.identity import InstanceRef, TurnRef
from grimoire.core.model import ControlAssignment
from grimoire.decisions import DecisionError, decision_to_payload, record_decision, replay
from grimoire.decisionstore import DecisionLibrary, decision_actor_ids
from grimoire.goals import Agenda, Approach, Goal, Holds, Limit, Present, choose
from grimoire.ledgers import LedgerInvalid
from grimoire.store import LibraryStore, ProcessLocker, TakenIdsUnlisted
from grimoire.store.ledger import render_batches

from adjudication_fixtures import IRIS, MARA, Q, SOLA, Table, build_library

FALLING = "span-falling"
AGENDA = Agenda(Q, IRIS, (
    Goal("live", "Get off the bridge alive", 70, (
        Approach("fall-back", "Run back to the near cliff", needs=(Holds(FALLING),), tags=frozenset({"flight"})),
        Approach("brace", "Brace against the rail and hold on", needs=(Holds(FALLING),)),
    )),
    Goal("watch", "Keep an eye on the far span", 5, (Approach("look", "Watch the far span"),)),
), limits=(Limit("stay-with-sola", "Never run while Sola is still out on the span",
                 forbids=frozenset({"flight"}), when=(Present(SOLA),)),))
CONTROLS = (ControlAssignment(Q, "iris-1", DIRECTOR), ControlAssignment(Q, "sola-1", DIRECTOR),
            ControlAssignment(Q, "mara-1", "human:ana"))
TURN = TurnRef(Q, "t-1.000003")


def iris_knows():
    seen = Report(Q, "iris-sees", IRIS, FALLING, "observed", Moment(2), "scene:s1/t3", confirmed=True)
    belief = Belief(Q, "iris-on-span", IRIS, FALLING, "believed", Moment(2), DIRECTOR, ("iris-sees",))
    ledger = Knowledge(Q, propositions=[Proposition(Q, FALLING, "The far span is falling", Moment(1), "director")],
                       evidence=[seen], beliefs=[belief])
    return actor_knowledge(ledger, IRIS, as_of=2)


def iris_choice(present=(IRIS, MARA), **kw):
    return choose(AGENDA, iris_knows(), present=present, controls=CONTROLS, **kw)


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
    table.store, table.authority, table.key, table.owner = fresh.store, fresh.authority, fresh.key, fresh.owner
    return table


def library(table: Table) -> DecisionLibrary:
    return DecisionLibrary(table.store, table.authority, table.key)


def test_a_recorded_decision_survives_a_restart_and_a_reroll_replays_it(table):
    record = record_decision(iris_choice(), TURN)
    entry = library(table).record(record)
    assert entry.record.chosen == ("live", "fall-back") and entry.recorded_by == "app:adjudicator"
    assert library(table).record(record) == entry  # the same decision again writes nothing

    table = _reopen(table)
    found = library(table).get(TURN, IRIS)
    assert found is not None and found.record == record
    assert library(table).decisions(TURN) == (found,)
    again = replay(found.record, AGENDA, iris_knows(), present=(IRIS, MARA), controls=CONTROLS)
    assert again.approach.approach_id == "fall-back"
    assert library(table).get(TurnRef(Q, "t-1.000004"), IRIS) is None


def test_a_turn_is_decided_once(table):
    library(table).record(record_decision(iris_choice(), TURN))
    # Sola walked back onto the span: the policy would now brace, but this turn is already decided.
    other = record_decision(iris_choice(present=(IRIS, MARA, SOLA)), TURN)
    with pytest.raises(DecisionError) as error:
        library(table).record(other)
    assert error.value.reason == "already_decided"
    later = library(table).record(replace(other, turn=TurnRef(Q, "t-1.000004")))
    assert later.record.chosen == ("live", "brace")  # the next turn decides anew


def test_a_decision_names_only_the_campaigns_own_actors(table):
    stranger = InstanceRef(Q, "character", "wren-1")
    agenda = Agenda(Q, IRIS, (Goal("g", "Find Wren", 5, (Approach("a", "Call to Wren", needs=(Present(stranger),)),)),))
    record = record_decision(choose(agenda, iris_knows(), present=(IRIS, stranger), controls=CONTROLS), TURN)
    with pytest.raises(DecisionError) as error:
        library(table).record(record)
    assert error.value.reason == "unknown_actor"
    ghost = InstanceRef("q-9", "character", "iris-1")
    ghost_choice = choose(Agenda("q-9", ghost, AGENDA.goals[1:]), actor_knowledge(Knowledge("q-9"), ghost, as_of=1),
                          present=(ghost,), controls=(ControlAssignment("q-9", "iris-1", DIRECTOR),))
    elsewhere = record_decision(ghost_choice, TurnRef("q-9", "x.000001"))
    with pytest.raises(DecisionError) as error:
        library(table).record(elsewhere)
    assert error.value.reason == "missing_campaign"


def test_the_ledger_reserves_the_ids_it_names_and_refuses_forgery(table):
    library(table).record(record_decision(iris_choice(present=(IRIS, MARA, SOLA)), TURN))
    assert decision_actor_ids(table.store, Q) == {"iris-1", "sola-1"}  # the decider, and Sola as evidence
    assert {"iris-1", "sola-1"} <= campaign_taken_ids(table.store, Q)

    name = f"decisions/{Q}"
    forged = decision_to_payload(record_decision(iris_choice(), TURN))  # another decision for the same turn
    entry = {"schema": 1, "kind": "decision", "recorded_at": "x", "recorded_by": "app:adjudicator", "value": forged}
    parts, data = render_batches(name, table.store.read_ledger(name).head, [entry])[0]
    (table.store.root / "/".join(parts)).write_bytes(data)
    with pytest.raises(LedgerInvalid, match="already_decided"):
        library(_reopen(table)).get(TURN, IRIS)

    folder = table.store.root / "authority" / "decisions" / Q
    next(folder.iterdir()).write_bytes(b"not json")
    with pytest.raises(TakenIdsUnlisted):
        decision_actor_ids(table.store, Q)
