"""031 for 035 milestone 2: a player character's pack, packed for its present controller. Pure, no store.

Over the synthetic ``context_fixtures`` cast: Anya is a player character, played by one human. Her ledger
holds what she witnessed, one conclusion her player authored, one conclusion she held as an NPC (the
director's) and one that a previous player authored while playing her. Only her player's own conclusion is
hers to see.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

import context_fixtures as f
from context_fixtures import actor, knowledge, snapshot
import grimoire.context.actor as packer
from grimoire.context import (
    Budget, ContextError, controller_projection, knowledge_digest, pack_actor_context, pack_player_context,
)
from grimoire.core import DIRECTOR, ControlAssignment
from grimoire.core.knowledge import Belief, Knowledge, Moment, Perception, Proposition, Subject

ANA, BOB = "human:ana", "human:bob"
ANYA = actor("anya")
#: What Anya's scene window holds: nothing that triggers any keyed lore.
QUIET = "Anya: The tide is turning."
#: Her player's own conclusion; her NPC-era conclusion (the director's); her previous player's conclusion.
OWN = "Orrin keeps a second set of books"
INHERITED = "the lighthouse keeper owes the harbor a debt"
PREVIOUS = "the salt pans flood at the new moon"
#: Every conclusion rests on her perception of the ferry leaving (``v4``); the two not hers are about her too,
#: so an NPC-style pack ranks them first.
FERRY = Subject("event", "e1")
ABOUT = (FERRY, Subject("instance", str(ANYA)))


def ledger(base: Knowledge | None = None) -> Knowledge:
    """``context_fixtures.knowledge`` plus Anya's three conclusions (and the perception she already has)."""
    base = base or knowledge()
    c, m = f.C, Moment
    return Knowledge(
        c, events=base.events,
        propositions=[*base.propositions,
                      Proposition(c, "p6", OWN, m(4), "director", about=(FERRY,)),
                      Proposition(c, "p7", INHERITED, m(4), "director", about=ABOUT),
                      Proposition(c, "p8", PREVIOUS, m(4), "director", about=ABOUT)],
        rulings=base.rulings, evidence=base.evidence,
        beliefs=[*base.beliefs,
                 Belief(c, "b2", ANYA, "p6", "believed", m(5, "s1", "t1"), ANA, ("v4",), contribution_id="k1"),
                 Belief(c, "b3", ANYA, "p7", "believed", m(4), DIRECTOR, ("v4",)),
                 Belief(c, "b4", ANYA, "p8", "believed", m(4, "s0", "t0"), BOB, ("v4",), contribution_id="k0")],
        disclosures=base.disclosures)


def controls(*extra: ControlAssignment, controller: str = ANA) -> tuple[ControlAssignment, ...]:
    """Anya is ``controller``'s; every other cast member is the director's."""
    found = [ControlAssignment(f.C, ANYA.instance_id, controller)]
    found += [ControlAssignment(f.C, actor(n).instance_id, DIRECTOR) for n in f.EVERYONE if n != "anya"]
    return (*found, *extra)


def packed(*, controller: str = ANA, assigned=None, scene_text: str = QUIET, budget=Budget(2000), **kwargs):
    return pack_player_context(snapshot(), kwargs.pop("ledger", None) or ledger(), ANYA, controller=controller,
                               controls=controls() if assigned is None else assigned, as_of=9, budget=budget,
                               scene_text=scene_text, readers=f.READERS, unpack=f.unpack, **kwargs)


def reason(call) -> str:
    with pytest.raises(ContextError) as caught:
        call()
    return caught.value.reason


def text(pack) -> str:
    return pack.render()


# -- only what the controller may see ------------------------------------------------------------

def test_the_pack_holds_the_characters_witnessing_and_its_controllers_own_conclusions():
    pack = packed()
    assert pack.controller == ANA and pack.actor == ANYA and pack.notes == ()
    memory = [item.line for item in pack.lane("memory")]
    assert "You perceived (witnessed): the ferry pulling out after dark" in memory  # what she witnessed
    assert f"You believe: {OWN}" in memory  # her player's own conclusion
    assert pack.items[0].line == "You are Anya." and pack.lane("portrayal")


def test_an_inherited_or_another_players_conclusion_is_never_in_a_controllers_pack():
    npc_style = pack_actor_context(snapshot(), ledger(), ANYA, as_of=9, budget=Budget(2000), scene_text=QUIET,
                                   readers=f.READERS, unpack=f.unpack)
    assert INHERITED in text(npc_style) and PREVIOUS in text(npc_style)  # an NPC's pack reads them as its own
    pack = packed()
    assert INHERITED not in text(pack) and PREVIOUS not in text(pack)
    assert not [o for o in pack.omitted if o.origin.record in ("p7", "p8")]  # not even shed: never candidates


