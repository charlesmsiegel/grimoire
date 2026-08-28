# Issue audit — 28 Aug 2026

A re-prioritization of the 59 open issues against the tree as it stands today.
The last sweep of these bodies predates most of what now defines the codebase —
mechanics modules (`store/sheets`, `store/dice.py`, `store/rolls.py`,
`store/checks.py`, `store/expressions.py`), voice anchors and drift
(`store/voice_anchors.py`, `store/voice_drift.py`), the five entity kinds,
emergent campaign-local characters (`store/overlay.py`), the fact ledger
(`store/facts.py`), events (`store/events.py`), the journal
(`store/journal.py`), commitments (`store/commitments.py`), prompt snapshots
(`store/prompt_log.py`), turn state (`store/turnstate.py`), the draft-run
contract (`runs.run_draft`), the detached-run machinery, and the play view's
column layout — so many "current state" sections in the issues describe a
codebase that no longer exists. Several issues are simply done.

This audit groups by the **aspect of the play experience** each issue affects,
not by the module it lands in. Within each aspect, issues are ordered by
priority. Priorities weigh three things: how much the aspect suffers today,
how much the cost dropped now that dependencies have landed, and whether the
issue is a decision rather than a build.

---

## 0. Overtaken by the codebase — verify and close

These cost nothing but reading, and closing them makes the backlog honest.

| Issue | Why it is done |
|---|---|
| #119 Reclassify a lore entry | Implemented wholesale: `store/reclassify.py` cites the issue in its docstring, handles both scopes, sweeps campaigns, repoints ledgers. |
| #113 Show drifted campaign records | `CompositionPanel.tsx` renders a per-ref `diverged` state (in the `conflict > update > diverged > insync` ladder) from the sync manifest — exactly the "explicit provenance" half the issue said was missing. |
| #57 Bulk taglines / dossier staleness | `POST /worlds/{wid}/characters/taglines/generate` exists and cites #57; the todo surface lists untaglined and anchorless characters per world. All that remains is the one-line decision on dossier staleness — record it and close. |
| #61 Auto-draft a voice anchor | Both its dependencies (#59 anchors, #60 emergent characters) landed, and generate routes exist at **both** scopes: `POST /worlds/{wid}/characters/{cid}/voice-anchor/generate` and `POST /campaigns/{cid}/characters/{char}/voice-anchor/generate` (202 + draft run). Verify the campaign route covers the emergent case, then close. |
| #189 Suppression invariants for aux tasks | The draft-run class **is** this contract: no exclusion key, no durable result, no writes, one shape for all twelve computing previews. The things the issue asked to suppress are structurally unreachable from a draft. |
| #22 Seed a scene from arbitrary text | `POST .../first-post` takes `FirstPost{text}` — arbitrary text, not just the streamed opener. Verify the UI lets a pasted text reach it (and that substitution applies), then close or trim to a UI-only remnant. |
| #40 World metadata editors | The half the issue called missing exists: `GET/PUT /worlds/{wid}/calendar` routes are live, and "atmosphere" became the climates system (`store/climates`, `ClimateEditor`, `ClimatesView`). Trim to whatever "per-world defaults" still means, or close. |
| #187 Auxiliary task framework | `runs.run_draft` / `common.draft_completion` / `api.draftRun` is the general `(kind, subject)` contract the issue asked for — twelve kinds already ride it. The user-facing remainder is #188/#190; fold and close. |
| #71 Composition controls | `CompositionPanel` is the composition overview with per-ref state, version locks carried beside sync state, and the incoming diff. Remaining sliver: a per-ref freeze ("stop offering me updates for this one"). Trim the issue to that. |

---

## 1. Character coherence

*Does each character sound and act like themselves, at every prominence level?*
This aspect got the most infrastructure since the issues were filed (anchors,
drift, taglines, dossiers, emergent characters, turn state) — the open items
are now the gaps **between** those systems.

- **#58 Compressed card views (voice-only / capsule) — HIGH.** Still true: a
  walk-on seated NPC injects its full card exactly like the lead. Now that
  anchors render per present character and `pack.py` budgets sections, the
  missing piece is a per-appearance tier so a minor character costs a capsule,
  not a card. This is the highest-leverage character-coherence item because it
  protects the lead voices' share of the context on every single turn.
