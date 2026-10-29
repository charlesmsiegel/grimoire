# Instructions for coding agents working on V2

**You are on the clean-core `v2` integration branch. Target `v2` in new pull requests, not `main`.** V1 is a separate working product on `main`. The first V2 commit intentionally removes the V1 application from this branch. Do not reimport V1 en masse.

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/PORTING.md](docs/PORTING.md) before building or porting a subsystem.

- Make one independently useful vertical slice at a time, with executable behavior/failure tests.
- Source-qualified definitions, campaign actors, control assignments, objective events and private observation/belief evidence are separate concepts. **PC and NPC are authority roles**, not distinct fictional species or permanent record kinds.
- The `backend/src/grimoire/core` prototype is pure and must not import LLM providers, filesystems or UI modules. Add persistent adapters outside it after a storage contract is reviewed.
- Do not freeze an invented schema because a dataclass appears in this first experiment. Historical migration, selective rebase and failure guarantees require explicit specs.
- User-owned Markdown/JSON is canonical. Embedded DBs, indexes, embeddings and prompt views are rebuildable derivatives.
- Narration never silently commits world/campaign state. A model may propose intentions or outcomes only under the applicable authority rules.
- Preserve character-specific epistemic boundaries and player control. Director-only events are not a character's memories.
- No user worlds, campaigns, transcripts, provider secrets, names or aggregate facts about private libraries may enter the public repository or CI artifacts. Use synthetic fixtures.
- Before implementing a feature, specify an acceptance scenario and failures, run tests, and review the independent domain theory. Reuse proven V1 utilities **only after** checking their dependencies and contracts.
- Python baseline: 3.11+. Test with `python -m pytest backend/tests -q`. The first branch is not a playable app, and its green CI must not be described as an app-integrated test.
