# Product and engineering principles

These are constraints on **future work**, not separate implementation projects.

- **Fiction first.** Grimoire supports lightweight, mechanics-free roleplay just as seriously as rules-driven tabletop play. A setting can recommend rules, but a campaign explicitly chooses one mechanics module or none. Any deliberate rules hybrid is itself a module.
- **Authoritative, readable files.** World, actor, campaign and play state are owned by Markdown/frontmatter and the existing human-editable JSON. Files must remain usable by external scripts, hand editing, Claude and Codex. SQLite, embeddings and compiled views are disposable projections, not secret canonical databases.
- **One owner for state.** Only deterministic, guarded operations commit identity, legal actions, random outcomes, inventory, time and continuity. LLMs can interpret, propose, select under a policy and narrate; they cannot silently overwrite an outcome or invent items.
- **Model effort proportional to judgment.** First use calculation, then lightweight decisions, then expensive narrative generation or bounded agentic workers when valuable. Account for all inference, retrieval, retries and tool costs per turn/job.
- **Identity before appearance.** Fictional species or reusable definitions are distinct from particular individuals, holdings and campaign state. Names are not stable cross-source identity; versions, portraits, aliases, overrides and provenance must survive imports, forks, edits and removal.
- **Preserve real ruleset differences.** D&D checks and combat, World of Darkness contested/political actions, Ars Magica seasons/laboratories and PbtA fiction-triggered moves are incompatible in important ways. Do not force them into one d20-shaped schema.
- **Independent, reviewable features.** Build small gameplay/authoring slices to reveal the right interfaces. Avoid abstraction projects justified only by hypothetical consumers. Engineering hygiene and reliability are worthwhile in themselves.
- **Compatibility and recovery.** Preserve local-first worlds and campaigns, Pydantic 1/Android support, actor/version and image semantics, existing locks, conflict behavior, prompt privacy, exports and user-editable files. Read-time degradation may be acceptable; writes must not silently report false success.
- **Usable without future infrastructure.** Basic inventory quantities and simple creature individualization should not be blocked by a speculative package manager, universal bestiary ontology or full mechanics engine.
- **Voice is an input modality, not a new game authority.** Spoken UI commands, director guidance, PC dialogue and fictional actions must have distinguishable routes, explicit permission and safe uncertainty handling. Raw audio and interim transcripts are not silent canonical game events; accepted text and validated effects use normal authority. Text and manual controls always remain usable.
- **Ambitious ideas are welcome.** Moonshots are judged by potential UX and roleplay quality, not constrained by estimates of human engineering labor. Each idea still needs coherent boundaries, measurable hypotheses, fallback and privacy/authority invariants. Early slices must not require an entire moonshot platform.
- **Assess storytelling as storytelling.** Test long-horizon consistency, distinct characterization, freedom of player choice, emotional payoff and credible NPC knowledge independently of ordinary code quality or superficial fluency.
- **Optional automation.** In-app maintenance and agentic workers are new ways to do work, never mandatory replacements for external coding agents or manual editing.
- **No invisible status games.** A delivered foundation is not another item to implement. A closed issue may still represent an unbuilt feature; an automated candidate is not a proven defect; an ungraded check is not a pass.

## How sprint proposals become specifications

1. Select a desired player, author or developer outcome from the [roadmap](README.md).
2. Read the relevant sprint proposal, especially its **Open questions**. Resolve critical ones before committing to architecture; questions that can be decided experimentally may become explicit spike deliverables.
3. Check current main: reuse shipped infrastructure and avoid duplicating prior work.
4. Write a scope, theory note, compatibility/migration plan, representative success/failure cases and acceptance tests on a **new implementation branch based on main**.
5. Run normal spec, plan, review, CI and final gates; after landing, remove completed work from future-focused browsing while retaining any unfinished goals.

**The roadmapping branch is a planning notebook, never merged or PR'd into main.** The planned strict B+ code-quality gate does not replace existing tests or review.
