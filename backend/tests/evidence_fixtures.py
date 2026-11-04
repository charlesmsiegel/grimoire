"""A synthetic six-batch campaign ledger (U) for the 020 tests. No user content.

U exercises every chain the quarantine rules protect:
- ruling chains: established→refuted (``p-rumor``), unknown→refuted (``p-mis``) and a single ruling (``p-truth``);
- belief chains of length 2 for Mara and Ines;
- an NPC-era belief of Sam (director-authored), superseded later by Sam's PC belief (player-1),
  so ``inherited`` and ``inherited_history`` are non-empty;
- a delayed, addressed letter from the duke to Ines (``sent_at`` before the high-water mark);
- an unconfirmed off-stage line overheard by Orrin;
- two disclosures of one event.

``tavi`` is a bystander, and ``guild`` (a group) and the duke are participants of the private event.
The private event's summary contains ``SUMMARY-TOKEN``, and Mara's percept contains ``PERCEPT-TOKEN``.
"""

from __future__ import annotations

import hashlib
import json

from grimoire.core import (
    DIRECTOR, Belief, ControlAssignment, Disclosure, Event, InstanceRef, Moment, Perception, PlayerContribution,
    Proposition, Report, Ruling, Subject,
)
from grimoire.evidence import ScannedFile, batch_entry, encode, encode_batch

C = "c1"
PLAYER = "player-1"


def actor(name: str, kind: str = "character") -> InstanceRef:
    return InstanceRef(C, kind, name)


MARA, INES, DUKE, ORRIN, SAM, TAVI = (actor(n) for n in ("mara", "ines", "duke", "orrin", "sam", "tavi"))
GUILD = actor("guild", "group")
ACTORS = (MARA, INES, DUKE, ORRIN, SAM, TAVI, GUILD)
SUMMARY = "SUMMARY-TOKEN the duke sold the guild's charter"
PERCEPT = "PERCEPT-TOKEN a sealed charter changing hands in the chapel"
LIE_TEXT = "the duke was framed by the guild"
RAIN_TEXT = "it rained on the docks"
E1, E2 = Subject("event", "e1"), Subject("event", "e2")


def m(sequence: int, scene: str | None = None, turn: str | None = None) -> Moment:
    return Moment(sequence, scene, turn)


#: the NPC era (sam directed) and the PC era (sam controlled by player-1)
NPC_CONTROLS = tuple(ControlAssignment(C, a.instance_id, DIRECTOR) for a in ACTORS if a.kind == "character")
PC_CONTROLS = tuple(c if c.actor_id != "sam" else ControlAssignment(C, "sam", PLAYER) for c in NPC_CONTROLS)
SAM_CONTRIBUTION = PlayerContribution(C, "c-1", "s3", "t1", "sam", PLAYER, "interiority", RAIN_TEXT)


def batches() -> list[list]:
    """U as six appends, in order; each is valid under the no-backdating rule."""
    return [
        [  # 1: the private betrayal, a witness and her mistaken interpretation; rain
            Event(C, "e1", SUMMARY, m(1), "director", participants=(DUKE, GUILD)),
            Event(C, "e2", "rain over the docks", m(1), "director"),
            Proposition(C, "p-truth", "the duke betrayed the guild", m(1), "director",
                        about=(E1, Subject("instance", str(DUKE)))),
            Proposition(C, "p-mis", "the guild sold its own charter", m(1), "director", about=(E1,)),
            Proposition(C, "p-lie", LIE_TEXT, m(1), "director", about=(Subject("instance", str(DUKE)),)),
            Proposition(C, "p-rain", RAIN_TEXT, m(1), "director", about=(E2,)),
            Proposition(C, "p-rumor", "the storm was summoned", m(1), "director", about=(E2,)),
            Perception(C, "v-mara", MARA, "e1", "witnessed", PERCEPT, m(1), "director", confirmed=True),
            Perception(C, "v-sam", SAM, "e2", "felt", "cold rain on the face", m(1), "director", confirmed=True),
            Belief(C, "b-mara-1", MARA, "p-mis", "believed", m(1), DIRECTOR, ("v-mara",)),
        ],
        [  # 2: first rulings; Sam's NPC-era belief; a bell sets the high-water mark to 4
            Ruling(C, "r-truth", "p-truth", "established", m(2), "director", grounds=(E1,)),
            Ruling(C, "r-rumor-1", "p-rumor", "established", m(2), "director", grounds=(E2,)),
            Ruling(C, "r-mis-1", "p-mis", "unknown", m(3), "director"),
            Belief(C, "b-sam-1", SAM, "p-rain", "believed", m(2), DIRECTOR, ("v-sam",)),
            Event(C, "e3", "a bell rings", m(4), "director"),
        ],
        [  # 3: the duke's delayed letter (sent at 3 < H=4, received at 5), an off-stage line, a disclosure
            Report(C, "v-ines", INES, "p-lie", "written", m(5), "director", confirmed=True, sender=DUKE,
                   attributed_to=DUKE, addressed=True, sent_at=m(3)),
            Report(C, "v-orrin", ORRIN, "p-lie", "overheard", m(5), "director", confirmed=False),
            Disclosure(C, "d1", "e1", m(5), "director"),
        ],
        [  # 4: the lie is refuted; Ines believes it anyway; a second disclosure of e1
            Ruling(C, "r-lie", "p-lie", "refuted", m(6), "director", grounds=(E1,)),
            Belief(C, "b-ines-1", INES, "p-lie", "believed", m(6), DIRECTOR, ("v-ines",)),
            Disclosure(C, "d2", "e1", m(6), "director"),
        ],
        [  # 5: rulings change; Mara reconsiders
            Ruling(C, "r-rumor-2", "p-rumor", "refuted", m(7), "director", grounds=(E2,)),
            Ruling(C, "r-mis-2", "p-mis", "refuted", m(7), "director", grounds=(E1,)),
            Belief(C, "b-mara-2", MARA, "p-mis", "rejected", m(7), DIRECTOR, ("v-mara",), supersedes="b-mara-1"),
        ],
        [  # 6: Sam is now a PC; Ines grows doubtful
            Belief(C, "b-sam-2", SAM, "p-rain", "rejected", m(8, "s3", "t1"), PLAYER, ("v-sam",),
                   supersedes="b-sam-1", contribution_id="c-1"),
            Belief(C, "b-ines-2", INES, "p-lie", "suspected", m(8), DIRECTOR, ("v-ines",), supersedes="b-ines-1"),
        ],
    ]


def controls_for(batch_number: int):
    return PC_CONTROLS if batch_number == 6 else NPC_CONTROLS


def all_records() -> list:
    return [record for batch in batches() for record in batch]


def fake_etag(ref, schema, payload) -> str:
    text = json.dumps([str(ref), schema, payload], sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def in_memory_files(record_batches=None) -> dict:
    """U as a scan would find it, with consistent stand-in etags."""
    files: dict = {}
    previous = None
    for seq, batch in enumerate(record_batches or batches(), start=1):
        entries = {}
        for record in batch:
            ref, schema, payload = encode(record)
            tag = fake_etag(ref, schema, payload)
            files[ref] = ScannedFile(tag, schema, payload)
            entries[ref.name] = batch_entry(record, tag)
        ref, schema, payload = encode_batch(C, seq, previous, entries)
        previous = fake_etag(ref, schema, payload)
        files[ref] = ScannedFile(previous, schema, payload)
    return files
