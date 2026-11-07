"""Read-time assembly (Sprint 020): visible damage can only make actors know less.

Each named case removes or alters files of the synthetic ledger U and checks the outcome; a
seeded property test then applies random visible damage and checks the four guarantees against U
for every actor, the player and every ``as_of``: monotone fail-closed, never promotes, no
resurfacing, and nothing silently dropped.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from grimoire.core import (
    DIRECTOR, Belief, Event, Knowledge, KnowledgeRef, Perception, Proposition, Report, Ruling, Subject,
    actor_knowledge,
    player_knowledge, standing,
)
from grimoire.evidence import ScannedFile, assemble, batch_entry, decode_batch, encode, encode_batch

from grimoire.graph import ClaimSet, claims_about

from evidence_fixtures import (
    ACTORS, C, CLAIMS, E1, INES, MARA, ORRIN, PC_CONTROLS, PLAYER, SAM, TAG_A, TAG_B, TAVI, all_records, batches,
    fake_etag, forked_files, in_memory_files, m,
)

AS_OF = range(0, 10)


def ref(name: str) -> KnowledgeRef:
    return KnowledgeRef.from_name(C, name)


def ledger(records) -> Knowledge:
    groups: dict = {"Event": [], "Proposition": [], "Ruling": [], "Evidence": [], "Belief": [], "Disclosure": []}
    for record in records:
        kind = type(record).__name__
        groups["Evidence" if kind in ("Perception", "Report") else kind].append(record)
    return Knowledge(C, groups["Event"], groups["Proposition"], groups["Ruling"], groups["Evidence"],
                     groups["Belief"], groups["Disclosure"])


U = ledger(all_records())
U_CLAIMS = ClaimSet(U, CLAIMS.values())


def reasons(assembly) -> dict:
    return {q.name: q.reason for q in assembly.quarantined}


def edit_batch(files, seq, drop=None):
    """Rewrite batch ``seq`` (optionally dropping an entry) as a hand edit would: consistent, new etag."""
    batch_ref = KnowledgeRef.batch(C, seq)
    old = decode_batch(batch_ref, files[batch_ref].schema, files[batch_ref].payload)
    entries = {name: entry for name, entry in old.entries.items() if name != drop}
    _, schema, payload = encode_batch(C, seq, old.previous, entries)
    files[batch_ref] = ScannedFile(fake_etag(batch_ref, schema, [payload, "hand edit"]), schema, payload)


# -- named cases ------------------------------------------------------------------------------

def test_an_intact_ledger_assembles_to_exactly_u():
    assembly = assemble(C, in_memory_files())
    assert assembly.knowledge == U and assembly.clean and assembly.log_intact
    assert dict(assembly.claims) == CLAIMS
    assert assembly.head[0] == 6 and assembly.high_water == 8


def test_deleting_the_newest_ruling_never_resurfaces_the_older_one():  # F11
    files = in_memory_files()
    del files[ref("ruling:r-rumor-2")]
    assembly = assemble(C, files)
    assert reasons(assembly)["ruling:r-rumor-2"] == "missing"
    assert reasons(assembly)["ruling:r-rumor-1"] == "chain_quarantined"
    assert standing(U, "p-rumor", as_of=9) == "refuted"
    assert standing(assembly.knowledge, "p-rumor", as_of=9) == "unasserted"  # never "established"


def test_deleting_the_newest_belief_never_resurfaces_the_older_stance():  # F12
    files = in_memory_files()
    del files[ref("belief:b-mara-2")]
    assembly = assemble(C, files)
    assert reasons(assembly) == {"belief:b-mara-2": "missing", "belief:b-mara-1": "chain_quarantined"}
    view = actor_knowledge(assembly.knowledge, MARA, as_of=9)
    assert view.current == () and view.history == ()
    assert actor_knowledge(assembly.knowledge, INES, as_of=9) == actor_knowledge(U, INES, as_of=9)


def test_a_deleted_perception_takes_only_the_beliefs_resting_on_it():  # F13
    files = in_memory_files()
    del files[ref("evidence:v-mara")]
    assembly = assemble(C, files)
    assert reasons(assembly)["evidence:v-mara"] == "missing"
    assert reasons(assembly)["belief:b-mara-1"] == "unknown_evidence"
    assert reasons(assembly)["belief:b-mara-2"] == "chain_quarantined"
    for other in (INES, SAM):
        assert actor_knowledge(assembly.knowledge, other, as_of=9) == actor_knowledge(U, other, as_of=9)


def test_a_hand_edit_is_damage_not_a_correction():  # F14
    files = in_memory_files()
    target = ref("evidence:v-ines")
    _, schema, payload = encode(next(r for r in all_records() if getattr(r, "evidence_id", "") == "v-ines"))
    files[target] = ScannedFile(fake_etag(target, schema, {**payload, "channel": "told"}), schema,
                                {**payload, "channel": "told"})
    assembly = assemble(C, files)
    assert reasons(assembly)["evidence:v-ines"] == "edited"
    assert actor_knowledge(assembly.knowledge, INES, as_of=9).evidence == ()


def test_an_unlisted_established_ruling_is_never_canon():
    files = in_memory_files()
    forged = Ruling(C, "r-forged", "p-lie", "established", m(9), "director", grounds=(E1,))
    forged_ref, schema, payload = encode(forged)
    files[forged_ref] = ScannedFile(fake_etag(forged_ref, schema, payload), schema, payload)
    assembly = assemble(C, files)
    assert reasons(assembly)["ruling:r-forged"] == "unlisted"
    assert reasons(assembly)["ruling:r-lie"] == "chain_quarantined"
    assert standing(assembly.knowledge, "p-lie", as_of=9) == "reported"


@pytest.mark.parametrize("case", ["edited-batch-later-ruling", "gap", "edited-batch-drops-entry"])
def test_a_broken_log_quarantines_every_chain_family(case):  # F15 a-c
    files = in_memory_files()
    if case == "edited-batch-later-ruling":  # r-rumor-2 is in batch 5; batch 4 edited, r-rumor-2 deleted
        edit_batch(files, 4)
        del files[ref("ruling:r-rumor-2")]
    elif case == "gap":  # batch 4 and its belief deleted, batch 5 present
        del files[KnowledgeRef.batch(C, 4)]
        del files[ref("belief:b-ines-1")]
    else:  # r-lie is listed in batch 4; batch 4 edited to drop it; r-lie deleted; the break shows at 5
        edit_batch(files, 4, drop="ruling:r-lie")
        del files[ref("ruling:r-lie")]
    assembly = assemble(C, files)
    assert "log_broken" in assembly.problems and not assembly.log_intact and assembly.head is None
    assert assembly.knowledge.rulings == () and assembly.knowledge.beliefs == ()
    assert assembly.knowledge.disclosures == ()
    for proposition in ("p-rumor", "p-lie", "p-mis", "p-truth"):
        assert standing(assembly.knowledge, proposition, as_of=9) in ("reported", "unasserted")
    if case == "edited-batch-later-ruling":
        assert reasons(assembly)["ruling:r-rumor-2"] == "missing"  # hinted by batch 5


def test_a_newer_schema_quarantines_its_chain():  # F16
    files = in_memory_files()
    target = ref("belief:b-ines-2")
    files[target] = ScannedFile(files[target].etag, "grimoire.belief/2", files[target].payload)
    assembly = assemble(C, files)
    assert reasons(assembly)["belief:b-ines-2"] == "unsupported_schema"
    assert reasons(assembly)["belief:b-ines-1"] == "chain_quarantined"


def test_an_unlisted_undecodable_belief_quarantines_every_belief():  # F17
    files = in_memory_files()
    files[ref("belief:b-x")] = ScannedFile(None, damage="malformed")
    assembly = assemble(C, files)
    assert assembly.knowledge.beliefs == () and reasons(assembly)["belief:b-x"] == "unlisted"
    assert reasons(assembly)["belief:b-sam-1"] == "belief_family_damaged"
    assert assembly.knowledge.rulings == U.rulings  # other families untouched


def test_a_key_mismatch_quarantines_both_chains():  # F17
    files = in_memory_files()
    # b-mara-2's file now holds Ines's chain, but batch 5 recorded Mara's key with the same etag.
    target = ref("belief:b-mara-2")
    forged = Belief(C, "b-mara-2", INES, "p-lie", "rejected", m(7), DIRECTOR, ("v-ines",), supersedes="b-ines-1")
    _, schema, payload = encode(forged)
    files[target] = ScannedFile(files[target].etag, schema, payload)
    assembly = assemble(C, files)
    assert "key_mismatch" in assembly.problems and reasons(assembly)["belief:b-mara-2"] == "key_mismatch"
    assert {"belief:b-mara-1", "belief:b-ines-1", "belief:b-ines-2"} <= set(reasons(assembly))
    check_guarantees(assembly, files)


def test_only_the_record_whose_rule_failed_is_quarantined():
    files = in_memory_files()
    # Ines's belief now cites Mara's perception (foreign evidence): only Ines's chain goes.
    target = ref("belief:b-ines-1")
    forged = Belief(C, "b-ines-1", INES, "p-lie", "believed", m(6), DIRECTOR, ("v-ines", "v-mara"))
    _, schema, payload = encode(forged)
    files[target] = ScannedFile(files[target].etag, schema, payload)  # as if the writer had stored it
    assembly = assemble(C, files)
    assert reasons(assembly)["belief:b-ines-1"] == "foreign_evidence"
    assert "evidence:v-mara" not in reasons(assembly)
    assert actor_knowledge(assembly.knowledge, MARA, as_of=9) == actor_knowledge(U, MARA, as_of=9)


def test_a_newer_batch_format_reads_as_a_broken_log():  # forward compatibility (spec 9)
    files = in_memory_files()
    batch_ref = KnowledgeRef.batch(C, 3)
    files[batch_ref] = ScannedFile(files[batch_ref].etag, "grimoire.knowledge-batch/2", files[batch_ref].payload)
    assembly = assemble(C, files)
    assert "log_broken" in assembly.problems
    assert assembly.knowledge.rulings == () and assembly.knowledge.beliefs == () and assembly.knowledge.disclosures == ()
    check_guarantees(assembly, files)


def test_a_record_listed_twice_breaks_the_log():
    files = in_memory_files()
    batch_ref = KnowledgeRef.batch(C, 5)
    old = decode_batch(batch_ref, files[batch_ref].schema, files[batch_ref].payload)
    first = decode_batch(KnowledgeRef.batch(C, 1), files[KnowledgeRef.batch(C, 1)].schema,
                         files[KnowledgeRef.batch(C, 1)].payload)
    entries = {**old.entries, "event:e1": first.entries["event:e1"]}
    _, schema, payload = encode_batch(C, 5, old.previous, entries)
    files[batch_ref] = ScannedFile(files[batch_ref].etag, schema, payload)  # forged consistently
    assembly = assemble(C, files)
    assert "log_broken" in assembly.problems


def test_a_batch_without_a_byte_hash_breaks_the_log():
    files = in_memory_files()
    head = KnowledgeRef.batch(C, 6)
    files[head] = ScannedFile(None, files[head].schema, files[head].payload)
    assembly = assemble(C, files)
    assert "log_broken" in assembly.problems and assembly.head is None
    assert assembly.knowledge.rulings == () and assembly.knowledge.beliefs == () and assembly.knowledge.disclosures == ()
    check_guarantees(assembly, files)


def test_a_stray_huge_batch_number_costs_one_file():
    import time
    files = in_memory_files()
    files[KnowledgeRef("c1", "batch", "9999999999")] = ScannedFile(None, damage="malformed")
    started = time.perf_counter()
    assembly = assemble(C, files)
    assert time.perf_counter() - started < 2
    assert "log_broken" in assembly.problems and assembly.knowledge.beliefs == ()


def forge(files, seq, name):
    """A hand edit of one record that also rewrites its entry's etag in batch ``seq`` (one altered batch)."""
    target = ref(name)
    payload = dict(files[target].payload)
    if "confirmed" in payload:
        payload["confirmed"] = True  # an unconfirmed off-stage line becomes "delivered"
    else:
        payload["provenance"] = "forged"
    tag = fake_etag(target, files[target].schema, payload)
    files[target] = ScannedFile(tag, files[target].schema, payload)
    batch_ref = KnowledgeRef.batch(C, seq)
    old = decode_batch(batch_ref, files[batch_ref].schema, files[batch_ref].payload)
    entries = {**old.entries, name: type(old.entries[name])(tag, **{
        k: getattr(old.entries[name], k) for k in ("proposition", "holder", "event")})}
    _, schema, new_payload = encode_batch(C, seq, old.previous, entries)
    files[batch_ref] = ScannedFile(fake_etag(batch_ref, schema, [new_payload, "hand edit"]), schema, new_payload)