def test_an_inherited_conclusion_triggers_no_lore():
    npc_style = pack_actor_context(snapshot(), ledger(), ANYA, as_of=9, budget=Budget(2000), scene_text=QUIET,
                                   readers=f.READERS, unpack=f.unpack)
    assert any("lighthouse has been dark" in item.line for item in npc_style.lane("lore"))  # INHERITED's key
    assert any("Ashen Guild" in item.line for item in npc_style.lane("lore"))  # PREVIOUS's key (salt pans)
    lore = [item.line for item in packed().lane("lore")]
    assert not any("lighthouse" in line or "Ashen Guild" in line for line in lore)
    assert any("ferry crosses the marsh" in line for line in lore)  # her own perception still triggers lore
    assert any("lighthouse has been dark" in item.line
               for item in packed(scene_text="Anya: Is the lighthouse lit?").lane("lore"))  # her window does too


def test_inherited_conclusions_take_none_of_the_memory_budget():
    tight = Budget(2000, {"memory": 30})
    npc_style = pack_actor_context(snapshot(), ledger(), ANYA, as_of=9, budget=tight, scene_text=QUIET,
                                   readers=f.READERS, unpack=f.unpack)
    assert f"You believe: {OWN}" not in [item.line for item in npc_style.lane("memory")]
    assert f"You believe: {OWN}" in [item.line for item in packed(budget=tight).lane("memory")]


def test_the_pack_digests_the_controllers_projection_and_holds_no_table_event():
    projected = controller_projection(ledger(), ANYA, ANA, controls(), as_of=9)
    assert projected.inherited is None and projected.inherited_history is None
    assert [belief.proposition_id for belief in projected.current] == ["p6"]
    assert packed().knowledge == knowledge_digest(projected)
    assert "the ferry leaves late" not in text(packed())  # a disclosed event is the player's, out of character


def test_another_players_character_is_not_in_the_pack():
    """Bob plays Tavi, who saw the ferry leave too and drew a conclusion of Bob's: none of it is Ana's."""
    tavi, base = actor("tavi"), ledger()
    theirs = Knowledge(
        f.C, events=base.events,
        propositions=[*base.propositions, Proposition(f.C, "p9", "TAVI-TOKEN the ferry ran aground", Moment(5),
                                                      "director", about=(FERRY,))],
        rulings=base.rulings,
        evidence=[*base.evidence, Perception(f.C, "v9", tavi, "e1", "witnessed", "TAVI-TOKEN a hull scraping",
                                             Moment(5), "director", confirmed=True)],
        beliefs=[*base.beliefs, Belief(f.C, "b5", tavi, "p9", "believed", Moment(5, "s1", "t2"), BOB, ("v9",),
                                       contribution_id="k2")],
        disclosures=base.disclosures)
    assigned = (*[c for c in controls() if c.actor_id != tavi.instance_id],
                ControlAssignment(f.C, tavi.instance_id, BOB))
    pack = packed(ledger=theirs, assigned=assigned)
    assert "TAVI-TOKEN" not in text(pack) and f"You believe: {OWN}" in text(pack)


# -- fail closed ----------------------------------------------------------------------------------

@pytest.mark.parametrize("assigned", [
    (),  # nobody controls her
    tuple(replace(c, controller_id=BOB) if c.actor_id == ANYA.instance_id else c for c in controls()),  # another
    controls(ControlAssignment(f.C, ANYA.instance_id, BOB)),  # ambiguous: two controllers
    tuple(replace(c, controller_id=DIRECTOR) if c.actor_id == ANYA.instance_id else c for c in controls()),  # NPC
])
def test_a_character_this_player_does_not_alone_control_packs_nothing(assigned):
    assert reason(lambda: packed(assigned=assigned)) == "uncontrolled_audience"


@pytest.mark.parametrize("controller", [DIRECTOR, "", None])
def test_a_controllers_pack_is_for_a_human(controller):
    assert reason(lambda: packed(controller=controller, assigned=controls(controller=DIRECTOR))) == "invalid_controller"


@pytest.mark.parametrize("assigned", ["human:ana", [object()], 3])
def test_controls_must_be_control_assignments(assigned):
    assert reason(lambda: packed(assigned=assigned)) == "invalid_controls"


