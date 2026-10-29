# Instructions for coding agents working on V2

**You are on the clean-core `v2` integration branch. Target `v2` in new pull requests, not `main`.** V1 is a separate working product on `main`. The first V2 commit intentionally removes the V1 application from this branch. Do not reimport V1 en masse.

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [the ordered V2 roadmap](docs/roadmap/README.md), and [docs/PORTING.md](docs/PORTING.md) before building or porting a subsystem.

- Make one independently useful vertical slice at a time, with executable behavior/failure tests.
- Source-qualified definitions, campaign actors, control assignments, objective events and private observation/belief evidence are separate concepts. **PC and NPC are authority roles**, not distinct fictional species or permanent record kinds.
- The `backend/src/grimoire/core` prototype is pure and must not import LLM providers, filesystems or UI modules. Add persistent adapters outside it after a storage contract is reviewed.
- Do not freeze an invented schema because a dataclass appears in this first experiment. Historical migration, selective rebase and failure guarantees require explicit specs.
- User-owned Markdown/JSON is canonical. Embedded DBs, indexes, embeddings and prompt views are rebuildable derivatives.
- V2 defaults to `~/.grimoire-v2/` (explicit `GRIMOIRE_V2_HOME` override; Android private storage). Never point V2 implicitly at V1's `~/.grimoire/` or reuse `GRIMOIRE_HOME`; source/destination migration paths must be distinct.
- V2 entities have typed composable capabilities and source-qualified instances. Canonical typed graph relations are separate from derived graph indices, with scope/time/evidence-aware traversal; graph connectivity never automatically grants actor knowledge.
- Every speaking NPC gets its own generation call by default, preserving V0.2.1's character voice. Optional extra model calls must be feature-flagged, costed and user-budgeted; ordinary graph traversal and observation lookup must not invoke an LLM.
- App-managed desktop/phone synchronization is an early release gate. Build revisions/conflict safety first and authenticated transport before mobile integration; Syncthing is not required, and conflicting offline edits must not be silently overwritten.
- For V0.2.1 asset translation, read [migration skill](.agents/skills/migrate-v1-to-v2/SKILL.md) and its image/verification references. The skill currently specifies a process and is not an executable converter.
- Narration never silently commits world/campaign state. A model may propose intentions or outcomes only under the applicable authority rules.
- Preserve character-specific epistemic boundaries and player control. Director-only events are not a character's memories.
- No user worlds, campaigns, transcripts, provider secrets, names or aggregate facts about private libraries may enter the public repository or CI artifacts. Use synthetic fixtures.
- Before implementing a feature, specify an acceptance scenario and failures, run tests, and review the independent domain theory. Reuse proven V1 utilities **only after** checking their dependencies and contracts.
- Python baseline: 3.11+. Test with `python -m pytest backend/tests -q`. The first branch is not a playable app, and its green CI must not be described as an app-integrated test.


## Mandatory V2 development and pull-request workflow

**Every V2 feature PR must target `v2`, not `main`.** GitHub's repository default branch remains `main` for the functioning V1 application. Explicitly choose **base: v2** when opening a pull request. Do not merge V1 `main` into `v2` to solve a porting problem, or merge `v2` into `main`; bring over narrowly scoped V1 code and tests only after review.

Keep the V2 integration history linear: rebase or squash feature branches into `v2`, rather than creating merge commits. Never force-push shared `v2` or bypass branch protection/review. A passing automated check is not permission to self-merge without required review.

For every feature or behavioral change, follow **spec → plan → implementation → review → PR**. The independent Codex checkpoints below are mandatory; do not advance until issues are resolved or explicitly adjudicated:

1. **Branch and specify.** Start a topic branch from the latest `v2`. State the user-visible outcome, the slice being built, source/authority boundaries, failure cases, migration/rollback implications, and executable acceptance scenarios in a written specification. Use `superpowers:brainstorming` if installed.
2. **Spec → plan gate.** Run `/codex:adversarial-review` on the **specification** before writing an implementation plan. Resolve material findings before proceeding.
3. **Write the plan.** Break the approved specification into small independently testable steps, naming touched interfaces, validation and test commands. Use `superpowers:writing-plans` if installed.
4. **Plan → implementation gate.** Run `/codex:adversarial-review` on the **implementation plan** before changing implementation code. Address material concerns and re-review substantive revisions.
5. **Implement and test.** Land the smallest complete vertical slice; include behavioral, negative, privacy and migration tests when relevant. Run `python -m pytest backend/tests -q` and applicable additional checks. Report anything not run; a green kernel test suite does not imply an application exists.
6. **Implementation → done gate.** Run `/codex:review` against the actual diff, fix findings, and re-review changed areas before claiming implementation complete.
7. **Final spec-conformance gate.** Run `/codex:adversarial-review` once more **against the final diff AND the original specification**. Ask explicitly about requirements lost, altered, bypassed or silently omitted—not only code quality. Resolve discrepancies or document an explicit decision with the requester.
8. **Open the PR.** Push the feature branch and open a pull request with **base `v2`**. Include the specification's intended behavior, implementation summary, test results, migration/privacy implications, outstanding limitations, and how each review gate was satisfied. CI and required PR reviews must complete before integration. If code changes after a review, ensure the reviewer sees and evaluates the updated diff.
9. **Integrate.** After required review/CI, use the repository's allowed linear-history merge method (rebase, fast-forward or squash). Update from current `v2` if required to resolve conflicts. Do not use a merge commit or change the PR base to `main` for convenience.

**Independent reviewers and unavailable tools.** Prefer the named Codex commands when installed. Otherwise use an independent Codex reviewer through the host's review mechanism or a Codex subagent, preserving the checkpoint order and including the same artifacts. A self-review is **not** an independent review. If an independent gate cannot run, say so and ask for an explicit decision; do not report it as passed or quietly skip it. If an unusually trivial change merits skipping a gate, ask the user first.

**Review artifacts and privacy.** Keep working specs, plans and review records under the git-ignored `.superpowers/specs/` and `.superpowers/plans/` workspace (or equivalent ignored scratch space). These are temporary work artifacts, not production docs. Document accepted current contracts in `docs/`, not an accumulating history of brainstorms and reviews. Do not include private campaign content in either location.

**PR-based Codex review.** `@codex review` on a PR can provide a review of the published diff; explicitly supply originating requirements and scope because locally ignored specifications are *not* visible to GitHub's reviewer. A later code push requires another review of the changed diff. A PR review of code cannot stand in for the earlier spec and plan gates it never saw.
