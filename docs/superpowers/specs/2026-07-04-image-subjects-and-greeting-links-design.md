# Image subjects + character-page greeting links

Two features for world greetings and character pages:

1. **Image subjects** — click an image in a world greeting and assign which
   character(s) appear in it. Subjects power an "Appears in" gallery on the
   character page, from which an image can be copied into the character's own
   assets as avatar or gallery art. (Naming: "subjects", deliberately not
   "tags" — tags already mean player-trait gating in greetings.)
2. **Greeting links** — a character's detail view lists every world greeting
   whose present cast includes them, as navigation links (not card content),
   with the ones where they're the primary character flagged.

## Decisions (settled with the user)

- Payoffs: character-page gallery, clean metadata for future play features,
  and a path for characters to get portraits/avatars from greeting art.
- Scope: **greeting images only** (not entity or character-gallery images).
- Picker: greeting's present cast as one-click checkboxes on top, searchable
  all-characters list below.
- Links: one "World greetings" section — all greetings where the character is
  present, in greetings-tab order, ★ on those where they're primary.
- Copy semantics: promoting an image to a character **copies bytes** into the
  character version's asset folder (survives greeting deletion; rides along on
  card export). Existing `put_image`/avatar semantics, including focus reset.
- Storage: sidecar file per greeting asset folder (the `focus.json` pattern),
  not a central index and not greeting frontmatter.

## Part 1 — store: `store/image_subjects.py`

Sidecar at `<world>/greetings/<gid>/assets/default/subjects.json`:
`{"<image-name>": ["<cid>", ...], ...}`.

```python
SUBJECTS_FILE = "subjects.json"

def read_subjects(root, gid) -> dict[str, list[str]]
    # {} on missing/garbled file; drops names that no longer match a stored
    # image and cids that no longer exist (tolerant read, like read_focus)

def write_subjects(root, gid, subjects: dict[str, list[str]]) -> None
    # validates gid/name safety (assets._safe rules); writes trimmed dict —
    # empty lists are dropped; unknown image names rejected with ValueError

def set_image_subjects(root, gid, name, cids: list[str]) -> None
    # read-modify-write of one image's entry

def appearances(root, cid) -> list[dict]
    # scan all greeting sidecars; return [{"gid": ..., "name": ...}] in
    # greetings-tab order (sorted gid, then image name) — cheap: ~100 small
    # files at most
```

Validation: image names checked against `assets.list_images(...,
base="greetings")`; cids checked against `characters` on read (a deleted
character silently drops out of subjects — no dangling chips).

## Part 2 — store: copy an image into a character's assets

`assets.py` already has everything except a byte-copy helper. Add to
`image_subjects.py` (it owns the greeting→character flow):

```python
def copy_to_character(root, gid, name, cid, vid, slot) -> str
    # slot "avatar": put_image(..., "avatar", ...) — existing avatar semantics
    # slot "gallery": next free "gallery_N" name; returns the stored name
    # raises FileNotFoundError if the greeting image doesn't exist
```

## Part 3 — routes (world scope only)

- `GET /api/worlds/{wid}/greetings/{gid}/subjects` → the whole sidecar map
  `{"<image-name>": ["cid", ...]}` — one call renders a whole greeting's chips.
- `GET /api/worlds/{wid}/greetings/{gid}/images/{name}/subjects`
  → `{"subjects": ["cid", ...]}`
- `PUT` same path, body `{"subjects": ["cid", ...]}` → writes sidecar only
  (image bytes remain read-only over HTTP); 404 unknown greeting/image,
  400 unknown cid.
- `GET /api/worlds/{wid}/characters/{cid}/appearances`
  → `[{"gid", "greeting_name", "name", "url"}]` — url is the existing serving
  URL; greeting_name from greetings meta so the UI needn't join.
- `POST /api/worlds/{wid}/characters/{cid}/versions/{vid}/images/copy-from-greeting`
  body `{"gid", "name", "slot": "avatar" | "gallery"}` → `{"name", "ext"}`;
  404 missing source, 400 bad slot.

## Part 4 — frontend: greeting view (assign + chips)

- `GreetingMarkdown` accepts an optional `imageExtras` render-prop:
  `(src) => ReactNode`, rendered in a wrapper under each `<img>`. GreetingEditor
  (view mode only) supplies it; CharacterEditor's card greetings pass nothing
  and are unaffected.
- In GreetingEditor view mode, each greeting image shows its subject chips
  (clickable → `onOpenCharacter(cid)`, the existing prop) plus a small
  "+ subjects" affordance. Clicking the image or the affordance opens a
  popover: present cast as one-click checkboxes on top, then a filter input
  over all world characters. Saving PUTs the subjects route and refreshes.
- Edit mode stays a plain textarea — no popover there.

## Part 5 — frontend: character page

Two additions to CharacterEditor's detail view:

- **"Appears in"** (`.side-section` or a strip under the card): thumbnails
  from the appearances route. Each thumbnail: click → enlarge/preview is out
  of scope; offers three actions — "Set as avatar", "Add to gallery"
  (both call copy-from-greeting for the viewed version, then refresh the
  character's images), and "Open greeting" (below).
- **"World greetings"**: chips/rows for every greeting whose `present`
  includes this character, in greetings-tab order, ★ prefix when
  `greeting.character === cid`. Data from the existing greetings list route.
  Clicking navigates to the Greetings tab with that greeting selected in
  view mode.
- Wiring mirrors `focusChar`: `WorldView` gains `openGreeting(gid)` →
  `setTab("greetings")` + `focusGreeting` state; `GreetingEditor` gets an
  optional `focus` prop that selects that greeting on mount/change.
  `CharacterEditor` gets `onOpenGreeting`.

## Error handling

- Sidecar read is tolerant (garbled JSON → `{}`); writes validate.
- Deleting a character: subjects reads drop its cid (no migration needed).
- Deleting a greeting: its sidecar dies with its folder; appearances no
  longer report it. Copied character images are unaffected (copies).
- Copy-from-greeting is idempotent-ish: re-copying to gallery creates a new
  `gallery_N`; re-copying to avatar overwrites (existing put_image behavior).

## Testing

Backend (pytest): sidecar round-trip incl. tolerant read + validation
rejects; appearances scan across multiple greetings; copy_to_character both
slots (gallery numbering, avatar focus reset); route tests — GET/PUT
subjects happy path + 404/400, appearances shape, copy endpoint, and
greeting image bytes still read-only (PUT image path still 404s).

Frontend (vitest): GreetingEditor view shows chips and opens the popover
(mocked api), saving PUTs; CharacterEditor shows "Appears in" thumbnails and
"World greetings" links with ★ on primary, in order; clicking a link fires
`onOpenGreeting`; WorldView switches tab and GreetingEditor honors `focus`;
card alternate-greetings rendering unchanged (no imageExtras). Existing
read-only-view pattern tests preserved.

## Out of scope

- Tagging entity/character-gallery images.
- Image preview/lightbox, drag-to-reorder galleries.
- Using subjects in campaign play (future; the metadata is the point).
- Auto-suggesting subjects from image content.
