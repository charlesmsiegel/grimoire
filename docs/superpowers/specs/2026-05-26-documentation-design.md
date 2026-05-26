# Documentation Design Spec

**Date:** 2026-05-26
**Status:** Approved

## Goal

Create comprehensive project documentation that serves three audiences: humans evaluating or using Grimoire (README), AI coding agents working in the codebase (CLAUDE.md / AGENTS.md), and human contributors (CONTRIBUTING.md).

## Deliverables

| File | Audience | Purpose |
|------|----------|---------|
| `README.md` | Humans (RPG players, potential users) | Feature showcase, quick start, project landing page |
| `CLAUDE.md` | Claude Code agents | Architecture, conventions, commands, pitfalls |
| `AGENTS.md` | Codex and other AI agents | Same content as CLAUDE.md (kept identical) |
| `CONTRIBUTING.md` | Human contributors | Setup, branching, testing, PR guidelines |
| `docs/images/` | README references | Directory with placeholder filenames for screenshots |

## README.md

Rich feature showcase aimed at RPG players who use LLMs (SillyTavern users, AI roleplay community).

### Structure

1. **Header** — Project name, one-line tagline ("A local-first RPG campaign companion that keeps your stories coherent"), badges (license, Python, TypeScript).

2. **Hero image** — Placeholder for main UI screenshot (`docs/images/hero-play-view.png`).

3. **What is Grimoire?** — 2-3 paragraphs explaining the problem (chat drift, lost continuity in LLM-assisted RPG play) and how Grimoire solves it (deterministic orchestrator, structured state, campaign model). Written for someone who's used SillyTavern and hit its limits.

4. **Features** — Visual feature grid with placeholder screenshots:
   - Campaign & world management
   - Multi-PC play with shared scenes
   - Structured continuity (facts, commitments, relationships)
   - Pluggable mechanics (WoD, Ars Magica, D&D — bring your own)
   - Context assembly (no more lost foreshadowing)
   - Image generation (integrated diffusers + A1111/ComfyUI/DALL-E)
   - Export (EPUB, Markdown, HTML)
   - Local-first, no telemetry

5. **Quick Start** — Prerequisites (Python 3.12+, uv, Node 20+, pnpm, Bash), install, run, shutdown commands.

6. **How It Works** — Simplified architecture: the three scopes (Library / Campaign-local / Code), what the Orchestrator does, how context is assembled deterministically.

7. **Extensibility** — Mechanics modules (game systems are pluggable, community-authored) and plugins (LLM providers, embedding providers, image backends, export formats).

8. **Screenshots Gallery** — Placeholder grid with specific filenames:
   - `docs/images/gallery-cast-view.png`
   - `docs/images/gallery-timeline.png`
   - `docs/images/gallery-ledger.png`
   - `docs/images/gallery-settings.png`

9. **Configuration** — Data directory (`~/.grimoire/`, `GRIMOIRE_DATA_ROOT`), ports (`GRIMOIRE_BACKEND_PORT` / `GRIMOIRE_FRONTEND_PORT`), other env vars.

10. **For Developers** — Link to CONTRIBUTING.md, brief test/lint commands.

11. **License** — TBD (MIT or Apache per spec).

### Placeholder Images

All in `docs/images/` with descriptive filenames so the user can drop in actual screenshots:

```
docs/images/hero-play-view.png
docs/images/feature-campaign-management.png
docs/images/feature-multi-pc.png
docs/images/feature-world-browser.png
docs/images/feature-mechanics-sheets.png
docs/images/feature-image-generation.png
docs/images/gallery-cast-view.png
docs/images/gallery-timeline.png
docs/images/gallery-ledger.png
docs/images/gallery-settings.png
```

Each image reference in the README uses a consistent pattern with alt text describing what the screenshot should show, so the user knows exactly what to capture.

### Tone

- Enthusiastic but not breathless — this solves a real problem
- Assumes familiarity with LLM roleplay (SillyTavern, Kobold, etc.) but doesn't require it
- Technical details are present but not leading — features and experience first
- Privacy and local-first philosophy stated plainly, not as marketing

