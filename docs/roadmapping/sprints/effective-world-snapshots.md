# Consistent Effective-World Reads

**Outcome:** Every scene sees a coherent world plus its campaign-specific edits, independent of where source files live.

## Scope
- Offer get/list/search/resolve over world, campaign overlay and chosen imports.
- Pin a consistent read revision for an entire turn, export or worker job.
- Compare virtual resolution with a rebuildable compiled filesystem/provenance projection.

## Milestones
1. Wrap existing one-parent copy-on-write reads behind an adapter.
2. Test a turn that runs while a source or overlay changes.
3. Compare virtual and compiled approaches for latency, compatibility and recovery.

## Moonshot
Make thousands of distributed authored records behave like one locally inspectable virtual world without losing source ownership.

## Proving scenario
A changed imported NPC does not appear half-updated between prompt assembly and action resolution; deleting a cache changes no answer.

## Independence and interfaces
Works against current files with a single source first; identity qualification is an interface, not a mandatory package-manager prerequisite.

## Open questions
- Virtual, compiled or hybrid?
- What is the exact revision boundary?
- How do UI edits display an out-of-date snapshot?
