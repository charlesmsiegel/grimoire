"""The Sprint 033 proving scenario, persisted: a boxed monster never speaks in an unrelated scene, and releasing
it deletes nothing it remembers.

Membership is written through ``PartyLibrary`` (preview, commit), the store is reopened, and party state and
scene casts are rebuilt from canonical relation files. The two creatures are 024-individualized goblins under
their own ``creature`` references. All fixtures are synthetic.
"""

from __future__ import annotations

import json

import pytest

from grimoire.core import DIRECTOR, Moment
from grimoire.graph import Relation
from grimoire.party import PartyRefused, cast_report, presence
from grimoire.store import LibraryStore
from grimoire.testing.fileops import snapshot

from graph_fixtures import relation_library
from party_fixtures import C, CREW, GRUB, INNKEEPER, MARA, SNIK, TOMA, party_library, seed


@pytest.fixture
def path(tmp_path):
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        seed(store)
    return root


def _two_creatures(path) -> None:
    """Milestone 1: Snik and Grub join Mara's party at 1; Grub is boxed at 4."""
    with LibraryStore.open(path) as store:
        party = party_library(store)
        change = party.join(MARA, SNIK, at=Moment(1), provenance="director")
        preview = change.preview()
        assert list(preview) == ["campaigns/c/relations/r-1.json"]
        payload = json.loads(preview["campaigns/c/relations/r-1.json"])["payload"]
        assert (payload["predicate"], payload["subject"], payload["object"]) == ("in_party", str(SNIK), str(MARA))
        assert change.before is None and change.after == (MARA, "party")
        change.commit()
        party.join(MARA, GRUB, at=Moment(1), provenance="director").commit()
        boxed = party.store_in_reserve(GRUB, at=Moment(4), provenance="director")
        assert sorted(boxed.preview()) == ["campaigns/c/relations/r-2.json", "campaigns/c/relations/r-3.json"]
        assert boxed.before == (MARA, "party") and boxed.after == (MARA, "reserve")
        result = boxed.commit()
        assert result.ended is not None and result.added is not None


def test_milestone_1_party_and_reserve_survive_reopening(path):
    _two_creatures(path)
    with LibraryStore.open(path) as store:  # reopened: rebuilt from canonical files
        party = party_library(store)
        assert party.view(C, as_of=9).state.party(MARA) == ((SNIK,), (GRUB,))
        assert party.view(C, as_of=2).state.party(MARA) == ((SNIK, GRUB), ())  # before the boxing (sorted by reference text)
        assert party.view(C, as_of=0).state.party(MARA) == ((), ())
        history = party.history(GRUB)
        assert [(r.predicate, r.since.sequence, r.interval()[1]) for r in history] == [
            ("in_party", 1, 4), ("in_reserve", 4, None)]
        assert party.view(C, as_of=9).state.problems == ()


def test_the_boxed_monster_never_speaks_in_an_unrelated_scene(path):
    _two_creatures(path)
    with LibraryStore.open(path) as store:
        relation_library(store).add(C, "owns", MARA, GRUB, since=Moment(1), provenance="director").commit()  # trainer link
        party = party_library(store)
        cast = party.stage(C, "s-inn", [MARA, INNKEEPER], as_of=6)
        assert cast.present == (INNKEEPER, MARA)
        assert not cast.may_speak(GRUB) and not cast.may_speak(SNIK)
        view = party.view(C, as_of=6)
        assert presence(view.state, cast, GRUB) == "reserved"
        assert presence(view.state, cast, SNIK) == "accompanying"  # nearby with Mara, but not on stage
        with pytest.raises(PartyRefused) as refused:
            party.stage(C, "s-inn", [MARA, INNKEEPER, GRUB], as_of=6)
        assert refused.value.reason == "in_reserve" and refused.value.members == (GRUB,)
        # a scene Mara is not in: her companions are absent too, whatever the owner link says
        elsewhere = party.stage(C, "s-market", [INNKEEPER], as_of=6)
        rows = {row.instance: row for row in cast_report(view.state, elsewhere, view.instances, controlled={MARA})}
        assert {ref: row.presence for ref, row in rows.items()} == {
            MARA: "absent", TOMA: "absent", INNKEEPER: "present", CREW: "absent", SNIK: "absent", GRUB: "reserved"}
        assert rows[MARA].controlled and not rows[SNIK].controlled
        assert rows[GRUB].membership.status == "reserve" and rows[INNKEEPER].membership is None


