# Rules Module Authoring and Validation UX

**Outcome:** An author can build, test and safely modify a mechanics module without patching Python or silently invalidating campaigns.

## Scope
- Expose manifest, sheets, checks, conditions, rules text and optional bounded hooks with strict validation.
- Provide understandable errors, templates, edition compatibility and version update previews.
- Retain current d20-basic/pool-basic examples while expanding beyond their simplifying assumptions.
- **Ruleset compilation moonshot:** turn an authorized rules reference or user-authored homebrew into a *proposed* module with linked rule evidence, edition caveats, executable checks and independent adversarial tests; never claim complete compliance based on unverified extraction.

## Milestones
1. Author a small validated module with one sheet and one check.
2. Preview compatibility/migration effects against existing characters.
3. Supply interactive tests for rule scripts and authoring extension policies.
4. Prototype source-grounded rule extraction/compilation on a small user-owned rules excerpt, with human review of every inferred mechanic and appropriate licensing.

## Moonshot
A visual rule editor compiles well-specified homebrew into versioned, reproducible mechanics with generated adversarial tests.

## Proving scenario
A malformed module cannot bind; a successful edit affects new checks without changing a previously committed roll.

## Independence and interfaces
Authoring tools can evolve using the existing module schema; full gameplay support for every rules family is not a prerequisite.

## Open questions
- Which hook language is acceptably safe?
- Which rule validations can be static?
- How should edition conversions be approved?
- What evidence, rights and validation are required before a reference may be compiled into a playable module?
