# Grimoire documentation

These pages describe the application in this checkout. Start with a task below.
For an introduction and screenshots, see the [project README](../README.md).

## Use Grimoire

| I want to… | Read |
|---|---|
| Install, update or play my first scene | [Getting started](guides/getting-started.md) |
| Build a setting or import characters | [Worldbuilding](guides/worldbuilding.md) |
| Play, reroll, finish a scene or review continuity | [Playing a campaign](guides/playing.md) |
| Connect a provider, choose models or understand costs | [Models and providers](guides/models.md) |
| Understand a prompt or improve campaign memory | [Context and memory](guides/context.md) |
| Move, sync, export or back up a library | [Data and backups](guides/data.md) |
| Diagnose an error or recover a stalled workflow | [Troubleshooting](guides/troubleshooting.md) |

## Develop or extend Grimoire

| I need… | Read |
|---|---|
| Components and request flows | [Architecture](reference/architecture.md) |
| File layout, lock boundaries and cache semantics | [Storage contracts](reference/storage.md) |
| Dice, mechanics packs, sheets or calendars | [Mechanics and extensions](reference/mechanics.md) |
| Android build and packaging details | [Android](reference/android.md) |
| Source navigation and verification | [Development](reference/development.md) |
| The source and capture process for public images | [Screenshot fixtures](screenshots/README.md) |

[CONTRIBUTING.md](../CONTRIBUTING.md) owns contribution commands.
[AGENTS.md](../AGENTS.md) owns universal coding conventions and routes to
the [development skill](../.agents/skills/grimoire-development/SKILL.md).
[templates/README.md](../templates/README.md) owns the prompt-template catalog.
These references link to those owners instead of maintaining competing copies.

## Terms used throughout

| Term | Meaning |
|---|---|
| World | A reusable setting and its records |
| Campaign | One playthrough, with its own scenes and evolving state |
| PC | A player character or persona |
| Character | A reusable NPC card, with one or more versions |
| Greeting | A reusable scene opening with cast and eligibility information |
| Chronicle | Reviewed scene summaries and the campaign's accumulated recap |
| Wrap-up / absorb | Preparing and reviewing updates from a completed scene |
| Provider | An account or endpoint that serves models |
| Role | A model assignment for Primary, Fast, Decision or Embedding work |
| Route | A type of model work that inherits a role or uses an override |

## Keeping these pages useful

Each topic has one owning page. Update it when the shipped workflow changes,
check its commands against the source, and follow its links from the index.
Plans, review transcripts and generated analysis reports are working artifacts;
Git history preserves retired material. They are not part of this manual.
Public examples and screenshots must use synthetic fixtures, never a real
library. Tests own their inputs and do not parse these pages.