def test_only_batches_before_k_minus_1_are_trusted():
    """Batch 3 and Orrin's off-stage line edited together: the break shows at 4, and batch 3 is not trusted."""
    files = in_memory_files()
    forge(files, 3, "evidence:v-orrin")
    assembly = assemble(C, files)
    assert "log_broken" in assembly.problems
    assert reasons(assembly)["evidence:v-orrin"] == "log_broken"
    assert actor_knowledge(assembly.knowledge, ORRIN, as_of=9).evidence == ()
    check_guarantees(assembly, files)


def test_an_edit_that_names_another_holder_leaves_that_holders_chain_alone():
    files = in_memory_files()
    target = ref("belief:b-mara-2")
    edited = Belief(C, "b-mara-2", INES, "p-lie", "rejected", m(7), DIRECTOR, ("v-ines",), supersedes="b-ines-1")
    _, schema, payload = encode(edited)
    files[target] = ScannedFile(fake_etag(target, schema, payload), schema, payload)  # a new etag: an edit
    assembly = assemble(C, files)
    assert reasons(assembly) == {"belief:b-mara-2": "edited", "belief:b-mara-1": "chain_quarantined"}
    assert actor_knowledge(assembly.knowledge, INES, as_of=9) == actor_knowledge(U, INES, as_of=9)


