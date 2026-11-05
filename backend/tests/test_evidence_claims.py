"""017's claim shapes stored with their propositions (Sprint 020, obligations from 017).

A shape is part of its proposition's file (``grimoire.proposition/2``), written only with a new
proposition in the same append, read back through assembly, and read by an actor only through
``claims_about``. Synthetic fixtures only.
"""

from __future__ import annotations

import json
import traceback

import pytest

from grimoire.core import (
    DIRECTOR, Belief, ControlAssignment, Event, InstanceRef, KnowledgeRef, Moment, Proposition, Report, Ruling,
    Subject, actor_knowledge, player_knowledge,
)
from grimoire.evidence import (
    CLAIMED_PROPOSITION_SCHEMA, SCHEMAS, EvidenceCodecError, ScannedFile, assemble, decode, decode_claimed, encode,
)
from grimoire.evidencestore import EvidenceLibrary, EvidenceWriteError, evidence_instance_ids
from grimoire.graph import ClaimSet, ClaimShape, claims_about
from grimoire.store import LibraryStore, StaleWrite

from evidence_fixtures import (
    ACTORS, C, CLAIMS, DUKE, GUILD, INES, MEMBER_TEXT, NPC_CONTROLS, ORRIN, PC_CONTROLS, PLAYER, SAM, TAVI, WREN,
    fake_etag, in_memory_files, m,
)
from store_helpers import OWNER, owner_handle
from test_evidence_assemble import AS_OF, U, U_CLAIMS, reasons
from test_evidencestore_scenarios import append_u, knowledge_files, seed_actors

SENTINEL = "SENTINEL-CLAIM-TEXT"
MARA = InstanceRef(C, "character", "mara")


def instance(*refs: InstanceRef) -> tuple[Subject, ...]:
    return tuple(Subject("instance", str(ref)) for ref in refs)


def shaped(pid="p-x", predicate="member_of", subject=DUKE, object_=GUILD, about=None, seq=9, text=SENTINEL):
    proposition = Proposition(C, pid, text, m(seq), "director",
                              about=instance(subject, object_) if about is None else about)
    return proposition, ClaimShape(C, pid, predicate, subject, object_)


def ref(name: str) -> KnowledgeRef:
    return KnowledgeRef.from_name(C, name)


# -- codec ------------------------------------------------------------------------------------

def test_a_shaped_proposition_round_trips_under_its_own_schema():
    proposition, shape = shaped()
    address, schema, payload = encode(proposition, None, shape)
    assert schema == CLAIMED_PROPOSITION_SCHEMA == "grimoire.proposition/2"
    assert payload["claim"] == {"predicate": "member_of", "subject": str(DUKE), "object": str(GUILD)}
    assert decode_claimed(address, schema, payload) == (proposition, None, shape)
    assert "campaign_id" not in payload["claim"] and "proposition_id" not in payload["claim"]


def test_an_unshaped_proposition_is_byte_for_byte_what_it_was():
    proposition, _ = shaped()
    assert encode(proposition) == encode(proposition, None, None)
    assert encode(proposition)[1] == SCHEMAS[Proposition] == "grimoire.proposition/1"
    assert "claim" not in encode(proposition)[2]
    assert decode_claimed(*encode(proposition)) == (proposition, None, None)


def test_an_unshaped_proposition_keeps_its_pinned_form():
    """A golden literal, not this code against itself: /1 and its payload are exactly what 020 wrote before."""
    proposition = Proposition(C, "p-g", "the duke is a sworn member of the guild", m(3, "s1"), "director",
                              about=instance(DUKE, GUILD))
    assert encode(proposition) == (KnowledgeRef(C, "proposition", "p-g"), "grimoire.proposition/1", {
        "text": "the duke is a sworn member of the guild", "moment": {"sequence": 3, "scene_id": "s1"},
        "provenance": "director", "about": [{"kind": "instance", "ref": "campaign:c1/character/duke"},
                                            {"kind": "instance", "ref": "campaign:c1/group/guild"}]})


@pytest.mark.parametrize("variant", [" campaign:c1/character/duke", "campaign:c1/character/duke ",
                                     "campaign:c1/character/duke/", "campaign:c1//character/duke",
                                     "CAMPAIGN:c1/character/duke", "campaign:c1/character/DUKE"])
def test_a_claim_endpoint_in_any_other_spelling_is_refused(variant):
    assert _refused(*_claim(subject=variant)) in ("invalid_field", "non_canonical", "not_overt")


def test_plain_decode_never_reads_a_proposition_without_its_shape():
    proposition, shape = shaped()
    with pytest.raises(EvidenceCodecError) as caught:
        decode(*encode(proposition, None, shape))
    assert caught.value.reason == "claimed_proposition" and caught.value.record == "proposition:p-x"
    assert decode(*encode(proposition)) == (proposition, None)


