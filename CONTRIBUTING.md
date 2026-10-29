# Contributing to Grimoire V2

This branch is the V2 integration line. **Set GitHub PR base to `v2` explicitly**; GitHub's repository default remains `main` for the working V1 application. A PR into `main` is not a V2 contribution.

Start from current `v2`, read [Architecture](docs/ARCHITECTURE.md), and describe what user-visible behavior or falsifiable contract the PR adds. Include tests for ambiguity, wrong-source identity, privacy, cancellation/stale state and compatibility where relevant. Don't land abstract infrastructure with no proving scenario.

The first commit is a deliberately headless baseline. Porting V1 modules from `main` requires a dedicated PR preserving relevant behavior and tests while removing V1-only domain coupling.

Run:

```bash
python -m pip install -e "./backend[dev]"
python -m pytest backend/tests -q
```

Never add actual Grimoire home/library data, logs, recordings, provider credentials, or information about private campaigns to this public repository.
