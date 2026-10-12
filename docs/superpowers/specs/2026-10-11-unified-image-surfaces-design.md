# Unified image viewing and record art

## 1. Intent and status

Images should be easy to discover while reading a record, large enough to
inspect, and managed through the same controls wherever they belong. A reader
should be able to examine a character portrait, an item illustration, a
location map, or a lore document without learning a different image interface
for each record type.

The unified record-art sources are **Characters, PCs, Locations, Lore, Items,
Groups, and Creatures**. All seven are peers in one viewing and management
system, using the same panel, viewer, operations, and API contract. Greetings
have a separate art workflow because their images are embedded in greeting
content and governed by subject tagging rather than the same primary/gallery
management contract. This document specifies the complete seven-source system;
it does not authorize implementation or replace the repository's review gates.

**Status:** approved by the user; the adversarial spec gate approved planning
on 2026-10-11 after two description-conflict findings were resolved. That gate
reviewed the complete supplied diff; its local repository reader was unavailable.
The description-cleanup clarification also passed the combined spec/plan review.
Source behavior was checked against
`origin/main` at `f816921c8`. This is a source-based review; no rendered browser
walkthrough was available during drafting.

Success means:

- Every one of the seven record sources exposes art through the same entry point,
  including on a phone and before its first upload.
- Clicking an artwork opens the same accessible viewer, preserving the complete
  picture and the reader's place in the app.
- Upload, reuse, primary selection, preview crop, descriptions, and removal
  have the same interaction contract across record types.
- World inheritance, campaign overrides, character versions, shared metadata,
  legacy images, and existing narrative image references retain their meaning.
- Failed reads and writes are visible and cannot masquerade as an empty gallery
  or a successful operation.
- Greetings retain their dedicated rendering, subject-tagging, localization,
  and existing character-copy workflow outside the unified record-art system.

## 2. Existing behavior and constraints

These are repository facts, not observations of a private library.

| Surface | Existing implementation | Current distinction |
|---|---|---|
| Character | `CharacterPage.tsx`, `character/ArtTab.tsx` | Art tab; square tiles; world/inherited/shadowed art; other world versions; greeting appearances; batch upload; main portrait opens crop adjustment |
| PC | PC detail route, `pc/PCArtTab.tsx` | Art tab; separate shelf implementation; single-file upload; crop and removal |
| Location | `EntityEditor.tsx`, kind `locations` | Primary image, rail preview, and gallery above the body; wide tiles |
| Lore | `EntityEditor.tsx`, kind `lore` | Same entity image surface as locations |
| Item | `EntityEditor.tsx`, kind `items` | Same entity image surface as locations |
| Group | `EntityEditor.tsx`, kind `groups` | Same entity image surface as locations |
| Creature | `EntityEditor.tsx`, kind `creatures` | Same entity image surface as locations |
| Combined gallery | `ImagesView.tsx` | Characters, PCs, entities, greetings, and libraries; a separate full-image detail implementation |
| Record drawer | `RecordDrawer.tsx` | Actor portrait; location drawer reads the body but does not load location art |
| Inline art | `markdown/MarkdownImage.tsx`, `GreetingMarkdown.tsx` | Markdown images; collections have occurrence-specific selection and previous/next/reroll controls |

`ENTITY_KINDS` is `locations`, `lore`, `items`, `groups`, and `creatures`.
Entities use the internal asset version `default`; it is not an edition of the
record and should not be shown to the reader as one.

The existing shelves generally link originals into a new browser tab. Character
tiles are 96px square crops; entity tiles are 154px wide crops. Controls differ:
the character shelf exposes removal only for its avatar, the PC shelf for every
image, and the entity shelf exposes no removal control. Entity image-list errors
currently become `[]`. Entity image management also appears in its read-only
record view before the prose.

The content-addressed image store already separates image objects from their
placements. Image bytes, descriptions, and subject tags are shared; preview
focus belongs to a placement. Descriptions distinguish an absent value from
an explicit empty string. Campaign reads must use the overlay. Promotion swaps
the former primary into the selected gallery slot and is journalled. Removing
a placement does not collect its image object. This design builds on those
contracts and preserves their legacy fallback rules.

## 3. Scope and chosen approach

### 3.1 Required scope

The following roster defines both the sources and destinations of unified
record art. Every row receives the complete common contract in world and
campaign scope.

| Source | API kind | Art belongs to | Primary label |
|---|---|---|---|
| Characters | `characters` | Selected character version | Avatar |
| PCs | `pcs` | Selected PC version | Avatar |
| Locations | `locations` | Location record | Primary image |
| Lore | `lore` | Lore record | Primary image |
| Items | `items` | Item record | Primary image |
| Groups | `groups` | Group record | Primary image |
| Creatures | `creatures` | Creature record | Primary image |

All seven use the same browse/manage modes, Art entry point, contained gallery,
full-image viewer, multi-file upload, existing-art reuse, primary selection,
preview crop, description editing, usage display, removal, provenance, and
error behavior. The permitted differences are version addressing, the primary
label and preview aspect, and record-owned related sections. Description
drafting follows the same world-only endpoint availability rule for all seven.
The seven-source feature is reviewed and accepted together; coverage of all
seven is a completion requirement.

Greetings are outside this roster. They receive no generic record Art tab,
record-art API target, or ordinary primary/gallery controls. Their editor,
subject tagging, tagging queue, localization, and greeting-image rendering
remain separate. Existing character **Appears in** and copy-from-greeting
actions remain a specialized bridge: once copied into a character placement,
the destination image is managed by the common character Art panel. Greeting
placements themselves are not candidates in the generic existing-art picker.

The complete feature consists of:

1. A shared image viewer and shared gallery tiles.
2. One shared record art panel covering all seven roster entries, in world and
   campaign scope.
