# Institutional choices (057)

`grimoire.institutions` is a pure, headless first slice. An institution has an exact
campaign group reference, a current leader, authored goals and policy. `decide`
records the leader's explicit collective choice while retaining every individual
preference, including dissent. It does not execute either choice. Two groups can
pursue conflicting goals without making their members share an opinion.

`InstitutionState`, `Institution`, `MemberPreference`, and `decide` are the public
entry points. A call supplies a proposal and selected choice, operation ID,
expected state revision, request `moment`, current `authorization_moment`,
audience/channel/provenance, and a frozen current
roster/definition/graph/control snapshot. Its result is `(new_state, decision)`.
`decision.request` is the full immutable receipt; `decision.dissent` contains the
preferences whose choice differs. The policy is unilateral leader authority, not
voting or automatically interpreted policy prose.

Membership is read from canonical `member_of` graph relations active at the
request moment and the current authorization moment. The mandatory
`authorization_moment` must be a `Moment` at or after the request moment. Direct members and members of one explicitly linked chapter
qualify; deeper ancestry is not traversed. The package stores no second membership
ledger. Exact roster identities and effective definitions are required. The
institution must have kind `group`; members and audience must be agents. Only
director-controlled NPC leaders and preference authors are supported. PC
preferences and decisions are refused in this slice, even if a caller claims to
represent their player. Existing player contribution contracts need a separately
reviewed adapter before PC authoring can be supported.

A new decision increments the revision once. An exact retry matches the complete
request, including agreeing preferences, audience and the original expected
revision. Changed payloads under the same operation ID fail. Current identity,
control and active membership are checked even for retries. Current authorization
time is excluded from request identity: a later authorized retry succeeds, but
a leader or member whose membership has ended or been retracted is refused.
Membership must also have held at the original moment, preventing backdated
choices by newly joined members. Changing the leader
requires a new request; old receipts retain their original leadership. Caller
snapshots are trusted domain inputs: this library does not authenticate a remote
user or atomically snapshot live files. A future storage/API adapter must bind
review to current graph, roster, control and institution revisions together.

Inputs are bounded to 256 relations and controls, 64 preferences, audience members
and retained decisions, 16 goals, and 400 characters per authored text. These are
structural safety bounds, not performance measurements. Oversized input fails;
history is never silently pruned. Mutable collections are frozen at admission.

Public audience/channel metadata is **not delivery**. `decide` never writes
`Knowledge`, accepted effects, reports, events or PC interiority. The synthetic
`test_guild_dissent_does_not_teach_distant_chapter` records the member's assistance
as a separate accepted `Event`, and explicitly gives the local PC a report of the
rejection and a perception of the assistance. The distant chapter receives neither.

No persistence schema, migrations, UI, provider calls, rumor transport, strategic
clock advancement, resource simulation or world economy are implemented. Existing
graph records remain canonical; future adapters must use existing effects and
evidence delivery contracts for accepted outcomes. Wave 8 UI integration belongs
to 115. These tests demonstrate headless contracts, not a playable institutional UI.