def test_a_creature_is_cast_under_its_own_reference(path):
    """033 owns non-character actors in a scene cast: a creature is staged with no re-key."""
    _two_creatures(path)
    with LibraryStore.open(path) as store:
        party = party_library(store)
        cast = party.stage(C, "s-road", [MARA, SNIK], as_of=6)
        assert cast.present == (MARA, SNIK) and cast.may_speak(SNIK)
        assert SNIK.kind == "creature"
        party.withdraw(GRUB, at=Moment(7), provenance="director").commit()
        assert party.stage(C, "s-road", [MARA, SNIK, GRUB], as_of=7).may_speak(GRUB)
        assert party.stage(C, "s-road", [MARA, SNIK], as_of=6).present == (MARA, SNIK)  # history is unchanged


def test_releasing_deletes_nothing_it_remembers(path):
    _two_creatures(path)
    before = snapshot(path)
    with LibraryStore.open(path) as store:
        party = party_library(store)
        change = party.release(GRUB, at=Moment(8), provenance="director")
        assert change.before == (MARA, "reserve") and change.after is None
        assert list(change.preview()) == ["campaigns/c/relations/r-3.json"]  # only its membership is ended
        change.commit()
        assert party.view(C, as_of=9).state.membership(GRUB) is None
        assert party.view(C, as_of=7).state.membership(GRUB).status == "reserve"  # history kept
    after = snapshot(path)
    changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    assert {key for key in changed if not key.startswith(".grimoire")} == {"campaigns/c/relations/r-3.json"}
    assert after["campaigns/c/instances/creature/goblin-2.json"] == before["campaigns/c/instances/creature/goblin-2.json"]
    with LibraryStore.open(path) as store:
        ended = [r for r in party_library(store).history(GRUB) if isinstance(r, Relation)]
        assert len(ended) == 2 and all(r.ending is not None for r in ended)


def test_a_party_spanning_two_player_characters(path):
    """Troupe play: the party anchor is a group; both PCs and a creature travel in it."""
    with LibraryStore.open(path) as store:
        party = party_library(store)
        for member in (MARA, TOMA, SNIK):
            party.join(CREW, member, at=Moment(2), provenance="director").commit()
        state = party.view(C, as_of=3).state
        assert state.party(CREW) == ((MARA, TOMA, SNIK), ())
        cast = party.stage(C, "s-camp", [MARA], as_of=3)
        assert presence(state, cast, TOMA) == "absent" and presence(state, cast, SNIK) == "absent"
        moved = party.move(SNIK, MARA, "reserve", at=Moment(5), provenance="director")
        assert moved.before == (CREW, "party") and moved.after == (MARA, "reserve")
        moved.commit()
        assert party.view(C, as_of=5).state.party(MARA) == ((), (SNIK,))
        assert party.view(C, as_of=5).state.party(CREW) == ((MARA, TOMA), ())


def test_the_director_graph_walks_membership(path):
    _two_creatures(path)
    with LibraryStore.open(path) as store:
        index = relation_library(store).graph(C, requested_by=DIRECTOR).index
        found = index.traverse(MARA, as_of=9, requested_by=DIRECTOR, direction="in",
                               predicates={"in_party", "in_reserve"})
        assert dict(found.reached) == {MARA: 0, SNIK: 1, GRUB: 1}
