# grimoire — agent instructions and project conventions

Instructions for coding agents working in this repository. Read this whole
file before making changes, especially the privacy section below.
For code changes, read [`CONTRIBUTING.md`](CONTRIBUTING.md) for the gate and guards,
then the relevant development skill branch linked below.

**This file is the authority for project conventions.** `CLAUDE.md` points
here so every coding agent follows the same instructions.

FastAPI backend (`backend/`, pytest) + Vite/React frontend (`frontend/`, vitest).
The app and its data live in a markdown/JSON store rooted at `~/.grimoire` by
default. The root is resolved by `store.home()`: `GRIMOIRE_HOME` env var (tests /
overrides) → the user-chosen path recorded in the bootstrap pointer
`~/.grimoire.json` → `~/.grimoire`. The path is editable from the Settings
page (Storage location); point it at a synced folder to share a library across
devices.

Companion documents own the detailed procedures this file points to:

| You need | Read |
|---|---|
| how to run the gate, and what the guards want | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| what the store promises about atomicity and locks | [`docs/reference/storage.md`](docs/reference/storage.md) |
| what the app is and how a user runs it | [`README.md`](README.md) |
| prompt template layout | [`templates/README.md`](templates/README.md) |
| the Android packaging contract | [`docs/reference/android.md`](docs/reference/android.md) |
| current user guides and technical references | [`docs/README.md`](docs/README.md) |

Documentation is maintained by review. Tests and tools must not parse
repository prose as configuration or scenario data. Useful test scenarios
belong in fixtures owned by the tests.

## Working agreements

- **Keep worktrees under the main checkout's `.worktrees/` directory**:
  `~/github/grimoire/.worktrees/<branch>`. Use `git worktree add` when a
  native worktree tool cannot honor this location.
- **Verify before claiming.** `make check` is the gate, and its output is the
  evidence. "Should pass" is not a result. In a worktree the default `PY` is
  wrong — see `CONTRIBUTING.md`.
- **Match the surrounding code.** This codebase comments *why*, at length, and
  the comments carry the issue numbers that explain a shape. A change that
  strips that context is a regression even when the code still works.
- **Keep source files below 1,000 lines.** When a file reaches 1,000 lines,
  split it into focused modules with clear responsibilities. In Python, convert
  the large module into a package with smaller modules and a narrow public
  interface. Preserve behavior, import paths, comments and fault-injection seams.
- **Put all module imports at the top of the file, never inside a function.**
  Resolve dependency cycles by changing module boundaries; do not hide a cycle
  with a local import. Keep the backend module graph acyclic.
- **No code may depend on `docs/`.** Runtime code, tests, scripts and builds
  must not import, read or otherwise require files from any `docs/` directory.
  Documentation stays downstream of code.
- **Use the platform's resolvers and portable dependencies.** Backend paths use
  `store.paths`, `prompts.templates_dir()` and `main.dist_dir()`. Keep base
  dependencies Android-installable and pydantic models compatible with v1/v2.
- **Reuse test fixtures.** LLM behavior comes from `backend/tests/llm_fakes.py`.
  Never regenerate `backend/tests/fixtures/frozen_campaign/home/` or the lore
  activation golden to get a test green. Read the testing branch for deliberate
  snapshot updates.

## Privacy: real data — and references to it — never get committed

This repo is public. `~/.grimoire` (wherever `store.home()` points) holds
real, private worldbuilding/campaign/character content, kept out of the repo
structurally. That boundary holds in both directions:

- **Never commit anything under the data store itself.** Worlds, campaigns,
  and calendars all live outside the repo by design — that's the point of
  `store.home()`.
- **Never use a real world/campaign/character name as a "concrete example"**
  in a design doc, commit message, or test fixture, even in passing —
  invented names only. This has happened before (real slugs/character names
  leaked into `docs/superpowers/` and got adopted as recurring test
  fixtures) and required a full git-history rewrite to fix. Reuse the
  codebase's existing placeholder names (e.g. Seraphine, Mara, Winifred,
  Realm, Saltmarch) rather than inventing a new one that might coincidentally
  match something real.
- **Never describe the store's *contents*, not even in aggregate.** The
  rule above is about names; this one is about data, and it is the easier of
  the two to talk yourself past. A design doc may not report how many
  characters or cards a library holds, what proportion of them fill in a
  given field, a size distribution (median/p90/max), how many campaigns set
  some setting, what a real `config.md` contains, or whether a given file
  exists in the store — in a spec, a commit message, a code comment, a test
  fixture, or an appendix explaining how to measure any of it. Anonymised
  counts still describe somebody's private library, and once committed they
  are in history. **Qualitative is the acceptable form**: "many cards have no
  `mes_example`" is fine; "N of M cards" is not. Argue from what the code,
  the templates and the shipped defaults do — those are repo facts. Where a
  constant seems to need a measurement to justify it, justify it
  structurally and say it should be tuned against real prompts later. If
  measuring the live store genuinely helps a decision, do it in conversation
  and keep the result out of every committed file.
