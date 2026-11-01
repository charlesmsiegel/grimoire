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
    DIRECTOR, Belief, Knowledge, KnowledgeRef, Ruling, actor_knowledge, player_knowledge, standing,
)
from grimoire.evidence import ScannedFile, assemble, decode_batch, encode, encode_batch

from evidence_fixtures import (
    ACTORS, C, E1, INES, MARA, PC_CONTROLS, PLAYER, SAM, TAVI, all_records, fake_etag, in_memory_files, m,
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


def test_a_stray_huge_batch_number_costs_one_file():
    import time
    files = in_memory_files()
    files[KnowledgeRef("c1", "batch", "9999999999")] = ScannedFile(None, damage="malformed")
    started = time.perf_counter()
    assembly = assemble(C, files)
    assert time.perf_counter() - started < 2
    assert "log_broken" in assembly.problems and assembly.knowledge.beliefs == ()


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
    check_accounting(assembly, files)


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
                       "lookalike"])
    if not records:
        return "none"
    target = rng.choice(records)
    scanned = files[target]
    if kind in ("delete", "lookalike"):
        del files[target]
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
