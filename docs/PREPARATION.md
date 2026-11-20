# Scenario preparation (064)

`grimoire.preparation` supplies an explicit headless director workflow for one
optional scene: premise, cast, location, questions, clues, tensions, stakes and
at least two possible directions. These are preparation notes, never canon.

Construct `PreparationSession(campaign_id, reader, proposer)` with a trusted
current-snapshot reader and a local deterministic/scripted proposer. Call
`prepare(request_id, brief)` explicitly. The proposer receives the current
`Snapshot`, whose evidence records carry qualified source/campaign references,
revisions and attributed text. Choices and goals require campaign references;
a source definition cannot establish a player's choice. Snapshot assembly must
read accepted player choices and appropriate director evidence, not model claims.
Each proposed `Note` names evidence IDs resolved in that exact snapshot.
`Snapshot.missing` lists unavailable secret, goal, calendar and choice categories;
these are missing dependencies, not inferred facts. An empty clue list is allowed.

A director reviews the exact immutable proposal using `Review`: credible stakes,
open agency and grounded content must each be acknowledged. `accept` rereads the
snapshot, comparing all content even if the revision token did not change. It
returns `PreparedScene`; it does not record a scene, event, belief, player action,
resolution or obstacle attempt. Even acceptance retries fail when stale. A new
request with updated choice evidence can prepare departure instead of a meeting;
no unplayed meeting becomes a fact. Prose credibility and freedom from forced PC
outcomes are human judgments, not automatic text classification.

Request IDs replay identical successful requests without another proposer call;
changed brief or snapshot under the same ID refuses. `reject` permanently blocks
that ID for this session. A deliberately new ID can request a new proposal; this
adapter does not detect semantic paraphrases. Exceptions before proposal creation
leave no request result, and retry may invoke the local proposer again. Session
operations serialize; callbacks must not reenter. State is in-memory and scoped
to one adapter lifetime, with no persistence or cross-process retry guarantee.

This is a trusted director-only library seam, not an authentication boundary.
115 must authorize callers, assemble current evidence and protect all output from
actor-facing disclosure. 113's campaign UI is untouched. Existing obstacles remain
owned by obstacles/obstaclestore: preparation never changes their facts, costs,
attempts or outcomes. Live inference requires a separately authorized, budgeted
provider adapter; none is included or called. Linked scenario sets, multi-session
kits, durable notes, obstacle conversion and UI integration remain later work.
