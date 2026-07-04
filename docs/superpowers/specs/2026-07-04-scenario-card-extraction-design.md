# Scenario-card extraction — one-off world population + greeting-image plumbing

Populate an existing world from a single multi-character scenario card (local
PNG/JSON/CHARX file): split the cast into full per-character cards, extract lore
and locations, re-create the card's greetings as world greetings, and localize
each greeting's art. The extraction itself is a supervised one-off (proposal →
review → scripted apply); the only product code added is the minimal plumbing
world greetings are missing today: an asset home for greeting images and a
localize pass for greeting bodies.

A reusable "import scenario card" feature is explicitly out of scope — tracked
as [#943](https://github.com/charlesmsiegel/grimoire/issues/943), which should
absorb the learnings from this pass.

## Decisions (settled with the user)

- **Scope**: one-off extraction + minimal plumbing; no import feature/UI.
- **Greeting art home**: per-greeting asset folder
  `<world>/greetings/<gid>/assets/default/`, the exact pattern locations/lore
  already use (`assets.py` `base=` param; no `assets.py` changes).
- **Card source**: a local card file; art is whatever URLs/data-URIs the card
  text embeds.
- **Character cards**: full drafted cards per cast member (description,
  personality, shared scenario field), synthesized from the card's prose,
  keeping original per-character text where it exists. User reviews drafts
  before anything is written.
- **Target**: an existing world, named by the user at execution time. The
  unsplit scenario card is NOT imported — extraction only.
- **Plumbing depth**: serve + localize only. No upload/delete/promote routes or
  management UI for greeting images (comes with #943 if wanted).

## Part 1 — plumbing (product code, TDD)

### 1a. Serve greeting images through the existing generic routes

The generic entity-image routes already produce the URL shape we want:
`/api/worlds/{wid}/{kind}/{eid}/images[/{name}]` with `kind="greetings"` —
they're just rejected by `_entity_kind_or_404` (`routes.py:960`). Change:

- Add an image-kind guard accepting `ENTITY_KINDS + ("greetings",)`.
- Use it in the two **GET** routes only: `list_world_entity_images` and
  `get_world_entity_image` (`routes.py:991-999`).
- PUT / DELETE / promote keep the strict entity guard — greeting images are
  read-only over HTTP for now.
- World routes only; the campaign entity-image routes are untouched.

Storage resolves through `assets.py` unchanged:
`_dir(root, gid, "default", base="greetings")` →
`<world>/greetings/<gid>/assets/default/<name>.<ext>`, coexisting with
`<world>/greetings/<gid>.md` exactly as entities do.

### 1b. `localize_greeting` in `store/localize.py`

```python
def localize_greeting(root, gid, wid, *, fetch=None, cap=10) -> dict
```

- Reads the greeting (`greetings.read_greeting`), runs `find_refs` on its body.
- Downloads/decodes each ref (same data-URI / http handling, dedup via a
  `seen` map, and download `cap` semantics as `localize_card`), storing bytes
  content-hash-named (`embed-<sha256[:12]>`) via `_store`, which gains a
  `base` parameter (default `"characters"` so `localize_card` is unchanged).
- Rewrites the body with `_apply_field`-style last-span-first splicing; the
  serving URL builder is greeting-flavored:
  `/api/worlds/{wid}/greetings/{gid}/images/{name}`.
- Persists via `greetings.update_greeting(root, gid, body=...)` and returns a
  `{"total", "localized", "skipped", "failed", "capped"}` summary.
- Plain function (no generator/streaming): there is no HTTP route for it in
  this pass; the apply script calls it directly. Best-effort per ref — a
  failed download never raises out of the function.

### Tests (backend, pytest)

- GET list/serve of a stored greeting image works
  (`/api/worlds/{wid}/greetings/{gid}/images/...`); PUT and DELETE to a
  `greetings` kind still 404; entity kinds unaffected.
- `localize_greeting` on a body containing a markdown image URL (fake fetch)
  and a data-URI: file lands under `greetings/<gid>/assets/default/`, body is
  rewritten to the local URLs, summary counts are right; already-local URLs
  and failed fetches are skipped/counted without raising.

Frontend: no changes. `GreetingEditor` already renders bodies with
react-markdown, so localized markdown images display as-is. `npx tsc -b` and
vitest run as regression only.

## Part 2 — extraction (supervised, no product code)

1. Parse the card file with `cards.loads` (handles PNG/JSON/CHARX; sniff via
   `characters._sniff_card_format` if needed).
2. Draft a **proposal document** (scratchpad file + chat summary) containing:
   - **Characters**: one full drafted card per cast member — name,
     description, personality, and a scenario field reflecting the shared
     setting. Original prose kept verbatim wherever it is already
     per-character.
   - **Lore / locations**: entries mapped from the lorebook and setting prose
     — name, keys, body — assigned to `lore` or `locations` by content.
   - **Greetings**: one per `first_mes` / alternate greeting — name, inferred
     primary character (+ version), present cast (via `greetings.present_in`),
     body, and an art inventory (each image ref and its proposed
     destination).
   - **Art routing**: images that are clearly a single character's portrait
     are proposed as that character's `avatar` asset instead of greeting art.
3. User reviews/edits the proposal. **Nothing is written to the store until
   the proposal is approved.** The target world is named here.

## Part 3 — apply (one-off script through the store layer)

A scratchpad Python script run with `backend/.venv/Scripts/python.exe` against
the live store (`store.home()` resolution; world root via the worlds store):

1. `characters.create_character(root, name, card=...)` per cast member
   (card built from `blank_card` + drafted fields).
2. `entities.create_entity(root, kind, name, body, keys)` per lore/location.
3. `greetings.create_greeting(root, name, character, version, body, present=...)`
   per greeting.
4. `localize.localize_greeting(root, gid, wid)` per created greeting.
5. `assets.put_image(root, cid, vid, "avatar", ...)` for proposed avatars.

Properties:

- Store-layer calls only, so ids/frontmatter/uniquify are exactly canonical.
- Best-effort image downloads (same semantics as `localize_card`); the script
  prints every created id and each localize summary.
- **Not idempotent**: re-running duplicates records via `uniquify`
  (`name-2`, …). Run once; recovery from a partial run is manual deletion of
  the printed ids.

## Verification

- New backend tests green: `backend/.venv/Scripts/python.exe -m pytest backend -q`.
- Frontend regression: from `frontend/`, `npx vitest run` and `npx tsc -b`.
- End-to-end: open the target world's greetings page and confirm greeting art
  renders from the local URLs.

## Out of scope

- Reusable import feature, review UI, LLM-assisted extraction routes (#943).
- Upload/delete/promote routes or management UI for greeting images.
- Campaign-side greeting images.
- Asset-folder cleanup on `delete_greeting` (entities have the same gap;
  address uniformly later if it matters).
