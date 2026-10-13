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

(Filled in after the plan gate.)
