# PC detail and mobile character art

## Purpose

A player character (PC) should read like a character: one record owns the
screen, its portrait and versions have room to breathe, and its art is a clear
destination. On a phone, both kinds of character should expose their art from
the main view without opening the context sheet or discovering an offscreen tab.
The existing image, persona, version, sheet, and campaign operations remain
available.

## Routes and page structure

The existing PC addresses, `/worlds/:wid/pcs/:pid` and
`/campaigns/:cid/world/pcs/:pid`, become dedicated PC detail routes. The
recordless `/pcs` section remains inside `WorldView` for browsing and creating
PCs. Its rows link to the detail page. Creating a PC, including through the
sheet wizard, navigates to its new page in edit mode; a bookmarked or later
visit opens in read mode. Changing the record id remounts the detail page, so
an old record's read or edit state cannot land under a new address. The shared
`sectionHref` builder continues to construct these URLs.

The PC detail page uses `PageShell` as `CharacterPage` does: a context column
for identity, version controls, campaign lock/import state, tags, and related
record links; main for a screen heading and tabs. The main tabs are **Persona**,
**Lore**, **Art**, and **Sheet** when a mechanics sheet is available. Persona
starts read only, with an explicit Edit action. Existing whole-persona Save
and Cancel behavior remains, along with birthdate, tags, default version,
delete, and campaign promote/push controls. Version selection changes the
persona, portrait, and Art tab together. An absent or unreadable PC reports an
error, not an empty record.

The PC list and detail page do not maintain separate copies of mutation
handlers. Moving the current record behavior out of `PCEditor` into the detail
page may extract narrow shared helpers for image URLs or version selection,
but leaves a single implementation of each write. The list keeps only its
list and creation responsibilities. Existing links to a PC continue to work.

## Art on both record pages

PCs gain an Art tab with the same editing contract their current inline shelf
offers: avatar, gallery in numeric order, image descriptions, add, promote,
remove, and avatar crop. World scope alone offers generated descriptions;
campaign scope does not present a control whose endpoint is unavailable.
Images and descriptions belong to the selected version. The tab's current
version art appears as downscaled tiles that link directly to the original
images in a new tab. The same tiles carry the editing controls.
An image write refreshes the displayed bytes through the store's version
tokens, rather than an unversioned browser-cache URL.

CharacterPage keeps its Art tab and its existing world/campaign image sections,
including inherited and shadowed art. Its shelves, descriptions, greeting-art
actions, and localization controls stay available. An image-free version keeps
the existing add control.

## Phone access

At widths where `PageShell` hides the context column in a sheet, both PC and
character pages put a portrait preview beside or directly below the record
heading in main. The preview uses the current version's avatar, or its first
gallery image when there is no avatar, with an initials fallback when neither
exists. A visible **View art** action opens the Art tab; it is present even
with no image so the add control can be reached. The Art tab itself must be
reachable without horizontally scrolling a tab strip. Its linked image tiles
remain touch accessible without an inline large-image viewer.
The desktop context-column portrait and its crop control remain available.

## Data and failure behavior

No backend API or store format changes are required. PC images use the
existing `listPCImages` and `actorImageUrl` contract; character images use
the version's `images` and `image_v` fields. An image-list failure says the
shelf could not be read rather than treating it as an empty shelf. Late reads
from an old PC, version, or scope must not replace the new page's art or
persona. A failed image write reports its error and leaves the selected
record and version in place.

## Verification

Frontend tests cover both route scopes, list-to-detail and new-PC navigation,
read/edit transitions, version switching, existing image writes, numeric
gallery order, missing/read failures, and stale-response rejection. At phone
width they assert that both pages show art and a direct Art action in main,
including avatar, gallery-only, and image-free cases. Art tab tests assert
that thumbnail links open the selected version's originals while the editing
controls remain. Run frontend typecheck, coverage, ESLint
baseline, production build, and the repository gate where the environment
supports it. Browser verification uses only an isolated placeholder store.