def test_a_rule_failure_on_the_newest_belief_takes_its_whole_chain():
    """Inside the 012 loop: b-ines-2 now cites Mara's perception under its own etag (foreign_evidence)."""
    files = in_memory_files()
    target = ref("belief:b-ines-2")
    payload = {**files[target].payload, "evidence_ids": ["v-mara"]}
    files[target] = ScannedFile(files[target].etag, files[target].schema, payload)
    assembly = assemble(C, files)
    assert reasons(assembly) == {"belief:b-ines-2": "foreign_evidence", "belief:b-ines-1": "chain_quarantined"}
    assert actor_knowledge(assembly.knowledge, INES, as_of=9).current == ()  # never the older b-ines-1
    check_guarantees(assembly, files)


def test_dependents_of_a_rule_failure_drain_linearly():
    """A proposition that fails a 012 rule (about a later event) takes thousands of reports in linear time."""
    import time
    first = [Event(C, "e-late", "a bell", m(5), "director")]
    second = [Proposition(C, "p-big", "the bell rang", m(5), "director", about=(Subject("event", "e-late"),))]
    reports = [Report(C, f"v-r-{n}", MARA, "p-big", "told", m(5), "director", confirmed=True) for n in range(2000)]
    files = in_memory_files([first, second, *(reports[i:i + 500] for i in range(0, 2000, 500))])
    late = ref("event:e-late")
    files[late] = ScannedFile(files[late].etag, files[late].schema,  # read later than its proposition
                              {**files[late].payload, "moment": {"sequence": 9}})
    import sys
    module = sys.modules["grimoire.evidence.assemble"]  # the name is shadowed by the function
    builds = []
    real = module.build_ledger
    module.build_ledger = lambda *args: builds.append(1) or real(*args)
    try:
        started = time.perf_counter()
        assembly = assemble(C, files)
    finally:
        module.build_ledger = real
    assert time.perf_counter() - started < 5
    assert len(builds) <= 3  # one failing build, one valid one: never one rebuild per dependent
    assert reasons(assembly)["proposition:p-big"] == "about_future_event"
    assert sum(r == "unknown_proposition" for r in reasons(assembly).values()) == 2000


