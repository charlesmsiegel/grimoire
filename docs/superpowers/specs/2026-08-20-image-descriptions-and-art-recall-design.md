# Image descriptions, and art the narrator can reach for — design

**Status:** approved design, ready for planning
**Issue:** describable images across the galleries, so relevant existing art can
be surfaced during play.

## The problem

Every image in the store is anonymous. `gallery_3.png` says nothing about what
it depicts, so nothing — no reader, no search, and above all no model — can
answer "which of these pictures is the harbour at dusk?". Art accumulates in
character galleries, entity galleries and the campaign's own library, and the
only way back to any of it is to remember it exists and go looking by eye.

The model's side is worse than anonymous: it is *deliberately blind*.
`scenario.strip_images` removes every image reference before text reaches the
extraction prompt, and nothing in `context/` has ever put an image in front of
the model. As far as a narrator turn is concerned, the library's art does not
exist.

## The goal

1. Any image on the four `assets`-backed surfaces can carry a **description**,
   hand-written, with an optional model-drafted first pass.
2. During play, Grimoire ranks described art against the moment and offers the
   closest few to the model as a droppable prompt section.
3. The model may embed one of them in its post by writing a **handle**, which
   Grimoire rewrites into real markdown on the way in. A handle naming
   something it was not offered resolves to nothing and disappears.

## Non-goals

- **Greeting images.** They are the one surface with a tagging sidecar already
  (`image_subjects.py`), and they are still excluded. `PostImagePicker`'s
  docstring gives the reason and it is unchanged: a greeting image's URL is
  world-scoped, so a post carrying one points into the world rather than into
  the campaign, and it is the single image shape that does not follow a
  campaign that later diverges. The supported route stays `copy-from-greeting`,
  which lands the image on the character's own version — where this feature
  then sees it.
- **Campaign covers.** A cover is chrome for the campaigns list, not art in the
  fiction.
- **Generating images.** `sd_prompt` exists on entities and is untouched; this
  feature describes images that already exist.
- **A library-wide image search page.** The ranking module is written so one
  could be built on it, but no such UI is in scope.
- **Making the context builder async.** `semantic.py` already documents why the
  embedding call blocks the event loop and why that is accepted; this feature
  inherits that trade rather than reopening it.

## Architecture

Five new pieces, each on rails that already exist:

| piece | mirrors |
|---|---|
| `store/image_descriptions.py` | `store/image_subjects.py`, itself modelled on `focus.json` |
| description routes on four surfaces | the existing per-surface image routes |
| `store/context/art.py` | `context/semantic.py` (retrieve, bound, degrade) |
| `Section("available_art", …)` | `Section("recalled_lore", …)`, same `pack.RECALLED` tier |
| handle resolution in `_persist_reply` | `turnstate.split_block`, the existing "lift machine-readable output out of a reply" step |

### 1. Storage — a `descriptions.json` sidecar

One sidecar per asset directory, keyed by image name:

```json
{ "avatar": "Seraphine in half-plate, rain-soaked, the keep burning behind her.",
  "gallery_1": "" }
```

It sits beside `focus.json` in `<base>/<id>/assets/<vid>/` for the three
per-version surfaces (characters, pcs, entity kinds) and in
`<campaign>/assets/images/` for the campaign library. Written through
`atomic.write_text` with `indent=2, sort_keys=True`, matching both existing
sidecars.

**Tolerant reads, strict writes** — the rule `image_subjects` states and
`focus` follows. A missing or garbled file reads as `{}`. Entries whose image
has vanished drop out of a read silently, so no description can be offered for
art that is not there. A write refuses a key that is not a stored image of that
folder.

**Absent key ≠ empty string.** Key absent means *undescribed*, and the image
belongs in the authoring backlog. An explicit `""` means *reviewed, no
description wanted*: it leaves the backlog and it is never offered to the
model. `subjects.json` earned this distinction for exactly the same reason —
without it, "I looked at this and it needs no description" is unrepresentable
and the queue never empties.

**Overlay behaviour is inherited, not written.** The file-level overlay already
serves a campaign's copy of a sidecar when it has one and the world's
otherwise. Two edits keep that honest:

- `campaigns/lifecycle.py::_prune_duplicate_files` must not prune a
  `descriptions.json` from a folder that holds any campaign-side image. This is
  the `focus.json` carve-out one step broader: a campaign with a divergent
  `gallery_1` but an as-yet-unedited sidecar would otherwise inherit the
  *world's* description for a genuinely different picture. Broader because
  focus has one key and this has one per image.
- `world_bundle.py::_rewrite_urls`' comment asserts the sidecars under
  `assets/` "hold ids and offsets and contain no URLs". A hand-written
  description is free text and may contain one. The behaviour is right either
  way — rewriting a world id inside a description is what you would want — so
  only the claim needs correcting, rather than being left to quietly outlive
  its truth.

