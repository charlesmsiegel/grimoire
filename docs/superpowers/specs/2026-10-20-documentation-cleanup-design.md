# Documentation cleanup

## Approved intent

Replace the repository's scattered, historical documentation with a coherent
guide to the application that exists today. Keep the README's recognizable
structure, refresh its claims and screenshots, rebuild every existing file under
the root `docs/` directory, and delete `ROADMAP-CHECKLIST.md`. Historical material
remains available in Git history, rather than being moved into another published
archive. The user approved this design in conversation on 2026-10-20.

The user additionally directed that code, tests and tools must not depend on
documentation. Remove those dependencies instead of redirecting them to new
pages. Preserve tests of application behavior.

## Scope and boundaries

- Work on `docs/cleanup` in `.worktrees/docs/cleanup`.
- Rewrite the root README and root `docs/` tree. Remove historical specs, plans,
  validation records, benchmarks, generated HTML reports and obsolete images.
- Update repository entry points and skill links where this reorganization
  changes their targets. Keep `CLAUDE.md` authoritative for coding conventions
  and `CONTRIBUTING.md` authoritative for the contribution workflow.
- Do not build a documentation website or add a documentation framework.
  Markdown and relative links should work directly on GitHub and in a checkout.
- Do not alter storytelling, persistence, inference or UI behavior.
- Generated reports in other packages' `docs/` directories are outside the
  requested root-tree replacement; they must not become dependencies of the new
  documentation.
- This working spec and its implementation plan are temporary pipeline
  artifacts. Remove them from the final published documentation tree along with
  the historical pipeline material.

## Published structure

The root README remains the introduction, showcase and short path to running
Grimoire. Retain the useful existing sections: status, screenshots, prompt
inspection, capabilities, requirements, install, run, first steps, providers,
storage, Android, development, license and related projects. Move long reference
material, such as dice syntax and provider setup details, to linked pages.

The documentation index routes by the task a reader is trying to accomplish:

| Path | Purpose |
|---|---|
| `docs/README.md` | Entry point, reading paths and terminology |
| `docs/guides/getting-started.md` | Install, launch, update and first campaign |
| `docs/guides/worldbuilding.md` | Worlds, characters, PCs, records and greetings |
| `docs/guides/playing.md` | Scenes, turns, controls, wrap-up and continuity review |
| `docs/guides/models.md` | Providers, roles, routes, presets, capabilities and costs |
| `docs/guides/context.md` | Prompt inspection, memory, lore and historical retrieval |
| `docs/guides/data.md` | Storage location, backups, sync, exports and image maintenance |
| `docs/guides/troubleshooting.md` | Concrete symptoms, diagnosis and recovery |
| `docs/reference/architecture.md` | Components, entry points and critical flows |
| `docs/reference/storage.md` | File layout, overlays, atomicity, locking and limits |
| `docs/reference/mechanics.md` | Implemented modules, sheets, dice, calendars and extension seams |
| `docs/reference/android.md` | Build commands, packaging and runtime constraints |
| `docs/reference/development.md` | Navigation to contribution rules, verification and maintenance |
| `docs/screenshots/README.md` | Reproducible capture procedure and asset inventory |

Keep each fact in one owning page and link to it elsewhere. User guides explain
operations and visible outcomes. Technical references link claims to source
files and named symbols that were actually inspected. Distinguish available
features, defaults, opt-in features and implementation limits. Do not describe a
planned feature as available or reproduce a development backlog.

## Documentation dependency inventory

This inventory records executable reads, not historical mentions in comments.

