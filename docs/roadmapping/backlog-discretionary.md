# Deferred and discretionary backlog — pre-cleanup consolidation

**Status: user-approved tracker consolidation; feature dispositions are not implementation claims.**
**Date: 2026-10-15.**
**Scope:** Seven formerly open issues, none referenced by any of the ten PRs in review at the time of triage.

The purpose is to leave the GitHub issue queue containing only the work intended to **land before the technical-debt sprint**, plus issues that are already being addressed by in-review PRs. Closing these seven trackers does **not** mean their features shipped. Some are long-term candidates; two (#151 and #190) may ultimately be *unnecessary*. These are suggestions, not commitments to build every item.

The full original issue bodies, including all old options, dependencies and test notes, are preserved in [archived-issue-text.md](archived-issue-text.md). Original "current state" and implementation notes may predate the current backend; any later spec must recheck `main`.

## 1. Multi-PC turn loop — #81

**Original:** [#81 — Multi-PC turn loop](https://github.com/charlesmsiegel/grimoire/issues/81)

**Disposition:** **Defer** to a **Multi-PC / Multiplayer** phase; not required for ordinary one-player roleplay or the next backend cleanup.

**Retained requirements:**
- A scene may include multiple player characters and distinguish their authored posts unambiguously.
- The UI should allow a player/controller to prepare separate posts from two or more PCs and deliberately **advance once**, with the model receiving a coherent combined turn.
- Support a straightforward local pending-post buffer first if valuable. True asynchronous multi-user submissions, durable pending queues, cross-device ownership and synchronized advancement are **separate, larger requirements**, not automatic part of the first slice.
- Preserve correct per-PC speaker attribution, the existing transcript marker grammar, deterministic turn ordering and no premature model invocation before a deliberate advance.
- Test multi-PC posts round-trip through canonical scene history and survive retries/cancel appropriately.

**Revisit when:** Party management, multi-PC authoring or multiplayer takes priority. A simple single-user multi-PC UI may be viable earlier, but need not block other work.

## 2. Cross-campaign scene copy — #93

**Original:** [#93 — Copy a scene in from another campaign](https://github.com/charlesmsiegel/grimoire/issues/93)

**Disposition:** **Defer** until campaign storage and identity representations are clearer; do not lock in an ad hoc scene-transfer format before backend redesign. The detailed wider requirements also remain in [deferred-systems.md](deferred-systems.md).

**Retained requirements:**
- Copy/import a scene into a target campaign using a **new target scene identity** with correct ordering and transcript content.
- Reconcile source/target cast and version locks; never match identity only by display name, silently swap cards, or create dangling asset links.
- Decide whether cast, continuity records, mechanics results, scene-specific data and provenance transfer or instead require explicit re-extraction/review against the target campaign.
- Preserve source campaign data; preflight conflicts, abort cleanly and recover partial destination writes.
- Reuse established scene import, campaign branching and future World View/Change Sets rather than inventing an unrelated copy framework.

**Revisit when:** campaign-state storage/migration contracts stabilize, or a strongly desired same-world transcript-only import justifies a narrow compatible slice.

## 3. Live context preview while typing — #132

**Original:** [#132 — Live prompt preview while typing](https://github.com/charlesmsiegel/grimoire/issues/132)

**Disposition:** **Low-priority optional UI enhancement**; defer rather than block the backend cleanup.

**Retained requirements if pursued:**
- Reflect draft input in prompt inspection **without sending, saving or altering authoritative scene state**.
- Debounce compute/API traffic while typing and avoid stale, reordered responses.
- Show how draft text affects triggered lore/characters/context selection, token budgets and any shed sections in the actual effective prompt, as opposed to a misleading separate heuristic.
- Preserve context privacy and model-call-free preview behavior; avoid significant per-keystroke disk churn, embedding spend or concurrency bugs.
- Use existing inspector, captured prompt and World View infrastructure if it can answer cheaply.

**Revisit when:** UI/Context Inspector redesign or repeated player demand demonstrates value. The implementation is optional, not promised solely because the historic issue existed.

## 4. Auxiliary generation task kinds — #188

**Original:** [#188 — Auxiliary task kinds](https://github.com/charlesmsiegel/grimoire/issues/188)

**Disposition:** **Partially served by existing framework; defer residual scope** to an **Auxiliary Generation**/UI sprint. Do not recreate #187's already-landed generic non-canonical task infrastructure.

**Retained candidate kinds:**
- Write as/impersonate a selected PC (with explicit authorship/consent and no surprise scene mutation).
- Continue as a selected character.
- Ask hypothetically "what would this character say?" without making the hypothetical canonical.
- Polish/rewrite existing text; brainstorm; translate.
- Text-only transformations should not require full scene/world retrieval when unnecessary, nor should they all share a generic, bloated prompt.

**First step if revisited:** audit **current code** to identify which kinds, prompt templates, UI surfaces and safe output flows actually already exist. Implement only the missing high-value ones. Reuse current task model routing, metering, bounded generation and the explicit non-mutation guarantee from #187/#189.

**Revisit when:** a task kind has a concrete user-facing case not fulfilled by current generation/editor tools. The old issue's all-six-kinds wishlist is not itself a delivery mandate.

## 5. Historical prompt/turn replay — #151

**Original:** [#151 — Replay turn through gateway, with fork and seed](https://github.com/charlesmsiegel/grimoire/issues/151)

**Disposition:** **Reassess / possibly decline entirely.** The original gateway assumptions are obsolete. Historical prompt capture, attempts and campaign forking have developed since filing; replaying a historic turn may no longer justify a separate product surface. The wider exploratory requirement remains described in [deferred-systems.md](deferred-systems.md).

**Potentially worthwhile behavior *only if needed*:**
- Read-only, ephemeral rerun of **captured historical prompt input** against a specified model, with side-by-side results and metering.
- Distinguish best-effort text regeneration from exact replay of non-deterministic provider/model output; seeds are best-effort and not a reproducibility guarantee.
- Never rewrite original campaign state merely by running a comparison; alternate branch adoption requires deliberate fork/review semantics.
- Investigate whether existing retry, regeneration, prompt log, captured decisions and evaluation tooling already cover the actual debugging purpose.

**Revisit when:** there is a concrete reproducibility or A/B debugging need that current observability cannot address. May be marked permanently **won't do** after product audit without violating a committed milestone.

## 6. Accept/discard auxiliary results — #190

**Original:** [#190 — Accept or discard an auxiliary result](https://github.com/charlesmsiegel/grimoire/issues/190)

**Disposition:** **Reassess / possibly unnecessary.** A general accept-to-canonical-path dispatcher may overcomplicate a straightforward "copy to composer", "insert in scene", "edit message", or "discard" flow. Avoid imposing persistence for a purely hypothetical response.

**Potential requirements only if distinct auxiliary tasks demand them:**
- Auxiliary generation remains **ephemeral** until the user explicitly acts; discard is always non-mutating.
- If applying, route to the **correct existing canonical store mutation** (PC/user post, NPC/assistant post, draft replacement, edited message, or clipboard), with explicit target/authorization.
- All actual mutations use validated existing store APIs and respect concurrency/idempotency; the UI must not falsely indicate that copied text has become canonical.
- Keep destination/action mapping in one deliberate contract if multiple task kinds share it; do not add a universal dispatcher for a single operation.

**Revisit only alongside #188** and actual in-app Auxiliary Generation UI; may be omitted if current editing affordances already suffice.

## 7. Customizable campaign HUD — #196

**Original:** [#196 — Per-campaign HUD configuration](https://github.com/charlesmsiegel/grimoire/issues/196)

**Disposition:** **Defer to a coherent UI/Presentation improvement sprint** rather than build new HUD storage/layout infrastructure immediately.

**Retained requirements:**
- Choose, show/hide and reorder scene widgets (cast, location, time, events, commitments, possibly future inventory or mechanics).
- Optional density, position and grouping controls; reset to usable defaults.
- Stable widget identities and graceful handling of newly added/removed widget kinds.
- Campaign-specific settings if appropriate; consider whether they should travel with a campaign or stay device-specific. Prefer the project's established editable-file settings when campaign state should sync, rather than silently inventing browser-only persistence.
- Reconcile with the actual current scene HUD, inspector, mobile/Android layout, UI conventions and future inventory view. Do **not** take the pre-rebuild claim that no HUD exists as current fact.
- Ensure accessibility, performance and a sensible default layout; customization is a convenience, not a prerequisite for gameplay.

**Revisit when:** a broader navigation/HUD sprint or multiple new widgets (e.g. Inventory) justify configuration.

---

## Work intentionally left in the GitHub backlog

These four **unlinked-to-active-PR** issues should **be implemented during the backlog completion phase**, not retired to long-term planning:

| Open issue | Work to do | Note |
|---|---|---|
| [#62](https://github.com/charlesmsiegel/grimoire/issues/62) | Generate capsule/tagline and tags for sparse emergent NPCs | Reuse now-existing emergent actors and authoring machinery |
| [#128](https://github.com/charlesmsiegel/grimoire/issues/128) | Explain why prompt chunks/characters were included | Explicit inclusion evidence, not just a token-count summary |
| [#208](https://github.com/charlesmsiegel/grimoire/issues/208) | Ship a proper macOS launcher icon | Small packaging task, platform compatibility |
| [#437](https://github.com/charlesmsiegel/grimoire/issues/437) | Resolve and implement image-library placement and EntityEditor browse-layout work | UI contract decision required; retain issue open until done |

The ten PRs under review (`#519`–`#528`, all verified open during triage) and their **11 associated issues** are **not to be closed by this consolidation**. Once they land, let verified PRs close the corresponding issues normally. #480 has partial coverage and must be reconciled before closing it.

**Desired state:** the remaining GitHub issues are real, actionable items in progress or genuinely intended to be implemented immediately—not an unbounded wish list. Deferred optional features and major platform consumers live on the permanently unmerged `roadmapping` branch.

**Source archive:** the original #81/#93/#132/#188/#151/#190/#196 bodies are kept in [archived-issue-text.md](archived-issue-text.md); their GitHub issue bodies are left intact below a clear closure notice, so the full history survives closure.