**Deletion.** `assets.delete_image` already clears `focus.json` when the avatar
goes; it gains the matching drop of the image's description entry.
`delete_version_images` needs nothing — it removes the folder, sidecar and all,
and says so.

**Guards** (CLAUDE.md): writes through `store.atomic`, filesystem access
through the resolvers, module-scope acyclic imports, and a classification in
`store/locks.py` — this module mutates campaign-scoped state, so it is
`DOMAIN_MODULES` with its public `cid`-taking mutators taking
`locks.campaign_lock(cid)`, or `test_lock_domain_guard` fails naming it.

### 2. Routes

`GET`/`PUT` of one image's description, on each surface, campaign- and
world-side, beside the image routes that already exist:

```
{world|campaign}/characters/{id}/versions/{vid}/images/{name}/description
{world|campaign}/pcs/{id}/versions/{vid}/images/{name}/description
{world|campaign}/{kind}/{eid}/images/{name}/description
campaigns/{cid}/images/{name}/description
```

Plus one enumeration used by the authoring backlog:
`GET {world|campaign}/images/undescribed` → every stored image with no sidecar
key, mirroring `subjects/untagged`. Route ordering matters the same way it did
there: it must register before the generic `{kind}/{eid}` entity routes.

A `PUT` against an image the surface does not hold is a 404, not a silent
no-op — the strict-write rule surfaced as a status code.

### 3. The candidate pool and the ranking

New `store/context/art.py`.

It lives under `context/` rather than at `store/art_catalog.py`, where this
design first put it, for two reasons that only became clear while building it:
the ranking needs `embed_space`/`vectors` and is the same *kind* of thing
`context/semantic.py` is — store state turned into prompt material — and
`routes/streaming.py` reaches the resolver through `store.context` anyway. The
handle grammar, the section that prints it and the resolver that reads it back
belong in one module, and that module belongs beside the other context
builders.

**The pool is the turn, not the library.** Ranking every described image in a
world of hundreds of characters on every turn is the cost that would sink this.
It is also the wrong answer: art belonging to a character who is not in the
scene should not be offered. So the pool is assembled from what `_assemble`
already has in hand:

- the on-stage `cast` (characters and pcs), at their **locked** versions — the
  version that is actually speaking, which is also the version whose art the
  reader sees;
- the current setting's location, when one is set and not excluded;
- entities activated or recalled into world info this turn;
- the campaign's own image library, always.

Cost therefore scales with the scene for the record half: a handful of small
JSON reads per turn, the same order as `image_subjects.appearances`, which the
store already considers cheap. The campaign's own library is the exception —
it has no record to be in scope through, so it is included whole. Measured, a
300-image described library costs ~9ms to assemble, ~4ms to rank by keyword and
~19ms more to read its cached vectors. Acceptable, and stated rather than left
inside a claim that cost never touches library size.

**Exclusions are inherited, not re-implemented.** An actor removed by a pin
exclude is already out of `cast` before this module sees it; a GM-only location
never becomes the current setting; owner-gated lore never reaches the recalled
list. This module adds no visibility rules of its own, and — as in
`semantic.py` — the absence of an `owners` check here is structural, not an
oversight.

**Ranking: keyword first, semantic as an upgrade.**

- *Keyword* (always available): score the description against the scan window
  using the term rule `search.query_terms` already defines — case-folded
  substring, terms ANDed, quoted phrases intact. A name hit on the owning
  record counts too, so "Seraphine" in the recent text lifts Seraphine's art.
- *Semantic* (when `embed_space.resolve` returns an endpoint): embed each
  description and the scan window and rank by cosine, reusing `vectors.py`'s
  on-disk cache and `embed_space.warm_window`'s rotation, with the same
  bounded-warm and dimension-mismatch-evicts handling `semantic.py` documents.

An endpoint replaces the *scores* — the two are not on one scale, and averaging
them would leave neither threshold meaning anything — but not the **rule that a
record named in the scan window has its art offered**. Wholesale replacement
did, which made "semantic as an upgrade" false: a description that never
mentions Seraphine is not close to a sentence about her either, so configuring
an endpoint silently switched off the commonest reason the feature is useful.
A named record the cosine did not reach keeps a small positive floor, so it is
offered last and only when `depth` has room left.

**It fails to keyword, never to an error**, and when there is no keyword hit
either it returns `[]` and the section does not render. No connection, no
model, a dead endpoint, a rate limit: the turn proceeds. A scene is never worth
losing to an image index.

**Bounds.** `art_catalog_depth` (default 4) caps how many are offered;
`art_catalog_threshold` is the floor a candidate must clear. Both live in
`config.md` beside the recall knobs and are read the same tolerant way
(`_int`/`_float` with defaults, never raising on a hand-edited store).