def _refused(address, schema, payload) -> str:
    with pytest.raises(EvidenceCodecError) as caught:
        decode_claimed(address, schema, payload)
    assert SENTINEL not in str(caught.value) and caught.value.__cause__ is None and caught.value.__context__ is None
    return caught.value.reason


def _claim(**change):
    proposition, shape = shaped()
    address, schema, payload = encode(proposition, None, shape)
    claim = {**payload["claim"], **change}
    return address, schema, {**payload, "claim": {k: v for k, v in claim.items() if v is not None}}


@pytest.mark.parametrize("payload_change, reason", [
    (dict(predicate="employs"), "unknown_predicate"),
    (dict(object=str(DUKE)), "self_relation"),
    (dict(object="campaign:c2/group/guild"), "campaign_mismatch"),
    (dict(object=str(TAVI)), "not_overt"),  # an endpoint the text does not overtly name
    (dict(subject=None), "invalid_field"),  # a missing key inside the claim
    (dict(extra="x"), "invalid_field"),  # an unknown key inside the claim
    (dict(predicate=1), "invalid_field"),
    (dict(subject="not a ref"), "invalid_field"),
])
def test_a_bad_claim_is_refused_with_a_reason(payload_change, reason):
    assert _refused(*_claim(**payload_change)) == reason


def test_the_schema_and_the_claim_key_must_agree():
    proposition, shape = shaped()
    address, schema, payload = encode(proposition, None, shape)
    assert _refused(address, SCHEMAS[Proposition], payload) == "unknown_field"  # /1 carries no claim
    bare = {key: value for key, value in payload.items() if key != "claim"}
    assert _refused(address, schema, bare) == "missing_field"  # /2 always does
    assert _refused(address, schema, {**payload, "claim": None}) == "invalid_field"
    assert _refused(address, schema, {**payload, "claim": [str(DUKE)]}) == "invalid_field"
    assert _refused(ref("ruling:p-x"), schema, payload) == "schema_mismatch"
    assert _refused(address, "grimoire.proposition/3", payload) == "unsupported_schema"


def test_encoding_refuses_a_shape_that_is_not_this_propositions():
    proposition, shape = shaped()
    other = ClaimShape(C, "p-other", "member_of", DUKE, GUILD)
    loose = ClaimShape(C, "p-x", "member_of", DUKE, TAVI)  # tavi is not in about
    for claim, reason in ((other, "invalid_claim"), (loose, "not_overt"), ("member_of", "invalid_claim")):
        with pytest.raises(EvidenceCodecError) as caught:
            encode(proposition, None, claim)  # type: ignore[arg-type]
        assert caught.value.reason == reason and SENTINEL not in str(caught.value)
    with pytest.raises(EvidenceCodecError) as caught:
        encode(Event(C, "e-x", SENTINEL, m(1), "director"), None, shape)
    assert caught.value.reason == "invalid_claim"


# -- assembly ---------------------------------------------------------------------------------

def test_a_hand_edited_claim_is_damage_and_takes_its_dependents():
    files = in_memory_files()
    target = ref("proposition:p-member")
    payload = {**files[target].payload, "claim": {**files[target].payload["claim"], "predicate": "allied_with"}}
    files[target] = ScannedFile(fake_etag(target, files[target].schema, payload), files[target].schema, payload)
    assembly = assemble(C, files)
    assert reasons(assembly)["proposition:p-member"] == "edited"
    assert reasons(assembly)["evidence:v-wren-member"] == "unknown_proposition"
    assert "p-member" not in assembly.claims
    view = actor_knowledge(assembly.knowledge, WREN, as_of=9)
    assert claims_about(view, ClaimSet(assembly.knowledge, assembly.claims.values()), DUKE).items == ()


def test_a_newer_builds_proposition_schema_is_quarantined_without_a_shape():
    files = in_memory_files()
    target = ref("proposition:p-member")
    files[target] = ScannedFile(files[target].etag, "grimoire.proposition/3", files[target].payload)
    assembly = assemble(C, files)
    assert reasons(assembly)["proposition:p-member"] == "unsupported_schema" and "p-member" not in assembly.claims


def test_an_older_build_quarantines_a_shaped_proposition(monkeypatch):
    """A 020-only build does not know /2: it fails closed exactly like any newer schema."""
    import grimoire.evidence.codec as codec
    monkeypatch.delitem(codec._CLASS_OF_SCHEMA, CLAIMED_PROPOSITION_SCHEMA)
    assembly = assemble(C, in_memory_files())
    assert reasons(assembly)["proposition:p-member"] == "unsupported_schema"
    assert reasons(assembly)["evidence:v-wren-member"] == "unknown_proposition" and not assembly.claims


