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

A greeting is optional. The new-scene chooser also supports your own premise,
generated ideas and importing an existing transcript.

## Art

Record pages provide controls for uploading, reusing, describing and selecting
images. Primary-image selection and preview crop affect presentation. Shared
image descriptions belong to the underlying picture, so editing a description
can affect other records that reuse it.

Removing art removes its placement. Reclaiming unused stored images is a separate
[maintenance operation](data.md#image-maintenance).

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
