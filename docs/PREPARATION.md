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

## Provider-backed generation

`PreparationGenerator(adapter, routes, handle, approvals=..., policy=...)` is a
`PreparationSession` proposer over the configured provider adapter (013). Each
explicit `prepare` that is not a replay plans one optional `planning` call for
the scene (no actor) under the caller's `InferencePolicy`; a policy without a
`planning` cap refuses `not_budgeted` and sends nothing. The call runs on an
untrusted worker handle with per-attempt approval, so a denied approval sends
nothing. The cap bounds calls per explicit request; there is no aggregate cap
across requests, and every retry spends another approved call. A snapshot with
no evidence or no location is refused before planning. `meters` keeps every
planned call's booked attempts, failures included. The proposer runs under the
session lock; an optional `CancelToken` can be passed when calling it directly.

`preparation_messages` puts fixed instructions in the system message and the
brief, evidence (id, kind, qualified source, text), missing categories and offered cast/location
references in one JSON data message; campaign text is never instructions. The
reply must be exactly one JSON object (optionally one fenced json block).
`parse_draft` rebuilds it into a `Draft` only from instance references copied
exactly from the offered lists and evidence IDs from the same snapshot. Display
names, other campaigns' references, extra fields, missing sections, fewer than
two distinct directions, ungrounded notes, invalid or non-UTF-8 text and
truncated, refused or unparseable replies all refuse the whole reply with a
`PreparationError` code that retains no reply text. A refused generation leaves
no proposal. Grounding is checked by evidence ID only: whether a draft actually
respects the latest choice is the director's `grounded` review, aided by the
prompt instruction, not an automatic check.

The prompt carries director-only secrets to the configured provider; callers
own that routing decision. Tests use a scripted transport only. No live-model
quality, credibility or cost has been measured.

This is a trusted director-only library seam, not an authentication boundary.
115 must authorize callers, assemble current evidence and protect all output from
actor-facing disclosure. 113's campaign UI is untouched. Existing obstacles remain
owned by obstacles/obstaclestore: preparation never changes their facts, costs,
attempts or outcomes. Live inference goes only through the generator above;
no app service or UI wires it yet. Linked scenario sets, multi-session
kits, durable notes, obstacle conversion and UI integration remain later work.