3. Consistent record entry points and an explicit image-management mode.
4. Multi-file uploads, reuse of existing visible art, primary selection,
   placement-specific crop adjustment, descriptions, and removal.
5. Viewer integration with the combined gallery, world art library, existing
   record drawers, and non-greeting Markdown images.
6. Combined-gallery text search and links into the owning record's Art panel.
7. The additive API support necessary for provenance, safe append/reuse, entity
   focus, and guarding mutations against a changed image.

Existing world-art uploads/removal and library behavior remain available, using
shared display primitives where applicable. Libraries can supply reusable art
but are not an eighth record kind. No greeting-specific viewer or tagging-queue
redesign is required by this feature.

### 3.2 Choice of interface

The chosen approach is a compact primary preview and **Art** entry point in
the record, a reusable Art panel, and a reusable viewer. It keeps prose readable
while making both viewing and management discoverable.

Two alternatives were considered:

- Expanding the entity's inline shelf would make images larger, but would keep
  management above the prose and leave character and entity workflows distinct.
- Sending all art work to the combined Images page would centralize controls,
  but would take the reader away from the record and make version/campaign
  context harder to retain.

Unification applies to the image interaction contract. Record-specific tabs,
metadata, greeting relationships, and page navigation remain owned by their
existing pages.

### 3.3 Excluded work

No image generation, recognition, semantic image search, new accepted file
formats, freeform photo editing, arbitrary cropping of stored bytes, collection
authoring redesign, cross-world asset browser, automatic migration or garbage
collection, or new persistent image database is introduced. Cover editors and
the composer image picker are not redesigned. Existing record-navigation
portraits in lists, cast columns, and transcript speaker plates keep opening
their records; this feature does not change those navigation controls into
image-viewing controls.

## 4. Record presentation and navigation

### 4.1 Common entry point

Every entry in the seven-source roster has an **Art** destination, with the effective
current-version image count once it has been read. Art is visible at zero
images. An unknown count draws no numeric badge; a failed read cannot draw zero.

Characters and PCs keep their existing tabs and use the shared panel for Art.
Entity read views gain **Details** and **Art** tabs within `editor-body`; their
record index and metadata sidebar retain the project's list/detail pattern.
Details is the default destination. Entity prose editing stays the explicit
Edit flow, and Art management is a separate explicit flow.

The count includes all effective placements displayed in this record/version's
main art shelf: primary, numeric gallery slots, and other addressable image
slots. It does not include shadowed world originals, other versions, or greeting
appearances. An inline body occurrence does not itself add to the count; if its
localized source is also a placement in this shelf, that placement counts once.
Duplicate placements of the same picture count separately because the
operations act on placements.

A primary preview sits near the identity/title. A character/PC uses its existing
identity portrait; an entity uses a compact preview rather than duplicating
the primary in a management shelf above the body. If there is no primary but
there is gallery art, the first gallery image can supply a browsing preview,
labelled as a preview rather than claimed as the primary. An empty record uses
the existing text/initials treatment and still offers Art.

Clicking an artwork preview opens the viewer. **Art** opens the panel. **Adjust
preview crop** is an explicit action in management; clicking a character's
identity portrait no longer opens crop adjustment. Existing record-navigation
portraits, named in section 3.3, are a separate interaction and remain unchanged.

### 4.2 Mobile

At widths where `PageShell` turns the context column into a sheet, the main
record area includes the preview and a visible **View art** action. Reuse
`MobileArtEntry` rather than inventing another phone-only interaction. The
action works for primary-only, gallery-only, and empty records. Art and its
management controls remain reachable without opening the context sheet or
horizontal scrolling to find a tab.

The viewer uses the available viewport on phones. Management controls stack
below the selected image with usable touch targets. The gallery does not force
page-width horizontal scrolling, and its minimum tile width shrinks to fit a
narrow pane.

### 4.3 Addresses and history

Art is bookmarkable using `?tab=art` on existing record URLs. Actor URLs can
also carry the selected version; extend `worldPaths.SectionTarget` and its
parser deliberately for PCs as well as characters. Entity URLs do not gain a
version modifier. All producers continue to use `sectionHref` or its owning
page helper; components do not concatenate record paths.

Tab and version changes update the current history entry so exploring one
record does not create a Back step for each image or tab. Navigating from the
combined gallery to a record creates a normal history entry, carrying the
version when the image belongs to an actor version. Back returns to the gallery
with its filters, selected placement, and scroll position restored.

Viewer open/close, zoom, and image stepping are transient state. They do not
rewrite the record address or require a new route. Changing record, scope,
version, or route closes its viewer and exits management. A dirty description
or crop is handled by section 7.1 before a user-initiated context change proceeds.

## 5. Shared image viewer

### 5.1 Content and sequence

The viewer receives an ordered sequence of display descriptors, an initial
placement/occurrence key, and the invoking element. It does not discover
records, infer permissions from an image URL, or perform record mutations.

The image area displays the complete image using its natural, browser-corrected
orientation. Fit mode contains the image within the available area with no
mandatory crop or stretching. A small image is not enlarged beyond natural
size in initial fit mode. Originals preserve animation where the stored format
supports it.

Navigation follows the sequence the reader was looking at:

- A record shelf: primary first, gallery slots in numeric order, then any other
  addressable record-image names in natural name order.
- A separate inherited/original/other-version section: only
  that section, in its displayed order.
- Combined gallery: the filtered record/library placements in displayed order;
  greeting entries keep their separate gallery workflow.
- Ordinary inline image: that occurrence alone.
- Inline collection: the available members in manifest order, starting at the
  currently rendered member; surviving members keep their canonical indices.

Previous/Next stop at the ends; they do not wrap. A one-image sequence omits
those controls. A counter announces the selected position and sequence length.
Navigation is by placement/occurrence key, never merely by `image_id`, because
one image can appear several times with different provenance.

