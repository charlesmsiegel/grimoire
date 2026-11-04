"""Sprint 021 acceptance scenarios, persisted and reopened (A1–A5 and the A2b stack).

The proving scenario: a character gives away a key mid-scene. The next reply knows it is gone, wrap-up
remembers the event but does not remove another key, and rerolling the turn cannot secretly
duplicate it. All names are synthetic; nothing calls a model.
"""

from __future__ import annotations

import pytest

from grimoire.core import DIRECTOR, Moment
from grimoire.core.identity import AcceptedRef, RelationRef
from grimoire.effects import EffectProposal, Op, as_knowledge_event
from grimoire.effectstore import InvalidEffect
from grimoire.store import LibraryStore
from grimoire.testing.fileops import snapshot

from effects_fixtures import (
    C, CHAT, CONTROLS, GUILD, INES, KEY1, KEY2, TAM, WREN, Handles, contribution, effects_library, give, moment, seed,
)

SEED_KEY1 = RelationRef(C, "r-seed-1")


@pytest.fixture
def world(tmp_path):
    handles = Handles()
    with LibraryStore.create(tmp_path / "lib") as store:
        seed(store, handles)
        yield store, handles, effects_library(store, handles)


def holding(index, as_of: int) -> set[tuple]:
    """Every fact that holds at ``as_of``, as (predicate, subject, object)."""
    found = set()
    for node in index.instances:
        for relation in index.relations(node, as_of=as_of, requested_by=DIRECTOR):
            found.add((relation.predicate, relation.subject.instance_id, relation.object.instance_id))
    return found


def accept_give(library, handles, proposal=None, cid="c7", **kwargs):
    proposal = proposal or give()
    turn = proposal.moment.turn_id
    change = library.accept(proposal, controls=CONTROLS, contributions=[contribution(cid, turn=turn, **kwargs)],
                            endorsements=[handles.endorse(proposal, cid)])
    return change, change.commit()


def continuity(library, as_of=8):
    return library.continuity(C, as_of=as_of, requested_by=DIRECTOR)


def reason(call) -> str:
    with pytest.raises(InvalidEffect) as caught:
        call()
    return caught.value.reason


# -- A1: the proving scenario ---------------------------------------------------------------------

def test_a1_the_next_reply_knows_the_key_is_gone(world):
    store, handles, library = world
    change, done = accept_give(library, handles)
    assert sorted(change.preview()) == [f"campaigns/{C}/accepted/e-1.json", f"campaigns/{C}/relations/r-1.json",
                                        f"campaigns/{C}/relations/r-seed-1.json"]
    view = continuity(library)
    assert [e.event_id for e in view.events] == ["e-1"] and view.problems == ()
    index = view.graph.index
    assert index.recorded("carries", TAM, KEY1, as_of=8, requested_by=DIRECTOR)
    assert not index.recorded("carries", WREN, KEY1, as_of=8, requested_by=DIRECTOR)
    assert index.recorded("carries", WREN, KEY2, as_of=8, requested_by=DIRECTOR)
    assert index.recorded("carries", WREN, KEY1, as_of=6, requested_by=DIRECTOR)  # history is kept
    wren = index.relation("r-seed-1", requested_by=DIRECTOR).relation
    assert wren.ending.until == moment() and wren.ending.provenance == "effect:accepted/e-1"
    tam = index.relation("r-1", requested_by=DIRECTOR).relation
    assert tam.provenance == "effect:accepted/e-1" and tam.since == moment()
    event, _ = library.read(done.ref, requested_by=DIRECTOR)
    assert [item.domain for item in event.operations] == ["voluntary_action"] * 2
    assert [(e.principal, e.fingerprint) for e in event.endorsements] == [("human:ana", give().fingerprint)]
    assert event.committed_by == "human:owner"


