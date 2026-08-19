# Issue audit — 19 Aug 2026

**Scope.** All 153 open issues. Bodies read for 148 of them (the other five — #227,
#250, #256, #322, #351 — read individually); titles, labels and milestones for all
153. Every claim below was checked against the working tree at `49e2c11`, not
against the issue text.

**Counts.** 153 open, 111 closed, numbers running to #351 (the gaps are PRs).

**The backlog is unusually well kept.** Most bodies carry a hand-written
`## Current state` section, and several carry per-item checkboxes marking what has
already landed. Those sections were written 11–14 Jul 2026 and were accurate then.
The tree has moved since, so the yield of this audit is mostly *drift*: work that
landed after the triage and was never reflected back into the issue.

Two structural findings are worth more than any individual issue:

1. **Five shipped, tested backends have zero frontend callers.** Sync/incoming,
   cast suggestions, and cost/usage each have working routes that nothing in
   `frontend/src/api/client.ts` ever calls. These are the cheapest value in the
   backlog, and at least one of them (sync) behaves like a correctness bug rather
   than a missing feature.
2. **Three self-contained epics — imagegen, plugins, inventory — account for 21
   open issues (14%) with no partial work and no dependents.** They are noise in
   every triage pass until they are labelled as parked.

---

## 1. Done — close these

| # | Title | Evidence in the tree |
|---|---|---|
| **#250** | weather fixture `phi` 1 ULP off | `backend/tests/test_weather_draw.py:13` now defines `PHI_ULPS = 4`; the `phi` assertion is `pytest.approx(row["phi"], abs=PHI_ULPS * math.ulp(...))` while `u`/`z`/`g`/`drawn` stay bit-exact, and the docstring now separates the portable columns from the libm-dependent one. That is precisely option 1 from the issue body, including the docstring change both issues asked for. |
| **#256** | same failure, filed separately | Duplicate of #250 — same test, same assertion, same root cause. Fixed by the same change. |
| **#148** | prompt caching with cache-token accounting | `llm_usage.py` implements `cache_written()` / `cache_read()` across both provider wire shapes; `store/usage.py` carries `cache_read_tokens` / `cache_write_tokens` as slices of `prompt_tokens`. Both modules cite #148 by number. |
| **#15** | drop unaddressable dotted-stem entries from `list_images` | `store/assets.py:list_images` filters on `_addressable_name(p.stem)`, with a comment explaining the addressability-not-extension rule and the one deliberate `promote-tmp` exception. |
| **#214** | large-service splits | The issue's entire scope was "split `routes.py`". It is now a `routes/` package of 15 domain modules (`campaigns`, `characters`, `scenes`, `worlds`, `entities`, `greetings`, `mechanics`, `modules`, `config`, `models`, `search`, `streaming`, `usage`, `weather`, `common`). |
| **#89** | new-scene picker: ledger ideas + LLM suggestions + free-text custom + LLM-only refresh | All three clauses now hold. `SceneIdeaPicker.tsx` composes `savedDraft` / `suggestionDraft` / `greetingDraft` / `customDraft`; the saved half reads the idea ledger (#88, since landed, with `GET/POST /campaigns/{cid}/scene-ideas`); `useSceneSuggestions.run(direction, rank)` re-runs with `rank=false`, which regenerates the generated slots without redoing greeting ranking — the exact gap the body named. |

---

## 2. Blocker cleared or gap closed since triage — re-scope, don't re-read

These are not done, but each body describes a blocker or a gap that has since
closed. Left as written, they will be re-analysed from scratch every time.

| # | What changed |
|---|---|
| **#29** context-builder tracking | Both remaining layers landed. "User-editable section ordering" → `store/context/layout.py` + `PromptLayoutEditor.tsx` + `GET/PUT /prompt-layout`. "Group active-speaker turn-taking" → `store/context/speaker.py` + the `speaker_turn_taking` config flag. Only "playtest validation of the tuning points" is left, which is not an engineering task. |
| **#212** data-layer hardening | Both headline gaps closed. Atomicity: `store/atomic.py`, with `test_atomic_guard.py` parsing the package's own AST so every write must go through it. Locking: `store/locks.py`, plus `test_lock_order_guard.py` and `test_lock_domain_guard.py`. Auto-backup and conflicted-copy detection landed on top. |
| **#216** router/API consistency | Sub-routers: done (see #214). Kebab-case: the body itself says it was already satisfied. Only pagination remains — and the body calls it a non-problem for personal-scale data. Reduce to "pagination" or close. |
| **#215** lifecycle extraction | `_lifespan` in `main.py` now has real structure: guarded idempotent startup steps that degrade to a warning under `StoreBusy`, an `anyio` task group, a backup ticker, and cancel-on-exit. The body's premise ("nothing to extract yet") has resolved itself. |
| **#201** Mechanics view | Both stated dependencies shipped. #160 → `store/modules/` + `builtin_modules/` + `ModulesView.tsx`. #161 → `store/sheets/` + `SheetPanel`/`SheetEditor`/`SheetLayout`/`SheetWidgets` + world and campaign sheet routes. Of the original three asks, only "bulk-create-missing" appears unbuilt. |
| **#202** style guides & image presets | The Styles half is fully shipped: `store/styles.py`, `/styles` CRUD + duplicate routes, `StyleGuidesView.tsx` + `StyleGuideEditor.tsx`, and its own entry in `librarySections.ts`. Image presets remain blocked on imagegen. Split it in two and close the Styles half. |
| **#57** AI brief | The body's "the real remaining gap" was that nothing displayed a dossier. `components/play/DossierColumn.tsx` now does. What is left is the batch derive (#5) and a staleness flag that taglines deliberately reject. |
| **#211** README / AGENTS / CONTRIBUTING / screenshots | `README.md` now exists at the root (259 lines). `AGENTS.md`, `CONTRIBUTING.md` and docs screenshots are still absent. Re-scope to those three. |
| **#82** per-character posts | The split half was already done. `context/speaker.py` now nominates a lead speaker from history without N calls per turn, which addresses the failure mode the "speaker loop" was meant to fix at a fraction of the cost. Worth deciding whether the loop is still wanted at all. |
| **#77** regenerate with steering or a different model | Steering shipped (`RegenerateBody.guidance`, `templates/scene/regenerate_guidance.j2`, the reroll-guidance input in `CampaignView`). Only the per-call model override is left, and it is cheaper now that `store/llm_connections.py` holds named connections. |
| **#83** director mode | 5 of 7 boxes already ticked in the body; the residue is a dedicated director's-note input affordance. |
| **#207 / #208** setup scripts, shortcuts, icons | Both bodies already say "largely implemented". The residue is small and specific (a macOS `.icns`, a few flow gaps). Re-title so they stop reading as unstarted. |

---

## 3. Stale premise — the code these describe no longer exists

- **#4** (show/edit/regenerate `brief.md`) and **#5** (batch "derive all briefs") both
  cite `store/briefs.py` and `GET/PUT/POST /worlds/{wid}/characters/{cid}/brief` at
  `routes.py:464-503`. None of that exists. There is no `brief` module and no brief
  route anywhere in `backend/src/grimoire`; the concept was split into taglines
  (`store/taglines.py`, full UI in `CharacterEditor` + `TaglinePrompt.tsx`) and
  dossiers (`store/dossiers.py`, now surfaced by `DossierColumn.tsx`). #57 already
  records this. **#4 should close**; **#5 should be refiled** as "batch tagline
  derive across a world".
- More broadly, essentially the whole 11 Jul batch cites `routes.py:NNNN` line
  numbers into a file that no longer exists. The prose is still good; the
  coordinates are not.

---

## 4. Verified still open (spot-checked; no drift)

Each of these was checked directly rather than trusted:

- **#11** — `ConfigView.tsx` has no `scan_depth` control (the backend setting is in `store/config.py`).
- **#14** — `client.ts:1635` `createPC` body is still `{name, tags?, persona?}`; no `version_name`.
- **#17** — `store/greetings.py:update_greeting` takes `name`/`body`/`requires_tags`/`predecessor_join`/`present`/`pcless`; no `character`/`version`.
- **#18** — `store/playing.py:player_tags` filters on `a["kind"] == "pcs"`, so a character cast as player contributes nothing.
- **#20** — no `secondary_keys` / `probability` / `case_sensitive` / `order` anywhere in `store/lorebook.py`.
- **#26** — CHARX *avatar* extraction landed under #25 (`_resolve_avatar` + `cards.read_charx_asset`); additional bundled image assets are still not stored.
- **#99** — `appearances/cast.py:cast_detail` returns `{kind, id, name, version, body}`; still no provenance field.
- **#113** — `store/provenance.py` exists but is absorb *citation* provenance, not library-vs-campaign drift. `changes.py` gives per-record deltas within a campaign, which is adjacent but not the ask.
- **#142 / #143** — no per-task or tiered routing anywhere in `llm.py` or `store/llm_connections.py`.
- **#213** — every non-stream LLM error site still hardcodes `status_code=502` regardless of `exc.kind`; nine such sites across five route modules. `llm.py:55` still says it "waits for #213".

---

## 5. High impact — ranked

### Tier 1 · Shipped backend, zero frontend callers

The cheapest value in the backlog. Each is a working, tested feature that is
silently inert because no client method exists.

1. **#6 + #8 — world → campaign sync.** `GET /campaigns/{cid}/incoming` and
   `POST .../incoming/accept|reject` exist and are tested. The string `incoming`
   does not appear in `client.ts` at all. **Campaigns never see world updates and
   nothing tells the user so.** This is the one item here that reads as a
   correctness bug rather than a missing feature, and it is the highest-value
   single item in the backlog.
2. **#7 (parent #96) — "who should appear" strip.** `GET /scenes/{sid}/suggestions`
   and `POST .../suggestions/dismiss` exist, backed by
   `appearances/transitions.py:suggestions` with per-scene dismiss persistence.
   Nothing calls either. #96 already has 4 of 6 boxes ticked; #7 is the whole
   remainder.
3. **#153 — per-turn cost breakdown.** `store/usage.py`, `GET /usage/summary` and
   `GET /campaigns/{cid}/usage` all exist, including the cache accounting from
   #148. `client.ts` has no caller. There is currently no way to see what a
   campaign costs.

Also orphaned but untracked by any issue: `POST /campaigns/{cid}/rolls/{rid}/replay`
and `GET /worlds/{wid}/campaigns` have no frontend caller either. Worth a
one-line issue or a deletion.

### Tier 2 · CI health — this taxes every other issue

4. **#351 — the second `CampaignView.test.tsx` race.** It reds CI on PRs that never
   touch the file, so every unrelated change pays for it and everyone learns to
   re-run instead of read. Highest leverage of anything in the backlog per hour
   spent. The issue already contains the measurements (sequential green, parallel
   flaky, a different victim each run) and names three more files with the same
   never-settling-mock shape.
5. **#322 — `test_concurrent_resolved_retries_persist_once`.** The test has since
   been made self-diagnosing (each thread records its own outcome, so a raising
   racer no longer disappears into `excepthook`), but the root cause is explicitly
   not established. Cheaper to chase than #351: one 3-minute file run.
6. **#1 — quality gates.** Twelve unchecked boxes, most of them small config PRs
   (coverage reporting, mypy, typed eslint rules, jsx-a11y, ruff C901, wider ruff
   selection). Land report-only, clear the surfaced set, then promote to blocking.

### Tier 3 · Biggest narrative payoff

7. **#97 + #98 — in-turn cast change from prose, and unknown-name routing.** The
   natural successor to #96: today discovery is card-text only, so a character
   mentioned in play but in no card never surfaces. This is what makes emergent
   characters (#60) actually emerge.
8. **#101 + #103 — scheduled events firing on advance, and commitment aging.**
   `store/commitments.py` and `store/plot.py` already write everything these need
   — `commitments.py:17` says so outright ("aging (#103) reads, and this module
   only has to keep them"). Unclaimed payoff on data already on disk.
9. **#72 — campaign fork.** Cheap on its own, and it is the safety net the retcon
   epic needs. Do this before #78.
10. **#78 / #79 / #80 — retcon with re-extraction, replay, and the fork nudge.**
    The highest-value "fix the story" capability in the product and the largest
    epic left. Nothing named `retcon` exists in the tree today. Do not start it
    before #72 exists.
11. **#84 — scene-break detection.** Small next to the above, and it removes a
    manual step from every session.
12. **#219 — PCs have no avatar/image support.** Characters have upload, replace,
    remove, promote, crop focus and copy-from-greeting; PCs have none of it. Small,
    visible, and an asymmetry a user hits immediately.

### Deliberately park

Three self-contained epics with no partial implementation, no dependents, and no
code in the tree:

- **imagegen** — #172–#181 (10 issues). No `imagegen` string anywhere in the backend.
- **plugins** — #182–#186, #206 (6 issues). No plugin loader, no keyring.
- **inventory** — #167–#171 (5 issues). No inventory store.

21 issues, 14% of the backlog. Label them `epic:parked` so triage stops
re-reading them. Note that #202 (image presets half) and #159 (imagegen cost) are
downstream of the imagegen epic and inherit the parking.

---

## 6. Housekeeping

- **Duplicates:** #250 and #256 describe the same test, the same assertion and the
  same root cause. Close both as fixed; keep #250 as the record since it has the
  per-column analysis.
- **Label coverage is thin:** roughly 43 of 153 open issues carry any label, and
  almost all of those are from the 11 Jul batch or the four newest bugs. The
  ~110 unlabelled ones are the bulk of the backlog.
- **The `partially implemented` label is doing real work** — 20 issues carry it and
  in every case checked it was accurate. Consider a companion
  `blocker-cleared` label for section 2 above.
- **Line references rot fast.** The bodies' prose survived the `routes.py` split
  intact; the `routes.py:NNNN` citations did not. Module-and-symbol references
  (`store/assets.py:list_images`) held up across the same refactor.