### 5.2 Viewer controls

Required controls are Close, Previous/Next where applicable, Zoom in/out, Fit,
100%, and Open original. The image can be panned when it exceeds the image area;
pan is clamped so it cannot be lost completely offscreen. Zoom buttons and a
visible percentage offer the same capability without gestures. Zoom ranges
from fit to 400% of natural size; selecting another image resets zoom and pan.
Pinch zoom and drag panning work on touch devices. A single-image drag is not
also interpreted as next/previous; stepping remains explicit controls.

**Download** is provided for local images through a response that supplies an
appropriate filename and attachment behavior. Remote Markdown images keep
Open original but do not pretend a cross-origin `download` attribute can
guarantee a download. **Open record** appears where a descriptor has an owning
record; the viewer itself never exposes crop, deletion, or description saves.

A readable information area shows the supplied record/occurrence label, image
description and review status, primary role, actor version where meaningful,
and provenance. It is collapsible on a phone. Object usage is fetched only when
**Used in** is opened, through the existing `ImageUsage` behavior.

### 5.3 Accessibility and overlays

The viewer is an accessible modal dialog with a name, `aria-modal`, initial
focus, a focus trap, and background interaction blocked. Closing restores
focus to the invoking element; if it has disappeared, use the owning shelf or
record heading. Background scroll is restored to its previous position.

Keyboard bindings use `useHotkeys` with modal ownership: Escape closes;
Left/Right step; labelled zoom/fit bindings mirror enabled controls. Keys are
not intercepted from editable fields or controls that use them, and no new
window key listener is introduced. Controls have meaningful accessible names;
descriptions or record/occurrence labels supply alt text instead of a bare
`gallery_1`.

An image viewer opened from an existing drawer or queue owns the top overlay.
Escape closes only the viewer, after which the underlying overlay regains
focus and keyboard ownership. Unsaved management forms do not sit underneath
an automatically opened second viewer; selecting artwork while such a form is
dirty first follows section 7.1.

### 5.4 Loading and failure

Only the active image loads its original. Shelves use the existing thumbnail
pipeline, lazy loading, version tokens, and async decoding; no shelf fetches
all originals or preloads neighboring originals. Previous images are detached
when stepping, and temporary object URLs are revoked when their owner releases
them. The viewer may retain a loaded thumbnail while its original loads.

Failure shows **Image unavailable**, the supplied label, and Retry. The reader
can still close, step, or open the original. An unavailable member is not
silently substituted with a different image. Ordinary missing images remain
in their viewer sequence until the parent refresh changes that sequence.

If the parent refresh removes the selected placement, the viewer closes and
reports that the image is no longer available. It never switches silently to
a different picture under the same old selection key. A changed version token
for a retained placement reloads that image and resets zoom/pan.

## 6. Shared record art panel and tiles

### 6.1 Browsing

Art initially opens in read-only browsing mode. A responsive tile grid shows
effective images; the selected primary has a visible **Avatar** badge for
characters/PCs or **Primary image** for entities. Tile metadata uses readable
labels and a short description; internal slot names can appear in management
details but are not the only accessible or visible label.

Art thumbnails use a common contained image area, nominally 160px wide on a
desktop, so a portrait, map, document, or narrow item can be recognized without
a forced square or landscape crop. The grid adapts down to the pane width.
Primary identity previews may still be square portraits for actors and 16:10
previews for entities. Those previews, not the contained gallery, use placement
focus. The full viewer always contains the original.

The panel offers **Manage images**. Empty browsing says **No images yet** and
offers **Add images**, which enters management and opens the file chooser.
There is no tile pretending a failed listing is an empty shelf.

### 6.2 Provenance and specialized sections

Use clear badges rather than requiring the reader to infer provenance from
where bytes live:

- **World** on world-scoped images.
- **Campaign** on placements this campaign owns.
- **Inherited from world** on effective images resolved through the overlay.
- **World original** on a world placement hidden by this campaign's same-named
  placement. It is browsable here and its mutation controls are absent.

The effective shelf contains both campaign-owned and inherited images, with
their badges. A separate **World originals** disclosure contains shadowed
images. Existing character **Other world versions** remains a read-only related
section using the common viewer. **Appears in** remains a separate greeting-art
section with its existing viewing, greeting links, and copyability restrictions;
it is not another common record-art shelf. The Art badge counts only the
effective shelf, as section 4.1 defines.

An inherited image is actionable through campaign routes where those routes
support the operation. Mutating its placement creates a campaign change; it
does not edit or delete the world placement. Editing its description is a
shared image edit, with the distinct explanation in section 7.5. A world
original or a version this campaign no longer holds is read-only here.

Subject tagging remains a greeting capability. The common panel does not offer
subject tagging, localization, generated descriptions, or record mutations
merely because an image has a URL. Actor-version art never leaks into another
version's effective shelf through the shared component.

## 7. Management contract

### 7.1 Mode and saves

Management exposes explicit actions but does not put all descriptions into
textareas. Selecting **Edit description** or **Adjust preview crop** opens one
editor in the management details area, with Save and Cancel. Only one unsaved
editor is open at a time. Moving to another image or closing management with
dirty text/focus asks whether to discard that local draft; cancelling that
decision preserves it.

Upload, reuse, primary selection, removal, and a description/crop Save are
immediate persistent actions. **Done** exits management; it is not a commit
button and does not roll back actions already saved. Description/crop Cancel
discards only the unsaved local draft. Buttons say what they save, and a
short management hint makes the immediate-save behavior explicit.

While a mutation is pending, conflicting actions for the same record/version
are disabled. An operation captures its scope, record, version, slot, and
expected image token at start. A late result cannot repopulate a different
record or version, overwrite a new form, or put its success/error banner there.

