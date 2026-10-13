# Roadmap 11-S2: the deterministic classifier — plan

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-11-epistemic-history-retrieval-design.md`
§3 (vocabulary), §4 (the deterministic stage), §9 11-C1, §11 "Deterministic
classifier". **Slice:** 11-S2. **Needs:** 11-S1 (landed in this stack), 09-S1
(on `spec-09-hybrid-history`, built on here), 08-S4 (S, on main).

**Goal:** `store/context/epistemic.py`, a pure, read-only library that turns
09's `EvidenceItem`s into per-actor access classes. Nothing calls it on the
turn path yet (11-S3 does), so no prompt changes and no golden moves.

## 1. What the landed 09 code changes about §4

Each is recorded as a spec amendment in §4 (marked "Amended by 11-S2").

1. **`EvidencePost.part` is `int | None` and is `None` for every real
   continuation.** 08's `_part` casts `response_part` with `int()`, and real
   parts are roll-proposal ids (`pr-<hex>`). The `(key, part)` that R1 hands
   `knowledge.decide` is therefore taken from the **re-matched message**
   (`knowledge.post_key(m)`, `knowledge.post_part(m)`), never from the
   `EvidencePost`.
2. **There is no raw-text digest on an `EvidencePost`, and its `text` may be
   clipped.** 08 clips each post to `MESSAGE_BYTES` with a leading and/or
   trailing `…`, and 09's `_trim` can clip the last post again. Re-matching
   therefore compares the post's text against the prompt view **recomputed
   from this read** (`regex_view.view(messages, cid=source, phase="prompt")`
   over the whole transcript, then `export.drop_images` and the lone-surrogate
   replacement 08 applies): the evidence text with its `…` ends removed must
   be a substring of the recomputed text (equal when nothing was clipped).
   The key must be equal too, and a keyless post must still be keyless.
3. **A renamed scene keeps its evidence.** 08 follows a scene identity to its
   current sid, and 09 keeps the old sid on the `SceneRef`. The classifier
   resolves `item.scene.identity` to the scene's current sid: first by the
   sid it was given (`scene_identity(source, sid) == identity`), then
   `find_by_identity`. An identity that resolves to nothing drops the item for
   everyone (the scene was deleted). `UnreadableError` from the scan is the
   "unreadable source scene" row.
4. **The failure row for `knowledge.json` needs `read_checked`.** The tolerant
   `knowledge.read` skips malformed entries silently. `classify` uses
   `knowledge.read_checked`, and `intact=False` (or an exception) is the
   unreadable row.
5. **Cross-campaign evidence.** 09's tier 3 can return scenes from another
   campaign (`SceneRef.campaign`). It is never used on a turn (`_validate_scope`
   refuses tier 3 there). The classifier does not classify across campaigns:
   an item whose `scene.campaign` is set and differs from `perspective.cid`
   is `unknown` (basis `unverifiable`) for every actor and rendered for the
   narrator as 09 renders it. A ref in another campaign proves nothing about
   the same-named actor here (`retrieval._shared_ref` is the proof, and it is
   not the classifier's to borrow).
6. **The keyless-post rule.** §4's needs line says a keyless post in a scene
   with any post-listed override is withheld. `knowledge.decide` already lets
   a keyless post reach whole-scene entries only; the withholding is the
   classifier's. It runs after R1 and before R2: a keyless post that no
   whole-scene entry settles, in a scene that has at least one post-listed
   entry (any audience, any status), is `unknown` with a new basis,
   `unkeyed` (added to the closed set in §3.5 — the term 11-S4 already uses).
7. **Narrator slices.** §4.6 says `slices` are "for the perspective's actor".
   For the narrator perspective a slice carries access `known` and basis
   `("narrator",)` (added to §3.5): every admissible post, in 09's excerpt
   boundaries. A dropped post (`moved`, hidden since retrieval) is a slice
   with access `unknown` and an empty text, for every perspective, so the
   inspector can show it and no renderer can.

## 2. API (`store/context/epistemic.py`)

```python
ACCESS = ("witnessed", "known", "suspected", "narrator_only", "unknown")
VISIBLE = ("witnessed", "known", "suspected")
BASES = frozenset({...})   # §3.5's closed set, plus "unkeyed" and "narrator"
STATES = ("attributed", "withheld", "unattributed")