- **Personal/homebrew calendars follow the same split as any other private
  content**: only `gregorian` and `hebrew` (real-world, real holidays) ship
  in `store/calendars/`; anything else — a fictional calendar, a custom
  holiday set — is a plugin loaded from `<GRIMOIRE_HOME>/calendars/` (see
  `store/calendars/plugins.py`), never committed.

## Development workflow: Codex review gates

**Keep Git history linear.** Integrate feature branches by rebasing onto the target
branch when needed, then fast-forwarding it (`git merge --ff-only`). Never create
merge commits. If the target is already an ancestor, fast-forward directly.

The spec → plan → implementation pipeline (`superpowers:brainstorming` writes
specs to `.superpowers/specs/`; `superpowers:writing-plans` writes plans to
`.superpowers/plans/`) has mandatory Codex checkpoints — do not advance to
the next stage until the gate passes and its findings are resolved:

- **Spec → planning**: run `/codex:adversarial-review` against the spec before
  starting `superpowers:writing-plans`.
- **Plan → implementation**: run `/codex:adversarial-review` against the plan
  before starting implementation.
- **Implementation → done**: run `/codex:review` against the diff before
  considering the work complete (merge, PR, `finishing-a-development-branch`).
- **Done → actually done**: once implementation is otherwise complete, run
  `/codex:adversarial-review` a final time against the diff *and* the
  originating spec, asking specifically whether the changes implement the
  spec (not just whether the diff is clean code) — gaps, drift, and
  quietly-dropped requirements are the target, not style.

Use those commands where installed. In a host without them, use an independent
Codex reviewer through the host's review tool or a Codex subagent, keeping the
same artifact scope and checkpoint order. Report a gate you cannot run; an
unavailable command does not make self-review equivalent to independent review.

If a gate surfaces findings, address them (or explicitly note why not) before
moving on. Don't skip a gate because a change feels small — ask the user
first if you think one should be skipped.

Keep those temporary work records in the ignored `.superpowers/` workspace.
Publish current behavior and constraints in the manual rather than accumulating
designs, plans and review transcripts under `docs/`.

**A Codex review on the pull request can cover the published diff.** Commenting
`@codex review` asks Codex's GitHub reviewer (`chatgpt-codex-connector`) to review
the current head. Supply the originating requirements and review context
explicitly: ignored local specs and plans are not visible on the PR. A review
does not cover context it was never given. Resolve findings and request another
review after a push that changes code it has not seen.

## Task-specific instructions

Use the matching skill before changing its area. Start with its `SKILL.md`, then
follow only the references relevant to the task. Specialized rules live there
once; this file keeps the universal agreements.

| Task | Skill |
|---|---|
| Application code, tests, prompts, inference, frontend or Android | [grimoire-development](.agents/skills/grimoire-development/SKILL.md) |
| Isolated browser verification | [verify](.agents/skills/verify/SKILL.md) |
| Mechanics data packs | [create-mechanics-module](.agents/skills/create-mechanics-module/SKILL.md) |
| A World from a concept | [create-world](.agents/skills/create-world/SKILL.md) |
| Raw campaign transcripts | [ingest-campaign-log](.agents/skills/ingest-campaign-log/SKILL.md) |
| Bulk extraction from character cards | [populate-world-content](.agents/skills/populate-world-content/SKILL.md) |
| Editorial integration of imported cards | [world-card-integration](.agents/skills/world-card-integration/SKILL.md) |
| Private library writes outside the app | [grimoire-store-editing](.agents/skills/grimoire-store-editing/SKILL.md) |

## Maintaining agent instructions

[`.agents/skills/`](.agents/skills/) owns each procedure, reference and helper.
[`.claude/skills/`](.claude/skills/) contains generated discovery pointers. Edit
only the authoritative packages, then run `python scripts/sync_skills.py --write`
and commit the pointers alongside them. `python scripts/sync_skills.py --check`
detects drift. Supporting paths resolve from the authoritative package.

Keep skill descriptions specific, entry points short, and conditional details
in linked Markdown references. Explain when to read each branch. When moving a
page, update its incoming links and review its claims against source. Use
`sync_skills.py --check` to verify generated pointers. Read code before
refreshing a claim; documentation review owns links and reference reachability.