### 7.2 Upload

**Upload images** accepts multiple PNG, JPEG, GIF, or WebP files using the
existing ingestion/sanitization contract. It attempts each selected file in
order, shows progress, and reports individual failures without abandoning files
after a failed one. Retrying selects failed files deliberately rather than
automatically repeating successful uploads.

An empty effective primary takes the first successful upload; later successes
append gallery placements. A failed first file does not prevent a later
successful file from becoming the primary. Existing primary art is never
replaced implicitly by uploading.

The server allocates the placement, rather than trusting a client-side count.
Gallery allocation uses the highest occupied numeric slot plus one, inspecting
the effective world/campaign shelf and unresolved local placements. Deleted
gaps do not cause another image to be overwritten. Concurrent appends to the
same destination produce distinct slots and cannot overwrite a primary or
gallery picture that appeared while an upload was being processed.

Completion refreshes the art read and affected row/identity previews with fresh
version tokens. Refresh failure after a successful write says **Saved, but the
gallery could not be refreshed** and offers Retry; it does not call the upload
failed or silently show stale art as the final state.

### 7.3 Choose existing art

**Choose existing art** opens a picker scoped to the world, or to the effective
campaign gallery when in campaign scope. It includes visible placements from
all seven record sources and the image libraries, with kind filters and text
search over record names and already supplied descriptions. Greeting placements
are excluded; their specialized character-copy workflow remains separate. The
picker does not enumerate the
global object store or expose another world's assets. Hidden/tombstoned images
and world originals excluded from the effective campaign gallery are not picker
candidates; existing specialized character greeting-copy actions remain.

The picker chooses one image per action. **Add to gallery** creates a destination
placement. **Use as avatar/primary** adds it to a free gallery slot and promotes
it, preserving the former primary by the existing swap contract. Neither action
rewrites the selected image's bytes or shared description. No crop is copied
from the source placement: the destination starts centered.

The request names the source placement and the image version the reader saw,
not a supplied filesystem path or an arbitrary image id. The server resolves
it again within the allowed scope, verifies it is still visible and arrived,
then links the resulting object. A legacy image may be ingested once for the
link, without deleting or rewriting its source file. A legacy-only description
is carried using the existing legacy fallback convention rather than silently
overwriting a global description; its effective metadata scope is reported.

A missing, hidden, changed, or unarrived source produces a visible refusal and
no destination placement. Selecting the already effective primary for **Use
as primary** is a no-op. Other repeated placements remain possible; reuse is
not a mandate to deduplicate a record's intentional uses.

### 7.4 Primary and preview crop

**Set as avatar/primary** on an existing gallery image uses the existing
journalled promotion operation: the previous primary becomes the selected
gallery slot. Primary selection never discards the previous artwork. The
result is re-read before primary badges or previews claim success.

**Adjust preview crop** is available only for the effective primary. It changes
placement focus, not the original pixels. Reuse the crop picker with a label
appropriate to the record type and a preview matching the actual identity
aspect: square for actors, 16:10 for entities. The existing focus value remains
a single 0-100 position along the axis with crop slack; the crop window is
derived from image and target-preview aspect, not assumed square. Gallery and
viewer display are unaffected. This is not a freeform crop, rotation, or edit
to the stored image.

An inherited primary's crop is a campaign placement override and leaves world
focus unchanged. Changing to different primary art resets focus under the
existing rule; reusing the identical image in an unchanged primary can retain
its existing focus. Entity primary summaries and serving reads must supply
effective focus so the header and rail actually reflect the saved adjustment.

### 7.5 Descriptions and usage

Reuse `ImageDescriptionField`'s three states: unreviewed, described, and
deliberately undescribed. **No description** saves an explicit empty string,
leaves the describe backlog, and does not publish an art handle. Text Save
retains existing validation and art-recall behavior. Merely viewing an image
does not mark it reviewed or offer it to the model.

For placement-backed shared metadata, the editor says **This description
applies wherever this image is used.** The existing on-demand **Used in**
report is available beside it. For a legacy-only local description, the editor
says it currently applies to that legacy placement; the UI does not make a
global claim the fallback store cannot uphold. The description save follows
the existing resolver and reports its resulting scope after refresh.

Description editing captures a separate `description_rev` when the editor
opens, alongside the image's byte token. Background refresh never advances
that captured revision while the draft is open. Saving a stale description
returns 409 `stale_description`, preserving the draft and showing the current
description/review state for deliberate reconciliation. Reloading the picture
does not silently authorize overwriting a newer description.

When legacy text masks an image-backed shared description, the editor also
shows the underlying shared write-target state before Save, making clear that
Save updates that shared description and clears the applicable legacy override.
On conflict it shows both the current effective legacy state and the current
shared state, including explicit-empty or unreviewed values. The reader must
explicitly reconcile against the displayed shared value before the draft adopts
a new expected revision; reloading unchanged legacy text alone is insufficient.

Generated description drafting remains available only through an existing
supported route, presently world-scoped for these record surfaces. Drafting is
an explicit user action, retains the existing detached-run and inference
contracts, and fills an unsaved editor; accepting the draft is a separate Save.
Browsing, opening usage, or choosing existing art starts no model call.

### 7.6 Removal

Every actionable primary or gallery placement offers **Remove from this
record**. Confirmation names the record and placement role, explains that the
picture remains in other places using it, and states that existing narrative
links to this placement can stop working. Campaign confirmation also says that
removal hides an inherited world image in this campaign. A world original or
other read-only image has no removal action.

Removal uses the owning record's delete/overlay route. It removes the placement
or creates the existing tombstone; it does not delete the shared object, run
garbage collection, remove other placements, or automatically choose another
primary. A gallery-only record remains gallery-only after primary removal.