| Reader | Current dependency | Resolution |
|---|---|---|
| `backend/tests/test_docs_guard.py` | Reads README, contribution and agent instructions, storage and screenshot docs, incoming-capture docs, and template README; enforces paths, words, inventories and duplication | Delete the documentation guard and its helper-only tests |
| `backend/tests/test_capstone_acceptance.py` | Parses acceptance tables and status wording from the continuity spec and phase-status document | Delete; behavioral continuity and recorded-eval tests already live separately |
| `backend/tests/test_skills_guard.py` | Reads skill Markdown and repository prose; imports helpers from the documentation guard | Delete the documentation/skill-content guard |
| `backend/tests/test_install_scripts.py` | Parses requirements, URLs, dev flags and platform command examples from README, CLAUDE, fixture/eval READMEs and a skill | Remove documentation assertions; retain installer, launcher, version-floor and proxy behavior checks |
| `backend/tests/test_continuity_wording.py` | Includes root README in a source wording scan | Remove documentation from the scan; retain the source-only copy and relation checks |
| `backend/tests/test_create_world.py` | Extracts a fenced JSON example from a skill and applies it | Delete the example-document test; retain the plan validation and CLI behavior tests |
| `scripts/roadmap_slices.py` | Parses Markdown roadmap specs and `ROADMAP-CHECKLIST.md` | Delete with the retired roadmap |
| `scripts/validate_partition.py` | Parses the historical store partition spec and implementation plan | Delete the one-off historical migration checker |

An AST scan of Python string literals and focused searches of frontend, Android,
scripts and build configuration found no application runtime reader of the
repository documentation. Comments that cite documents are references, not
executable dependencies. Application Markdown records and Jinja prompt templates
are runtime inputs and remain supported; they are not repository documentation.

Deliver the dependency readout in the final response, including what was removed
and any remaining executable dependency discovered during implementation.

## Screenshot requirements

- Follow `.claude/skills/verify/SKILL.md` and inspect its current interfaces.
- Set `GRIMOIRE_HOME` to a newly created scratch directory before importing the
  application. Do not read the real store or bootstrap pointer to seed data.
- Use dedicated free localhost ports, never the user's normal app ports.
- Confirm the isolated world's listing is empty before seeding.
- Seed synthetic text through the application API, using existing placeholders
  such as Realm, Saltmarch, Mara, Seraphine and Winifred. Never copy a real
  library, character card, image, provider setting or transcript.
- Replay existing test cassettes or shared LLM fakes. Patch generation, catalog
  and connection-check URLs as required; no real provider calls.
- Capture the current scene, world overview, character record and campaign list
  at a readable desktop size. Add a narrower view only if it contributes useful
  coverage of the current UI.
- Inspect every final image for synthetic content, clean layout, loaded assets,
  console errors and consistency with its caption. Replace every previous image.
- Record fixture provenance and reproduction commands in the screenshot guide.
  Stop temporary servers when finished.

## Validation and acceptance

1. Every original root `docs/` file is removed or rewritten, including images;
   no historical report or aspirational roadmap survives in the published tree.
2. `ROADMAP-CHECKLIST.md` and its retired tools are absent.
3. All new pages are reachable from the README or documentation index. Relative
   links, anchors and image paths resolve in a case-sensitive checkout.
4. Every substantive technical claim is checked against the source. Commands
   reflect the current scripts, Makefile, package requirements and routes.
5. The new README screenshots come only from the isolated synthetic fixture.
6. No test or executable tool reads repository documentation as an input. Audit
   remaining matches to separate source comments, runtime store files and prompt
   templates from repository documentation.
7. Relevant behavioral tests continue to pass. Run `make check` with an explicit
   interpreter path as required for a worktree. If removing linted files reduces
   recorded findings, regenerate and include the smaller baselines.
8. Verify the Markdown and images manually as documentation work, without adding
   a new product-test dependency on the documentation.
9. Follow the repository's spec, plan, diff-review and final acceptance gates;
   report unavailable tooling or obtain an explicit waiver before substituting
   another review process.

## Review status

The layout has user approval. This written specification awaits review. The
`codex` executable is not on this environment's PATH, and no dedicated Codex
review tool is exposed. No Codex CLI review gate is claimed as completed.