def test_the_high_water_mark_counts_every_family():
    files = in_memory_files(batches() + [[Report(C, "v-last", ORRIN, "p-lie", "told", m(12), "director",
                                                 confirmed=True)]])
    assert assemble(C, files).high_water == 12


def test_a_cascade_from_one_deleted_file_stays_linear():
    import time
    seen = [Perception(C, f"v-big-{n}", MARA, "e-big", "seen", "faces", m(1), "director", confirmed=True)
            for n in range(1500)]
    first = [Event(C, "e-big", "a crowd gathers", m(1), "director"),
             Proposition(C, "p-big", "a crowd gathered", m(1), "director", about=(Subject("event", "e-big"),))]
    beliefs = [Belief(C, f"b-big-{n}", MARA, "p-big", "believed", m(1), DIRECTOR, (f"v-big-{n}",))
               for n in range(400)]
    about = [Proposition(C, f"p-about-{n}", "the crowd", m(1), "director", about=(Subject("event", "e-big"),))
             for n in range(300)]
    grounded = [Ruling(C, f"r-g-{n}", f"p-about-{n}", "established", m(1), "director",
                       grounds=(Subject("event", "e-big"),)) for n in range(300)]
    files = in_memory_files([first, *(seen[i:i + 500] for i in range(0, 1500, 500)), beliefs, about, grounded])
    del files[ref("event:e-big")]
    started = time.perf_counter()
    assembly = assemble(C, files)
    assert time.perf_counter() - started < 5
    assert len(assembly.quarantined) == 1 + 1 + 1500 + 400 + 300 + 300 and assembly.knowledge.evidence == ()
    assert assembly.knowledge.rulings == ()


