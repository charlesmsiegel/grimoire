# Issue triage, 19 Aug 2026

126 open issues. This is a read of all of them against the tree at `d61f383`,
answering two questions: **what should be done next**, and **what should stop
being tracked**.

Headline: **close 26, re-scope 4, file 2 new** — leaving ~100 open, of which 11
are a credible "do next" list.

> **Status, 19 Aug 2026.** The plugin epic (6) and the imagegen generation set
> (10) have been closed as `not planned`. The replacement work is filed as
> **#376** (insert an image into a post) and **#377** (send those images to the
> model when it accepts them). The remaining recommendations in this document
> — inventory, the done-already pair, the re-scopes — are still recommendations. The three parked epics (plugins, imagegen,
inventory) are 24 of the 126, and none of them survives contact with the
current codebase in the shape it was filed in.

A note that applies to almost every issue numbered under 227: they were filed
against the pre-rebuild tree, and their bodies cite `backend/src/grimoire/routes.py`
as a single ~2100-line module. It is now a `routes/` package. **Do not trust a
line number or a file path in an old body without re-deriving it** — the
defects mostly still hold, the coordinates mostly do not.

Two issue bodies (#82, #202) cite `docs/issue-audit-2026-08-19.md` as the record
of an earlier re-scoping pass. That file is in no commit on `main`. Either it
should land or the citations should be dropped; right now they point at nothing.

---

## 1. Close: the plugin epic (#182, #183, #184, #185, #186, #206)

**Plugins add nothing to the app as it stands, and the epic's own recommended
first increment has already shipped by another route.**

The epic exists to serve four capability kinds. Their status today:

| Kind | Status | Delivered as |
|---|---|---|
| LLM | **shipped, no plugin system** | `store/llm_connections.py` — named connections of kind `openrouter` / `claude` / `openai_compatible`, each holding its own `base_url` + `api_key` + `model`; clients in `openrouter.py`, `claude_agent.py`, `openai_compatible.py` behind `llm.py`. #141 ("Multi-provider gateway (cloud + local, plugin-loaded)") closed **completed** on 2026-08-14. |
| Embedding | **shipped, no plugin system** | `store/embed_space.py` resolves `{model, base_url, key, space}` from a named connection; `vectors.py` / `semsearch.py` / `context/semantic.py` consume it. #34 closed completed. |
| Export | one bundled implementation | `store/export.py` + `store/epub.py`. #140 (export filters) asks for more filters, not for third-party code. |
| Imagegen | does not exist | and is itself parked — see §2. |

#183's own body names Option B — "declarative user providers, no code
execution… a single bundled generic client serves all of them… covers the
dominant real case (local llama.cpp/ollama/vLLM and most cloud providers speak
the OpenAI dialect)" — as the recommended first increment. That is a precise
description of what `llm_connections.py` does. The recommendation shipped; the
issue tracking it did not notice.

So the registry in #182 has no consumer left that is not already served, and
what remains of the epic is only cost. #183 Option A is arbitrary code
execution out of a directory under `store.home()` — and the Configuration page
exists specifically to point that directory at a synced folder (CLAUDE.md:
"point it at a synced folder to share a library across devices"). That is a
sync client with a code-execution channel into the app, bought in exchange for
capability the connections system already provides.

**Counter-argument, stated fairly:** the app *does* already load user Python —
calendar plugins from `<GRIMOIRE_HOME>/calendars/` via
`store/calendars/plugins.py`. So the precedent exists. But it is deliberately
scoped to one pure-computation domain with a narrow ABC, and it is the right
thing to extend *if* a concrete third-party need ever appears. That would be a
fresh, small issue against a working precedent — not this six-issue epic built
around a host that no longer has a job.

**Recommended:** close all six as `not planned`, citing #141 and
`llm_connections.py`. — **Done, 19 Aug 2026.**

---

## 2. Close: the imagegen epic, except two — and file its replacement

Thirteen issues (#159, #172–#181, #200, #202). There is no image *generation*
anywhere in the tree. There is a great deal of image *handling*:
`store/assets.py` (per-version named images, promote), `thumbs.py` (lazily
cached WebP downscales served by `?w=`), `localize.py` (scan text for image
refs, download, rewrite to local URLs), `image_subjects.py` (who appears in
which greeting image), `covers.py`, `appearances/`, plus six live image route
surfaces (world/campaign × character/PC/entity) and ETag/`?v=` immutable
serving.

That split is the whole triage: **the handling substrate is worth building on;
the generation epic on top of it is not.**

### Close as `not planned` (9) — **done, 19 Aug 2026**

#172 (generate from context), #173 (pluggable backends), #174 (queue), #175
(re-roll / img2img), #177 (per-character seed), #179 (auto-illustration
policy), #181 (extract expressions from prose), #159 (cost per generated
image), #202 (image presets — its style-guides half shipped as `store/styles.py`
+ `StyleGuidesView`; its remaining half is blocked on #172).

### Close, with a note (1) — **done, 19 Aug 2026**