def test_an_unlisted_shaped_proposition_gives_no_shape():
    files = in_memory_files()
    proposition, shape = shaped("p-stray")
    address, schema, payload = encode(proposition, None, shape)
    files[address] = ScannedFile(fake_etag(address, schema, payload), schema, payload)
    assembly = assemble(C, files)
    assert reasons(assembly)["proposition:p-stray"] == "unlisted" and set(assembly.claims) == {"p-member"}


# -- the store: proving scenario ----------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_actors(opened)
        yield opened


@pytest.fixture
def evidence(store):
    return EvidenceLibrary(store, owner_handle(), owner=OWNER)


def test_claims_survive_reopen_exactly_for_every_actor_node_and_moment(tmp_path):
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        seed_actors(store)
        append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
    with LibraryStore.open(root, writable=False) as store:
        evidence = EvidenceLibrary(store)
        stored = evidence.read(C, requested_by=DIRECTOR)
        assert dict(stored.claims.shapes) == CLAIMS and stored.claims.campaign_id == C
        for as_of in AS_OF:
            for node in ACTORS:
                for actor in ACTORS:
                    assert (evidence.claims_about(C, actor, node, as_of=as_of)
                            == claims_about(actor_knowledge(U, actor, as_of=as_of), U_CLAIMS, node))
                want = tuple(claims_about(view, U_CLAIMS, node)
                             for view in player_knowledge(U, PLAYER, PC_CONTROLS, as_of=as_of).actors)
                assert evidence.player_claims_about(C, PLAYER, PC_CONTROLS, node, as_of=as_of) == want
        # only the actor told holds the claim, attributed to its apparent author; never the other endpoint
        duke = evidence.claims_about(C, WREN, DUKE, as_of=9).items
        assert [(c.proposition_id, c.predicate, c.position, c.text) for c in duke] == [
            ("p-member", "member_of", "subject", MEMBER_TEXT)]
        assert [h.attributed_to for h in duke[0].heard] == [DUKE] and duke[0].stance == "rejected"
        assert evidence.claims_about(C, WREN, DUKE, as_of=6).items[0].stance == "believed"  # before reconsidering
        # Orrin sent it to Sam: Orrin recalls sending it, and Sam's NPC-era suspicion is inherited once a PC
        (orrin,) = evidence.claims_about(C, ORRIN, GUILD, as_of=9).items
        assert orrin.heard == () and [r.proposition_id for r in orrin.sent] == ["p-member"] and orrin.stance is None
        (sam,) = evidence.player_claims_about(C, PLAYER, PC_CONTROLS, DUKE, as_of=9)
        assert [(c.stance, c.inherited_stance) for c in sam.items] == [(None, "suspected")]
        assert [c.stance for c in evidence.claims_about(C, SAM, DUKE, as_of=9).items] == ["suspected"]
        assert evidence.claims_about(C, WREN, GUILD, as_of=9).items[0].position == "object"
        assert evidence.claims_about(C, WREN, DUKE, as_of=4).items == ()  # told at 5
        for actor in (TAVI, INES, DUKE, GUILD, MARA):  # holders of nothing about it: the same empty answer
            assert evidence.claims_about(C, actor, DUKE, as_of=9).items == ()
        assert "kind='group'" not in repr(evidence.claims_about(C, WREN, DUKE, as_of=9))  # no other endpoint


def test_a_shaped_proposition_is_one_file_under_its_own_schema(store, evidence):
    append_u(evidence, upto=1)
    files = knowledge_files(store)
    document = json.loads(files[f"campaigns/{C}/evidence/proposition/p-member.json"])
    assert document["schema"] == CLAIMED_PROPOSITION_SCHEMA
    assert document["payload"]["claim"] == {"predicate": "member_of", "subject": str(DUKE), "object": str(GUILD)}
    for pid in ("p-truth", "p-lie", "p-rain"):
        assert json.loads(files[f"campaigns/{C}/evidence/proposition/{pid}.json"])["schema"] == "grimoire.proposition/1"
    assert {"duke", "guild"} <= evidence_instance_ids(store, C)


def test_a_one_shot_iterable_of_claims_is_accepted(store, evidence):
    append_u(evidence, upto=1)
    proposition, shape = shaped()
    evidence.append(C, [proposition], claims=(s for s in [shape])).commit()
    assert evidence.read(C, requested_by=DIRECTOR).claims.shapes["p-x"] == shape


# -- the store: refusals write nothing and carry no text ------------------------------------------

