"""Durable events and evidence end to end on the real store (Sprint 020 proving scenario and failures).

Every fixture is synthetic under ``tmp_path``. No model is called, and the provider package is
never imported. U is the six-batch ledger of ``evidence_fixtures``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import traceback
from pathlib import Path

import pytest

from grimoire.chats import Launch
from grimoire.core import (
    DIRECTOR, AuthorityError, Belief, BeliefAuthorityError, ChatRef, Event, InstanceRef, KnowledgeError, Ruling,
    KnowledgeRef, Perception, PlayerContribution, Proposition, Report, Subject, actor_knowledge,
    director_knowledge, knowledge_about, player_knowledge, standing,
)
from grimoire.evidence import TEXT_LIMIT
from grimoire.evidencestore import EvidenceLibrary, EvidenceWriteError
from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest
from grimoire.store import KnowledgeRecord, LibraryStore, RecordEnvelope, StaleWrite, StoreError, WriteDenied
from grimoire.testing.fileops import snapshot

from chat_fixtures import ANYA, MARA as MARA_CARD, WORLD, chat_library, seed_sources
from evidence_fixtures import (
    ACTORS, C, DUKE, E1, GUILD, INES, LIE_TEXT, MARA, NPC_CONTROLS, ORRIN, PC_CONTROLS, PERCEPT, PLAYER,
    SAM, SAM_CONTRIBUTION, SUMMARY, TAVI, all_records, batches, controls_for, m,
)
from store_helpers import OWNER, owner_handle
from test_evidence_assemble import U

AS_OF = range(0, 10)


def seed_actors(store: LibraryStore, campaign: str = C, actors=ACTORS) -> None:
    txn = store.transaction()
    for actor in actors:
        txn.put(RecordEnvelope(InstanceRef(campaign, actor.kind, actor.instance_id), {}), None)
    txn.commit(owner_handle())


def append_u(evidence: EvidenceLibrary, upto: int = 6) -> None:
    for number, batch in enumerate(batches()[:upto], start=1):
        contributions = {"b-sam-2": SAM_CONTRIBUTION} if number == 6 else None
        evidence.append(C, batch, controls=controls_for(number), contributions=contributions).commit()


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_actors(opened)
        yield opened


@pytest.fixture
def evidence(store):
    return EvidenceLibrary(store, owner_handle(), owner=OWNER)


def knowledge_files(store) -> dict:
    return {path: data for path, data in snapshot(store.root).items()
            if data is not None and ("/events/" in path or "/evidence/" in path)}


# -- A1: the proving scenario -----------------------------------------------------------------

def test_a1_private_witness_false_report_and_bystander_survive_reopen(tmp_path):
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
    with LibraryStore.open(root, writable=False) as store:  # close, reopen, read only
        evidence = EvidenceLibrary(store)
        stored = evidence.read(C, requested_by=DIRECTOR)
        assert stored.knowledge == U and stored.quarantined == () and stored.problems == ()
        for as_of in AS_OF:
            for actor in ACTORS:
                assert evidence.actor_knowledge(C, actor, as_of=as_of) == actor_knowledge(U, actor, as_of=as_of)
                assert (evidence.knowledge_about(C, actor, E1, as_of=as_of)
                        == knowledge_about(U, actor, E1, as_of=as_of))
            assert (evidence.player_knowledge(C, PLAYER, PC_CONTROLS, as_of=as_of)
                    == player_knowledge(U, PLAYER, PC_CONTROLS, as_of=as_of))
            for proposition in U.propositions:
                pid = proposition.proposition_id
                assert evidence.standing(C, pid, as_of=as_of) == standing(U, pid, as_of=as_of)
            assert (director_knowledge(stored.knowledge, as_of=as_of, requested_by=DIRECTOR)
                    == director_knowledge(U, as_of=as_of, requested_by=DIRECTOR))
        # the bystander and the participants learned nothing; the duke recalls only what he sent
        for actor in (TAVI, GUILD):
            assert evidence.actor_knowledge(C, actor, as_of=9) == actor_knowledge(U.__class__(C), actor, as_of=9)
        duke = evidence.actor_knowledge(C, DUKE, as_of=9)
        assert duke.evidence == () and duke.current == () and [s.text for s in duke.sent] == [LIE_TEXT]
        # the deceived actor holds the report and her belief, never standing, ruling, sender or event
        ines = evidence.actor_knowledge(C, INES, as_of=9)
        assert [e.content for e in ines.evidence] == [LIE_TEXT] and ines.current[0].stance == "suspected"
        assert SUMMARY not in repr(ines) and "refuted" not in repr(ines) and "r-lie" not in repr(ines)
        # the witness's mistaken interpretation is hers, resting on what she saw
        mara = evidence.actor_knowledge(C, MARA, as_of=3)
        assert mara.current[0].proposition_id == "p-mis" and mara.evidence[0].content == PERCEPT
        # the rumour is reported, then refuted, never established
        assert [evidence.standing(C, "p-lie", as_of=t) for t in AS_OF] == (
            ["unasserted"] * 5 + ["reported"] + ["refuted"] * 4)  # received at 5, refuted at 6
    event_bytes = (root / "campaigns" / C / "events" / "e1.json").read_bytes().decode()
    assert "mara" not in event_bytes and "PERCEPT" not in event_bytes and "v-mara" not in event_bytes


def test_a1_files_are_one_per_record_plus_one_batch_per_append(store, evidence):
    append_u(evidence)
    files = knowledge_files(store)
    assert len(files) == len(all_records()) + 6
    assert sum("/evidence/batch/" in path for path in files) == 6


# -- A2: world-less chats ---------------------------------------------------------------------

@pytest.fixture
def chat_store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_sources(opened)
        yield opened


def test_a2_a_worldless_chat_owns_its_evidence_and_a_late_actor_knows_nothing(chat_store):
    chats = chat_library(chat_store)
    started = chats.start_chat(Launch("character", MARA_CARD)).commit()
    campaign, mara = started.scope.campaign_id, started.actors[0]
    evidence = EvidenceLibrary(chat_store, owner_handle(), owner=OWNER)
    records = [Event(campaign, "e1", "a lantern goes dark", m(1), "director"),
               Perception(campaign, "v1", mara, "e1", "witnessed", "the lantern flickers out", m(1), "director",
                          confirmed=True)]
    evidence.append(campaign, records, chat=started.chat).commit()
    _, chat_etag = chats.read_chat(started.chat)
    late = chats.attach_source(started.chat, ANYA, "character", expected=chat_etag).commit().actors[0]
    for as_of in AS_OF:
        view = evidence.actor_knowledge(campaign, late, as_of=as_of)
        assert view.evidence == () and view.current == () and view.sent == ()
    assert [e.content for e in evidence.actor_knowledge(campaign, mara, as_of=1).evidence] == ["the lantern flickers out"]
    stored = evidence.read(campaign, requested_by=DIRECTOR)
    assert stored.origins == {"event:e1": started.chat, "evidence:v1": started.chat}
    # (b) the same card in a second chat is a new scope: its actor sees nothing of the first
    second = chats.start_chat(Launch("character", MARA_CARD)).commit()
    assert second.scope != started.scope
    assert evidence.actor_knowledge(second.scope.campaign_id, second.actors[0], as_of=9).evidence == ()
    # (c) a continuity actor reused in a new chat of the same scope keeps exactly its own records
    third = chats.start_chat(Launch("character", MARA_CARD), scope=started.scope,
                             reuse_actors={("character", MARA_CARD.entity_key): mara}).commit()
    assert third.actors == ()  # no new actor: the same individual continues
    assert [e.content for e in evidence.actor_knowledge(campaign, mara, as_of=9).evidence] == ["the lantern flickers out"]
    # (d) naming the scope and attaching a world leave every knowledge file byte-identical
    before = knowledge_files(chat_store)
    _, scope_etag = chats.read_scope(started.scope)
    chats.name_scope(started.scope, "Harbor nights", expected=scope_etag).commit()
    _, scope_etag = chats.read_scope(started.scope)
    chats.attach_world(started.scope, WORLD, expected=scope_etag).commit()
    assert knowledge_files(chat_store) == before


# -- A3 and A4 --------------------------------------------------------------------------------

def test_a3_an_unconfirmed_off_stage_line_is_director_only(store, evidence):
    append_u(evidence, upto=3)
    assert evidence.actor_knowledge(C, ORRIN, as_of=9).evidence == ()
    flagged = director_knowledge(evidence.read(C, requested_by=DIRECTOR).knowledge, as_of=9, requested_by=DIRECTOR)
    assert [(item.evidence_id, item.confirmed) for item in flagged.evidence if item.holder == ORRIN] == [
        ("v-orrin", False)]
    before = knowledge_files(store)
    with pytest.raises(KnowledgeError) as caught:
        evidence.append(C, [Belief(C, "b-orrin", ORRIN, "p-lie", "believed", m(5), DIRECTOR, ("v-orrin",))],
                        controls=NPC_CONTROLS)
    assert caught.value.reason == "unconfirmed_support" and knowledge_files(store) == before


def test_a4_only_the_controller_authors_a_pc_belief_verbatim(store, evidence):
    append_u(evidence, upto=5)
    pc = batches()[5][0]
    for kwargs, reason in (
        (dict(contributions={"b-sam-2": SAM_CONTRIBUTION}, controls=NPC_CONTROLS), "wrong_author"),
        (dict(contributions=None, controls=PC_CONTROLS), "invalid_expression"),
        (dict(contributions={"b-sam-2": PlayerContribution(C, "c-1", "s3", "t1", "sam", PLAYER, "interiority",
                                                           "it did not rain")}, controls=PC_CONTROLS),
         "not_verbatim"),
    ):
        with pytest.raises(BeliefAuthorityError) as caught:
            evidence.append(C, [pc], **kwargs)
        assert caught.value.reason == reason
    director_pc = Belief(C, "b-sam-x", SAM, "p-rain", "rejected", m(8, "s3", "t1"), DIRECTOR, ("v-sam",),
                         supersedes="b-sam-1")
    with pytest.raises(BeliefAuthorityError) as caught:
        evidence.append(C, [director_pc], controls=PC_CONTROLS)
    assert caught.value.reason == "pc_belief_authorship"


def test_a4_an_npc_era_belief_is_inherited_after_reopen(tmp_path):
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER), upto=5)
    with LibraryStore.open(root, writable=False) as store:
        view = EvidenceLibrary(store).player_knowledge(C, PLAYER, PC_CONTROLS, as_of=9).actors[0]
        assert view.current == () and [b.belief_id for b in view.inherited] == ["b-sam-1"]
    with LibraryStore.open(root) as store:
        append_u_tail = batches()[5]
        EvidenceLibrary(store, owner_handle(), owner=OWNER).append(
            C, append_u_tail, controls=PC_CONTROLS, contributions={"b-sam-2": SAM_CONTRIBUTION}).commit()
    with LibraryStore.open(root, writable=False) as store:
        view = EvidenceLibrary(store).player_knowledge(C, PLAYER, PC_CONTROLS, as_of=9).actors[0]
        assert [b.belief_id for b in view.current] == ["b-sam-2"]
        assert [b.belief_id for b in view.inherited_history] == ["b-sam-1"]


def test_a5_the_same_appends_write_the_same_bytes(tmp_path):
    trees = []
    for name in ("one", "two"):
        with LibraryStore.create(tmp_path / name) as store:
            seed_actors(store)
            append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
            trees.append(knowledge_files(store))
    assert trees[0] == trees[1]


# -- failures: nothing is written ---------------------------------------------------------------

def refused(store, evidence, records, reason, **kwargs):
    before = knowledge_files(store)
    with pytest.raises((EvidenceWriteError, KnowledgeError)) as caught:
        evidence.append(C, records, **kwargs)
    assert caught.value.reason == reason
    assert knowledge_files(store) == before
    return caught.value


def test_f1_ledger_rules_fail_exactly_as_in_memory(store, evidence):
    append_u(evidence, upto=1)
    error = refused(store, evidence, [Perception(C, "v-x", TAVI, "e9", "witnessed", "x", m(2), "director",
                                                 confirmed=True)], "unknown_event")
    assert error.records == ("evidence:v-x",)


def test_f3_shape_campaign_chat_and_actors(store, evidence):
    rain = Event(C, "e9", "rain", m(1), "director")
    refused(store, evidence, [], "empty")
    refused(store, evidence, [Event(C, f"e{n}", "x", m(1), "director") for n in range(501)], "too_many")
    refused(store, evidence, ["not a record"], "invalid_record")
    refused(store, evidence, [Event("c2", "e9", "rain", m(1), "director")], "campaign_mismatch")
    refused(store, evidence, [rain], "unknown_chat", chat=ChatRef(C, "nope"))
    refused(store, evidence, [rain], "unknown_chat", chat=ChatRef("c2", "nope"))
    stranger = InstanceRef(C, "character", "stranger")
    refused(store, evidence, [Event(C, "e9", "x", m(1), "director", participants=(stranger,))], "unknown_actor")
    refused(store, evidence, [Proposition(C, "p9", "x", m(1), "director", about=(Subject("instance", str(stranger)),))],
            "unknown_actor")
    with pytest.raises(EvidenceWriteError) as caught:
        evidence.append("nobody", [Event("nobody", "e1", "x", m(1), "director")])
    assert caught.value.reason == "unknown_campaign"
    (store.root / "campaigns" / "typo" / "instances" / "character").mkdir(parents=True)  # an empty leftover
    with pytest.raises(EvidenceWriteError) as caught:
        evidence.append("typo", [Event("typo", "e1", "x", m(1), "director")])
    assert caught.value.reason == "unknown_campaign"


@pytest.mark.parametrize("make", [
    lambda who: Perception(C, "v9", who, "e9", "witnessed", "x", m(1), "director", confirmed=True),
    lambda who: Report(C, "v9", TAVI, "p9", "told", m(1), "director", confirmed=True, sender=who, addressed=True,
                       sent_at=m(1)),
    lambda who: Report(C, "v9", TAVI, "p9", "told", m(1), "director", confirmed=True, attributed_to=who),
    lambda who: Ruling(C, "r9", "p9", "established", m(1), "director", grounds=(Subject("instance", str(who)),)),
], ids=["holder", "sender", "attributed_to", "grounds"])
def test_f3_every_actor_slot_must_exist(store, evidence, make):
    stranger = InstanceRef(C, "character", "stranger")
    context = [Event(C, "e9", "x", m(1), "director"), Proposition(C, "p9", "x", m(1), "director")]
    error = refused(store, evidence, [*context, make(stranger)], "unknown_actor")
    assert len(error.records) == 1


def test_f3_an_existing_chat_of_another_campaign_is_unknown_here(store, evidence):
    from grimoire.store import ScopeRecord
    txn = store.transaction()
    txn.put(ScopeRecord(ChatRef("c2", "t1"), "test.chat/1", {}), None)
    txn.commit(owner_handle())
    refused(store, evidence, [Event(C, "e9", "x", m(1), "director")], "unknown_chat", chat=ChatRef("c2", "t1"))


def test_reading_an_absent_campaign_says_so(evidence):
    assert "campaigns/c-typo:absent" in evidence.read("c-typo", requested_by=DIRECTOR).problems
    assert evidence.read(C, requested_by=DIRECTOR).problems == ()


def test_standing_of_a_quarantined_proposition_is_refused_not_guessed(store, evidence):
    append_u(evidence)
    (store.root / "campaigns" / C / "evidence" / "proposition" / "p-truth.json").unlink()
    with pytest.raises(KnowledgeError) as caught:
        evidence.standing(C, "p-truth", as_of=9)
    assert caught.value.reason == "unknown_proposition"  # 012's answer for an id the ledger does not hold


def test_an_oversized_record_is_refused_with_a_reason(store, evidence):
    subjects = tuple(Subject("relation", f"relation-{n:07d}") for n in range(140_000))  # renders past 8 MiB
    refused(store, evidence, [Proposition(C, "p9", "x", m(1), "director", about=subjects)], "too_large")


def test_f4_f5_fresh_ids_and_no_backdating(store, evidence):
    append_u(evidence)  # high-water mark 8
    refused(store, evidence, [Event(C, "e1", "again", m(9), "director")], "duplicate_id")
    refused(store, evidence, [Event(C, "e9", "late", m(7), "director")], "backdated")
    refused(store, evidence, [Event(C, "e9", "far", m(8 + 1_000_001), "director")], "sequence_jump")
    evidence.append(C, [Event(C, "e-edge", "the largest allowed step", m(8 + 1_000_000), "director")])
    with pytest.raises(KnowledgeError) as caught:  # a delayed letter needs its proposition already stored
        evidence.append(C, [Proposition(C, "p-new", "x", m(8), "director"),
                            Report(C, "v-new", TAVI, "p-new", "written", m(9), "director", confirmed=True,
                                   sender=DUKE, addressed=True, sent_at=m(2))])
    assert caught.value.reason == "before_proposition"
    letter = Report(C, "v-late", TAVI, "p-lie", "written", m(9), "director", confirmed=True, sender=DUKE,
                    addressed=True, sent_at=m(2))  # sent_at 2 < H = 8: the single exception
    evidence.append(C, [letter]).commit()
    assert [s.text for s in evidence.actor_knowledge(C, DUKE, as_of=9).sent] == [LIE_TEXT, LIE_TEXT]


def test_f6_unstorable_records_are_refused_before_a_change_exists(store, evidence):
    refused(store, evidence, [Event(C, "e9", "x", m(1), "director"),
                              Perception(C, "v9", TAVI, "e9", "witnessed", "x" * (TEXT_LIMIT + 1), m(1),
                                         "director", confirmed=True)], "too_long")
    for bad_id in ("con", "aux", "e" * 124):  # a Windows device name; no room left for ".json"
        refused(store, evidence, [Event(C, bad_id, "x", m(1), "director")], "unstorable_id")


def test_f7_anything_changing_between_append_and_commit_is_stale(store, evidence):
    append_u(evidence, upto=1)
    first = evidence.append(C, [Event(C, "e7", "x", m(1), "director")])
    second = evidence.append(C, [Event(C, "e8", "y", m(1), "director")])
    first.commit()
    with pytest.raises(StaleWrite):
        second.commit()
    evidence.append(C, [Event(C, "e8", "y", m(1), "director")]).commit()  # a fresh append works
    pending = evidence.append(C, [Event(C, "e9", "z", m(1), "director", participants=(TAVI,))])
    (store.root / "campaigns" / C / "instances" / "character" / "tavi.json").unlink()
    with pytest.raises(StaleWrite):
        pending.commit()
    seed_actors(store, actors=(TAVI,))
    pending = evidence.append(C, [Event(C, "e9", "z", m(1), "director")])
    (store.root / "campaigns" / C / "evidence" / "belief").mkdir(parents=True, exist_ok=True)
    (store.root / "campaigns" / C / "evidence" / "belief" / "stray.txt").write_text("x")
    with pytest.raises(StaleWrite):
        pending.commit()
    (store.root / "campaigns" / C / "evidence" / "belief" / "stray.txt").unlink()
    pending = evidence.append(C, [Event(C, "e9", "z", m(1), "director")])
    (store.root / "campaigns" / C / "evidence" / "unknown").mkdir()  # a change only the problem list shows
    (store.root / "campaigns" / C / "evidence" / "unknown" / "x.json").write_text("{}")
    with pytest.raises(StaleWrite):
        pending.commit()


def test_f7_a_removed_chat_is_stale(chat_store):
    chats = chat_library(chat_store)
    started = chats.start_chat(Launch("character", MARA_CARD)).commit()
    evidence = EvidenceLibrary(chat_store, owner_handle(), owner=OWNER)
    pending = evidence.append(started.scope.campaign_id, [Event(started.scope.campaign_id, "e1", "x", m(1), "d")],
                              chat=started.chat)
    (chat_store.root / "campaigns" / started.scope.campaign_id / "chats" / f"{started.chat.chat_id}.json").unlink()
    with pytest.raises(StaleWrite):
        pending.commit()


def test_f8_approvals_gate_the_write_and_a_change_commits_once(store):
    authority = Authorizer([OWNER])
    authority.add_grant(Grant("g", "app:evidence", frozenset({Permission.APPLY}), ResourcePath.parse("campaigns"),
                              OWNER))
    gated = EvidenceLibrary(store, authority.handle_for("app:evidence"), owner=OWNER)
    change = gated.append(C, batches()[0], controls=NPC_CONTROLS)
    before = snapshot(store.root)
    with pytest.raises(WriteDenied):
        change.commit()
    assert snapshot(store.root) == before
    preview = change.preview()
    assert sorted(preview) == sorted(f"campaigns/{C}/" + ("events/" if r.name.startswith("event:") else
                                                          f"evidence/{r.family}/") + f"{r.record_id}.json"
                                     for r in [KnowledgeRef.from_name(C, n) for n in _names(batches()[0])]
                                     + [KnowledgeRef.batch(C, 1)])
    approvals = {}
    for number, (address, data) in enumerate(sorted(preview.items())):
        authority.register_approval(Approval(f"a{number}", "app:evidence", Permission.APPLY,
                                             ResourcePath.parse(address), payload_digest(data), OWNER))
        approvals[address] = f"a{number}"
    assert set(change.commit(approvals)) == set(preview)  # the same change, retried with approvals
    with pytest.raises(StoreError):
        change.commit(approvals)


def _names(records):
    from grimoire.evidence import record_ref
    return [record_ref(record).name for record in records]


def test_f10_a_store_level_put_is_not_a_supported_writer(store, evidence):
    append_u(evidence, upto=3)
    sneaky = Belief(C, "b-sneak", INES, "p-lie", "believed", m(5), DIRECTOR, ("v-ines",))
    from grimoire.evidence import encode
    txn = store.transaction()
    ref, schema, payload = encode(sneaky)
    txn.put(KnowledgeRecord(ref, schema, payload), None)
    txn.commit(owner_handle())
    assert evidence.actor_knowledge(C, INES, as_of=9).current == ()
    stored = evidence.read(C, requested_by=DIRECTOR)
    assert {q.name: q.reason for q in stored.quarantined} == {"belief:b-sneak": "unlisted"}
    refused(store, evidence, [Event(C, "e9", "x", m(5), "director")], "ledger_damaged")  # F18


def test_f18_a_stray_file_alone_blocks_appends(store, evidence):
    append_u(evidence, upto=1)
    stray = store.root / "campaigns" / C / "evidence" / "belief"
    stray.mkdir(parents=True, exist_ok=True)
    (stray / "notes.txt").write_text("x")
    assert evidence.read(C, requested_by=DIRECTOR).quarantined == ()
    error = refused(store, evidence, [Event(C, "e9", "x", m(1), "director")], "ledger_damaged")
    assert error.records == (f"campaigns/{C}/evidence/belief/notes.txt:unrecognized",)


def test_f19_the_raw_ledger_is_the_directors(evidence):
    for who in ("player-1", "narrator", None):
        with pytest.raises(AuthorityError):
            evidence.read(C, requested_by=who)  # type: ignore[arg-type]


def test_reads_work_read_only_and_appends_refuse_there(tmp_path):
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle()), upto=2)
    with LibraryStore.open(root, writable=False) as store:
        reader = EvidenceLibrary(store, owner_handle())
        assert reader.actor_knowledge(C, MARA, as_of=9).evidence
        with pytest.raises(EvidenceWriteError) as caught:
            reader.append(C, [Event(C, "e9", "x", m(4), "director")])
        assert caught.value.reason == "read_only"
    with LibraryStore.open(root) as store:
        with pytest.raises(EvidenceWriteError):
            EvidenceLibrary(store).append(C, [Event(C, "e9", "x", m(4), "director")])  # no handle


def test_text_utf8_cannot_hold_is_refused_as_a_reason(store, evidence):
    refused(store, evidence, [Event(C, "e9", "half \ud800 a pair", m(1), "director")], "invalid_field")
    refused(store, evidence, [Event(C, "e9", "x", m(1, "s\udfff"), "director")], "invalid_field")


def test_f22_errors_never_carry_record_text(store, evidence):
    secret = "SENTINEL-RECORD-TEXT"
    cases = [
        [Perception(C, "v9", TAVI, "e9", "witnessed", secret, m(1), "director", confirmed=True)],
        [Event(C, "e9", secret, m(1), "director", participants=(InstanceRef(C, "character", "x"),))],
        [Perception(C, "v9", TAVI, "e9", "witnessed", secret * 2000, m(1), "director", confirmed=True)],
        [Proposition(C, "p9", secret, m(1), "director"),
         Belief(C, "b9", TAVI, "p9", "believed", m(1), DIRECTOR, ())],
    ]
    for records in cases:
        with pytest.raises(Exception) as caught:
            evidence.append(C, records, controls=NPC_CONTROLS)
        rendered = "".join(traceback.format_exception(caught.value))
        assert secret not in str(caught.value) and secret not in rendered
        assert caught.value.__context__ is None or secret not in repr(caught.value.__context__)


def test_the_service_loads_no_provider():
    code = ("import sys; import grimoire.evidencestore, grimoire.evidence; "
            "bad = [m for m in sys.modules if m.startswith(('grimoire.providers', 'grimoire.capture'))]; "
            "assert not bad, bad")
    source = str(Path(__file__).parents[1] / "src")
    subprocess.run([sys.executable, "-c", code], check=True, env={**os.environ, "PYTHONPATH": source})
