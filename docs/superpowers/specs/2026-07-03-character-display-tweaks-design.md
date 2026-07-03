# Character display tweaks — design

2026-07-03

Three tweaks to how characters are displayed:

1. Clicking the profile portrait on the character detail page opens a picker to
   choose **which square region** of the avatar image is displayed.
2. Character-list cards show **badges** for the number of gallery images and
   localized images.
3. Greetings on the character detail page render as **markdown**, so localized
   images display inline.

## 1. Avatar crop focus

All square avatar renders use `object-fit: cover`, which spans the image's
short side and crops along the long axis. "Choosing the square" therefore means
choosing where along the long axis the square sits. The crop is
**non-destructive**: a focus value 0–100 (percent along the long axis; default
50 = today's center crop) stored as metadata. The image file is untouched.

Switching *which* image is the avatar is already covered by the existing
promote buttons and is out of scope.

### Backend

- The assets store (`store/assets.py`) reads/writes a sidecar `focus.json` in
  the version's assets dir (`characters/<cid>/assets/<vid>/focus.json`), shape
  `{"avatar": <int 0-100>}`. `list_images` must skip the sidecar.
- Character detail response: each version gains optional `avatar_focus`.
- Character list response: `CharacterSummary` gains optional `avatar_focus`
  (default version's value) so list cards can apply it.
- New endpoint: `PUT /api/worlds/{wid}/characters/{cid}/versions/{vid}/images/avatar/focus`
  with body `{"focus": <0-100>}`.
- **Reset on image change:** promoting a gallery image to avatar or uploading a
  new avatar clears the stored focus — the old crop referred to different
  pixels.

### Frontend

- Clicking the detail-page profile portrait opens a modal (tagline-modal
  pattern: fixed backdrop + centered panel) showing the full avatar at natural
  aspect ratio with a square viewport overlay. The overlay drags along the long
  axis (orientation known from `naturalWidth`/`naturalHeight`); its position is
  the focus value. Save persists via the new endpoint and updates local state;
  Cancel discards.
- Rendering: with `object-fit: cover`, only the long axis has crop slack, so an
  inline style `object-position: ${f}% ${f}%` is correct for both tall and wide
  images with no orientation logic at render time. Applied (when a focus value
  exists) to:
  - the detail profile `<img.detail-avatar>`,
  - the char-card avatar `<img.char-card-avatar>`,
  - `Portrait.tsx` via a new optional `focus` prop, passed wherever the caller
    already has the data. Campaign-scoped endpoints are out of scope unless the
    value already flows there.

## 2. Character-card badges

- `store.characters.list_characters` adds per-character `gallery_count` and
  `localized_count`, counted from the **default version's** assets by name
  prefix: `gallery_*` and `embed-*` (localize stores images as
  `embed-<sha256[:12]>`). The avatar is never counted.
- Each `.char-card` renders a badge row (small chips) under the name/tagline:
  `N gallery` and `N localized`. **A badge renders only when its count > 0**,
  so a character whose only asset is the avatar shows no badges.

## 3. Markdown-rendered greetings

- On the character detail page, the **first message** (`first_mes`) and each
  **alternate greeting** render through `<Markdown remarkPlugins={[remarkGfm]}>`
  (the app-standard `.detail-rendered` treatment) instead of plain text.
  Localized images are markdown, so they render inline. react-markdown drops
  raw HTML, which defers the general handle-HTML-in-cards question.
- `first_mes` is special-cased out of the plain-text `TEXT_FIELDS` detail loop;
  all other card fields (description, personality, scenario, mes_example, …)
  stay plain text for now.
- Alternate greetings keep their blockquote framing with markdown rendered
  inside.

## Testing

Backend (pytest, `GRIMOIRE_HOME` isolated):
- Focus round-trip: PUT then read detail → `avatar_focus` present; list →
  default version's focus present.
- `focus.json` never appears in `list_images` output.
- Promote / avatar re-upload clears focus.
- List counts: gallery and embed assets counted correctly; avatar-only
  character reports zeros.

Frontend (vitest, run from `frontend/`):
- Clicking the profile portrait opens the crop picker; Save calls the API and
  the portrait gets the `object-position` style.
- Cards render badges with counts; zero-count badges are absent; avatar-only
  character shows no badges.
- A greeting containing `![img](url)` renders an `<img>`; other card fields
  remain unrendered plain text.