- **#82 Decide the per-NPC-call question — HIGH (decision, cheap).**
  `speaker_turn_taking` still ships default-off. The issue is now literally a
  playtest checklist: turn it on, play a four-hander, close or re-scope. Do it
  before building anything else in this aspect — its answer bounds #81 too.
- **#62 Capsule + tags for sparse emergent NPCs — MEDIUM-HIGH.** Emergent
  characters exist now and are created from play with near-empty cards (the
  materializer writes one provenance sentence), so the sparse-card problem this
  predicted is live in every campaign that plays long enough. The tagline
  machinery it extends is built, including bulk derive.
- **#122 Thought privacy — MEDIUM.** Still unbuilt; `turnstate.py` is
  transient mood/intent, not private knowledge. Real value for GM-style play,
  but it needs #86's observer model to mean anything. Build after #86.
- **#86 Per-scene POV — MEDIUM.** Still no `pov` frontmatter anywhere. The
  template layer proved viewpoint switching works (`pcless` selectors), so the
  cost is modest, and it unblocks #122 and sharpens narration voice.
- **#66 Merged PC card / #65 structured PC fields / #67 PC revision history —
  MEDIUM-LOW.** `PERSONA_FIELDS` is unchanged, so #65 is accurate as filed.
  #67 note: `store/journal.py` now keeps write-back history, but manual PC
  saves still bypass it — the issue's gap is real but narrower than filed.
  #66's "merged view" is half-covered by the casefile/composition surfaces.
- **#50 Character variants across worlds — LOW.** Copy-to-world with lineage;
  no dependency changed. Nice library feature, no coherence risk without it.
- **#18 Character-as-player tags — LOW.** Confirmed still true
  (`playing.player_tags` counts PCs only). Edge case; revisit on demand.

## 2. Plot & continuity coherence

*Does the story stay consistent across scenes, skips and campaign lifetimes?*
The ledgers this aspect needed (facts, events, commitments, journal, briefing,
chronicle watermark) all landed; what is left is making **time** first-class.

- **#102 Tick NPC/group state over a time skip — HIGH.** `clock.py` explicitly
  defers the "meanwhile" pass (#100's option C), and every ingredient now
  exists: group state, facts, events, the absorb StagedEdits review. A long
  skip today silently freezes the world, which is the single biggest
  continuity hole left in long-form play.
- **#108 Advance to the end of an activity — MEDIUM-HIGH.** Builds directly on
  the now-landed `clock.advance`; the estimate → advance → resolve composition
  is the natural next rung, and pairs with #102 (a resolved activity is a
  "meanwhile" with a known endpoint).
- **#91 Adapted greeting — MEDIUM-HIGH.** Still absent (no adapted mode in
  `store/playing.py` / greeting routes). Verbatim greetings visibly contradict
  an advanced campaign's state — a direct coherence failure at the most
  visible moment, the scene opening. The opener/draft machinery it needs is
  all built.
- **#124 Promote an extra into a fact or the library — MEDIUM.** Both landing
  spots the issue said were missing now exist: `store/facts.py` (the fact
  ledger) and `sync.promote` (campaign→world). Re-scope the body around them;
  the remaining build is mostly UI plumbing.
- **#105 Per-campaign time granularity — MEDIUM.** Unchanged: minutes exist in
  the store (`Thh:mm`) but are unreachable from the picker and dropped from
  prompts, so scenes at different hours of one day still blur. Half of this is
  a small UI/template fix that could ship alone.
- **#93 Copy a scene between campaigns — LOW.** `store/scene_import.py` (#92)
  now handles parse/commit of grimoire transcripts, which is the honest
  four-fifths of a cross-campaign copy. Re-scope this as a thin layer on it.

## 3. Mechanical adherence

*Do the game's rules — sheets, dice, possessions — bind what the fiction says?*
The mechanics engine the parked items were waiting for exists now.

- **#221 Per-kind game-mechanical fields on entities — HIGH.** Its stated
  blocker — "the Mechanics & Dice Phase 1 data contract" — has landed
  (sheets, `entity_schema.py`, `expressions.py`). Creature stat blocks and
  item mechanics defined once against that contract is the difference between
  the dice engine binding NPCs/monsters or only PCs.