The usage report counts placements, not arbitrary URLs embedded in prose, so
it must not claim that no narrative references exist. Primary promotion can
also change what a narrative link under `avatar` or the swapped slot displays;
the primary-selection control includes a short explanation that those named
links follow the slot. This work does not rewrite stored narrative links or
promise image-id-stable historical art.

## 8. Combined gallery, libraries, drawers, and Markdown

### 8.1 Combined gallery

`ImagesView` retains kind, unfinished, and campaign-appearance filters. A text
search matches record name, displayed version/label, and supplied description,
case-insensitively; it is a local filter over the gallery read, with no new
semantic index or model call. Search and existing filters compose.

Clicking a record/library gallery tile opens the shared viewer rather than
another full-image detail implementation. **Open record / Manage images** navigates to the
owning record's Art destination, carrying actor version and campaign scope.
The destination opens browsing; management still requires its explicit action.
World-library images navigate to World art; campaign-library images navigate
to their existing campaign-library management entry point, not to a nonexistent
campaign world section. Greeting entries retain their existing gallery detail
and subject information, with navigation into the dedicated greeting
editor/tagging workflow. They never navigate into generic record Art management
or join its viewer sequence. Unsupported destination kinds get no guessed link.

The gallery itself does not add independent copies of record mutation handlers.
An image without a description and one deliberately left blank remain distinct,
and greeting unfinished state continues to use subjects rather than an
unavailable greeting-description workflow. Empty results say **No images match
these filters**; the unfiltered empty state says there is no art yet.

### 8.2 World library and existing drawers

World-art tiles use the shared contained tile and viewer. Existing library
upload, description, usage, and removal contracts remain; this feature does
not give a library image an actor version or a primary role.

Existing actor and location drawers expose their effective primary, or a
gallery preview when no primary exists, and **View art**. Location art is read
through the campaign overlay. Loading or failing to read art does not suppress
the record body. Full art is loaded on request; the drawer need not read every
gallery's originals merely to display a portrait. Opening its viewer preserves
the drawer, transcript position, and existing scene-specific actor version.
There is no expansion of `DrawerTarget` to unrelated record kinds in this work.

### 8.3 Markdown and collections

Non-greeting Markdown image call sites opt into the shared viewer entry point.
`GreetingMarkdown` keeps its dedicated greeting rendering and image extras.
Its existing use of `MarkdownImage` does not implicitly enroll greetings into
the new workflow: viewer participation is an explicit stable renderer/context
option, with greeting call sites opting out. Existing image extras, subject
buttons, collection controls, Markdown links, and stream-stable component
identity are preserved in both rendering paths.
Interactive viewer controls must not be nested inside another button or link;
linked Markdown images receive a separate accessible enlarge control when
wrapping would interfere with their link.

Collection enlargement starts with the currently selected concrete member,
not a new random draw. Viewer stepping is temporary and does not change the
inline occurrence's selected member, reroll it, or mutate the immutable
manifest. Two occurrences of the same collection continue to own independent
selection state. Only available canonical member indices are offered. If the
collection manifest read failed, a rendered fallback image opens as one image.

Ordinary remote HTTP(S) images are displayed using their existing browser URL;
the viewer does not create a server proxy, fetch a remote file into the store,
or infer editable local records from a remote URL. Unsupported enlargement
sources retain their ordinary Markdown rendering. Re-rendering a streamed
paragraph must not close the viewer or reroll collection selection; a true
occurrence/context change may close it under section 5.4.

## 9. Component and read contracts

### 9.1 Boundaries

Use focused shared components under `components/art/`:

| Unit | Responsibility | Dependencies |
|---|---|---|
| Image tile | Contained thumbnail, label, role/provenance badges, open action | A display descriptor and callbacks |
| Image viewer | Sequence, fit/zoom/pan, modal lifecycle and read-only information | Display descriptors, shortcut registry, optional lazy usage |
| Record art panel | Browse/manage modes, sections, selection and mutation feedback | A record art read and its scoped adapter |
| Existing-art picker | Scope gallery, filters, selection, reuse action | Gallery read and explicit destination adapter |
| Record adapters | Existing/new API calls, URL builders, refresh and capability mapping | Scope, kind, record, actor version where applicable |

One server-owned `ART_RECORD_KINDS` roster is composed from characters, the PC
asset base, and `ENTITY_KINDS`; it excludes greetings. Read validation, append,
reuse, common operation support, and the frontend's exhaustive `ArtRecordKind`
type share that contract. Adapters translate addressing and record-owned
related sections; they do not implement separate character, PC, location, lore,
item, group, or creature galleries or mutation state machines. Missing support
for any roster entry is a failed requirement, not a silent capability omission.
Greeting components are outside this roster and cannot become another adapter
by supplying a URL or the internal `default` version.

Display descriptors include a stable occurrence key; original and thumbnail
URLs/version tokens; human label and accessible text; optional image object id,
dimensions, owning-record destination, description/review state, metadata scope,
primary role, and provenance. A viewer descriptor is not a mutation target.
Management descriptors additionally hold a typed owning placement and explicit
supported operations; a missing handler withdraws that operation.

Do not turn `Portrait` into an image manager or attach global behavior to every
`img` element. Keep `Portrait` for navigation/identity previews and explicit
viewer entry controls for artwork. Do not retain three independent copies of
the same art shelf; character and PC wrappers supply their specialized sections
to the shared panel.

### 9.2 Uniform record art read

Add `GET /api/worlds/{wid}/record-art/{kind}/{rid}` and its campaign counterpart.
Actors require `?version={vid}`; entities reject a version modifier and use
their internal `default`. Missing record/version is 404. Unknown kind is 404.
Every one of the seven `ART_RECORD_KINDS` entries uses this same response
contract. `greetings` is rejected as a record-art kind on all new routes.
The route uses existing root, version, entity, and overlay resolvers.

