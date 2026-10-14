# Roadmap 11-S3: perspective-aware retrieval and prompt separation — plan

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-11-epistemic-history-retrieval-design.md`
§6 (11-C2), §8.1–8.2 (the offline arm of 11-C4), §11 "Prompt separation",
§13, and the 11-S3 slice. **Needs:** 11-S2 (in this stack), 09-S1/S2 (on
`spec-09-hybrid-history`, built on here); 10-S1/S2 are soft and not on main,
so there is no planner and an actor's evidence is 09's alone.

**Goal:** history reaches NPC prompts for the first time, classified by
construction. Each compose step retrieves for its own perspective, `classify`
runs on what came back, and compose renders only what the view allows: the
narrator gets `history_recall` (annotated) and `history_narrator_only`; an
actor gets `history_known` and `history_suspected`, built from `visible()`
alone. Everything stays behind 09's `history_recall_depth` (off by default),
so an off prompt is byte-identical.

## 1. Where the landed code moves §6

Each is a spec amendment ("Amended by 11-S3").

1. **The per-step hook** is between `_pre_generation` and `_prepare` in
   `_round_frames` (`routes/character_turns.py`), where 09's `_round_history`
   sits. `_select` runs only for a round's first speaker.
2. **Restricting an actor's query needs more than `query.prepare`.**
   `retrieval._prepare` and `structural.candidates` re-derive subjects through
   `query.subjects`, which re-adds the whole present cast, the location,
   present actors' groups and thread/commitment involvement. So `subjects`
   takes the query's perspective: for an actor it returns the query's own
   seeds unchanged (built once in `prepare`), and the actor's `Query.scopes`
   drop `"thread"`. Thread and commitment names are filtered from the
   observed window's seeds too (`_thread_catalog` puts them in the catalog).
3. **An actor's seeds:** the present roster, the scene's location (the actor
   is standing in it), the actor's own relationship targets
   (`actor.own_relationships`' reading of `relationships.read`), and catalog
   records named in the observed window, minus `thread:` and `commitment:`.
   The query text is the observed window's tail, through the same prompt view
   and `QUERY_BYTES` clip.
4. **`summary_omissions` is the narrator's.** An actor's omissions are `{}`:
   the archive and recap that carry a summary for the narrator are blanked
   for actors.
5. **`Perspective.present` needs PCs; the round's `present` does not hold
   them.** A helper `epistemic.present_roster(cid, sid)` rebuilds it as
   `_assemble` does: `appearances.cast.scene_cast` minus the pins' excluded
   refs.
6. **Per-step caching.** `history_by_round` becomes keyed by `(round_id,
   actor)`; the appended/continuation and replay-peek skips apply per step.
   A roll resume still recomposes with no history: storing the view on the
   response record is 11-S6's (spec slice text), and 09 already stores no
   evidence there.
7. **Rendering.** 09's `history.section` needs real `EvidencePost`s to mark
   spans, and an actor's `Classified.item` is redacted. So each visible
   `Slice` carries the `EvidencePost`s of its run (`Slice.source`, never in
   `detail()`), and the sections are built as `EvidenceItem`s from visible
   slices and passed through a generalized `history.section` that takes its
   frame. One fitting, macro and shed path for all four sections.
8. **Drop order needs tiers.** The packer drops the largest section first
   within a tier, so "narrator-only before recall, suspected before known"
   cannot be one tier. A new tier `HISTORY_ASIDE` goes first in `DROP_ORDER`
   (holding `history_narrator_only` and `history_suspected`, which never share
   a prompt), then `HISTORY_RECALL` (holding `history_recall` and
   `history_known`). 09's pin `DROP_ORDER[0] == HISTORY_RECALL` moves.
9. **The shed pin counts functions.** All four sections shed through the one
   generalized `history.section`, so 09's AST pin (`(module, function)` pairs
   carrying a shed dict) is unchanged; the spec's "set of five" sections is
   held by a new test that composes each section and asserts its shed units.
10. **Layout coupling has no mechanism.** `layout.COUPLED` maps each new id to
    `history_recall`: `describe` omits them (the editor shows one "Recalled
    history" row), `apply` keeps them directly after their parent and removes
    them when the parent is disabled, and `enabled_ids` answers through the
    parent.
11. **The budget is shared.** The narrator's two sections, and the actor's
    two, split 09's one ceiling in proportion to their item counts, so 11
    never spends more than 09 would.
12. **The live inspector is narrator-only.** `context_breakdown` composes
    with no actor, so it shows the narrator view's classification (each item's
    state, `known_to`, `suspected_by`, and its parts' classes and bases). An
    actor turn's classification appears in that turn's prompt-log capture.
13. **The capture's accept needs keys.** `detail()` gains each post slice's
    `{key, part}` (from the re-matched messages), and the accept button
    fetches `ledgerKnowledgeScene` for the fingerprints the route requires
    (`expect`), as the Knowledge ledger form does.
14. **The eval corpus path.** `evals/cases/epistemic/` would shadow
    `evals/cases.py`; the suite goes in `evals/epistemic/`, as 09's went in
    `evals/history/`.
15. **The golden.** `fixtures/lore_golden.json` predates 09 and already pins
    `history=None` for the narrator, director, opener and actor scenarios. It
    is the byte-identity golden; a new test adds depth on with no view.

## 2. Phases (one commit each, tests with each)

### Phase A — retrieval per perspective (`store/history`, `routes/history_recall.py`)

- `model.Query.perspective` accepts `"narrator"` and
  `"actor:characters:<id>"` (`retrieval.validate`); anything else still
  raises (09's `"actor"` and `"Mara"` tests stay green).
- `query.prepare(cid, sid, *, seed, actor_ref)` builds the actor query (§1.2–3).
- `query.subjects` returns the given seeds for an actor perspective.
- `history_recall.gather(..., actor_ref=None)` → `gather_view(...)`
  returning an `EpistemicView`: retrieve with the step's perspective, then
  `classify` (narrator: roster = present). On any failure, the narrator gets
  a pass-through view (09's evidence, unannotated) and an actor gets None.
  `gather_sync` likewise.
- Tests: the actor query never carries a thread or commitment seed or text
  outside the observed window; a perspective-mismatched retrieval refuses;
  narrator retrieval is unchanged (09's suite).

### Phase B — compose (`store/context`)

- `compose_turn` / `compose_director_turn` take `history_view` and no
  `history=`; `_assemble(recalled_history=...)` becomes `history_view`.
- The mismatch refusal: `ValueError` when the view's cid, sid, identity or
  actor differs from the compose's.
- Data keys: narrator `history_evidence` (attributed and unattributed items,
  with annotation lines) and `history_withheld`; actor `history_known`,
  `history_suspected` (from `visible()` only). All four on the actor blanking
  list, and the actor keys re-set from `visible()` after it.
- Templates: `history_item.j2` gains optional `Known to:` / `Suspected by:`
  lines (names, in present order); new `history_narrator_only.j2`
  (`secret_block`'s instruction), `history_actor_known.j2` (heading, the
  perception sentence, "You were there" / "You know this" slice labels),
  `history_actor_suspected.j2`. `scripts/verify_templates.py` and
  `templates/README.md` updated.
- Catalog: three `Section`s after `history_recall`; `layout.COUPLED`; the
  `HISTORY_ASIDE` tier; per-item shed for all four.
- Inspector: `_history_row` whitelists the view's `detail()`.
- 09's tests that pin "no history for NPCs" are rewritten to pin the new
  contract (an NPC gets only its visible slices).
- Tests (`test_epistemic_prompt.py`): an actor compose with a withheld item
  holds neither its needle nor its title in any message; a narrator view to
  an actor compose raises, and so does a view for another scene;
  `test_epistemic_basis_never_reaches_the_prompt` (bases and override notes);
  depth on with no view is byte-identical to depth off; turning "Recalled
  history" off removes all four sections; each section sheds per item and
  the drop order holds; an item is in `history_recall` or
  `history_narrator_only`, never both.

### Phase C — the round driver (`routes/character_turns.py`, `routes/scenes.py`)

- `_round_history` gathers for every step's perspective, cached by
  `(round_id, actor)`, with 09's skips per step. The legacy doors pass
  narrator views.
- Tests: an NPC step gathers once with its own perspective and its prompt
  carries only its visible slices; a narrator step in the same round gathers
  separately; a retry or reroll replays the snapshot with no retrieval; a
  pending incomplete response skips; off reads nothing past the config.

### Phase D — the offline leakage suite (`evals/epistemic/`)

- `synth.py` recipes (placeholder names; the classifier tests' helpers move
  here and `test_epistemic_classify.py` imports them), `cases.py` with cases
  1–6 and 8–14 of 8.1, `graders.py` (every forbidden needle absent from every
  actor message, every allowed needle present), `runner.py`;
  `backend/tests/test_epistemic_evals.py` runs it in `pytest backend`.

### Phase E — the capture view's accept (frontend)

- `detail()` carries `{key, part}` per post slice.
- In `SceneInspector`, a frozen capture's history rows show each classified
  part with "Mark as known / suspected / unaware to {name}", posting 11-S4's
  route with `source: "accepted"` and the fingerprints from
  `ledgerKnowledgeScene`; a keyless part shows `unkeyed` and offers only
  `unaware` (stored whole-scene, as 11-S4 does). Shown only on a frozen
  capture, never the live panel.
- Tests on the list/detail-free inspector component: the button posts the
  right subject; `unkeyed` is shown; not offered live.

## 3. Repo rules

- Import guard: `store/history` must not import `store/context` (cycle):
  the perspective string check lives in `history/model.py` as a pattern.
- Regex prompt guard: `query.prepare` stays pinned; the observed window comes
  from its own prompt view.
- No lock: retrieval and classification run before `_prepare`;
  `test_history_lock_guard.py` covers the new call sites.
- Settings copy: the depth setting's caption states the per-step cost.
- CLAUDE.md: the "no history in NPC prompts" line becomes the 11-C2 rule.

## 4. Review dispositions

Plan gate: an independent adversarial reviewer (substitute for the Codex CLI
gate). Every finding is taken; where these differ from §§1–3, these win.

| # | Finding | Disposition |
|---|---|---|
| 1 | The capture's accept fingerprints the scene **now**, so it can grant a variant swiped in after the turn | **Fixed.** `detail()` carries each post slice's fingerprint as classified (`key`, `part`, `variant`, `digest`, and `shown`, computed exactly as 11-S4's route computes it, through one shared helper). The accept sends those as `expect`; the route's 409 `moved` then means "changed since this turn", which the button says. The summary is **not** offered in the capture: a whole-scene grant exposes more than the capture shows, and stays the ledger form's |
| 2 | Blanking data keys does not guard the real render path; an actor view can carry `withheld` states | **Fixed.** `_assemble` hands the narrator sections a view only when the compose is not actor-scoped, and the actor sections only `visible()`. Test: an actor whose own `unaware` makes an item `withheld` gets no narrator-only heading, block or `Known to:` line |
| 3 | A partial attendee's header names the scene's last location | **Fixed in 11-S2** (date and location ride a whole-scene grant) |
| 4 | Narrator-only material still steers an actor's ranking (rerank text, all-pairs relationships, thread names in the catalog) | **Fixed.** Rerank is skipped for actor perspectives (it would grade withheld summaries); relationship pairs are restricted to pairs containing the actor; `thread:` and `commitment:` names are dropped from the actor's catalog. Test: changing a withheld summary or two other actors' relationship history leaves the actor's candidates unchanged |
| 5 | `query.prepare` cannot import `actor.observed_history` (cycle), and filtering before windowing misaligns intervals | **Fixed.** The interval rule moves to `appearances.paths.observed_indices(cid, sid, ref, n)`; `actor.observed_history` calls it, and so does `query.prepare`, over the full prompt-view list before excluded posts and notes are filtered. Test: a hidden post before the interval start |
| 6 | A proportional cap split shrinks each item's share | **Fixed.** Each section keeps the evidence's depth (09's per-item share) and its cap is `min(cap, share × its item count)`, so no item gets less than 09 gave it and the total never exceeds 09's |
| 7 | The inspector would show only `known`/`narrator`; withheld actor parts have no row | **Fixed.** `Classified.by_ref` carries each roster ref's per-part classes (posts and summary, with bases and fingerprints); `detail()` reports it, the narrator inspector and capture show it per present NPC, and an actor compose with only withheld parts keeps a held-back row for its sections |
| 8 | Removing `history=` breaks unlisted callers | **Taken.** `epistemic.narrator_view(cid, sid, evidence, present)` is a public pass-through (no re-match) for hand-built evidence. Every caller is converted: 09's eval runner, `verify_templates.py`, the 09 route and context tests, `context_breakdown`, `history_section.preview`; 09's narrator assertions stay as they are |
| 9 | The narrator's byte-identity with 09 is not pinned; where annotations go is unsaid | **Fixed.** An unsplit narrator slice reuses 09's own `Excerpt.text`; annotations are lines directly after the header line (inside the header region `_fit_block` keeps whole, before the summary). Test: an `unattributed` item renders byte-identical to 09 |
| 10 | An actor header with no title renders `****` | **Fixed.** `history_item.j2` (and the actor item template) omit the bold title when it is empty, and an empty header is omitted |
| 11 | `layout.COUPLED` can disagree with saved layouts | **Fixed.** Layout migration strips stored entries for coupled ids, so `merge`, `describe`, `apply` and `enabled_ids` share one rule |
| 12 | A mismatch refusal could fail a turn after a classify failure | **Fixed.** Every view on the turn path is built from the run's scene identity, so a pass-through never mismatches; the refusal stays for real programming errors |
| 13 | Returning the actor's seeds unchanged drops `cat.allowed` | **Fixed.** The filter stays |
| 14 | The lock guard matches names; classify must not run on the loop | **Fixed.** The new gather names join `test_history_lock_guard`; classification runs through `history_threads.run` |
| 15 | The frontend tier union and budget bar lack the new tier | **Fixed** in Phase B: `api/types.ts` and `ContextBudgetBar.BUCKET_OF` |
| 16 | Narrator-only summaries can repeat the archive | **Fixed.** Omissions are computed whenever the narrator view has items, for both narrator sections |
| 17 | Spec drift and test gaps | **Taken.** Amendments: classify after every retrieval round (vacuous until 10); the per-section render-failure policy for the new sections; rerank skipped for actors. The offline suite asserts narrator-side placement too (cases 6, 10, 11). Tests: `Known to:` never in an actor prompt; the actor query unaffected by narrator-only material. CLAUDE.md gains a rule (there is no old line to rewrite). Open question 7 (per-round embed count) stays open: 09's history row already reports its own retrieval, and per-step cost is stated on the setting |

## 5. Found while implementing

| Phase | Finding | Disposition |
|---|---|---|
| A | Finding 4's test also asked that "changing a withheld summary leaves the actor's candidates unchanged". Lexical and semantic scoring read 08's search documents, and a document carries its scene's summary, so an actor's query (words it heard) can match a summary's words and admit or order a scene. Keeping it out would need a second document set and a second vector cache per actor. | **Accepted as a residual, recorded in the spec.** A summary can steer which scenes an actor's retrieval looks at, never what reaches its prompt: classification still gates every slice, and a summary reaches an actor only on a whole-scene grant (S1). The rerank (which reads rendered summaries as text to grade) is skipped for actors, and the test pins that instead. Two other actors' relationship history is tested as specified |
| A | `observed_indices` needs each post's role, for the legacy rule (no recorded presence reads from the last player post), so it takes the transcript rather than a count | **Taken.** `appearances.paths.observed_indices(cid, sid, ref, history)` |

## 6. Code review dispositions

Code gate: an independent adversarial reviewer over `ef71e69..HEAD`
(substitute for the Codex CLI gate). It found no leakage path into an NPC
prompt; every finding is taken except where noted.

| # | Finding | Disposition |
|---|---|---|
| 1 | Reserving the second section's share first starved `history_recall` at the 80-token floor (budget 200, depth 3: the top recalled item dropped while withheld items rendered) | **Fixed.** The first section of a pair fits as 09's did, and the second fits in what it left (`Part.spent`). Test in the floor regime (fails on the old split) |
| 2 | An NPC capture offered grants on parts whose text the capture never showed; a keyless unaware silently covered the whole scene | **Fixed.** On an NPC's turn a part it was not given offers only unaware; a keyless unaware's button says "(whole scene)" |
| 3 | `by_ref` was emitted but never rendered; `Known to:` showed raw refs | **Fixed.** The narrator's rows show each present ref's class per part, and every ref is shown by name |
| 4 | Each item of the second section appeared twice in a capture, with two sets of accept buttons | **Fixed.** The classification rows ride the first section only. The repeated coverage line under both rows stays: it is the same retrieval, said once per section row |
| 5 | The settings copy over-counted queries for a round | **Fixed:** a query per contribution, the narrator's only when it writes |
| 6 | A momentarily unreadable scene header failed the turn through `_check_view` | **Fixed.** The strict identity read; a header that cannot be read costs the compose its history, never the turn |
| 7 | Structural ranking for an actor still reads the chronicle's cast and location | **Accepted as a residual**, recorded beside the summary one in the spec: it ranks or admits, never renders |
| 8 | An unstamped player post read `**You:**` under "You were there:" | **Fixed.** "The player" in an NPC's own sections; the narrator's rendering is 09's |
| 9 | `_actor("narrator")`; `gather`/`gather_sync` have no production caller; a mis-indented keyword | `_actor` and the indent fixed. `gather`/`gather_sync` stay: the evidence-only doors 09's tests and the lock guard name, and what `gather_view` composes |
| 10 | Tests that did not test their claims | **Fixed:** an empty view sends what recall off sends; the aside tier is dropped first under real packing pressure; an empty title leaves no `****`; an identity mismatch on the same scene is refused |

## 7. Final gate dispositions

Final gate: an independent reviewer asking whether `ef71e69..HEAD`
implements the spec. No blocker, no leak.

| # | Finding | Disposition |
|---|---|---|
| 1 | Narrator-only material (summaries in 08's documents, the chronicle's cast and location) still steers which scenes an NPC's retrieval ranks; NPCs are never reranked | **Put to the user.** Recorded as residuals in §6.1's amendment; the alternative is a per-actor document set and vector cache |
| 2 | The override-notes half of `test_epistemic_basis_never_reaches_the_prompt` was missing, and some basis codes unchecked | **Fixed.** Notes planted on every status for every reader, the remaining codes added, the test given the spec's name |
| 3 | The accept was offered on parts the packer shed or dropped | **Fixed.** A grant needs every post of the part in a fitted, unshed item of an undropped section |
| 4 | The summary is not offered for accept, recorded only in the plan | **Amended in the spec**, with the reason |
| 5 | Case 13 lacked the unaware edit leg and a positive control | **Fixed:** both legs for the unaware, and unchanged grants of both shapes as controls |
| 6 | The evals path and the golden amendments lived only in the plan | **Amended in the spec** |
| 7 | "Never in both" was one-directional; the same-actor other-scene refusal untested | **Fixed** |
| 8 | Checklist and spec status text | **Fixed:** Review and Final gate ticked; the spec header says where the slices stand |
| 9 | §14 Q7 (report the per-round embed count in the history row) | **Put to the user**; the cost is stated in the settings copy today |