def test_another_characters_ambiguous_control_never_erases_this_characters_memory():
    # Anya's control is Ana's alone; only Wren, another character Ana is assigned, is ambiguous
    ambiguous = controls(ControlAssignment(f.C, "wren-1", ANA), ControlAssignment(f.C, "wren-1", BOB))
    pack = packed(assigned=ambiguous)
    assert pack.notes == ()
    assert "You perceived (witnessed): the ferry pulling out after dark" in pack.render()
    assert f"You believe: {OWN}" in pack.render() and INHERITED not in pack.render()
    assert pack.knowledge == knowledge_digest(controller_projection(ledger(), ANYA, ANA, ambiguous, as_of=9))


def test_knowledge_that_cannot_be_projected_packs_no_memory(monkeypatch):
    """Once her control is confirmed, 020 refuses only a holder kind it does not project (no chat actor has one,
    and no card composes over one), so the refusal is simulated: whatever its cause, it packs no memory."""
    monkeypatch.setattr(packer, "controller_projection", lambda *args, **kwargs: None)
    pack = packed()
    assert pack.notes == ("knowledge_unprojected",) and pack.lane("memory") == ()
    assert not any("ferry crosses" in item.line for item in pack.lane("lore"))  # no memory triggers anything
    assert pack.items[0].line == "You are Anya."


# -- a conclusion someone else revised -----------------------------------------------------------

def revised_while_away() -> Knowledge:
    """Ana concluded p6 (b2, sequence 5); while Anya was the director's NPC, the director revised it (b9, a
    suspicion, supersedes b2 at sequence 6); Ana plays Anya again at sequence 9."""
    base = ledger()
    return Knowledge(f.C, events=base.events, propositions=base.propositions, rulings=base.rulings,
                     evidence=base.evidence,
                     beliefs=[*base.beliefs, Belief(f.C, "b9", ANYA, "p6", "suspected", Moment(6), DIRECTOR, ("v4",),
                                                    supersedes="b2")],
                     disclosures=base.disclosures)


def test_a_controllers_own_conclusion_revised_by_someone_else_stays_theirs():
    revised = revised_while_away()
    pack = packed(ledger=revised)
    lines = pack.lines()
    assert f"You believe: {OWN}" in lines  # her own latest conclusion, shown as any own belief
    assert f"You suspect: {OWN}" not in lines and not any("revis" in line for line in lines)
    held = controller_projection(revised, ANYA, ANA, controls(), as_of=9)
    assert [(b.belief_id, b.stance) for b in held.current if b.proposition_id == "p6"] == [("b2", "believed")]
    assert all(b.belief_id != "b9" for b in (*held.current, *held.history))
    assert pack.knowledge == knowledge_digest(held)
    # the very pack she had before the director's revision: nothing of the head shows, ranks or counts
    assert pack == packed()


def test_a_previous_players_conclusion_the_controller_revised_never_surfaces():
    base = ledger()
    revised = Knowledge(f.C, events=base.events, propositions=base.propositions, rulings=base.rulings,
                        evidence=base.evidence,
                        beliefs=[*base.beliefs, Belief(f.C, "b10", ANYA, "p8", "rejected", Moment(7, "s2", "t3"), ANA,
                                                       ("v4",), contribution_id="k9", supersedes="b4")],
                        disclosures=base.disclosures)
    pack = packed(ledger=revised)
    assert f"You do not believe: {PREVIOUS}" in pack.lines()
    assert pack.knowledge == knowledge_digest(controller_projection(revised, ANYA, ANA, controls(), as_of=9))
    # before Ana's revision Bob's conclusion is the head, and inherited: nothing at all about p8
    earlier = controller_projection(revised, ANYA, ANA, controls(), as_of=6)
    assert all(belief.proposition_id != "p8" for belief in (*earlier.current, *earlier.history))


def test_an_npcs_pack_is_unchanged_and_controls_may_be_any_iterable():
    npc = pack_actor_context(snapshot(), ledger(), ANYA, as_of=9, budget=Budget(2000), scene_text=QUIET,
                             readers=f.READERS, unpack=f.unpack)
    assert npc == pack_actor_context(snapshot(), ledger(), ANYA, as_of=9, budget=Budget(2000), scene_text=QUIET,
                                     readers=f.READERS, unpack=f.unpack)
    assert npc.controller is None and npc.notes == ()
    once = packed(assigned=(control for control in controls()))  # a generator is read once, safely
    assert once.controller == ANA and OWN in once.render()


def test_a_character_of_another_kind_is_not_projected():
    other = replace(ANYA, kind="persona")
    assert controller_projection(ledger(), other, ANA, controls(), as_of=9) is None
    assert controller_projection(ledger(), ANYA, BOB, controls(), as_of=9) is None  # not Bob's to see
