# Grimoire V2 — clean-core integration branch

**This is an intentionally headless, pre-alpha domain prototype, not a playable release.** The working V1 application is on [main](https://github.com/charlesmsiegel/grimoire/tree/main); its source and history remain intact. All **new V2 pull requests must target `v2`**, not `main`. The default GitHub branch is still `main`: choose **base: v2** explicitly when opening a PR.

V2 is a redesign of the **fictional world and actor model**, not a wholesale repudiation of everything V1 implemented. The branch starts with a deliberately small native core instead of a V3-card-backed character store, separate PC/NPC entities, transcript-first prompts or materialized whole-actor campaign copies.

## What this initial commit retains

- The project's original license and Git history.
- A pure-Python typed kernel: source-qualified character definitions, independent campaign actors, control assignments, objective events, actor-specific observations/beliefs, and a read-only actor-perspective projection.
- Small executable tests for source identity, independent campaign continuities, player/NPC role independence and private knowledge boundaries.
- A V2-specific CI workflow for pushes and PRs targeting `v2`.
- [Architecture](docs/ARCHITECTURE.md), [porting plan](docs/PORTING.md), and [working agreements](AGENTS.md).

**Not yet present:** persistence, game-state transactions, actual model inference, a playable scene runner, desktop/web UI, Android, voice, content import/export, or server/API. Those are intentionally absent, not silently claimed complete. Current types are first-slice experiments, **not a finalized file schema**.

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

The broader [roadmapping notebook](https://github.com/charlesmsiegel/grimoire/tree/roadmapping/docs/roadmapping) remains planning-only. Its original wave schedule assumed an evolved V1 baseline; V2 integration must validate new dependencies rather than blindly treating every old wave as ready.

V1 source snapshot for this cut: [`e4c4ec4`](https://github.com/charlesmsiegel/grimoire/commit/e4c4ec4abacd0beb87fc9f1cdd06e6787d5a88ce).