def test_a_consistent_tail_edit_is_the_documented_limit():
    """Like 010b ledgers: dropping the newest record and its entry from the newest batch is invisible."""
    files = in_memory_files()
    edit_batch(files, 6, drop="belief:b-sam-2")
    del files[ref("belief:b-sam-2")]
    assembly = assemble(C, files)
    assert assembly.clean  # nothing to see: the log is an integrity check, not authentication


def test_assembly_is_independent_of_input_order():
    files = in_memory_files()
    del files[ref("evidence:v-mara")]
    shuffled = list(files.items())
    random.Random(5).shuffle(shuffled)
    assert assemble(C, dict(shuffled)) == assemble(C, files)


# -- the property test (F23) ------------------------------------------------------------------

def _views(knowledge: Knowledge, as_of: int):
    actors = {a: actor_knowledge(knowledge, a, as_of=as_of) for a in ACTORS}
    return actors, player_knowledge(knowledge, PLAYER, PC_CONTROLS, as_of=as_of)


def _subset(small, big) -> bool:
    return set(small) <= set(big)


def check_guarantees(assembly, files) -> None:
    check_projections(assembly.knowledge)
    check_claims(assembly.knowledge, assembly.claims)
    check_accounting(assembly, files)


def _claims_within(got, want) -> None:
    """Per field (spec B1): ids, readings and text are U's; heard/sent are subsets; a stance is U's or gone."""
    wanted = {item.proposition_id: item for item in want.items}
    for item in got.items:
        theirs = wanted[item.proposition_id]
        assert (item.predicate, item.position, item.text) == (theirs.predicate, theirs.position, theirs.text)
        assert _subset(item.heard, theirs.heard) and _subset(item.sent, theirs.sent)
        assert item.stance in (theirs.stance, None) and item.inherited_stance in (theirs.inherited_stance, None)


def check_claims(knowledge: Knowledge, shapes) -> None:
    """Stored claims only ever shrink: every kept shape is U's own, and every claims_about answer is within U's."""
    kept = {p.proposition_id for p in knowledge.propositions}
    assert set(shapes) <= kept and all(CLAIMS[pid] == shape for pid, shape in shapes.items())
    claims = ClaimSet(knowledge, shapes.values())
    for as_of in AS_OF:
        (actors_a, player_a), (actors_u, player_u) = _views(knowledge, as_of), _views(U, as_of)
        for node in ACTORS:
            for a in ACTORS:
                _claims_within(claims_about(actors_a[a], claims, node), claims_about(actors_u[a], U_CLAIMS, node))
            assert [v.actor for v in player_a.actors] == [v.actor for v in player_u.actors]
            for got, want in zip(player_a.actors, player_u.actors):
                _claims_within(claims_about(got, claims, node), claims_about(want, U_CLAIMS, node))


def check_projections(knowledge: Knowledge) -> None:
    """Monotone fail-closed, no resurfacing and never promotes, against U, for everyone and every as_of."""
    for as_of in AS_OF:
        (actors_a, player_a), (actors_u, player_u) = _views(knowledge, as_of), _views(U, as_of)
        for a in ACTORS:  # monotone fail-closed, and no resurfacing (current is a subset)
            for bucket in ("evidence", "sent", "current", "history"):
                assert _subset(getattr(actors_a[a], bucket), getattr(actors_u[a], bucket)), (a, bucket, as_of)
        for got, want in zip(player_a.actors, player_u.actors):
            for bucket in ("evidence", "sent", "current", "history", "inherited", "inherited_history"):
                assert _subset(getattr(got, bucket), getattr(want, bucket)), (bucket, as_of)
        assert _subset(player_a.table_events, player_u.table_events), as_of
        for proposition in U.propositions:  # never promotes
            pid = proposition.proposition_id
            if pid not in {p.proposition_id for p in knowledge.propositions}:
                continue
            got, want = standing(knowledge, pid, as_of=as_of), standing(U, pid, as_of=as_of)
            assert got == want or got in ("reported", "unasserted"), (pid, as_of, got, want)
            assert not (got == "reported" and want == "unasserted")