## CLAUDE.md / AGENTS.md

Identical content. Comprehensive guide for AI coding agents working in the Grimoire codebase.

### Structure

1. **Project Overview** — What Grimoire is in 2-3 sentences. The three-scope model as the foundational concept.

2. **Tech Stack** — Python 3.12+ / FastAPI / uv (backend), TypeScript / React 18 / Vite / pnpm (frontend), SQLite + FTS5 + sqlite-vec, Markdown + YAML files.

3. **Repository Layout** — Directory tree of key paths with brief descriptions.

4. **Architecture Essentials** —
   - The three scopes: Library (user content, files), Campaign-local (play state, files + SQLite), Code (mechanics + plugins)
   - Module map (simplified ASCII diagram)
   - Canonical turn flow (numbered steps)
   - Storage model: files = SSOT, SQLite = cache + query engine
   - Communication: direct typed calls (Protocol interfaces) + async event bus

5. **Module Ownership** — Table of all modules with what they own and their role. Key rule: "writes go through the owning module."

6. **Development Commands** —
   - Install: `scripts/install.sh`
   - Run: `scripts/run.sh`
   - Backend tests: `cd backend && uv run pytest`
   - Backend lint: `cd backend && uv run ruff check && uv run ruff format --check`
   - Frontend tests: `cd frontend && pnpm test`
   - Frontend lint: `cd frontend && pnpm lint && pnpm typecheck`
   - pytest markers: conformance, integration, frozen_campaign, perf, scenario, golden

7. **Code Conventions** —
   - Backend: Pydantic models, Protocol interfaces for boundaries, ruff (line-length 100, Python 3.12 target)
   - Frontend: Zod validation, Radix UI, React Router 7, strict TypeScript, Prettier
   - Content files: YAML frontmatter + Markdown body
   - Naming patterns per entity type

8. **Branching and Workflow** —
   - Always use feature branches for anything more complex than a bugfix
   - Use rebase-merge (not merge --no-ff) when integrating to main
   - Put worktrees under `.worktrees/` at repo root

9. **Data Directory** — `~/.grimoire/` layout, `GRIMOIRE_DATA_ROOT`, what lives where.

10. **Common Pitfalls** —
    - Don't write directly to SQLite for things with file SSOT
    - Don't bypass the read cascade (campaign-local → library)
    - Don't put mechanics logic in core — modules are external
    - Don't add telemetry or phone-home
    - Don't import across module boundaries that shouldn't know about each other
    - Don't forget to update both file and index when changing content

11. **Keep Documentation Up to Date** — When you change behavior, APIs, module boundaries, commands, or conventions, update the relevant docs (README, CLAUDE.md, AGENTS.md, CONTRIBUTING.md) in the same PR. If you add a new module, update the module ownership table. If you change a command, update the dev commands section. If you add a feature, consider whether the README needs updating.

12. **Testing Strategy** — What to test, where tests live, marker meanings, the conformance/integration/golden distinction.

## CONTRIBUTING.md

Brief contributor guide for humans.

### Structure

1. **Getting Started** — Prerequisites, clone, `scripts/install.sh`, `scripts/run.sh`
2. **Branch Strategy** — Feature branches for anything beyond a simple bugfix. Rebase-merge to main.
3. **Testing** — Run before submitting: backend tests + lint, frontend tests + typecheck + lint. Exact commands listed.
4. **PR Guidelines** — Keep PRs focused, include test coverage, update docs if behavior changes.
5. **Code Style** — Enforced by ruff (backend) and eslint/prettier (frontend). Just run the formatters.

## Implementation Notes

- All files go in the worktree at `.worktrees/feat/documentation`
- README.md replaces the existing minimal README
- CLAUDE.md and AGENTS.md are new files at repo root
- CONTRIBUTING.md is a new file at repo root
- `docs/images/` directory created with a `.gitkeep` or placeholder README
- Content is derived from the existing specs (especially `specs/00-overview.md`) and project config files, but written to be self-contained — no required cross-references
- AGENTS.md content is identical to CLAUDE.md
