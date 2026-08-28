# Reroll steering feeds the absorb — closing the correction loop

## Status

Approved design (2026-08-28). Names in this document are placeholders from the
codebase's own set (Saltmarch, Mara, Seraphine, Winifred). Every example is
invented. Nothing here is measured from a real library.

## Problem

When the model gets a lore item wrong mid-scene — or reaches for lore the
player intended but never wrote down — the player's fix is the reroll popover:
type a steering prompt ("the east gate is barred at dusk, not dawn", "Mara
already knows about the ledger") and regenerate. That text is the single
highest-signal artifact the app ever sees about the state of the lore: every
steering prompt is a place where the written record and the player's intent
disagreed hard enough to interrupt play.

And the app throws it away. Today reroll guidance has exactly two consumers,
both transient:

- It is appended once as a trailing system message for the one call it steers
  (`scene/regenerate_guidance.j2`, composed in `routes/scenes.py`'s
  `_regenerate_run`) and never re-sent.
- It is parked in the alternates sidecar (`<sid>.alts.json`,
  `store/alternates.py`) as a **display label** on the variant it produced —
  clipped to 500 chars, surfaced only in the swipe tooltip, capped at 8
  variants, and dropped entirely the moment the next player turn moves the
  set's anchor. The alternates store's own docstring calls the hint
  "display-only", and it is right.

Meanwhile the end-of-scene absorb — the one pass whose whole job is evolving
the lore — is built from the chronicle transcript plus deterministic snapshots
of stored state (`store/absorb/prompt.py`, `snapshots.py`). It sees the
*corrected* replies, but never the corrections; it cannot distinguish a scene
where the lore held from a scene where the player had to fight it. The loop
the feature request names — "close the loop for self improvement" — does not
exist.

## What this builds

Two halves, deliberately small:

1. **A durable per-scene steering log.** Every non-empty guidance string a
   regenerate carries is appended to a third per-scene sidecar,
   `<sid>.steering.json`, at the same moment (and under the same campaign-lock
   hold) that `alternates.archive` parks it today. The log survives anchor
   moves, alternates retention, cuts, and retcons — it lives for the scene's
   lifetime, like the pending review.

2. **A new absorb prompt input.** The absorb prompt gains a "player steering
   notes" block — the scene's logged guidance, in order — plus a system-prompt
   instruction telling the model what the notes are and what they are not:
   *signals* that lore was wrong or missing, to be answered through the
   existing `lore_edits` / `new_lore` / `facts` sections, and never *evidence*
   that may be cited.

No new output-contract keys. No new UI. The capture surface (the reroll
popover) and the review surface (the absorb drawer) both already exist; this
spec only connects them.

## Decisions, and the reasons

### Storage: a third sidecar, not a transcript line and not a new key in `.alts.json`

The director-note precedent (`DIRECTOR_SPEAKER`, `store/scenes/serialize.py`)
argues for a synthetic transcript line; it was rejected here, with the owner's
approval, for three reasons:

- A director note earned its transcript seat for **cost attribution**: it was
  the one generation whose ledger row had no index to sit against. Reroll
  guidance already has attribution — a reroll's cost buckets with the post it
  answers — so the transcript solves nothing steering actually needs.
- A transcript line demands an exclusion everywhere a synthetic speaker is
  special-cased (history projection, exports, reply counting, drift
  measurement, the alternates *anchor*, which counts messages in front of a
  generation and would have to learn to step over steering lines the way it
  steps over transitions). That is a wide blast radius for a record only the
  absorb reads.
- It changes the transcript format the frozen campaign holds frozen.

Extending `.alts.json` with a scene-lifetime list was also rejected: the
alternates file's whole contract is *lifecycle-bound to the trailing
generation* — anchor moves drop the set, retention trims it — and a key inside
it that pointedly ignores both rules is a second store wearing the first one's
filename. `paths.py` already states the classification this design lands in:
a per-scene JSON sidecar "a deleted scene must take with it", keyed by
filename. The review sidecar is the second; this is the third.

### Capture at reroll time, unconditionally — not at landing time

The steering log is appended where `alternates.archive` is called: inside
`_regenerate_run`'s single lock hold, before the stream starts, whether or not
the reroll's stream ever lands. Alternates needed the pending/spend/disown
machinery because a hint must be credited to the *variant it produced* and a
dead stream produces none. Steering has no such binding: "the player had to
correct this" is true the moment they typed it, and stays true if the network
then ate the run. Logging at landing time would inherit all of the disown
lifecycle for no gain.

The cost of unconditional capture is duplication — the frontend's
error-banner Retry re-sends the same regenerate with the same guidance — and
it is paid for with one rule: **an append identical to the log's most recent
entry is a no-op.** Consecutive-dedupe also collapses a player hammering
reroll with the same instruction, which is the same signal once, not five
times.

### All of the scene's guidance reaches the absorb, not just survivors'

Owner's explicit choice. Guidance whose variant was later superseded or
promoted away is still a lore signal — repeatedly correcting the same item is
the *strongest* evidence of a gap, and it is exactly the guidance that never
survives as a live variant.

### Signal, never evidence

The absorb deliberately drops director notes from its transcript
(`chronicle.transcript_text`), because "absorbed as though it were dialogue it
would put the author's own instructions into the chronicle, and cited as
evidence for a proposal it would be the reviewer's own words coming back as a
finding." Steering notes are the same words in a different box, and the same
principle holds — so they enter the prompt as a **separate context block**,
never inside the transcript, and the system prompt instructs:

- Steering notes tell you where the played lore was wrong or missing. Check
  whether an existing record should be sharpened (`lore_edits`), a missing one
  written (`new_lore`, `new_locations`), or a standing truth recorded
  (`facts`).
- A note may steer tone or length rather than lore ("shorter", "more
  dialogue"); those are not lore signals and get no edit.
- The citation contract is unchanged: `quote`/`speaker` must come from the
  transcript. A steering note is not a speaker and may not be quoted. An edit
  the transcript itself cannot support is left uncited — which routes it to
  the review's low-confidence/uncited drawers, where the player who typed the
  steering in the first place adjudicates it. The player's own words never
  come back as a citation.

This is what keeps the loop honest: steering raises the *recall* of the lore
pass, and the existing routing bands keep its *precision* — nothing lands
without either transcript support or the player's explicit approval in the
review.

## Design

### `store/steering.py` (new)

The scene's reroll-steering log. Third per-scene sidecar,
`<scenes dir>/<sid>.steering.json`:

```json
{"v": 1,
 "entries": [{"text": "Mara already knows about the ledger",
              "created": "2026-08-28T19:04:11Z"},
             ...]}
```

- `record(cid, sid, text)` — strip; no-op on empty; clip to
  `MAX_STEERING_CHARS = 500` (the same bound, for the same wire-input reason,
  as `alternates.MAX_GUIDANCE_CHARS`); no-op when identical to the newest
  entry; append `{"text", "created": now}`; trim oldest past
  `STEERING_LIMIT = 100`. Takes `locks.campaign_lock(cid)` (reentrant, so the
  route's hold is free), writes through `store.atomic`.
  **Failsoft on `OSError`**: a steering row is an absorb hint, and losing one
  must never fail the reroll that carries it. The concrete case is a
  pre-id-cap store whose `<sid>.md` fits the filesystem and whose
  `.steering.json` does not (ENAMETOOLONG) — the same tolerance
  `lifecycle._unlink_sidecar` documents. A garbled existing file is replaced
  with a fresh record rather than raised on: whatever corrupted it already
  lost its entries, and refusing to log new ones on top serves nobody.
- `texts(cid, sid) -> list[str]` — entry texts, oldest first; `[]` on a
  missing or garbled file (the absorb-snapshot tolerance rule).
- `repoint_scenes(cid, mapping)` — move the file on rename, exactly as the
  alternates and review sidecars move.
- `clear_destinations(cid, sids)` — the orphan sweep `repad` and the legacy
  migration run before any transcript moves, exactly as the other two
  sidecars have. (An earlier draft also specified a `drop_scene`; it was
  dropped at implementation because nothing would call it —
  `scenes.lifecycle.delete_scene` unlinks all three sidecars directly, and
  the fan-out reaches this store only through `repoint_scenes`.)

Entries carry no transcript index or anchor on purpose. The absorb consumes
text and order; an index would renumber under cuts (the ledger's own
"breakdown, not truth" caveat) and an anchor would drag in the alternates'
slot mathematics — both machinery for a consumer that does not exist. If a
review UI later wants "which post this steered", the field can be added to
new entries without a migration; readers treat entry keys as open.

The log is never cleared — not by a chronicle save, so a `force` re-absorb is
primed with the same notes the first absorb saw, which is right: a re-absorb
exists to redo the extraction, not to forget its inputs. Only scene deletion
removes it.

`STEERING_LIMIT` is a backstop against a pathological writer, not a packing
policy — the fact-snapshot cap's reasoning, one store over. It is justified
structurally (500-char entries × 100 = bounded file and prompt block) and
should be tuned against real prompts later, not measured against any live
store now.

### Integration surface (the third-sidecar checklist)

Every line below exists because the second sidecar needed it:

- `store/scenes/paths.py`: `_steering_path`, and `_sid_taken` learns the third
  name — an orphaned steering log adopted by a recycled id would feed a fresh
  scene's absorb another scene's corrections.
- `store/scenes/lifecycle.py` `delete_scene`: unlink the steering sidecar
  with the other two, before the transcript, same crash-ordering argument.
- `store/scene_ids.py`: `_LONGEST_SUFFIX` now measures `.steering.json`
  (14 chars), so new ids leave room for it.
- `store/scene_refs.py`: `steering` joins the repoint fan-out (the
  moved-not-rewritten group), and the module docstring's census grows by one.
- `store/locks.py`: `store.steering` joins `DOMAIN_MODULES`.
- `store/pending_reviews.name_usable` gets **no** steering analog: the review
  gate exists because losing a review loses the longest generation in the
  app; a steering write that cannot land is failsoft by design.

### Capture (`routes/scenes.py`, `_regenerate_run`)

Immediately after `store.alternates.archive(cid, sid, guidance, ran_on)`,
inside the same lock hold:

```python
if guidance:
    store.steering.record(cid, sid, guidance)
```

Retry (`post_retry`) carries no guidance field and is untouched. The empty
send, director turns, and replay's guidance-less regenerate all pass empty
guidance and log nothing.

### Absorb prompt

- `store/absorb/snapshots.py`: `steering_snapshot(cid, sid) -> str` — the
  logged texts rendered one per line as `- <text>`, oldest first, `""` when
  the log is empty or garbled (the module's standing tolerance rule).
- `store/absorb/prompt.py` `build_prompt(...)`: new trailing keyword
  `steering_snapshot: str | None = None`.
- `templates/absorb/user.j2`: a new head block, after "Standing facts:" —

  ```
  Player steering notes (rerolls the player requested while playing, in order):
  - <text>
  ```

- `templates/absorb/system.j2`: a new paragraph carrying the signal-never-
  evidence contract from the Decisions section above, phrased in the
  template's own register.
- `routes/scenes.py` `_absorb_start`: pass
  `store.absorb.steering_snapshot(cid, sid)` alongside the other snapshot
  reads.
- `store/absorb/__init__.py`: re-export `steering_snapshot` with its
  siblings.

`parse.py`, `materializer.py`, `routing.py`, `apply.py` are untouched: the
answers arrive through existing sections under the existing citation and
routing rules.

### Guards, evals, docs

- `scripts/verify_templates.py`: the absorb check gains the new variable and a
  head-line assertion ("Player steering notes" present when the snapshot is),
  mirroring the Groups/commitments/facts assertions.
- `evals/cases.py`: `build_absorb` seeds the fixture scene with one or two
  invented steering entries (placeholder content only); `_absorb_prompt`
  passes the new snapshot; `grade_absorb`'s prompt-side check requires the
  steering instruction verbatim in the assembled prompt, which is what makes
  replay mode react to a `system.j2` edit that drops it.
- The frozen campaign's `snapshot.json` is regenerated **iff** its read-only
  sweep renders the absorb prompt (to be confirmed at implementation; the
  sweep's README rule applies — regenerate deliberately, with the template
  change that moved it, and never touch `home/`).
- `templates/README.md`: document the new variable and template block.
- Android/pydantic constraints: pure-Python store module, no models, no new
  dependencies — nothing to do.

### Tests

- `backend/tests/test_steering.py` (new): record/clip/dedupe/cap/empty-no-op;
  garbled-file tolerance; failsoft OSError; drop and repoint lifecycle.
- Extend the existing surfaces that inventory per-scene sidecars: the
  delete-scene and rename tests that today assert `.alts.json` and
  `.review.json` move or vanish learn the third name; `_sid_taken` tests
  likewise.
- Regenerate-route test: a guidance-bearing regenerate leaves a steering
  entry; a guidance-less one leaves none; two identical retries leave one.
- Absorb test: `build_prompt` with a steering snapshot renders the block;
  `_absorb_start`'s prompt contains logged texts (through the route, with the
  LLM faked via `llm_fakes`); an empty log renders no block, byte-identical
  to today's prompt — the director-note precedent's "byte-identical when
  absent" guarantee, kept on purpose so existing cassettes still match.
- Eval offline run (`pytest backend` covers it) green after the fixture and
  grader changes.

## Non-goals

- **No review-UI changes.** Steering-driven proposals surface as ordinary
  rows in the existing drawers; showing the raw steering list in the review
  panel is a later, separate decision.
- **No director notes in the absorb.** Their exclusion is load-bearing and
  this spec reaffirms it rather than weakening it.
- **No retroactive import** of guidance still sitting in `.alts.json` files:
  those hold at most the trailing generation's hints, and a one-shot
  migration for a tooltip's leftovers is not worth its failure modes.
- **No new absorb output keys**, no routing/authority changes, no retry-route
  guidance field.
