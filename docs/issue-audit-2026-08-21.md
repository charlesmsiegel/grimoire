# Issue audit — 21 Aug 2026

Scope: all 103 open issues **except** #396/#397/#398 (detached runs, Phases 2/2b/3
— the current work), so 100 issues.

Every judgment below was checked against the tree at `795393b`, not against the
issue body. That distinction is the point of this audit: most of these issues
were written between 11 Jul and 19 Aug, and the codebase has moved a long way
since. Several bodies describe a "current state" that is now simply false, and
two of them are false in the direction that matters — they say a thing is
*impossible* when it now ships, or say there is *nothing to guard* when there
now is.

A note on a broken reference: six issue bodies (#57, #77, #82, #83, #201, #208)
cite `docs/issue-audit-2026-08-19.md`. That file has never existed in the repo
(`git log --all -- 'docs/issue-audit*'` is empty). The re-scoping work it
describes clearly happened — those six bodies were rewritten — but the document
they point at was not committed. Worth either restoring it or editing the six
references.

---

## 1. Close these

### Shipped — the issue is done

| # | Title | Evidence in tree |
|---|---|---|
| **#1** | Re-establish the CI quality gates | All seven "clean carry-overs" are done. `.github/workflows/ci.yml` has 8 jobs; coverage uploads for both halves (`backend/coverage.xml`, `frontend/coverage/lcov.info`); a `mypy` job; an `eslint` job whose config loads `jsx-a11y`, `react`, `react-hooks` and pins `jsx-key`; `backend/ruff.toml` selects **33** rule families (was E/F/I/B/UP/SIM/RUF) including `C90` with `max-complexity = 10` — i.e. the C901 gate. All three lint gates are ratcheted (`lint-baselines/{ruff,mypy,eslint}.json`). "Boundary-bypass ratchets" also shipped, as the AST guards. |
| **#79** | Retcon replay | `store/replay.py` — `preview` / `begin` / `stage` / `accept` / `cancel` / `repoint_scenes`; the `post_replay_turn` route; `ReplayPanel.tsx`, whose docstring names "#79/#80". |
| **#80** | Offer to fork before a large retcon replay | Same panel: "the fork nudge that sits between the two". `config.replay_fork_threshold()` + `DEFAULT_REPLAY_FORK_THRESHOLD`; `store/fork.py:fork_campaign(cid, name, from_scene)`. Both prerequisites (#72, #79) landed and the nudge landed with them. |
| **#101** | Fire scheduled events when the clock advances | `store/events.py` (create/update/delete/`fire`/`unfire`/`crossed`/`upcoming`/`on_day`); `clock._crossing` + `clock._fire` stamp on advance and feed the digest; `EventsPanel.tsx`. |
| **#103** | Age commitments into overdue and stale | `store/aging.py` — `OK, STALE, OVERDUE`, `age`, `annotate`, `summary`, `stale_after`; `store/commitments.py` (so #115 landed too). Both the "stale" and the "overdue" half exist. |
| **#147** | One Providers tab | `ConnectionsView.tsx` + `ConnectionEditor.tsx` + `ConnectionForm.tsx` over `store/llm_connections.py` (`openrouter`, `claude`, `openai_compatible`). Embeddings are configured through the same mechanism (`embeddings_connection_id`). Only the imagegen card is missing, and that belongs to #172/#173. |
| **#21** | Re-importing a lorebook makes name-2 duplicates | `lorebook.commit` now dedups by (name, keys, body) signature per category via `_existing_signatures` and drops repeats. The duplicate behaviour the issue describes is gone. |
| **#24** | Gallery UI for a character's non-avatar images | `CharacterEditor.tsx:559` builds `galleryImages` from the version's `images` minus the avatar, rendered in `images-shelf` blocks, with a describe-images pass on top. |

For **#1**, if you want to keep a tracker: four boxes are genuinely unticked
(scenario smoke subset, self-test the harness, spec-to-test traceability,
parametrized boundary coverage). Those are a different, smaller issue than the
one filed — retitle rather than leave a 7/11 checklist reading as "CI is not
set up".

### Superseded — the need was met a different way

- **#125 — Suggested starter keys for extras, per entity kind.** This was a UI
  nudge stacked on #123's freeform `x-` extras. What actually shipped is
  `store/entity_schema.py`: a *typed per-kind field registry* (#37) with exactly
  the keys the issue proposed as suggestions — `item_type`/`rarity`,
  `group_type`, `creature_type`/`threat`. The suggestion list was the weak
  version of the schema that now exists. Close; and re-scope or close **#123**
  (freeform extras) with it, since its premise — "entity frontmatter is a fixed
  schema, `{name, keys, owners}`" — is no longer true.

- **#203 — Layered CI markers.** Its Current State opens "There is no
  `.github/workflows/` directory at all yet". There are now eight jobs, split
  one per `make check-*` target — which *is* the layering, expressed as
  something that already reproduces locally. The frozen-campaign layer exists as
  a fixture inside the suite (#205 landed). Adding `pytest.mark` names would
  create a second taxonomy over the same tests with no consumer. Close as
  superseded unless someone specifically wants `-m "not slow"` locally.

- **#197 — Mechanics-contributed HUD widgets.** Its own body says: do not scope
  this further until #160 and #195/#196 land. #160 landed (`store/modules/`,
  `store/sheets/`, `ModulesView`). #195/#196 did not — there is no HUD anywhere
  in the tree. So the issue is still a placeholder that will need a complete
  rewrite whenever a HUD exists, and its dependency list is the only content in
  it. Close; re-file from #196 at that point.

### Not obsolete — **inverted**. Rewrite, don't close.

**#189 — Suppression invariants for auxiliary tasks.** The body's entire
argument is: *"This is already the natural state of the codebase, not a gap to
build — the things this issue asks to suppress mostly don't exist"*, backed by
"grep finds nothing resembling a rules engine, dice roller, or LLM tool-use"
and "no 'drift' concept exists in the backend".

Both greps now hit. `store/dice.py`, `store/checks.py`, `store/rolls.py`,
`store/modules/`, `store/sheets/` (schema/reader/writer/creation/advancement/
pools/tally), `store/voice_drift.py` and `store/length_drift.py` all exist. The
premise that made this a documentation task is gone; there is now real
machinery that an ephemeral path could fire and nothing structural stopping it.

This is the highest impact-to-effort item in the backlog and it reads as the
lowest, which is exactly why it is called out here. See §2.

---

## 2. The most important and impactful

Ranked. The first is a guardrail; the rest are user-visible.

**1. #189 (rewritten) — a real suppression guard for ephemeral generation.**
Now that mechanics, dice, rolls, sheets, voice-drift and length-drift all ship,
nothing prevents a future ephemeral or auxiliary path from rolling dice,
mutating a sheet, or recording drift as a side effect of a preview. This repo
already enforces exactly this class of invariant by parsing its own ASTs —
`test_atomic_guard`, `test_overlay_guard`, `test_paths_guard`,
`test_lock_order_guard`, `test_lock_domain_guard`, `test_import_guard`, each
with its own `# <name>-ok: <reason>` escape. A `test_ephemeral_guard` in that
family, plus the one-paragraph rule it enforces, is a day's work and closes a
hole that will otherwise be found by a user seeing their character sheet change
because they asked for a tagline.

**2. #142 — Route each task to its own model.** Every LLM call in the app runs
on one connection: scene turn, opener, absorb, the per-NPC dossier loop inside
absorb, scene suggestions, taglines, voice anchors, image descriptions,
scenario parse, the audit pass. `store/usage.py` already tracks cost per task
and already knows which calls came back unpriced. This is the largest cost
lever left, and the absorb + dossier loop is where the money goes. Build #142
(per-task), not **#143** (two tiers) — the call-site list has grown well past
the six the tier split was drawn around, and the two are mutually exclusive by
their own admission. Note the shape changed under both bodies: the unit to
route to is now a *connection*, not a model string.

**3. #52 / #53 / #60 — the campaign→library direction.** Sync is still strictly
one-way. `sync.py` diffs three hashes and only ever advances the campaign; the
only `promote` routes in the tree are for *images*. So everything that emerges
in play — an NPC, a location, an evolved lore entry, a campaign edit that turned
out to be the better version — is trapped in that campaign permanently. Three
issues describe three faces of one missing mechanism (promote a campaign-local
record, push an edit back as a library version, create an emergent character at
all). Worth one design pass across all three rather than three separate builds;
#52's base-hash discipline is the hard part and the other two inherit it.

**4. #201 — Bulk "create missing sheets".** Both blockers shipped and
`sheets/tally.py:coverage(cid)` already computes precisely "who has a sheet and
who doesn't". Nothing renders it, and `SheetPanel.createSheet()` makes one sheet
for the entity on screen. A world imported in bulk means clicking through the
whole cast. Well-scoped, unblocked, and the issue body already names the one
real decision (what "default sheet" means when the module's creation step has
required choices).

**5. #57 — Batch-derive taglines across a world.** Same shape and the same
cause: bulk import leaves a roster with no taglines and no way to fix that
except one character at a time. Unlike #201 this degrades *output*, not just
ergonomics — taglines are what the off-scene cast directory shows for "known"
characters, so a tagline-less roster silently weakens every prompt. The second
half of that issue (decide whether a dossier can go stale) is a five-minute
decision that has been sitting as an accident rather than a choice.

**6. #83 — Give the director's note its own input.** The issue says it best:
"the last thing between director mode and being usable by someone who did not
build it". The whole subsystem shipped; the composer is overloaded so that in a
pcless scene whatever you type *is* a director's note, and nothing on screen
says so. Frontend-only, and it finishes a feature rather than starting one.

**7. #224 — Absorb can't evolve item, group or creature bodies.** Verified live:
`absorb/materializer.py:_entity_kind` still loops `("lore", "locations")` while
`entities.ENTITY_KINDS` is now `("locations", "lore", "items", "groups",
"creatures")`. Three of the five entity kinds are write-once after creation.
The issue correctly frames this as a decision to make deliberately rather than a
bug — but it has been undecided since the kinds shipped.

**8. #119 — Reclassify a lore entry into another kind.** Unblocked by #36.
Lorebook import is the main on-ramp into a world and it dumps almost everything
into `lore`; there is no way to move a record afterwards without losing its id
and therefore its sync ref. The issue's Option A (lore↔locations move, with the
dependent-campaign manifest rewrite) now extends free to all five kinds.

**9. #146 — Provider health check, on demand.** With three connection kinds
including local `openai_compatible` servers, "did this key and base URL actually
work" is a routine question whose only answer today is a failed turn.
`post_connection_models_refresh` already does the round trip — a Test button is
a thin route plus a button, and the issue's Option A (skip the scheduler
entirely) is the right call.

