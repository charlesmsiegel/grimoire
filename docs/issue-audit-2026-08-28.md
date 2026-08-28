# Issue audit — 2026-08-28

A re-prioritization of all 59 open issues, verified against the tree at
`d0405af`. Unlike the 2026-08-19 audit (which re-scoped issue bodies in
place, per module), this one organizes by **which aspect of play each issue
protects or improves** — UX/workflow, character coherence, plot & continuity
coherence, mechanical adherence, prompt transparency & control, data
portability, infrastructure — because that is the axis a "what should we
build next" decision actually runs on.

Every issue's "Current state" section was spot-checked against the code.
Since the Aug-19 audit the tree moved ~50 commits (write token #409, costs
rail / usage rollup, director notes as transcript lines), but the bigger
drift is older claims the last audit didn't touch: several issues name
blockers that have since shipped (#36 entity kinds, #37/#222 typed entity
fields, #59/#60 voice anchors + emergent characters, #100 advance, #110/#111
banding + conflicts, #120 turnstate, #126 packer, #130 prompt diff, #139
export formats, #150 prompt log, #160 mechanics, #376 post images,
`sync.promote`/`push`, `sync.diverged` + CompositionPanel). One structural
note: `routes.py` and `store/context.py` are packages now, so file:line
citations in older issue bodies are stale as *lines* even where the claims
hold.

## 0. List hygiene first: close, verify-and-close, or narrow

These change the list before any prioritization of it means anything.

| Issue | Verdict | Why |
|---|---|---|
| #57 | **Close** | Both remaining checkboxes shipped: bulk tagline derive is `POST /worlds/{wid}/characters/taglines/generate` (`routes/characters.py:349`), and the dossier-staleness question is answered in prose at the top of `store/dossiers.py` ("No staleness hash, and that is a decision"). |
| #113 | **Close (or narrow)** | Option A is built end-to-end: `sync.diverged` (`store/sync.py:670`), `GET /campaigns/{cid}/diverged`, rendered as "campaign override" in `CompositionPanel.tsx`. Residue if kept: the `source: absorb|manual` distinction, and drift trails for relationship/bond/plot edits. |
| #119 | **Close (or narrow to lore→character)** | `entities.reclassify` + `store/reclassify.py` implement the entity-kind case including the cross-campaign sync sweep the issue predicted; frontend picker exists. Only Option B (lore→*character* conversion, a different record shape) remains. |
| #26 | Narrowed already | Avatar half shipped (#25); what's left is iterating `data.assets` past `AVATAR` in `import_card`, reusing the existing safe-zip machinery. Small. |
| #40 | **Narrow to "world default climate"** | World calendar editor shipped (#223); mechanics default exists. Atmosphere exists but campaign-scoped (`store/campaign_climate.py`); the world-level default is the only open slice. Small. |
| #71 | **Narrow to "per-ref sync freeze"** | The composition overview and sync UI it asked for exist (`CompositionPanel`, `IncomingReview`, `sync.promote`/`push`). Only the per-ref pin ("stop offering me updates for this ref") is missing. Small. |
| #22 | **Narrow to frontend** | The backend primitive shipped: `POST .../first-post` takes arbitrary text with the right guards. Missing is only a "paste your own opener" affordance in `SceneConfirmForm`/`OpenerComposer`. Small. |
| #82 | **Decide, don't build** | The nominated-speaker layer exists and is gated off by default (`DEFAULT_SPEAKER_TURN_TAKING = "off"`). One playtest decides whether per-NPC calls are still wanted. Cheapest open item, and it gates how #58 should be sequenced. |
| #437 | **Decide, don't build** | Both halves are recorded in code comments as deliberate decisions (`librarySections.ts:63`, `EntityEditor.tsx:803`). Deciding rail-vs-grid matters more than Images: it is live convention drift (`CharacterEditor` already ships a grid) that every future editor inherits. |

## 1. Character coherence

Voice, identity, attribution, and knowledge boundaries — "does the cast stay
who they are."

**Now**

