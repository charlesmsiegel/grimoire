# V2 sprint proposals

**119 focused proposals**, scheduled for first useful delivery in the [ordered V2 roadmap](../README.md). Use that README rather than filenames or this folder's alphabetical order to choose implementation priority, hard dependencies and parallel wave ownership.

There are **17 V2-specific architecture/migration/play/synchronization proposals**, **90 carried-forward RP, engineering, content and moonshot proposals**, and **12 new UI delivery/exit proposals** from the older planning notebook. Carried-forward proposals are preserved for scope and ambition; **any reference in them to existing V1 stores, routes, prompts or frontend functionality means reusable behavior to reimplement/port, not a V2 capability already present**. The most immediate proposals have been explicitly annotated or rewritten for V2.

[Thematic catalog](../CATALOG.md) | [Architecture](../../ARCHITECTURE.md) | [Migration skill](../../../.agents/skills/migrate-v1-to-v2/SKILL.md)

Every implementation is on a feature branch based on `v2`, PR base `v2`, subject to the independent specification, plan, code and final conformance reviews in [AGENTS.md](../../../AGENTS.md). A sprint's moonshot need not be done to finish its first useful slice.

**World-less chat requirement:** [Sprint 107](world-optional-chat-scopes.md) is a **post-010, Wave 3** storage follow-up. Read the [chat/portrayal/navigation contract](../CHAT_MODEL.md). The first playable chat without a world is a Wave 5 obligation; direct card/scenario/lorebook import moves to Wave 4 (Sprint 037). Existing sprint numbers remain stable; follow **wave and row order**, not increasing numerical IDs.

**UI scheduling:** Sprints [108–114](../UI_COVERAGE.md) form **new Wave 7** and make the functionality delivered through Wave 6 usable end-to-end. Each later wave (8–12) ends with an **explicitly sequential** UI exit (115–119) after its own domain features land. Do not count UI exit tails as parallel domain sprints or mistake a headless first slice for finished UI.