The response contains a target identity, effective image rows, read-only world
original rows, optional actor-specific related sections, and panel operations.
Each row carries its original `name`, `ext`, `v`, optional `image_id`, URLs,
description/review state, `description_scope` (`shared`, `legacy`, or absent),
an opaque `description_rev` for every actionable row, including unreviewed art,
and the description write-target state when legacy text masks shared metadata,
effective focus for the primary, and provenance plus typed owning placement.
Actor-specific related sections may be assembled by the actor adapter from
existing detail/appearance reads; the shared panel does not duplicate them.

Image-object dimensions can be supplied cheaply when available. Legacy rows
may omit them; a whole gallery read does not decode every file just to learn
dimensions. Contained thumbnail sizing uses real dimensions where known and
an appropriate bounded fallback otherwise; the existing cover-crop `srcset`
assumptions are not blindly reused for a contained image area.

Keep existing image-list and gallery routes compatible with current clients.
Add provenance and typed placement references to combined-gallery rows without
replacing their existing fields. Entity summaries gain optional effective
`image_focus`; image-free summaries omit it or carry null. Read-only world
originals are served from world URLs, not campaign URLs that would resolve the
overriding picture.

## 10. Mutation API and store guarantees

### 10.1 Additive operations

Let `{scope}` stand for `worlds/{wid}` or `campaigns/{cid}`.

| Operation | Contract |
|---|---|
| Append upload | `POST /api/{scope}/record-art/{kind}/{rid}/images`, actor `version` query, multipart `file`; server chooses the next safe placement and returns `{name, ext, v, image_id}` |
| Reuse | `POST /api/{scope}/record-art/{kind}/{rid}/images/link`, actor `version` query; JSON source placement, source `expected_v`, and intent `gallery` or `primary`; returns destination identity and placement |
| Entity focus | `PUT /api/{scope}/{kind}/{rid}/images/avatar/focus`, JSON `focus` and `expected_v`; plain model fields, range checked at the route/store boundary |
| Existing mutations | Existing named-image promote/delete/description routes and actor focus routes remain; new UI supplies optional `expected_v` preconditions checked under the mutation's locks |
| Download | Existing local image-serving routes accept an explicit download option that supplies `Content-Disposition: attachment` with a safe filename; preview/original reads keep their current behavior |

Source placement references distinguish record images from world/campaign
library images. Record references accept all seven `ART_RECORD_KINDS` and
exclude greeting placements. Record references include kind, record id, slot, and actor
version where applicable. Their scope must equal the destination world or its
effective campaign scope. A campaign can resolve its own or inherited world
art, but cannot link an unrelated campaign or world by providing ids.

A new caller always includes the shown image token for named-image mutation.
DELETE carries `expected_v` in the query; promotion carries it in its JSON body;
description and focus writes add it to their existing JSON bodies. Description
saves also send `expected_description_rev`, separately from `expected_v`.
Existing description callers may omit the new revision for compatibility;
the unified panel always supplies it. Promotion
also sends `expected_primary_v`, a token or null meaning the primary was absent,
so a changed primary is not swapped away without the reader seeing it.
The route rejects a changed target as 409 `stale_image` before mutating it.
Reuse carries `source.expected_v` and, for a primary intent,
`expected_primary_v` with that same token-or-null meaning. The picker captures
the destination expectation when it opens. Omitting the destination expectation
on a new primary reuse request is 400; null is different from an omitted key.
Existing callers that omit the optional preconditions on existing routes retain
their current API behavior. Plain model fields and route validation preserve
the repository's pydantic compatibility rules.

Invalid source/target ids or focus give 400/404 by the existing validation
conventions; a not-yet-arrived image gives the existing unavailable refusal;
a busy store uses the existing retryable busy response. A stale refusal keeps
the local description/crop draft and offers Reload followed by a deliberate
retry against the newly displayed picture. It does not save the old draft onto
a replacement image automatically.

`description_rev` is a deterministic digest of the current description state,
not the image bytes or the whole object sidecar's timestamp. It distinguishes
absent, explicit-empty, and text values, includes pending description conflicts,
and binds the image identity and applicable legacy overrides. For an
image-backed legacy override, it includes the underlying shared description
state too: saving that override can update the shared object even while the
legacy text masks it. Subject tags, preview focus, and other unrelated metadata
do not change this revision. No persistent revision field or store-format
migration is introduced.

On Save, compare the revision with fresh authoritative state and write within
the same description lock hold. A shared description is checked under the
image object's lock, including when two editors address different placements;
applicable legacy keys are checked under their sorted sidecar locks. An absent
description also has a revision, so two first descriptions cannot both pass
an empty-state check. A conflict returns 409 `stale_description` and the current
description revision, scope, text, and review state without changing shared or
legacy metadata. Conflict recovery preserves the draft until the reader
explicitly reconciles it or discards it. These guarantees cover cooperating
processes on one machine, with the existing synced-device limits.

If a shared description commits but clearing a legacy override fails, the
description route preserves its failure status and returns a structured
`description_cleanup_incomplete` result identifying the confirmed shared change.
The result includes the confirmed saved text separately from any later state.
It carries current effective/shared states and their revision when readable;
unreadable recovery metadata is marked unavailable rather than invented empty.
The UI says the shared description saved but the local override could not be
cleared, refreshes/invalidate other uses despite the failed response, and keeps
the local editor/recovery state. It never automatically replays the Save.

Recovery offers **Finish applying saved description** after current states have
been displayed. The existing description PUT accepts optional `cleanup_only`
for this deliberate retry, requiring both image and description preconditions.
Under the same sorted locks it verifies that the current shared description is
the confirmed saved value, then clears only the applicable legacy keys; it
does not rewrite shared metadata. Another shared or placement change refuses
cleanup and returns to reconciliation. A repeated cleanup failure remains a
visible partial save; an ambiguous response requires a fresh read, not replay.

