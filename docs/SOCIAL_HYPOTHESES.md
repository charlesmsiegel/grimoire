# NPC social hypotheses

Sprint 060's headless first slice is `grimoire.social_hypotheses`. An NPC can hold
an evidence-attributed conjecture about one actor's belief, or about that actor's
belief concerning another actor. A selected conjecture can change a proposed
tactic. Neither operation writes actual beliefs, accepted events or director truth.

`Hypothesis` names its exact owner, one or two `inferred_actors` in outer-to-inner
order, claim, confidence, evidence IDs, moment and provenance. For example,
`(mayor, pc)` with owner `spy` means the spy conjectures what the mayor thinks the
PC believes. The claim remains authored text: the API does not parse, prove or
infer it. Confidence is confidence in that claim, not its canonical standing.

`admit(ledger, hypothesis, knowledge=..., roster=..., definitions=..., controls=...,
authored_by=..., expected_revision=...)` requires a consistent current snapshot.
It resolves every actor through the exact campaign roster and effective entity,
requires agent capability, and permits only a director-authored conjecture owned
by a director-controlled NPC. Player-controlled actors can be targets of an NPC's
uncertain conjecture; they cannot be owners in this slice. Their interiority is
never authored here. Unknown, wrong-kind and wrong-campaign references fail closed.

Supporting evidence must comprise 1–8 distinct confirmed items held by the owner
at or before the conjecture's moment. Access is checked through the existing actor
knowledge projection; director events, another actor's evidence and unconfirmed
or future evidence do not suffice. Attribution proves availability, not logical
entailment. The director is responsible for writing a plausible interpretation;
this module is not a semantic oracle or autonomous inference engine.

A revision names the current `supersedes` head, preserves the owner and exact
inferred-actor chain, and has a strictly later moment. Prior records remain.
Ledger revision is the number of retained admissions. Repeating an identical ID,
record and original expected revision returns the same ledger after rechecking
current identity, evidence and NPC control. A changed record or expected revision
under the same ID is a conflict. A new record with a stale expected revision fails.
Constructors validate structural history; callers must use `admit` for authority
and evidence validation rather than construct a pre-populated ledger from
untrusted input.

`recall(ledger, owner, as_of=..., limit=8)` returns only that exact owner's current
hypotheses at the requested moment, latest first with ID tie breaks. Earlier
moments recover the previous head before a revision. `choose_tactic` takes one
explicit current hypothesis ID and a confidence threshold, returning either a
caller-supplied credited tactic or fallback, with hypothesis/evidence attribution.
It does not search anyone else's mind, execute the tactic or grant the target
knowledge. These are pure director/internal projections, not authenticated API
endpoints. A controller-facing adapter must authorize the requested owner and
revalidate current control before using a proposed tactic as an NPC action.

Structural bounds are 64 retained records, depth two, recall 1–8 items and
400 characters per claim, provenance or tactic. Overflow fails; history is not
silently pruned. These are safety bounds, not measured performance targets. No
provider calls, recursive reasoning or paid inference occur. The deterministic
selector is invoked only for a caller-selected salient hypothesis.

`backend/tests/test_social_hypotheses.py` proves a synthetic spy incorrectly
believes the mayor thinks the PC knows a secret, selects a bluff, then revises its
confidence from new evidence and changes tactics. The mayor's actual belief and
director ruling remain unchanged. Tests also cover first-order recall, privacy,
revision branching, idempotence, stale requests, authority changes, evidence
access, actor identity, collection mutation and bounded input.

This is an immutable in-memory domain contract. It adds no store schema,
migration, serialization, synchronization, play-runner integration or UI.
A future adapter must persist the authoritative human-editable records and bind
snapshot/revision checks atomically. Existing knowledge remains owned by
`Knowledge`/evidencestore; effects remain owned by the accepted-effect workflow.
Rumor delivery, offscreen simulation, full strategic agents and the Wave 8 UI exit
are separate work. These tests do not establish live social plausibility or
provider cost/quality measurements.