- **#167/#168 Inventory subsystem + item records — MEDIUM-HIGH (unpark).**
  The epic was parked when nothing it needed existed. Today the `items` entity
  kind is real, campaign continuity ledgers are a settled pattern, and the
  absorb review pipeline is exactly the "never silent writes" gate #169 asks
  for. Who-holds-what is both a mechanics and a continuity hole ("the sword
  she lost two scenes ago"). Recommend unparking the ledger (#167/#168) and
  extraction ops (#169) and leaving resolution/UI (#170/#171) parked behind
  them.
- **#220 Items/groups/creatures as lore owners — MEDIUM.** The kinds exist
  now; the issue's real content — presence semantics — is unchanged and still
  the right question to settle before wiring the picker.
- **#227 Mechanics phase-8 minors — LOW (chore).** Cheap, batched, none
  load-bearing; good filler work.

## 4. What the model sees — prompt quality & fidelity

*Is the prompt the best version of the campaign the budget can buy?*

- **#377 Send post images to the model — HIGH (cost collapsed).** The issue
  costs itself as "change message content from `str` to content parts across
  all clients — the bulk of the work." That work is **done**: `llm.py` already
  carries content-part messages and documents which connection kinds can't
  (the Claude SDK path joins to string). What is left is the cheap half:
  capability detection from the model catalog plus assembling image parts from
  localized transcript images. Visual scenes are already first-class in the
  transcript; the model is the only reader still blind to them.
- **#109 Extraction modes with auto-select — MEDIUM-LOW (re-scope down).**
  The "separate" mode grew watermarks, pending reviews and detached runs, and
  `turnstate.py` is a working in-reply structured block (fenced, stripped,
  recorded) — a proof the "inline tracker" mode's grammar works. Tool-use
  extraction and auto-select look like machinery without a driving failure;
  recommend narrowing this to "document the two real modes, drop auto-select
  unless a failure shows up."
- **#30 Named prompt-template variants — LOW (value shrank).** Since filing,
  users got response presets, style guides, and the prompt layout (order,
  presence, labels of sections). The audience that remains is people editing
  Jinja by hand; `GRIMOIRE_TEMPLATES` already gives them a whole-tree
  override. Keep open, low.

## 5. Trust & transparency

*Can the player see why the model said what it said?* The inspector grew
frozen per-turn snapshots (#157), a turn-vs-turn diff (#130), and layout
labels — the remaining items are about granularity, not existence.

- **#131 Expand a source to its exact text — MEDIUM.** Still true that
  world-info and cast sections merge sources into one blob. This is the most
  useful of the three inspector items because it turns "this section is 3k
  tokens" into "this entry is the 3k tokens".
- **#128 Say why each chunk is in the prompt — MEDIUM-LOW.** `activate()`
  still discards the matched key. Small, honest feature; pairs naturally with
  #131 (one payload can carry both).
- **#151 Replay a turn — MEDIUM-LOW (dependency landed).** Its blocker was
  "nothing stores what was sent"; `store/prompt_log.py` now freezes exactly
  that per turn. An ephemeral re-run of a frozen prompt through the draft
  machinery is now a modest build. "Gateway, fork and seed" remain out of
  scope — re-title accordingly.
- **#132 Live prompt preview while typing — LOW.** Unchanged, and the least
  urgent: it burns composition work per keystroke to answer a question the
  frozen snapshots mostly answer after the fact.

## 6. Play-surface UX

*The moment-to-moment experience of running a scene.*

- **#188 Auxiliary task kinds / #190 accept-or-discard routing — MEDIUM-HIGH
  (as one unit).** The framework (#187) exists; these two are its entire
  user-visible value: impersonate, polish, "what would she say", brainstorm
  are the workflows players actually reach for mid-scene. Every kind maps to
  context-builder pieces that already exist, and acceptance destinations
  (composer draft, appended post, edited post) are all built primitives.
- **#81 Multi-PC turn loop — LOW-MEDIUM.** Unchanged single-player assumption
  in the streaming routes. Only worth building if multi-PC play is actually
  wanted; #82's decision should come first since a speaker-nominated single
  call weakens the case for a pending-post buffer.
- **#196 Per-campaign HUD configuration — LOW (re-scope).** The "HUD" became
  the play view's column architecture (cast, dossier, conditions) plus the
  prompt-layout pattern for per-section presence/order. If configurability is
  still wanted, it should mirror `context/layout.py`'s override shape over the
  play columns — a much smaller issue than the one on file.
- **#73 Campaign settings tabs — LOW (mostly overtaken).** Per-campaign
  routing exists (`store/routing.py` reads `campaign.md`), the hub has a
  Settings section, and presets/styles are per-campaign choices. Trim to
  whatever specific knob is still global-only but wanted per-campaign.
- **#171 Inventory HUD + review — LOW.** Parked correctly behind #167–#169.

## 7. Authoring & library UX

*Building and maintaining worlds outside a scene.*

- **#17 Re-point a greeting — HIGH within aspect (quick win).** Confirmed
  still missing: `update_greeting` takes no `character`/`version`. Delete-and-
  recreate loses plot-map edges, which is real data loss for a routine edit.
  Small, sharp, shippable.
- **#27 Route embedded character_book through review — MEDIUM (quick win).**
  The one-click import still bypasses the parse-review-commit flow the
  lorebook importer already has (dedup softens but doesn't fix mis-categorized
  entries). Consistency fix on built machinery.
- **#437 Library Images section + entity browse grid — MEDIUM (decision).**
  Both halves are decisions someone has to make, and the first has a natural
  answer now: the undescribed-image backlog is already a cross-world todo
  chore, which argues for the all-worlds grid. Decide, then the builds are
  ordinary.
- **#38 Rich world-create form — MEDIUM-LOW.** Unchanged (creation is
  name-only). Worth noting the body's caveat still holds: `world.md` doesn't
  reach play context, so genre/tone only matter if a context section is added
  for them — that's the real feature.
- **#68 Greeting-tag wizard preview — LOW.** Matching is built; the preview is
  polish.
- **#55 Library activity feed — LOW.** `journal.py` covers campaign-side
  history only; the world-side signal problem is unchanged. Low demand.
- **#56 `create-world` authoring skill — LOW.** Largely superseded in practice
  by the `populate-world-content` and `world-card-integration` skills; what
  remains is the from-scratch concept case.
- **#42/#43 Spatial queries + map view — LOW.** Unchanged, big build, modest
  payoff; the location graph should wait for a consumer (adjacency-aware
  context or travel-time on the clock would be one).
- **#40 remainder (per-world defaults) — LOW.** See §0.

## 8. Import/export fidelity

*Content survives crossing the boundary in either direction.*

- **#20 Preserve ST advanced lorebook fields — MEDIUM-HIGH (quick win).**
  Confirmed still dropped at `_normalize`, and the loss is irreversible per
  import. Stashing the fields is cheap insurance whether or not activation
  ever honors them.
- **#26 CHARX bundled non-avatar images — MEDIUM-LOW.** The avatar half is
  done (bundled `embeded://` avatars land, budgeted and sniffed); the gallery/
  expression assets remain ignored. `store/expressions.py` exists now, which
  strengthens the case for landing the rest.
- **#136 Free-text character import — MEDIUM-LOW.** Unbuilt, but the
  scenario-card pipeline (`store/scenario.py`) established the exact
  LLM-parse → review → commit shape this should reuse; the heuristic-only
  option is probably no longer worth building.
- **#140 Export filters + history — LOW (re-scope).** The shared collector the
  issue wanted to sit on (`store/export.py`) exists with multiple renderers.
  Anonymize is feasible from the roster; strip-OOC still has no marker
  convention to key on. Trim to anonymize + history.

## 9. Housekeeping

- **#208 macOS `.icns` + `.app` wrapper — LOW.** Accurate as re-scoped; purely
  platform polish.
- **#227 — LOW.** See §3.

---

## The short list

If the next stretch of work took only ten items, in order:

1. **Close-out sweep** (§0): verify and close/trim #119, #113, #57, #61,
   #189, #22, #40, #187, #71 — nine issues off the board for an afternoon.
2. **#377** — images to the model (the hard half is already in `llm.py`).
3. **#17 + #20 + #27** — three small, confirmed-open fidelity fixes.
4. **#82** — run the playtest, settle the speaker-loop question.
5. **#58** — presence tiers for seated NPCs.
6. **#102** — the "meanwhile" pass on time skips.
7. **#188/#190** — auxiliary task kinds on the existing draft framework.
8. **#221** — per-kind mechanical fields against the landed data contract.
9. **#91** — the adapted greeting.
10. **#167/#168** — unpark the inventory ledger.