### 4. The prompt section and the handle grammar

A new entry in `SECTIONS`:

```python
Section("available_art", "Available art", "scene/sections/available_art.j2", pack.RECALLED)
```

`pack.RECALLED` for the reason `recalled_lore` is there: this content is
retrieved *because* the conversation touched on it, and it should be the first
thing to give way when the prompt does not fit. It renders nothing when the
catalogue is empty, which is every turn on a store with no descriptions — so
an install that never uses the feature gets a byte-identical prompt, the
property `semantic.py` calls "with recall off, the prompt is byte-identical to
the one that shipped before any of this existed".

It inherits the prompt-layout controls for free: `context/layout.py` already
lets a reader reorder or disable any section by `Section.id`, and that is the
feature's off switch. No second control surface, per the approved design.

**The handle.** The template lists each candidate as a handle and its
description, and instructs: at most one image per reply, only when it genuinely
fits the moment, and never in place of describing something in prose.

```
[[art:characters:seraphine:gallery_2]]
[[art:locations:harbour:avatar]]
[[art:campaign:coastline]]
```

Three fields after `art:` for a record-owned image (kind, record id, image
name) and two for the campaign library, which has no record. The version is
deliberately **absent**: it is not the model's to choose, and resolution uses
the campaign's locked version — the same one the catalogue was built from.

### 5. The return path

`_persist_reply(cid, sid, text)` is the single funnel for model output — the
four streaming call sites and the greeting-opener adoption all pass through it.
Handle resolution happens there, immediately after `turnstate.split_block` and
before `split_reply`, so no handle ever becomes a post and none is ever stored.

**Resolution does not need the catalogue that was offered**, which matters
because `_persist_reply` takes only `(cid, sid, text)` and is reached from five
places. A handle resolves if and only if:

1. it parses, and names a kind this store has images under;
2. the named image exists and is visible in this campaign — through
   `overlay.image_root`, so a tombstoned or detached image resolves to nothing;
3. it carries a **non-empty description**, so only art an author deliberately
   wrote up is reachable, never any file in the store;
4. its record is one this scene could legitimately show: a `gm-only` entity is
   refused outright, and an actor must actually be cast in the scene.

Rules 3 and 4 are what close the gap that not carrying the catalogue opens.
Without them a model could compose a plausible handle for an image that exists
but was never offered and have it land — and the first draft of this design
asserted that visibility rules were "inherited, never re-implemented" here,
which was simply false: a `gm-only` location's described art resolved, though
its body never reaches a prompt at all.

`secret` is deliberately **not** refused by rule 4. A secret entry's body does
reach the prompt, and the catalogue does offer its art, so refusing it here
would make the two halves disagree about the same picture. What rule 4 does not
re-derive is the scene-scoped part of world-info gating (owner gating, and which
entries activated): those depend on state this function is not given, and the
honest statement of the guarantee is the four rules rather than a fifth that
merely resembles the catalogue's.

A handle failing any rule is deleted from the text, leaving the prose intact.

**At most one picture per reply is enforced here, not merely asked for.** Every
other clause of the contract is a rule resolution applies, so leaving the count
to the model's goodwill made it the one exception — and a model offered four
candidates has an obvious way to use all four. The first *resolvable* handle
wins, so a typo in an earlier one does not silently spend the slot.

**URLs are percent-encoded segment by segment.** `campaign_images.addressable`
keeps the library's names inside what a markdown link can carry; the other three
surfaces have no such rule, and `assets.storable` accepts `art(1)`, `my art` and
`a#b` — each of which ends a markdown destination early and spills the rest of
the URL into the prose. Encoding rather than refusing, because the image is real
and the author's.

What replaces it is campaign-scoped markdown, `![description](url)`, with the
description as alt text — the same choice `PostImagePicker.insertion` makes and
for the same reason: a text-only export, and a model later sent the transcript
as text, get the alt text and nothing else. The URL is bare, with no `?v=`
token, because a `?v=` URL is served `immutable, max-age=1y` and this one is
about to be written into a transcript that outlives every cache.

**A picture does not count against the length budget.** `length_drift._words`
already excludes roll fences — machine-readable output the protocol asked for —
and an image is the same case: the model wrote a ten-character handle, and the
alt text came out of an author's sidecar. Measured, one image added ~7 phantom
words and a whole phantom paragraph to a fifteen-word reply, which under a
`terse` budget is enough on its own to trip the drift correction and tell the
model to write *less prose* for having included a picture.

**Images reach the model as alt text, never as markdown.** Whichever way a post
came by its picture — a reader's pick or a resolved handle — what is stored is
`![description](/api/campaigns/...)`, and `context/story._project_history`
strips that to the description before the history is sent. Two reasons, and
they point the same way: the URL costs ~27 tokens per image on every remaining
turn and says nothing the description does not, and leaving it in is a worked
example of a shape the model must not produce — a handle is validated on the
way back, a raw markdown URL is passed through untouched, so a model imitating
its own history can write a plausible URL for a picture that does not exist.