def test_a1_reroll_and_alternate_response_are_duplicates(world):
    store, handles, library = world
    _, done = accept_give(library, handles)
    before = snapshot(store.root)
    for reworded in (give(summary="Wren, sighing, hands Tam the key."), give(proposed_by="player")):
        change = library.accept(reworded, controls=CONTROLS, contributions=[contribution()],
                                endorsements=[handles.endorse(reworded)])
        assert change.duplicate_of == done.ref and change.preview() == {}
        assert change.commit().ref == done.ref
    assert snapshot(store.root) == before


def test_a1_wrap_up_remembers_without_removing_another_key(world):
    store, handles, library = world
    _, done = accept_give(library, handles)
    before = snapshot(store.root)
    change = library.wrap_up(CHAT, "s1", through=moment(8), summary="Wren gave Tam the iron key.", cites=["e-1"],
                             written_by="director", requested_by=DIRECTOR)
    assert len(change.preview()) == 1
    wrapped = change.commit()
    after = snapshot(store.root)
    new = set(after) - set(before)
    assert {key for key in new if key.endswith(".json")} == {f"campaigns/{C}/wrapups/{wrapped.ref.wrapup_id}.json"}
    assert all(after[key] == value for key, value in before.items())  # every other file byte-identical
    index = continuity(library).graph.index
    assert index.recorded("carries", WREN, KEY2, as_of=8, requested_by=DIRECTOR)
    carriers = [r for r in index.history(KEY1, requested_by=DIRECTOR)
                if r.relation.subject == TAM and not r.retracted]
    assert len(carriers) == 1
    assert library.read_wrapup(wrapped.ref, requested_by=DIRECTOR)[0].cites == ("e-1",)


def test_a1_a_later_re_mention_cannot_apply_it_again(world):
    store, handles, library = world
    accept_give(library, handles)
    again = give(sequence=9, backed="c9")
    assert reason(lambda: library.accept(again, controls=CONTROLS, contributions=[contribution("c9", turn="t9")],
                                         endorsements=[handles.endorse(again, "c9")])) == "precondition_failed"


def test_a1_reopening_the_library_gives_the_same_continuity(world, tmp_path):
    store, handles, library = world
    accept_give(library, handles)
    library.wrap_up(CHAT, "s1", through=moment(8), summary="Wren gave Tam the key.", cites=["e-1"],
                    written_by="director", requested_by=DIRECTOR).commit()
    first = continuity(library)
    store.close()
    with LibraryStore.open(tmp_path / "lib") as reopened:
        second = effects_library(reopened, handles).continuity(C, as_of=8, requested_by=DIRECTOR)
        assert (second.events, second.revoked, second.wrapups, second.problems) == (
            first.events, first.revoked, first.wrapups, first.problems)
        assert second.graph.index.to_json(requested_by=DIRECTOR) == first.graph.index.to_json(requested_by=DIRECTOR)


# -- A2: the revised turn -------------------------------------------------------------------------

def test_a2_revoke_then_accept_the_revision(world):
    store, handles, library = world
    before = continuity(library, as_of=0).graph.index
    _, done = accept_give(library, handles)
    library.wrap_up(CHAT, "s1", through=moment(8), summary="Wren gave Tam the key.", cites=["e-1"],
                    written_by="director", requested_by=DIRECTOR).commit()
    _, etag = library.read(done.ref, requested_by=DIRECTOR)
    library.revoke(done.ref, reason="Ana edited her turn", expected=etag,
                   endorsements=[handles.undo(done.ref)]).commit()
    view = continuity(library)
    assert view.events == () and [e.event_id for e in view.revoked] == ["e-1"]
    assert [p.code for p in view.problems] == ["stale_wrapup"]
    index = view.graph.index
    seed_key1 = index.relation("r-seed-1", requested_by=DIRECTOR)
    assert seed_key1.reopened and not seed_key1.ended  # restored in place: same id, no gap
    assert index.relation("r-1", requested_by=DIRECTOR).retracted
    assert all(holding(index, s) == holding(before, s) for s in range(12))
    revision = give(to=INES, summary="Wren gives Ines the key instead.")
    change, redone = accept_give(library, handles, revision)
    assert change.duplicate_of is None and redone.ref == AcceptedRef(C, "e-2")
    view = continuity(library)
    assert [e.event_id for e in view.events] == ["e-2"]
    assert view.graph.index.recorded("carries", INES, KEY1, as_of=8, requested_by=DIRECTOR)
    rewrap = library.wrap_up(CHAT, "s1", through=moment(8), summary="Wren gave Ines the key.", cites=["e-2"],
                             written_by="director", requested_by=DIRECTOR,
                             expected=library.read_wrapup(view.wrapups[0].ref, requested_by=DIRECTOR)[1])
    rewrap.commit()
    assert continuity(library).problems == ()