For an image-backed legacy override, the art read and `stale_description`
response expose both `effective_description` and `write_target_description`,
each with its scope, text, review state, and pending conflicts. The latter is
the underlying shared object state Save will replace. These states and their
combined `description_rev` come from one consistent snapshot. Reconciliation
displays both states, explains the shared effect, and requires an explicit
choice to use the shared value, keep/revise the draft, or cancel. Only that
choice adopts the displayed revision; another concurrent change still refuses
the subsequent Save. If the metadata needed to explain the write target cannot
be read, the editor cannot authorize a guarded shared save by hiding it.

### 10.2 Persistence and concurrency

All new writes use `store.assets`, `store.image_refs`, `store.atomic`, and the
existing overlay primitives. No route writes image bytes into a record folder,
mutates a blob, bypasses a campaign overlay, or edits record prose as a side
effect of art management. New public campaign mutators are classified in
`store/locks.py` and take the campaign lock where required.

Allocation checks and placement publication are serialized by destination
slot locks with an occupancy recheck; a losing append retries a new slot, not
a named-image overwrite. Campaign allocation reads its effective union and
tombstones under its campaign lock. Unresolved local placements occupy slots.
An inherited candidate's world-slot check participates in the sorted name-lock
hold alongside its campaign destination; the campaign lock alone cannot
serialize a concurrent world image write. Source validation and target writes
observe image-name locks in
the existing sorted family order. No hand-built nesting of differently ordered
name locks is introduced. Existing legacy named PUT operations remain explicit
replacement operations, not append operations.

The lock order remains campaign, collection job/collection where needed, name,
sidecar, ingest/GC, object. Image reuse must protect object arrival and
publication against collection between its final validation and placing the
reference; it cannot rely only on `link_in` accepting a well-formed id, because
that primitive deliberately permits unresolved sync placements. Take the
existing ingest/GC exclusion in its documented order. Decode/ingest expensive
work stays outside long-lived campaign/name holds where possible, with the
source token and destination occupancy rechecked before publication.

Promotion uses the current journal and recovery contract. Reuse with primary
intent is an append followed by that promotion: if append lands and promotion
fails, the API reports a partial change with the created gallery slot. The UI
refreshes, says the image was added but could not be made primary, and offers
the normal primary action on that slot. Retrying must not append another copy
of this partial result. A disconnect with unknown outcome triggers a fresh
read and an honest outcome-unknown message; there is no blind automatic replay
of a mutation and no exactly-once promise across an ambiguous response.

The guarantees remain local-process/cross-process-on-one-machine guarantees;
this feature adds no distributed transaction across synced devices. Campaign
mutations continue to stamp the campaign revision through the appropriate
existing route/write mechanism. Multi-campaign writes are not introduced.

### 10.3 Compatibility

No store format, migration, garbage collector, image identity, metadata
precedence, collection manifest, or export/bundle format changes are required.
Legacy files remain readable and reusable through existing adoption/ingest
behavior. Thumbnail URLs continue to use byte/visual identity and the current
thumbnail revision. Crop tokens and summary reads must refresh when effective
focus changes, while the blob's original token remains its byte identity.

Downloads contain the store's retained, sanitized image representation, not
the user's pre-ingest original. Enabling attachment responses must preserve
normal inline cache behavior and must not let a cached attachment header leak
into a normal image read.

Backend additions remain compatible with Android's dependency set, pydantic
v1/v2 rules, and filesystem resolvers. The frontend uses browser capabilities
available in the Android WebView; explicit viewer controls are the fallback
for gesture behavior. No new native library is needed for viewing or cropping.

## 11. Errors, refresh, and privacy

Art reads have loading, loaded-empty, loaded-with-images, and failed states.
Failure offers Retry and does not suppress the record body. A failed request
for an optional related section reports that section's failure, leaving the
effective shelf available. A missing individual image keeps its label and a
broken-image treatment rather than silently disappearing.

Refresh uses fresh API reads after writes, not a shared in-flight GET started
before the write. Art list, primary preview, rail image/focus, count, and any
open usage/description details are refreshed as their mutation requires.
Records that share an image object invalidate their descriptions when an
in-app save is observed; open editors preserve unsaved text. Reopening an Art
panel performs a fresh metadata read, so it does not keep a description cache
as a second authority. External sync is detected through the existing store
read/version behavior; immediate cross-device push updates are not promised.

Request generations are keyed by scope, record, actor version, and operation.
An old read cannot land in a new context. Mutation failures retain selection,
the relevant local draft, and actionable feedback. A partial failure says which
step landed. Removing/promoting updates named-slot selections only after a
fresh read; a viewer is never allowed to mutate through a stale selection.

All tests, examples, and browser captures use the existing placeholder set and
synthetic images. Verification runs use the isolated verify procedure; no real
store names, data, aggregate measurements, screenshots, or references to a
private library enter this document or later artifacts. Server-side reuse
accepts typed references, never arbitrary paths or remote downloads.

## 12. Acceptance and verification

### 12.1 User-visible acceptance cases

1. Open Characters, PCs, Locations, Lore, Items, Groups, and Creatures in world
   and campaign scope: each has read-only record content, the same Art panel
   and management actions, and primary/gallery viewing inside the app.
2. Open character and PC Art, change version, and follow a gallery link into
   Art: the preview, count, shelf, and mutations all refer to that version.
3. At phone width, reach Art on an empty or gallery-only record without opening
   the context sheet; view and manage without page-width overflow.
4. Inspect a tall portrait, wide map, narrow object, small image, and animated
   file: contained tiles and the viewer preserve the full image; crop changes
   only identity previews.