@dataclass(frozen=True)
class Perspective:
    cid: str; sid: str; identity: str | None; actor: str | None
    present: tuple[str, ...]
    def to_string(self) -> str
    @staticmethod
    def of_string(s, *, cid, sid, identity, present) -> Perspective  # ValueError on anything else
    @staticmethod
    def of(cid, sid, actor_ref, present, *, identity=None) -> Perspective  # None/"grimoire" -> narrator

@dataclass(frozen=True) class Slice: part, posts, access, basis, text
@dataclass(frozen=True) class Classified: item, slices, header, state, known_to, suspected_by
@dataclass(frozen=True) class EpistemicView:
    perspective; items; decided: int = 0; pending: tuple = ()
    def visible(self) -> tuple[Classified, ...]
    def detail(self) -> dict          # JSON form for the inspector/capture (S3 wires it)

def classify(perspective, items, *, roster=()) -> EpistemicView
```

- `of_string` accepts exactly `"narrator"` and `"actor:characters:<id>"`.
- `roster` defaults to `(perspective.actor,)` for an actor and to
  `perspective.present` for the narrator.
- `known_to` / `suspected_by` are refs, in `present` order. 11-S3 names them
  (and checks 07-C3c's `prompt_visible` for group audiences).
- `decided` stays 0 and `pending` stays `()` in this slice: they belong to
  the Decision stage (11-S6), which computes the channel test. The fields
  exist so the shape is final.

## 3. The algorithm

`classify` groups items by source campaign (always `perspective.cid` on a
turn) and, per source, takes one `locks.best_effort_campaign_lock(source)`
hold, inside which it reads once each:

| Read | Function | Failure |
|---|---|---|
| presence | `appearances.paths.record(source)` | no R2–R5 for anyone |
| overrides | `knowledge.read_checked(source)` | actors get nothing a derived rule would show (every part `unknown`, `unverifiable`) |
| audiences | `knowledge_scene.audiences_for(source)` | `Audiences({}, {})`: actor and group grants fail closed; `*` grants and every `unaware` still match |
| chronicle | `chronicle.read_chronicle(source)` | no chronicle `cast` for R5; whole-scene grants cannot hold (the scene digest needs the record) |
| cast names | `appearances.cast.scene_cast(perspective.cid, perspective.sid, include_departed=True)` | R2' cannot fire (PCs never `witnessed`) |
| per scene | `scenes.read.read_scene`, `responses.actor_refs`, `responses.variants_by_response` | scene: dropped for actors, 09's rendering for the narrator; `actor_refs`: no R2; variants: grants on `r-` posts fail closed (variant unknown) |

Per item:

1. **Admit.** Cross-campaign (§1.5) and `identity is None` items are
   `unverifiable` for actors and pass through unchanged for the narrator.
   Resolve the identity to the current sid (§1.3) and read the scene once.
2. **Re-match** every `EvidencePost` (§1.2): index in range, same key, text
   contained in the recomputed prompt view, and the message still in context
   (`not is_excluded`, `not is_director_note`). A failure is a `moved` slice
   for everyone.
3. **Facts** for `knowledge.decide`: `knowledge_scene.scene_facts(messages,
   variants, title=meta title, record=chronicle record)`.
4. **Classify** each re-matched post for each roster ref, R1 → R6 (§4.2),
   with R2' (a post whose speaker matches a present PC's name by
   `serialize.match_name`) for PC refs only, and the keyless rule (§1.6)
   between R1 and R2. R1 maps `knows → known`, `suspects → suspected`,
   `unaware → narrator_only`, basis `Match.basis`. `author` for `*` is
   `actor_refs.get(response_id)`.
5. **Summary and title (S1):** `knowledge.decide(..., post=None)`; a grant is
   the only way an actor sees them. Otherwise `unknown`,
   `summary_needs_grant`. An `unaware` whole-scene entry makes them
   `narrator_only`.
6. **Slices:** each excerpt is split into contiguous runs of one access; a
   run's basis is its posts' bases, sorted and de-duplicated; its text is
   `prompts.render("snippets/transcript.j2", ...)` over the run's
   `EvidencePost`s, the body 09's `budget._excerpt` uses (a test holds them
   byte-equal for a whole excerpt). The summary is one slice, `part="summary"`.
7. **Header:** the narrator gets title, date and location. An actor gets date
   and location when any post slice is visible, and the title when the
   whole-scene access is visible.
8. **Aggregate (narrator):** over `perspective.present`. `attributed` if any
   present ref has a visible part; else `withheld` if any present actor has a
   part `narrator_only` by an `unaware` override; else `unattributed`.
   `known_to` holds refs with a `witnessed` or `known` part, and `suspected_by`
   holds refs with a `suspected` part that are not in `known_to`.

`visible()` returns, for an actor, each item with at least one `VISIBLE`
slice, carrying only those slices; for the narrator, every item with its
`moved` slices removed.

## 4. Guards and repo rules

- **Regex prompt guard:** pin the function that reads the scene and computes
  the view (`_scene_view`) in `PINNED`. It calls `regex_view.view(...,
  phase="prompt")` itself.
- **Import guard:** module-scope imports that bind submodules only
  (`from ..appearances import cast as appearances_cast, paths as appearances_paths`,
  `from ..scenes import identity as scenes_identity, read as scenes_read, serialize as scenes_serialize`,
  `from ..regex import view as regex_view`, `from ..history import model`,
  `from .. import chronicle, export, knowledge, knowledge_scene, locks, responses`,
  `from ... import prompts`).
- **Lock domain:** read-only, so declared in no list.
- **Store API baseline:** `store/context` is a package; adding a submodule
  changes no top-level name. `context/__init__.py` lists submodules, so it
  gains `epistemic`.
- **No model, no write, no `campaign_lock`.** A test patches
  `locks.campaign_lock` to raise and runs `classify`.

## 5. Tests (`backend/tests/test_epistemic_classify.py`)

Fixtures build real stores: `history_fixtures.actor`/`played`/`absorbed`,
presence through `appearances.transitions.appear`/`leave`, replies with
`response_id` through the responses ledger, and overrides through
`knowledge.add` with fingerprints from `knowledge_scene.current`.
`EvidenceItem`s are built from the transcript the way 09's adapter does
(`searchdocs.expand.expand(..., phase="prompt")` mapped through
`history.expand.excerpts`), so the texts are 08's real prompt views.

1. One case per rule: R1 (`knows`, `suspects`, `unaware`; actor, group, `*`),
   R2 (authored, with presence missing), R3, R4, R5 (scene list and chronicle
   cast), R6, and R2' for a PC.
2. A split excerpt: the actor arrives mid-excerpt; two slices, and the actor's
   visible text holds only the covered posts.
3. S1: a whole-scene attendee's summary and title are withheld; a whole-scene
   `knows` with a holding digest shows them; after an edit they are withheld
   again; an `unaware` makes them `narrator_only`.
4. `visible()`: an item with no visible slice is absent for the actor, header
   included; for the narrator every item stays.
5. The narrator aggregate: `attributed` (with `known_to` / `suspected_by`),
   `withheld`, `unattributed`, and a legacy scene with no intervals rendering
   unannotated.
6. Re-matching: a post whose key moved, a post whose text changed, a post
   hidden since retrieval and a cut (`responses.delete`) between retrieval
   and classification — each is `moved`, and nothing becomes newly
   `witnessed`. A clipped post (long text) still matches. A renamed scene
   still matches.
7. A scene with no identity is `unverifiable` for actors and passes through
   for the narrator; a deleted scene drops the item; a cross-campaign item is
   `unverifiable`.
8. The keyless rule: a keyless post in a scene with a post-listed override is
   `unkeyed`; without one it follows R3.
9. Each failure row: unreadable `appearances.json`, `responses.json`,
   `knowledge.json` (bad JSON and a malformed entry), the chronicle, and a
   source scene.
10. Purity: no `campaign_lock`, no write (the store tree's mtimes are
    unchanged), and `Perspective` string round-trips and refusals.
11. The renderer equals 09's `budget._excerpt` for a whole excerpt.

## 6. Files

- `backend/src/grimoire/store/context/epistemic.py` (new)
- `backend/src/grimoire/store/context/__init__.py` (list the submodule)
- `backend/tests/test_epistemic_classify.py` (new)
- `backend/tests/test_regex_prompt_guard.py` (pin)
- spec §3.5 and §4 amendments; `ROADMAP-CHECKLIST.md` row for 11-S2; this plan.

## 7. Review dispositions

Plan gate: an independent adversarial reviewer (substitute for the Codex CLI
gate). Every finding is taken; the implementation follows these, where they
differ from §§1–6 above.

| # | Finding | Disposition |
|---|---|---|
| 1 | A group `unaware` whose group cannot be resolved (audiences unreadable, or the group absent from `membership.campaign_index`) stops matching, so its post shows | **Fixed.** Unreadable audiences are the unreadable-overrides row (actors get nothing). A group `unaware` naming an unresolvable group withholds what it covers from every actor (`unknown`, `override_group`), unless a more specific entry beats it by 7.2 (a post-level actor entry; any post entry over a whole-scene one) |
| 2a | A whole-scene grant shows the summary and title past a post-level `unaware` on the very post they paraphrase | **Fixed (amendment to S1).** The summary and title need the whole-scene grant **and** no post-listed `unaware` in that scene reaching the actor (an unresolvable group reaches everyone, `*` reaches everyone) |
| 2b/c | A rolling summary, or a summary or title changed since retrieval, can ride a grant that never covered it | **Fixed.** A grant shows `item.summary` only when it equals the chronicle record's `summary` from this read, and the title only when `item.title` equals the scene's current title |
| 3 | An unlocked read (best-effort hold not acquired) can pair a remapped `appearances.json` with the old transcript | **Fixed.** With `held` False the interval rules R3/R4 are off (posts fall to R5/R6); a row in the failure table, and a test writing only the remapped record |
| 4 | An unreadable chronicle lets a pre-absorb whole-scene grant hold | **Fixed.** A `chronicle_ok` flag: when false the scene digest is a sentinel no grant can equal, so whole-scene grants fail and whole-scene `unaware` and post grants still apply |
| 5 | Patching `campaign_lock` to raise breaks `best_effort_campaign_lock` itself | **Fixed.** The purity test spies on `best_effort_campaign_lock` and asserts `campaign_lock` is never entered, and the store's files are byte-identical after |
| 6 | `Classified.item` hands a renderer the unredacted item | **Fixed.** For an actor perspective `item` is replaced by a redacted copy: header fields per §3 step 7, no summary, no excerpts. Slices carry the visible text |
| 7 | No identity re-check after the scene read; `variants_by_response` re-resolves fail-soft | **Fixed.** The identity is re-checked after the scene, authorship and variant reads, and a mismatch is the unreadable-scene row; `variants_by_response` is called with `token=identity` |
| 8 | Malformed presence data crashes or misreads | **Fixed.** A record that is not a dict is the unreadable row; a per-actor interval list is validated as `observed_history` does (int `start >= 0`, `end` int or None); a malformed one is `unknown`, `no_record` |
| 9 | R2' is underspecified | **Fixed.** R2' needs `role == "user"` and a non-empty stored speaker, matched with `serialize.match_name` against every present name, resolving to that PC's name |
| 10 | Keyless re-matching is loose | **Fixed.** Re-matching also requires the evidence speaker to equal the speaker 08 renders for the message now (`searchdocs.expand._speaker`, with the scene's `player_label`) |
| 11 | The regex pin moved from "the slice renderer" to the scene reader | **Taken, amended in §10.** The renderer reads only 09's texts and cannot satisfy `_reaches_view`; the pinned function is the one that reads the scene and views it |
| 12 | Smaller points | **Taken.** Summary slice only when `item.summary and not item.summary_omission`; cross-campaign items take no reads and no lock; `context/__init__` listing is optional (done for discoverability); `author=None` for the summary and title; the digest's title is the scene's meta title |
| — | Test gaps | **Taken.** Added: audiences failure and unresolvable group; `*` `unaware` skipping the author; a post `unaware` on the actor's own reply; a keyless post settled by a whole-scene entry; whole-scene `knows` plus post `unaware` (summary and title withheld); a rolling summary under a grant; the lock-skew row; the chronicle digest row; appended text still matching and replaced text `moved`; a post trimmed by 09's `_trim`; a legacy scene with no PC post; malformed intervals |

The slice renderer is 09's own `budget._excerpt` (ruff does not select the
private-access rule), so the byte-equality is by construction rather than by
test; the test stays as a tripwire.

### Code review (substitute for the Codex review and final gates)

| # | Finding | Disposition |
|---|---|---|
| 1 | `classify` could raise: the regex view and `scene_facts` were unguarded (a bad byte in `campaign.md` reached `layers.effective`) | **Fixed.** Every read after the scene read sits in one guarded block (the unreadable-scene row), and each item is classified under its own guard that falls back to `_unclassified` (nothing for actors, 09's rendering for the narrator). Tested with the view raising and with `_classify_item` raising |
| 2 | Mutation testing: eleven guards survived their removal | **Fixed with tests:** leaving ends presence; a moved key or speaker; a director note; a whole-scene `unaware` for an unresolvable group; an unresolvable group's post `unaware` withholding the summary; a resolvable group grant through real membership; R2' needing a stamped user post; `known_to`/`suspected_by` exclusive; the identity re-check; R2 off with presence unreadable (on Mara, who authored); the header. The cut test now asserts the moved post precisely; the rename test classifies Winifred, so presence must follow the rename |
| 3 | §4.3's title paragraph contradicted the code | **Amended**: the title rides the whole-scene grant itself |
| 4 | The redacted item kept the sid (the slugged title) and every evidence id | **Fixed**: the redacted `SceneRef` has no sid (the identity remains its key) and no evidence ids |
| 5 | The group-withholding wording overclaimed 7.2 | **Fixed**: stated as stricter than 7.2, recency not consulted |
| 6 | A scene deleted between the read and the re-check reached the narrator | **Fixed**: a failed re-check re-resolves the identity, and nothing found is `_GONE` |
| 7 | Date and location rode any visible post | **Changed, with the 11-S3 plan review's finding 3**: 09's date is the scene's first moment and its location the scene's last, so a partial attendee is not told either. Both ride the whole-scene grant, like the title. Amended in §4.3 |
| 8 | The docstring's "each file once" was untrue | **Fixed**: it says which helpers re-read `appearances.json`, and that a stale read only narrows |
| 9 | Narrator-annotation edge cases; `Perspective.of` accepted anything | `Perspective.of` now refuses a non-`characters:` actor. The annotation cases are as specified (an unresolvable group's withholding is `unknown`, so the item reads `unattributed`; the narrator still sees everything) |
