# Grimoire V2 — clean-core integration branch

**This is an intentionally headless, pre-alpha domain prototype, not a playable release.** The working V1 application is on [main](https://github.com/charlesmsiegel/grimoire/tree/main); its source and history remain intact. All **new V2 pull requests must target `v2`**, not `main`. The default GitHub branch is still `main`: choose **base: v2** explicitly when opening a PR.

V2 is a redesign of the **fictional world and actor model**, not a wholesale repudiation of everything V1 implemented. The branch starts with a deliberately small native core instead of a V3-card-backed character store, separate PC/NPC entities, transcript-first prompts or materialized whole-actor campaign copies.

## What this initial commit retains

- The project's original license and Git history.
- A read-only V2 library-path resolver that refuses any V1 library location (`python -m grimoire.library`).
- A pure-Python typed kernel: source-qualified character definitions, independent campaign actors, control assignments, objective events, actor-specific observations/beliefs, and a read-only actor-perspective projection.
- An offline [story-quality gate](docs/evals/STORY_QUALITY.md) (`python -m grimoire.evals.story_quality`) that rejects recorded runs with knowledge leaks, negated player choices, PC authorship, merged NPC generation calls, voice or cost regressions against a **synthetic** V0.2.1-shaped baseline. It invokes no model.
- A [portrayal voice evidence contract](docs/VOICE.md) (`grimoire.voice`): per-portrayal voice anchors and examples, campaign pins/overrides, a knowledge-screened brief for **one** NPC speaker and a one-call-per-NPC rendering contract, proved with a deterministic fake renderer. No model is called.
- Small executable tests for source identity, independent campaign continuities, player/NPC role independence and private knowledge boundaries.
- A deterministic fault-injection test harness (`grimoire.testing`): crash, torn-write, I/O-error and external-edit injection at every file operation of a writer and its recovery, with named invariants. See [docs/TESTING.md](docs/TESTING.md) for the invariant catalog and the fault modes that are not simulated. It is test infrastructure, not a storage implementation.
- A V2-specific CI workflow for pushes and PRs targeting `v2`.
- [Architecture](docs/ARCHITECTURE.md), [porting plan](docs/PORTING.md), and [working agreements](AGENTS.md).

**Not yet present on the integration branch at the time of this roadmap update:** a complete persisted story/game runtime, chat storage, a playable scene runner, desktop/web UI, Android, interactive voice, approved content import/apply, or a server/API. The native generic record store is Sprint 010 work; its implementation status must be checked independently of this README. Those are intentionally absent, not silently claimed complete. Current types are first-slice experiments, **not a finalized file schema**.

## Try the kernel

Python 3.11 or newer:

```bash
python -m pip install -e "./backend[dev]"
python -m pytest backend/tests -q
```

No model keys, network service, user library or LLM are required for the tests. The user-owned Grimoire V1 library is never automatically migrated or modified.

## Development process

1. Read [Architecture](docs/ARCHITECTURE.md) and [Porting](docs/PORTING.md).
2. Branch from the latest `v2`. Open a PR with **base `v2`**.
3. Keep changes vertical and independently demonstrable; make the next behavior work in tests before adding architecture for hypothetical consumers.
4. Port proven, compatible V1 code deliberately from `main` or a recorded V1 commit. Do not merge the V1 application tree or introduce parallel canonical stores to avoid a migration decision.
5. Preserve human-editable Markdown/JSON authority, strict writes, no secret data in the repository, and loss-aware card interoperability when those features return.

## Prioritized V2 delivery roadmap

Start with the [**ordered V2 sprint roadmap**](docs/roadmap/README.md) and its [Wave 2 interface/exit contract](docs/roadmap/WAVE2_HANDOFFS.md): 107 proposals are arranged into 11 dependency-safe parallel waves with a critical migration/play path. The original [roadmapping notebook](https://github.com/charlesmsiegel/grimoire/tree/roadmapping/docs/roadmapping) remains an archived V1-era planning reference; the V2 wave order supersedes it.

- **V2 desktop library default:** `~/.grimoire-v2/` (override with an absolute `GRIMOIRE_V2_HOME`), never V1's `~/.grimoire/`, `GRIMOIRE_HOME` or `~/.grimoire.json`. The read-only resolver and V1-isolation guard are in `grimoire.library` (see [Architecture](docs/ARCHITECTURE.md#library-location-and-v1-isolation-implemented-grimoirelibrary)); run `python -m grimoire.library` to see which library V2 would use. Nothing writes a library yet.
- **Migration from `0.2.1`:** begin with [the migration skill](.agents/skills/migrate-v1-to-v2/SKILL.md), which currently specifies the preview/apply/verification workflow, including image objects, galleries, expressions and inheritance. The **runnable, read-only inventory scanner** is implemented (`python -m grimoire.migrate_v1 scan`; see [the inventory contract](docs/MIGRATION_INVENTORY.md)); reviewed import/apply is Wave 4 and is not implemented.
- **Playable testing:** native storage and a lightweight chat scope (Sprint 107), then **standalone character/scenario/lorebook cards (037)** and reviewed V1 worlds; Wave 5 must play **both a world-less chat and a migrated-world scene**, with phone play, one-click onboarding and synchronization in Wave 6. See [chat and navigation design](docs/roadmap/CHAT_MODEL.md).
- **Sync without Syncthing:** authenticated app-managed transfer and conflict-preserving revisions are early work. An off-LAN sync needs reachable infrastructure (remote endpoint or optional relay).
- **Token economics:** separate actor-specific model generation for every speaking NPC is the required default. Additional paid inference is optional, feature-flagged and metered.

V1 source snapshot for this cut: [`e4c4ec4`](https://github.com/charlesmsiegel/grimoire/commit/e4c4ec4abacd0beb87fc9f1cdd06e6787d5a88ce).
