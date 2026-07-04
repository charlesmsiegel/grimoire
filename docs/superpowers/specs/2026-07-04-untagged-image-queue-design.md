# Untagged-image tagging queue

A stepper on the Greetings tab that walks every greeting image still lacking a
subjects entry, so a whole world's art can be tagged in one sitting. Follows
the image-subjects feature (spec 2026-07-04-image-subjects-and-greeting-links).

## Decisions (settled with the user)

- **"Reviewed, no subjects" persists.** An explicit empty list in the
  `subjects.json` sidecar now means "someone looked; nobody's in it" and the
  image never re-enters the queue. Key absent = unreviewed.
- **UX: a focused stepper on the Greetings tab** (rail button
  `▶ Tag images (N)`), one image at a time with the present-cast-first picker;
  Save & next / No subjects / Skip / Close; progress counter. Walks greetings
  in tab order. (Per-row badges rejected — one entry point is enough.)

## Store (`store/image_subjects.py`)

- `write_subjects` **keeps** empty lists instead of dropping them (semantics
  change; the existing drop-empty test flips to assert persistence).
  `set_image_subjects(root, gid, name, [])` therefore records the reviewed
  marker.
- `read_subjects` keeps `[]` entries (chips render nothing; GET returns `[]`).
  The existing behavior of dropping deleted characters can now legitimately
  produce a persisted-`[]` entry — that reads as "reviewed", which is true.
- New: `untagged(root) -> list[dict]` — every stored greeting image whose name
  has **no key** in its greeting's sidecar, as `[{"gid", "name"}]` sorted by
  (gid, name). Scans `greetings/*/assets/default/` directly (images may exist
  where no sidecar does).

## Route

- `GET /api/worlds/{wid}/subjects/untagged` →
  `[{"gid", "greeting_name", "name", "url"}]` — same enrichment as the
  appearances route (greeting names joined from `list_greetings`, url is the
  serving URL). Powers both the rail count and the queue.
- No other route changes: "No subjects" is a normal
  `PUT .../images/{name}/subjects` with `{"subjects": []}` (which now
  persists the marker and removes the image from untagged).

## Frontend

- `api.listUntaggedImages(wid)` → `Appearance[]` (same shape; reuse the type).
- New `TaggingQueue.tsx` component, hosted by GreetingEditor:
  - Props: `{ wid, chars, greetings, queue: Appearance[], onClose(): void,
    onSaved(gid: string): void }`.
  - State: current index, remaining list (skip advances; save/no-subjects
    advances and consumes), done-count for the `k / N` progress line
    (N = queue length at open).
  - Renders: progress line + greeting name, the image (large,
    `max-width: 100%`), the picker inline — present-cast chips first (from the
    image's greeting `present`), search over all characters below (same chip
    interaction as SubjectsPopover; extract the shared chip-picker bits if
    that avoids duplication, otherwise a sibling implementation is fine at
    this size), and the four actions: **Save & next**, **No subjects**
    (PUT `[]`), **Skip**, **Close**.
  - Finishing the last item shows "All images tagged 🎉" with a Close button.
- GreetingEditor:
  - Fetches `listUntaggedImages` alongside its other lists; rail shows
    `▶ Tag images (N)` under `+ New greeting` when N > 0.
  - Clicking it sets a `queueOpen` state; the editor body renders TaggingQueue
    instead of view/form while open. Close (or finish) refetches the untagged
    list and returns to the previous view.
  - `onSaved(gid)`: if the currently selected greeting is `gid`, refresh its
    subjects so chips are current when the queue closes.

## Error handling

- A failed PUT keeps the stepper on the current image and surfaces the error
  banner (existing `.banner` pattern); Skip/Close always work.
- Deleting a greeting or image between queue open and save: the PUT 404s —
  show the error, Skip past it.
- Empty queue: the rail button doesn't render at N = 0.

## Testing

- Store: explicit-`[]` round-trip (write → read shows `[]`); `untagged` lists
  only keyless images (tagged and reviewed-`[]` both excluded), sorted, across
  multiple greetings; greeting with images but no sidecar appears.
- Routes: untagged shape + enrichment; a `[]` PUT removes the image from a
  subsequent untagged call.
- Frontend: rail button shows the count and opens the stepper; Save & next
  PUTs and advances; No subjects PUTs `[]`; Skip advances without a PUT;
  Close calls back; finish state renders after the last item; existing
  view/edit tests unchanged.

## Out of scope

- Per-greeting-row untagged badges.
- Auto-suggestion of subjects from surrounding prose or image content.
- Queue ordering options (plot order, size, random).
