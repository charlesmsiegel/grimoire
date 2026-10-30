# Reviewable Multi-Record World Changes

**Outcome:** Authoring tools and agents can propose coordinated changes to Markdown/JSON records without silent overwrites.

## Scope
- Represent typed target, expected revision, precondition, evidence and proposed new content.
- Present per-file human-readable diffs, scope/permission checks, conflicts and explicit apply/reject decisions.
- Reuse guarded file publication with recovery and undo semantics appropriate to authored content.

## Milestones
1. Manually draft and apply a two-record world Change Set.
2. Detect an external hand edit between proposal and acceptance.
3. Allow a bounded authoring worker to submit exactly the same reviewed representation.

## Proving scenario
Add a location and update an existing guild link; a conflicting edit aborts or requests reconciliation, and no unrelated file changes.

## Independence and interfaces
No autonomous agent required. This edits source records; gameplay effects have different domain rules and need not share an event type.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- Its apply/reject decisions are the first approval UI: issue `grimoire.security` approvals only from a direct human action, showing `describe()` and the staged content its digest covers (007).

## Open questions
- All-or-nothing or recoverable staged edits?
- Who may approve auto-apply exceptions?
- What should undo mean after further outside edits?