def check_accounting(assembly, files) -> None:
    """Nothing silently dropped: every present record is kept or quarantined; absent listed ones are missing."""
    kept = _names(assembly.knowledge)
    quarantined = {q.name for q in assembly.quarantined}
    for file_ref in files:  # nothing silently dropped
        if file_ref.family != "batch":
            assert file_ref.name in kept | quarantined, file_ref.name
    if not assembly.log_intact:  # the break is reported; every absent record a readable batch lists is missing
        assert "log_broken" in assembly.problems
        reasons = {q.name: q.reason for q in assembly.quarantined}
        for file_ref, scanned in files.items():
            if file_ref.family != "batch":
                continue
            try:
                entries = decode_batch(file_ref, scanned.schema, scanned.payload).entries
            except Exception:
                continue
            for name in entries:
                if ref(name) not in files:
                    assert reasons.get(name) == "missing", name
    if assembly.log_intact:
        listed = set()
        for file_ref, scanned in files.items():
            if file_ref.family == "batch":
                listed |= set(decode_batch(file_ref, scanned.schema, scanned.payload).entries)
        for name in listed:
            if ref(name) not in files:
                assert dict((q.name, q.reason) for q in assembly.quarantined)[name] == "missing"


def _names(knowledge: Knowledge) -> set[str]:
    names = {f"event:{r.event_id}" for r in knowledge.events}
    names |= {f"proposition:{r.proposition_id}" for r in knowledge.propositions}
    names |= {f"ruling:{r.ruling_id}" for r in knowledge.rulings}
    names |= {f"evidence:{r.evidence_id}" for r in knowledge.evidence}
    names |= {f"belief:{r.belief_id}" for r in knowledge.beliefs}
    return names | {f"disclosure:{r.disclosure_id}" for r in knowledge.disclosures}


EXTRA = [
    Ruling(C, "r-forged", "p-lie", "established", m(9), "director", grounds=(E1,)),
    Belief(C, "b-tavi", TAVI, "p-truth", "believed", m(9), DIRECTOR, ("v-mara",)),
]


def damage(files: dict, rng: random.Random) -> str:
    records = sorted((r for r in files if r.family != "batch"), key=str)
    kind = rng.choice(["delete", "edit", "add", "undecodable", "newer_schema", "batch_edit", "batch_delete",
                       "consistent_edit", "same_etag_rule_break"])  # a look-alike rename is a real rename only on disk (damage test)
    if not records:
        return "none"
    target = rng.choice(records)
    scanned = files[target]
    if kind == "delete":
        del files[target]
    elif kind == "same_etag_rule_break":  # content another build reads differently: same bytes' etag,
        beliefs = [r for r in records if r.family == "belief" and files[r].damage is None]  # 012 rule broken
        evidence = [r.record_id for r in records if r.family == "evidence"]
        if beliefs and evidence:
            victim = rng.choice(beliefs)
            payload = {**files[victim].payload, "evidence_ids": [rng.choice(evidence)]}
            files[victim] = ScannedFile(files[victim].etag, files[victim].schema, payload)
    elif kind == "consistent_edit":  # edit a non-chain record AND its entry's etag, in one batch other than the newest
        listed = [(seq, entry_name) for seq in range(1, 6) if KnowledgeRef.batch(C, seq) in files
                  for entry_name in decode_batch(KnowledgeRef.batch(C, seq), files[KnowledgeRef.batch(C, seq)].schema,
                                                 files[KnowledgeRef.batch(C, seq)].payload).entries
                  if ref(entry_name).family in ("event", "proposition", "evidence") and ref(entry_name) in files
                  and files[ref(entry_name)].damage is None]
        if listed:
            seq, entry_name = rng.choice(listed)
            forge(files, seq, entry_name)
    elif kind == "edit":
        other = rng.choice([r for r in records if r.family == target.family])
        payload = files[other].payload
        files[target] = ScannedFile(fake_etag(target, files[other].schema, [payload, "edit"]), files[other].schema,
                                    payload)
    elif kind == "add":
        extra_ref = KnowledgeRef(C, rng.choice(["ruling", "belief", "disclosure", "evidence"]), f"x-{rng.randrange(99)}")
        files[extra_ref] = rng.choice([ScannedFile(None, damage="malformed"),
                                       ScannedFile("sha256:" + "f" * 64, *encode(rng.choice(EXTRA))[1:])])
    elif kind == "undecodable":
        files[target] = ScannedFile(scanned.etag, damage=rng.choice(["malformed", "address_mismatch", "link"]))
    elif kind == "newer_schema":
        files[target] = ScannedFile(scanned.etag, str(scanned.schema)[:-1] + "9", scanned.payload)
    elif kind == "batch_edit":  # never the newest batch: a consistent tail edit is undetectable by design
        seq = rng.randrange(1, 6)
        batch_ref = KnowledgeRef.batch(C, seq)
        if batch_ref in files:
            old = decode_batch(batch_ref, files[batch_ref].schema, files[batch_ref].payload)
            drop = rng.choice(sorted(old.entries))
            edit_batch(files, seq, drop=drop if len(old.entries) > 1 else None)
            if rng.random() < 0.7 and len(old.entries) > 1:
                files.pop(ref(drop), None)
    elif kind == "batch_delete":
        files.pop(KnowledgeRef.batch(C, rng.randrange(1, 6)), None)
    return kind


