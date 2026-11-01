"""Real on-disk damage to a stored campaign (Sprint 020, F23 on disk).

A real library holding U is copied for every seed and damaged with real file operations:
deleting, editing or truncating a record, renaming it to a look-alike, writing an unlisted
record, a newer schema, editing or deleting a middle batch, or replacing a record with a link.
Each seed then opens the library read-only and checks, through ``EvidenceLibrary``, that the four
guarantees hold against U. Only one director read is taken per seed; the full ``as_of`` sweep
runs in memory over the knowledge that read returned, and a few spot checks go through the
per-projection methods.
"""

from __future__ import annotations

import functools
import json
import os
import random
import tempfile
from pathlib import Path

from grimoire.core import DIRECTOR, Ruling, actor_knowledge, standing
from grimoire.evidence import encode
from grimoire.evidencestore import EvidenceLibrary
from grimoire.store import LibraryStore
from grimoire.store.jsonfmt import render
from grimoire.testing.fileops import snapshot

from evidence_fixtures import C, E1, INES, MARA, m
from store_helpers import OWNER, owner_handle
from test_evidence_assemble import U, _names, check_projections
from test_evidencestore_scenarios import append_u, seed_actors

KNOWLEDGE = f"campaigns/{C}/"


@functools.cache
def _template() -> tuple[tuple[str, bytes | None], ...]:
    with tempfile.TemporaryDirectory() as folder:
        with LibraryStore.create(Path(folder) / "lib") as store:
            seed_actors(store)
            append_u(EvidenceLibrary(store, owner_handle(), owner=OWNER))
        return tuple(snapshot(folder).items())


def _materialize(root: Path) -> Path:
    for key, data in _template():
        path = root / key
        if data is None:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return root / "lib"


def _knowledge_files(lib: Path, batch: bool) -> list[Path]:
    trees = [lib / KNOWLEDGE / "events", lib / KNOWLEDGE / "evidence"]
    found = sorted(path for tree in trees for path in tree.rglob("*.json") if path.is_file())
    return [path for path in found if ("/batch/" in path.as_posix()) == batch]


def _damage(lib: Path, rng: random.Random, outside: Path) -> str:
    records, batches = _knowledge_files(lib, False), _knowledge_files(lib, True)
    kind = rng.choice(["delete", "edit", "truncate", "lookalike", "unlisted", "newer_schema", "batch_edit",
                       "batch_delete", "link"])
    target = rng.choice(records) if records else None
    if target is None:
        return "none"
    if kind == "delete":
        target.unlink()
    elif kind == "edit":
        document = json.loads(target.read_bytes())
        document["payload"]["provenance"] = "hand edit"
        target.write_bytes(render(document))
    elif kind == "truncate":
        target.write_bytes(target.read_bytes()[:20])
    elif kind == "lookalike":
        target.rename(target.with_name(target.name.upper().replace(".JSON", ".json")))
    elif kind == "unlisted":
        forged = Ruling(C, f"r-x{rng.randrange(99)}", "p-lie", "established", m(9), "director", grounds=(E1,))
        ref, schema, payload = encode(forged)
        path = lib / KNOWLEDGE / "evidence" / "ruling" / f"{ref.record_id}.json"
        path.write_bytes(render({"format": "grimoire.knowledge-record/1", "address": str(ref), "schema": schema,
                                 "payload": payload, "previous": None}))
    elif kind == "newer_schema":
        document = json.loads(target.read_bytes())
        document["schema"] = document["schema"][:-1] + "9"
        target.write_bytes(render(document))
    elif kind in ("batch_edit", "batch_delete"):
        middle = batches[:-1]  # the newest batch is the documented, undetectable tail
        chosen = rng.choice(middle)
        if kind == "batch_delete":
            chosen.unlink()
        else:
            document = json.loads(chosen.read_bytes())
            names = sorted(document["payload"]["records"])
            if len(names) > 1:
                dropped = rng.choice(names)
                del document["payload"]["records"][dropped]
                family, record_id = dropped.split(":")
                folder = lib / KNOWLEDGE / ("events" if family == "event" else f"evidence/{family}")
                (folder / f"{record_id}.json").unlink(missing_ok=rng.random() < 0.5)
            chosen.write_bytes(render(document) + b" ")
    elif kind == "link":
        copy = outside / target.name
        copy.write_bytes(target.read_bytes())
        target.unlink()
        try:
            os.symlink(copy, target)
        except (OSError, NotImplementedError):
            copy.replace(target)
            return "link-unavailable"
    return kind