**10. #218 — Greeting location.** Small and plugs a real hole: greetings are
scene openers, scenes carry a location, greetings don't. Filed from a real
session that imported 99 greetings all of which had an obvious setting and
nowhere to record it. Also unblocks seeding a scene's location from its
greeting.

**Close behind:** **#86** (per-scene POV — foundational for #122 and the POV
half of #140, and nothing else can start until it exists), **#92** (import a
chat log in-app — the whole pipeline exists as `ingest-campaign-log`, but only
an agent can drive it), **#99** (cast source badges — derives from hashes the
lock bookkeeping already computes), **#156** (error store — the per-NPC dossier
loop still swallows exceptions with a bare `except Exception: continue`, so a
failing dossier leaves zero trace anywhere).

---

## 3. The easiest, ranked

Effort ascending. Impact is deliberately not weighted here; several of these are
small precisely because something else already landed that did the hard part.

| Rank | # | Why it is cheap now |
|---|---|---|
| 1 | **#224** | One tuple in `_entity_kind`, plus the absorb prompt wording and tests. The decision is the work; the diff is a line. |
| 2 | **#138** | `LorebookImport.tsx` hardcodes five `<option>` values that are exactly `entities.ENTITY_KINDS`. Expose the tuple, read it. |
| 3 | **#218** | One optional frontmatter key + `_meta_dict` + two route models + a chip in `GreetingEditor` + seed-on-start. No migration (absent key = none). |
| 4 | **#83** | Frontend-only. `CampaignView` already tracks `directorNote` and renders it during a run; it needs an input and a label, not a mechanism. |
| 5 | **#99** | One derived `source` field in `cast_detail`, computed from the `base` hash already stored at lock time vs `actor_hash`; one chip to render it. |
| 6 | **#77** | One field on `RegenerateBody`, threaded to the run, one connection picker beside the reroll-guidance input that already exists, stamped onto the alternate. |
| 7 | **#146** | One route reusing the call path `post_connection_models_refresh` already exercises, one button in `ConnectionEditor`. |
| 8 | **#149** | `models.ts` is the last hardcoded `https://openrouter.ai/api/v1/models`. `llm_connections.cached_models(id)` and the refresh route already exist server-side; this is pointing the client at them. |
| 9 | **#41** | `world_bundle.write_bundle` + `import_bundle` already do the entire copy, including `_repoint_urls` for the localized image URLs the issue flags as the one real hazard. Fork = bundle to a temp path, import, rename. |
| 10 | **#107** | Both prerequisites shipped (`clock.advance`, `fork.fork_campaign`) and the exact pattern shipped too — it is the replay fork nudge (#80) with a different threshold. Copy it. |
| 11 | **#130** | `prompt_log` stores frozen `context_breakdown` payloads per turn, `SceneInspector` already renders live *and* frozen side by side, and `changes.line_diff` is the diff primitive. This is wiring, not design. |
| 12 | **#189** | A guard test in an established house pattern plus a paragraph of prose. (Cheap *and* item 1 in §2 — take it first.) |
| 13 | **#82** | No code at all: turn on `speaker_turn_taking` (already a config key), play a four-hander, and answer the question. Most likely outcome is that the issue closes. |
| 14 | **#68** | The availability engine is a pure function that already takes `player_tags`; the missing piece is a thin world-scope wrapper route and a debounced fetch in wizard step 2. |
| 15 | **#193** | One `useGlobalHotkeys` hook, no new dependency. Moderate rather than trivial — but self-contained, and it fixes a real inconsistency on the way (`RecordDrawer` has no Escape handler while `NewSceneChooser` does). |