#178 (auto-generate an export cover) — `store/covers.py` already gives a
campaign a cover and `epub.py` already makes it the book's first page. What is
missing is only the *generation*, which goes with the rest.

### Keep, re-scoped (2 → merge to 1)

#176 (image index: star, tag, cross-record browse) and #200 (Images view) are
both about **managing images the store already holds** — character galleries,
greeting art, entity images, covers. Neither needs a generator. Merge into one
issue: *"Images view: browse every stored image across worlds and campaigns,
with star and free-form tags"*, built on `list_images` + `thumbs.py`, modelled
on `TaggingQueue.tsx` rather than the list/detail pattern (it is a grid, not a
record list).

### Owner's call (1)

#180 (expression sprites in the scene pane) is the one generation-adjacent
issue with standalone value: sprite packs are something users already own and
import, and CHARX import (#26) brings bundled images in. Without #181 there is
no automatic driver for *which* expression to show, so it would need manual or
per-post selection. Keep re-scoped, or close — but decide it on its own merits,
not as part of the epic.

### File the replacement: #376 and #377 — **filed, 19 Aug 2026**

**Today there is no way to get an image into a scene post at all.** Confirmed:

- Scenes have no asset store — no `base="scenes"` call anywhere in the backend,
  no `/scenes/{sid}/images` route.
- The composer has no paste, drop, or file control — no `onPaste`, `onDrop`, or
  `type="file"` in `CampaignView.tsx` or `OpenerComposer.tsx`.

But the transcript already renders markdown: `RenderedMarkdown`
(`CampaignView.tsx:246`) is `react-markdown` + `remarkGfm`, used for every post
(`:4252`) and the streaming reply (`:4280`). **An `![](/api/…)` in a post body
would render today** — nothing can produce the URL.

So the feature is small and almost entirely reuse:

1. A place to put images that belong to no character or greeting. This document
   first sketched *scene*-scoped assets; #376 lands it as a **campaign**-scoped
   store instead (`<campaign>/assets/images/`, on the `covers.py` precedent),
   which is the better shape — see the note on `scene_refs` below.
2. `PUT/GET/DELETE /campaigns/{cid}/scenes/{sid}/images/{name}`, mirroring the
   six existing image surfaces.
3. Paste / drop / file-picker in the composer and the edit-post textarea, which
   uploads and inserts the markdown at the cursor.
4. A picker to *reference* an existing character/greeting/entity image rather
   than uploading a second copy of it.

Inherited free: thumbnails (`?w=`), ETag and `?v=` immutable serving, the
extension allowlist, `routes.common._upload_image_ext`.

Three things to get right, all of which have precedent to copy:

- `store/export.py:collect` already scans scene text for localized image URLs
  and packs the bytes into the EPUB — but its `_LOCAL_*` regex (`export.py:28–37`)
  enumerates the URL shapes the app writes. A new scene-image shape must be
  added there or exports will silently ship a book with broken images.
- `store/scene_refs.py:repoint` bulk-renumbers scene ids across every store that
  persists them (four today). A scene-keyed asset folder would be a fifth, or
  fork / undo / retcon would orphan it. **#376's campaign-scoped store sidesteps
  this entirely** — the images do not hang off a scene id, so there is nothing to
  repoint. This is the reason to prefer it over the scene-scoped sketch above.
- Write the existence gate in from the start — see §5, #373. A new image write
  surface is exactly where that bug class comes from.

**This is the trade the epic should make:** ~13 issues of speculative
generation infrastructure, for one issue that makes the images users already
have reachable from the place they actually play.

---

## 3. Collapse: the inventory epic (#167–#171 → 1 spike)

Inventory genuinely does not exist (`grep -ri inventory backend/src` finds two
unrelated docstrings). Unlike plugins it is not superseded — it is unstarted.
But five issues and ~2000 words of design for a subsystem with no code and no
date is its own kind of inventory.

It has also aged. #167 Option A proposes a bespoke `inventory.json` plus a
campaign-frontmatter toggle, written in July. Since then the mechanics-module
system shipped: `store/modules`, `store/sheets`, `store/entity_schema.py`,
per-entity sheets with derived fields evaluated by `expressions.py`, plus
per-kind coverage counts. A module-defined equipment field is now a plausible
host that did not exist when #167 was written. Separately, items-as-entities
already exist (`routes/entities.py` covers items, groups and creatures), so
#167's "#36 dependency" is satisfied.

**Recommended:** close #168, #169, #170, #171 as `not planned`; keep #167,
re-scoped to a single spike — *"Decide whether inventory is its own subsystem
or a mechanics-module concern"* — with the four closed issues' schemas quoted
in it as prior art. If the answer is "subsystem", re-file then, against a tree
that will have moved again.

---

## 4. Close: done or effectively done

- **#24** (gallery UI for non-avatar images). `CharacterEditor.tsx:502` derives
  the gallery from the version's `images`; upload allocates the next
  `gallery_N` slot (`:1075`); `copyFromGreeting` (`:1047`) fills it; the
  "Appears in" strip and `TaggingQueue` exist. Every acceptance-criteria line
  appears to be met, with the single deviation that names follow the `gallery_N`
  convention rather than being free-form. **Walk the checklist, then close.**
- **#201** (sheet coverage + bulk create). Both owner comments say Phase 1 and
  Phase 3 delivered it; the sole residual — bulk-create-missing — was
  deliberately handed to #164's creation wizard, because blank default sheets
  would defeat the coverage indicator. **Close as tracked in #164**, or reduce
  the body to that one line.
- **#202** — see §2.

---

## 5. The "do next" list

### Tier 1 — real defects, hours each

**#373 — entity image writes accept any id.** The third copy of #360, which
closed *today* (PR #368). The fix shape is already on main
(`common._world_char_version_or_404`), and the test that enumerates the actor
image write surface off `app.routes` just needs its regex widened to cover the
six entity handlers. Cheapest issue on the board, and the context is warm.

**#213 — LLM error kinds do not reach HTTP status.** Six sites hardcode
`status_code=502` while carrying `exc.kind` in the body:
`routes/campaigns.py:1379`, `characters.py:208` and `:262`, `config.py:306`,
`scenes.py:100` and `:131`. A rate limit is therefore indistinguishable from a
broken gateway, which means no correct retry/backoff can ever be written on
either side. `llm_errors.LLMError` already carries the kind; this is a mapping
helper and six call sites.

**#14 — `createPC` / `createCampaignPC` client types omit `version_name`.**
Still exactly as described, at `client.ts:678` and `:681`, while the backend
model accepts it. Two-line type fix.

### Tier 2 — most felt change per hour

**#82 — decide whether one LLM call per NPC is still wanted.** #29, the
playtest that gated this, is closed — but `DEFAULT_SPEAKER_TURN_TAKING = "off"`
(`config.py:62`), so the question it was supposed to answer never got answered.
Turn the flag on, play a four-hander, and either close #82 (deleting an
N-calls-per-turn design) or reopen it with evidence. **Best ratio on the board
of cost-to-resolve against scope-removed.**

**New: images in posts** — §2.

**#11 — expose `context_scan_depth` in Configuration.** The key is in
`_CONFIG_KEYS` (`config.py:176`) and drives `context/assemble.py:150`, but has
no hit anywhere in `frontend/src`: a prompt-composition knob reachable only by
hand-editing `config.md`. `ConfigView` already renders its siblings
(`prompt_layout_enabled`, `speaker_turn_taking`, `offscene_known_limit`) — this
is one more field in an existing group.

**#210 — tell the user they are offline.** Worth more now than when filed:
local models genuinely work through `openai_compatible` connections, so
"offline" is a distinguishable state with a correct behaviour, not just a
failure.

**#223 — surface the calendar `confirmed` flag on the Overview checklist.** The
checklist exists; this is one more row on it.

### Tier 3 — structural, schedule deliberately

**#1 — re-establish the CI quality gates.** Confirmed against the tree: no
`mypy` and no `C901` in `pyproject.toml`, `ruff.toml`, the `Makefile` or
`ci.yml`. That is **55k lines of backend source and 84k lines of tests with no
static type check at all**, in a codebase whose architecture is otherwise
defended by seven AST-parsing guard tests. The issue's own ladder — land
report-only, clear the surfaced set, promote to blocking — is the right shape;
take the checklist one line at a time, mypy first. Highest-leverage chore open,
but it is a multi-PR grind: schedule it, do not wedge it between features.

**Not filed, should be: `CampaignView.tsx` is 4559 lines.** It is the page
almost every other open frontend issue lands in — #83, #196, #197, #199, the
new images work, the Tier-2 items — and #351 was already a CI race inside its
test file. Every feature above pays a tax here. Worth filing *before* the next
batch of frontend work, not after.

**#216 — opt-in limit/offset on the scene and chronicle list routes.** A long
campaign's list route returns everything. This gets more expensive to fix the
longer real campaigns run, which is an argument for doing it while they are
short.

### Tier 4 — valuable, keep, do not start yet

#187–#190 (auxiliary task framework), #79/#80 (retcon replay), #122 (thought
privacy), #123–#125 (narrative extras), #142/#143 (per-task model routing),
#150/#151 (audit trail and replay). All coherent, all sizeable, none urgent.

---

## Summary of recommended actions

| Action | Issues | Count |
|---|---|---|
| **Closed** — plugin epic superseded by `llm_connections` | #182, #183, #184, #185, #186, #206 | 6 |
| **Closed** — imagegen generation | #159, #172, #173, #174, #175, #177, #178, #179, #181, #202 | 10 |
| Close — inventory sub-issues | #168, #169, #170, #171 | 4 |
| Close — done or tracked elsewhere | #24, #201 | 2 |
| Re-scope | #176+#200 → one Images view; #167 → one spike; #180 → decide | 4 |
| **Filed** | **#376** (insert an image into a post), **#377** (send images to the model) | 2 |
| File | "Break up CampaignView.tsx" | 1 |
| Do next | #373, #213, #14, #82, #11, #210, #223, #1, #216 | 9 |
