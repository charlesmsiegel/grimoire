"""053's consolidated memory in 031's packs: standing accounts in, obsolete evidence and history out. Pure."""
from __future__ import annotations

import pytest

import context_fixtures as f
from context_fixtures import actor, knowledge, snapshot
from grimoire.context import Budget, ContextError, pack_actor_context, pack_player_context
from grimoire.core import DIRECTOR, ControlAssignment
from grimoire.core.knowledge import Moment
from grimoire.core.perspective import actor_knowledge
from grimoire.memory import MemoryCorrection, MemorySummary, consolidate_dossier

MARA, ANYA = actor("mara"), actor("anya")
#: Mara heard early that Anya and Tavi are cousins (v6), and later that Anya keeps the old charts (v3).
COUSINS = "Anya and Tavi are cousins"
CHARTS = "Anya keeps the old charts"


def dossier(*summaries, who=MARA, corrections=(), as_of=9, editor=DIRECTOR):
    held = actor_knowledge(knowledge(), who, as_of=as_of)
    return consolidate_dossier(held, "play", (), corrections, summaries, editor=editor)


def packed(memory, *, who=MARA, as_of=9):
    return pack_actor_context(snapshot(), knowledge(), who, as_of=as_of, budget=Budget(2000), readers=f.READERS,
                              unpack=f.unpack, memory=memory)


def reason(call) -> str:
    with pytest.raises(ContextError) as caught:
        call()
    return caught.value.reason


def test_standing_accounts_join_memory_and_corrected_evidence_leaves_it():
    fix = MemoryCorrection("fix", MARA, ("v6",), ("v3",), Moment(4), "the cousins were a mistake")
    one = MemorySummary("one", MARA, "play", "anya", "Anya guards the old charts.", ("v3",), Moment(4))
    two = MemorySummary("two", MARA, "play", "anya", "Anya sells charts to smugglers.", ("v3",), Moment(5))
    stale = MemorySummary("stale", MARA, "play", "kin", "Anya and Tavi are cousins.", ("v6",), Moment(5))
    without, pack = packed(None), packed(dossier(one, two, stale, corrections=(fix,)))
    lines = pack.lines()
    assert "You remember conflicting accounts; one is: Anya guards the old charts." in lines
    assert "You remember conflicting accounts; one is: Anya sells charts to smugglers." in lines
    assert any(COUSINS in line for line in without.lines())
    assert not any(COUSINS in line for line in lines)
    assert any(CHARTS in line for line in lines)
    summary = next(item for item in pack.items if item.origin.kind == "summary")
    assert summary.origin.evidence == ("v3",) and summary.origin.name == "anya"
    # the brief checks the pack against the actor's own projection, which consolidation does not change
    assert pack.knowledge == without.knowledge


def test_a_dossier_for_another_actor_moment_or_controller_is_refused():
    one = MemorySummary("one", MARA, "play", "anya", "Anya guards the old charts.", ("v3",), Moment(4))
    assert reason(lambda: packed(dossier(one), as_of=8)) == "memory_mismatch"
    assert reason(lambda: packed(dossier(one, editor="human:ana"))) == "memory_mismatch"
    assert reason(lambda: packed(dossier(), who=ANYA)) == "memory_mismatch"
    assert reason(lambda: packed("You remember: anything")) == "invalid_memory"


def test_a_dossier_citing_evidence_the_pack_does_not_hold_is_refused():
    # another ledger read's dossier: Anya's own perception cited as Mara's
    foreign = MemorySummary("one", ANYA, "play", "ferry", "The ferry left after dark.", ("v4",), Moment(4))
    memory = dossier(foreign, who=ANYA)
    stolen = type(memory)(MARA, memory.collection_id, 9, DIRECTOR, memory.evidence, memory.topics, ())
    assert reason(lambda: packed(stolen)) == "memory_mismatch"


def test_a_player_pack_takes_only_its_controller_s_dossier():
    controls = (ControlAssignment(f.C, ANYA.instance_id, "human:ana"),)
    own = MemorySummary("own", ANYA, "play", "ferry", "The ferry left after dark.", ("v4",), Moment(4))

    def player(memory):
        return pack_player_context(snapshot(), knowledge(), ANYA, controller="human:ana", controls=controls, as_of=9,
                                   budget=Budget(2000), readers=f.READERS, unpack=f.unpack, memory=memory)

    assert "You remember: The ferry left after dark." in player(dossier(own, who=ANYA, editor="human:ana")).lines()
    assert reason(lambda: player(dossier(own, who=ANYA))) == "memory_mismatch"


def test_a_belief_resting_on_corrected_evidence_leaves_the_pack_with_it():
    watch = "the duke paid off the harbor watch"
    assert any(watch in line for line in packed(None).lines())
    fix = MemoryCorrection("fix", MARA, ("v2",), ("v3",), Moment(3), "Orrin's rumor was wrong")
    lines = packed(dossier(corrections=(fix,))).lines()
    assert not any(watch in line for line in lines)
    assert any(CHARTS in line for line in lines)