def test_a2b_the_stack_is_undone_latest_first(world):
    store, handles, library = world
    before = continuity(library, as_of=0).graph.index
    _, e7 = accept_give(library, handles)
    snatch = give(KEY2, to=TAM, agent=TAM, backed=None, sequence=8, summary="Tam snatches the brass key.")
    e8 = library.accept(snatch, controls=CONTROLS).commit()
    onward = give(KEY1, giver=TAM, to=INES, agent=TAM, backed=None, sequence=9, summary="Tam passes it to Ines.")
    e9 = library.accept(onward, controls=CONTROLS).commit()
    _, etag7 = library.read(e7.ref, requested_by=DIRECTOR)
    assert reason(lambda: library.revoke(e7.ref, reason="r", expected=etag7,
                                         endorsements=[handles.undo(e7.ref)])) == "not_latest"
    for done, endorse in ((e9, []), (e8, []), (e7, [handles.undo(e7.ref)])):
        _, etag = library.read(done.ref, requested_by=DIRECTOR)
        library.revoke(done.ref, reason="rewind", expected=etag, endorsements=endorse).commit()
        assert [p for p in continuity(library, as_of=20).problems if p.code == "receipt_mismatch"] == []
    index = continuity(library, as_of=20).graph.index
    assert all(holding(index, s) == holding(before, s) for s in range(20))
    revision = give(to=INES, summary="Wren gives Ines the key.")
    accept_give(library, handles, revision)
    assert [e.event_id for e in continuity(library).events] == ["e-4"]


# -- A3–A5 --------------------------------------------------------------------------------------

def test_a3_the_director_alone_accepts_an_npc_taking_the_pcs_key(world):
    store, handles, library = world
    snatch = give(KEY2, to=TAM, agent=TAM, backed=None, sequence=8, summary="Tam snatches the brass key.")
    done = library.accept(snatch, controls=CONTROLS).commit()
    event, _ = library.read(done.ref, requested_by=DIRECTOR)
    assert [item.domain for item in event.operations] == ["director", "director"] and event.endorsements == ()
    assert continuity(library).graph.index.recorded("carries", TAM, KEY2, as_of=8, requested_by=DIRECTOR)


def test_a4_a_pc_joins_a_guild_with_contribution_and_endorsement(world):
    store, handles, library = world
    join = EffectProposal(C, CHAT, moment(7), "Wren joins the guild.",
                          (Op("begin", "member_of", WREN, GUILD, WREN, "c7"),), "narration")
    change = library.accept(join, controls=CONTROLS, contributions=[contribution(text="I sign the guild roll.")],
                            endorsements=[handles.endorse(join)])
    change.commit()
    assert continuity(library).graph.index.recorded("member_of", WREN, GUILD, as_of=7, requested_by=DIRECTOR)


def test_a5_knowledge_bridge_and_no_knowledge_files(world):
    store, handles, library = world
    before = snapshot(store.root)
    _, done = accept_give(library, handles)
    written = {key for key, value in snapshot(store.root).items() if before.get(key) != value and value is not None}
    assert {key.split("/")[2] for key in written} == {"accepted", "relations"}
    event, _ = library.read(done.ref, requested_by=DIRECTOR)
    bridged = as_knowledge_event(event)
    assert bridged.event_id == "e-1" and bridged.moment == Moment(7, "s1", "t7")
    assert not (store.root / "campaigns" / C / "evidence").exists()
    assert not (store.root / "campaigns" / C / "events").exists()
