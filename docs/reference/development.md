# Development

[Documentation](../README.md) · [Architecture](architecture.md)

## Start with the owning instructions

- [CONTRIBUTING.md](../../CONTRIBUTING.md) owns installation for contributors,
  the gate commands and submission workflow.
- [AGENTS.md](../../AGENTS.md) owns project conventions and review requirements,
  and routes agents to the relevant development skill references.
- [CLAUDE.md](../../CLAUDE.md) forwards Claude agents to the same instructions.
- [templates/README.md](../../templates/README.md) owns prompt-template families.

Use those files for the commands rather than a second test recipe here. In a
worktree, pass the main checkout's Python interpreter explicitly as CONTRIBUTING
describes; the checks must import the worktree's sources.

## Find the change boundary

| Change | Start reading |
|---|---|
| Add or change a screen | [App.tsx](../../frontend/src/App.tsx), then its route/component |
| Navigation or keyboard controls | [shell/](../../frontend/src/shell/), [shortcuts/](../../frontend/src/shortcuts/) |
| API behavior | [routes/](../../backend/src/grimoire/routes/) and request models |
| Campaign persistence | [store/](../../backend/src/grimoire/store/) and [storage contracts](storage.md) |
| Prompt content | [context assembly](../../backend/src/grimoire/store/context/assemble/) and templates |
| Provider/model behavior | [inference resolution](../../backend/src/grimoire/store/inference/resolve/), [operations](../../backend/src/grimoire/inference/), adapter |
| Shared test behavior | [conftest.py](../../backend/tests/conftest.py), [LLM fakes](../../backend/tests/llm_fakes.py), [frontend testkit](../../frontend/src/testkit/) |
| Android packaging | [Android reference](android.md) |

Read the concrete flow before choosing a layer. A route-level check, a store
invariant and a disabled UI control protect different entry points.

## Verify without a private library

Follow the [verify skill](../../.agents/skills/verify/SKILL.md) for an isolated
app. Set a new scratch `GRIMOIRE_HOME` before application imports, use dedicated
ports and shared fake/cassette responses, and confirm an empty world listing
before seeding. Do not reuse a running personal instance for convenience.

For public images, the [screenshot procedure](../screenshots/README.md) adds
the exact fixture provenance and capture requirements. The frozen campaign's
`home/` fixture is historical compatibility evidence and is never regenerated.

## Tests own their inputs

Application tests must not parse README, docs, skill prose or historical plans
as configuration or scenario data. A useful scenario belongs in a fixture owned
by its test file or a shared test fixture. A prose assertion is not application
behavior coverage.

The [continuity acceptance matrix](../../backend/tests/test_capstone_acceptance.py)
and [complete-world fixture](../../backend/tests/test_create_world.py) show this
boundary. Installer tests compare executable scripts with package requirements
and proxy configuration, not with README wording. Generated OpenAPI comes from
routes. Runtime story Markdown and prompt templates remain genuine inputs;
they are different from repository documentation.

The [build-input fixture](../../backend/tests/test_compiled_build.py) creates
arbitrary README/report files under a temporary directory to prove they are
ignored. It checks the runtime boundary without reading the actual manual.

## Maintain the manual

When shipped behavior changes, update its owning guide/reference and follow the
links from the [index](../README.md). Verify commands against the scripts and
claims against source. Check local link targets, anchors and screenshots during
the documentation review, without making the product test suite depend on prose.

Keep temporary designs, plans, logs and review outputs in the ignored
`.superpowers/` workspace. They inform the implementation; the public manual
records the resulting behavior and constraints. Git history retains removed
reports. When review needs an originating brief, supply that context explicitly
rather than treating an old document as executable authority.
