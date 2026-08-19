# AGENTS.md

Instructions for coding agents working in this repository.

**This file routes; it does not restate.** Every convention lives in one place
already, and a second copy would drift silently until an agent followed the
stale one. So: read the file that owns a rule, not a summary of it here.

| You need | Read |
|---|---|
| the project's conventions, in full | [`CLAUDE.md`](CLAUDE.md) |
| how to run the gate, and what the guards want | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| what the store promises about atomicity and locks | [`docs/store-guarantees.md`](docs/store-guarantees.md) |
| what the app is and how a user runs it | [`README.md`](README.md) |
| prompt template layout | [`templates/README.md`](templates/README.md) |
| the Android packaging contract | [`docs/android-architecture.md`](docs/android-architecture.md) |

**[`CLAUDE.md`](CLAUDE.md) is the authority.** Where anything here appears to
disagree with it, it wins.

---

## Read first, before touching anything

1. [`CLAUDE.md`](CLAUDE.md), whole — not the section you think applies. Every
   part of it is a rule you can break in your first edit.
2. The **privacy** section of it in particular. This repo is public and the
   data store is not; the failure mode is a real world/campaign/character name
   reaching a doc, a commit message, a test fixture or a screenshot, and the
   fix last time was a git-history rewrite.
3. [`CONTRIBUTING.md`](CONTRIBUTING.md) for how to run `make check` and what
   each guard is asking of you.

---

## The short version of what will fail on you

Each of these is enforced by a test that reads the package's own ASTs, so it
fails at review time, not at runtime. `CONTRIBUTING.md` has the table of guards
and their `# <marker>: <reason>` escape hatches; `CLAUDE.md` explains why each
exists.

- A bare `Path.write_text` in `store/`.
- A campaign-scoped mutator that neither takes `locks.campaign_lock(cid)` nor
  gets classified in `store/locks.py`.
- A second campaign lock held outside `locks.hold_all`.
- A campaign read of an inheritable record that bypasses `store.overlay`.
- An import inside a function body, a new cycle in the module graph, or a
  `from ..pkg.leaf import func` inside `store/`.
- `model_dump()`, `Field`, a validator, or `ConfigDict` anywhere in a pydantic
  model.
- Filesystem access that assumes a repo checkout or a desktop `~`.

An exemption marker with **no stated reason fails deliberately**, and the
reason has to hold up rather than merely be present. Do not add one to get a
run green.

---

## Working agreements

- **Verify before claiming.** `make check` is the gate, and its output is the
  evidence. "Should pass" is not a result. In a worktree the default `PY` is
  wrong — see `CONTRIBUTING.md`.
- **Follow the review gates.** The spec → plan → implementation pipeline has
  mandatory Codex checkpoints (`CLAUDE.md`, "Development workflow"). Don't skip
  one because a change feels small; ask first.
- **Don't invent fixtures.** Names come from the existing placeholder set
  (Seraphine, Mara, Winifred, Realm, Saltmarch); LLM behaviour comes from
  `backend/tests/llm_fakes.py`, never a new inline fake.
- **Don't regenerate `backend/tests/fixtures/frozen_campaign/home/`.** Its
  entire value is that today's code did not write it. Only `snapshot.json` is
  regenerated, and only deliberately.
- **Match the surrounding code.** This codebase comments *why*, at length, and
  the comments carry the issue numbers that explain a shape. A change that
  strips that context is a regression even when the code still works.

---

## Skills

[`.claude/skills/`](.claude/skills/) holds task-specific procedures —
launching an isolated instance for end-to-end verification (`verify`),
authoring a mechanics module, ingesting a campaign log, populating world
content. If one covers what you are doing, use it; it knows the ports, the
mocks and the isolation rules that keep a verification run away from the
user's real library.
