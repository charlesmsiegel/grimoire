# V2 library paths and V1 isolation

**Outcome:** V2 defaults to ~/.grimoire-v2/ rather than ~/.grimoire/; no implicit V1 reads or writes.

## Scope and first proof
Test path resolution, GRIMOIRE_V2_HOME overrides, symlink/case normalization, refusal to use a V1 source as a V2 destination, and no directory creation on an ordinary read.

## Subsequent milestones and moonshot
Add explicit library initialization, Android private default, and user-selected portable location.

## Independence and interfaces
V2's storage path/launching module; independent of entity schema.

## Proving scenario
Launching with no override never opens V1, while explicit migration requires an authorized source and distinct destination.

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