- **#61 + #62 — emergent-NPC anchor, capsule and tags, one pass.** Both
  former blockers (#59, #60) shipped; each issue collapsed to the same small
  task: a *transcript-sourced* draft (today `voice_anchors.build_prompt`
  reads the card only, which starves exactly the emergent case) plus a
  first-scene/absorb trigger. The failure prevented is an NPC who walked on
  mid-scene drifting into generic narrator voice and staying an unlabelled
  name in the cast. Small; build together.
- **#86 — per-scene POV.** Still absent everywhere (`pov` greps clean).
  Small-medium, copies the per-scene datetime/location pattern, and it
  unblocks the auto-hide half of #122 and the POV consolidation half of
  #140. High leverage per line of code.
- **#81 — multi-PC attribution.** A two-PC scene still writes an
  *unattributed* user post (`speaker = names[0] if len(names) == 1 else
  None`), degrading who-said-what for the model, the recap, and the ledger.
  Option A (client-side queue over the existing marker machinery) is medium.

**Next**

- **#122 — thought privacy, Option A.** One `private` playstate section +
  one context rule + one drawer block; prevents the model reading every
  character's inner monologue through a PC's eyes. Ship Option A without
  waiting for #86; the observer-filtered half waits.
- **#58 — compressed card tiers (`full|voice|capsule`).** The packer now
  handles blunt overflow, but *deliberate* per-NPC prominence still doesn't
  exist — a walk-on costs the same prompt space as the lead. Sequence after
  the #82 decision, since both touch the same layer of the context builder.
- **#65 — structured PC fields (goals, narrator guidance).**
  `PERSONA_FIELDS` unchanged; small for Option A, and the "does
  player_notes reach the model" decision doubles as a transparency rule.

**Later**

- **#50 — character variants across worlds.** Real, unchanged, medium; the
  cross-world search dependency landed but world fork (#41) did not.

## 2. Plot & continuity coherence

The world advancing consistently — canon, time, and lore that actually
activates.

**Now**

- **#220 — items/groups/creatures as lore owners.** The blocker (#36)
  shipped, but `loreOwnerOptions` still stops at characters/pcs/locations —
  so an item-owned lore entry can be written and then *silently never
  fire*. Small if presence is defined as "the owner's own entry activated."
  Silent non-activation is the worst kind of continuity bug: invisible.
- **#91 — adapted greeting.** The three-way opener selector shipped, so
  this is now literally a fourth radio: rewrite a greeting against current
  campaign state instead of pasting text written for session one into a
  campaign forty scenes deep. Small-medium.

**Next**

- **#102 — NPC/group ticks over a time skip.** #100's advance + digest
  shipped, and the digest's overdue/stale annotations already are Option
  C's deterministic report; group state exists now. The remaining build is
  the reviewable interim pass. Medium. Do before #108.
- **#105 — time granularity.** Day-only precision is now the binding
  constraint on #108 (durations are `days` only) and stored time already
  partially reaches prompts (`{{time}}` macro, weather). Medium.
- **#108 — activity advancement.** Estimate a duration, advance, resolve
  through review. Medium-large; sequence after #102 (shares the resolve
  shape) and #105.
- **#377 — send post images to the model.** More built than the issue
  thinks: content parts flow for two of three provider kinds today. What
  remains: `_strict_messages` flattening, a vision-capability field on
  connections, the `send_images` gate, and the budget/accounting decisions.
  Medium; the payoff is the model finally seeing what the scene shows.

**Later**

- **#55 — library activity feed.** The gap is real but a key premise
  changed: `worlds.touch()` now updates on sync ops, and the campaign side
  has `revision.txt`/`activity.txt` as a model. Medium.
- **#42 — spatial queries.** Effort dropped sharply: with #222's typed
  `ref` widget, a `connections` multi-ref field on locations is a table
  entry plus a small pure query module. Small-medium, and it unblocks #43.
- **#124 — promote an extra to canon.** Still hard-blocked on #123
  (extras don't exist), but its Option B unblocked: `sync.promote`/`push`
  are the campaign→world path the body says doesn't exist.

## 3. Mechanical adherence

Game rules meaning what they say.

**Now**

- **#189 — auxiliary suppression invariants.** The issue's own "this is
  free today" claim is now decisively false: mechanics (#160), two drift
  checkers, and the turnstate ledger all exist, so an auxiliary path must
  *actively* skip mechanics sections, drift staging, turnstate writes and
  cost bucketing. Write the invariants + regression test **before** #187
  builds the framework — this is the integrity half of the aux epic.
- **#221 — sheets on world records.** Re-scope and unblock: the v1 typed
  fields it deferred are shipped, and `store/sheets/` *is* the mechanics
  data contract it was waiting for. The open question is now concrete: can
  a creature/item/group carry a module sheet. Medium.

**Parked (keep parked, but the premises improved)**

- **#167 → #168 → #169 → #170 → #171 — the inventory epic.** Still
  `epic:parked`, still nothing inventory-shaped in the tree. But if it
  is ever unparked, the ground has shifted in its favor: `items` is a
  synced entity kind with `holder`/`item_type`/`rarity` fields (half of
  #168), absorb now has conflict handling and confidence banding (#170's
  "no conflict handling exists" is false), failures are reported
  (`apply_edits` returns `(applied, failures)`), and `store/sheets/`
  resource fields are a second candidate home that should be weighed
  before adding a third per-campaign state file. Start at #168's
  campaign-side ledger half if unparking; note `holder` (a static library
  fact) needs a stated relationship to any campaign ledger or the two will
  disagree.

## 4. Prompt transparency & control

Knowing — and choosing — what the model was told. This aspect is the
multiplier on all the coherence aspects: you can't fix what you can't see.

**Now**

- **#128 — per-chunk inclusion reasons.** `activate()` still discards the
  matched key; the `/context` payload carries no reasons. Two new reason
  categories (pins/exclusions #129, semantic recall) have appeared since
  filing and want covering. Small-medium; the core "why did the model know
  that" question.
- **#131 — expand a source to its exact text.** Cheaper than written:
  `_world_info` now returns `{body, kind, id}` dicts, so identity survives
  to the template boundary and is flattened only at the join. Off-scene
  cast is already split. Small-medium; shares structure with #128 — build
  together.

**Next**

- **#132 — live debounced prompt preview.** The `wi_seed` threading is now
  doubly precedented (opener, director notes). New cost consideration: a
  preview runs a full assemble+pack, so the debounce interval is a real
  decision. Small-medium.
- **#151 — replay a turn through the gateway.** All three named
  dependencies (#150 prompt log, #72 fork, #77 model-override reroll)
  shipped; what's missing is one ephemeral stream over stored messages.
  Small now. Watch the name collision: `/replay` in `routes/scenes.py` is
  #79's retcon replay, a different feature.
- **#30 — named prompt-template variants.** `store/styles.py` is a working
  precedent for exactly the bundled+user overlay this asks for. Medium.
- **#71 (narrowed) — per-ref sync freeze.** Small.

**Later**

- **#109 — extraction modes.** The inline mode's hard part (fenced-block
  grammar, streaming redactor) is proven by turnstate; "none" exists but
  undeclared. Medium; do when a second mode has a concrete demand.

## 5. UX & workflow

Friction in the editors, wizards, and play surface.

**Now**

- **#17 — re-point a greeting.** Still delete-and-recreate, which loses the
  greeting id and therefore its plot-map edges — a data-loss edge wearing a
  UX costume. `update_greeting` has since grown two new fields, so the
  path is proven extensible. Small.
- **#27 — embedded character_book through parse→review→commit.** The blind
  one-click commit now misfiles across *five* kinds, not two, since
  `LorebookImport`'s category picker reads the live entity-kind list. The
  review UI exists; extract it into a shared component. Medium. (Misfiled
  lore is also a continuity bug: keyless lore is always-on, keyless
  locations are not.)
- **#68 — greeting availability preview in the wizard.** A thin wrapper
  over the existing pure `greetings.availability` plus a debounced fetch;
  the wizard currently promises "tags unlock matching openings" without
  showing which. Small.
- **#22 (narrowed) — paste-your-own opener UI.** Small, frontend-only.

**Next**

- **#18 — character-as-player should satisfy requires_tags.** The gate
  silently never opens for a character cast as player (only PCs carry
  tags). Needs a small design decision (where character tags live) more
  than code; `overlay.pc_root` means the change lands in one place. Medium.
- **#73 — campaign settings IA.** Substantially overtaken: routing,
  response, climate, calendar are all per-campaign already. What's left is
  a consolidated surface plus three still-global keys (`recap_depth`,
  `context_scan_depth`, `system_prompt`). Small-medium now.
- **#66 — merged PC card.** `GET /diverged` now answers the divergence
  third cheaply; capabilities third still waits on #161. Medium.
- **#67 — PC revision history.** Edits are still destructive overwrites;
  Option A is ~4 store functions. Small, and it's the undo story for the
  longest hand-written text in the app.
- **#38 — world creation form.** The "no overview surface" claim is stale —
  `WorldOverview` is a real hub now, so genre/description/tone have an
  obvious home. Small-medium.

**Later**

- **#187 → #190 → #188 — the auxiliary-generation epic** (with #189 first,
  from §3). Framework cost *grew*: ephemeral generation now runs through
  detached runs and the cost ledger, so a new task family must slot into
  run records and attribution, not just SSE. #190 must also not collide
  with synthetic speakers (`DIRECTOR_SPEAKER`, `ROLL_SPEAKER`), and
  #188's continue-as-character should be framed against the shipped
  director-note flow rather than duplicating it. Worth doing as a planned
  epic, not casually.
- **#196 — per-campaign HUD config.** The "green field" framing is wrong
  now: `SceneInspector` already persists per-section collapse in
  localStorage with stable widget ids. Remaining: reorder, density,
  grouping, per-campaign scope, reset. Medium; #195 still absent.
- **#43 — map view.** Blocked by #42; its Option C (connection chips in
  the detail sidebar) is small and shippable first. `PlotMapEditor` is a
  hand-rolled node/edge precedent the issue predates.
- **#56 — create-world skill.** Smaller than filed: `populate-world-content`
  and its CLI cover the post-import half; the remaining gap is genuinely
  "a world from a concept, from nothing." Medium.
- **#55, #40 (narrowed), #437** — see their entries above.

## 6. Data portability

Getting content in and out without loss.

**Now**

- **#20 — preserve ST advanced lorebook fields.** Still dropped
  irreversibly at import — and *every import that happens while this is
  open loses the fields permanently*, which is why a small stash-only
  change outranks its size. The typed-fields work (#222) created a
  sanctioned place to land the stash that didn't exist at filing. Small.
- **#136 — plaintext character import.** Still no path for pasted prose;
  the preview-then-persist convention it wants is now used by five
  features and `run_draft` gives drafts an async home. Medium; the
  biggest onboarding ramp on the list.

**Next**

- **#140 (narrowed) — export filters + history.** The premise died: #139's
  `collect()` + four formats shipped, so anonymize is one pass over its
  output and history is one sidecar. Small. POV consolidation still waits
  on #86.
- **#26 (narrowed) — bundled CHARX images.** Small.

**Later**

- **#93 — copy a scene across campaigns.** Materially reduced: #92's
  scene import + campaign fork already achieve it manually with review;
  what's left is the picker convenience layer. Medium-low.

## 7. Infrastructure & polish

- **#227 — mechanics phase-8 minors.** All spot-checked items still open.
  One has a data-safety edge and is worth pulling out of the batch: the
  `recover()`/`_replay_journal` startup path has no guard against an I/O
  error escaping into lifespan. The rest are true minors.
- **#208 — macOS `.icns`.** Confirmed unchanged; `gen_android_icon.py` is
  a precedent for the iconset pipeline. Small; #209 would moot it.

## The shortlist, across aspects

If the next stretch of work took only the top of each aspect, in order:

1. **Hygiene**: close #57/#113/#119; run the #82 playtest; write down the
   #437 rail-vs-grid decision. (Hours, and the list shrinks by five.)
2. **#61+#62** — emergent-NPC voice/capsule pass (character coherence,
   small, fully unblocked).
3. **#20** — stash ST lorebook fields (portability, small, *lossy while
   open*).
4. **#17, #68, #22-residue** — three small greeting/opener frictions.
5. **#220** — lore owners that can actually fire (continuity, small).
6. **#86** — per-scene POV (small-medium, unlocks #122/#140 halves).
7. **#128+#131** — inclusion reasons + source expansion (transparency,
   built together).
8. **#91** — adapted greeting (continuity at scene start).
9. **#122 Option A** — thought privacy (knowledge boundaries).
10. **#81** — multi-PC attribution (the one remaining silent
    mis-attribution in the transcript).

Behind those: #102 → #105 → #108 as the time-skip arc, #27 + #136 as the
import arc, #58 after the #82 decision, #151/#132/#30 as the transparency
arc, #221 as the mechanics arc — and the two epics (inventory, auxiliary
generation) stay parked/planned rather than started casually, each with a
note above about how the ground under them has improved.
