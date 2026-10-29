# Human-editable V2 file storage and revisions

**Outcome:** Native canonical Markdown/JSON records support definitions and campaign instances independently, with stable references and safe revisioned writes.

## Scope and first proof
Persist one definition and two divergent campaign instances; reopen them; preserve unknown authored fields; reject a stale write.

## Subsequent milestones and moonshot
Versioned snapshots and change journals, atomic replacement, external edit handling and guarded cross-file mutations.

## Independence and interfaces
Canonical storage package, not graph indexes or the image blob store; requires V2 path and identity contracts.

## Proving scenario
A world change doesn't silently rewrite a pinned campaign instance; a failed write doesn't masquerade as success.

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
