# Ensemble conversation (051 first slice)

`grimoire.ensemble` schedules and runs a bounded text exchange. It is a headless
consumer of the existing separately voiced generation runtime. It returns
unreviewed proposals and writes no transcript, evidence, actor state or canon.
Wave 8 UI integration belongs to 115; the ordinary app play path is unchanged.

## Floor and bids

`schedule(campaign_id, turn_id, *, present, controls, initiator, addressees,
floor=None, player_speaking=False, bids=(), policy=None)` returns a sealed,
ephemeral `ConversationPlan`. Use typed `InstanceRef`s throughout. Every present
actor must have one controller, and only director-controlled present actors may
bid. Instance ids must be unique within the campaign even across entity kinds.

A `ConversationBid` has its actor, a kind (`answer`, `interject`, `aside`,
`silence`, `nonverbal`), addressed actors, an explicit audience, a private reason
and grounding. Optional scene role and attention metadata remain private to the
plan. A nonverbal bid carries an authored proposed beat; other bids cannot.
Reasons and beats are bounded to 1,000 characters, roles to 80.

Grounding is one of:

- `addressed`: this actor was an addressee of the initiating question and its
  sealed brief contains a line from the initiating speaker.
- `heard`: `heard_index` identifies a line in this actor's own brief.
- `intention`: this actor's own brief carries its existing goal choice.
- `listening`: a silence or nonverbal contribution, never a speaking bid.

Interjections require heard evidence or an intention and another NPC holding the
floor. They run first in bid order, then the floor holder’s answer, then other speaking
bids in bid order. This is a text interjection before the reserved response,
not editing or regenerating an already delivered sentence. Each actor contributes
at most once. Simultaneous bids are serialized; real-time overlap and TTS are
later milestones.

While `player_speaking=True`, the initiating human-controlled actor must hold the
floor. Every NPC bid is held and no call is planned. Finishing player speech
requires a newly scheduled exchange. No NPC may interrupt unfinished player
speech or author a player's response.

The scheduler validates citation ownership/availability, not whether a supplied
rationale is psychologically convincing or a question's semantics match its
metadata. Trusted callers derive bids from actor-local goals and evidence, not
from director-only truth. There is no model-priced speaker selection.

## Execution and privacy

`run_conversation(plan, runtime, meter, briefs, *, controls, present, token=None,
cancel=None)` requires the same current cast and owners and a `TurnMeter` whose
plan equals `plan.inference`. Supply one sealed `NpcBrief` for **every bidder**,
including silent/nonverbal bidders. It validates grounding and screens private
reasons and beats before sending any request. The runtime receives only speaking
briefs and a fixed `ContributionMode` instruction for each: direct answer, brief
interjection or side remark. Existing runtime callers with no modes keep the
same prompts. Raw reasons and other actors' internal state never enter these
instructions.

A bid's audience must include its speaker and addressees. The audience determines
which later generation calls hear its new line. An addressee is not necessarily
the only listener. Earlier/history lines must already be actor-projected when
building each brief, using the knowledge/communication owners. Audiences are
explicit trusted inputs; a graph connection or mere presence never grants
permission to hear a private line.

The full plan and `ConversationOutcome` are **director-private** diagnostics.
`plan.for_actor(actor)` gives an actor its own reason and other audience-visible
coordination kinds/addressees only, omitting others' reasons, attention, roles and
nonverbal text. Silence is private to its actor. This projection is not a memory
or delivery record. Generated lines and quiet proposals require the existing
review/acceptance owners before they become fiction.

`outcome.generation` is the existing `TurnOutcome`; `outcome.quiet` contains
silence/nonverbal proposals only after a complete, unheld exchange. Cancellation,
a halt or stale fencing withholds quiet proposals. Stale generation yields no
lines. All spent attempts remain in the meter. Every meter can execute once,
even when held, cancelled before sending or entirely silent; fresh execution
needs a fresh turn/meter. The in-process replay guard uses weak references and
a lock. It is not cross-process durable recovery.

Secret screening uses existing explicit markers; it is not a semantic proof
against paraphrases or PC-authorship prose. No output automatically becomes
canonical. No persistent schema changes or migration are introduced.

## Evidence and handoffs

`backend/tests/test_ensemble.py` exercises a direct question, grounded
interjection, meaningful silence, three separate voices, private side remarks,
player floor protection, nonverbal proposals, replay, cancellation and stale
cast/control failures with synthetic scripted providers only. The runtime's
existing retry/accounting and voice/privacy tests also run unchanged.

115 can consume the plan/projection and submit reviewed bids through its future
UI. 033 remains owner of current presence; 032/041 own communication and physical
hearing; 039/045 own intentions. This package does not replace those policies.
Optional planner/critic experiments are separately owned by 068 and are not
required by the scheduler.