def test_real_damage_never_teaches_anyone_anything(tmp_path):
    kinds: set[str] = set()
    for seed in range(40):
        rng = random.Random(seed)
        root = tmp_path / f"s{seed}"
        lib = _materialize(root)
        outside = root / "outside"
        outside.mkdir()
        for _ in range(rng.randrange(1, 3)):
            kinds.add(_damage(lib, rng, outside))
        with LibraryStore.open(lib, writable=False) as store:
            evidence = EvidenceLibrary(store)
            stored = evidence.read(C, requested_by=DIRECTOR)
            check_projections(stored.knowledge)
            scan = store.campaign_knowledge(C)
            accounted = _names(stored.knowledge) | {q.name for q in stored.quarantined}
            for ref in (*scan.records, *scan.damaged):  # nothing on disk silently dropped
                if ref.family != "batch":
                    assert ref.name in accounted, (seed, ref.name)
            # spot checks through the service's own projection methods
            assert evidence.actor_knowledge(C, INES, as_of=9) == actor_knowledge(stored.knowledge, INES, as_of=9)
            kept = {p.proposition_id for p in stored.knowledge.propositions}
            if "p-truth" in kept:
                assert evidence.standing(C, "p-truth", as_of=9) in ("established", "reported", "unasserted")
            assert set(evidence.actor_knowledge(C, MARA, as_of=9).evidence) <= set(
                actor_knowledge(U, MARA, as_of=9).evidence)
            if "p-lie" in kept:
                assert standing(stored.knowledge, "p-lie", as_of=9) in ("refuted", "reported", "unasserted")
    assert {"delete", "edit", "truncate", "lookalike", "unlisted", "newer_schema", "batch_edit",
            "batch_delete"} <= kinds


def test_a_newer_builds_record_is_quarantined_with_its_chain(tmp_path):  # F16, as a newer build writes it
    from grimoire.core import KnowledgeRef
    from grimoire.evidence import BatchEntry, encode_batch
    from grimoire.evidencestore import EvidenceWriteError
    from grimoire.store import KnowledgeRecord, etag
    import pytest
    lib = _materialize(tmp_path)
    with LibraryStore.open(lib) as store:
        head = store.read(KnowledgeRef.batch(C, 6))
        ref = KnowledgeRef(C, "belief", "b-ines-3")
        newer = KnowledgeRecord(ref, "grimoire.belief/2", {"holder": str(INES), "proposition_id": "p-lie",
                                                           "stance": "doubted", "supersedes": "b-ines-2"})
        txn = store.transaction()
        txn.put(newer, None)
        tag = etag(txn.preview()[f"{KNOWLEDGE}evidence/belief/b-ines-3.json"])
        batch_ref, schema, payload = encode_batch(C, 7, head.etag, {ref.name: BatchEntry(
            tag, proposition="p-lie", holder=str(INES))})
        txn.put(KnowledgeRecord(batch_ref, schema, payload), None)
        txn.commit(owner_handle())
        evidence = EvidenceLibrary(store, owner_handle())
        stored = evidence.read(C, requested_by=DIRECTOR)
        reasons = {q.name: q.reason for q in stored.quarantined}
        assert reasons == {"belief:b-ines-3": "unsupported_schema", "belief:b-ines-1": "chain_quarantined",
                           "belief:b-ines-2": "chain_quarantined"}
        assert "log_broken" not in stored.problems and evidence.actor_knowledge(C, INES, as_of=9).current == ()
        check_projections(stored.knowledge)
        with pytest.raises(EvidenceWriteError) as caught:
            evidence.append(C, [U.events[0].__class__(C, "e9", "x", m(9), "director")])
        assert caught.value.reason == "ledger_damaged"