**Accepted cost: the handle is briefly visible while streaming.** The SSE
deltas carry raw model text, and resolution happens at persist. So a handle
appears in the streaming view and becomes the image when the turn lands. The
alternative — resolving inside the delta stream — means buffering across chunk
boundaries on the most latency-sensitive path in the app, to fix a flicker.
Recorded here rather than discovered later.

### 6. Authoring

**In the galleries.** Each of the four art UIs gains a description field on the
selected image, saved explicitly. The four are `CharacterEditor`, `PCEditor`,
`EntityEditor` and `PostImagePicker`'s library view.

**The backlog stepper.** A `DescribeQueue` component, modelled on
`TaggingQueue`: the image, a textarea, Save / No description / Skip, and a
position counter. Save and "No description" both `PUT` (the second writes
`""` — reviewed, not offered); Skip advances and leaves the image undescribed.

**"Describe it for me".** A button that asks the active LLM connection for a
first draft the author then edits. Human text always wins; nothing is ever
auto-saved.

This is the one place the feature adds a genuinely new capability to the LLM
layer, and it has a sharp edge worth stating: **`claude_agent.py` cannot carry
an image.** It joins `m["content"]` as a string, so a multimodal message
crashes it. The other two clients pass `messages` straight into the JSON
payload, so OpenAI-style content parts (`{"type": "image_url", "image_url":
{"url": "data:…"}}`) work with no client change at all. So the draft endpoint
serves `openrouter` and `openai_compatible` connections and returns a clear,
actionable refusal on a `claude` connection, rather than a 500 from deep inside
the SDK path.

## Testing

- **Store**: `test_image_descriptions_store.py` — tolerant read of a garbled
  file, strict write rejecting an unknown image, vanished images dropping out,
  absent-vs-empty distinction, deletion clearing the entry.
- **Overlay**: a campaign description shadowing a world one; the prune
  carve-out holding a sidecar beside a divergent campaign image.
- **Catalogue**: pool assembly from cast/setting/world-info/library; the score
  floor; the depth bound; `[]` on no embeddings endpoint *with* keyword hits
  still returned; `[]` and no section when nothing clears.
- **Prompt**: the section renders its handles; it renders nothing on an empty
  catalogue; the prompt is byte-identical with the feature unused.
- **Return path**: a valid handle rewrites; an unknown record, a tombstoned
  image, and an image with an empty description each delete the handle; prose
  around it survives; no handle is ever persisted.
- **Frontend**: the description field saves; the stepper advances on all three
  buttons; "No description" persists an empty string. Per CLAUDE.md, every
  `await` is a settle point, and shared scaffolding goes in `src/testkit/`.
- **Evals**: `evals/run.py` renders real templates and requires instruction
  text verbatim in the assembled prompt. The art instruction joins that set, so
  rewording it fails there rather than silently everywhere else.
- **Gates**: `make check` — and because the three lint gates are ratcheted,
  `make baseline` runs with the change and the smaller baselines are committed
  alongside it.

## Risks and accepted costs

| risk | disposition |
|---|---|
| The model over-reaches for art, and every post grows a picture | The instruction caps it at one per reply and the depth bound caps the offer at 4. If play feel suffers, the prompt-layout switch turns the section off without a code change — and `_assemble` skips the ranking entirely when it is off, so the switch turns off the cost too, not merely the output. |
| A handle is visible during streaming | Accepted, documented above. |
| Semantic ranking blocks the event loop | Inherited from `semantic.py`, which documents it; the same tight timeout and vector cache apply, and the layer is skipped entirely with no endpoint configured. |
| Absorb and the summary prompts still see the full markdown | `chronicle.transcript_text` serves both those prompts and the plain-text export, so stripping there would cost the export its pictures. The waste is bounded and one-off (~27 tokens per image, per absorb, not per turn), and the imitation risk does not apply — those calls return JSON and summaries, not transcript posts. Left, and named. |
| A fork's posts point at the campaign they were written in | Pre-existing and already documented in `store/campaign_images.py` — the serving URL carries a campaign id and `store.fork` copies text verbatim — but this feature makes the shape common rather than occasional, so it is worth restating. Exports are unaffected: `export._resolve_image` resolves against the campaign being exported, not the id in the URL. Only the app's own `<img>` follows the id, and only a *deleted* source breaks it. |
| Descriptions drift from the art they describe | Nothing detects this. An image replaced under the same name keeps the old description — the same way `focus.json` keeps a crop. Stated, not solved. |
| The vision draft is unavailable on Claude connections | A clear refusal, not a crash. Widening `claude_agent.py` to multimodal is a separate change. |