def test_random_visible_damage_never_teaches_anyone_anything():  # F23, in memory
    seen: Counter = Counter()
    partial = 0
    for seed in range(300):
        rng = random.Random(seed)
        files = in_memory_files()
        for _ in range(rng.randrange(1, 4)):
            damage(files, rng)
        assembly = assemble(C, files)
        check_guarantees(assembly, files)
        seen.update(q.reason for q in assembly.quarantined)
        seen.update(assembly.problems)
        if assembly.quarantined and _names(assembly.knowledge):
            partial += 1
    for reason in ("missing", "edited", "unlisted", "chain_quarantined", "log_broken", "unsupported_schema",
                   "malformed", "belief_family_damaged", "ruling_family_damaged", "disclosure_family_damaged",
                   "unknown_evidence"):
        assert seen[reason], reason  # every reachable path was exercised
    assert partial > 100


# -- 028: a log two devices appended to offline is a DAG of batches -----------------------------------

FORK_A4, FORK_A5 = KnowledgeRef.batch(C, 4, TAG_A), KnowledgeRef.batch(C, 5, TAG_A)
FORK_B4, JOIN = KnowledgeRef.batch(C, 4, TAG_B), KnowledgeRef.batch(C, 6, TAG_A)


def _rewrite(files, batch_ref, *, seq=None, previous="same", drop=None, add=None):
    """Re-encode one batch of ``files`` as a hand edit would (a new etag), optionally changing it."""
    old = decode_batch(batch_ref, files[batch_ref].schema, files[batch_ref].payload)
    entries = {name: entry for name, entry in old.entries.items() if name != drop}
    entries.update(add or {})
    seq = old.seq if seq is None else seq
    new_ref = KnowledgeRef.batch(C, seq, batch_ref.tag)
    _, schema, payload = encode_batch(C, seq, old.previous if previous == "same" else previous, entries, batch_ref.tag)
    del files[batch_ref]
    files[new_ref] = ScannedFile(fake_etag(new_ref, schema, [payload, "hand edit"]), schema, payload)


def test_a_log_two_devices_extended_offline_and_a_join_assembles_to_exactly_u():
    assembly = assemble(C, forked_files())
    assert assembly.knowledge == U and assembly.clean and assembly.log_intact
    assert dict(assembly.claims) == CLAIMS
    assert assembly.heads == ((6, forked_files()[JOIN].etag),) and assembly.head == assembly.heads[0]
    assert assembly.high_water == 8


def test_two_unjoined_heads_are_both_extended_by_the_next_append():
    files = forked_files()
    del files[JOIN], files[ref("belief:b-ines-2")]
    assembly = assemble(C, files)
    assert assembly.clean and assembly.heads == tuple(sorted(((4, files[FORK_B4].etag), (5, files[FORK_A5].etag))))