def refused(store, evidence, records, reason, *, claims=(), records_named=None):
    before = knowledge_files(store)
    with pytest.raises(EvidenceWriteError) as caught:
        evidence.append(C, records, claims=claims)
    error = caught.value
    assert error.reason == reason, error.reason
    if records_named is not None:
        assert error.records == tuple(records_named)
    assert knowledge_files(store) == before
    text = "".join(traceback.format_exception(error))
    assert SENTINEL not in text and error.__cause__ is None and error.__context__ is None
    return error


def test_claims_must_be_claim_shapes(store, evidence):
    append_u(evidence, upto=1)
    proposition, shape = shaped()
    for claims in ("member_of", b"x", 7, [shape, "member_of"], [{"predicate": "member_of"}], ClaimSet(U, ())):
        refused(store, evidence, [proposition], "invalid_claim", claims=claims)


def test_a_shape_comes_only_with_its_proposition(store, evidence):
    append_u(evidence, upto=1)
    stored = ClaimShape(C, "p-truth", "member_of", DUKE, GUILD)  # a stored, unshaped proposition
    refused(store, evidence, [Event(C, "e-x", "x", m(9), "director")], "stored_proposition", claims=[stored],
            records_named=["proposition:p-truth"])
    nowhere = ClaimShape(C, "p-nowhere", "member_of", DUKE, GUILD)
    refused(store, evidence, [Event(C, "e-x", "x", m(9), "director")], "unknown_proposition", claims=[nowhere],
            records_named=["proposition:p-nowhere"])
    reshape = ClaimShape(C, "p-member", "allied_with", DUKE, GUILD)  # no re-shaping a stored shaped one either
    refused(store, evidence, [Event(C, "e-x", "x", m(9), "director")], "stored_proposition", claims=[reshape])


def test_017s_claim_rules_hold_on_write(store, evidence):
    append_u(evidence, upto=1)
    proposition, shape = shaped()
    refused(store, evidence, [proposition], "duplicate_claim", claims=[shape, shape],
            records_named=["proposition:p-x"])
    loose = ClaimShape(C, "p-x", "member_of", DUKE, TAVI)
    refused(store, evidence, [proposition], "not_overt", claims=[loose], records_named=["proposition:p-x"])
    c2 = ClaimShape("c2", "p-x", "member_of", InstanceRef("c2", "character", "duke"),
                    InstanceRef("c2", "group", "guild"))
    refused(store, evidence, [proposition], "campaign_mismatch", claims=[c2], records_named=["proposition:p-x"])


def test_012s_rules_and_earlier_steps_win_over_a_bad_shape(store, evidence):
    append_u(evidence, upto=1)
    _, loose = shaped("p-truth", object_=TAVI)
    duplicate = Proposition(C, "p-truth", "again", m(9), "director", about=instance(DUKE, TAVI))
    refused(store, evidence, [duplicate], "duplicate_id", claims=[loose])  # step 5 before the claim rules
    late, shape = shaped(seq=0)
    refused(store, evidence, [late], "backdated", claims=[shape])


def test_a_claim_endpoint_removed_before_commit_is_stale(store, evidence):
    append_u(evidence, upto=1)
    seed_actors(store, actors=(InstanceRef(C, "character", "kell"),))
    kell = InstanceRef(C, "character", "kell")
    proposition, shape = shaped(subject=kell)
    pending = evidence.append(C, [proposition], claims=[shape])
    (store.root / "campaigns" / C / "instances" / "character" / "kell.json").unlink()
    with pytest.raises(StaleWrite):
        pending.commit()
    assert "p-x" not in evidence.read(C, requested_by=DIRECTOR).claims.shapes


def test_a_shaped_proposition_supports_a_report_and_a_pc_belief_like_any_other(store, evidence):
    append_u(evidence)
    proposition, shape = shaped(subject=INES, object_=GUILD)
    report = Report(C, "v-x", MARA, "p-x", "told", m(9), "director", confirmed=True, attributed_to=INES)
    belief = Belief(C, "b-x", MARA, "p-x", "believed", m(9), DIRECTOR, ("v-x",))
    ruling = Ruling(C, "r-x", "p-x", "refuted", m(9), "director")
    evidence.append(C, [proposition, report, belief, ruling], claims=[shape], controls=NPC_CONTROLS).commit()
    mara = evidence.claims_about(C, MARA, GUILD, as_of=9).items
    assert [(c.proposition_id, c.stance, c.position) for c in mara] == [("p-x", "believed", "object")]
    assert "refuted" not in repr(mara)  # standing never appears in a claim
    controls = tuple(c if c.actor_id != "mara" else ControlAssignment(C, "mara", PLAYER) for c in PC_CONTROLS)
    (entry,) = [n for n in evidence.player_claims_about(C, PLAYER, controls, GUILD, as_of=9) if n.actor == MARA]
    assert [(c.stance, c.inherited_stance) for c in entry.items] == [(None, "believed")]  # an NPC-era belief
