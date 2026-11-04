"""Sprint 021 refusals, races, repairs and damage containment (F1–F13). Nothing is written on a refusal."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from grimoire.core import DIRECTOR, Moment
from grimoire.core.authority import AuthorityError
from grimoire.core.identity import InstanceRef, RelationRef, WrapUpRef
from grimoire.core.model import ControlAssignment
from grimoire.effects import EffectProposal, Op
from grimoire.effectstore import Endorsement, InvalidEffect, TurnEffectsLibrary
from grimoire.graphstore import RelationLibrary
from grimoire.store import LibraryStore, StaleWrite
from grimoire.testing.fileops import snapshot

from chat_fixtures import Clock, Ids
from effects_fixtures import (
    ANA, BOB, C, CHAT, CONTROLS, GUILD, INES, KEY1, KEY2, TAM, WREN, Handles, contribution, effects_library, give,
    moment, seed,
)
from graph_fixtures import instance_record
from purity import NETWORK_MODULES
from store_helpers import OWNER, authorizer, make_dir_link

SRC = Path(__file__).resolve().parents[1] / "src"
SEED_KEY1 = RelationRef(C, "r-seed-1")


@pytest.fixture
def world(tmp_path):
    handles = Handles()
    with LibraryStore.create(tmp_path / "lib") as store:
        seed(store, handles)
        yield store, handles, effects_library(store, handles)


def reason(call) -> str:
    with pytest.raises(InvalidEffect) as caught:
        call()
    return caught.value.reason


def accept(library, handles, proposal=None, contributions=None, endorsements=None, controls=CONTROLS):
    proposal = proposal or give()
    return library.accept(proposal, controls=controls,
                          contributions=[contribution()] if contributions is None else contributions,
                          endorsements=[handles.endorse(proposal)] if endorsements is None else endorsements)


def relations(store, handles) -> RelationLibrary:
    return RelationLibrary(store, handles.app, clock=Clock(), new_id=lambda prefix: "r-hand", owner=OWNER)


def write(root: Path, relative: str, content) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode())


def unchanged(store, call) -> None:
    before = snapshot(store.root)
    call()
    assert snapshot(store.root) == before


# -- F1/F2: authority ---------------------------------------------------------------------------------

def test_f1_pc_authorship_and_endorsement_refusals(world):
    store, handles, library = world
    p = give()
    assert reason(lambda: accept(library, handles, contributions=[])) == "pc_authorship"
    assert reason(lambda: accept(library, handles, contributions=[contribution(turn="t6")])) == "invalid_expression"
    assert reason(lambda: accept(library, handles, endorsements=[])) == "not_endorsed"
    assert reason(lambda: accept(library, handles, endorsements=[handles.endorse(p, principal=BOB)])) == \
        "invalid_endorsement"
    assert reason(lambda: accept(library, handles, endorsements=[Endorsement("c7", handles.player(), "sha256:"
                                                                             + "0" * 64)])) == "invalid_endorsement"
    untrusted = handles.authorizer.handle_for(ANA)
    foreign = authorizer().handle_for(ANA, trusted=True)
    app = handles.authorizer.handle_for("app:grimoire", trusted=True)
    for handle in (untrusted, foreign, app, handles.app):
        assert reason(lambda: accept(library, handles, endorsements=[Endorsement("c7", handle, p.fingerprint)])) == \
            "invalid_endorsement"
    assert reason(lambda: accept(library, handles, endorsements=[("c7", handles.player())])) == "invalid_endorsement"
    assert not (store.root / "campaigns" / C / "accepted").exists()


def test_f1_the_outcome_key_must_be_trusted_and_issued(world):
    store, handles, _ = world
    for handle in (handles.authorizer.handle_for(OWNER), authorizer().handle_for(OWNER, trusted=True)):
        with pytest.raises(AuthorityError):
            TurnEffectsLibrary(store, handle, issued=handles.authorizer.issued, clock=Clock(), new_id=Ids())


def test_f2_backing_on_a_director_op_and_ambiguous_control(world):
    store, handles, library = world
    assert reason(lambda: accept(library, handles, give(KEY2, agent=TAM))) == "unexpected_backing"
    both = CONTROLS + (ControlAssignment(C, "wren", BOB),)
    with pytest.raises(AuthorityError):
        accept(library, handles, controls=both)


# -- F3/F4: preconditions and inputs --------------------------------------------------------------

def test_f3_preconditions(world):
    store, handles, library = world
    nothing = give(KEY1, giver=INES, to=TAM, agent=None, backed=None)
    assert reason(lambda: library.accept(nothing, controls=CONTROLS)) == "precondition_failed"
    ghost = InstanceRef(C, "item", "ghost-key")
    lost = EffectProposal(C, CHAT, moment(), "A key appears.", (Op("begin", "carries", TAM, ghost),), "director")
    assert reason(lambda: library.accept(lost, controls=CONTROLS)) == "missing_endpoint"
    payload = json.loads((store.root / "campaigns/c/relations/r-seed-1.json").read_bytes())
    payload["address"] = "campaign:c#relation/r-dup"
    write(store.root, "campaigns/c/relations/r-dup.json", payload)  # the same fact recorded twice, by hand
    assert reason(lambda: accept(library, handles)) == "ambiguous_relation"


def test_f4_campaign_chat_and_sequence(world):
    store, handles, library = world
    other = EffectProposal("elsewhere", CHAT.__class__("elsewhere", "t-1"), moment(), "x",
                           (Op("begin", "has_met", InstanceRef("elsewhere", "character", "a"),
                               InstanceRef("elsewhere", "character", "b")),), "director")
    assert reason(lambda: library.accept(other, controls=())) == "missing_campaign"
    write(store.root, "campaigns/Elsewhere/x.json", b"{}")
    assert reason(lambda: library.accept(other, controls=())) == "missing_campaign"  # a look-alike folder
    nochat = give(chat=CHAT.__class__(C, "t-9"))
    assert reason(lambda: accept(library, handles, nochat, endorsements=[handles.endorse(nochat)])) == "missing_chat"
    library.accept(give(KEY2, to=TAM, agent=TAM, backed=None, sequence=8), controls=CONTROLS).commit()
    assert reason(lambda: accept(library, handles)) == "stale_sequence"  # t7 is behind the accepted t8


# -- F5: races between preview and commit ---------------------------------------------------------

def _stale(store, change) -> None:
    before = snapshot(store.root)
    with pytest.raises(StaleWrite):
        change.commit()
    assert snapshot(store.root) == before


def test_f5_relation_changed_after_preview(world):
    store, handles, library = world
    change = accept(library, handles)
    rel = relations(store, handles)
    rel.end(SEED_KEY1, until=Moment(5), provenance="director", expected=rel.read(SEED_KEY1)[1]).commit()
    _stale(store, change)


def test_f5_endpoint_replaced_after_preview(world):
    store, handles, library = world
    change = accept(library, handles)
    txn = store.transaction()
    txn.put(instance_record(TAM).__class__(TAM, {"replaced": True}, owner=OWNER), store.read(TAM).etag)
    txn.commit(handles.app)
    _stale(store, change)


def test_f5_duplicate_relation_after_preview(world):
    store, handles, library = world
    change = accept(library, handles)
    relations(store, handles).add(C, "carries", TAM, KEY1, since=Moment(9), provenance="director").commit()
    _stale(store, change)


def test_f5_same_effect_committed_twice_from_two_previews(world):
    store, handles, library = world
    first, second = accept(library, handles), accept(library, handles)
    first.commit()
    _stale(store, second)
    assert [e.event_id for e in library.continuity(C, as_of=9, requested_by=DIRECTOR).events] == ["e-1"]


def test_f5_a_later_event_committed_after_preview(world):
    store, handles, library = world
    change = accept(library, handles)
    library.accept(give(KEY2, to=TAM, agent=TAM, backed=None, sequence=8), controls=CONTROLS).commit()
    _stale(store, change)


def test_f5_a_duplicate_revoked_before_its_commit_is_stale(world):
    store, handles, library = world
    done = accept(library, handles).commit()
    retry = accept(library, handles)
    assert retry.duplicate_of == done.ref
    _, etag = library.read(done.ref, requested_by=DIRECTOR)
    library.revoke(done.ref, reason="r", expected=etag, endorsements=[handles.undo(done.ref)]).commit()
    with pytest.raises(StaleWrite):
        retry.commit()


def test_f5_a_duplicate_whose_effect_was_undone_by_hand_is_stale(world):
    store, handles, library = world
    accept(library, handles).commit()
    retry = accept(library, handles)
    _break_receipt(store, handles)
    with pytest.raises(StaleWrite):
        retry.commit()


def test_f5_two_begins_in_one_change_never_share_an_id(world):
    store, handles, _ = world
    values = iter(["e-1", "r-x", "r-x", "r-x", "r-y"])
    library = effects_library(store, handles, new_id=lambda prefix: next(values))
    both = EffectProposal(C, CHAT, moment(), "They meet and Ines joins.",
                          (Op("begin", "has_met", TAM, INES), Op("begin", "member_of", INES, GUILD)), "director")
    change = library.accept(both, controls=CONTROLS)
    assert sorted(change.preview()) == ["campaigns/c/accepted/e-1.json", "campaigns/c/relations/r-x.json",
                                        "campaigns/c/relations/r-y.json"]
    change.commit()


# -- F6: receipts, repair and orphans -------------------------------------------------------------

def _break_receipt(store, handles) -> None:
    """What a raced partial publish or a direct 017 edit leaves: e-1's ending of Wren's key is gone."""
    rel = relations(store, handles)
    rel.reopen(SEED_KEY1, provenance="editor", expected=rel.read(SEED_KEY1)[1]).commit()


def test_f6_receipt_mismatch_is_reported_refused_and_repairable(world):
    store, handles, library = world
    done = accept(library, handles).commit()
    _break_receipt(store, handles)
    view = library.continuity(C, as_of=8, requested_by=DIRECTOR)
    assert [(p.code, p.op) for p in view.problems] == [("receipt_mismatch", 0)]
    assert reason(lambda: accept(library, handles)) == "receipt_mismatch"
    _, etag = library.read(done.ref, requested_by=DIRECTOR)
    assert reason(lambda: library.revoke(done.ref, reason="r", expected=etag,
                                         endorsements=[handles.undo(done.ref)])) == "receipt_mismatch"
    library.revoke(done.ref, reason="repair", expected=etag, endorsements=[handles.undo(done.ref)],
                   partial=True).commit()
    event, _ = library.read(done.ref, requested_by=DIRECTOR)
    assert [(s.op, s.why) for s in event.revocation.skipped] == [(0, "not ended by this event")]
    view = library.continuity(C, as_of=8, requested_by=DIRECTOR)
    assert [(p.code, p.op) for p in view.problems] == [("partial_revocation", 0)]  # partly undone, and said so
    assert view.graph.index.relation("r-1", requested_by=DIRECTOR).retracted


def test_f6_a_mismatched_earlier_event_is_reported_but_does_not_block_other_effects(world):
    store, handles, library = world
    accept(library, handles).commit()
    _break_receipt(store, handles)
    snatch = give(KEY2, to=TAM, agent=TAM, backed=None, sequence=8)
    library.accept(snatch, controls=CONTROLS).commit()
    assert "receipt_mismatch" in {p.code for p in library.continuity(C, as_of=8, requested_by=DIRECTOR).problems}


def test_f6_an_undecodable_relation_file_blocks_acceptance(world):
    store, handles, library = world
    join = EffectProposal(C, CHAT, moment(7), "Tam joins.", (Op("begin", "member_of", TAM, GUILD),), "director")
    library.accept(join, controls=CONTROLS).commit()
    path = store.root / "campaigns/c/relations/r-1.json"
    document = json.loads(path.read_bytes())
    document["schema"] = "grimoire.relation/9"  # a newer build's file, or a hand edit
    path.write_bytes(json.dumps(document).encode())
    again = EffectProposal(C, CHAT, moment(8), "Tam joins.", (Op("begin", "member_of", TAM, GUILD),), "director")
    assert reason(lambda: library.accept(again, controls=CONTROLS)) == "undecodable_relation"


def test_f6_a_relation_file_damaged_after_preview_makes_the_acceptance_stale(world):
    store, handles, library = world
    change = library.accept(give(KEY2, to=TAM, agent=TAM, backed=None, sequence=8), controls=CONTROLS)
    write(store.root, "campaigns/c/relations/r-zz.json", b"{broken")
    _stale(store, change)


def test_f6_a_look_alike_relations_folder_blocks_acceptance(world):
    store, handles, library = world
    write(store.root, "campaigns/c/Relations/r-1.json", b"{}")
    assert reason(lambda: library.accept(give(KEY2, to=TAM, agent=TAM, backed=None, sequence=8),
                                         controls=CONTROLS)) == "undecodable_relation"


def test_f6_orphan_effects_are_reported(world):
    store, handles, library = world
    done = accept(library, handles).commit()
    (store.root / "campaigns/c/accepted" / f"{done.ref.event_id}.json").unlink()  # relations published, event not
    found = {(p.code, p.relation_id) for p in library.continuity(C, as_of=8, requested_by=DIRECTOR).problems}
    assert found == {("orphan_effect", "r-1"), ("orphan_effect", "r-seed-1")}


# -- F7: revocation -------------------------------------------------------------------------------

def test_f7_revocation_refusals(world):
    store, handles, library = world
    done = accept(library, handles).commit()
    _, etag = library.read(done.ref, requested_by=DIRECTOR)
    with pytest.raises(StaleWrite):
        library.revoke(done.ref, reason="r", expected="sha256:" + "0" * 64, endorsements=[handles.undo(done.ref)])
    assert reason(lambda: library.revoke(done.ref, reason="r", expected=etag)) == "not_endorsed"
    moved = Endorsement("c7", handles.player(BOB), give().fingerprint)  # the current controller is not the endorser
    assert reason(lambda: library.revoke(done.ref, reason="r", expected=etag, endorsements=[moved])) == \
        "invalid_endorsement"
    library.revoke(done.ref, reason="r", expected=etag, endorsements=[handles.undo(done.ref)]).commit()
    _, etag = library.read(done.ref, requested_by=DIRECTOR)
    assert reason(lambda: library.revoke(done.ref, reason="r", expected=etag,
                                         endorsements=[handles.undo(done.ref)])) == "already_revoked"


def test_f7_an_editorially_ended_relation_is_a_dependent(world):
    store, handles, library = world
    done = accept(library, handles).commit()
    rel = relations(store, handles)
    tam = RelationRef(C, "r-1")
    rel.end(tam, until=Moment(9), provenance="director", expected=rel.read(tam)[1]).commit()
    _, etag = library.read(done.ref, requested_by=DIRECTOR)
    assert reason(lambda: library.revoke(done.ref, reason="r", expected=etag,
                                         endorsements=[handles.undo(done.ref)])) == "has_dependents"
    library.revoke(done.ref, reason="r", expected=etag, endorsements=[handles.undo(done.ref)], partial=True).commit()
    event, _ = library.read(done.ref, requested_by=DIRECTOR)
    assert [s.op for s in event.revocation.skipped] == [1]


def test_f7_a_reopen_that_would_overlap_is_a_dependent(world):
    store, handles, library = world
    done = accept(library, handles).commit()
    relations(store, handles).add(C, "carries", WREN, KEY1, since=Moment(9), provenance="director").commit()
    _, etag = library.read(done.ref, requested_by=DIRECTOR)
    assert reason(lambda: library.revoke(done.ref, reason="r", expected=etag,
                                         endorsements=[handles.undo(done.ref)])) == "has_dependents"
    library.revoke(done.ref, reason="r", expected=etag, endorsements=[handles.undo(done.ref)], partial=True).commit()
    event, _ = library.read(done.ref, requested_by=DIRECTOR)
    assert [(s.op, s.why) for s in event.revocation.skipped] == [(0, "has_dependents: duplicate_relation")]


# -- F8: wrap-up ----------------------------------------------------------------------------------

def test_f8_wrap_up_refusals_and_problems(world):
    store, handles, library = world

    def wrap(cites, through=8, expected=None, requested_by=DIRECTOR):
        return library.wrap_up(CHAT, "s1", through=moment(through), summary="x", cites=cites, written_by="director",
                               requested_by=requested_by, expected=expected)

    first = wrap([], through=8).commit()  # nothing accepted yet
    assert reason(lambda: wrap("e-1")) == "invalid_field"
    with pytest.raises(AuthorityError):
        wrap([], requested_by="human:ana")
    done = accept(library, handles).commit()  # accepted after the wrap-up, inside its range
    assert ("incomplete_wrapup", "e-1") in {(p.code, p.event_id) for p in
                                            library.continuity(C, as_of=8, requested_by=DIRECTOR).problems}
    assert reason(lambda: wrap([])) == "uncited_event"
    assert reason(lambda: wrap(["e-1", "e-9"])) == "unknown_event"
    with pytest.raises(StaleWrite):
        wrap(["e-1"]).commit()  # exists: needs expected
    _, etag = library.read_wrapup(first.ref, requested_by=DIRECTOR)
    wrap(["e-1"], expected=etag).commit()
    _, event_etag = library.read(done.ref, requested_by=DIRECTOR)
    library.revoke(done.ref, reason="r", expected=event_etag, endorsements=[handles.undo(done.ref)]).commit()
    assert reason(lambda: wrap(["e-1"])) == "revoked_event"


def test_f8_a_hand_added_operations_key_makes_the_wrap_up_undecodable(world):
    store, handles, library = world
    accept(library, handles).commit()
    done = library.wrap_up(CHAT, "s1", through=moment(8), summary="x", cites=["e-1"], written_by="director",
                           requested_by=DIRECTOR).commit()
    path = store.root / "campaigns/c/wrapups" / f"{done.ref.wrapup_id}.json"
    document = json.loads(path.read_bytes())
    document["payload"]["operations"] = [{"kind": "end"}]
    path.write_bytes(json.dumps(document).encode())
    assert reason(lambda: library.read_wrapup(done.ref, requested_by=DIRECTOR)) == "undecodable_wrapup"
    view = library.continuity(C, as_of=8, requested_by=DIRECTOR)
    assert ("undecodable_wrapup", "unknown_field") in {(p.code, p.detail) for p in view.problems}
    library.accept(give(KEY2, to=TAM, agent=TAM, backed=None, sequence=8), controls=CONTROLS).commit()  # never blocks


# -- F9/F10 ---------------------------------------------------------------------------------------

def test_f9_a_duplicate_hidden_from_the_ledger_is_stopped_by_the_precondition(world, tmp_path):
    store, handles, library = world
    done = accept(library, handles).commit()
    (store.root / "campaigns/c/accepted" / f"{done.ref.event_id}.json").rename(tmp_path / "moved-away.json")
    assert reason(lambda: accept(library, handles)) == "precondition_failed"


def test_f10_reads_are_director_only(world):
    store, handles, library = world
    done = accept(library, handles).commit()
    for call in (lambda: library.continuity(C, as_of=8, requested_by=ANA),
                 lambda: library.read(done.ref, requested_by="tam"),
                 lambda: library.read_wrapup(WrapUpRef(C, "w-x"), requested_by=ANA)):
        with pytest.raises(AuthorityError):
            call()


# -- F12/F13: conventions, imports and blocking damage ----------------------------------------------

def test_f12_the_service_never_mints_handles():
    source = (SRC / "grimoire" / "effectstore" / "library.py").read_text(encoding="utf-8")
    assert "handle_for" not in source and "Authorizer" not in source


def _loaded(module: str, watched=NETWORK_MODULES) -> set[str]:
    code = (f"import sys, json; import {module}; "
            f"print(json.dumps(sorted(m for m in {list(watched)!r} "
            f"if any(n == m or n.startswith(m + '.') for n in sys.modules))))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         env={**os.environ, "PYTHONPATH": str(SRC)})
    return set(json.loads(out.stdout))


def test_f12_no_provider_or_network_imports():
    assert _loaded("grimoire.effects") == set()
    assert _loaded("grimoire.effectstore") == _loaded("grimoire.store")
    assert _loaded("grimoire.effectstore", ("grimoire.providers", "grimoire.capture", "grimoire.testing")) == set()


def _schema_newer(store):
    path = store.root / "campaigns/c/accepted/e-1.json"
    document = json.loads(path.read_bytes())
    document["schema"] = "grimoire.accepted-event/9"
    path.write_bytes(json.dumps(document).encode())


@pytest.mark.parametrize("damage", [
    _schema_newer,
    lambda store: write(store.root, "campaigns/c/accepted/e-1.json", b"{broken"),
    lambda store: write(store.root, "campaigns/c/accepted/e-7.json", (store.root / "campaigns/c/accepted/e-1.json")
                        .read_bytes()),  # address_mismatch
    lambda store: write(store.root, "campaigns/c/accepted/E-8.json", b"{}"),  # name_mismatch
    lambda store: write(store.root, "campaigns/c/accepted/notes.txt", b"x"),  # unrecognized
    lambda store: write(store.root, "campaigns/c/Accepted/e-9.json", b"{}"),  # a look-alike folder
    lambda store: write(store.root, "campaigns/c/accepted/e-6.json",
                        {"format": "grimoire.scope-record/9", "address": "campaign:c#accepted/e-6", "schema": "s",
                         "payload": {}}),  # unsupported_format
])
def test_f13_damage_under_accepted_blocks_accept_revoke_and_wrap_up(world, damage):
    store, handles, library = world
    done = accept(library, handles).commit()
    _, etag = library.read(done.ref, requested_by=DIRECTOR)
    damage(store)
    snatch = give(KEY2, to=TAM, agent=TAM, backed=None, sequence=8)
    assert reason(lambda: library.accept(snatch, controls=CONTROLS)) == "undecodable_event"
    assert reason(lambda: library.wrap_up(CHAT, "s1", through=moment(8), summary="x", cites=["e-1"],
                                          written_by="d", requested_by=DIRECTOR)) == "undecodable_event"
    try:
        current = library.read(done.ref, requested_by=DIRECTOR)[1]
    except Exception:
        current = etag
    assert reason(lambda: library.revoke(done.ref, reason="r", expected=current,
                                         endorsements=[handles.undo(done.ref)])) == "undecodable_event"
    assert "undecodable_event" in {p.code for p in library.continuity(C, as_of=8, requested_by=DIRECTOR).problems}


def test_f13_accepted_as_a_file_not_a_folder_blocks(tmp_path):
    handles = Handles()
    with LibraryStore.create(tmp_path / "lib") as store:
        seed(store, handles)
        write(store.root, "campaigns/c/accepted", b"a file, not a folder")
        library = effects_library(store, handles)
        snatch = give(KEY2, to=TAM, agent=TAM, backed=None, sequence=8)
        assert reason(lambda: library.accept(snatch, controls=CONTROLS)) == "undecodable_event"


def test_f13_a_linked_accepted_folder_blocks(world, tmp_path):
    store, handles, library = world
    outside = tmp_path / "outside"
    outside.mkdir()
    make_dir_link(store.root / "campaigns/c/accepted", outside)
    snatch = give(KEY2, to=TAM, agent=TAM, backed=None, sequence=8)
    assert reason(lambda: library.accept(snatch, controls=CONTROLS)) == "undecodable_event"


def test_f13_duplicate_ordinals_are_an_inconsistent_ledger(world):
    store, handles, library = world
    done = accept(library, handles).commit()
    snatch = give(KEY2, to=TAM, agent=TAM, backed=None, sequence=8)
    second = library.accept(snatch, controls=CONTROLS).commit()
    path = store.root / "campaigns/c/accepted" / f"{second.ref.event_id}.json"
    document = json.loads(path.read_bytes())
    document["payload"]["ordinal"] = 1  # as two offline devices merging could leave it
    path.write_bytes(json.dumps(document).encode())
    assert reason(lambda: library.accept(give(KEY1, giver=TAM, to=INES, agent=TAM, backed=None, sequence=9),
                                         controls=CONTROLS)) == "inconsistent_ledger"
    _, etag = library.read(done.ref, requested_by=DIRECTOR)
    assert reason(lambda: library.revoke(done.ref, reason="r", expected=etag,
                                         endorsements=[handles.undo(done.ref)])) == "inconsistent_ledger"
    assert "duplicate_ordinal" in {p.code for p in library.continuity(C, as_of=9, requested_by=DIRECTOR).problems}


def test_f13_an_ordinal_contradicting_the_sequence_is_an_inconsistent_ledger(world):
    store, handles, library = world
    accept(library, handles).commit()  # e-1, ordinal 1, sequence 7
    second = library.accept(give(KEY2, to=TAM, agent=TAM, backed=None, sequence=8), controls=CONTROLS).commit()
    path = store.root / "campaigns/c/accepted" / f"{second.ref.event_id}.json"
    document = json.loads(path.read_bytes())
    document["payload"]["moment"] = {"sequence": 5, "scene_id": "s1", "turn_id": "t8"}  # behind e-1, ahead in order
    path.write_bytes(json.dumps(document).encode())
    assert reason(lambda: library.accept(give(KEY1, giver=TAM, to=INES, agent=TAM, backed=None, sequence=9),
                                         controls=CONTROLS)) == "inconsistent_ledger"
    assert "inconsistent_ledger" in {p.code for p in
                                     library.continuity(C, as_of=9, requested_by=DIRECTOR).problems}


# -- brutal-review round 1 -------------------------------------------------------------------------

def test_an_acceptance_endorsement_cannot_be_replayed_as_consent_to_undo(world):
    store, handles, library = world
    p = give()
    done = accept(library, handles, p, endorsements=[handles.endorse(p)]).commit()
    _, etag = library.read(done.ref, requested_by=DIRECTOR)
    assert reason(lambda: library.revoke(done.ref, reason="r", expected=etag,
                                         endorsements=[handles.endorse(p)])) == "invalid_endorsement"
    library.revoke(done.ref, reason="r", expected=etag, endorsements=[handles.undo(done.ref)]).commit()
    event, _ = library.read(done.ref, requested_by=DIRECTOR)
    assert event.revocation.endorsements[0].fingerprint != event.endorsements[0].fingerprint


def test_two_endorsements_for_one_contribution_are_ambiguous(world):
    store, handles, library = world
    p = give()
    assert reason(lambda: accept(library, handles, endorsements=[handles.endorse(p), handles.endorse(p)])) == \
        "invalid_endorsement"
    assert not (store.root / "campaigns" / C / "accepted").exists()


def test_an_overlong_contribution_is_named(world):
    store, handles, library = world
    long_text = contribution(text="I give Tam the iron key. " * 900)
    assert reason(lambda: accept(library, handles, contributions=[long_text])) == "contribution_too_long"


@pytest.mark.parametrize("cites", [[1, "e-1"], [["e-1"]], [None], None, 7])
def test_wrap_up_refuses_non_text_cites(world, cites):
    store, handles, library = world
    assert reason(lambda: library.wrap_up(CHAT, "s1", through=moment(8), summary="x", cites=cites,
                                          written_by="d", requested_by=DIRECTOR)) == "invalid_field"


def test_continuity_of_a_missing_campaign_is_an_error(world):
    store, handles, library = world
    assert reason(lambda: library.continuity("nope", as_of=1, requested_by=DIRECTOR)) == "missing_campaign"


def test_a_direct_end_of_a_begun_relation_is_reported_as_edited(world):
    store, handles, library = world
    done = accept(library, handles).commit()
    rel = relations(store, handles)
    tam = RelationRef(C, "r-1")
    rel.end(tam, until=Moment(9), provenance="editor: Tam lost it", expected=rel.read(tam)[1]).commit()
    found = {(p.code, p.event_id, p.op) for p in library.continuity(C, as_of=9, requested_by=DIRECTOR).problems}
    assert found == {("edited_since", done.ref.event_id, 1)}


def test_a_later_event_ending_a_begun_relation_is_not_an_edit(world):
    store, handles, library = world
    accept(library, handles).commit()
    library.accept(give(KEY1, giver=TAM, to=INES, agent=TAM, backed=None, sequence=9), controls=CONTROLS).commit()
    assert library.continuity(C, as_of=9, requested_by=DIRECTOR).problems == ()


def test_an_event_file_landing_during_the_snapshot_makes_the_change_stale(world, tmp_path, monkeypatch):
    store, handles, library = world
    done = accept(library, handles).commit()  # e-1 at sequence 7
    path = store.root / "campaigns/c/accepted" / f"{done.ref.event_id}.json"
    saved = path.read_bytes()
    path.unlink()  # as if a sync tool had not delivered it yet
    original = RelationLibrary.graph

    def graph_then_deliver(self, campaign_id, *, requested_by):
        found = original(self, campaign_id, requested_by=requested_by)
        if not path.exists():
            path.write_bytes(saved)  # it lands while the snapshot is being taken
        return found

    monkeypatch.setattr(RelationLibrary, "graph", graph_then_deliver)
    join = EffectProposal(C, CHAT, moment(5, turn="t5"), "Ines joins.", (Op("begin", "member_of", INES, GUILD),),
                          "director")
    change = library.accept(join, controls=CONTROLS)  # decided without e-1 (floor 0)
    monkeypatch.setattr(RelationLibrary, "graph", original)
    _stale(store, change)


def test_accept_and_commit_read_the_relation_folder_once_not_per_operation(world, monkeypatch):
    import grimoire.graphstore.library as graphstore_library
    store, handles, library = world
    calls = []
    original = graphstore_library._decoded
    monkeypatch.setattr(graphstore_library, "_decoded", lambda *a: calls.append(1) or original(*a))
    items = [InstanceRef(C, "item", f"coin-{n}") for n in range(8)]
    for item in items:
        txn = store.transaction()
        txn.put(instance_record(item), None)
        txn.commit(handles.app)
    many = EffectProposal(C, CHAT, moment(), "Tam finds coins.",
                          tuple(Op("begin", "carries", TAM, item) for item in items), "director")
    library.accept(many, controls=CONTROLS).commit()
    assert calls == []  # every overlap check used the one snapshot (accept) or one read (commit)



# -- brutal-review round 2 -------------------------------------------------------------------------

def test_a_second_swipe_of_an_accepted_turn_is_refused(world):
    store, handles, library = world
    accept(library, handles).commit()
    other = give(KEY2)  # the same turn t7, a different effect
    assert reason(lambda: accept(library, handles, other, endorsements=[handles.endorse(other)])) == \
        "turn_already_accepted"


def test_a_turn_retried_at_a_new_sequence_after_a_reversal_is_refused(world):
    store, handles, library = world
    accept(library, handles).commit()  # e-1: t7, Wren gives key-1 to Tam
    back = give(KEY1, giver=TAM, to=WREN, agent=TAM, backed=None, sequence=9)
    library.accept(back, controls=CONTROLS).commit()  # e-2: Tam gives it back, so the precondition holds again
    retry = give(sequence=10, turn="t7")
    assert reason(lambda: accept(library, handles, retry, endorsements=[handles.endorse(retry)])) == \
        "turn_already_accepted"


def test_retrying_a_revoke_that_landed_says_already_revoked(world):
    store, handles, library = world
    done = accept(library, handles).commit()
    _, etag = library.read(done.ref, requested_by=DIRECTOR)
    library.revoke(done.ref, reason="r", expected=etag, endorsements=[handles.undo(done.ref)]).commit()
    assert reason(lambda: library.revoke(done.ref, reason="r", expected=etag,
                                         endorsements=[handles.undo(done.ref)])) == "already_revoked"


def test_a_chat_deleted_after_preview_makes_the_acceptance_stale(world):
    store, handles, library = world
    change = library.accept(give(KEY2, to=TAM, agent=TAM, backed=None, sequence=8), controls=CONTROLS)
    (store.root / "campaigns/c/chats/t-1.json").unlink()
    _stale(store, change)


def test_a_trusted_handle_of_a_non_human_controller_cannot_endorse(world):
    store, handles, library = world
    bot = "app:bot"
    controls = (ControlAssignment(C, "wren", bot), ControlAssignment(C, "tam", DIRECTOR))
    p = give()
    endorsement = Endorsement("c7", handles.authorizer.handle_for(bot, trusted=True), p.fingerprint)
    assert reason(lambda: library.accept(p, controls=controls, contributions=[contribution(by=bot)],
                                         endorsements=[endorsement])) == "invalid_endorsement"


@pytest.mark.parametrize("token", ["effect:accepted/e-9", "effect:accepted/e-1", "effect:revoked/e-1"])
def test_an_ending_with_a_token_of_no_later_active_event_is_an_edit(world, token):
    store, handles, library = world
    accept(library, handles).commit()
    rel = relations(store, handles)
    tam = RelationRef(C, "r-1")
    rel.end(tam, until=Moment(9), provenance=token, expected=rel.read(tam)[1]).commit()
    codes = {p.code for p in library.continuity(C, as_of=9, requested_by=DIRECTOR).problems}
    assert "edited_since" in codes


def test_a_duplicate_whose_ledger_moved_on_before_commit_is_stale(world):
    store, handles, library = world
    accept(library, handles).commit()
    retry = accept(library, handles)
    library.accept(give(KEY2, to=TAM, agent=TAM, backed=None, sequence=8), controls=CONTROLS).commit()
    with pytest.raises(StaleWrite):
        retry.commit()


def test_an_agent_that_does_not_exist_is_refused(world):
    store, handles, library = world
    ghost = InstanceRef(C, "character", "ghost-1")
    join = EffectProposal(C, CHAT, moment(), "Wren joins.", (Op("begin", "member_of", WREN, GUILD, ghost),),
                          "director")
    assert reason(lambda: library.accept(join, controls=CONTROLS)) == "missing_agent"


def test_ids_named_by_accepted_events_stay_taken_for_new_actors(world):
    from grimoire.chatstore import ChatLibrary
    from grimoire.effectstore import accepted_instance_ids
    store, handles, library = world
    pact = EffectProposal(C, CHAT, moment(), "Ines makes Wren and the guild allies.",
                          (Op("begin", "allied_with", WREN, GUILD, INES),), "director")
    library.accept(pact, controls=CONTROLS).commit()
    (store.root / "campaigns/c/instances/character/ines.json").unlink()  # ines is named only as an agent now
    assert "ines" in accepted_instance_ids(store, C)
    chats = ChatLibrary(store, handles.app, clock=Clock(), new_id=Ids(), owner=OWNER)
    assert "ines" in chats._campaign_taken(C)


def test_a_wrap_up_through_another_scene_is_refused(world):
    store, handles, library = world
    assert reason(lambda: library.wrap_up(CHAT, "s1", through=Moment(8, "s2", "t99"), summary="x", cites=[],
                                          written_by="d", requested_by=DIRECTOR)) == "invalid_moment"


def test_a_partial_revocation_that_leaves_an_item_held_twice_is_reported(world):
    store, handles, library = world
    done = accept(library, handles).commit()
    rel = relations(store, handles)
    tam = RelationRef(C, "r-1")
    rel.end(tam, until=Moment(9), provenance="editor", expected=rel.read(tam)[1]).commit()
    _, etag = library.read(done.ref, requested_by=DIRECTOR)
    library.revoke(done.ref, reason="r", expected=etag, endorsements=[handles.undo(done.ref)], partial=True).commit()
    view = library.continuity(C, as_of=8, requested_by=DIRECTOR)
    assert view.graph.index.recorded("carries", TAM, KEY1, as_of=8, requested_by=DIRECTOR)
    assert view.graph.index.recorded("carries", WREN, KEY1, as_of=8, requested_by=DIRECTOR)
    assert [(p.code, p.op) for p in view.problems] == [("partial_revocation", 1)]