def test_two_devices_first_appends_are_two_roots():
    files = {}
    for tag, records in ((TAG_A, batches()[0][:1]), (TAG_B, batches()[0][1:2])):
        entries = {}
        for record in records:
            record_ref, schema, payload = encode(record)
            files[record_ref] = ScannedFile(fake_etag(record_ref, schema, payload), schema, payload)
            entries[record_ref.name] = batch_entry(record, files[record_ref].etag)
        batch_ref, schema, payload = encode_batch(C, 1, None, entries, tag)
        files[batch_ref] = ScannedFile(fake_etag(batch_ref, schema, payload), schema, payload)
    assembly = assemble(C, files)
    assert assembly.clean and len(assembly.heads) == 2 and {e.event_id for e in assembly.knowledge.events} == {"e1", "e2"}


@pytest.mark.parametrize("how", ["edited", "deleted"])
def test_damage_to_a_branch_head_a_join_names_trusts_nothing_of_it(how):
    files = forked_files()
    if how == "edited":
        _rewrite(files, FORK_B4)
    else:
        del files[FORK_B4]
    assembly = assemble(C, files)
    assert "log_broken" in assembly.problems and not assembly.log_intact and assembly.heads == ()
    assert reasons(assembly)["belief:b-sam-2"] in ("log_broken", "unlisted", "belief_family_damaged")
    check_guarantees(assembly, files)
    # the shared prefix and the other branch's events and evidence are still read
    assert {"e1", "e2", "e3"} <= {e.event_id for e in assembly.knowledge.events}


def test_a_join_that_skips_a_sequence_number_breaks_the_log():
    files = forked_files()
    _rewrite(files, JOIN, seq=7)
    assembly = assemble(C, files)
    assert "log_broken" in assembly.problems
    check_guarantees(assembly, files)


def test_a_record_both_branches_list_breaks_the_log():
    files = forked_files()
    shared = decode_batch(FORK_A4, files[FORK_A4].schema, files[FORK_A4].payload).entries
    name = "event:e3" if "event:e3" in shared else sorted(shared)[0]
    entry = next(decode_batch(r, f.schema, f.payload).entries[name] for r, f in files.items()
                 if r.family == "batch" and name in decode_batch(r, f.schema, f.payload).entries)
    _rewrite(files, FORK_B4, add={name: entry})
    del files[JOIN]  # the join named the old head: drop it so only the duplicate breaks the log
    files.pop(ref("belief:b-ines-2"))
    assembly = assemble(C, files)
    assert "log_broken" in assembly.problems
    check_guarantees(assembly, files)


def test_a_join_naming_one_parent_twice_or_unsorted_is_never_read():
    files = forked_files()
    old = decode_batch(JOIN, files[JOIN].schema, files[JOIN].payload)
    for bad in (list(old.parents[::-1]), [old.parents[0], old.parents[0]], [old.parents[0]]):
        with pytest.raises(Exception):
            decode_batch(JOIN, files[JOIN].schema, {**files[JOIN].payload, "previous": bad})


def _fork_damage(files: dict, rng: random.Random) -> str:
    """One visible damage to the forked log: record damage as for U, or an edit or deletion of a batch
    that some batch names (a head's own edit is the undetectable tail case, as for a linear log)."""
    if rng.random() < 0.5:
        return damage(files, rng)
    named = sorted((r for r in files if r.family == "batch" and r != JOIN), key=str)
    target = rng.choice(named)
    if rng.random() < 0.5:
        del files[target]
        return "batch_delete"
    old = decode_batch(target, files[target].schema, files[target].payload)
    drop = rng.choice(sorted(old.entries))
    _rewrite(files, target, drop=drop if len(old.entries) > 1 else None)
    if rng.random() < 0.7 and len(old.entries) > 1:
        files.pop(ref(drop), None)
    return "batch_edit"


def test_random_visible_damage_to_a_forked_log_never_teaches_anyone_anything():
    seen: Counter = Counter()
    for seed in range(150):
        rng = random.Random(seed)
        files = forked_files()
        kind = _fork_damage(files, rng)
        assembly = assemble(C, files)
        check_guarantees(assembly, files)
        seen[kind] += 1
        seen.update(assembly.problems)
    assert seen["log_broken"] and seen["batch_edit"] and seen["batch_delete"]