---

## 4. Bodies that are stale but the issue is still live

Not close candidates — but do not trust their "Current state" sections. Each
describes a codebase that no longer exists in some load-bearing way:

- **#150 / #151 / #154 / #155** — all four open by asserting nothing is captured
  or logged. `store/prompt_log.py` (frozen per-turn composition), `store/usage.py`
  (tokens, cost, `duration_ms`, status, cache reads/writes, `unpriced_calls`),
  `store/audit/` and `logging` in `main.py`/`llm.py`/`runner.py` all exist.
  #150 is largely satisfied; #151 and #154 are now mostly consumers of data that
  already lands; #155 needs a file handler and a view, not instrumentation.
- **#71 / #199** — `store/pins.py` exists, `IncomingReview.tsx` and
  `WorldPushPanel.tsx` shipped (#6 and #8 are done). What is left is the
  composition *overview*, not the pieces.
- **#113** — `journal.py` (#31) and the `rev`/`StaleRecordBanner` machinery (#35)
  both landed; the drift signal is much closer than the body suggests.
- **#140** — #139 landed: `export.collect()` plus markdown/HTML/text/JSON
  builders. Filters now sit on a real shared collector, exactly as the issue
  hoped.
- **#66** — #161 (sheets) landed, so the "capabilities" third of it is unblocked.
- **#27** — still a blind one-click commit, but `lorebook.commit`'s dedup makes
  the failure mode much milder than when it was filed.
- **#26** — `cards.read_charx_asset` / `charx_avatar_path` / `MAX_ASSET_BYTES`
  exist, so the avatar half is done. Verify whether non-avatar bundled assets
  land before deciding whether anything is left.
- **#167–#171** (inventory) and **#200** (images view) remain correctly parked:
  no inventory anywhere, and no generative imagegen (`store/image_descriptions.py`
  and `DescribeQueue.tsx` describe existing images, they do not make new ones).
- **#42 / #43 / #86 / #187–#190** — genuinely unbuilt. Their bodies are accurate.