5. Use keyboard and touch to fit, zoom, pan, step, and close. Closing restores
   focus/scroll; nested viewer Escape leaves the underlying drawer open.
6. Upload several files with one invalid file and two concurrent appends:
   successes remain, failures are named, the first success takes an empty
   primary, and no pre-existing placement is overwritten.
7. Reuse visible art across the seven record kinds and from a library: the
   destination has a new placement, no extra blob, correct metadata, and
   centered focus. A hidden/changed/unarrived source creates no placement;
   a greeting placement is refused by the generic reuse API.
8. Set a gallery image as primary with an existing primary: both pictures remain
   and the former primary occupies the gallery slot. Simulated failure after
   reuse append reports the partial result and retries promotion only.
9. Remove primary, gallery, and inherited art: only the requested placement or
   campaign visibility changes; other uses remain, and another primary is not
   silently selected.
10. Edit a shared description, explicitly choose no description, and cancel a
    draft: scope wording and review state are correct, and other visible uses
    refresh after a shared save.
11. Change record/version while a read or write is pending: late results cannot
    change the new context. Replace an image externally before saving a crop
    or description: the precondition refuses editing the replacement.
12. Search/filter the combined gallery, open its viewer, navigate to the record,
    and go Back: scope/version, filters, selected placement, and scroll survive.
13. Enlarge two occurrences of one inline collection: each starts at its own
    rendered member, preserves canonical member indices, and closing does not
    reroll either occurrence.
14. Fail an art read, an original image load, and a refresh following a landed
    write: each failure is visible and distinguishable from empty or unsaved.
15. Open greeting art, its subject tagging/queue, and a character's greeting-copy
    bridge: the dedicated greeting behavior remains, no common record-art CRUD
    is offered for the greeting, and a copied character placement subsequently
    receives the complete common Art contract.
16. Open description editors for two different placements of one image. After
    one saves, the other's stale text or explicit-empty save is refused without
    losing its draft or the first save. The same holds for two first descriptions
    and for an image-backed legacy override masking changed shared metadata.
    In the masked case, recovery reveals the newer shared value as well as the
    legacy text before explicit reconciliation and retry. A further shared edit
    between that choice and Save is refused again.
    Inject failure after the shared commit but before legacy cleanup: other uses
    refresh, the editor reports a partial save, and deliberate cleanup retries
    cannot overwrite a subsequent shared edit.

### 12.2 Automated checks

Frontend tests cover the viewer's sequence/zoom/failure/focus behavior, nested
modal ownership, shared panel view/manage transitions, mobile entry points,
an explicit seven-kind by two-scope matrix, URL round-trips, stale-result
rejection, description states, partial upload/reuse results, and collection
occurrence stability. Existing shelf tests are updated to assert the new viewer
interaction while retaining coverage of specialized character/greeting actions.
The matrix exercises every common management action for each kind, not only
the presence of an Art tab. Greeting regression tests assert its separate
rendering/tagging contract and explicit opt-out from common viewer integration.

Backend tests cover read provenance, overlay focus, source visibility and
preconditions, unavailable/corrupt objects, legacy reuse, safe concurrent append,
primary preservation/recovery, removal/tombstones, metadata scope, cross-placement
description conflicts (including absent/text/explicit-empty and legacy masking), download
headers/cache separation, and routes refusing nonexistent records or versions
before any write. Parameterize common read/write behavior across the same
seven-kind by two-scope matrix; test cross-kind reuse and rejection of greetings
as both a common target and a generic reuse source. Extend the image-surface roster for new writers and assert
reuse writes placements rather than duplicate bytes. Use existing LLM fakes
for description drafting; no new inline fake or live model call is needed.

Browser verification uses an isolated store with synthetic portrait/map/item
images and placeholder records, at desktop and phone widths. It checks visual
fit and overflow, touch/pan behavior, keyboard focus restoration, all seven sources,
inherited art, navigation back into the filtered gallery, and a collection
viewer during streamed Markdown updates. Android verification exercises these
same web flows in the packaged WebView; any build check is reported separately
under the repository's Android procedure.

Implementation completion requires the repository gate (`make check`, with
the main checkout's interpreter supplied as `PY` in this worktree), the focused
behavior checks, and the mandatory implementation reviews from `CLAUDE.md`.
Baseline ratchets are updated only for real lint improvements. The frozen
campaign's `home/` is never regenerated.

### 12.3 Review handoff

This document is the full behavior spec, not an implementation plan. Before
planning, obtain user review of this written draft and run the mandatory spec
adversarial-review checkpoint defined in `CLAUDE.md`. Resolve findings before
writing a plan. The plan then needs its own checkpoint and execution-method
selection before product changes begin. No checkpoint is recorded as passed
by the existence of this draft.

## References

- [Project conventions](../../../CLAUDE.md) and [gate guidance](../../../CONTRIBUTING.md).
- [Image/store guarantees](../../store-guarantees.md#the-image-store).
- [Content-addressed image design](2026-10-05-content-addressed-image-store-design.md).
- [PC detail and mobile art design](2026-09-28-pc-and-mobile-art-design.md).
- [Character art](../../../frontend/src/components/character/ArtTab.tsx),
  [PC art](../../../frontend/src/components/pc/PCArtTab.tsx), and
  [entity editor](../../../frontend/src/components/EntityEditor.tsx).
- [Combined gallery](../../../frontend/src/components/ImagesView.tsx),
  [Markdown images](../../../frontend/src/markdown/MarkdownImage.tsx), and
  [record navigation](../../../frontend/src/worldPaths.ts).
- [Asset primitives](../../../backend/src/grimoire/store/assets.py),
  [campaign overlay](../../../backend/src/grimoire/store/overlay.py), and
  [image descriptions](../../../backend/src/grimoire/store/image_descriptions.py).
