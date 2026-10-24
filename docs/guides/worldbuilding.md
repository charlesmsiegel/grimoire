# Worldbuilding

[Documentation](../README.md) · [Playing a campaign](playing.md)

## Start with a reusable world

A world holds a setting for use across playthroughs. Open it from
**Library → Worlds**. Its overview leads to records, greetings, images,
calendar and other setting information.

Give the world a premise and add what the next scene needs. You can expand the
setting while playing; a complete encyclopedia is not a prerequisite.

## Choose the right record

| Record | Use it for |
|---|---|
| Character | An NPC card, its versions, voice and associated art |
| PC | A persona the player takes into a campaign |
| Location | A place, its description and setting information |
| Lore | Reusable world information and keyword-activated context |
| Item | A significant object or possession |
| Group | An organization, its relationships and explicit members |
| Creature | A non-character creature record |
| Greeting | An opening that can seed a scene |

Open a record to read it. Choose **Edit**, then save or cancel. Creating a new
record opens its own form. Larger rosters use searchable grids; the current
page's column and links provide navigation to individual records.

## Characters, PCs and versions

A character can carry multiple versions of its card: for example, different
portrayals of Seraphine. Pick the version appropriate to a scene instead of
overwriting the only copy. Character and PC pages also expose their profiles
and art; their fields differ because a PC is the player's persona.

Voice anchors describe concrete features of a character's speech. They can
influence the prompt and support the later voice-drift review. A drift finding
is a review suggestion, not proof that a portrayal is wrong.

## Import existing material

Character import accepts supported card files, including JSON, PNG cards and
CHARX. Importing by URL supports the app's Chub integration. Embedded lorebooks
and world-info imports can become lore records. Text import accepts pasted text
or `.txt`/`.md` files, with optional model-assisted refinement before saving.

Review the resulting fields before saving. Imported content can contain its own
instructions, links and image references; use the editor to decide what belongs
in your world. Export a character version from its Export menu for a portable
card again.

## Lore, ownership and activation

Give lore useful keys rather than relying on its title. The context engine can
activate entries from the recent conversation and apply their other activation
controls. Ownership and secrecy affect who receives information. Groups can
name explicit character and PC members.

When a record seems absent from a prompt, inspect **Context** in a scene that
should use it. Eligibility, activation, exclusions and the context budget can
all matter. [Context and memory](context.md) explains these layers.

## Greetings and scene openings

A greeting can specify text, characters, a location, PC eligibility and links to
other greetings. Use these for repeatable openings or a sequence of prepared
scenes. The campaign records which openings have been played.

A character's **Greetings** tab shows the greetings associated with the selected
version, with a linked title and full text for each opening. Source greetings
use their linked version; supporting characters use their default version.
The card's first message and alternates are editable under **Card**; import them
as world greetings before using them to start scenes. Card import sources do
not contribute to the playable greeting count.

A greeting is optional. The new-scene chooser also supports your own premise,
generated ideas and importing an existing transcript.

## Art

Record pages provide controls for uploading, reusing, describing and selecting
images. Primary-image selection and preview crop affect presentation. Shared
image descriptions belong to the underlying picture, so editing a description
can affect other records that reuse it.

Removing art removes its placement. Reclaiming unused stored images is a separate
[maintenance operation](data.md#image-maintenance).

## Expression outfits

Open a character or PC, select its version, and choose **Expressions**. This
library is separate from Art and the avatar. **Edit expressions** adds or
duplicates outfits, uploads individual sprites, edits outfit names, and sets
the default outfit, fallback expression, scale and bottom-center alignment.
Renaming an outfit changes its display name; its lowercase ID stays stable.
The character's **Expressions** tab badge counts the selected version's available
outfits, including inherited outfits and excluding hidden ones.

Each image is a complete sprite. Transparent PNG or WebP images work well.
Sparse outfits are valid: start with `neutral`, then add expressions as needed.
The resolver keeps the selected outfit whenever it has a usable sprite; missing
anger in armor does not switch the character into an everyday outfit. The
library uses the 28 tracker visible-mood labels, plus explicitly named custom
labels. Missing tracker information resolves to neutral. The scene menu’s **Visual novel reader** uses these sprites for historical
playback, with background and per-post appearance controls; see [Playing](playing.md#visual-novel-reader).

Campaign libraries inherit the attached world character's selected version.
Replacing one expression leaves the others inherited. **Remove override**
reveals the world sprite; **Hide** suppresses it in this campaign. Restore
controls undo hiding. Detached characters never inherit by a matching name or ID.

**Import expression pack** accepts a flat SillyTavern ZIP or folder into a chosen outfit,
or a Grimoire ZIP/folder with `expressions/<outfit>/<expression>.png` (also JPEG,
WebP and static GIF). Optional `expressions/manifest.json` carries format 1 outfit
metadata and a default outfit. Review filename mappings before publication:
alternate images and custom labels need explicit selection, and replacing an
existing sprite needs its replacement checkbox. Invalid archives leave the
collection unchanged; stale or expired previews must be uploaded again.
New outfits retain their metadata even when they contain no sprites. Existing
outfit settings are preserved. The preview shows the pack's default outfit.

Imports allow 512 files, a 128 MiB upload, 512 MiB expanded data, 20 MiB and
16 megapixels per sprite, and 128 megapixels decoded in total. Animated sprites
and unsupported files are reported. Unsafe paths, duplicate normalized names,
encryption and excessive compression are refused. Preview tokens expire after
30 minutes; abandoned previews are bounded, so an older preview may expire sooner.

**Export expression pack** exports the selected version's visible effective
collection. **Full character / PC pack** exports every version, ordinary art and
the original expression layers. See [Data and backups](data.md#native-actor-packs)
for its import and inheritance choices. Ordinary PNG/JSON character cards do not
include expression libraries.

The editor reports unavailable configured defaults and fallback sprites while
continuing to show usable images. **Clean up old revisions** removes retired
expression references, keeping the current and previous revisions, active reader
leases, and revisions less than one hour old. Removed or replaced image bytes
remain until this cleanup and the existing Image maintenance scan/recycle flow
release them; cleanup itself never deletes image objects.

## Understand campaign copies

Campaigns read shared world records until a local edit makes them diverge. A
campaign's own record then takes precedence. Local deletions can hide inherited
records; a campaign-created record remains local. Editing a campaign copy is
different from editing the shared world.

Use the app's synchronization and review controls to move changes between the
two deliberately. A world edit does not simply replace a campaign copy that
has diverged. The [storage reference](../reference/storage.md#world-and-campaign-overlays)
defines these resolution rules.

## Implementation references

- [World pages](../../frontend/src/routes/WorldView.tsx) and [character pages](../../frontend/src/routes/CharacterPage.tsx)
- [Record kinds](../../backend/src/grimoire/store/entities.py) and [overlay resolution](../../backend/src/grimoire/store/overlay/)
- [Character records](../../backend/src/grimoire/store/characters/) and [greetings](../../backend/src/grimoire/store/greetings.py)
- [Voice anchors](../../backend/src/grimoire/store/voice_anchors.py) and [image descriptions](../../backend/src/grimoire/store/image_descriptions.py)
